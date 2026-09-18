"""Climate entities for the Salus iT500 heating zones."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import HeatingMode
from .api.schedule import current_slot, decode_heating, describe_heating, next_change
from .const import (
    DOMAIN,
    MAX_TEMP,
    MIN_TEMP,
    PRESET_BOOST,
    PRESET_FOLLOW_SCHEDULE,
    PRESET_PERMANENT_HOLD,
    PRESET_TEMP_HOLD,
    TEMP_STEP,
    ZONE_CH1,
    ZONE_CH2,
)
from .coordinator import SalusConfigEntry, SalusDataUpdateCoordinator
from .entity import SalusZoneEntity

_LOGGER = logging.getLogger(__name__)

HVAC_TO_MODE = {
    HVACMode.OFF: HeatingMode.OFF,
    HVACMode.AUTO: HeatingMode.AUTO,
    HVACMode.HEAT: HeatingMode.MANUAL,
}

MODE_TO_HVAC = {
    HeatingMode.OFF: HVACMode.OFF,
    HeatingMode.AUTO: HVACMode.AUTO,
    HeatingMode.TEMP_HOLD: HVACMode.AUTO,
    HeatingMode.MANUAL: HVACMode.HEAT,
}

PRESET_TO_MODE = {
    PRESET_FOLLOW_SCHEDULE: HeatingMode.AUTO,
    PRESET_TEMP_HOLD: HeatingMode.TEMP_HOLD,
    PRESET_PERMANENT_HOLD: HeatingMode.MANUAL,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SalusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add one climate entity per wired heating zone."""
    coordinator = entry.runtime_data
    entities = [SalusClimate(coordinator, ZONE_CH1)]

    if coordinator.data.has_ch2 and coordinator.client.supports_second_zone:
        entities.append(SalusClimate(coordinator, ZONE_CH2))

    async_add_entities(entities)


class SalusClimate(SalusZoneEntity, ClimateEntity):
    """A single iT500 heating zone."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = TEMP_STEP
    _attr_min_temp = MIN_TEMP
    _attr_max_temp = MAX_TEMP
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.AUTO, HVACMode.HEAT]
    _attr_translation_key = "heating"

    def __init__(
        self, coordinator: SalusDataUpdateCoordinator, zone: str
    ) -> None:
        """Advertise the features the active transport can honour."""
        super().__init__(coordinator, zone, "thermostat")
        self._attr_name = None if zone == ZONE_CH1 else "Zone 2"

        features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )
        if coordinator.client.supports_boost:
            features |= ClimateEntityFeature.PRESET_MODE
        self._attr_supported_features = features

        if coordinator.client.supports_boost:
            self._attr_preset_modes = [
                PRESET_FOLLOW_SCHEDULE,
                PRESET_TEMP_HOLD,
                PRESET_PERMANENT_HOLD,
                PRESET_BOOST,
            ]

    # --- State -------------------------------------------------------------

    @property
    def current_temperature(self) -> float | None:
        """Room temperature as measured by the thermostat."""
        return self.heating_zone.current_temperature

    @property
    def target_temperature(self) -> float | None:
        """The setpoint currently in force."""
        return self.heating_zone.target_temperature

    @property
    def hvac_mode(self) -> HVACMode:
        """Off, schedule-following, or holding a manual setpoint."""
        return MODE_TO_HVAC.get(self.heating_zone.mode, HVACMode.AUTO)

    @property
    def hvac_action(self) -> HVACAction:
        """Whether the boiler is actually being called right now."""
        zone = self.heating_zone
        if zone.mode is HeatingMode.OFF:
            return HVACAction.OFF
        return HVACAction.HEATING if zone.relay_on else HVACAction.IDLE

    @property
    def preset_mode(self) -> str | None:
        """Surface boost and frost, which sit on top of the mode flags."""
        zone = self.heating_zone
        if zone.boost_active:
            return PRESET_BOOST
        if zone.mode is HeatingMode.MANUAL:
            return PRESET_PERMANENT_HOLD
        if zone.mode is HeatingMode.TEMP_HOLD:
            return PRESET_TEMP_HOLD
        if zone.mode is HeatingMode.AUTO:
            return PRESET_FOLLOW_SCHEDULE
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose schedule context that has no first-class HA equivalent."""
        zone = self.heating_zone
        attributes: dict[str, Any] = {
            "salus_mode": zone.mode.value,
            "relay_on": zone.relay_on,
            "frost_protection_active": zone.frost_active,
            "boost_remaining_hours": zone.boost_hours,
        }

        if zone.manual_setpoint is not None:
            attributes["manual_setpoint"] = zone.manual_setpoint
        if zone.auto_setpoint is not None:
            attributes["schedule_setpoint"] = zone.auto_setpoint
        if zone.schedule_type is not None:
            attributes["schedule_type"] = zone.schedule_type.name.lower()

        today = self._today_slots()
        if today:
            stamp = self._now_hhmm()
            attributes["schedule_today"] = describe_heating(today)
            active = current_slot(today, stamp)
            upcoming = next_change(today, stamp)
            if active:
                attributes["scheduled_setpoint_now"] = active["temperature"]
            if upcoming:
                attributes["next_schedule_change"] = upcoming["time"]
                attributes["next_schedule_temperature"] = upcoming["temperature"]

        return attributes

    def _now_hhmm(self) -> str:
        """Local wall-clock time as HH:MM, for schedule comparisons."""
        return dt_util.now().strftime("%H:%M")

    def _today_slots(self) -> list[dict[str, Any]]:
        """Decoded schedule slots for today, if the transport has them."""
        if not self.coordinator.client.supports_schedules:
            return []
        day = dt_util.now().strftime("%A").lower()
        return decode_heating(self.heating_zone.programs.get(day, ""))

    # --- Commands ----------------------------------------------------------

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Hold a new setpoint."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return

        if not MIN_TEMP <= temperature <= MAX_TEMP:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="temperature_out_of_range",
                translation_placeholders={
                    "min": str(MIN_TEMP),
                    "max": str(MAX_TEMP),
                },
            )

        def optimistic(state) -> None:
            zone = state.zone(self._zone)
            zone.target_temperature = temperature
            if zone.mode is HeatingMode.AUTO:
                zone.mode = HeatingMode.TEMP_HOLD

        await self.coordinator.async_command(
            lambda: self.coordinator.client.async_set_target_temperature(
                self._zone, float(temperature)
            ),
            optimistic=optimistic,
        )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Switch the zone off, onto the schedule, or into manual hold."""
        mode = HVAC_TO_MODE.get(hvac_mode)
        if mode is None:
            raise ServiceValidationError(f"Unsupported HVAC mode {hvac_mode}")

        def optimistic(state) -> None:
            state.zone(self._zone).mode = mode

        await self.coordinator.async_command(
            lambda: self.coordinator.client.async_set_heating_mode(self._zone, mode),
            optimistic=optimistic,
        )

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Apply a preset, including starting or cancelling a boost."""
        zone = self.heating_zone

        if preset_mode == PRESET_BOOST:
            await self.coordinator.async_command(
                lambda: self.coordinator.client.async_set_boost(self._zone, 1)
            )
            return

        mode = PRESET_TO_MODE.get(preset_mode)
        if mode is None:
            raise ServiceValidationError(f"Unknown preset {preset_mode}")

        # Leaving a preset while a boost is running should also end the boost.
        if zone.boost_active:
            await self.coordinator.async_command(
                lambda: self.coordinator.client.async_set_boost(self._zone, 0)
            )

        await self.coordinator.async_command(
            lambda: self.coordinator.client.async_set_heating_mode(self._zone, mode),
            optimistic=lambda state: setattr(state.zone(self._zone), "mode", mode),
        )

    async def async_turn_on(self) -> None:
        """Return the zone to its schedule."""
        await self.async_set_hvac_mode(HVACMode.AUTO)

    async def async_turn_off(self) -> None:
        """Turn the zone off entirely (frost protection still applies)."""
        await self.async_set_hvac_mode(HVACMode.OFF)
