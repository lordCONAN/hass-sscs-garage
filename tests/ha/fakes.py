"""A scripted stand-in for the shutter controller behind a BleakClient.

Random keys only: never a real shutter key.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
import os
from typing import Any

from bleak.exc import BleakError

from custom_components.sscs_garage import protocol

TEST_KEY = bytes.fromhex("5a" * 16 + "c3" * 16)
TEST_ADDRESS = "AA:BB:CC:DD:EE:FF"


@dataclass
class FakeController:
    """How the fake controller behaves; flip fields to script failures."""

    has_service: bool = True
    challenge_on_subscribe: bool = True
    challenge_on_nudge: bool = True
    status_after_token: bool = True
    ack_after_command: bool = True
    ack_before_write_response: bool = False
    reject_token: bool = False
    reject_command: bool = False
    disconnect_after_token: bool = False
    disconnect_after_command: bool = False
    write_delay: float = 0.0
    flags: bytes = bytes.fromhex("12100900")

    challenges: list[bytes] = field(default_factory=list)
    writes: list[bytes] = field(default_factory=list)
    connects: int = 0
    disconnects: int = 0
    active: int = 0
    max_active: int = 0


class FakeServices:
    def __init__(self, controller: FakeController) -> None:
        self._controller = controller

    def get_characteristic(self, uuid: str) -> object | None:
        if not self._controller.has_service:
            return None
        return object() if uuid in (protocol.CMD_UUID, protocol.DATA_UUID) else None


class FakeBleakClient:
    """Implements the slice of BleakClient the session uses."""

    def __init__(self, controller: FakeController, disconnected_callback: Callable[[Any], None] | None) -> None:
        self.controller = controller
        self._disconnected_callback = disconnected_callback
        self._notify: dict[str, Callable[[Any, bytearray], None]] = {}
        self.connected = True
        self.services = FakeServices(controller)

    def _emit(self, data: bytes) -> None:
        if self.connected and (cb := self._notify.get(protocol.CMD_UUID)) is not None:
            asyncio.get_running_loop().call_soon(cb, None, bytearray(data))

    def _send_challenge(self) -> None:
        packet = os.urandom(16) + self.controller.flags
        self.controller.challenges.append(packet)
        self._emit(packet)

    def _drop(self) -> None:
        if self.connected:
            self.connected = False
            if self._disconnected_callback is not None:
                asyncio.get_running_loop().call_soon(self._disconnected_callback, self)

    async def start_notify(self, uuid: str, callback: Callable[[Any, bytearray], None]) -> None:
        self._notify[uuid] = callback
        if uuid == protocol.CMD_UUID and self.controller.challenge_on_subscribe:
            self._send_challenge()

    async def write_gatt_char(self, uuid: str, data: bytes, response: bool = False) -> None:
        assert uuid == protocol.CMD_UUID
        assert response is True
        if not self.connected:
            raise BleakError("Not connected")
        data = bytes(data)
        self.controller.writes.append(data)
        c = self.controller
        if data == protocol.NUDGE:
            if c.challenge_on_nudge and not c.challenges:
                self._send_challenge()
            return
        if len(data) == protocol.TOKEN_LEN:
            await asyncio.sleep(c.write_delay)
            if c.reject_token:
                raise BleakError("Write rejected")
            if c.status_after_token:
                self._emit(b"\xcb\x00\x01\x02")
            if c.disconnect_after_token:
                self._drop()
            return
        if len(data) == protocol.COMMAND_LEN:
            if c.ack_before_write_response:
                self._emit(b"\xcc\x00")
                await asyncio.sleep(0)
                raise BleakError("Write response lost")
            await asyncio.sleep(c.write_delay)
            if c.reject_command:
                raise BleakError("Write rejected")
            if c.disconnect_after_command:
                self._drop()
            elif c.ack_after_command:
                self._emit(b"\xcc\x00")
            return
        raise AssertionError(f"unexpected write of {len(data)} bytes")

    async def disconnect(self) -> bool:
        self.controller.disconnects += 1
        self.controller.active -= 1
        self._drop()
        return True


def make_establish_connection(controller: FakeController, delay: float = 0.0):
    """Replacement for bleak_retry_connector.establish_connection."""

    async def establish_connection(client_class, device, name, disconnected_callback=None, **kwargs):
        await asyncio.sleep(delay)
        controller.connects += 1
        controller.active += 1
        controller.max_active = max(controller.max_active, controller.active)
        return FakeBleakClient(controller, disconnected_callback)

    return establish_connection
