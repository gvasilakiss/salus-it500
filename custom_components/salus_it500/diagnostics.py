"""Diagnostics support for the Salus iT500 integration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .coordinator import SalusConfigEntry

TO_REDACT = {CONF_USERNAME, CONF_PASSWORD, "device_id", "devId", "unique_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SalusConfigEntry
) -> dict[str, Any]:
    """Return everything useful for a bug report, minus the credentials."""
    coordinator = entry.runtime_data
    state = coordinator.data

    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "connection": {
            "backend": coordinator.transport,
            "last_update_success": coordinator.last_update_success,
            "last_successful_update": (
                coordinator.last_success_at.isoformat()
                if coordinator.last_success_at
                else None
            ),
            "last_update_duration_seconds": coordinator.last_update_duration,
            "last_error": coordinator.last_error,
            "update_interval": str(coordinator.update_interval),
        },
        "capabilities": {
            "supports_schedules": coordinator.client.supports_schedules,
            "supports_second_zone": coordinator.client.supports_second_zone,
            "supports_boost": coordinator.client.supports_boost,
            "supports_calibration": coordinator.client.supports_calibration,
            "supports_differential": coordinator.client.supports_differential,
            "supports_holiday": coordinator.client.supports_holiday,
            # Device-driven, not transport-driven - see api/client.py.
            "has_ch2": state.has_ch2,
            "has_hot_water": state.hw.available,
            "has_battery_info": state.battery_low is not None,
        },
        "state": async_redact_data(
            {
                **{
                    k: v
                    for k, v in asdict(state).items()
                    if k not in ("raw", "ch1", "ch2", "hw")
                },
                "system_type": state.system_type.name,
                "ch1": asdict(state.ch1),
                "ch2": asdict(state.ch2),
                "hw": asdict(state.hw),
            },
            TO_REDACT,
        ),
        # The raw attribute map is the single most useful thing when the
        # protocol shifts under us, so include it verbatim. It is Salus
        # attribute codes and values only - never credentials or tokens.
        "raw_attributes": state.raw,
    }

