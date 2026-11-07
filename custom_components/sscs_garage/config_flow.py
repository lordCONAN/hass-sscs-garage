"""Config and options flow for the SSCs garage shutter.

Discovery (or a list of nearby SSCs devices) picks the shutter; the key step accepts
the 64-hex key or the setup QR text. The key is never logged or shown again; the
fingerprint identifies it instead.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import BluetoothServiceInfoBleak, async_discovered_service_info
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from . import protocol
from .const import (
    CONF_KEY,
    CONF_OPEN_WHEN,
    CONF_STATE_SENSOR,
    CONF_TRAVEL_TIME,
    DEFAULT_NAME,
    DEFAULT_OPEN_WHEN,
    DEFAULT_TRAVEL_TIME,
    DOMAIN,
    OPEN_WHEN_OFF,
    OPEN_WHEN_ON,
)
from .protocol import KeyParseResult

KEY_ERRORS: dict[KeyParseResult, str] = {
    KeyParseResult.EMPTY: "key_empty",
    KeyParseResult.BAD_FORMAT: "key_bad_format",
    KeyParseResult.BAD_LENGTH: "key_bad_length",
    KeyParseResult.UNSUPPORTED_SLOT: "key_unsupported_slot",
    KeyParseResult.SHARE_CODE: "key_share_code",
}


class SSCSConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add an SSCs shutter."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovery: BluetoothServiceInfoBleak | None = None
        self._discovered: dict[str, BluetoothServiceInfoBleak] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> SSCSOptionsFlow:
        return SSCSOptionsFlow()

    async def async_step_bluetooth(self, discovery_info: BluetoothServiceInfoBleak) -> ConfigFlowResult:
        """A shutter was found by the bluetooth matcher in the manifest."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        if not protocol.is_sscs_name(discovery_info.name):
            return self.async_abort(reason="not_supported")
        self._discovery = discovery_info
        self.context["title_placeholders"] = {"name": discovery_info.name, "address": discovery_info.address}
        return await self.async_step_key()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose from the SSCs devices heard nearby."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            self._discovery = self._discovered[address]
            return await self.async_step_key()

        configured = self._async_current_ids(include_ignore=False)
        for info in async_discovered_service_info(self.hass, connectable=True):
            if info.address in configured or info.address in self._discovered:
                continue
            if protocol.is_sscs_name(info.name):
                self._discovered[info.address] = info
        if not self._discovered:
            return self.async_abort(reason="no_devices_found")

        options = {
            address: f"{info.name} ({address}, {info.rssi} dBm)"
            for address, info in sorted(self._discovered.items(), key=lambda item: -(item[1].rssi or -127))
        }
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): vol.In(options)}),
        )

    async def async_step_key(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Enter the key as 64 hex characters or the setup QR text."""
        assert self._discovery is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            result, key = protocol.parse_key_result(user_input.get(CONF_KEY, ""))
            if key is not None:
                return self.async_create_entry(
                    title=user_input.get(CONF_NAME) or DEFAULT_NAME,
                    data={
                        CONF_ADDRESS: self._discovery.address,
                        CONF_KEY: key.hex(),
                    },
                )
            errors[CONF_KEY] = KEY_ERRORS[result]

        name = (user_input or {}).get(CONF_NAME, DEFAULT_NAME)
        return self.async_show_form(
            step_id="key",
            data_schema=vol.Schema(
                {
                    # The key field is never pre-filled, even after an error.
                    vol.Required(CONF_KEY): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                    ),
                    vol.Optional(CONF_NAME, default=name): str,
                }
            ),
            errors=errors,
            description_placeholders={
                "name": self._discovery.name,
                "address": self._discovery.address,
            },
        )


class SSCSOptionsFlow(OptionsFlow):
    """State sensor, its polarity, and travel time."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        sensor_key: vol.Marker = vol.Optional(CONF_STATE_SENSOR)
        if (sensor := options.get(CONF_STATE_SENSOR)) is not None:
            sensor_key = vol.Optional(CONF_STATE_SENSOR, description={"suggested_value": sensor})
        schema = vol.Schema(
            {
                sensor_key: selector.EntitySelector(selector.EntitySelectorConfig(domain="binary_sensor")),
                vol.Required(CONF_OPEN_WHEN, default=options.get(CONF_OPEN_WHEN, DEFAULT_OPEN_WHEN)): (
                    selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[OPEN_WHEN_ON, OPEN_WHEN_OFF],
                            translation_key=CONF_OPEN_WHEN,
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    )
                ),
                vol.Required(CONF_TRAVEL_TIME, default=options.get(CONF_TRAVEL_TIME, DEFAULT_TRAVEL_TIME)): (
                    selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1, max=300, step=1, unit_of_measurement="s", mode=selector.NumberSelectorMode.BOX
                        )
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            description_placeholders={
                "fingerprint": protocol.key_fingerprint(bytes.fromhex(self.config_entry.data[CONF_KEY]))
            },
        )
