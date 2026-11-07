"""Session state machine of SSCSClient against a scripted fake controller."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock, patch

from bleak.exc import BleakError
import pytest

from homeassistant.core import HomeAssistant

from custom_components.sscs_garage import protocol
from custom_components.sscs_garage.client import Result, SSCSClient, Timings
from custom_components.sscs_garage.protocol import Command

from .fakes import TEST_ADDRESS, TEST_KEY, FakeController, make_establish_connection

FAST = Timings(connect=0.4, challenge=0.3, nudge=0.1, auth_settle=0.05, ack=0.2, device_poll=0.05)
TOKEN = protocol.TOKEN_LEN


@pytest.fixture
def controller() -> FakeController:
    return FakeController()


@pytest.fixture
def device() -> MagicMock:
    dev = MagicMock()
    dev.address = TEST_ADDRESS
    dev.name = "SSCs0001"
    return dev


@pytest.fixture
def ble(controller: FakeController, device: MagicMock):
    """Patch the HA Bluetooth lookup and the connector; yields the lookup mock."""
    with (
        patch(
            "custom_components.sscs_garage.client.bluetooth.async_ble_device_from_address", return_value=device
        ) as lookup,
        patch(
            "custom_components.sscs_garage.client.establish_connection",
            side_effect=make_establish_connection(controller),
        ),
    ):
        yield lookup


@pytest.fixture
def client(hass: HomeAssistant, ble) -> SSCSClient:
    return SSCSClient(hass, TEST_ADDRESS, TEST_KEY, timings=FAST)


@pytest.fixture
def results(client: SSCSClient) -> list[tuple[Command, Result]]:
    collected: list[tuple[Command, Result]] = []
    client.add_result_listener(lambda cmd, result: collected.append((cmd, result)))
    return collected


async def run(client: SSCSClient, cmd: Command) -> None:
    client.send(cmd)
    await client.async_wait_idle()


async def test_acknowledged(client, controller, results) -> None:
    statuses: list[str] = []
    client.add_status_listener(statuses.append)
    await run(client, Command.UP)

    assert results == [(Command.UP, Result.ACKED)]
    assert len(controller.challenges) == 1
    token = protocol.make_token(TEST_KEY, controller.challenges[0], protocol.NO_PASSCODE)
    assert token[16:] == b"\xff\xff\xff\xff"
    assert controller.writes == [token, protocol.command_bytes(Command.UP)]
    assert controller.disconnects == 1
    assert statuses == ["Connecting", "Authenticating", "Sending", "up: acknowledged"]
    assert client.status == "up: acknowledged"
    assert not client.busy


async def test_nudge_when_no_challenge(client, controller, results) -> None:
    controller.challenge_on_subscribe = False
    await run(client, Command.STOP)
    assert results == [(Command.STOP, Result.ACKED)]
    assert controller.writes[0] == protocol.NUDGE
    assert controller.writes[2] == protocol.command_bytes(Command.STOP)


async def test_no_nudge_when_challenge_arrives(client, controller, results) -> None:
    await run(client, Command.DOWN)
    await asyncio.sleep(FAST.nudge * 2)  # a stray nudge timer would write now
    assert protocol.NUDGE not in controller.writes


async def test_no_challenge(client, controller, results) -> None:
    controller.challenge_on_subscribe = False
    controller.challenge_on_nudge = False
    await run(client, Command.UP)
    assert results == [(Command.UP, Result.NO_CHALLENGE)]
    assert controller.writes == [protocol.NUDGE]
    assert controller.disconnects == 1
    assert client.status == "up: no challenge received"


async def test_command_without_status(client, controller, results) -> None:
    """No cb00 after the token: carry on after the settle time, like the working firmware."""
    controller.status_after_token = False
    await run(client, Command.DOWN)
    assert results == [(Command.DOWN, Result.ACKED)]
    assert controller.writes[-1] == protocol.command_bytes(Command.DOWN)


async def test_sent_no_ack(client, controller, results) -> None:
    controller.ack_after_command = False
    await run(client, Command.DOWN)
    assert results == [(Command.DOWN, Result.SENT_NO_ACK)]
    assert results[0][1].delivered


async def test_ack_before_write_response(client, controller, results) -> None:
    controller.ack_before_write_response = True
    await run(client, Command.UP)
    assert results == [(Command.UP, Result.ACKED)]


async def test_not_found(client, ble, controller, results) -> None:
    ble.return_value = None
    await run(client, Command.UP)
    assert results == [(Command.UP, Result.NOT_FOUND)]
    assert controller.connects == 0
    assert ble.call_count > 1  # kept looking for the advertisement until the timeout
    assert not results[0][1].delivered


async def test_device_appears_late(client, ble, device, controller, results) -> None:
    ble.side_effect = [None, None, device, device, device]
    await run(client, Command.UP)
    assert results == [(Command.UP, Result.ACKED)]


async def test_connect_fails(client, controller, results) -> None:
    with patch("custom_components.sscs_garage.client.establish_connection", side_effect=BleakError("no slot")):
        await run(client, Command.UP)
    assert results == [(Command.UP, Result.NOT_FOUND)]


async def test_connect_timeout(client, controller, results) -> None:
    with patch(
        "custom_components.sscs_garage.client.establish_connection",
        side_effect=make_establish_connection(controller, delay=5),
    ):
        await run(client, Command.UP)
    assert results == [(Command.UP, Result.NOT_FOUND)]


async def test_no_service(client, controller, results) -> None:
    controller.has_service = False
    await run(client, Command.UP)
    assert results == [(Command.UP, Result.NO_SERVICE)]
    assert controller.writes == []
    assert controller.disconnects == 1


async def test_token_rejected(client, controller, results) -> None:
    controller.reject_token = True
    await run(client, Command.UP)
    assert results == [(Command.UP, Result.REJECTED)]
    assert len(controller.writes) == 1


async def test_token_write_timeout(client, controller, results) -> None:
    controller.write_delay = 1.0
    await run(client, Command.UP)
    assert results == [(Command.UP, Result.REJECTED)]


async def test_command_rejected(client, controller, results) -> None:
    controller.reject_command = True
    await run(client, Command.DOWN)
    assert results == [(Command.DOWN, Result.REJECTED)]


async def test_disconnect_before_command(client, controller, results) -> None:
    controller.disconnect_after_token = True
    controller.status_after_token = False
    await run(client, Command.UP)
    assert results == [(Command.UP, Result.DISCONNECTED)]
    assert len(controller.writes) == 1  # the command was never written


async def test_disconnect_after_command(client, controller, results) -> None:
    controller.disconnect_after_command = True
    await run(client, Command.UP)
    assert results == [(Command.UP, Result.SENT_NO_ACK)]


async def test_newer_command_replaces_unwritten(client, controller, results) -> None:
    client.send(Command.UP)
    client.send(Command.STOP)
    client.send(Command.DOWN)
    await client.async_wait_idle()
    assert results == [(Command.DOWN, Result.ACKED)]
    assert controller.connects == 1
    assert controller.writes[1:] == [protocol.command_bytes(Command.DOWN)]


async def test_command_after_write_runs_next(client, controller, results) -> None:
    statuses: list[str] = []

    def on_status(status: str) -> None:
        statuses.append(status)
        if status == "Sending" and len(statuses) < 5:
            client.send(Command.STOP)  # arrives once 'up' has been written

    client.add_status_listener(on_status)
    await run(client, Command.UP)
    assert results == [(Command.UP, Result.ACKED), (Command.STOP, Result.ACKED)]
    assert controller.connects == 2
    assert controller.max_active == 1  # one session at a time
    commands = [w for w in controller.writes if len(w) == protocol.COMMAND_LEN]
    assert commands == [protocol.command_bytes(Command.UP), protocol.command_bytes(Command.STOP)]


async def test_passcode_tail(hass: HomeAssistant, ble, controller) -> None:
    """Unverified passcode modes still produce the reference tail."""
    client = SSCSClient(hass, TEST_ADDRESS, TEST_KEY, passcode=12345678, timings=FAST)
    await run(client, Command.STOP)
    token = controller.writes[0]
    assert token == protocol.make_token(TEST_KEY, controller.challenges[0], 12345678)
    assert token[16:] != b"\xff\xff\xff\xff"


async def test_stop_cancels_session(client, controller, results) -> None:
    controller.challenge_on_subscribe = False
    controller.challenge_on_nudge = False
    client.send(Command.UP)
    await asyncio.sleep(0.05)
    assert client.busy
    await client.async_stop()
    assert not client.busy
    assert results == []


async def test_key_never_logged(client, controller, caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="custom_components.sscs_garage")
    controller.challenge_on_subscribe = False
    await run(client, Command.UP)
    controller.ack_after_command = False
    await run(client, Command.DOWN)
    text = caplog.text + repr(client)
    assert TEST_KEY.hex() not in text.lower()
    assert TEST_KEY.hex()[:16] not in text.lower()
    for challenge in controller.challenges:
        token = protocol.make_token(TEST_KEY, challenge, protocol.NO_PASSCODE)
        assert token.hex() not in text.lower()
    assert client.fingerprint in repr(client)


def test_bad_key_length(hass: HomeAssistant) -> None:
    with pytest.raises(ValueError):
        SSCSClient(hass, TEST_ADDRESS, b"short")
