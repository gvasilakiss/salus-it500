"""Tests for the attribute model (custom_components.salus_it500.api.model).

Covers the S06/S09/S10/S11/S12/S13/S15/S17 system attributes, the A84/A85/A87/
A88/A89/A91/A92 heating attributes, the C42/C43/C45 hot-water attributes, the
heating-mode flag table, and the Celsius/Fahrenheit conversion boundary.

No Home Assistant dependency.
"""

from __future__ import annotations

import pytest

from custom_components.salus_it500.api.model import (
    HeatingMode,
    HotWaterMode,
    SystemType,
    is_fahrenheit,
    parse_attributes,
)


def test_parse_attributes_ch1_basic():
    attrs = {
        "desc": "Living room",
        "S06": "0",
        "S09": "500",
        "S13": "1.2.3",
        "A84": "2000",
        "A85": "2150",
        "A87": "1",
        "A88": "0",
        "A89": "0",
        "A92": "0",
        "A91": "0",
    }
    state = parse_attributes("33591764", attrs)

    assert state.name == "Living room"
    assert state.system_type is SystemType.CH1
    assert state.firmware == "1.2.3"
    assert state.frost_temperature == 5.0
    assert state.ch1.current_temperature == 20.0
    assert state.ch1.target_temperature == 21.5
    assert state.ch1.relay_on is True
    assert state.ch1.mode is HeatingMode.AUTO
    assert state.ch1.available is True
    assert state.has_ch2 is False
    assert state.has_hot_water is False


def test_parse_attributes_missing_description_falls_back_to_device_id():
    state = parse_attributes("33591764", {})
    assert state.name == "Salus iT500 33591764"


@pytest.mark.parametrize(
    ("off_flag", "manual_flag", "hold_flag", "expected"),
    [
        ("0", "0", "0", HeatingMode.AUTO),
        ("0", "0", "1", HeatingMode.TEMP_HOLD),
        ("0", "1", "0", HeatingMode.MANUAL),
        ("1", "0", "0", HeatingMode.OFF),
        ("1", "1", "1", HeatingMode.UNKNOWN),
    ],
)
def test_heating_mode_flag_table(off_flag, manual_flag, hold_flag, expected):
    """A89/A92/A88 combine into one mode exactly as the README documents."""
    attrs = {"A89": off_flag, "A92": manual_flag, "A88": hold_flag, "A84": "2000"}
    state = parse_attributes("dev", attrs)
    assert state.ch1.mode is expected


def test_system_type_ch1_ch2():
    state = parse_attributes("dev", {"S06": "1"})
    assert state.system_type is SystemType.CH1_CH2
    assert state.has_ch2 is True
    assert state.has_hot_water is False


def test_system_type_ch1_hot_water():
    state = parse_attributes("dev", {"S06": "2"})
    assert state.system_type is SystemType.CH1_HW
    assert state.has_hot_water is True
    assert state.has_ch2 is False


def test_hot_water_attributes():
    attrs = {"C42": "1", "C43": "2", "C45": "1", "C46": "0"}
    state = parse_attributes("dev", attrs)
    assert state.hw.mode is HotWaterMode.ONCE
    assert state.hw.boost_hours == 2
    assert state.hw.boost_active is True
    assert state.hw.on is True
    assert state.hw.available is True


def test_hot_water_unavailable_when_attributes_absent():
    state = parse_attributes("dev", {"A84": "2000"})
    assert state.hw.available is False
    assert state.hw.on is False
    assert state.hw.mode is HotWaterMode.OFF
    assert state.hw.boost_active is False


@pytest.mark.parametrize(("raw", "expected"), [("1", True), ("0", False), (None, None)])
def test_battery_status(raw, expected):
    attrs = {} if raw is None else {"S03": raw}
    state = parse_attributes("dev", attrs)
    assert state.battery_low is expected


def test_holiday_fields():
    attrs = {"S10": "1", "S11": "2024-01-01", "S12": "2024-01-10"}
    state = parse_attributes("dev", attrs)
    assert state.holiday_active is True
    assert state.holiday_start == "2024-01-01"
    assert state.holiday_end == "2024-01-10"


def test_holiday_inactive_by_default():
    state = parse_attributes("dev", {})
    assert state.holiday_active is False
    assert state.holiday_start is None


def test_span_and_offset_are_deltas_not_absolute_temperatures():
    attrs = {"S15": "50", "S17": "-150"}
    state = parse_attributes("dev", attrs)
    assert state.span == 0.5
    assert state.temperature_offset == -1.5


def test_ch2_and_hw_zone_lookup():
    attrs = {"A84": "2000", "B84": "1800", "C45": "1"}
    state = parse_attributes("dev", attrs)
    assert state.zone("ch1") is state.ch1
    assert state.zone("ch2") is state.ch2
    assert state.zone("hw") is state.hw
    assert state.ch2.current_temperature == 18.0


def test_zone_lookup_rejects_unknown_zone():
    state = parse_attributes("dev", {})
    with pytest.raises(KeyError):
        state.zone("not-a-zone")


def test_blank_temperature_value_is_none_and_unavailable():
    state = parse_attributes("dev", {"A84": ""})
    assert state.ch1.current_temperature is None
    assert state.ch1.available is False


# --- Fahrenheit boundary conversion -------------------------------------------


def test_is_fahrenheit_detection():
    assert is_fahrenheit({"S07": "1"}) is True
    assert is_fahrenheit({"S07": "0"}) is False
    assert is_fahrenheit({}) is False


def test_fahrenheit_absolute_temperature_conversion():
    # 6800 -> 68.00F -> 20.0C
    attrs = {"S07": "1", "A84": "6800", "S09": "4100"}  # frost 41F -> 5C
    state = parse_attributes("dev", attrs)
    assert state.ch1.current_temperature == pytest.approx(20.0)
    assert state.frost_temperature == pytest.approx(5.0)
    assert state.source_unit_fahrenheit is True


def test_fahrenheit_delta_conversion_has_no_32_degree_offset():
    # A 9-Fahrenheit-degree delta is a 5-Celsius-degree delta, not (9-32)/1.8.
    attrs = {"S07": "1", "S17": "900", "S15": "900"}
    state = parse_attributes("dev", attrs)
    assert state.temperature_offset == pytest.approx(5.0)
    assert state.span == pytest.approx(5.0)


def test_celsius_is_the_default_when_s07_is_absent():
    state = parse_attributes("dev", {"A84": "2000"})
    assert state.source_unit_fahrenheit is False
    assert state.ch1.current_temperature == 20.0
