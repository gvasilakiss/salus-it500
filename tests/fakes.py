"""Test doubles shared across the Salus iT500 entity/coordinator tests."""

from __future__ import annotations

from dataclasses import dataclass, field

from custom_components.salus_it500.api.client import SalusClient
from custom_components.salus_it500.api.model import (
    DeviceState,
    HeatingMode,
    HeatingZoneState,
    HotWaterMode,
    HotWaterState,
    SystemType,
)


def make_state(
    *,
    device_id: str = "33591764",
    system_type: SystemType = SystemType.CH1,
    ch1_temperature: float = 19.5,
    ch1_target: float = 20.0,
    ch1_mode: HeatingMode = HeatingMode.AUTO,
    online: bool = True,
    hw_available: bool = False,
) -> DeviceState:
    """Build a plausible DeviceState without needing a real transport."""
    ch1 = HeatingZoneState(
        prefix="A",
        available=True,
        current_temperature=ch1_temperature,
        target_temperature=ch1_target,
        mode=ch1_mode,
        programs={
            "monday": "",
            "tuesday": "",
            "wednesday": "",
            "thursday": "",
            "friday": "",
            "saturday": "",
            "sunday": "",
        },
    )
    ch2 = HeatingZoneState(prefix="B")
    hw = HotWaterState(available=hw_available)
    state = DeviceState(
        device_id=device_id,
        name=f"Salus iT500 {device_id}",
        system_type=system_type,
        online=online,
        frost_temperature=7.0,
        ch1=ch1,
        ch2=ch2,
        hw=hw,
    )
    return state


@dataclass
class FakeSalusClient(SalusClient):
    """An in-memory stand-in for ArrayentClient/WebClient used in entity tests."""

    state: DeviceState
    transport: str = "api"
    supports_schedules: bool = True
    supports_second_zone: bool = True
    supports_boost: bool = True
    supports_calibration: bool = True
    supports_differential: bool = True
    supports_holiday: bool = True
    calls: list[tuple[str, tuple]] = field(default_factory=list)

    async def async_login(self) -> None:
        return None

    async def async_list_devices(self) -> list[dict]:
        return [{"device_id": self.state.device_id, "name": self.state.name}]

    async def async_get_state(self) -> DeviceState:
        return self.state

    async def async_close(self) -> None:
        return None

    async def async_set_target_temperature(self, zone: str, temperature: float) -> None:
        self.calls.append(("set_target_temperature", (zone, temperature)))
        zone_state = self.state.zone(zone)
        zone_state.target_temperature = temperature
        zone_state.mode = HeatingMode.TEMP_HOLD

    async def async_set_heating_mode(self, zone: str, mode: HeatingMode) -> None:
        self.calls.append(("set_heating_mode", (zone, mode)))
        self.state.zone(zone).mode = mode

    async def async_set_hot_water_mode(self, mode: HotWaterMode) -> None:
        self.calls.append(("set_hot_water_mode", (mode,)))
        self.state.hw.mode = mode
        self.state.hw.on = mode is HotWaterMode.ON

    async def async_set_frost_temperature(self, temperature: float) -> None:
        self.calls.append(("set_frost_temperature", (temperature,)))
        self.state.frost_temperature = temperature

    async def async_set_boost(self, zone: str, hours: int) -> None:
        self.calls.append(("set_boost", (zone, hours)))
        self.state.zone(zone).boost_hours = hours

    async def async_set_temperature_offset(self, offset: float) -> None:
        self.calls.append(("set_temperature_offset", (offset,)))
        self.state.temperature_offset = offset

    async def async_set_span(self, span: float) -> None:
        self.calls.append(("set_span", (span,)))
        self.state.span = span

    async def async_set_program(self, zone: str, day: str, program: str) -> None:
        self.calls.append(("set_program", (zone, day, program)))
        self.state.zone(zone).programs[day] = program

    async def async_set_holiday(
        self, enabled: bool, start: str | None = None, end: str | None = None
    ) -> None:
        self.calls.append(("set_holiday", (enabled, start, end)))
        self.state.holiday_active = enabled
        if start:
            self.state.holiday_start = start
        if end:
            self.state.holiday_end = end
