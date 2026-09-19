"""Select entities for Salus iT500 modes."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import HeatingMode, HotWaterMode
from .const import ZONE_CH1, ZONE_CH2
from .coordinator import SalusConfigEntry, SalusDataUpdateCoordinator
from .entity import SalusEntity, SalusZoneEntity

HEATING_OPTIONS = ["auto", "temp_hold", "manual", "off"]
HOT_WATER_OPTIONS = ["auto", "once", "on", "off"]

HOT_WATER_BY_NAME = {
    "auto": HotWaterMode.AUTO,
    "once": HotWaterMode.ONCE,
    "on": HotWaterMode.ON,
    "off": HotWaterMode.OFF,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SalusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add mode selectors for every wired zone."""
    coordinator = entry.runtime_data
    entities: list[SalusEntity] = [SalusHeatingModeSelect(coordinator, ZONE_CH1)]

    if coordinator.data.has_ch2 and coordinator.client.supports_second_zone:
        entities.append(SalusHeatingModeSelect(coordinator, ZONE_CH2))
    if coordinator.data.hw.available:
        entities.append(SalusHotWaterModeSelect(coordinator))

    async_add_entities(entities)


class SalusHeatingModeSelect(SalusZoneEntity, SelectEntity):
    """Expose the four heating modes directly, without HVAC mapping.

    The climate entity has to squeeze four Salus modes into three HVAC modes.
    This entity gives automations the real thing.
    """

    _attr_translation_key = "heating_mode"
    _attr_options = HEATING_OPTIONS
    _attr_icon = "mdi:thermostat"

    def __init__(self, coordinator: SalusDataUpdateCoordinator, zone: str) -> None:
        """Set the unique ID and, for zone 2, a distinguishing name."""
        super().__init__(coordinator, zone, "mode_select")
        if zone == ZONE_CH2:
            self._attr_translation_key = "heating_mode_zone2"

    @property
    def current_option(self) -> str | None:
        """The zone's current consolidated mode."""
        mode = self.heating_zone.mode
        return None if mode is HeatingMode.UNKNOWN else mode.value

    async def async_select_option(self, option: str) -> None:
        """Apply the selected mode."""
        try:
            mode = HeatingMode(option)
        except ValueError as err:
            raise ServiceValidationError(f"Unknown heating mode {option}") from err

        self.coordinator.clear_pending_override(self._zone)
        await self.coordinator.async_command(
            lambda: self.coordinator.client.async_set_heating_mode(self._zone, mode),
            optimistic=lambda state: setattr(state.zone(self._zone), "mode", mode),
        )


class SalusHotWaterModeSelect(SalusEntity, SelectEntity):
    """Hot water mode, including the 'once' option the app offers."""

    _attr_translation_key = "hot_water_mode"
    _attr_icon = "mdi:water-thermometer"

    def __init__(self, coordinator: SalusDataUpdateCoordinator) -> None:
        """Trim the option list when the web transport is in use."""
        super().__init__(coordinator, "hot_water_mode")
        self._attr_options = (
            ["auto", "on", "off"]
            if coordinator.client.transport == "web"
            else HOT_WATER_OPTIONS
        )

    @property
    def current_option(self) -> str | None:
        """The configured hot water mode."""
        return self.state_data.hw.mode.name.lower()

    async def async_select_option(self, option: str) -> None:
        """Apply the selected hot water mode."""
        mode = HOT_WATER_BY_NAME.get(option)
        if mode is None:
            raise ServiceValidationError(f"Unknown hot water mode {option}")

        def optimistic(state) -> None:
            state.hw.mode = mode
            if mode in (HotWaterMode.ON, HotWaterMode.OFF):
                state.hw.on = mode is HotWaterMode.ON

        await self.coordinator.async_command(
            lambda: self.coordinator.client.async_set_hot_water_mode(mode),
            optimistic=optimistic,
        )
