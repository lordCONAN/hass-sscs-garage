"""Open/close/stop cover for the shutter.

Open/closed comes from the shared StateSource: the binary sensor chosen in the
options (when the state source select is on it), else assumed from the last command.
The cover always reports assumed_state so both buttons stay available: a contact at
the bottom cannot tell "fully open" from "partly open".
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.cover import CoverDeviceClass, CoverEntity, CoverEntityFeature
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.restore_state import RestoreEntity

from . import SSCSConfigEntry
from .client import Result
from .const import CONF_TRAVEL_TIME, DEFAULT_TRAVEL_TIME
from .entity import SSCSEntity
from .protocol import Command


async def async_setup_entry(
    hass: HomeAssistant, entry: SSCSConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([SSCSCover(entry)])


class SSCSCover(SSCSEntity, CoverEntity, RestoreEntity):
    """The shutter."""

    _attr_name = None  # the device name, e.g. "Garage Shutter"
    _attr_device_class = CoverDeviceClass.GARAGE
    _attr_assumed_state = True
    _attr_supported_features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP

    def __init__(self, entry: SSCSConfigEntry) -> None:
        super().__init__(entry, "cover")
        self._travel_time = float(entry.options.get(CONF_TRAVEL_TIME, DEFAULT_TRAVEL_TIME))
        self._attr_is_closed = True
        self._attr_is_opening = False
        self._attr_is_closing = False
        self._closed_before = True
        self._motion_timer: CALLBACK_TYPE | None = None

    async def async_added_to_hass(self) -> None:
        if (last := await self.async_get_last_state()) is not None and last.state in ("open", "closed"):
            self._attr_is_closed = last.state == "closed"
        self.async_on_remove(self.client.add_result_listener(self._on_result))
        self.async_on_remove(self.source.add_listener(self._on_source))
        self.async_on_remove(self._cancel_motion)
        self._apply_source_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"state_source": self.source.sensor if self.source.effective_open() is not None else "assumed"}

    # ---------------------------------------------------------------- commands

    async def async_open_cover(self, **kwargs: Any) -> None:
        self._start_motion(Command.UP)

    async def async_close_cover(self, **kwargs: Any) -> None:
        self._start_motion(Command.DOWN)

    async def async_stop_cover(self, **kwargs: Any) -> None:
        self.client.send(Command.STOP)
        self._cancel_motion()
        self._attr_is_opening = self._attr_is_closing = False
        self.async_write_ha_state()

    @callback
    def _start_motion(self, cmd: Command) -> None:
        self.client.send(cmd)
        self._closed_before = bool(self._attr_is_closed)
        self._attr_is_opening = cmd is Command.UP
        self._attr_is_closing = cmd is Command.DOWN
        if self.source.effective_open() is None:
            self._attr_is_closed = cmd is Command.DOWN
        self._cancel_motion()
        self._motion_timer = async_call_later(self.hass, self._travel_time, self._motion_done)
        self.async_write_ha_state()

    @callback
    def _motion_done(self, _now: Any) -> None:
        self._motion_timer = None
        self._attr_is_opening = self._attr_is_closing = False
        self.async_write_ha_state()

    @callback
    def _cancel_motion(self) -> None:
        if self._motion_timer is not None:
            self._motion_timer()
            self._motion_timer = None

    @callback
    def _on_result(self, cmd: Command, result: Result) -> None:
        if result.delivered:
            return
        was_moving = (cmd is Command.UP and self._attr_is_opening) or (cmd is Command.DOWN and self._attr_is_closing)
        if not was_moving:
            return
        # The command never reached the shutter: undo the optimistic change.
        self._cancel_motion()
        self._attr_is_opening = self._attr_is_closing = False
        if self.source.effective_open() is None:
            self._attr_is_closed = self._closed_before
        self.async_write_ha_state()

    # ---------------------------------------------------------------- state source

    @callback
    def _on_source(self) -> None:
        self._apply_source_state()
        self.async_write_ha_state()

    @callback
    def _apply_source_state(self) -> None:
        if (is_open := self.source.effective_open()) is None:
            return  # assumed: keep the last known position
        self._attr_is_closed = not is_open
        # Reaching "closed" ends a close; "open" only means it left the closed end.
        if not is_open and self._attr_is_closing:
            self._cancel_motion()
            self._attr_is_closing = False
