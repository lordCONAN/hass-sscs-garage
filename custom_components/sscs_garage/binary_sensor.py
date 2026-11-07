"""Door: open/closed as the chosen state sensor reports it."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SSCSConfigEntry
from .entity import SSCSEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: SSCSConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([SSCSDoorSensor(entry)])


class SSCSDoorSensor(SSCSEntity, BinarySensorEntity):
    """On = open. Unknown without a state sensor or while it reports nothing.

    Follows the sensor whatever the state source select says, so the reading stays
    visible while the cover is on Assumed.
    """

    _attr_device_class = BinarySensorDeviceClass.GARAGE_DOOR
    _attr_translation_key = "door"

    def __init__(self, entry: SSCSConfigEntry) -> None:
        super().__init__(entry, "door")

    @property
    def is_on(self) -> bool | None:
        return self.source.sensor_open()

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.source.add_listener(self._on_source))

    @callback
    def _on_source(self) -> None:
        self.async_write_ha_state()
