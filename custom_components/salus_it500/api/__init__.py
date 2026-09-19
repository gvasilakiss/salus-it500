"""Transport layer for the Salus iT500 integration."""

from __future__ import annotations

import logging

from aiohttp import ClientSession

from .arrayent import ArrayentClient
from .client import SalusClient
from .exceptions import (
    SalusAuthError,
    SalusConnectionError,
    SalusDeviceError,
    SalusDeviceNotFound,
    SalusError,
    SalusProtocolError,
    SalusRateLimitError,
    SalusUnsupportedFeature,
    SalusValidationError,
)
from .model import (
    DeviceState,
    HeatingMode,
    HeatingZoneState,
    HotWaterMode,
    HotWaterState,
    ScheduleType,
    SystemType,
)
from .web import WebClient

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "ArrayentClient",
    "DeviceState",
    "HeatingMode",
    "HeatingZoneState",
    "HotWaterMode",
    "HotWaterState",
    "SalusAuthError",
    "SalusClient",
    "SalusConnectionError",
    "SalusDeviceError",
    "SalusDeviceNotFound",
    "SalusError",
    "SalusProtocolError",
    "SalusRateLimitError",
    "SalusUnsupportedFeature",
    "SalusValidationError",
    "ScheduleType",
    "SystemType",
    "WebClient",
    "async_create_client",
]


async def async_create_client(
    session: ClientSession,
    username: str,
    password: str,
    device_id: str,
    transport: str = "auto",
) -> SalusClient:
    """Build a logged-in client using the requested transport.

    ``auto`` prefers the mobile-app API and falls back to the web portal only
    if the API is unreachable. A rejected password is not a reason to fall
    back - both routes use the same credentials, so we surface it immediately.
    """
    if transport == "web":
        client: SalusClient = WebClient(session, username, password, device_id)
        await client.async_login()
        return client

    api_client = ArrayentClient(session, username, password, device_id)

    if transport == "api":
        await api_client.async_login()
        return api_client

    try:
        await api_client.async_login()
    except SalusAuthError:
        raise
    except SalusConnectionError as err:
        _LOGGER.warning(
            "Salus app API unavailable (%s); falling back to the web portal", err
        )
        web_client = WebClient(session, username, password, device_id)
        await web_client.async_login()
        return web_client

    return api_client
