"""Common interface implemented by every Salus transport."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .exceptions import SalusUnsupportedFeature
from .model import DeviceState, HeatingMode, HotWaterMode


class SalusClient(ABC):
    """A way of talking to one iT500 wiring centre."""

    #: Short identifier used in logs, diagnostics and the options flow.
    transport: str = "unknown"

    #: Operations this transport can actually perform. Hot water and battery
    #: are deliberately absent here: those depend on what the wiring centre
    #: itself reports (see ``DeviceState.hw`` / ``battery_low``), not on which
    #: transport is in use, so they are never assumed at the transport level.
    supports_schedules: bool = False
    supports_second_zone: bool = False
    supports_boost: bool = False
    supports_calibration: bool = False
    supports_differential: bool = False
    supports_holiday: bool = False

    @abstractmethod
    async def async_login(self) -> None:
        """Establish a session. Raises SalusAuthError on bad credentials."""

    @abstractmethod
    async def async_list_devices(self) -> list[dict[str, Any]]:
        """Return the devices visible on this account."""

    @abstractmethod
    async def async_get_state(self) -> DeviceState:
        """Fetch and normalise the current device state."""

    @abstractmethod
    async def async_close(self) -> None:
        """Release any resources held by the transport."""

    # --- Commands ----------------------------------------------------------

    @abstractmethod
    async def async_set_target_temperature(self, zone: str, temperature: float) -> None:
        """Set the heating setpoint for ``zone`` ('ch1' or 'ch2')."""

    @abstractmethod
    async def async_set_heating_mode(self, zone: str, mode: HeatingMode) -> None:
        """Switch a heating zone between auto, hold, manual and off."""

    @abstractmethod
    async def async_set_hot_water_mode(self, mode: HotWaterMode) -> None:
        """Set the hot water operating mode."""

    async def async_set_boost(self, zone: str, hours: int) -> None:
        """Start or cancel a boost. ``hours`` of 0 cancels."""
        raise SalusUnsupportedFeature(
            f"{self.transport} transport cannot control boost"
        )

    @abstractmethod
    async def async_set_frost_temperature(self, temperature: float) -> None:
        """Set the frost-protection setpoint."""

    async def async_set_temperature_offset(self, offset: float) -> None:
        """Calibrate the reported room temperature."""
        raise SalusUnsupportedFeature(
            f"{self.transport} transport cannot set the temperature offset"
        )

    async def async_set_span(self, span: float) -> None:
        """Set the switching differential."""
        raise SalusUnsupportedFeature(f"{self.transport} transport cannot set the span")

    async def async_set_program(self, zone: str, day: str, program: str) -> None:
        """Write one day of the weekly program."""
        raise SalusUnsupportedFeature(
            f"{self.transport} transport cannot write schedules"
        )

    async def async_set_holiday(
        self, enabled: bool, start: str | None = None, end: str | None = None
    ) -> None:
        """Enable or disable holiday mode."""
        raise SalusUnsupportedFeature(
            f"{self.transport} transport cannot set holiday mode"
        )
