"""Tests for the Arrayent (app API) transport, mocked with aioresponses.

No Home Assistant dependency - only aiohttp + aioresponses.
"""

from __future__ import annotations

import asyncio
import re

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.salus_it500.api.arrayent import ArrayentClient
from custom_components.salus_it500.api.exceptions import (
    SalusAuthError,
    SalusConnectionError,
    SalusDeviceError,
    SalusProtocolError,
    SalusRateLimitError,
)

AUTH_URL = re.compile(r".*/acc/applications/SalusService/sessions.*")
DEVICE_LIST_URL = re.compile(r".*/getDeviceList.*")
ATTRS_URL = re.compile(r".*/getDeviceAttributesWithValues.*")
SET_URL = re.compile(r".*/setMultiDeviceAttributes2.*")

ATTRS_XML = """<?xml version="1.0"?>
<response>
  <desc>Living room</desc>
  <attrList><name>S06</name><value>0</value></attrList>
  <attrList><name>A84</name><value>2000</value></attrList>
</response>
"""


@pytest.fixture
def mock_api():
    with aioresponses() as m:
        yield m


@pytest.fixture
async def session():
    async with aiohttp.ClientSession() as s:
        yield s


def _client(session: aiohttp.ClientSession) -> ArrayentClient:
    return ArrayentClient(session, "user@example.com", "hunter2", "33591764")


# --- Authentication ------------------------------------------------------


async def test_login_success(mock_api, session):
    mock_api.post(AUTH_URL, payload={"securityToken": "tok-123", "userId": 42})
    client = _client(session)
    await client.async_login()
    assert client._token == "tok-123"  # noqa: SLF001 - internal state check
    assert client._user_id == 42  # noqa: SLF001


@pytest.mark.parametrize("status", [401, 403])
async def test_login_rejected_credentials(mock_api, session, status):
    mock_api.post(AUTH_URL, status=status, body="Unauthorized")
    with pytest.raises(SalusAuthError):
        await _client(session).async_login()


async def test_login_rate_limited(mock_api, session):
    mock_api.post(AUTH_URL, status=429, body="Too Many Requests")
    with pytest.raises(SalusRateLimitError):
        await _client(session).async_login()


async def test_login_server_error(mock_api, session):
    mock_api.post(AUTH_URL, status=500, body="Internal Server Error")
    with pytest.raises(SalusConnectionError):
        await _client(session).async_login()


async def test_login_malformed_json(mock_api, session):
    mock_api.post(AUTH_URL, status=200, body="<html>not json</html>")
    with pytest.raises(SalusAuthError):
        await _client(session).async_login()


async def test_login_timeout_is_retried_then_raises(mock_api, session, monkeypatch):
    async def _fast_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)
    mock_api.post(AUTH_URL, exception=asyncio.TimeoutError())
    mock_api.post(AUTH_URL, exception=asyncio.TimeoutError())
    mock_api.post(AUTH_URL, exception=asyncio.TimeoutError())
    mock_api.post(AUTH_URL, exception=asyncio.TimeoutError())
    with pytest.raises(SalusConnectionError):
        await _client(session).async_login()


async def test_login_never_logs_password(mock_api, session, caplog):
    """The password (or its MD5 digest) must never appear in log output."""
    mock_api.post(AUTH_URL, payload={"securityToken": "tok-123", "userId": 1})
    with caplog.at_level("DEBUG"):
        await _client(session).async_login()
    assert "hunter2" not in caplog.text
    assert "tok-123" not in caplog.text


# --- Reads -----------------------------------------------------------------


async def test_get_state_success(mock_api, session):
    mock_api.post(AUTH_URL, payload={"securityToken": "tok", "userId": 1})
    mock_api.post(ATTRS_URL, body=ATTRS_XML, content_type="text/xml")
    state = await _client(session).async_get_state()
    assert state.name == "Living room"
    assert state.ch1.current_temperature == 20.0


async def test_get_state_malformed_xml_raises_protocol_error(mock_api, session):
    mock_api.post(AUTH_URL, payload={"securityToken": "tok", "userId": 1})
    mock_api.post(ATTRS_URL, body="<broken", content_type="text/xml")
    with pytest.raises(SalusProtocolError):
        await _client(session).async_get_state()


async def test_get_device_list_malformed_xml_raises_protocol_error(mock_api, session):
    mock_api.post(AUTH_URL, payload={"securityToken": "tok", "userId": 1})
    mock_api.post(DEVICE_LIST_URL, body="not xml at all", content_type="text/xml")
    with pytest.raises(SalusProtocolError):
        await _client(session).async_list_devices()


async def test_expired_token_triggers_single_reauth(mock_api, session):
    """A 401 on a normal call forces one re-login, then the call is retried."""
    mock_api.post(AUTH_URL, payload={"securityToken": "old-token", "userId": 1})
    mock_api.post(ATTRS_URL, status=401, body="expired")
    mock_api.post(AUTH_URL, payload={"securityToken": "new-token", "userId": 1})
    mock_api.post(ATTRS_URL, body=ATTRS_XML, content_type="text/xml")

    client = _client(session)
    state = await client.async_get_state()
    assert state.ch1.current_temperature == 20.0
    assert client._token == "new-token"  # noqa: SLF001


async def test_double_auth_rejection_raises(mock_api, session):
    mock_api.post(AUTH_URL, payload={"securityToken": "tok", "userId": 1})
    mock_api.post(ATTRS_URL, status=401, body="expired")
    mock_api.post(AUTH_URL, payload={"securityToken": "tok2", "userId": 1})
    mock_api.post(ATTRS_URL, status=401, body="still rejected")

    with pytest.raises(SalusAuthError):
        await _client(session).async_get_state()


async def test_rate_limited_read(mock_api, session):
    mock_api.post(AUTH_URL, payload={"securityToken": "tok", "userId": 1})
    mock_api.post(ATTRS_URL, status=429, body="slow down")
    with pytest.raises(SalusRateLimitError):
        await _client(session).async_get_state()


# --- Writes ------------------------------------------------------------------


async def test_set_tolerates_http_500(mock_api, session):
    """setMultiDeviceAttributes2 often 500s even on success; must not raise."""
    mock_api.post(AUTH_URL, payload={"securityToken": "tok", "userId": 1})
    mock_api.get(SET_URL, status=500, body="")
    await _client(session).async_set_frost_temperature(7.0)  # should not raise


async def test_set_raises_on_error_message(mock_api, session):
    mock_api.post(AUTH_URL, payload={"securityToken": "tok", "userId": 1})
    mock_api.get(
        SET_URL,
        status=200,
        body="<response><errorMsg>Invalid attribute</errorMsg></response>",
        content_type="text/xml",
    )
    with pytest.raises(SalusDeviceError):
        await _client(session).async_set_frost_temperature(7.0)


async def test_set_raises_on_nonzero_retcode(mock_api, session):
    mock_api.post(AUTH_URL, payload={"securityToken": "tok", "userId": 1})
    mock_api.get(SET_URL, status=200, body="<response><retCode>5</retCode></response>")
    with pytest.raises(SalusDeviceError):
        await _client(session).async_set_frost_temperature(7.0)


async def test_set_accepts_non_xml_response(mock_api, session):
    mock_api.post(AUTH_URL, payload={"securityToken": "tok", "userId": 1})
    mock_api.get(SET_URL, status=200, body="OK")
    await _client(session).async_set_frost_temperature(7.0)  # should not raise
