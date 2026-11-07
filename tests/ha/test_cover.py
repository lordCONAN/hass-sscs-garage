"""Cover and sensors, with the BLE client's send() mocked."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.cover import DOMAIN as COVER_DOMAIN, CoverState
from homeassistant.const import ATTR_ENTITY_ID, ATTR_ASSUMED_STATE, CONF_ADDRESS, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.sscs_garage import protocol
from custom_components.sscs_garage.client import Result, SSCSClient
from custom_components.sscs_garage.const import CONF_KEY, CONF_OPEN_WHEN, CONF_STATE_SENSOR, CONF_TRAVEL_TIME, DOMAIN
from custom_components.sscs_garage.protocol import Command

from .fakes import TEST_ADDRESS, TEST_KEY

COVER = "cover.garage_shutter"
STATUS = "sensor.garage_shutter_ble_status"
FINGERPRINT = "sensor.garage_shutter_key_fingerprint"
CONTACT = "binary_sensor.garage_contact"


@pytest.fixture(autouse=True)
def no_bluetooth_setup() -> Generator[None]:
    with patch("homeassistant.loader.Integration.dependencies", new=[]):
        yield


@pytest.fixture
def send() -> Generator[MagicMock]:
    with patch.object(SSCSClient, "send") as mock:
        yield mock


async def setup(hass: HomeAssistant, options: dict | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Garage Shutter",
        unique_id=TEST_ADDRESS,
        data={CONF_ADDRESS: TEST_ADDRESS, CONF_KEY: TEST_KEY.hex()},
        options=options or {},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def call(hass: HomeAssistant, service: str) -> None:
    await hass.services.async_call(COVER_DOMAIN, service, {ATTR_ENTITY_ID: COVER}, blocking=True)


def report(entry: MockConfigEntry, cmd: Command, result: Result) -> None:
    client: SSCSClient = entry.runtime_data.client
    for listener in list(client._result_listeners):
        listener(cmd, result)


async def test_entities(hass: HomeAssistant, send: MagicMock, entity_registry: er.EntityRegistry) -> None:
    await setup(hass)
    state = hass.states.get(COVER)
    assert state.state == CoverState.CLOSED
    assert state.attributes[ATTR_ASSUMED_STATE] is True
    assert state.attributes["device_class"] == "garage"
    assert hass.states.get(STATUS).state == "Idle"
    assert hass.states.get(FINGERPRINT).state == protocol.key_fingerprint(TEST_KEY)
    for entity_id in (STATUS, FINGERPRINT):
        assert entity_registry.async_get(entity_id).entity_category == "diagnostic"
    assert entity_registry.async_get(COVER).unique_id == f"{TEST_ADDRESS}-cover"


async def test_assumed_open_close_stop(hass: HomeAssistant, send: MagicMock) -> None:
    entry = await setup(hass)
    await call(hass, "open_cover")
    send.assert_called_once_with(Command.UP)
    assert hass.states.get(COVER).state == CoverState.OPENING

    report(entry, Command.UP, Result.ACKED)
    await hass.async_block_till_done()
    assert hass.states.get(COVER).state == CoverState.OPENING  # still travelling

    await call(hass, "stop_cover")
    send.assert_called_with(Command.STOP)
    assert hass.states.get(COVER).state == CoverState.OPEN

    await call(hass, "close_cover")
    send.assert_called_with(Command.DOWN)
    assert hass.states.get(COVER).state == CoverState.CLOSING


async def test_travel_time(hass: HomeAssistant, send: MagicMock, freezer) -> None:
    from datetime import timedelta

    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    await setup(hass, {CONF_TRAVEL_TIME: 10})
    await call(hass, "open_cover")
    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(COVER).state == CoverState.OPEN


@pytest.mark.parametrize("result", [Result.NOT_FOUND, Result.NO_CHALLENGE, Result.REJECTED, Result.DISCONNECTED])
async def test_reverts_when_not_delivered(hass: HomeAssistant, send: MagicMock, result: Result) -> None:
    entry = await setup(hass)
    await call(hass, "open_cover")
    report(entry, Command.UP, result)
    await hass.async_block_till_done()
    assert hass.states.get(COVER).state == CoverState.CLOSED


async def test_sent_no_ack_keeps_motion(hass: HomeAssistant, send: MagicMock) -> None:
    entry = await setup(hass)
    await call(hass, "open_cover")
    report(entry, Command.UP, Result.SENT_NO_ACK)
    await hass.async_block_till_done()
    assert hass.states.get(COVER).state == CoverState.OPENING


async def test_state_sensor(hass: HomeAssistant, send: MagicMock) -> None:
    hass.states.async_set(CONTACT, STATE_ON)
    entry = await setup(hass, {CONF_STATE_SENSOR: CONTACT, CONF_OPEN_WHEN: "on"})
    assert hass.states.get(COVER).state == CoverState.OPEN
    assert hass.states.get(COVER).attributes["state_source"] == CONTACT

    await call(hass, "close_cover")
    assert hass.states.get(COVER).state == CoverState.CLOSING
    # Reaching closed ends the close.
    hass.states.async_set(CONTACT, STATE_OFF)
    await hass.async_block_till_done()
    assert hass.states.get(COVER).state == CoverState.CLOSED

    # A failed open does not override what the sensor says.
    await call(hass, "open_cover")
    report(entry, Command.UP, Result.NOT_FOUND)
    await hass.async_block_till_done()
    assert hass.states.get(COVER).state == CoverState.CLOSED


async def test_state_sensor_inverted_and_unavailable(hass: HomeAssistant, send: MagicMock) -> None:
    hass.states.async_set(CONTACT, STATE_ON)
    await setup(hass, {CONF_STATE_SENSOR: CONTACT, CONF_OPEN_WHEN: "off"})
    assert hass.states.get(COVER).state == CoverState.CLOSED

    hass.states.async_set(CONTACT, "unavailable")
    await hass.async_block_till_done()
    assert hass.states.get(COVER).attributes["state_source"] == "assumed"
    await call(hass, "open_cover")
    assert hass.states.get(COVER).state == CoverState.OPENING


async def test_status_sensor_follows_client(hass: HomeAssistant, send: MagicMock) -> None:
    entry = await setup(hass)
    client: SSCSClient = entry.runtime_data.client
    client._set_status("Connecting")
    await hass.async_block_till_done()
    assert hass.states.get(STATUS).state == "Connecting"
    client._set_status("down: shutter not found")
    await hass.async_block_till_done()
    assert hass.states.get(STATUS).state == "down: shutter not found"


async def test_unload(hass: HomeAssistant, send: MagicMock) -> None:
    entry = await setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(COVER).state == "unavailable"


async def test_key_not_in_states(hass: HomeAssistant, send: MagicMock) -> None:
    await setup(hass)
    for state in hass.states.async_all():
        assert TEST_KEY.hex() not in str(state.as_dict()).lower()
