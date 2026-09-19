"""Tests for the iT500 weekly-schedule codec (custom_components.salus_it500.api.schedule).

No Home Assistant dependency - these exercise pure encode/decode/validation logic.
"""

from __future__ import annotations

import pytest

from custom_components.salus_it500.api.exceptions import SalusValidationError
from custom_components.salus_it500.api.schedule import (
    current_slot,
    decode_heating,
    decode_hot_water,
    describe_heating,
    describe_hot_water,
    encode_heating,
    encode_hot_water,
    next_change,
)

# --- decode_heating ----------------------------------------------------------


def test_decode_heating_documented_example():
    """ "5XE0" is the exact example documented in the README: 05:40 at 21.0C."""
    assert decode_heating("5XE0") == [{"time": "05:40", "temperature": 21.0}]


def test_decode_heating_empty_string():
    assert decode_heating("") == []


def test_decode_heating_none():
    assert decode_heating(None) == []


def test_decode_heating_whitespace_only():
    assert decode_heating("   ") == []


def test_decode_heating_truncated_trailing_group_is_dropped():
    # One full 4-char group plus a 2-char remainder that can't form a slot.
    assert decode_heating("5XE0" + "5X") == [{"time": "05:40", "temperature": 21.0}]


def test_decode_heating_rejects_out_of_range_hour():
    # hour=30 (chr(48+30)), minute=0, whole=20, tenths=0
    bad_group = chr(48 + 30) + chr(48 + 0) + chr(48 + 20) + chr(48 + 0)
    assert decode_heating(bad_group) == []


def test_decode_heating_rejects_out_of_range_minute():
    bad_group = chr(48 + 5) + chr(48 + 70) + chr(48 + 20) + chr(48 + 0)
    assert decode_heating(bad_group) == []


def test_decode_heating_rejects_out_of_range_temperature():
    # whole=60 is above the documented 0-50 ceiling.
    bad_group = chr(48 + 5) + chr(48 + 0) + chr(48 + 60) + chr(48 + 0)
    assert decode_heating(bad_group) == []


def test_decode_heating_malformed_string_does_not_raise():
    """Garbage input degrades to an empty/partial list, never an exception."""
    assert decode_heating("not-a-schedule-!!") == []


def test_decode_heating_six_entries_roundtrip():
    entries = [
        {"time": f"{6 + h:02d}:00", "temperature": 16.0 + h * 0.5} for h in range(6)
    ]
    raw = encode_heating(entries)
    assert decode_heating(raw) == entries


# --- encode_heating / validation ---------------------------------------------


def test_encode_heating_basic():
    assert encode_heating([{"time": "05:40", "temperature": 21.0}]) == "5XE0"


def test_encode_heating_empty_list():
    assert encode_heating([]) == ""


def test_encode_heating_rejects_more_than_six_entries():
    entries = [{"time": f"{h:02d}:00", "temperature": 20.0} for h in range(7)]
    with pytest.raises(SalusValidationError):
        encode_heating(entries)


def test_encode_heating_rejects_duplicate_times():
    entries = [
        {"time": "06:00", "temperature": 20.0},
        {"time": "06:00", "temperature": 17.0},
    ]
    with pytest.raises(SalusValidationError):
        encode_heating(entries)


def test_encode_heating_rejects_invalid_time():
    with pytest.raises(SalusValidationError):
        encode_heating([{"time": "25:00", "temperature": 20.0}])


def test_encode_heating_rejects_malformed_time_string():
    with pytest.raises(SalusValidationError):
        encode_heating([{"time": "not-a-time", "temperature": 20.0}])


def test_encode_heating_rejects_temperature_out_of_range():
    with pytest.raises(SalusValidationError):
        encode_heating([{"time": "06:00", "temperature": 55.0}])


def test_encode_heating_rejects_negative_temperature():
    with pytest.raises(SalusValidationError):
        encode_heating([{"time": "06:00", "temperature": -5.0}])


def test_encode_heating_rejects_unsupported_increment():
    with pytest.raises(SalusValidationError):
        encode_heating([{"time": "06:00", "temperature": 20.3}])


def test_encode_heating_rejects_non_numeric_temperature():
    with pytest.raises(SalusValidationError):
        encode_heating([{"time": "06:00", "temperature": "warm"}])


def test_encode_heating_accepts_half_degree_step():
    # Should not raise.
    raw = encode_heating([{"time": "06:00", "temperature": 20.5}])
    assert decode_heating(raw) == [{"time": "06:00", "temperature": 20.5}]


# --- decode_hot_water / encode_hot_water -------------------------------------


def test_decode_hot_water_roundtrip():
    entries = [{"on_time": "06:30", "off_time": "08:00"}]
    raw = encode_hot_water(entries)
    assert decode_hot_water(raw) == entries


def test_decode_hot_water_empty():
    assert decode_hot_water("") == []
    assert decode_hot_water(None) == []


def test_decode_hot_water_rejects_out_of_range_times():
    bad_group = chr(48 + 30) + chr(48 + 0) + chr(48 + 8) + chr(48 + 0)
    assert decode_hot_water(bad_group) == []


def test_encode_hot_water_rejects_more_than_six_entries():
    entries = [{"on_time": f"{h:02d}:00", "off_time": f"{h:02d}:30"} for h in range(7)]
    with pytest.raises(SalusValidationError):
        encode_hot_water(entries)


def test_encode_hot_water_rejects_duplicate_slots():
    entries = [
        {"on_time": "06:00", "off_time": "08:00"},
        {"on_time": "06:00", "off_time": "08:00"},
    ]
    with pytest.raises(SalusValidationError):
        encode_hot_water(entries)


def test_encode_hot_water_rejects_invalid_time():
    with pytest.raises(SalusValidationError):
        encode_hot_water([{"on_time": "99:99", "off_time": "08:00"}])


def test_encode_hot_water_six_entries_ok():
    entries = [{"on_time": f"{h:02d}:00", "off_time": f"{h:02d}:30"} for h in range(6)]
    raw = encode_hot_water(entries)
    assert decode_hot_water(raw) == entries


# --- describe_* ---------------------------------------------------------------


def test_describe_heating_empty():
    assert describe_heating([]) == "No schedule"


def test_describe_heating_formats_time_and_temperature():
    text = describe_heating([{"time": "06:00", "temperature": 20.0}])
    assert "06:00" in text
    assert "20" in text


def test_describe_hot_water_empty():
    assert describe_hot_water([]) == "No schedule"


def test_describe_hot_water_formats_slots():
    text = describe_hot_water([{"on_time": "06:00", "off_time": "08:00"}])
    assert "06:00-08:00" in text


# --- current_slot / next_change -----------------------------------------------

_DAY = [
    {"time": "06:00", "temperature": 20.0},
    {"time": "09:00", "temperature": 17.0},
    {"time": "22:00", "temperature": 16.0},
]


def test_current_slot_empty_schedule():
    assert current_slot([], "12:00") is None


def test_current_slot_before_first_event_wraps_to_previous_days_final_period():
    """Before 06:00, the last slot of the (conceptual) previous day still applies."""
    assert current_slot(_DAY, "02:00") == {"time": "22:00", "temperature": 16.0}


def test_current_slot_between_events():
    assert current_slot(_DAY, "07:30") == {"time": "06:00", "temperature": 20.0}


def test_current_slot_after_last_event():
    assert current_slot(_DAY, "23:30") == {"time": "22:00", "temperature": 16.0}


def test_current_slot_exact_boundary_match():
    assert current_slot(_DAY, "09:00") == {"time": "09:00", "temperature": 17.0}


def test_current_slot_at_midnight():
    assert current_slot(_DAY, "00:00") == {"time": "22:00", "temperature": 16.0}


def test_next_change_returns_next_strictly_after():
    assert next_change(_DAY, "07:00") == {"time": "09:00", "temperature": 17.0}


def test_next_change_none_after_last_event():
    assert next_change(_DAY, "23:00") is None


def test_next_change_empty_schedule():
    assert next_change([], "12:00") is None


def test_next_change_at_midnight_returns_first_slot():
    assert next_change(_DAY, "00:00") == {"time": "06:00", "temperature": 20.0}


def test_next_change_exact_boundary_is_not_next():
    """A slot starting exactly now is the current one, not the next one."""
    assert next_change(_DAY, "06:00") == {"time": "09:00", "temperature": 17.0}
