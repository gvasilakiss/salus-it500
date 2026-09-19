"""Switches for the Salus iT500."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import HotWaterMode
from .coordinator import SalusConfigEntry, SalusDataUpdateCoordinator
from .entity import SalusEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SalusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the switches this system supports."""
    coordinator = entry.runtime_data
    entities: list[SalusEntity] = []

    if coordinator.data.hw.available:
        entities.append(SalusHotWaterSwitch(coordinator))
    if coordinator.client.supports_holiday:
        entities.append(SalusHolidaySwitch(coordinator))

    async_add_entities(entities)


class SalusHotWaterSwitch(SalusEntity, SwitchEntity):
    """Force the hot water permanently on or off.

    This is the blunt instrument: it overwrites the mode rather than boosting.
    Use the boost buttons for a timed override that reverts on its own.
    """

    _attr_translation_key = "hot_water_override"
    _attr_icon = "mdi:water-boiler"

    def __init__(self, coordinator: SalusDataUpdateCoordinator) -> None:
        """Set the unique ID."""
        super().__init__(coordinator, "hot_water_override")

    @property
    def is_on(self) -> bool:
        """Whether hot water is pinned on."""
        return self.state_data.hw.mode is HotWaterMode.ON

    async def _async_apply(self, mode: HotWaterMode) -> None:
        def optimistic(state) -> None:
            state.hw.mode = mode
            state.hw.on = mode is HotWaterMode.ON

        await self.coordinator.async_command(
            lambda: self.coordinator.client.async_set_hot_water_mode(mode),
            optimistic=optimistic,
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Pin hot water on."""
        await self._async_apply(HotWaterMode.ON)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Pin hot water off."""
        await self._async_apply(HotWaterMode.OFF)


class SalusHolidaySwitch(SalusEntity, SwitchEntity):
    """Holiday mode, which drops every zone to frost protection."""

    _attr_translation_key = "holiday_mode"
    _attr_icon = "mdi:bag-suitcase"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: SalusDataUpdateCoordinator) -> None:
        """Set the unique ID."""
        super().__init__(coordinator, "holiday_mode")

    @property
    def is_on(self) -> bool:
        """Whether holiday mode is currently engaged."""
        return self.state_data.holiday_active

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the configured holiday window."""
        return {
            "start": self.state_data.holiday_start,
            "end": self.state_data.holiday_end,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Engage holiday mode with whatever dates are already stored."""
        await self.coordinator.async_command(
            lambda: self.coordinator.client.async_set_holiday(True),
            optimistic=lambda state: setattr(state, "holiday_active", True),
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Cancel holiday mode."""
        await self.coordinator.async_command(
            lambda: self.coordinator.client.async_set_holiday(False),
            optimistic=lambda state: setattr(state, "holiday_active", False),
        )
