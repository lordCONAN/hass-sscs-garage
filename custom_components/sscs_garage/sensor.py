"""Diagnostic sensors: last command result, key fingerprint, state source in use."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SSCSConfigEntry
from .entity import SSCSEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: SSCSConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([SSCSStatusSensor(entry), SSCSFingerprintSensor(entry), SSCSSourceSensor(entry)])


class SSCSStatusSensor(SSCSEntity, SensorEntity):
    """Phase of the running session, or the last result, e.g. "up: acknowledged"."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "ble_status"
    _attr_icon = "mdi:bluetooth-transfer"

    def __init__(self, entry: SSCSConfigEntry) -> None:
        super().__init__(entry, "ble_status")

    @property
    def native_value(self) -> str:
        return self.client.status

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.client.add_status_listener(self._on_status))

    @callback
    def _on_status(self, _status: str) -> None:
        self.async_write_ha_state()


class SSCSFingerprintSensor(SSCSEntity, SensorEntity):
    """First 4 bytes of SHA256(key): identifies the key without revealing it."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "key_fingerprint"
    _attr_icon = "mdi:key-chain"

    def __init__(self, entry: SSCSConfigEntry) -> None:
        super().__init__(entry, "key_fingerprint")
        self._attr_native_value = self.client.fingerprint


class SSCSSourceSensor(SSCSEntity, SensorEntity):
    """Where the cover's open/closed comes from: the sensor's name, or Assumed."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "active_state_source"
    _attr_icon = "mdi:source-branch"

    def __init__(self, entry: SSCSConfigEntry) -> None:
        super().__init__(entry, "active_state_source")

    @property
    def native_value(self) -> str:
        return self.source.description()

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.source.add_listener(self._on_source))

    @callback
    def _on_source(self) -> None:
        self.async_write_ha_state()
