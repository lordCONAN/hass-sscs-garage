"""Base entity for the SSCs garage shutter."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity import Entity

from . import SSCSConfigEntry
from .const import DOMAIN, MANUFACTURER, MODEL


class SSCSEntity(Entity):
    """Entity of one shutter device."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: SSCSConfigEntry, key: str) -> None:
        self.client = entry.runtime_data.client
        self.source = entry.runtime_data.source
        address = self.client.address
        self._attr_unique_id = f"{address}-{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            connections={(CONNECTION_BLUETOOTH, address)},
            name=entry.title,
            manufacturer=MANUFACTURER,
            model=MODEL,
        )
