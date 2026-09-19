"""Boost and schedule-override buttons for the Salus iT500."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import MAX_BOOST_HOURS, ZONE_CH1, ZONE_CH2, ZONE_HW
from .coordinator import SalusConfigEntry, SalusDataUpdateCoordinator
from .entity import SalusEntity, SalusZoneEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SalusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add boost and schedule-override buttons for every wired zone."""
    coordinator = entry.runtime_data
    entities: list[SalusEntity] = []

    heating_zones = [ZONE_CH1]
    if coordinator.data.has_ch2 and coordinator.client.supports_second_zone:
        heating_zones.append(ZONE_CH2)

    boost_zones = list(heating_zones)
    if coordinator.data.hw.available:
        boost_zones.append(ZONE_HW)

    if coordinator.client.supports_boost:
        for zone in boost_zones:
            for hours in range(1, MAX_BOOST_HOURS + 1):
                entities.append(SalusBoostButton(coordinator, zone, hours))
            entities.append(SalusCancelBoostButton(coordinator, zone))

    if coordinator.client.supports_schedules:
        for zone in heating_zones:
            entities.append(SalusAdvanceButton(coordinator, zone))
            entities.append(SalusSkipButton(coordinator, zone))
            entities.append(SalusCancelOverrideButton(coordinator, zone))

    async_add_entities(entities)


def _zone_prefix(zone_label: str, zone: str) -> str:
    """CH1 is implicit everywhere else in this integration; keep buttons the same."""
    return "" if zone == ZONE_CH1 else f"{zone_label} "


class SalusBoostButton(SalusZoneEntity, ButtonEntity):
    """Start a timed boost that reverts on its own."""

    _attr_icon = "mdi:fire"

    def __init__(
        self, coordinator: SalusDataUpdateCoordinator, zone: str, hours: int
    ) -> None:
        """One button per boost duration, which is how the app presents it."""
        super().__init__(coordinator, zone, f"boost_{hours}h")
        self._hours = hours
        self._attr_name = f"{_zone_prefix(self.zone_label, zone)}Boost {hours}h"

    async def async_press(self) -> None:
        """Start the boost."""
        await self.coordinator.async_command(
            lambda: self.coordinator.client.async_set_boost(self._zone, self._hours)
        )


class SalusCancelBoostButton(SalusZoneEntity, ButtonEntity):
    """Cancel a running boost."""

    _attr_icon = "mdi:fire-off"

    def __init__(self, coordinator: SalusDataUpdateCoordinator, zone: str) -> None:
        """Set the unique ID and display name."""
        super().__init__(coordinator, zone, "boost_cancel")
        self._attr_name = f"{_zone_prefix(self.zone_label, zone)}Cancel boost"

    async def async_press(self) -> None:
        """Set the boost countdown to zero."""
        await self.coordinator.async_command(
            lambda: self.coordinator.client.async_set_boost(self._zone, 0)
        )


class SalusAdvanceButton(SalusZoneEntity, ButtonEntity):
    """Jump to the next scheduled temperature immediately.

    A one-shot hold at the upcoming slot's own temperature. See
    :meth:`SalusDataUpdateCoordinator.async_advance_zone`.
    """

    _attr_icon = "mdi:skip-forward"

    def __init__(self, coordinator: SalusDataUpdateCoordinator, zone: str) -> None:
        """Set the unique ID and display name."""
        super().__init__(coordinator, zone, "advance")
        self._attr_name = f"{_zone_prefix(self.zone_label, zone)}Advance"

    async def async_press(self) -> None:
        """Advance to the next scheduled temperature."""
        await self.coordinator.async_advance_zone(self._zone)


class SalusSkipButton(SalusZoneEntity, ButtonEntity):
    """Skip the next scheduled change, applying the one after it instead.

    The iT500 has no native "skip" command - this holds the current
    temperature and lets the coordinator supervise the hold until the
    schedule entry after the skipped one. See
    :meth:`SalusDataUpdateCoordinator.async_skip_zone`.
    """

    _attr_icon = "mdi:skip-next"

    def __init__(self, coordinator: SalusDataUpdateCoordinator, zone: str) -> None:
        """Set the unique ID and display name."""
        super().__init__(coordinator, zone, "skip_next")
        self._attr_name = f"{_zone_prefix(self.zone_label, zone)}Skip next change"

    async def async_press(self) -> None:
        """Skip the next scheduled change."""
        await self.coordinator.async_skip_zone(self._zone)


class SalusCancelOverrideButton(SalusZoneEntity, ButtonEntity):
    """Cancel any temporary hold - manual, advance or skip - and resume the schedule."""

    _attr_icon = "mdi:calendar-sync"

    def __init__(self, coordinator: SalusDataUpdateCoordinator, zone: str) -> None:
        """Set the unique ID and display name."""
        super().__init__(coordinator, zone, "cancel_override")
        self._attr_name = f"{_zone_prefix(self.zone_label, zone)}Cancel override"

    async def async_press(self) -> None:
        """Return the zone to following its schedule."""
        await self.coordinator.async_cancel_override_zone(self._zone)

