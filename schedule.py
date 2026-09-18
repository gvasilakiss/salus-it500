"""Encode and decode the iT500 weekly program strings.

Each daily program is a run of 4-character groups. Every character carries a
small integer as ``ord(char) - 48``, so ``'0'`` is 0, ``':'`` is 10 and
``'X'`` is 40.

Heating groups are ``HH MM TT tt``: hour, minute, whole degrees, tenths.
``"5XE0"`` therefore means 05:40 at 21.0 degrees.

Hot water groups are ``ON_H ON_M OFF_H OFF_M`` - a pair of switching times
with no temperature.
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict

_LOGGER = logging.getLogger(__name__)

_OFFSET = 48
_GROUP = 4


class HeatingSlot(TypedDict):
    """One heating schedule entry."""

    time: str
    temperature: float


class HotWaterSlot(TypedDict):
    """One hot water schedule entry."""

    on_time: str
    off_time: str


def _decode(char: str) -> int:
    return ord(char) - _OFFSET


def _encode(value: int) -> str:
    if not 0 <= value <= 74:  # chr(122) == 'z' is the practical ceiling
        raise ValueError(f"Value {value} cannot be encoded")
    return chr(value + _OFFSET)


def _hhmm(text: str) -> tuple[int, int]:
    """Parse ``HH:MM`` or ``HHMM`` into a (hour, minute) pair."""
    cleaned = text.strip()
    if ":" in cleaned:
        hour_s, _, minute_s = cleaned.partition(":")
    else:
        hour_s, minute_s = cleaned[:2], cleaned[2:4]
    hour, minute = int(hour_s), int(minute_s)
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"'{text}' is not a valid time of day")
    return hour, minute


def _groups(raw: Any) -> list[str]:
    text = str(raw or "").strip()
    return [
        text[i : i + _GROUP]
        for i in range(0, len(text) - len(text) % _GROUP, _GROUP)
    ]


def decode_heating(raw: Any) -> list[HeatingSlot]:
    """Decode a heating program string into time/temperature slots."""
    slots: list[HeatingSlot] = []
    for group in _groups(raw):
        try:
            hour, minute, whole, tenths = (_decode(c) for c in group)
        except (TypeError, ValueError):
            _LOGGER.debug("Unreadable schedule group %r", group)
            continue
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            continue
        if not (0 <= whole <= 50 and 0 <= tenths <= 9):
            continue
        slots.append(
            HeatingSlot(
                time=f"{hour:02d}:{minute:02d}",
                temperature=round(whole + tenths / 10, 1),
            )
        )
    return slots


def encode_heating(slots: list[dict[str, Any]]) -> str:
    """Encode time/temperature slots into a heating program string."""
    parts: list[str] = []
    for slot in slots:
        hour, minute = _hhmm(str(slot.get("time", "00:00")))
        temperature = float(slot.get("temperature", 20.0))
        whole = int(temperature)
        tenths = int(round((temperature - whole) * 10))
        if tenths == 10:  # rounding spill, e.g. 20.999
            whole, tenths = whole + 1, 0
        parts.append(
            _encode(hour) + _encode(minute) + _encode(whole) + _encode(tenths)
        )
    return "".join(parts)


def decode_hot_water(raw: Any) -> list[HotWaterSlot]:
    """Decode a hot water program string into on/off slots."""
    slots: list[HotWaterSlot] = []
    for group in _groups(raw):
        try:
            on_h, on_m, off_h, off_m = (_decode(c) for c in group)
        except (TypeError, ValueError):
            continue
        if not (0 <= on_h <= 23 and 0 <= on_m <= 59):
            continue
        if not (0 <= off_h <= 23 and 0 <= off_m <= 59):
            continue
        slots.append(
            HotWaterSlot(
                on_time=f"{on_h:02d}:{on_m:02d}",
                off_time=f"{off_h:02d}:{off_m:02d}",
            )
        )
    return slots


def encode_hot_water(slots: list[dict[str, Any]]) -> str:
    """Encode on/off slots into a hot water program string."""
    parts: list[str] = []
    for slot in slots:
        on_h, on_m = _hhmm(str(slot.get("on_time", "00:00")))
        off_h, off_m = _hhmm(str(slot.get("off_time", "00:00")))
        parts.append(
            _encode(on_h) + _encode(on_m) + _encode(off_h) + _encode(off_m)
        )
    return "".join(parts)


def describe_heating(slots: list[HeatingSlot]) -> str:
    """Render heating slots as a single readable line."""
    if not slots:
        return "No schedule"
    return "  ".join(
        f"{s['time']} {s['temperature']:g}\u00b0C" for s in slots
    )


def describe_hot_water(slots: list[HotWaterSlot]) -> str:
    """Render hot water slots as a single readable line."""
    if not slots:
        return "No schedule"
    return "  ".join(f"{s['on_time']}-{s['off_time']}" for s in slots)


def next_change(slots: list[HeatingSlot], now_hhmm: str) -> HeatingSlot | None:
    """Return the next slot that starts strictly after ``now_hhmm``."""
    for slot in sorted(slots, key=lambda s: s["time"]):
        if slot["time"] > now_hhmm:
            return slot
    return None


def current_slot(slots: list[HeatingSlot], now_hhmm: str) -> HeatingSlot | None:
    """Return the slot that is in force at ``now_hhmm``."""
    active: HeatingSlot | None = None
    for slot in sorted(slots, key=lambda s: s["time"]):
        if slot["time"] <= now_hhmm:
            active = slot
        else:
            break
    # Before the first slot of the day the last slot of the day still applies.
    if active is None and slots:
        active = sorted(slots, key=lambda s: s["time"])[-1]
    return active
