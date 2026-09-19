"""Tests for the climate entity: hvac modes, temperature setting, availability."""

from __future__ import annotations

import pytest
from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.salus_it500.api.model import HeatingMode
from custom_components.salus_it500.climate import SalusClimate
from custom_components.salus_it500.const import CONF_DEVICE_ID, DOMAIN, ZONE_CH1
from custom_components.salus_it500.coordinator import SalusDataUpdateCoordinator

from .fakes import FakeSalusClient, make_state


async def _make_coordinator(
    hass, client: FakeSalusClient
) -> SalusDataUpdateCoordinator:
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_DEVICE_ID: client.state.device_id}
    )
    entry.add_to_hass(hass)
    coordinator = SalusDataUpdateCoordinator(hass, entry, client, scan_interval=120)
    entry.runtime_data = coordinator
    await coordinator.async_refresh()
    return coordinator


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (HeatingMode.AUTO, HVACMode.AUTO),
        (HeatingMode.TEMP_HOLD, HVACMode.AUTO),
        (HeatingMode.MANUAL, HVACMode.HEAT),
        (HeatingMode.OFF, HVACMode.OFF),
    ],
)
async def test_hvac_mode_reflects_heating_mode(hass, mode, expected):
    client = FakeSalusClient(state=make_state(ch1_mode=mode))
    coordinator = await _make_coordinator(hass, client)
    entity = SalusClimate(coordinator, ZONE_CH1)

    assert entity.hvac_mode is expected


async def test_current_and_target_temperature(hass):
    client = FakeSalusClient(state=make_state(ch1_temperature=18.9, ch1_target=20.0))
    coordinator = await _make_coordinator(hass, client)
    entity = SalusClimate(coordinator, ZONE_CH1)

    assert entity.current_temperature == 18.9
    assert entity.target_temperature == 20.0


@pytest.mark.parametrize(
    ("mode", "relay_on", "expected"),
    [
        (HeatingMode.OFF, True, HVACAction.OFF),
        (HeatingMode.AUTO, True, HVACAction.HEATING),
        (HeatingMode.AUTO, False, HVACAction.IDLE),
    ],
)
async def test_hvac_action(hass, mode, relay_on, expected):
    state = make_state(ch1_mode=mode)
    state.ch1.relay_on = relay_on
    coordinator = await _make_coordinator(hass, FakeSalusClient(state=state))
    entity = SalusClimate(coordinator, ZONE_CH1)

    assert entity.hvac_action is expected


async def test_set_temperature_out_of_range_raises(hass):
    coordinator = await _make_coordinator(hass, FakeSalusClient(state=make_state()))
    entity = SalusClimate(coordinator, ZONE_CH1)

    with pytest.raises(ServiceValidationError):
        await entity.async_set_temperature(temperature=99.0)


async def test_set_temperature_writes_through_and_updates_state(hass):
    client = FakeSalusClient(state=make_state(ch1_target=18.0))
    coordinator = await _make_coordinator(hass, client)
    entity = SalusClimate(coordinator, ZONE_CH1)

    await entity.async_set_temperature(temperature=21.0)

    assert ("set_target_temperature", ("ch1", 21.0)) in client.calls
    assert coordinator.data.ch1.target_temperature == 21.0


async def test_set_hvac_mode_off(hass):
    client = FakeSalusClient(state=make_state(ch1_mode=HeatingMode.AUTO))
    coordinator = await _make_coordinator(hass, client)
    entity = SalusClimate(coordinator, ZONE_CH1)

    await entity.async_set_hvac_mode(HVACMode.OFF)

    assert coordinator.data.ch1.mode is HeatingMode.OFF
    assert ("set_heating_mode", ("ch1", HeatingMode.OFF)) in client.calls


async def test_climate_unavailable_when_device_offline(hass):
    coordinator = await _make_coordinator(
        hass, FakeSalusClient(state=make_state(online=False))
    )
    entity = SalusClimate(coordinator, ZONE_CH1)

    assert entity.available is False


async def test_climate_available_when_device_online(hass):
    coordinator = await _make_coordinator(
        hass, FakeSalusClient(state=make_state(online=True))
    )
    entity = SalusClimate(coordinator, ZONE_CH1)

    assert entity.available is True


async def test_preset_mode_boost_starts_boost(hass):
    client = FakeSalusClient(state=make_state())
    coordinator = await _make_coordinator(hass, client)
    entity = SalusClimate(coordinator, ZONE_CH1)

    from custom_components.salus_it500.const import PRESET_BOOST

    await entity.async_set_preset_mode(PRESET_BOOST)

    assert ("set_boost", ("ch1", 1)) in client.calls


async def test_manual_temperature_change_clears_pending_override(hass):
    client = FakeSalusClient(state=make_state(ch1_target=18.0))
    coordinator = await _make_coordinator(hass, client)
    await coordinator.async_skip_zone("ch1")
    assert coordinator.pending_override_until("ch1") is not None

    entity = SalusClimate(coordinator, ZONE_CH1)
    await entity.async_set_temperature(temperature=19.5)

    assert coordinator.pending_override_until("ch1") is None
