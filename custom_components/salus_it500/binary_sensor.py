"""Binary sensors for the Salus iT500."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import DeviceState
from .coordinator import SalusConfigEntry, SalusDataUpdateCoordinator
from .entity import SalusEntity


@dataclass(frozen=True, kw_only=True)
class SalusBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a Salus binary sensor and how to read it."""

    value_fn: Callable[[DeviceState], bool | None]
    exists_fn: Callable[[DeviceState], bool] = lambda _: True


BINARY_SENSORS: tuple[SalusBinarySensorDescription, ...] = (
    SalusBinarySensorDescription(
        key="ch1_relay",
        translation_key="heating_relay",
        device_class=BinarySensorDeviceClass.HEAT,
        value_fn=lambda state: state.ch1.relay_on,
    ),
    SalusBinarySensorDescription(
        key="ch2_relay",
        translation_key="heating_relay_zone2",
        device_class=BinarySensorDeviceClass.HEAT,
        value_fn=lambda state: state.ch2.relay_on,
        exists_fn=lambda state: state.has_ch2,
    ),
    SalusBinarySensorDescription(
        key="hw_status",
        translation_key="hot_water_running",
        device_class=BinarySensorDeviceClass.HEAT,
        value_fn=lambda state: state.hw.on,
        exists_fn=lambda state: state.hw.available,
    ),
    SalusBinarySensorDescription(
        key="frost_active",
        translation_key="frost_protection",
        device_class=BinarySensorDeviceClass.COLD,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.ch1.frost_active,
    ),
    SalusBinarySensorDescription(
        key="holiday_active",
        translation_key="holiday_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.holiday_active,
    ),
    SalusBinarySensorDescription(
        key="battery_low",
        translation_key="battery_low",
        device_class=BinarySensorDeviceClass.BATTERY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.battery_low,
        exists_fn=lambda state: state.battery_low is not None,
    ),
    SalusBinarySensorDescription(
        key="boost_active",
        translation_key="boost_active",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.ch1.boost_active,
    ),
    SalusBinarySensorDescription(
        key="ch2_boost_active",
        translation_key="boost_active_zone2",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.ch2.boost_active,
        exists_fn=lambda state: state.has_ch2,
    ),
    SalusBinarySensorDescription(
        key="hw_boost_active",
        translation_key="hot_water_boost_active",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.hw.boost_active,
        exists_fn=lambda state: state.hw.available,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SalusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the binary sensors relevant to this system's wiring."""
    coordinator = entry.runtime_data
    state = coordinator.data

    descriptions = [d for d in BINARY_SENSORS if d.exists_fn(state)]
    if not coordinator.client.supports_boost:
        descriptions = [d for d in descriptions if "boost" not in d.key]
    if not coordinator.client.supports_holiday:
        descriptions = [d for d in descriptions if "holiday" not in d.key]

    entities: list[SalusEntity] = [
        SalusBinarySensor(coordinator, d) for d in descriptions
    ]
    entities.append(SalusConnectivityBinarySensor(coordinator))
    async_add_entities(entities)


class SalusBinarySensor(SalusEntity, BinarySensorEntity):
    """A boolean derived from the device state."""

    entity_description: SalusBinarySensorDescription

    def __init__(
        self,
        coordinator: SalusDataUpdateCoordinator,
        description: SalusBinarySensorDescription,
    ) -> None:
        """Bind the description to the coordinator."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Read the flag out of the current state."""
        return self.entity_description.value_fn(self.state_data)


class SalusConnectivityBinarySensor(SalusEntity, BinarySensorEntity):
    """Whether the last poll of the active backend actually succeeded.

    Deliberately independent of :class:`SalusEntity.available` - this is the
    entity that is supposed to tell you the connection is down, so it stays
    available even when everything else does not.
    """

    _attr_translation_key = "connectivity"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SalusDataUpdateCoordinator) -> None:
        """Set the unique ID."""
        super().__init__(coordinator, "connectivity")

    @property
    def available(self) -> bool:
        """Always visible - it exists to report when the backend is down."""
        return True

    @property
    def is_on(self) -> bool:
        """Whether the most recent poll succeeded."""
        return self.coordinator.last_update_success
