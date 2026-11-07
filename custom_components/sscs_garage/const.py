"""Constants for the SSCs garage shutter integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "sscs_garage"

MANUFACTURER: Final = "Sysdes"
MODEL: Final = "SSCs Smart Garage"
DEFAULT_NAME: Final = "Garage Shutter"

# Config entry data
CONF_KEY: Final = "key"  # 64 hex chars; never logged or shown

# Options
CONF_STATE_SENSOR: Final = "state_sensor"
CONF_OPEN_WHEN: Final = "open_when"
CONF_TRAVEL_TIME: Final = "travel_time"
OPEN_WHEN_ON: Final = "on"
OPEN_WHEN_OFF: Final = "off"
DEFAULT_OPEN_WHEN: Final = OPEN_WHEN_ON
DEFAULT_TRAVEL_TIME: Final = 30  # seconds

# Session timings, the same as the ESPHome component (seconds).
CONNECT_TIMEOUT: Final = 20.0
CHALLENGE_TIMEOUT: Final = 5.0
CHALLENGE_NUDGE: Final = 1.2  # 0 = never send the 00 00 nudge
AUTH_SETTLE: Final = 0.3
ACK_TIMEOUT: Final = 3.0
# How often to look again for the shutter's advertisement while connecting.
DEVICE_POLL_INTERVAL: Final = 0.5
