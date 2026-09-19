"""Polling coordinator for the Salus iT500."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    DeviceState,
    HeatingMode,
    SalusAuthError,
    SalusClient,
    SalusConnectionError,
    SalusDeviceError,
    SalusDeviceNotFound,
    SalusError,
    SalusProtocolError,
    SalusRateLimitError,
    SalusUnsupportedFeature,
    SalusValidationError,
)
from .api.model import HeatingZoneState
from .api.schedule import decode_heating, next_change
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

#: The thermostat takes a few seconds to acknowledge a change through the
#: cloud, so a write is followed by a delayed confirming read rather than an
#: immediate one.
POST_COMMAND_DELAY = 4.0

type SalusConfigEntry = ConfigEntry[SalusDataUpdateCoordinator]


@dataclass(slots=True)
class _PendingOverride:
    """An HA-managed "advance"/"skip" hold, and when to let go of it.

    The iT500 has no native skip/advance command; both are emulated by
    holding a temperature and re-asserting it each poll until ``until``,
    at which point the zone is explicitly returned to the schedule. This
    does not survive a Home Assistant restart - a missed skip simply means
    the zone falls back to its normal schedule, never anything unsafe.
    """

    until: datetime
    temperature: float


def _hhmm_delta(value: str) -> timedelta:
    """Parse an ``HH:MM`` string into a timedelta since midnight."""
    hour, _, minute = value.partition(":")
    return timedelta(hours=int(hour), minutes=int(minute))


class SalusDataUpdateCoordinator(DataUpdateCoordinator[DeviceState]):
    """Polls the Salus cloud and serialises commands onto it."""

    config_entry: SalusConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: SalusConfigEntry,
        client: SalusClient,
        scan_interval: int = DEFAULT_SCAN_INTERVAL,
    ) -> None:
        """Set up polling at the configured interval."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.data.get('device_id')}",
            update_interval=timedelta(seconds=scan_interval),
            request_refresh_debouncer=Debouncer(
                hass,
                _LOGGER,
                cooldown=POST_COMMAND_DELAY,
                immediate=False,
            ),
        )
        self.client = client
        self.device_id = str(entry.data.get("device_id"))

        # Diagnostics bookkeeping, surfaced by sensor.py / diagnostics.py.
        self.last_success_at: datetime | None = None
        self.last_update_duration: float | None = None
        self.last_error: str | None = None

        # All writes are serialised through this so a temperature write, a
        # mode write and a boost command can never race each other.
        self._command_lock = asyncio.Lock()
        self._pending_overrides: dict[str, _PendingOverride] = {}

    @property
    def transport(self) -> str:
        """Which route is currently in use."""
        return self.client.transport

    def pending_override_until(self, zone: str) -> datetime | None:
        """When the advance/skip override on ``zone`` will release, if any."""
        override = self._pending_overrides.get(zone)
        return override.until if override else None

    def clear_pending_override(self, zone: str) -> None:
        """Forget a pending advance/skip override for ``zone``.

        Called whenever the zone's mode or temperature is set through the
        normal entity paths (climate, select), so a stale skip/advance can
        never fight a deliberate manual change.
        """
        self._pending_overrides.pop(zone, None)

    async def _async_update_data(self) -> DeviceState:
        """Fetch the latest state, translating errors for Home Assistant."""
        start = time.monotonic()
        try:
            state = await self.client.async_get_state()
        except SalusAuthError as err:
            self.last_error = str(err)
            raise ConfigEntryAuthFailed(str(err)) from err
        except SalusDeviceNotFound as err:
            self.last_error = str(err)
            raise UpdateFailed(str(err)) from err
        except SalusRateLimitError as err:
            self.last_error = str(err)
            raise UpdateFailed(
                f"{err}. Consider increasing the scan interval in the "
                "integration options."
            ) from err
        except SalusConnectionError as err:
            self.last_error = str(err)
            raise UpdateFailed(str(err)) from err
        except SalusError as err:
            self.last_error = f"Unexpected Salus error: {err}"
            raise UpdateFailed(self.last_error) from err

        self.last_update_duration = time.monotonic() - start
        self.last_success_at = dt_util.utcnow()
        self.last_error = None

        await self._async_supervise_overrides(state)
        return state

    async def async_command(
        self,
        action: Callable[[], Awaitable[Any]],
        *,
        optimistic: Callable[[DeviceState], None] | None = None,
    ) -> None:
        """Run a write, optionally update local state, then confirm.

        The optimistic callback keeps the UI responsive: Salus can take the
        better part of a minute to reflect a change, and without it the
        thermostat card snaps back to the old value. Writes are serialised
        so two commands issued in quick succession (e.g. a temperature
        change followed by a boost) can never be interleaved on the wire.
        """
        async with self._command_lock:
            try:
                await action()
            except SalusAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except SalusUnsupportedFeature as err:
                raise HomeAssistantError(str(err)) from err
            except SalusValidationError as err:
                raise ServiceValidationError(str(err)) from err
            except (SalusDeviceError, SalusProtocolError, SalusConnectionError) as err:
                raise HomeAssistantError(f"Salus command failed: {err}") from err

            if optimistic is not None and self.data is not None:
                optimistic(self.data)
                self.async_update_listeners()

        await self.async_request_refresh()

    # --- Advance / skip -------------------------------------------------

    async def async_advance_zone(self, zone: str) -> None:
        """Jump to the next scheduled temperature immediately.

        A one-shot hold at the upcoming slot's own temperature - if the
        thermostat reverts to the schedule exactly when that slot's own
        start time arrives, the effective temperature does not change, so
        no ongoing supervision is required.
        """
        zone_state = self._heating_zone(zone)
        upcoming = self._next_slot(zone_state)
        if upcoming is None:
            raise HomeAssistantError("No further schedule change today to advance to")

        self.clear_pending_override(zone)
        await self.async_command(
            lambda: self.client.async_set_target_temperature(
                zone, upcoming["temperature"]
            ),
            optimistic=lambda state: _apply_hold(state, zone, upcoming["temperature"]),
        )

    async def async_skip_zone(self, zone: str) -> None:
        """Skip the next scheduled change, applying the one after it instead."""
        zone_state = self._heating_zone(zone)
        if zone_state.target_temperature is None:
            raise HomeAssistantError("No current setpoint to hold")

        now = dt_util.now()
        upcoming = self._next_slot(zone_state)
        after = (
            self._next_slot(zone_state, after=upcoming["time"])
            if upcoming is not None
            else None
        )
        until = self._slot_datetime(zone_state, now, after)
        hold_temperature = zone_state.target_temperature

        await self.async_command(
            lambda: self.client.async_set_target_temperature(zone, hold_temperature),
            optimistic=lambda state: _apply_hold(state, zone, hold_temperature),
        )
        self._pending_overrides[zone] = _PendingOverride(
            until=until, temperature=hold_temperature
        )

    async def async_cancel_override_zone(self, zone: str) -> None:
        """Cancel a temporary hold (manual, advance or skip) and resume the schedule."""
        self.clear_pending_override(zone)
        await self.async_command(
            lambda: self.client.async_set_heating_mode(zone, HeatingMode.AUTO),
            optimistic=lambda state: setattr(state.zone(zone), "mode", HeatingMode.AUTO),
        )

    def _heating_zone(self, zone: str) -> HeatingZoneState:
        """Look up a heating zone, asserting it really is one."""
        zone_state = self.data.zone(zone)
        if not isinstance(zone_state, HeatingZoneState):
            raise HomeAssistantError(f"'{zone}' is not a heating zone")
        return zone_state

    def _next_slot(
        self, zone_state: HeatingZoneState, *, after: str | None = None
    ) -> dict[str, Any] | None:
        """The next schedule slot today after ``after`` (default: now)."""
        if not self.client.supports_schedules:
            return None
        day = dt_util.now().strftime("%A").lower()
        slots = decode_heating(zone_state.programs.get(day, ""))
        stamp = after if after is not None else dt_util.now().strftime("%H:%M")
        return next_change(slots, stamp)

    def _slot_datetime(
        self, zone_state: HeatingZoneState, now: datetime, slot: dict[str, Any] | None
    ) -> datetime:
        """Resolve a schedule slot to an absolute datetime.

        Falls back to tomorrow's first slot, then to a flat 24h hold, if
        there is nothing left to transition to today.
        """
        if slot is not None:
            return dt_util.start_of_local_day(now) + _hhmm_delta(slot["time"])

        tomorrow = now + timedelta(days=1)
        tomorrow_slots = decode_heating(
            zone_state.programs.get(tomorrow.strftime("%A").lower(), "")
        )
        if tomorrow_slots:
            first = sorted(tomorrow_slots, key=lambda s: s["time"])[0]
            return dt_util.start_of_local_day(tomorrow) + _hhmm_delta(first["time"])

        return now + timedelta(hours=24)

    async def _async_supervise_overrides(self, state: DeviceState) -> None:
        """Enforce any pending advance/skip overrides that are still in force.

        This is a Home Assistant-side emulation, not a native device
        feature: the coordinator re-applies the held temperature if the
        thermostat reverts to the schedule early, and explicitly hands
        control back at the target time. Nothing here runs if nothing is
        pending, so it costs no extra Salus traffic in the common case.
        """
        if not self._pending_overrides:
            return

        now = dt_util.now()
        for zone, override in list(self._pending_overrides.items()):
            zone_state = state.zone(zone)
            if not isinstance(zone_state, HeatingZoneState):
                del self._pending_overrides[zone]
                continue

            if now >= override.until:
                del self._pending_overrides[zone]
                try:
                    await self.client.async_set_heating_mode(zone, HeatingMode.AUTO)
                except SalusError as err:
                    _LOGGER.warning("Could not clear %s override: %s", zone, err)
                continue

            if zone_state.mode is not HeatingMode.TEMP_HOLD:
                _LOGGER.debug(
                    "%s left temporary hold early; re-applying override until %s",
                    zone,
                    override.until,
                )
                try:
                    await self.client.async_set_target_temperature(
                        zone, override.temperature
                    )
                except SalusError as err:
                    _LOGGER.warning("Could not re-apply %s override: %s", zone, err)


def _apply_hold(state: DeviceState, zone: str, temperature: float) -> None:
    """Optimistically reflect a temporary hold at ``temperature`` on ``zone``."""
    zone_state = state.zone(zone)
    zone_state.target_temperature = temperature
    if isinstance(zone_state, HeatingZoneState):
        zone_state.mode = HeatingMode.TEMP_HOLD

