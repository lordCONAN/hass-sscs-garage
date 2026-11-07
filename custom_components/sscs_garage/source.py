"""Where open/closed comes from: the chosen state sensor, or assumed from the last command.

One StateSource per shutter, shared by the cover, the Door binary sensor, the state
source sensor and the state source select, so they always agree.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event


class SourceMode(StrEnum):
    """Options of the state source select."""

    ASSUMED = "assumed"
    SENSOR = "sensor"


class StateSource:
    """Tracks the state sensor and which source is selected."""

    def __init__(self, hass: HomeAssistant, sensor: str | None, open_when_on: bool) -> None:
        self._hass = hass
        self.sensor = sensor
        self._open_state = STATE_ON if open_when_on else STATE_OFF
        # With a sensor configured it is used unless the select says otherwise.
        self._mode = SourceMode.SENSOR if sensor else SourceMode.ASSUMED
        self._listeners: list[Callable[[], None]] = []
        self._unsub: CALLBACK_TYPE | None = None

    @callback
    def async_start(self) -> None:
        if self.sensor is not None and self._unsub is None:
            self._unsub = async_track_state_change_event(self._hass, [self.sensor], self._on_sensor)

    @callback
    def async_stop(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    @callback
    def add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    @callback
    def _on_sensor(self, _event: Event[EventStateChangedData]) -> None:
        self._notify()

    # ---------------------------------------------------------------- selection

    @property
    def modes(self) -> list[SourceMode]:
        return [SourceMode.ASSUMED, SourceMode.SENSOR] if self.sensor else [SourceMode.ASSUMED]

    @property
    def mode(self) -> SourceMode:
        return self._mode

    @callback
    def set_mode(self, mode: SourceMode) -> None:
        if mode not in self.modes or mode == self._mode:
            return
        self._mode = mode
        self._notify()

    # ---------------------------------------------------------------- readings

    def sensor_open(self) -> bool | None:
        """What the state sensor says (whatever the selection), or None if it says nothing."""
        if self.sensor is None or (state := self._hass.states.get(self.sensor)) is None:
            return None
        if state.state not in (STATE_ON, STATE_OFF):
            return None
        return state.state == self._open_state

    def effective_open(self) -> bool | None:
        """Open/closed the cover should show, or None when it must assume."""
        return self.sensor_open() if self._mode is SourceMode.SENSOR else None

    def sensor_name(self) -> str | None:
        if self.sensor is None:
            return None
        state = self._hass.states.get(self.sensor)
        return state.name if state is not None else self.sensor

    def description(self) -> str:
        """Shown by the state source sensor."""
        if self._mode is SourceMode.ASSUMED or self.sensor is None:
            return "Assumed"
        if self.sensor_open() is None:
            return "Assumed (sensor unavailable)"
        return self.sensor_name() or self.sensor
