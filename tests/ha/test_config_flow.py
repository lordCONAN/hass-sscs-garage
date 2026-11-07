"""Config and options flow."""

from __future__ import annotations

from collections.abc import Generator
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_USER
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.sscs_garage import protocol
from custom_components.sscs_garage.const import (
    CONF_KEY,
    CONF_OPEN_WHEN,
    CONF_STATE_SENSOR,
    CONF_TRAVEL_TIME,
    DEFAULT_NAME,
    DOMAIN,
)

from .fakes import TEST_ADDRESS, TEST_KEY
from .helpers import service_info

OTHER_ADDRESS = "AA:BB:CC:DD:EE:01"
DISCOVERED = service_info(TEST_ADDRESS, "SSCs0001", rssi=-58)


def _qr_text() -> tuple[str, bytes]:
    """First QR vector from tests/reference.py (random key, never a real one)."""
    import re

    out = subprocess.run(
        [sys.executable, "-I", str(Path(__file__).resolve().parents[1] / "reference.py")],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    text, key = re.findall(r'^  \{"([^"]+)", (\{[^}]*\})\},$', out, re.M)[0]
    return text, bytes(int(x, 16) for x in re.findall(r"0x([0-9a-f]{2})", key))


QR_TEXT, QR_KEY = _qr_text()


@pytest.fixture(autouse=True)
def mock_setup_entry() -> Generator[AsyncMock]:
    with patch("custom_components.sscs_garage.async_setup_entry", return_value=True) as mock:
        yield mock


@pytest.fixture(autouse=True)
def no_bluetooth_setup() -> Generator[None]:
    """The flow only needs the integration's own code, not a running Bluetooth stack."""
    with patch("homeassistant.loader.Integration.dependencies", new=[]):
        yield


def _assert_no_key(result: dict) -> None:
    """Nothing the frontend receives may contain the key."""
    text = str(result).lower()
    for key in (TEST_KEY, QR_KEY):
        assert key.hex() not in text


async def test_bluetooth_discovery_hex_key(hass: HomeAssistant, mock_setup_entry: AsyncMock) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=DISCOVERED)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "key"
    assert result["description_placeholders"] == {"name": "SSCs0001", "address": TEST_ADDRESS}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_KEY: TEST_KEY.hex(":").upper(), CONF_NAME: "Garage Shutter"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Garage Shutter"
    assert result["data"] == {CONF_ADDRESS: TEST_ADDRESS, CONF_KEY: TEST_KEY.hex()}
    assert result["result"].unique_id == TEST_ADDRESS
    assert len(mock_setup_entry.mock_calls) == 1


async def test_bluetooth_discovery_qr_key(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=DISCOVERED)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_KEY: f"  {QR_TEXT}\n"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == DEFAULT_NAME
    assert result["data"][CONF_KEY] == QR_KEY.hex()


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("", "key_empty"),
        ("abc", "key_bad_format"),
        ("R2" + QR_TEXT[2:], "key_unsupported_slot"),
        ("R4" + QR_TEXT[2:], "key_unsupported_slot"),
        ("0" + QR_TEXT, "key_share_code"),
        (QR_TEXT + "z", "key_bad_length"),
        (QR_TEXT + "0", "key_bad_format"),
    ],
)
async def test_key_errors(hass: HomeAssistant, text: str, error: str) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=DISCOVERED)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_KEY: text, CONF_NAME: "Door"})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "key"
    assert result["errors"] == {CONF_KEY: error}
    _assert_no_key(result)
    # The name survives the retry; the key field is never pre-filled.
    schema = {str(k): k for k in result["data_schema"].schema}
    assert schema[CONF_NAME].default() == "Door"
    assert not callable(getattr(schema[CONF_KEY], "default", None))

    # Still fixable in the same flow.
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_KEY: TEST_KEY.hex()})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Door"


async def test_bluetooth_already_configured(hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id=TEST_ADDRESS, data={CONF_ADDRESS: TEST_ADDRESS}).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=DISCOVERED)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_bluetooth_not_sscs(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=service_info(OTHER_ADDRESS, "Thermometer")
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_supported"


async def test_user_no_devices(hass: HomeAssistant) -> None:
    with patch("custom_components.sscs_garage.config_flow.async_discovered_service_info", return_value=[]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_user_picks_from_nearby(hass: HomeAssistant) -> None:
    configured = "AA:BB:CC:DD:EE:02"
    MockConfigEntry(domain=DOMAIN, unique_id=configured, data={CONF_ADDRESS: configured}).add_to_hass(hass)
    nearby = [
        service_info(OTHER_ADDRESS, "SSCg0002", rssi=-80),
        DISCOVERED,
        service_info("AA:BB:CC:DD:EE:03", "Thermometer"),
        service_info(configured, "SSCs0003"),
    ]
    with patch("custom_components.sscs_garage.config_flow.async_discovered_service_info", return_value=nearby):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    choices = result["data_schema"].schema[CONF_ADDRESS].container
    # Only SSCs devices not configured yet, strongest first.
    assert list(choices) == [TEST_ADDRESS, OTHER_ADDRESS]
    assert choices[TEST_ADDRESS] == f"SSCs0001 ({TEST_ADDRESS}, -58 dBm)"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ADDRESS: OTHER_ADDRESS})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "key"
    assert result["description_placeholders"]["name"] == "SSCg0002"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_KEY: QR_TEXT})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == OTHER_ADDRESS
    assert result["data"] == {CONF_ADDRESS: OTHER_ADDRESS, CONF_KEY: QR_KEY.hex()}


async def test_options_flow(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=TEST_ADDRESS, data={CONF_ADDRESS: TEST_ADDRESS, CONF_KEY: TEST_KEY.hex()}
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    assert result["description_placeholders"] == {"fingerprint": protocol.key_fingerprint(TEST_KEY)}
    _assert_no_key(result)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_STATE_SENSOR: "binary_sensor.garage_contact", CONF_OPEN_WHEN: "off", CONF_TRAVEL_TIME: 25},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        CONF_STATE_SENSOR: "binary_sensor.garage_contact",
        CONF_OPEN_WHEN: "off",
        CONF_TRAVEL_TIME: 25,
    }

    # Defaults come from the stored options next time; the sensor is optional.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema = {str(k): k for k in result["data_schema"].schema}
    assert schema[CONF_OPEN_WHEN].default() == "off"
    assert schema[CONF_TRAVEL_TIME].default() == 25
    assert schema[CONF_STATE_SENSOR].description == {"suggested_value": "binary_sensor.garage_contact"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_OPEN_WHEN: "on", CONF_TRAVEL_TIME: 30}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert CONF_STATE_SENSOR not in entry.options
