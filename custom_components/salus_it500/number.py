"""Number entities for Salus iT500 settings."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import DeviceState, SalusClient
from .const import (
    FROST_MAX_TEMP,
    FROST_MIN_TEMP,
    OFFSET_MAX,
    OFFSET_MIN,
    OFFSET_STEP,
)
from .coordinator import SalusConfigEntry, SalusDataUpdateCoordinator
from .entity import SalusEntity


@dataclass(frozen=True, kw_only=True)
class SalusNumberDescription(NumberEntityDescription):
    """Describes a writable numeric setting."""

    value_fn: Callable[[DeviceState], float | None]
    set_fn: Callable[[SalusClient, float], Awaitable[None]]
    #: Name of a ``SalusClient`` capability flag that must be true for this
    #: entity to be created, e.g. ``"supports_calibration"``. ``None`` means
    #: every transport that gets this far (frost protection) can write it.
    capability: str | None = None


NUMBERS: tuple[SalusNumberDescription, ...] = (
    SalusNumberDescription(
        key="frost_temperature",
        translation_key="frost_temperature",
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=FROST_MIN_TEMP,
        native_max_value=FROST_MAX_TEMP,
        native_step=0.5,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda state: state.frost_temperature,
        set_fn=lambda client, value: client.async_set_frost_temperature(value),
    ),
    SalusNumberDescription(
        key="temperature_offset",
        translation_key="temperature_offset",
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=OFFSET_MIN,
        native_max_value=OFFSET_MAX,
        native_step=OFFSET_STEP,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda state: state.temperature_offset,
        set_fn=lambda client, value: client.async_set_temperature_offset(value),
        capability="supports_calibration",
    ),
    SalusNumberDescription(
        key="span",
        translation_key="span",
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=0.2,
        native_max_value=1.0,
        native_step=0.1,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda state: state.span,
        set_fn=lambda client, value: client.async_set_span(value),
        capability="supports_differential",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SalusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the settings this transport can actually write."""
    coordinator = entry.runtime_data
    descriptions = [
        d
        for d in NUMBERS
        if d.capability is None or getattr(coordinator.client, d.capability, False)
    ]
    async_add_entities(SalusNumber(coordinator, d) for d in descriptions)


class SalusNumber(SalusEntity, NumberEntity):
    """A writable numeric setting on the wiring centre."""

    entity_description: SalusNumberDescription

    def __init__(
        self,
        coordinator: SalusDataUpdateCoordinator,
        description: SalusNumberDescription,
    ) -> None:
        """Bind the description to the coordinator."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        """Read the setting out of the current state."""
        return self.entity_description.value_fn(self.state_data)

    async def async_set_native_value(self, value: float) -> None:
        """Write the setting back to the thermostat."""
        await self.coordinator.async_command(
            lambda: self.entity_description.set_fn(self.coordinator.client, value)
        )
