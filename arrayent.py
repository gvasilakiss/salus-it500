"""Client for the Arrayent cloud API behind the Salus iT500 mobile app.

This is the same backend the official iT500 app talks to. It is considerably
more reliable than scraping salus-it500.com, exposes every device attribute
rather than the handful the web UI renders, and survives changes to the web
front end.

None of it is documented or endorsed by Salus. Endpoints may change without
notice.

Shape of the protocol
---------------------
1. ``POST /acc/applications/SalusService/sessions`` with the username and an
   MD5 hex digest of the password. Returns JSON with ``userId`` and
   ``securityToken``.
2. Every other call goes to ``/zdk/services/zamapi/<method>`` carrying
   ``secToken`` and ``userId``, and answers in XML.

Quirk worth knowing: ``setMultiDeviceAttributes2`` frequently answers with a
500 even when the command was applied. We treat 500 as "probably fine" and
confirm by re-reading state, which is what the app itself appears to do.
"""

from __future__ import annotations

import asyncio
import logging
import time
import xml.etree.ElementTree as ET
from hashlib import md5
from typing import Any

from aiohttp import ClientError, ClientResponse, ClientSession, ClientTimeout

from .client import SalusClient
from .exceptions import (
    SalusAuthError,
    SalusCommandError,
    SalusConnectionError,
    SalusDeviceNotFound,
    SalusRateLimitError,
)
from .model import (
    DAY_ATTR,
    HEATING_MODE_FLAGS,
    DeviceState,
    HeatingMode,
    HotWaterMode,
    Prefix,
    SystemAttr,
    ZoneAttr,
    parse_attributes,
)

_LOGGER = logging.getLogger(__name__)

HOST = "https://sal-emea-p01-api.arrayent.com"
AUTH_PATH = "acc/applications/SalusService/sessions"
API_PATH = "zdk/services/zamapi"

# Application identifier the iT500 app presents. Not a secret; it identifies
# the Salus tenant on Arrayent's multi-tenant platform.
APP_AUTHORIZATION = "687886-679716122"

TOKEN_TTL = 55 * 60
REQUEST_TIMEOUT = ClientTimeout(total=30)

#: setMultiDeviceAttributes2 accepts at most three pairs per call.
MAX_PAIRS_PER_CALL = 3

ZONE_PREFIX = {"ch1": Prefix.CH1, "ch2": Prefix.CH2, "hw": Prefix.HW}


def _strip_ns(tag: str) -> str:
    """Drop any XML namespace from a tag name."""
    return tag.rpartition("}")[2]


def _attributes_from_xml(text: str) -> dict[str, str]:
    """Flatten a getDeviceAttributesWithValues response into a dict."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as err:
        raise SalusCommandError(f"Malformed XML from Salus: {err}") from err

    attrs: dict[str, str] = {}
    for element in root.iter():
        if _strip_ns(element.tag) != "attrList":
            continue
        name = element.findtext("name")
        if name:
            attrs[name] = (element.findtext("value") or "").strip()

    # 'desc' lives on the response body rather than in attrList.
    for child in root:
        tag = _strip_ns(child.tag)
        if tag in {"desc", "devName"} and child.text:
            attrs.setdefault(SystemAttr.DESCRIPTION.value, child.text.strip())
    return attrs


class ArrayentClient(SalusClient):
    """Talks to the Salus cloud the way the phone app does."""

    transport = "api"
    supports_schedules = True
    supports_second_zone = True
    supports_boost = True
    supports_calibration = True
    supports_holiday = True

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
        self._password_hash = md5(password.encode("utf-8")).hexdigest()
        self._device_id = str(device_id)
        self._token: str | None = None
        self._user_id: int | None = None
        self._token_at: float = 0.0
        self._lock = asyncio.Lock()

    # --- Session handling --------------------------------------------------

    async def async_login(self) -> None:
        """Exchange credentials for a security token."""
        url = f"{HOST}/{AUTH_PATH}"
        payload = {"username": self._username, "password": self._password_hash}
        headers = {
            "Authorization": APP_AUTHORIZATION,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        try:
            response = await self._session.post(
                url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT
            )
            body = await response.text()
        except (ClientError, asyncio.TimeoutError) as err:
            raise SalusConnectionError(f"Could not reach the Salus cloud: {err}") from err

        if response.status in (401, 403):
            raise SalusAuthError("Salus rejected the email address or password")
        if response.status == 429:
            raise SalusRateLimitError("Salus is rate limiting this address")
        if response.status >= 500:
            raise SalusConnectionError(f"Salus login returned {response.status}")

        try:
            data = await response.json(content_type=None)
        except ValueError as err:
            raise SalusAuthError(f"Unexpected login response: {body[:200]}") from err

        if not isinstance(data, dict) or "securityToken" not in data:
            raise SalusAuthError(f"Login did not return a token: {str(data)[:200]}")

        self._token = data["securityToken"]
        self._user_id = data.get("userId")
        self._token_at = time.monotonic()
        _LOGGER.debug("Obtained Salus security token for user %s", self._user_id)

    async def _async_token(self) -> str:
        """Return a valid token, logging in again if the cached one is stale."""
        async with self._lock:
            if self._token is None or time.monotonic() - self._token_at > TOKEN_TTL:
                await self.async_login()
            assert self._token is not None
            return self._token

    async def _async_request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        *,
        tolerate_500: bool = False,
        _retry: bool = True,
    ) -> ClientResponse:
        """Issue an authenticated call against the zamapi surface."""
        token = await self._async_token()
        url = f"{HOST}/{API_PATH}/{endpoint}"

        body: dict[str, Any] = dict(params or {})
        body["secToken"] = token
        if self._user_id is not None:
            body.setdefault("userId", self._user_id)

        kwargs: dict[str, Any] = {"timeout": REQUEST_TIMEOUT}
        if method.lower() == "get":
            kwargs["params"] = {k: str(v) for k, v in body.items()}
        else:
            kwargs["data"] = {k: str(v) for k, v in body.items()}

        try:
            response = await self._session.request(method, url, **kwargs)
        except (ClientError, asyncio.TimeoutError) as err:
            raise SalusConnectionError(f"Salus request failed: {err}") from err

        if response.status in (401, 403) and _retry:
            _LOGGER.debug("Token rejected on %s, re-authenticating", endpoint)
            self._token = None
            return await self._async_request(
                method, endpoint, params, tolerate_500=tolerate_500, _retry=False
            )
        if response.status in (401, 403):
            raise SalusAuthError("Salus rejected the session token twice")
        if response.status == 429:
            raise SalusRateLimitError("Salus is rate limiting this address")
        if response.status == 500 and tolerate_500:
            _LOGGER.debug("%s returned 500; Salus often does this on success", endpoint)
            return response
        if response.status >= 400:
            raise SalusConnectionError(f"{endpoint} returned {response.status}")

        return response

    async def async_close(self) -> None:
        """Nothing to release; the aiohttp session is owned by Home Assistant."""
        self._token = None

    # --- Reads -------------------------------------------------------------

    async def async_list_devices(self) -> list[dict[str, Any]]:
        """Return every device on the account."""
        response = await self._async_request("post", "getDeviceList")
        text = await response.text()

        try:
            root = ET.fromstring(text)
        except ET.ParseError as err:
            raise SalusConnectionError(f"Malformed device list: {err}") from err

        devices: list[dict[str, Any]] = []
        for element in root.iter():
            if _strip_ns(element.tag) != "devList":
                continue
            entry = {
                _strip_ns(child.tag): (child.text or "").strip() for child in element
            }
            if entry.get("devId"):
                devices.append(
                    {
                        "device_id": entry["devId"],
                        "name": entry.get("devName") or f"iT500 {entry['devId']}",
                        "type_id": entry.get("typeId"),
                        "raw": entry,
                    }
                )
        return devices

    async def async_get_state(self) -> DeviceState:
        """Read every attribute and normalise it."""
        response = await self._async_request(
            "post",
            "getDeviceAttributesWithValues",
            {"devId": self._device_id, "deviceTypeId": 1},
        )
        attrs = _attributes_from_xml(await response.text())

        if not attrs:
            raise SalusDeviceNotFound(
                f"Salus returned no attributes for device {self._device_id}"
            )

        state = parse_attributes(self._device_id, attrs)
        # A thermostat that has lost its RF link reports a room temperature of
        # zero across the board rather than an explicit offline flag.
        state.online = state.ch1.current_temperature not in (None, 0.0)
        return state

    # --- Writes ------------------------------------------------------------

    async def _async_set(self, pairs: dict[str, Any]) -> None:
        """Write attribute name/value pairs, chunked to the API limit."""
        if not pairs:
            return

        items = list(pairs.items())
        for start in range(0, len(items), MAX_PAIRS_PER_CALL):
            chunk = items[start : start + MAX_PAIRS_PER_CALL]
            params: dict[str, Any] = {"devId": self._device_id}
            for index, (name, value) in enumerate(chunk, start=1):
                params[f"name{index}"] = name
                params[f"value{index}"] = value

            _LOGGER.debug("Salus set %s", chunk)
            response = await self._async_request(
                "get", "setMultiDeviceAttributes2", params, tolerate_500=True
            )

            if response.status == 500:
                # Well-known false negative; the coordinator refresh confirms.
                continue

            text = await response.text()
            try:
                root = ET.fromstring(text)
            except ET.ParseError:
                _LOGGER.debug("Non-XML set response: %s", text[:200])
                continue

            error = root.findtext("errorMsg")
            if error:
                raise SalusCommandError(f"Salus refused the command: {error}")

            ret_code = root.findtext("retCode")
            if ret_code not in (None, "", "0"):
                raise SalusCommandError(f"Salus returned code {ret_code}")

    @staticmethod
    def _zone_attr(zone: str, attr: ZoneAttr) -> str:
        try:
            return ZONE_PREFIX[zone].value + attr.value
        except KeyError as err:
            raise SalusCommandError(f"Unknown zone '{zone}'") from err

    async def async_set_target_temperature(self, zone: str, temperature: float) -> None:
        """Hold ``temperature`` until the next scheduled change."""
        await self._async_set(
            {
                self._zone_attr(zone, ZoneAttr.CH_TEMP_HOLD_MODE): 1,
                self._zone_attr(zone, ZoneAttr.CH_SETPOINT): int(
                    round(temperature * 100)
                ),
            }
        )

    async def async_set_heating_mode(self, zone: str, mode: HeatingMode) -> None:
        """Apply the three-flag combination that represents ``mode``."""
        if mode is HeatingMode.UNKNOWN:
            raise SalusCommandError("Cannot set an unknown heating mode")

        off_flag, manual_flag, hold_flag = HEATING_MODE_FLAGS[mode]
        await self._async_set(
            {
                self._zone_attr(zone, ZoneAttr.CH_OFF_MODE): off_flag,
                self._zone_attr(zone, ZoneAttr.CH_MANUAL_MODE): manual_flag,
                self._zone_attr(zone, ZoneAttr.CH_TEMP_HOLD_MODE): hold_flag,
            }
        )

    async def async_set_hot_water_mode(self, mode: HotWaterMode) -> None:
        """Set the hot water channel mode."""
        await self._async_set(
            {Prefix.HW.value + ZoneAttr.HW_MODE.value: int(mode)}
        )

    async def async_set_boost(self, zone: str, hours: int) -> None:
        """Start a boost of ``hours``, or cancel it with 0."""
        hours = max(0, min(int(hours), 3))
        attr = (
            ZoneAttr.HW_BOOST_HOURS if zone == "hw" else ZoneAttr.CH_BOOST_HOURS
        )
        await self._async_set({self._zone_attr(zone, attr): hours})

    async def async_set_frost_temperature(self, temperature: float) -> None:
        """Set the frost-protection setpoint."""
        await self._async_set(
            {SystemAttr.FROST_TEMPERATURE.value: int(round(temperature * 100))}
        )

    async def async_set_temperature_offset(self, offset: float) -> None:
        """Calibrate the room temperature reading."""
        await self._async_set(
            {SystemAttr.DISPLAY_OFFSET.value: int(round(offset * 100))}
        )

    async def async_set_span(self, span: float) -> None:
        """Set the switching differential."""
        await self._async_set({SystemAttr.SPAN.value: int(round(span * 100))})

    async def async_set_program(self, zone: str, day: str, program: str) -> None:
        """Write one day of the weekly program."""
        key = day.strip().lower()
        if key not in DAY_ATTR:
            raise SalusCommandError(f"'{day}' is not a day of the week")
        await self._async_set({self._zone_attr(zone, DAY_ATTR[key]): program})

    async def async_set_holiday(
        self, enabled: bool, start: str | None = None, end: str | None = None
    ) -> None:
        """Enable or disable holiday mode, optionally with a date range."""
        pairs: dict[str, Any] = {SystemAttr.HOLIDAY_OPTION.value: 1 if enabled else 0}
        if start:
            pairs[SystemAttr.HOLIDAY_START.value] = start
        if end:
            pairs[SystemAttr.HOLIDAY_END.value] = end
        await self._async_set(pairs)
