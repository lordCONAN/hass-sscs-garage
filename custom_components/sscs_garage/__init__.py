"""SSCs (Sysdes Smart Garage) BLE shutter controller.

Talks to the shutter through Home Assistant's Bluetooth stack (a local adapter or an
ESPHome Bluetooth proxy). Replaces the ESPHome sscs_garage component.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant

from .client import SSCSClient
from .const import CONF_KEY

PLATFORMS: list[Platform] = [Platform.COVER, Platform.SENSOR]

type SSCSConfigEntry = ConfigEntry[SSCSClient]


async def async_setup_entry(hass: HomeAssistant, entry: SSCSConfigEntry) -> bool:
    """Set up one shutter from a config entry."""
    entry.runtime_data = SSCSClient(hass, entry.data[CONF_ADDRESS], bytes.fromhex(entry.data[CONF_KEY]))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SSCSConfigEntry) -> bool:
    """Unload a config entry; cancels a session in progress."""
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_stop()
    return unloaded


async def _async_options_updated(hass: HomeAssistant, entry: SSCSConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
