"""Constants for the Salus iT500 integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "salus_it500"
MANUFACTURER: Final = "Salus Controls"

# --- Config / options keys -------------------------------------------------

CONF_DEVICE_ID: Final = "device_id"
CONF_DEVICE_NAME: Final = "device_name"
CONF_TRANSPORT: Final = "transport"
CONF_SCAN_INTERVAL: Final = "scan_interval"

TRANSPORT_API: Final = "api"  # Arrayent cloud API (what the mobile app uses)
TRANSPORT_WEB: Final = "web"  # salus-it500.com portal scraping
TRANSPORT_AUTO: Final = "auto"  # try api, fall back to web

TRANSPORTS: Final = [TRANSPORT_AUTO, TRANSPORT_API, TRANSPORT_WEB]

# Salus rate-limits aggressively and has been known to temporarily block IPs
# that poll too hard. 60s is a safe floor; the default is deliberately gentle.
DEFAULT_SCAN_INTERVAL: Final = 120
MIN_SCAN_INTERVAL: Final = 60
MAX_SCAN_INTERVAL: Final = 900

# --- Physical limits -------------------------------------------------------

MIN_TEMP: Final = 5.0
MAX_TEMP: Final = 35.0
TEMP_STEP: Final = 0.5

FROST_MIN_TEMP: Final = 5.0
FROST_MAX_TEMP: Final = 17.0

OFFSET_MIN: Final = -3.0
OFFSET_MAX: Final = 3.0
OFFSET_STEP: Final = 0.5

SPAN_VALUES: Final = [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]

MAX_BOOST_HOURS: Final = 3

# --- Zones -----------------------------------------------------------------

ZONE_CH1: Final = "ch1"
ZONE_CH2: Final = "ch2"
ZONE_HW: Final = "hw"

# --- Presets ---------------------------------------------------------------

PRESET_FOLLOW_SCHEDULE: Final = "Follow schedule"
PRESET_TEMP_HOLD: Final = "Temporary hold"
PRESET_PERMANENT_HOLD: Final = "Permanent hold"
PRESET_BOOST: Final = "Boost"
PRESET_FROST: Final = "Frost protection"

# --- Services --------------------------------------------------------------

SERVICE_SET_SCHEDULE: Final = "set_schedule"
SERVICE_GET_SCHEDULE: Final = "get_schedule"
SERVICE_BOOST: Final = "boost"
SERVICE_CANCEL_BOOST: Final = "cancel_boost"
SERVICE_ADVANCE: Final = "advance"
SERVICE_SKIP: Final = "skip"
SERVICE_SET_HOLIDAY: Final = "set_holiday"
SERVICE_CLEAR_HOLIDAY: Final = "clear_holiday"
SERVICE_REFRESH: Final = "refresh"

ATTR_ZONE: Final = "zone"
ATTR_DAY: Final = "day"
ATTR_ENTRIES: Final = "entries"
ATTR_HOURS: Final = "hours"
ATTR_START: Final = "start"
ATTR_END: Final = "end"

DAYS: Final = [
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]
