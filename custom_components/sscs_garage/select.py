"""State source select: Assumed, or the state sensor chosen in the options."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import SSCSConfigEntry
from .entity import SSCSEntity
from .source import SourceMode


async def async_setup_entry(
    hass: HomeAssistant, entry: SSCSConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    # Without a state sensor there is nothing to choose between.
    if entry.runtime_data.source.sensor is not None:
        async_add_entities([SSCSSourceSelect(entry)])


class SSCSSourceSelect(SSCSEntity, SelectEntity, RestoreEntity):
    """Switches the cover between the state sensor and assumed state; remembered across restarts."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "state_source"
    _attr_icon = "mdi:garage-alert"

    def __init__(self, entry: SSCSConfigEntry) -> None:
        super().__init__(entry, "state_source_select")
        self._attr_options = [mode.value for mode in self.source.modes]

    @property
    def current_option(self) -> str:
        return self.source.mode.value

    async def async_added_to_hass(self) -> None:
        if (last := await self.async_get_last_state()) is not None and last.state in self.options:
            self.source.set_mode(SourceMode(last.state))
        self.async_on_remove(self.source.add_listener(self._on_source))

    async def async_select_option(self, option: str) -> None:
        self.source.set_mode(SourceMode(option))

    @callback
    def _on_source(self) -> None:
        self.async_write_ha_state()
