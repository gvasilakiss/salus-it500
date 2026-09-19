"""Attribute map and normalised state model for the Salus iT500.

The iT500 cloud exposes a flat list of ``name``/``value`` attribute pairs.
Names are a one-character zone prefix followed by a two-character index, or
``S`` followed by a two-digit index for system-wide settings.

Zone prefixes:  A = central heating 1, B = central heating 2, C = hot water.

Temperatures are integers scaled by 100 (2150 == 21.5 degrees C).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any


class Prefix(str, Enum):
    """Zone attribute prefixes."""

    CH1 = "A"
    CH2 = "B"
    HW = "C"


class SystemAttr(str, Enum):
    """System-wide attributes (``S`` namespace)."""

    OTA_STATUS = "S00"
    ENERGY_SAVE_STATUS = "S01"
    UPGRADE_AVAILABLE = "S02"
    BATTERY_STATUS = "S03"
    TIME_ZONE = "S04"
    SYSTEM_MODE = "S05"
    SYSTEM_TYPE = "S06"
    TEMPERATURE_UNIT = "S07"
    HOUR_FORMAT = "S08"
    FROST_TEMPERATURE = "S09"
    HOLIDAY_OPTION = "S10"
    HOLIDAY_START = "S11"
    HOLIDAY_END = "S12"
    FIRMWARE_VERSION = "S13"
    DAYLIGHT_SAVING = "S14"
    SPAN = "S15"
    DISPLAY_TOLERANCE = "S16"
    DISPLAY_OFFSET = "S17"
    CH2_DISPLAY_OFFSET = "S18"
    DELAY_START_ENABLE = "S19"
    DESCRIPTION = "desc"


class ZoneAttr(str, Enum):
    """Per-zone attributes. Combine with a :class:`Prefix`."""

    PROGRAM_MON = "00"
    PROGRAM_TUE = "01"
    PROGRAM_WED = "02"
    PROGRAM_THU = "03"
    PROGRAM_FRI = "04"
    PROGRAM_SAT = "05"
    PROGRAM_SUN = "06"

    # Hot water only
    HW_MODE = "42"
    HW_BOOST_HOURS = "43"
    HW_SCHEDULE_TYPE = "44"
    HW_ON_OFF_STATUS = "45"
    HW_RUNNING_MANUAL_MODE = "46"

    # Central heating only
    CH_ROOM_TEMPERATURE = "84"
    CH_SETPOINT = "85"
    CH_SCHEDULE_TYPE = "86"
    CH_RELAY_STATUS = "87"
    CH_TEMP_HOLD_MODE = "88"
    CH_OFF_MODE = "89"
    CH_FROST_ACTIVE = "90"
    CH_BOOST_HOURS = "91"
    CH_MANUAL_MODE = "92"
    CH_MANUAL_SETPOINT = "93"
    CH_AUTO_SETPOINT = "94"


DAY_ATTR: dict[str, ZoneAttr] = {
    "monday": ZoneAttr.PROGRAM_MON,
    "tuesday": ZoneAttr.PROGRAM_TUE,
    "wednesday": ZoneAttr.PROGRAM_WED,
    "thursday": ZoneAttr.PROGRAM_THU,
    "friday": ZoneAttr.PROGRAM_FRI,
    "saturday": ZoneAttr.PROGRAM_SAT,
    "sunday": ZoneAttr.PROGRAM_SUN,
}


class SystemType(IntEnum):
    """What the boiler wiring centre is driving."""

    CH1 = 0
    CH1_CH2 = 1
    CH1_HW = 2


class ScheduleType(IntEnum):
    """How the weekly program is grouped."""

    ALL_DAYS = 0
    FIVE_TWO = 1
    INDEPENDENT = 2


class HeatingMode(str, Enum):
    """Consolidated central-heating mode.

    The thermostat stores three independent flags (off / manual / temp-hold);
    this collapses them into something a user can reason about.
    """

    AUTO = "auto"  # follow the weekly schedule
    TEMP_HOLD = "temp_hold"  # hold until the next scheduled change
    MANUAL = "manual"  # hold indefinitely, schedule ignored
    OFF = "off"
    UNKNOWN = "unknown"


# (off_mode, manual_mode, temp_hold_mode)
HEATING_MODE_FLAGS: dict[HeatingMode, tuple[int, int, int]] = {
    HeatingMode.OFF: (1, 0, 0),
    HeatingMode.MANUAL: (0, 1, 0),
    HeatingMode.TEMP_HOLD: (0, 0, 1),
    HeatingMode.AUTO: (0, 0, 0),
}
FLAGS_TO_HEATING_MODE = {v: k for k, v in HEATING_MODE_FLAGS.items()}


class HotWaterMode(IntEnum):
    """Hot water operating mode."""

    AUTO = 0  # follow schedule
    ONCE = 1  # on at first scheduled slot, off at last, then revert
    ON = 2  # permanently on
    OFF = 3  # permanently off


def _to_int(value: Any, default: int | None = None) -> int | None:
    """Best-effort int conversion; Salus returns blanks and floats freely."""
    if value is None:
        return default
    try:
        text = str(value).strip()
        if not text:
            return default
        return int(float(text))
    except (TypeError, ValueError):
        return default


def _scaled(
    value: Any, *, fahrenheit: bool = False, delta: bool = False
) -> float | None:
    """Convert a x100-scaled integer into degrees Celsius.

    Salus stores every temperature as an integer scaled by 100, in whichever
    unit ``S07`` selects on that account. Home Assistant only ever sees
    Celsius (see :class:`UnitOfTemperature`), so the Fahrenheit conversion
    happens exactly once, here, at the read boundary - never anywhere else,
    and never twice. ``delta=True`` converts a temperature *difference*
    (calibration offset, switching differential) rather than an absolute
    reading, which does not carry the 32-degree Fahrenheit offset.

    Only the Celsius branch (``fahrenheit=False``, S07 absent or ``0``) has
    been verified against a real device. The Fahrenheit branch follows the
    documented 0/1 convention but has no hardware to confirm it against.
    """
    raw = _to_int(value)
    if raw is None:
        return None
    degrees = raw / 100
    if fahrenheit:
        degrees = degrees / 1.8 if delta else (degrees - 32) / 1.8
    return round(degrees, 2)


def is_fahrenheit(attrs: dict[str, str]) -> bool:
    """Whether ``S07`` selects Fahrenheit rather than Celsius for this account."""
    return _to_int(attrs.get(SystemAttr.TEMPERATURE_UNIT.value), 0) == 1


@dataclass(slots=True)
class HeatingZoneState:
    """Normalised state for one central-heating zone."""

    prefix: str
    available: bool = False
    current_temperature: float | None = None
    target_temperature: float | None = None
    manual_setpoint: float | None = None
    auto_setpoint: float | None = None
    relay_on: bool = False
    frost_active: bool = False
    boost_hours: int = 0
    mode: HeatingMode = HeatingMode.UNKNOWN
    schedule_type: ScheduleType | None = None
    programs: dict[str, str] = field(default_factory=dict)

    @property
    def boost_active(self) -> bool:
        """Whether a boost is currently counting down."""
        return self.boost_hours > 0


@dataclass(slots=True)
class HotWaterState:
    """Normalised state for the hot water zone."""

    available: bool = False
    on: bool = False
    mode: HotWaterMode = HotWaterMode.OFF
    running_manual: bool = False
    boost_hours: int = 0
    schedule_type: ScheduleType | None = None
    programs: dict[str, str] = field(default_factory=dict)

    @property
    def boost_active(self) -> bool:
        """Whether a boost is currently counting down."""
        return self.boost_hours > 0


@dataclass(slots=True)
class DeviceState:
    """Everything the integration knows about one iT500 wiring centre."""

    device_id: str
    name: str = "Salus iT500"
    online: bool = True
    system_type: SystemType = SystemType.CH1
    firmware: str | None = None
    frost_temperature: float | None = None
    temperature_offset: float | None = None
    span: float | None = None
    battery_low: bool | None = None
    holiday_active: bool = False
    holiday_start: str | None = None
    holiday_end: str | None = None
    source_unit_fahrenheit: bool = False
    ch1: HeatingZoneState = field(default_factory=lambda: HeatingZoneState("A"))
    ch2: HeatingZoneState = field(default_factory=lambda: HeatingZoneState("B"))
    hw: HotWaterState = field(default_factory=HotWaterState)
    raw: dict[str, str] = field(default_factory=dict)

    @property
    def has_ch2(self) -> bool:
        """Whether a second heating zone is wired up."""
        return self.system_type is SystemType.CH1_CH2

    @property
    def has_hot_water(self) -> bool:
        """Whether a hot water channel is wired up."""
        return self.system_type is SystemType.CH1_HW

    def zone(self, name: str) -> HeatingZoneState | HotWaterState:
        """Look up a zone by its short name."""
        return {"ch1": self.ch1, "ch2": self.ch2, "hw": self.hw}[name]


def parse_attributes(device_id: str, attrs: dict[str, str]) -> DeviceState:
    """Build a :class:`DeviceState` from a raw attribute dictionary."""
    state = DeviceState(device_id=device_id, raw=dict(attrs))
    fahrenheit = is_fahrenheit(attrs)
    state.source_unit_fahrenheit = fahrenheit

    state.name = attrs.get(SystemAttr.DESCRIPTION.value) or f"Salus iT500 {device_id}"
    state.system_type = SystemType(
        _to_int(attrs.get(SystemAttr.SYSTEM_TYPE.value), 0) or 0
    )
    state.firmware = attrs.get(SystemAttr.FIRMWARE_VERSION.value)
    state.frost_temperature = _scaled(
        attrs.get(SystemAttr.FROST_TEMPERATURE.value), fahrenheit=fahrenheit
    )
    state.temperature_offset = _scaled(
        attrs.get(SystemAttr.DISPLAY_OFFSET.value), fahrenheit=fahrenheit, delta=True
    )
    state.span = _scaled(
        attrs.get(SystemAttr.SPAN.value), fahrenheit=fahrenheit, delta=True
    )

    battery = _to_int(attrs.get(SystemAttr.BATTERY_STATUS.value))
    state.battery_low = None if battery is None else bool(battery)

    state.holiday_active = bool(_to_int(attrs.get(SystemAttr.HOLIDAY_OPTION.value), 0))
    state.holiday_start = attrs.get(SystemAttr.HOLIDAY_START.value)
    state.holiday_end = attrs.get(SystemAttr.HOLIDAY_END.value)

    state.ch1 = _parse_heating(Prefix.CH1, attrs, fahrenheit=fahrenheit)
    state.ch2 = _parse_heating(Prefix.CH2, attrs, fahrenheit=fahrenheit)
    state.hw = _parse_hot_water(attrs)

    return state


def _parse_heating(
    prefix: Prefix, attrs: dict[str, str], *, fahrenheit: bool = False
) -> HeatingZoneState:
    """Extract one heating zone from the raw attribute map."""

    def get(attr: ZoneAttr) -> str | None:
        return attrs.get(prefix.value + attr.value)

    zone = HeatingZoneState(prefix=prefix.value)
    zone.current_temperature = _scaled(
        get(ZoneAttr.CH_ROOM_TEMPERATURE), fahrenheit=fahrenheit
    )
    zone.available = zone.current_temperature is not None
    zone.target_temperature = _scaled(get(ZoneAttr.CH_SETPOINT), fahrenheit=fahrenheit)
    zone.manual_setpoint = _scaled(
        get(ZoneAttr.CH_MANUAL_SETPOINT), fahrenheit=fahrenheit
    )
    zone.auto_setpoint = _scaled(get(ZoneAttr.CH_AUTO_SETPOINT), fahrenheit=fahrenheit)
    zone.relay_on = bool(_to_int(get(ZoneAttr.CH_RELAY_STATUS), 0))
    zone.frost_active = bool(_to_int(get(ZoneAttr.CH_FROST_ACTIVE), 0))
    zone.boost_hours = _to_int(get(ZoneAttr.CH_BOOST_HOURS), 0) or 0

    flags = (
        _to_int(get(ZoneAttr.CH_OFF_MODE), 0) or 0,
        _to_int(get(ZoneAttr.CH_MANUAL_MODE), 0) or 0,
        _to_int(get(ZoneAttr.CH_TEMP_HOLD_MODE), 0) or 0,
    )
    zone.mode = FLAGS_TO_HEATING_MODE.get(flags, HeatingMode.UNKNOWN)

    sched = _to_int(get(ZoneAttr.CH_SCHEDULE_TYPE))
    if sched is not None and sched in {s.value for s in ScheduleType}:
        zone.schedule_type = ScheduleType(sched)

    zone.programs = {
        day: str(attrs.get(prefix.value + attr.value) or "")
        for day, attr in DAY_ATTR.items()
    }
    return zone


def _parse_hot_water(attrs: dict[str, str]) -> HotWaterState:
    """Extract the hot water zone from the raw attribute map."""

    def get(attr: ZoneAttr) -> str | None:
        return attrs.get(Prefix.HW.value + attr.value)

    hw = HotWaterState()
    raw_status = get(ZoneAttr.HW_ON_OFF_STATUS)
    hw.available = raw_status is not None
    hw.on = bool(_to_int(raw_status, 0))
    hw.boost_hours = _to_int(get(ZoneAttr.HW_BOOST_HOURS), 0) or 0
    hw.running_manual = bool(_to_int(get(ZoneAttr.HW_RUNNING_MANUAL_MODE), 0))

    mode = _to_int(get(ZoneAttr.HW_MODE))
    if mode is not None and mode in {m.value for m in HotWaterMode}:
        hw.mode = HotWaterMode(mode)

    sched = _to_int(get(ZoneAttr.HW_SCHEDULE_TYPE))
    if sched is not None and sched in {s.value for s in ScheduleType}:
        hw.schedule_type = ScheduleType(sched)

    hw.programs = {
        day: str(attrs.get(Prefix.HW.value + attr.value) or "")
        for day, attr in DAY_ATTR.items()
    }
    return hw
