"""BLE session with one SSCs shutter controller.

One short session per command, the same flow as the ESPHome component: connect,
subscribe DATA then CMD, answer the 20-byte challenge, write the command, wait for
cc00, disconnect. cc00 means the controller received the command, not that the door
moved.

A command that arrives while a session has not written its command yet replaces the
queued one; a command arriving after the write runs in the next session. Sessions
are serialised by an asyncio lock.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
import logging
import time
from typing import TypeVar

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from homeassistant.components import bluetooth
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback

from . import protocol
from .const import ACK_TIMEOUT, AUTH_SETTLE, CHALLENGE_NUDGE, CHALLENGE_TIMEOUT, CONNECT_TIMEOUT, DEVICE_POLL_INTERVAL
from .protocol import Command

_LOGGER = logging.getLogger(__name__)

_T = TypeVar("_T")


class Result(StrEnum):
    """Outcome of one command session; the value is what the status sensor shows."""

    ACKED = "acknowledged"  # cc00 received
    SENT_NO_ACK = "sent, no acknowledgement"  # command written, no cc00 in time (may still have moved)
    NOT_FOUND = "shutter not found"  # never connected within the connect timeout
    NO_SERVICE = "GATT service missing"  # connected but service/characteristics missing
    NO_CHALLENGE = "no challenge received"  # no 20-byte challenge after subscribing
    REJECTED = "write rejected"  # token or command write refused
    DISCONNECTED = "disconnected early"  # link dropped before the command was written

    @property
    def delivered(self) -> bool:
        """True when the command reached the controller (with or without ACK)."""
        return self in (Result.ACKED, Result.SENT_NO_ACK)


@dataclass(frozen=True)
class Timings:
    """Session timeouts in seconds."""

    connect: float = CONNECT_TIMEOUT
    challenge: float = CHALLENGE_TIMEOUT
    nudge: float = CHALLENGE_NUDGE
    auth_settle: float = AUTH_SETTLE
    ack: float = ACK_TIMEOUT
    device_poll: float = DEVICE_POLL_INTERVAL


class _SessionEnded(Exception):
    """Ends a session early with a result."""

    def __init__(self, result: Result) -> None:
        super().__init__(result.value)
        self.result = result


class SSCSClient:
    """Sends commands to one shutter controller."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        key: bytes,
        passcode: int = protocol.NO_PASSCODE,
        timings: Timings | None = None,
    ) -> None:
        if len(key) != protocol.KEY_LEN:
            raise ValueError("key must be 32 bytes")
        self._hass = hass
        self.address = address
        self._key = bytes(key)
        self._passcode = passcode
        self.timings = timings or Timings()
        self.fingerprint = protocol.key_fingerprint(self._key)

        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._current: Command | None = None  # command of the running session, replaceable until written
        self._written = False
        self._pending: Command | None = None  # next session
        self._status = "Idle"
        self._result_listeners: list[Callable[[Command, Result], None]] = []
        self._status_listeners: list[Callable[[str], None]] = []

    def __repr__(self) -> str:  # never include the key
        return f"SSCSClient({self.address}, fingerprint {self.fingerprint})"

    # ---------------------------------------------------------------- listeners

    @property
    def status(self) -> str:
        """Current phase, or the last command's result ("down: acknowledged")."""
        return self._status

    @property
    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    @callback
    def add_result_listener(self, listener: Callable[[Command, Result], None]) -> CALLBACK_TYPE:
        self._result_listeners.append(listener)
        return lambda: self._result_listeners.remove(listener)

    @callback
    def add_status_listener(self, listener: Callable[[str], None]) -> CALLBACK_TYPE:
        self._status_listeners.append(listener)
        return lambda: self._status_listeners.remove(listener)

    def _set_status(self, status: str) -> None:
        self._status = status
        for listener in list(self._status_listeners):
            listener(status)

    # ---------------------------------------------------------------- commands

    @callback
    def send(self, cmd: Command) -> None:
        """Queue a command and return at once; the result arrives via the listeners."""
        if self.busy:
            if not self._written and self._current is not None:
                # Nothing written yet: the newest request wins.
                if cmd != self._current:
                    _LOGGER.info("Replacing queued '%s' with '%s'", self._current, cmd)
                    self._current = cmd
            else:
                _LOGGER.info("'%s' will be sent after the current session", cmd)
                self._pending = cmd
            return
        self._current = cmd
        self._task = self._hass.async_create_background_task(self._run(), f"sscs_garage {self.address}")

    async def async_wait_idle(self) -> None:
        """Wait until queued commands have finished (tests, unload)."""
        while (task := self._task) is not None and not task.done():
            await asyncio.shield(task)

    async def async_stop(self) -> None:
        """Cancel any session (config entry unload)."""
        self._pending = None
        if (task := self._task) is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        async with self._lock:
            while self._current is not None:
                self._written = False
                try:
                    result = await self._session()
                except _SessionEnded as end:
                    result = end.result
                except Exception:
                    _LOGGER.exception("Unexpected error talking to %s", self.address)
                    result = Result.SENT_NO_ACK if self._written else Result.DISCONNECTED
                cmd = self._current
                self._finish(cmd, result)
                self._current, self._pending = self._pending, None
                self._written = False

    def _finish(self, cmd: Command, result: Result) -> None:
        if result is Result.ACKED:
            _LOGGER.info("'%s' %s", cmd, result)
        else:
            _LOGGER.warning("'%s' %s", cmd, result)
        self._set_status(f"{cmd}: {result}")
        for listener in list(self._result_listeners):
            listener(cmd, result)

    # ---------------------------------------------------------------- session

    async def _find_device(self, deadline: float) -> BLEDevice:
        """Wait for a connectable advertisement from the shutter."""
        while True:
            device = bluetooth.async_ble_device_from_address(self._hass, self.address, connectable=True)
            if device is not None:
                return device
            if time.monotonic() + self.timings.device_poll > deadline:
                raise _SessionEnded(Result.NOT_FOUND)
            await asyncio.sleep(self.timings.device_poll)

    async def _connect(self, deadline: float, disconnected: asyncio.Event) -> BleakClientWithServiceCache:
        device = await self._find_device(deadline)

        def on_disconnect(_client: object) -> None:
            disconnected.set()

        try:
            async with asyncio.timeout(max(deadline - time.monotonic(), 0.1)):
                return await establish_connection(
                    BleakClientWithServiceCache,
                    device,
                    device.name or self.address,
                    disconnected_callback=on_disconnect,
                    ble_device_callback=lambda: (
                        bluetooth.async_ble_device_from_address(self._hass, self.address, connectable=True) or device
                    ),
                )
        except (TimeoutError, BleakError) as err:
            _LOGGER.debug("Connect to %s failed: %s", self.address, err)
            raise _SessionEnded(Result.NOT_FOUND) from err

    async def _session(self) -> Result:
        _LOGGER.info("Sending '%s'", self._current)
        self._set_status("Connecting")
        disconnected = asyncio.Event()
        deadline = time.monotonic() + self.timings.connect
        client = await self._connect(deadline, disconnected)
        try:
            return await self._authenticate_and_send(client, disconnected)
        finally:
            try:
                await client.disconnect()
            except BleakError as err:
                _LOGGER.debug("Disconnect from %s failed: %s", self.address, err)

    async def _authenticate_and_send(self, client: BleakClientWithServiceCache, disconnected: asyncio.Event) -> Result:
        loop = asyncio.get_running_loop()
        challenge: asyncio.Future[bytes] = loop.create_future()
        status_seen = asyncio.Event()
        acked = asyncio.Event()

        if client.services.get_characteristic(protocol.CMD_UUID) is None or (
            client.services.get_characteristic(protocol.DATA_UUID) is None
        ):
            return Result.NO_SERVICE

        def on_cmd(_char: object, data: bytearray) -> None:
            packet = bytes(data)
            if not challenge.done() and len(packet) == protocol.CHALLENGE_PACKET_LEN:
                challenge.set_result(packet)
            elif protocol.is_status(packet):
                _LOGGER.debug("Status notification")
                status_seen.set()
            elif protocol.is_ack(packet):
                acked.set()
            else:
                _LOGGER.debug("Unhandled CMD notification (%d bytes)", len(packet))

        # Same order as the app: DATA first, then CMD; the challenge follows on CMD.
        try:
            await client.start_notify(protocol.DATA_UUID, lambda _char, _data: None)
        except BleakError as err:
            _LOGGER.debug("Could not subscribe to DATA: %s", err)
        try:
            await client.start_notify(protocol.CMD_UUID, on_cmd)
        except BleakError as err:
            _LOGGER.warning("Could not subscribe to CMD: %s", err)
            return Result.NO_SERVICE if not disconnected.is_set() else Result.DISCONNECTED

        self._set_status("Authenticating")
        packet = await self._challenge(client, challenge, disconnected)
        _LOGGER.debug("Challenge received (flags 0x%08x)", protocol.challenge_flags(packet))

        token = protocol.make_token(self._key, packet, self._passcode)
        await self._write(client, token, disconnected, "Token")
        if not status_seen.is_set():
            # No cb00 yet; the working firmware just waited, so carry on after the settle time.
            await self._wait(status_seen.wait(), self.timings.auth_settle, disconnected, optional=True)

        # From here a newer command waits for the next session; set before the
        # status update, whose listeners may send one.
        self._written = True
        cmd = self._current
        assert cmd is not None
        self._set_status("Sending")
        try:
            await self._write(client, protocol.command_bytes(cmd), disconnected, "Command")
        except _SessionEnded as end:
            if acked.is_set():  # cc00 can arrive before the write response
                return Result.ACKED
            raise end
        try:
            await self._wait(acked.wait(), self.timings.ack, disconnected)
        except _SessionEnded:
            return Result.SENT_NO_ACK
        return Result.ACKED

    async def _challenge(
        self, client: BleakClientWithServiceCache, challenge: asyncio.Future[bytes], disconnected: asyncio.Event
    ) -> bytes:
        nudge_task: asyncio.Task[None] | None = None
        if 0 < self.timings.nudge < self.timings.challenge:

            async def nudge() -> None:
                await asyncio.sleep(self.timings.nudge)
                if challenge.done():
                    return
                # Some sessions only produce the challenge after an INI write of 00 00.
                _LOGGER.debug("No challenge yet, sending 00 00")
                try:
                    await client.write_gatt_char(protocol.CMD_UUID, protocol.NUDGE, response=True)
                except BleakError as err:
                    _LOGGER.debug("Nudge write failed: %s", err)

            nudge_task = asyncio.create_task(nudge())
        try:
            return await self._wait(asyncio.shield(challenge), self.timings.challenge, disconnected)
        finally:
            if nudge_task is not None:
                nudge_task.cancel()

    async def _write(
        self, client: BleakClientWithServiceCache, data: bytes, disconnected: asyncio.Event, what: str
    ) -> None:
        try:
            await self._wait(
                client.write_gatt_char(protocol.CMD_UUID, data, response=True),
                self.timings.ack,
                disconnected,
                timeout_result=Result.REJECTED,
            )
        except BleakError as err:
            _LOGGER.warning("%s write failed: %s", what, err)
            raise _SessionEnded(Result.DISCONNECTED if disconnected.is_set() else Result.REJECTED) from None

    async def _wait(
        self,
        aw: Awaitable[_T],
        timeout: float,
        disconnected: asyncio.Event,
        *,
        optional: bool = False,
        timeout_result: Result = Result.NO_CHALLENGE,
    ) -> _T | None:
        """Await aw for up to timeout seconds, ending the session if the link drops.

        A timeout raises _SessionEnded(timeout_result), or returns None when optional.
        A disconnect raises _SessionEnded(DISCONNECTED).
        """
        work = asyncio.ensure_future(aw)
        drop = asyncio.ensure_future(disconnected.wait())
        try:
            done, _ = await asyncio.wait({work, drop}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        finally:
            drop.cancel()
        if work in done:
            return work.result()
        work.cancel()
        if drop in done:
            raise _SessionEnded(Result.DISCONNECTED)
        if optional:
            return None
        raise _SessionEnded(timeout_result)
