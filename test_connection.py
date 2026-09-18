#!/usr/bin/env python3
"""Standalone connection test for the Salus iT500.

Run this before installing the integration. It has no Home Assistant
dependency - just aiohttp - so you can run it on any machine that can reach
the internet, including inside a Home Assistant terminal add-on.

    pip install aiohttp
    python3 test_connection.py you@example.com 'your-password'
    python3 test_connection.py you@example.com 'your-password' --device-id 33591764
    python3 test_connection.py you@example.com 'your-password' --transport web

It will:
  1. log in over the app API (and the website, if you ask),
  2. list the devices on your account,
  3. dump the full raw attribute map,
  4. decode it the same way the integration does.

Nothing is written to your thermostat. This is read-only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from hashlib import md5

try:
    import aiohttp
except ImportError:  # pragma: no cover
    sys.exit("This script needs aiohttp:  pip install aiohttp")

HOST = "https://sal-emea-p01-api.arrayent.com"
AUTH_URL = f"{HOST}/acc/applications/SalusService/sessions"
API = f"{HOST}/zdk/services/zamapi"
APP_AUTHORIZATION = "687886-679716122"

WEB_LOGIN = "https://salus-it500.com/public/login.php"
WEB_CONTROL = "https://salus-it500.com/public/control.php"
WEB_VALUES = "https://salus-it500.com/public/ajax_device_values.php"

TOKEN_RE = re.compile(r'<input[^>]*id="token"[^>]*value="([^"]+)"', re.I)

SYSTEM_TYPES = {0: "CH1 only", 1: "CH1 + CH2", 2: "CH1 + hot water"}
MODES = {
    (0, 0, 0): "Follow schedule",
    (0, 0, 1): "Temporary hold",
    (0, 1, 0): "Permanent hold",
    (1, 0, 0): "Off",
}
HW_MODES = {0: "Follow schedule", 1: "Once", 2: "Always on", 3: "Always off"}


def ok(message: str) -> None:
    print(f"  \033[32mOK\033[0m   {message}")


def fail(message: str) -> None:
    print(f"  \033[31mFAIL\033[0m {message}")


def info(message: str) -> None:
    print(f"       {message}")


def head(message: str) -> None:
    print(f"\n\033[1m{message}\033[0m")


def strip_ns(tag: str) -> str:
    return tag.rpartition("}")[2]


def as_int(value, default=None):
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def scaled(value):
    raw = as_int(value)
    return None if raw is None else raw / 100


def decode_schedule(raw: str) -> str:
    """Decode a heating program string, 4 characters per switching point."""
    text = str(raw or "").strip()
    out = []
    for i in range(0, len(text) - len(text) % 4, 4):
        chunk = text[i : i + 4]
        hour, minute, whole, tenths = (ord(c) - 48 for c in chunk)
        if 0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= tenths <= 9:
            out.append(f"{hour:02d}:{minute:02d} {whole + tenths / 10:g}C")
    return "  ".join(out) or "(empty)"


# --- App API ---------------------------------------------------------------


async def test_api(session, username, password, device_id):
    """Exercise the Arrayent app API end to end."""
    head("1. Logging in to the app API")
    info(AUTH_URL)

    payload = {"username": username, "password": md5(password.encode()).hexdigest()}
    headers = {
        "Authorization": APP_AUTHORIZATION,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    async with session.post(AUTH_URL, json=payload, headers=headers) as response:
        body = await response.text()
        if response.status != 200:
            fail(f"HTTP {response.status}")
            info(body[:400])
            return None
        try:
            data = json.loads(body)
        except ValueError:
            fail("Login did not return JSON")
            info(body[:400])
            return None

    token = data.get("securityToken")
    user_id = data.get("userId")
    if not token:
        fail(f"No securityToken in the response: {str(data)[:300]}")
        return None

    ok(f"Authenticated as user {user_id}")

    async def call(method, endpoint, params):
        body = {**params, "secToken": token, "userId": user_id}
        body = {k: str(v) for k, v in body.items()}
        kwargs = {"params": body} if method == "get" else {"data": body}
        async with session.request(method, f"{API}/{endpoint}", **kwargs) as resp:
            return resp.status, await resp.text()

    head("2. Listing devices on the account")
    status, text = await call("post", "getDeviceList", {})
    if status != 200:
        fail(f"getDeviceList returned HTTP {status}")
        info(text[:400])
        return None

    devices = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError as err:
        fail(f"Could not parse the device list: {err}")
        info(text[:400])
        return None

    for element in root.iter():
        if strip_ns(element.tag) != "devList":
            continue
        entry = {strip_ns(c.tag): (c.text or "").strip() for c in element}
        if entry.get("devId"):
            devices.append(entry)

    if not devices:
        fail("No devices returned. Is this the right account?")
        return None

    ok(f"Found {len(devices)} device(s)")
    for device in devices:
        marker = " <-- selected" if device["devId"] == str(device_id) else ""
        info(f"{device['devId']}  {device.get('devName', '?')}{marker}")

    target = str(device_id) if device_id else devices[0]["devId"]
    if not device_id:
        info(f"No --device-id given, using {target}")

    head(f"3. Reading attributes for device {target}")
    status, text = await call(
        "post", "getDeviceAttributesWithValues", {"devId": target, "deviceTypeId": 1}
    )
    if status != 200:
        fail(f"getDeviceAttributesWithValues returned HTTP {status}")
        info(text[:400])
        return None

    attrs = {}
    try:
        root = ET.fromstring(text)
    except ET.ParseError as err:
        fail(f"Could not parse attributes: {err}")
        info(text[:600])
        return None

    for element in root.iter():
        if strip_ns(element.tag) != "attrList":
            continue
        name = element.findtext("name")
        if name:
            attrs[name] = (element.findtext("value") or "").strip()

    if not attrs:
        fail("No attributes came back. Check the device ID.")
        info(text[:600])
        return None

    ok(f"Read {len(attrs)} attributes")
    return attrs


# --- Website ---------------------------------------------------------------


async def test_web(session, username, password, device_id):
    """Exercise the salus-it500.com portal."""
    head("Logging in to salus-it500.com")

    if not device_id:
        fail("The website route needs --device-id")
        return None

    agent = {"User-Agent": "Mozilla/5.0"}
    form = {
        "IDemail": username,
        "password": password,
        "login": "Login",
        "keep_logged_in": "1",
    }

    await session.post(WEB_LOGIN, data=form, headers=agent)
    async with session.get(
        WEB_CONTROL, params={"devId": device_id}, headers=agent
    ) as response:
        page = await response.text()

    match = TOKEN_RE.search(page)
    if not match:
        fail("No session token on the control page - check the credentials and ID")
        return None
    ok("Scraped a session token")

    params = {
        "devId": device_id,
        "token": match.group(1),
        "_": str(int(time.time() * 1000)),
    }
    async with session.get(WEB_VALUES, params=params, headers=agent) as response:
        body = await response.text()

    try:
        data = json.loads(body)
    except ValueError:
        fail("The values endpoint returned HTML, not JSON")
        info(body[:300])
        return None

    ok(f"Read {len(data)} values")
    head("Values")
    for key, value in sorted(data.items()):
        info(f"{key:<28} {value}")
    return None


# --- Reporting -------------------------------------------------------------


def report(attrs):
    """Print the decoded state the way the integration would see it."""
    head("4. Decoded state")

    system_type = as_int(attrs.get("S06"), 0)
    info(f"Description        {attrs.get('desc', '(none)')}")
    info(f"System type        {SYSTEM_TYPES.get(system_type, system_type)}")
    info(f"Firmware           {attrs.get('S13', '?')}")
    info(f"Units              {'Celsius' if as_int(attrs.get('S07'), 0) == 0 else 'Fahrenheit'}")
    info(f"Frost setpoint     {scaled(attrs.get('S09'))} C")
    info(f"Calibration        {scaled(attrs.get('S17'))} C")
    info(f"Holiday mode       {'on' if as_int(attrs.get('S10'), 0) else 'off'}")

    for prefix, label in (("A", "Heating zone 1"), ("B", "Heating zone 2")):
        if f"{prefix}84" not in attrs:
            continue
        flags = (
            as_int(attrs.get(f"{prefix}89"), 0),
            as_int(attrs.get(f"{prefix}92"), 0),
            as_int(attrs.get(f"{prefix}88"), 0),
        )
        print()
        info(f"--- {label} ---")
        info(f"Room temperature   {scaled(attrs.get(prefix + '84'))} C")
        info(f"Setpoint           {scaled(attrs.get(prefix + '85'))} C")
        info(f"Mode               {MODES.get(flags, f'unknown {flags}')}")
        info(f"Boiler calling     {'yes' if as_int(attrs.get(prefix + '87'), 0) else 'no'}")
        info(f"Frost active       {'yes' if as_int(attrs.get(prefix + '90'), 0) else 'no'}")
        info(f"Boost remaining    {as_int(attrs.get(prefix + '91'), 0)} h")

    if "C45" in attrs:
        print()
        info("--- Hot water ---")
        info(f"Relay              {'on' if as_int(attrs.get('C45'), 0) else 'off'}")
        info(f"Mode               {HW_MODES.get(as_int(attrs.get('C42')), '?')}")
        info(f"Boost remaining    {as_int(attrs.get('C43'), 0)} h")

    head("5. Weekly program, heating zone 1")
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for index, day in enumerate(days):
        raw = attrs.get(f"A0{index}", "")
        info(f"{day}  {decode_schedule(raw)}")
        if raw:
            info(f"     raw: {raw!r}")

    head("6. Raw attribute map")
    for name in sorted(attrs):
        info(f"{name:<8} {attrs[name]}")


async def main():
    """Parse arguments and run the requested test."""
    parser = argparse.ArgumentParser(description="Test Salus iT500 connectivity")
    parser.add_argument("username", help="the email address you use for the app")
    parser.add_argument("password", help="your Salus password")
    parser.add_argument("--device-id", help="devId from the portal URL")
    parser.add_argument(
        "--transport", choices=["api", "web"], default="api", help="which route to test"
    )
    args = parser.parse_args()

    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            if args.transport == "web":
                await test_web(session, args.username, args.password, args.device_id)
                return
            attrs = await test_api(
                session, args.username, args.password, args.device_id
            )
        except aiohttp.ClientError as err:
            fail(f"Network error: {err}")
            return
        except asyncio.TimeoutError:
            fail("Timed out. The Salus cloud may be down.")
            return

    if attrs:
        report(attrs)
        head("Result")
        ok("The app API works. Install the integration and use Automatic.")


if __name__ == "__main__":
    asyncio.run(main())
