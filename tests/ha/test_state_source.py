"""Door sensor, state source select and active state source sensor."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, mock_restore_cache

from homeassistant.components.cover import DOMAIN as COVER_DOMAIN, CoverState
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, ATTR_FRIENDLY_NAME, CONF_ADDRESS, STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er

from custom_components.sscs_garage.client import SSCSClient
from custom_components.sscs_garage.const import CONF_KEY, CONF_OPEN_WHEN, CONF_STATE_SENSOR, DOMAIN

from .fakes import TEST_ADDRESS, TEST_KEY

COVER = "cover.garage_shutter"
DOOR = "binary_sensor.garage_shutter_door"
SELECT = "select.garage_shutter_state_source"
ACTIVE = "sensor.garage_shutter_active_state_source"
CONTACT = "binary_sensor.garage_contact"


@pytest.fixture(autouse=True)
def no_bluetooth_setup() -> Generator[None]:
    with patch("homeassistant.loader.Integration.dependencies", new=[]):
        yield


@pytest.fixture(autouse=True)
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


def contact(hass: HomeAssistant, state: str) -> None:
    hass.states.async_set(CONTACT, state, {ATTR_FRIENDLY_NAME: "Garage contact"})


async def choose(hass: HomeAssistant, option: str) -> None:
    await hass.services.async_call(
        SELECT_DOMAIN, "select_option", {ATTR_ENTITY_ID: SELECT, "option": option}, blocking=True
    )


async def test_without_state_sensor(hass: HomeAssistant, entity_registry: er.EntityRegistry) -> None:
    await setup(hass)
    assert hass.states.get(DOOR).state == STATE_UNKNOWN
    assert hass.states.get(DOOR).attributes["device_class"] == "garage_door"
    assert hass.states.get(ACTIVE).state == "Assumed"
    assert hass.states.get(SELECT) is None  # nothing to choose between
    assert entity_registry.async_get(ACTIVE).entity_category == "diagnostic"


async def test_with_state_sensor(hass: HomeAssistant, entity_registry: er.EntityRegistry) -> None:
    contact(hass, STATE_ON)
    await setup(hass, {CONF_STATE_SENSOR: CONTACT, CONF_OPEN_WHEN: "on"})
    assert hass.states.get(DOOR).state == STATE_ON
    assert hass.states.get(ACTIVE).state == "Garage contact"
    select = hass.states.get(SELECT)
    assert select.state == "sensor"
    assert select.attributes["options"] == ["assumed", "sensor"]
    assert entity_registry.async_get(SELECT).entity_category == "config"

    contact(hass, STATE_OFF)
    await hass.async_block_till_done()
    assert hass.states.get(DOOR).state == STATE_OFF
    assert hass.states.get(COVER).state == CoverState.CLOSED

    contact(hass, "unavailable")
    await hass.async_block_till_done()
    assert hass.states.get(DOOR).state == STATE_UNKNOWN
    assert hass.states.get(ACTIVE).state == "Assumed (sensor unavailable)"


async def test_inverted_sensor(hass: HomeAssistant) -> None:
    contact(hass, STATE_ON)
    await setup(hass, {CONF_STATE_SENSOR: CONTACT, CONF_OPEN_WHEN: "off"})
    assert hass.states.get(DOOR).state == STATE_OFF  # on means closed here
    assert hass.states.get(COVER).state == CoverState.CLOSED


async def test_select_switches_cover_to_assumed(hass: HomeAssistant, send: MagicMock) -> None:
    contact(hass, STATE_OFF)
    await setup(hass, {CONF_STATE_SENSOR: CONTACT})
    assert hass.states.get(COVER).state == CoverState.CLOSED

    await choose(hass, "assumed")
    assert hass.states.get(SELECT).state == "assumed"
    assert hass.states.get(ACTIVE).state == "Assumed"
    assert hass.states.get(COVER).attributes["state_source"] == "assumed"

    # The cover now follows commands, not the sensor; the Door sensor still shows it.
    await hass.services.async_call(COVER_DOMAIN, "open_cover", {ATTR_ENTITY_ID: COVER}, blocking=True)
    await hass.services.async_call(COVER_DOMAIN, "stop_cover", {ATTR_ENTITY_ID: COVER}, blocking=True)
    assert hass.states.get(COVER).state == CoverState.OPEN
    # Same state, new attribute: still a state_changed event the cover must ignore.
    hass.states.async_set(CONTACT, STATE_OFF, {ATTR_FRIENDLY_NAME: "Garage contact", "x": 1})
    await hass.async_block_till_done()
    assert hass.states.get(COVER).state == CoverState.OPEN
    assert hass.states.get(DOOR).state == STATE_OFF

    # Back to the sensor: the cover takes its reading at once.
    await choose(hass, "sensor")
    assert hass.states.get(COVER).state == CoverState.CLOSED
    assert hass.states.get(COVER).attributes["state_source"] == CONTACT


async def test_select_restored(hass: HomeAssistant) -> None:
    mock_restore_cache(hass, [State(SELECT, "assumed")])
    contact(hass, STATE_ON)
    await setup(hass, {CONF_STATE_SENSOR: CONTACT})
    assert hass.states.get(SELECT).state == "assumed"
    assert hass.states.get(ACTIVE).state == "Assumed"
    assert hass.states.get(DOOR).state == STATE_ON


async def test_select_rejects_unknown_option(hass: HomeAssistant) -> None:
    from homeassistant.exceptions import ServiceValidationError

    contact(hass, STATE_ON)
    await setup(hass, {CONF_STATE_SENSOR: CONTACT})
    with pytest.raises(ServiceValidationError):
        await choose(hass, "camera")
    assert hass.states.get(SELECT).state == "sensor"
