"""Tests for the salus-it500.com web-portal transport, mocked with aioresponses.

No Home Assistant dependency - only aiohttp + aioresponses.
"""

from __future__ import annotations

import asyncio
import re

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.salus_it500.api.exceptions import (
    SalusAuthError,
    SalusConnectionError,
    SalusDeviceError,
    SalusRateLimitError,
    SalusValidationError,
)
from custom_components.salus_it500.api.web import WebClient

LOGIN_URL = re.compile(r".*/public/login\.php.*")
CONTROL_URL = re.compile(r".*/public/control\.php.*")
VALUES_URL = re.compile(r".*/public/ajax_device_values\.php.*")
SET_URL = re.compile(r".*/includes/set\.php.*")

CONTROL_PAGE_WITH_TOKEN = (
    '<html><body><input type="hidden" id="token" value="tok-abc123" /></body></html>'
)
LOGIN_PAGE_NO_TOKEN = (
    "<html><body><form>email <input name='password'/> login</form></body></html>"
)


@pytest.fixture
def mock_web():
    with aioresponses() as m:
        yield m


@pytest.fixture
async def session():
    async with aiohttp.ClientSession() as s:
        yield s


def _client(session: aiohttp.ClientSession) -> WebClient:
    return WebClient(session, "user@example.com", "hunter2", "33591764")


# --- Authentication ------------------------------------------------------


async def test_login_success(mock_web, session):
    mock_web.post(LOGIN_URL, body="ok")
    mock_web.get(CONTROL_URL, body=CONTROL_PAGE_WITH_TOKEN)
    client = _client(session)
    await client.async_login()
    assert client._token == "tok-abc123"  # noqa: SLF001


async def test_login_no_token_but_login_markers_present_is_auth_error(
    mock_web, session
):
    mock_web.post(LOGIN_URL, body="ok")
    mock_web.get(CONTROL_URL, body=LOGIN_PAGE_NO_TOKEN)
    with pytest.raises(SalusAuthError):
        await _client(session).async_login()


async def test_login_no_token_generic_layout_change(mock_web, session):
    mock_web.post(LOGIN_URL, body="ok")
    mock_web.get(CONTROL_URL, body="<html><body>Something else entirely</body></html>")
    with pytest.raises(SalusAuthError):
        await _client(session).async_login()


async def test_login_rate_limited(mock_web, session):
    mock_web.post(LOGIN_URL, body="ok")
    mock_web.get(CONTROL_URL, status=429, body="slow down")
    with pytest.raises(SalusRateLimitError):
        await _client(session).async_login()


async def test_login_timeout_retried_then_raises(mock_web, session, monkeypatch):
    async def _fast_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)
    for _ in range(4):
        mock_web.post(LOGIN_URL, exception=asyncio.TimeoutError())
    with pytest.raises(SalusConnectionError):
        await _client(session).async_login()


async def test_login_never_logs_password(mock_web, session, caplog):
    mock_web.post(LOGIN_URL, body="ok")
    mock_web.get(CONTROL_URL, body=CONTROL_PAGE_WITH_TOKEN)
    with caplog.at_level("DEBUG"):
        await _client(session).async_login()
    assert "hunter2" not in caplog.text
    assert "tok-abc123" not in caplog.text


# --- Reads -------------------------------------------------------------------


async def test_get_state_maps_json_fields(mock_web, session):
    mock_web.post(LOGIN_URL, body="ok")
    mock_web.get(CONTROL_URL, body=CONTROL_PAGE_WITH_TOKEN)
    mock_web.get(
        VALUES_URL,
        payload={
            "CH1currentRoomTemp": "20.5",
            "CH1currentSetPoint": "21.0",
            "CH1heatOnOffStatus": "1",
            "CH1heatOnOff": "0",
            "CH1autoMode": "0",
            "frost": "5.0",
        },
    )
    state = await _client(session).async_get_state()
    assert state.ch1.current_temperature == 20.5
    assert state.ch1.target_temperature == 21.0
    assert state.ch1.relay_on is True
    assert state.hw.available is False


async def test_get_state_detects_hot_water(mock_web, session):
    mock_web.post(LOGIN_URL, body="ok")
    mock_web.get(CONTROL_URL, body=CONTROL_PAGE_WITH_TOKEN)
    mock_web.get(
        VALUES_URL,
        payload={
            "CH1currentRoomTemp": "20.0",
            "HWonOffStatus": "1",
        },
    )
    state = await _client(session).async_get_state()
    assert state.hw.available is True
    assert state.hw.on is True


async def test_values_expired_token_returns_html(mock_web, session):
    mock_web.post(LOGIN_URL, body="ok")
    mock_web.get(CONTROL_URL, body=CONTROL_PAGE_WITH_TOKEN)
    mock_web.get(VALUES_URL, body="<html>please log in again</html>")
    with pytest.raises(SalusAuthError):
        await _client(session).async_get_state()


async def test_values_rate_limited(mock_web, session):
    mock_web.post(LOGIN_URL, body="ok")
    mock_web.get(CONTROL_URL, body=CONTROL_PAGE_WITH_TOKEN)
    mock_web.get(VALUES_URL, status=429, body="slow down")
    with pytest.raises(SalusRateLimitError):
        await _client(session).async_get_state()


async def test_values_server_error(mock_web, session):
    mock_web.post(LOGIN_URL, body="ok")
    mock_web.get(CONTROL_URL, body=CONTROL_PAGE_WITH_TOKEN)
    mock_web.get(VALUES_URL, status=500, body="oops")
    with pytest.raises(SalusConnectionError):
        await _client(session).async_get_state()


# --- Writes ------------------------------------------------------------------


async def test_post_command_error_message(mock_web, session):
    mock_web.post(LOGIN_URL, body="ok")
    mock_web.get(CONTROL_URL, body=CONTROL_PAGE_WITH_TOKEN)
    mock_web.post(SET_URL, payload={"errorMsg": "rejected"})
    with pytest.raises(SalusDeviceError):
        await _client(session).async_set_frost_temperature(7.0)


async def test_set_target_temperature_rejects_second_zone(session):
    with pytest.raises(SalusValidationError):
        await _client(session).async_set_target_temperature("ch2", 20.0)


async def test_set_heating_mode_rejects_second_zone(session):
    from custom_components.salus_it500.api.model import HeatingMode

    with pytest.raises(SalusValidationError):
        await _client(session).async_set_heating_mode("ch2", HeatingMode.AUTO)
