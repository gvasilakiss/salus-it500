"""Config flow for the Salus iT500 integration."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    SalusAuthError,
    SalusConnectionError,
    SalusDeviceNotFound,
    SalusError,
    SalusRateLimitError,
    async_create_client,
)
from .const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_SCAN_INTERVAL,
    CONF_TRANSPORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    TRANSPORT_AUTO,
    TRANSPORTS,
)
from .coordinator import SalusConfigEntry

_LOGGER = logging.getLogger(__name__)

DEVICE_ID_RE = re.compile(r"(\d{4,})")
MANUAL_DEVICE_ID = "manual_device_id"

CREDENTIALS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
        ),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.PASSWORD, autocomplete="current-password"
            )
        ),
        vol.Optional(CONF_TRANSPORT, default=TRANSPORT_AUTO): SelectSelector(
            SelectSelectorConfig(
                options=TRANSPORTS,
                mode=SelectSelectorMode.DROPDOWN,
                translation_key="transport",
            )
        ),
    }
)


def _device_schema(devices: list[dict[str, Any]]) -> vol.Schema:
    """Build the device-selection form.

    With a discovered device list, a dropdown is offered and the manual
    field becomes an override. The web transport (and an API account with
    no visible devices) yields nothing to discover, so only the manual
    field is shown.
    """
    manual_field = (
        vol.Optional(MANUAL_DEVICE_ID) if devices else vol.Required(MANUAL_DEVICE_ID)
    )
    schema: dict[Any, Any] = {}
    if devices:
        options = [
            SelectOptionDict(
                value=str(device["device_id"]),
                label=f"{device.get('name') or device['device_id']} ({device['device_id']})",
            )
            for device in devices
        ]
        schema[vol.Optional(CONF_DEVICE_ID)] = SelectSelector(
            SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN)
        )
    schema[manual_field] = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT))
    return vol.Schema(schema)


def extract_device_id(raw: str) -> str | None:
    """Pull a device ID out of a bare number or a control.php URL."""
    match = DEVICE_ID_RE.search(str(raw or ""))
    return match.group(1) if match else None


class SalusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Walk the user through adding an iT500."""

    VERSION = 1

    def __init__(self) -> None:
        """Start with nothing collected."""
        self._credentials: dict[str, Any] = {}
        self._devices: list[dict[str, Any]] = []

    # --- Initial setup -----------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect credentials and authenticate, then move on to device selection."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._credentials = {
                CONF_USERNAME: user_input[CONF_USERNAME],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
                CONF_TRANSPORT: user_input.get(CONF_TRANSPORT, TRANSPORT_AUTO),
            }
            errors = await self._async_try_login()
            if not errors:
                return await self.async_step_device()

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                CREDENTIALS_SCHEMA, user_input or {}
            ),
            errors=errors,
            description_placeholders={"portal": "https://salus-it500.com"},
        )

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick a discovered device, or type an ID manually."""
        errors: dict[str, str] = {}

        if user_input is not None:
            raw = user_input.get(MANUAL_DEVICE_ID) or user_input.get(CONF_DEVICE_ID) or ""
            device_id = extract_device_id(raw)
            if not device_id:
                errors["base"] = "invalid_device_id"
            else:
                verify = await self._async_verify_device(device_id)
                if verify:
                    errors["base"] = verify
                else:
                    return await self._async_create(
                        {"device_id": device_id, "name": self._device_name(device_id)}
                    )

        return self.async_show_form(
            step_id="device",
            data_schema=_device_schema(self._devices),
            errors=errors,
            description_placeholders={
                "devices_found": (
                    "No devices were found on this account - enter your device "
                    "ID manually."
                    if not self._devices
                    else f"Found {len(self._devices)} device(s) on this account. "
                    "Pick one, or enter a device ID manually to override it."
                ),
                "example": "https://salus-it500.com/public/control.php?devId=33591764",
            },
        )

    # --- Reauth ------------------------------------------------------------

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Salus rejected the stored credentials."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the password again and re-test it."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            self._credentials = {
                CONF_USERNAME: entry.data[CONF_USERNAME],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
                CONF_TRANSPORT: entry.options.get(
                    CONF_TRANSPORT, entry.data.get(CONF_TRANSPORT, TRANSPORT_AUTO)
                ),
            }
            errors = await self._async_try_login(entry.data[CONF_DEVICE_ID])

            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(
                            type=TextSelectorType.PASSWORD,
                            autocomplete="current-password",
                        )
                    )
                }
            ),
            errors=errors,
            description_placeholders={"username": entry.data[CONF_USERNAME]},
        )

    # --- Helpers -----------------------------------------------------------

    async def _async_try_login(self, device_id: str = "0") -> dict[str, str]:
        """Attempt a login and cache any device list we get back."""
        session = async_create_clientsession(self.hass)
        self._devices = []

        try:
            client = await async_create_client(
                session,
                self._credentials[CONF_USERNAME],
                self._credentials[CONF_PASSWORD],
                device_id,
                self._credentials.get(CONF_TRANSPORT, TRANSPORT_AUTO),
            )
            # Remember which route actually answered, so setup is predictable.
            self._credentials[CONF_TRANSPORT] = client.transport
            try:
                self._devices = await client.async_list_devices()
            except SalusError as err:
                _LOGGER.debug("Could not enumerate devices: %s", err)
            await client.async_close()
        except SalusAuthError:
            return {"base": "invalid_auth"}
        except SalusRateLimitError:
            return {"base": "rate_limited"}
        except SalusConnectionError:
            return {"base": "cannot_connect"}
        except SalusError as err:
            _LOGGER.exception("Unexpected Salus error during setup: %s", err)
            return {"base": "unknown"}

        return {}

    async def _async_verify_device(self, device_id: str) -> str | None:
        """Confirm the device ID actually resolves. Returns an error key."""
        session = async_create_clientsession(self.hass)
        try:
            client = await async_create_client(
                session,
                self._credentials[CONF_USERNAME],
                self._credentials[CONF_PASSWORD],
                device_id,
                self._credentials.get(CONF_TRANSPORT, TRANSPORT_AUTO),
            )
            await client.async_get_state()
            await client.async_close()
        except SalusDeviceNotFound:
            return "invalid_device_id"
        except SalusAuthError:
            return "invalid_auth"
        except SalusConnectionError:
            return "cannot_connect"
        except SalusError:
            return "unknown"
        return None

    def _device_name(self, device_id: str) -> str:
        """Use the account's own label for the device, if we found one."""
        for device in self._devices:
            if device["device_id"] == device_id:
                return device.get("name") or f"Salus iT500 {device_id}"
        return f"Salus iT500 {device_id}"

    async def _async_create(self, device: dict[str, Any]) -> ConfigFlowResult:
        """Create the entry for the chosen device."""
        device_id = str(device["device_id"])
        await self.async_set_unique_id(f"{DOMAIN}_{device_id}")
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=device.get("name") or f"Salus iT500 {device_id}",
            data={
                CONF_USERNAME: self._credentials[CONF_USERNAME],
                CONF_PASSWORD: self._credentials[CONF_PASSWORD],
                CONF_DEVICE_ID: device_id,
                CONF_DEVICE_NAME: device.get("name"),
                CONF_TRANSPORT: self._credentials.get(CONF_TRANSPORT, TRANSPORT_AUTO),
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: SalusConfigEntry) -> SalusOptionsFlow:
        """Return the options flow."""
        return SalusOptionsFlow()


class SalusOptionsFlow(OptionsFlow):
    """Tune the poll interval and transport after setup."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and save the options."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                    CONF_TRANSPORT: user_input[CONF_TRANSPORT],
                }
            )

        current_transport = self.config_entry.options.get(
            CONF_TRANSPORT, self.config_entry.data.get(CONF_TRANSPORT, TRANSPORT_AUTO)
        )
        current_interval = self.config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL, default=current_interval
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL,
                            max=MAX_SCAN_INTERVAL,
                            step=30,
                            unit_of_measurement="s",
                            mode=NumberSelectorMode.SLIDER,
                        )
                    ),
                    vol.Required(
                        CONF_TRANSPORT, default=current_transport
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=TRANSPORTS,
                            mode=SelectSelectorMode.DROPDOWN,
                            translation_key="transport",
                        )
                    ),
                }
            ),
        )
