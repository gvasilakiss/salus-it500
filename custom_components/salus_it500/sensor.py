"""Sensors for the Salus iT500."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import DeviceState
from .api.schedule import (
    current_slot,
    decode_heating,
    decode_hot_water,
    describe_heating,
    describe_hot_water,
    next_change,
)
from .const import ZONE_CH1, ZONE_CH2
from .coordinator import SalusConfigEntry, SalusDataUpdateCoordinator
from .entity import SalusEntity


@dataclass(frozen=True, kw_only=True)
class SalusSensorDescription(SensorEntityDescription):
    """Describes a Salus sensor and how to read it."""

    value_fn: Callable[[DeviceState], Any]
    attributes_fn: Callable[[DeviceState], dict[str, Any]] | None = None
    exists_fn: Callable[[DeviceState], bool] = lambda _: True


def _zone_schedule_attributes(zone_name: str) -> Callable[[DeviceState], dict[str, Any]]:
    """Build an attribute extractor for a zone's schedule sensor."""

    def extract(state: DeviceState) -> dict[str, Any]:
        day = dt_util.now().strftime("%A").lower()
        zone = state.zone(zone_name)
        slots = decode_heating(zone.programs.get(day, ""))
        stamp = dt_util.now().strftime("%H:%M")
        upcoming = next_change(slots, stamp)
        return {
            "day": day,
            "summary": describe_heating(slots),
            "slots": slots,
            "next_change_at": upcoming["time"] if upcoming else None,
            "next_change_temperature": upcoming["temperature"] if upcoming else None,
            "full_week": {
                name: decode_heating(program)
                for name, program in zone.programs.items()
            },
        }

    return extract


def _zone_schedule_value(zone_name: str) -> Callable[[DeviceState], Any]:
    """Build a value extractor that summarises today's schedule."""

    def extract(state: DeviceState) -> Any:
        day = dt_util.now().strftime("%A").lower()
        slots = decode_heating(state.zone(zone_name).programs.get(day, ""))
        stamp = dt_util.now().strftime("%H:%M")
        active = current_slot(slots, stamp)
        return active["temperature"] if active else None

    return extract


SENSORS: tuple[SalusSensorDescription, ...] = (
    SalusSensorDescription(
        key="ch1_temperature",
        translation_key="room_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        value_fn=lambda state: state.ch1.current_temperature,
    ),
    SalusSensorDescription(
        key="ch1_setpoint",
        translation_key="setpoint",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.ch1.target_temperature,
    ),
    SalusSensorDescription(
        key="ch2_temperature",
        translation_key="room_temperature_zone2",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        value_fn=lambda state: state.ch2.current_temperature,
        exists_fn=lambda state: state.has_ch2,
    ),
    SalusSensorDescription(
        key="ch1_boost_remaining",
        translation_key="boost_remaining",
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.ch1.boost_hours,
    ),
    SalusSensorDescription(
        key="ch2_boost_remaining",
        translation_key="boost_remaining_zone2",
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.ch2.boost_hours,
        exists_fn=lambda state: state.has_ch2,
    ),
    SalusSensorDescription(
        key="hw_boost_remaining",
        translation_key="hot_water_boost_remaining",
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.hw.boost_hours,
        exists_fn=lambda state: state.hw.available,
    ),
    SalusSensorDescription(
        key="heating_mode",
        translation_key="heating_mode",
        device_class=SensorDeviceClass.ENUM,
        options=["auto", "temp_hold", "manual", "off", "unknown"],
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.ch1.mode.value,
    ),
    SalusSensorDescription(
        key="frost_temperature",
        translation_key="frost_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.frost_temperature,
    ),
    SalusSensorDescription(
        key="ch1_schedule_today",
        translation_key="schedule_today",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_zone_schedule_value(ZONE_CH1),
        attributes_fn=_zone_schedule_attributes(ZONE_CH1),
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    ),
    SalusSensorDescription(
        key="ch2_schedule_today",
        translation_key="schedule_today_zone2",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_zone_schedule_value(ZONE_CH2),
        attributes_fn=_zone_schedule_attributes(ZONE_CH2),
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        exists_fn=lambda state: state.has_ch2,
    ),
    SalusSensorDescription(
        key="hw_schedule_today",
        translation_key="hot_water_schedule_today",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: describe_hot_water(
            decode_hot_water(
                state.hw.programs.get(dt_util.now().strftime("%A").lower(), "")
            )
        ),
        attributes_fn=lambda state: {
            "full_week": {
                name: decode_hot_water(program)
                for name, program in state.hw.programs.items()
            }
        },
        exists_fn=lambda state: state.hw.available,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SalusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the sensors relevant to this system's wiring."""
    coordinator = entry.runtime_data
    state = coordinator.data

    descriptions = [d for d in SENSORS if d.exists_fn(state)]
    if not coordinator.client.supports_schedules:
        descriptions = [d for d in descriptions if "schedule" not in d.key]
    if not coordinator.client.supports_boost:
        descriptions = [d for d in descriptions if "boost" not in d.key]

    entities: list[SalusEntity] = [SalusSensor(coordinator, d) for d in descriptions]
    entities.extend(
        SalusDiagnosticSensor(coordinator, d)
        for d in DIAGNOSTIC_SENSORS
        if d.exists_fn(coordinator)
    )
    async_add_entities(entities)


class SalusSensor(SalusEntity, SensorEntity):
    """A read-only value derived from the device state."""

    entity_description: SalusSensorDescription

    def __init__(
        self,
        coordinator: SalusDataUpdateCoordinator,
        description: SalusSensorDescription,
    ) -> None:
        """Bind the description to the coordinator."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Read the value out of the current state."""
        return self.entity_description.value_fn(self.state_data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Extra context, mostly decoded schedules."""
        if self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self.state_data)


@dataclass(frozen=True, kw_only=True)
class SalusDiagnosticSensorDescription(SensorEntityDescription):
    """Describes a sensor sourced from the coordinator, not the device state.

    Backend, timing and error information needs to stay readable even when
    the most recent poll failed - that is the whole point of these sensors -
    so they read from the coordinator directly rather than ``coordinator.data``
    and are never marked unavailable just because the last refresh failed.
    """

    value_fn: Callable[[SalusDataUpdateCoordinator], Any]
    exists_fn: Callable[[SalusDataUpdateCoordinator], bool] = lambda _: True


DIAGNOSTIC_SENSORS: tuple[SalusDiagnosticSensorDescription, ...] = (
    SalusDiagnosticSensorDescription(
        key="backend",
        translation_key="backend",
        device_class=SensorDeviceClass.ENUM,
        options=["api", "web"],
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator: coordinator.transport,
    ),
    SalusDiagnosticSensorDescription(
        key="system_type",
        translation_key="system_type",
        device_class=SensorDeviceClass.ENUM,
        options=["ch1", "ch1_ch2", "ch1_hw"],
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator: coordinator.data.system_type.name.lower(),
    ),
    SalusDiagnosticSensorDescription(
        key="firmware",
        translation_key="firmware",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator: coordinator.data.firmware,
        exists_fn=lambda coordinator: coordinator.data.firmware is not None,
    ),
    SalusDiagnosticSensorDescription(
        key="last_update",
        translation_key="last_update",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator: coordinator.last_success_at,
    ),
    SalusDiagnosticSensorDescription(
        key="last_update_duration",
        translation_key="last_update_duration",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=2,
        value_fn=lambda coordinator: coordinator.last_update_duration,
    ),
    SalusDiagnosticSensorDescription(
        key="last_error",
        translation_key="last_error",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator: coordinator.last_error or "none",
    ),
)


class SalusDiagnosticSensor(SalusEntity, SensorEntity):
    """A read-only value derived from the coordinator itself."""

    entity_description: SalusDiagnosticSensorDescription

    def __init__(
        self,
        coordinator: SalusDataUpdateCoordinator,
        description: SalusDiagnosticSensorDescription,
    ) -> None:
        """Bind the description to the coordinator."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Diagnostics stay visible even when the last poll failed."""
        return True

    @property
    def native_value(self) -> Any:
        """Read the value out of the coordinator."""
        return self.entity_description.value_fn(self.coordinator)
