"""SSCs (Sysdes Smart Garage) BLE shutter controller.

Talks to the shutter through Home Assistant's Bluetooth stack (a local adapter or an
ESPHome Bluetooth proxy). Replaces the ESPHome sscs_garage component.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant

from .client import SSCSClient
from .const import CONF_KEY, CONF_OPEN_WHEN, CONF_STATE_SENSOR, DEFAULT_OPEN_WHEN, OPEN_WHEN_ON
from .source import StateSource

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.COVER, Platform.SELECT, Platform.SENSOR]


@dataclass
class SSCSData:
    """What the platforms of one shutter share."""

    client: SSCSClient
    source: StateSource


type SSCSConfigEntry = ConfigEntry[SSCSData]


async def async_setup_entry(hass: HomeAssistant, entry: SSCSConfigEntry) -> bool:
    """Set up one shutter from a config entry."""
    options = entry.options
    source = StateSource(
        hass,
        options.get(CONF_STATE_SENSOR) or None,
        options.get(CONF_OPEN_WHEN, DEFAULT_OPEN_WHEN) == OPEN_WHEN_ON,
    )
    entry.runtime_data = SSCSData(
        client=SSCSClient(hass, entry.data[CONF_ADDRESS], bytes.fromhex(entry.data[CONF_KEY])),
        source=source,
    )
    source.async_start()
    entry.async_on_unload(source.async_stop)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SSCSConfigEntry) -> bool:
    """Unload a config entry; cancels a session in progress."""
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.client.async_stop()
    return unloaded


async def _async_options_updated(hass: HomeAssistant, entry: SSCSConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
