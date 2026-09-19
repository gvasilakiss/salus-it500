"""The Salus iT500 integration."""

from __future__ import annotations

import logging

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import (
    SalusAuthError,
    SalusConnectionError,
    SalusError,
    async_create_client,
)
from .const import (
    CONF_DEVICE_ID,
    CONF_SCAN_INTERVAL,
    CONF_TRANSPORT,
    DEFAULT_SCAN_INTERVAL,
    TRANSPORT_AUTO,
)
from .coordinator import SalusConfigEntry, SalusDataUpdateCoordinator
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.WATER_HEATER,
]


async def async_setup_entry(hass: HomeAssistant, entry: SalusConfigEntry) -> bool:
    """Set up Salus iT500 from a config entry."""
    # A dedicated session with a cookie jar: the web transport needs cookies,
    # and we do not want them leaking into other integrations.
    session = async_create_clientsession(hass)

    transport = entry.options.get(
        CONF_TRANSPORT, entry.data.get(CONF_TRANSPORT, TRANSPORT_AUTO)
    )
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

    try:
        client = await async_create_client(
            session,
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD],
            entry.data[CONF_DEVICE_ID],
            transport,
        )
    except SalusAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except SalusConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err
    except SalusError as err:
        raise ConfigEntryNotReady(f"Unexpected Salus error: {err}") from err

    coordinator = SalusDataUpdateCoordinator(hass, entry, client, scan_interval)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    _LOGGER.debug(
        "Salus iT500 %s set up over the %s transport, system type %s",
        coordinator.device_id,
        client.transport,
        coordinator.data.system_type.name,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    async_setup_services(hass)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: SalusConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.client.async_close()
    return unloaded


async def _async_options_updated(hass: HomeAssistant, entry: SalusConfigEntry) -> None:
    """Reload when the transport or poll interval changes."""
    await hass.config_entries.async_reload(entry.entry_id)
