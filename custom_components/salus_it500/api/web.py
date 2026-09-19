"""Fallback client that drives the salus-it500.com web portal.

This is the older, more fragile route: log in with a form post, scrape a CSRF
token out of the control page, then poll a small JSON endpoint. It exposes far
less than the Arrayent API - no schedules, no second zone, no boost, no
calibration - but it survives if the app backend changes or refuses the
account.

Salus has historically blocked IP addresses that poll this endpoint hard. Keep
the scan interval generous.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

from aiohttp import ClientSession, ClientTimeout

from .client import SalusClient
from .exceptions import (
    SalusAuthError,
    SalusConnectionError,
    SalusDeviceError,
    SalusRateLimitError,
    SalusValidationError,
)
from .model import (
    DeviceState,
    HeatingMode,
    HeatingZoneState,
    HotWaterMode,
    HotWaterState,
    SystemType,
)
from .util import async_request_with_retry

_LOGGER = logging.getLogger(__name__)

URL_LOGIN = "https://salus-it500.com/public/login.php"
URL_CONTROL = "https://salus-it500.com/public/control.php"
URL_VALUES = "https://salus-it500.com/public/ajax_device_values.php"
URL_SET = "https://salus-it500.com/includes/set.php"

TOKEN_TTL = 9 * 60
REQUEST_TIMEOUT = ClientTimeout(total=30)

TOKEN_RE = re.compile(r'<input[^>]*id="token"[^>]*value="([^"]+)"', re.IGNORECASE)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _as_float(payload: dict[str, Any], key: str) -> float | None:
    raw = payload.get(key)
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _as_flag(payload: dict[str, Any], key: str) -> bool:
    return str(payload.get(key, "0")).strip() in ("1", "true", "True")


class WebClient(SalusClient):
    """Scrapes the Salus consumer web portal."""

    transport = "web"
    supports_schedules = False
    supports_second_zone = False
    supports_boost = False
    supports_calibration = False
    supports_holiday = False

    def __init__(
        self,
        session: ClientSession,
        username: str,
        password: str,
        device_id: str,
    ) -> None:
        """Store credentials; no network traffic happens here."""
        self._session = session
        self._username = username
        self._password = password
        self._device_id = str(device_id)
        self._token: str | None = None
        self._token_at: float = 0.0
        self._lock = asyncio.Lock()

    # --- Session handling --------------------------------------------------

    async def async_login(self) -> None:
        """Post the login form and scrape the per-device token."""
        headers = {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        payload = {
            "IDemail": self._username,
            "password": self._password,
            "login": "Login",
            "keep_logged_in": "1",
        }

        await async_request_with_retry(
            lambda: self._session.post(
                URL_LOGIN, data=payload, headers=headers, timeout=REQUEST_TIMEOUT
            ),
            what="salus-it500.com",
        )
        response = await async_request_with_retry(
            lambda: self._session.get(
                URL_CONTROL,
                params={"devId": self._device_id},
                headers={"User-Agent": USER_AGENT},
                timeout=REQUEST_TIMEOUT,
            ),
            what="salus-it500.com",
        )
        body = await response.text()

        if response.status == 429:
            raise SalusRateLimitError("salus-it500.com is throttling this address")

        match = TOKEN_RE.search(body)
        if not match:
            if "login" in body.lower() and "password" in body.lower():
                raise SalusAuthError(
                    "salus-it500.com sent the login page back - check the "
                    "email address, password and device ID"
                )
            raise SalusAuthError("Could not find a session token on the control page")

        self._token = match.group(1)
        self._token_at = time.monotonic()
        _LOGGER.debug("Scraped a fresh salus-it500.com token")

    async def _async_token(self) -> str:
        async with self._lock:
            if self._token is None or time.monotonic() - self._token_at > TOKEN_TTL:
                await self.async_login()
            assert self._token is not None
            return self._token

    async def async_close(self) -> None:
        """Forget the cached token."""
        self._token = None

    # --- Reads -------------------------------------------------------------

    async def async_list_devices(self) -> list[dict[str, Any]]:
        """The portal gives no machine-readable device list."""
        return [
            {
                "device_id": self._device_id,
                "name": f"Salus iT500 {self._device_id}",
                "type_id": None,
                "raw": {},
            }
        ]

    async def _async_values(self) -> dict[str, Any]:
        """Fetch the raw JSON blob the control page polls."""
        token = await self._async_token()
        params = {
            "devId": self._device_id,
            "token": token,
            "_": str(int(time.time() * 1000)),
        }

        response = await async_request_with_retry(
            lambda: self._session.get(
                URL_VALUES,
                params=params,
                headers={
                    "User-Agent": USER_AGENT,
                    "X-Requested-With": "XMLHttpRequest",
                },
                timeout=REQUEST_TIMEOUT,
            ),
            what="salus-it500.com",
        )
        body = await response.text()

        if response.status == 429:
            raise SalusRateLimitError("salus-it500.com is throttling this address")
        if response.status >= 400:
            raise SalusConnectionError(f"Values endpoint returned {response.status}")

        try:
            data = json.loads(body)
        except ValueError:
            # An expired token yields the login page instead of JSON.
            self._token = None
            raise SalusAuthError(
                "Session expired; salus-it500.com returned HTML"
            ) from None

        if not isinstance(data, dict):
            raise SalusConnectionError("Unexpected payload from salus-it500.com")
        return data

    async def async_get_state(self) -> DeviceState:
        """Map the portal JSON onto the shared state model."""
        data = await self._async_values()

        zone = HeatingZoneState(prefix="A", available=True)
        zone.current_temperature = _as_float(data, "CH1currentRoomTemp")
        zone.target_temperature = _as_float(data, "CH1currentSetPoint")
        zone.relay_on = _as_flag(data, "CH1heatOnOffStatus")

        if _as_flag(data, "CH1heatOnOff"):
            zone.mode = HeatingMode.OFF
        elif str(data.get("CH1autoMode", "0")).strip() == "1":
            zone.mode = HeatingMode.TEMP_HOLD
        else:
            zone.mode = HeatingMode.AUTO

        hot_water = HotWaterState()
        if "HWonOffStatus" in data:
            hot_water.available = True
            hot_water.on = _as_flag(data, "HWonOffStatus")
            hot_water.mode = HotWaterMode.ON if hot_water.on else HotWaterMode.OFF

        state = DeviceState(
            device_id=self._device_id,
            name=f"Salus iT500 {self._device_id}",
            system_type=SystemType.CH1_HW if hot_water.available else SystemType.CH1,
            frost_temperature=_as_float(data, "frost"),
            ch1=zone,
            hw=hot_water,
            raw={k: str(v) for k, v in data.items()},
        )
        state.online = zone.current_temperature not in (None, 0.0)
        return state

    # --- Writes ------------------------------------------------------------

    async def _async_post(self, options: dict[str, Any]) -> Any:
        """Post a command to set.php and return the decoded response."""
        token = await self._async_token()
        payload = {
            **{k: str(v) for k, v in options.items()},
            "token": token,
            "devId": self._device_id,
        }
        headers = {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Requested-With": "XMLHttpRequest",
        }

        response = await async_request_with_retry(
            lambda: self._session.post(
                URL_SET, data=payload, headers=headers, timeout=REQUEST_TIMEOUT
            ),
            what="salus-it500.com",
        )
        body = (await response.text()).strip()

        if response.status >= 400:
            raise SalusDeviceError(f"set.php returned {response.status}")

        try:
            decoded = json.loads(body)
        except ValueError:
            return body

        if isinstance(decoded, dict) and decoded.get("errorMsg"):
            raise SalusDeviceError(str(decoded["errorMsg"]))
        return decoded

    async def async_set_target_temperature(self, zone: str, temperature: float) -> None:
        """Set the CH1 setpoint. The portal cannot address a second zone."""
        if zone != "ch1":
            raise SalusValidationError("The web portal only exposes zone 1")
        await self._async_post(
            {
                "tempUnit": "0",
                "current_tempZ1_set": "1",
                "current_tempZ1": f"{temperature:.1f}",
            }
        )

    async def async_set_heating_mode(self, zone: str, mode: HeatingMode) -> None:
        """Toggle between schedule and off. Hold maps onto schedule."""
        if zone != "ch1":
            raise SalusValidationError("The web portal only exposes zone 1")
        off = "1" if mode is HeatingMode.OFF else "0"
        await self._async_post({"auto": off, "auto_setZ1": "1"})

    async def async_set_hot_water_mode(self, mode: HotWaterMode) -> None:
        """Switch hot water between permanently on, off and schedule."""
        if mode is HotWaterMode.ON:
            options = {"hwmode_cont": "1"}
        elif mode is HotWaterMode.AUTO:
            options = {"hwmode_auto": "1"}
        else:
            options = {"hwmode_off": "1"}
        await self._async_post(options)

    async def async_set_frost_temperature(self, temperature: float) -> None:
        """Set the frost-protection setpoint."""
        await self._async_post(
            {
                "tempUnit": "0",
                "frost_temp_set": "1",
                "frost_temp": f"{temperature:.1f}",
            }
        )
