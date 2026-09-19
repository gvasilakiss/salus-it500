"""Custom services for the Salus iT500 integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .api import SalusValidationError

from .api.schedule import (
    decode_heating,
    decode_hot_water,
    describe_heating,
    describe_hot_water,
    encode_heating,
    encode_hot_water,
)
from .const import (
    ATTR_DAY,
    ATTR_END,
    ATTR_ENTRIES,
    ATTR_HOURS,
    ATTR_START,
    ATTR_ZONE,
    DAYS,
    DOMAIN,
    MAX_BOOST_HOURS,
    MAX_TEMP,
    MIN_TEMP,
    SERVICE_ADVANCE,
    SERVICE_BOOST,
    SERVICE_CANCEL_BOOST,
    SERVICE_CLEAR_HOLIDAY,
    SERVICE_GET_SCHEDULE,
    SERVICE_REFRESH,
    SERVICE_SET_HOLIDAY,
    SERVICE_SET_SCHEDULE,
    SERVICE_SKIP,
    ZONE_CH1,
    ZONE_CH2,
    ZONE_HW,
)
from .coordinator import SalusDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

ZONES = [ZONE_CH1, ZONE_CH2, ZONE_HW]
HEATING_ZONES = [ZONE_CH1, ZONE_CH2]

HEATING_ENTRY_SCHEMA = vol.Schema(
    {
        vol.Required("time"): cv.matches_regex(r"^([01]\d|2[0-3]):[0-5]\d$"),
        vol.Required("temperature"): vol.All(
            vol.Coerce(float), vol.Range(min=MIN_TEMP, max=MAX_TEMP)
        ),
    }
)

HOT_WATER_ENTRY_SCHEMA = vol.Schema(
    {
        vol.Required("on_time"): cv.matches_regex(r"^([01]\d|2[0-3]):[0-5]\d$"),
        vol.Required("off_time"): cv.matches_regex(r"^([01]\d|2[0-3]):[0-5]\d$"),
    }
)

BASE_SCHEMA = vol.Schema({vol.Required(ATTR_DEVICE_ID): cv.string})

SET_SCHEDULE_SCHEMA = BASE_SCHEMA.extend(
    {
        vol.Required(ATTR_ZONE, default=ZONE_CH1): vol.In(ZONES),
        vol.Required(ATTR_DAY): vol.All(cv.ensure_list, [vol.In(DAYS + ["all"])]),
        vol.Required(ATTR_ENTRIES): vol.All(
            cv.ensure_list,
            vol.Length(min=1, max=6),
            [vol.Any(HEATING_ENTRY_SCHEMA, HOT_WATER_ENTRY_SCHEMA)],
        ),
    }
)

GET_SCHEDULE_SCHEMA = BASE_SCHEMA.extend(
    {vol.Optional(ATTR_ZONE, default=ZONE_CH1): vol.In(ZONES)}
)

BOOST_SCHEMA = BASE_SCHEMA.extend(
    {
        vol.Required(ATTR_ZONE, default=ZONE_CH1): vol.In(ZONES),
        vol.Required(ATTR_HOURS, default=1): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=MAX_BOOST_HOURS)
        ),
    }
)

CANCEL_BOOST_SCHEMA = BASE_SCHEMA.extend(
    {vol.Required(ATTR_ZONE, default=ZONE_CH1): vol.In(ZONES)}
)

ADVANCE_SCHEMA = BASE_SCHEMA.extend(
    {vol.Required(ATTR_ZONE, default=ZONE_CH1): vol.In(HEATING_ZONES)}
)

SKIP_SCHEMA = BASE_SCHEMA.extend(
    {vol.Required(ATTR_ZONE, default=ZONE_CH1): vol.In(HEATING_ZONES)}
)

SET_HOLIDAY_SCHEMA = BASE_SCHEMA.extend(
    {
        vol.Optional(ATTR_START): cv.string,
        vol.Optional(ATTR_END): cv.string,
    }
)


def _coordinator_for(
    hass: HomeAssistant, device_id: str
) -> SalusDataUpdateCoordinator:
    """Resolve a HA device ID to this integration's coordinator."""
    registry = dr.async_get(hass)
    device = registry.async_get(device_id)

    if device is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="device_not_found",
            translation_placeholders={"device_id": device_id},
        )

    for entry_id in device.config_entries:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry and entry.domain == DOMAIN and hasattr(entry, "runtime_data"):
            return entry.runtime_data

    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="device_not_salus",
        translation_placeholders={"device_id": device_id},
    )


def _days_from(call: ServiceCall) -> list[str]:
    """Expand the 'all' shorthand into the seven weekday names."""
    requested = call.data[ATTR_DAY]
    return list(DAYS) if "all" in requested else requested


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's services exactly once."""
    if hass.services.has_service(DOMAIN, SERVICE_SET_SCHEDULE):
        return

    async def async_set_schedule(call: ServiceCall) -> None:
        """Write one or more days of a zone's weekly program."""
        coordinator = _coordinator_for(hass, call.data[ATTR_DEVICE_ID])
        zone = call.data[ATTR_ZONE]
        entries: list[dict[str, Any]] = call.data[ATTR_ENTRIES]

        if not coordinator.client.supports_schedules:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="schedules_unsupported"
            )

        is_hot_water = zone == ZONE_HW
        first = entries[0]

        if is_hot_water and "on_time" not in first:
            raise ServiceValidationError(
                "Hot water entries need on_time and off_time"
            )
        if not is_hot_water and "time" not in first:
            raise ServiceValidationError(
                "Heating entries need time and temperature"
            )

        try:
            program = (
                encode_hot_water(entries) if is_hot_water else encode_heating(entries)
            )
        except SalusValidationError as err:
            raise ServiceValidationError(str(err)) from err

        days = _days_from(call)
        for day in days:
            await coordinator.async_command(
                lambda d=day: coordinator.client.async_set_program(zone, d, program)
            )

        _LOGGER.debug("Wrote %s program %r", zone, program)

        # Read back what Salus actually stored rather than assuming the
        # write applied - the Arrayent API is known to accept a write while
        # still answering with an HTTP 500.
        await coordinator.async_refresh()
        zone_state = coordinator.data.zone(zone)
        mismatches = [day for day in days if zone_state.programs.get(day, "") != program]
        if mismatches:
            raise HomeAssistantError(
                "Salus did not confirm the new schedule for "
                f"{', '.join(mismatches)}. Check the device and try again."
            )

    async def async_get_schedule(call: ServiceCall) -> ServiceResponse:
        """Return the decoded weekly program for a zone."""
        coordinator = _coordinator_for(hass, call.data[ATTR_DEVICE_ID])
        zone_name = call.data[ATTR_ZONE]

        if not coordinator.client.supports_schedules:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="schedules_unsupported"
            )

        await coordinator.async_request_refresh()
        zone = coordinator.data.zone(zone_name)
        is_hot_water = zone_name == ZONE_HW

        week: dict[str, Any] = {}
        for day, raw in zone.programs.items():
            slots = decode_hot_water(raw) if is_hot_water else decode_heating(raw)
            summary = (
                describe_hot_water(slots) if is_hot_water else describe_heating(slots)
            )
            week[day] = {"raw": raw, "summary": summary, "entries": slots}

        return {"zone": zone_name, "schedule": week}

    async def async_boost(call: ServiceCall) -> None:
        """Start a timed boost on a zone."""
        coordinator = _coordinator_for(hass, call.data[ATTR_DEVICE_ID])
        if not coordinator.client.supports_boost:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="boost_unsupported"
            )
        await coordinator.async_command(
            lambda: coordinator.client.async_set_boost(
                call.data[ATTR_ZONE], call.data[ATTR_HOURS]
            )
        )

    async def async_cancel_boost(call: ServiceCall) -> None:
        """Cancel a running boost."""
        coordinator = _coordinator_for(hass, call.data[ATTR_DEVICE_ID])
        if not coordinator.client.supports_boost:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="boost_unsupported"
            )
        await coordinator.async_command(
            lambda: coordinator.client.async_set_boost(call.data[ATTR_ZONE], 0)
        )

    async def async_advance(call: ServiceCall) -> None:
        """Jump a heating zone to its next scheduled temperature now."""
        coordinator = _coordinator_for(hass, call.data[ATTR_DEVICE_ID])
        if not coordinator.client.supports_schedules:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="schedules_unsupported"
            )
        await coordinator.async_advance_zone(call.data[ATTR_ZONE])

    async def async_skip(call: ServiceCall) -> None:
        """Skip a heating zone's next scheduled change."""
        coordinator = _coordinator_for(hass, call.data[ATTR_DEVICE_ID])
        if not coordinator.client.supports_schedules:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="schedules_unsupported"
            )
        await coordinator.async_skip_zone(call.data[ATTR_ZONE])

    async def async_refresh(call: ServiceCall) -> None:
        """Force an immediate, un-debounced refresh from Salus."""
        coordinator = _coordinator_for(hass, call.data[ATTR_DEVICE_ID])
        await coordinator.async_refresh()

    async def async_set_holiday(call: ServiceCall) -> None:
        """Engage holiday mode, optionally with a date range."""
        coordinator = _coordinator_for(hass, call.data[ATTR_DEVICE_ID])
        await coordinator.async_command(
            lambda: coordinator.client.async_set_holiday(
                True, call.data.get(ATTR_START), call.data.get(ATTR_END)
            )
        )

    async def async_clear_holiday(call: ServiceCall) -> None:
        """Cancel holiday mode."""
        coordinator = _coordinator_for(hass, call.data[ATTR_DEVICE_ID])
        await coordinator.async_command(
            lambda: coordinator.client.async_set_holiday(False)
        )

    hass.services.async_register(
        DOMAIN, SERVICE_SET_SCHEDULE, async_set_schedule, schema=SET_SCHEDULE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_SCHEDULE,
        async_get_schedule,
        schema=GET_SCHEDULE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_BOOST, async_boost, schema=BOOST_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CANCEL_BOOST,
        async_cancel_boost,
        schema=CANCEL_BOOST_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_ADVANCE, async_advance, schema=ADVANCE_SCHEMA
    )
    hass.services.async_register(DOMAIN, SERVICE_SKIP, async_skip, schema=SKIP_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_REFRESH, async_refresh, schema=BASE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_HOLIDAY, async_set_holiday, schema=SET_HOLIDAY_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CLEAR_HOLIDAY, async_clear_holiday, schema=BASE_SCHEMA
    )
