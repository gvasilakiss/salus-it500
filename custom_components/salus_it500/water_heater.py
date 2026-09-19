"""Water heater entity for the Salus iT500 hot water channel.

The iT500 switches a hot water circuit on and off on a schedule; it has no
cylinder setpoint, so this entity advertises operation modes only.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.water_heater import (
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.const import STATE_OFF, STATE_ON, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import HotWaterMode
from .api.schedule import decode_hot_water, describe_hot_water
from .const import ZONE_HW
from .coordinator import SalusConfigEntry, SalusDataUpdateCoordinator
from .entity import SalusZoneEntity

STATE_SCHEDULE = "schedule"
STATE_ONCE = "once"

OPERATION_TO_MODE = {
    STATE_SCHEDULE: HotWaterMode.AUTO,
    STATE_ONCE: HotWaterMode.ONCE,
    STATE_ON: HotWaterMode.ON,
    STATE_OFF: HotWaterMode.OFF,
}
MODE_TO_OPERATION = {v: k for k, v in OPERATION_TO_MODE.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SalusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the hot water entity only when the device actually reports one.

    ``hw.available`` reflects whether the C45 attribute was present in the
    last read - the ground truth for what this specific wiring centre has
    wired up. ``system_type`` is a useful corroborating signal but is not
    trusted on its own, so a device that mis-reports its type can never
    conjure a hot water entity that doesn't correspond to real hardware.
    """
    coordinator = entry.runtime_data
    if coordinator.data.hw.available:
        async_add_entities([SalusWaterHeater(coordinator)])


class SalusWaterHeater(SalusZoneEntity, WaterHeaterEntity):
    """The hot water channel as a water heater entity."""

    _attr_supported_features = WaterHeaterEntityFeature.OPERATION_MODE
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_translation_key = "hot_water"
    _attr_name = "Hot water"

    def __init__(self, coordinator: SalusDataUpdateCoordinator) -> None:
        """Advertise the modes the active transport can reach."""
        super().__init__(coordinator, ZONE_HW, "water_heater")
        if coordinator.client.transport == "web":
            self._attr_operation_list = [STATE_SCHEDULE, STATE_ON, STATE_OFF]
        else:
            self._attr_operation_list = [
                STATE_SCHEDULE,
                STATE_ONCE,
                STATE_ON,
                STATE_OFF,
            ]

    @property
    def current_operation(self) -> str | None:
        """The configured mode, not the momentary relay state."""
        return MODE_TO_OPERATION.get(self.hot_water.mode)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Relay state, boost countdown and today's switching times."""
        hot_water = self.hot_water
        attributes: dict[str, Any] = {
            "hot_water_on": hot_water.on,
            "boost_remaining_hours": hot_water.boost_hours,
            "running_manual": hot_water.running_manual,
        }
        if hot_water.schedule_type is not None:
            attributes["schedule_type"] = hot_water.schedule_type.name.lower()

        if self.coordinator.client.supports_schedules:
            day = dt_util.now().strftime("%A").lower()
            slots = decode_hot_water(hot_water.programs.get(day, ""))
            if slots:
                attributes["schedule_today"] = describe_hot_water(slots)

        return attributes

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        """Change the hot water mode."""
        mode = OPERATION_TO_MODE.get(operation_mode)
        if mode is None:
            raise ServiceValidationError(f"Unknown hot water mode {operation_mode}")

        def optimistic(state) -> None:
            state.hw.mode = mode
            if mode is HotWaterMode.ON:
                state.hw.on = True
            elif mode is HotWaterMode.OFF:
                state.hw.on = False

        await self.coordinator.async_command(
            lambda: self.coordinator.client.async_set_hot_water_mode(mode),
            optimistic=optimistic,
        )
