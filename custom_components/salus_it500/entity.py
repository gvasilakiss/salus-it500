"""Base entity shared by every Salus iT500 platform."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import DeviceState, HeatingZoneState, HotWaterState
from .const import DOMAIN, MANUFACTURER, ZONE_CH1, ZONE_CH2, ZONE_HW
from .coordinator import SalusDataUpdateCoordinator

ZONE_LABEL = {
    ZONE_CH1: "Heating",
    ZONE_CH2: "Heating zone 2",
    ZONE_HW: "Hot water",
}


class SalusEntity(CoordinatorEntity[SalusDataUpdateCoordinator]):
    """Ties an entity to the single iT500 device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SalusDataUpdateCoordinator, key: str) -> None:
        """Register the entity against the wiring centre."""
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{coordinator.device_id}_{key}"

    @property
    def state_data(self) -> DeviceState:
        """The most recent normalised device state."""
        return self.coordinator.data

    @property
    def device_info(self) -> DeviceInfo:
        """All entities belong to one physical wiring centre."""
        state = self.coordinator.data
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device_id)},
            manufacturer=MANUFACTURER,
            model="iT500",
            name=state.name if state else f"Salus iT500 {self.coordinator.device_id}",
            sw_version=state.firmware if state else None,
            configuration_url=(
                "https://salus-it500.com/public/control.php"
                f"?devId={self.coordinator.device_id}"
            ),
        )

    @property
    def available(self) -> bool:
        """Offline thermostats should not report stale readings as truth."""
        return (
            super().available
            and self.coordinator.data is not None
            and self.coordinator.data.online
        )


class SalusZoneEntity(SalusEntity):
    """An entity scoped to one heating or hot water zone."""

    def __init__(
        self,
        coordinator: SalusDataUpdateCoordinator,
        zone: str,
        key: str,
    ) -> None:
        """Record which zone this entity speaks for."""
        super().__init__(coordinator, f"{zone}_{key}")
        self._zone = zone

    @property
    def zone(self) -> HeatingZoneState | HotWaterState:
        """The zone's slice of the device state."""
        return self.coordinator.data.zone(self._zone)

    @property
    def heating_zone(self) -> HeatingZoneState:
        """The zone, typed as central heating."""
        zone = self.zone
        assert isinstance(zone, HeatingZoneState)
        return zone

    @property
    def hot_water(self) -> HotWaterState:
        """The zone, typed as hot water."""
        zone = self.zone
        assert isinstance(zone, HotWaterState)
        return zone

    @property
    def zone_label(self) -> str:
        """Human-readable zone name for entity naming."""
        return ZONE_LABEL[self._zone]
