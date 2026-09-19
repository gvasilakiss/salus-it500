"""Tests for SalusDataUpdateCoordinator: write serialisation, diagnostics
bookkeeping, and the advance/skip/cancel-override supervision mechanism.
"""

from __future__ import annotations

import asyncio

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.salus_it500.api.model import HeatingMode
from custom_components.salus_it500.const import CONF_DEVICE_ID, DOMAIN
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


async def test_first_refresh_populates_diagnostics(hass):
    client = FakeSalusClient(state=make_state())
    coordinator = await _make_coordinator(hass, client)

    assert coordinator.data.ch1.current_temperature == 19.5
    assert coordinator.last_update_success is True
    assert coordinator.last_success_at is not None
    assert coordinator.last_update_duration is not None
    assert coordinator.last_error is None


async def test_async_command_applies_optimistic_update(hass):
    client = FakeSalusClient(state=make_state())
    coordinator = await _make_coordinator(hass, client)

    await coordinator.async_command(
        lambda: coordinator.client.async_set_target_temperature("ch1", 22.0),
        optimistic=lambda state: setattr(state.ch1, "target_temperature", 22.0),
    )

    assert coordinator.data.ch1.target_temperature == 22.0
    assert ("set_target_temperature", ("ch1", 22.0)) in client.calls


async def test_commands_are_serialised(hass):
    """Two concurrent commands must never run their actions interleaved."""
    client = FakeSalusClient(state=make_state())
    coordinator = await _make_coordinator(hass, client)

    order: list[str] = []

    async def slow_action(tag: str) -> None:
        order.append(f"{tag}-start")
        await asyncio.sleep(0.05)
        order.append(f"{tag}-end")

    await asyncio.gather(
        coordinator.async_command(lambda: slow_action("a")),
        coordinator.async_command(lambda: slow_action("b")),
    )

    # If the lock worked, one action fully completes before the other starts.
    assert order in (
        ["a-start", "a-end", "b-start", "b-end"],
        ["b-start", "b-end", "a-start", "a-end"],
    )


async def test_advance_zone_holds_next_scheduled_temperature(hass, monkeypatch):
    from homeassistant.util import dt as dt_util

    # Fixed at 04:00 *local*, whatever today happens to be, so the 05:40
    # slot below is always still ahead - avoids timezone-dependent flakiness.
    fixed_now = dt_util.now().replace(hour=4, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(dt_util, "now", lambda *a, **k: fixed_now)

    state = make_state()
    for day in state.ch1.programs:
        state.ch1.programs[day] = "5XE0"  # 05:40 at 21.0C, from the README example
    client = FakeSalusClient(state=state)
    coordinator = await _make_coordinator(hass, client)

    await coordinator.async_advance_zone("ch1")

    assert coordinator.data.ch1.target_temperature == 21.0
    assert coordinator.data.ch1.mode is HeatingMode.TEMP_HOLD


async def test_advance_zone_raises_without_a_schedule(hass):
    from homeassistant.exceptions import HomeAssistantError

    client = FakeSalusClient(state=make_state())
    coordinator = await _make_coordinator(hass, client)

    with pytest.raises(HomeAssistantError):
        await coordinator.async_advance_zone("ch1")


async def test_skip_zone_registers_a_pending_override(hass):
    client = FakeSalusClient(state=make_state(ch1_target=18.0))
    coordinator = await _make_coordinator(hass, client)

    await coordinator.async_skip_zone("ch1")

    assert coordinator.pending_override_until("ch1") is not None
    assert coordinator.data.ch1.target_temperature == 18.0


async def test_clear_pending_override_forgets_it(hass):
    client = FakeSalusClient(state=make_state(ch1_target=18.0))
    coordinator = await _make_coordinator(hass, client)
    await coordinator.async_skip_zone("ch1")

    coordinator.clear_pending_override("ch1")

    assert coordinator.pending_override_until("ch1") is None


async def test_cancel_override_zone_returns_to_auto(hass):
    client = FakeSalusClient(state=make_state(ch1_mode=HeatingMode.TEMP_HOLD))
    coordinator = await _make_coordinator(hass, client)

    await coordinator.async_cancel_override_zone("ch1")

    assert coordinator.data.ch1.mode is HeatingMode.AUTO
    assert coordinator.pending_override_until("ch1") is None


async def test_failed_update_records_last_error(hass):
    from custom_components.salus_it500.api.exceptions import SalusConnectionError

    client = FakeSalusClient(state=make_state())
    coordinator = await _make_coordinator(hass, client)

    async def _fail():
        raise SalusConnectionError("network is down")

    client.async_get_state = _fail  # type: ignore[method-assign]
    await coordinator.async_refresh()

    assert coordinator.last_update_success is False
    assert coordinator.last_error == "network is down"
