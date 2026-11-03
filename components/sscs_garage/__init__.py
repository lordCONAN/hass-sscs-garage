"""SSCs (Sysdes Smart Garage) BLE shutter controller hub."""

import re

from esphome import automation
import esphome.codegen as cg
from esphome.components import (
    button,
    esp32_ble,
    esp32_ble_client,
    esp32_ble_tracker,
    text,
    text_sensor,
)
from esphome.components.esp32_ble import BTLoggers
import esphome.config_validation as cv
from esphome.const import CONF_ID, CONF_MAC_ADDRESS, CONF_STATUS

CODEOWNERS = ["@anthony"]
DEPENDENCIES = ["esp32_ble_tracker"]
AUTO_LOAD = ["esp32_ble_client", "button", "text", "text_sensor"]
MULTI_CONF = True

CONF_SSCS_GARAGE_ID = "sscs_garage_id"
CONF_KEY = "key"
CONF_PASSCODE = "passcode"
CONF_CONNECT_TIMEOUT = "connect_timeout"
CONF_CHALLENGE_TIMEOUT = "challenge_timeout"
CONF_CHALLENGE_NUDGE = "challenge_nudge"
CONF_AUTH_SETTLE = "auth_settle"
CONF_ACK_TIMEOUT = "ack_timeout"
CONF_COMMAND = "command"
CONF_KEY_INPUT = "key_input"
CONF_KEY_STATUS = "key_status"
CONF_NAME_PREFIXES = "name_prefixes"
CONF_AUTO_SELECT_WINDOW = "auto_select_window"
CONF_DEVICE_INPUT = "device_input"
CONF_FIND_BUTTON = "find_button"
CONF_NEARBY = "nearby"

sscs_garage_ns = cg.esphome_ns.namespace("sscs_garage")
SSCSGarage = sscs_garage_ns.class_("SSCSGarage", esp32_ble_client.BLEClientBase)
Command = sscs_garage_ns.namespace("protocol").enum("Command", is_class=True)
COMMANDS = {
    "up": Command.UP,
    "open": Command.UP,
    "stop": Command.STOP,
    "down": Command.DOWN,
    "close": Command.DOWN,
}
SendAction = sscs_garage_ns.class_("SendAction", automation.Action)
KeyText = sscs_garage_ns.class_("KeyText", text.Text, cg.Component)
DeviceText = sscs_garage_ns.class_("DeviceText", text.Text, cg.Component)
FindButton = sscs_garage_ns.class_("FindButton", button.Button)


BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _base58_decode(text: str) -> bytes | None:
    n = 0
    for c in text:
        if (v := BASE58_ALPHABET.find(c)) < 0:
            return None
        n = n * 58 + v
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\0" * (len(text) - len(text.lstrip("1"))) + raw


def validate_key(value):
    """64 hex characters, or the setup QR text ("R1"/"MS" + Base58). Never echoes the value."""
    value = cv.string_strict(value).strip()
    if value[:2] in ("R1", "MS"):
        key = _base58_decode(value[2:])
        if key is None or len(key) != 32:
            raise cv.Invalid("key QR text did not decode to a 32-byte key")
        return key.hex()
    if value[:1] == "0" and not re.fullmatch(r"[0-9a-fA-F:\-\s]+", value):
        raise cv.Invalid("shared (encrypted) QR codes are not supported")
    if value[:2] in ("R2", "R3", "R4"):
        raise cv.Invalid("QR codes for remote slots 2-4 are not supported yet")
    cleaned = re.sub(r"[\s:\-]", "", value).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", cleaned):
        raise cv.Invalid("key must be 64 hex characters or the setup QR text")
    return cleaned


CONFIG_SCHEMA = cv.All(
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(SSCSGarage),
            # Optional when key_input is used: a key entered from HA overrides this one.
            cv.Optional(CONF_KEY): cv.sensitive(validate_key),
            cv.Optional(CONF_KEY_INPUT): text.text_schema(
                KeyText,
                icon="mdi:key-variant",
                entity_category="config",
                mode="PASSWORD",
            ).extend(cv.COMPONENT_SCHEMA),
            cv.Optional(CONF_KEY_STATUS): text_sensor.text_sensor_schema(
                icon="mdi:key-chain",
                entity_category="diagnostic",
            ),
            # Optional: without it the shutter is found by name and can be chosen from HA.
            cv.Optional(CONF_MAC_ADDRESS): cv.mac_address,
            cv.Optional(CONF_NAME_PREFIXES, default=["SSCs", "SSCg"]): cv.All(
                cv.ensure_list(cv.string_strict), cv.Length(min=1, max=4)
            ),
            cv.Optional(
                CONF_AUTO_SELECT_WINDOW, default="10s"
            ): cv.positive_time_period_milliseconds,
            cv.Optional(CONF_DEVICE_INPUT): text.text_schema(
                DeviceText,
                icon="mdi:bluetooth-settings",
                entity_category="config",
                mode="TEXT",
            ).extend(cv.COMPONENT_SCHEMA),
            cv.Optional(CONF_FIND_BUTTON): button.button_schema(
                FindButton,
                icon="mdi:bluetooth-audio",
                entity_category="config",
            ),
            cv.Optional(CONF_NEARBY): text_sensor.text_sensor_schema(
                icon="mdi:access-point",
                entity_category="diagnostic",
            ),
            cv.Optional(CONF_PASSCODE, default=-1): cv.int_range(
                min=-1, max=0xFFFFFFFF
            ),
            cv.Optional(
                CONF_CONNECT_TIMEOUT, default="20s"
            ): cv.positive_time_period_milliseconds,
            cv.Optional(
                CONF_CHALLENGE_TIMEOUT, default="5s"
            ): cv.positive_time_period_milliseconds,
            cv.Optional(
                CONF_CHALLENGE_NUDGE, default="1200ms"
            ): cv.positive_time_period_milliseconds,
            cv.Optional(
                CONF_AUTH_SETTLE, default="300ms"
            ): cv.positive_time_period_milliseconds,
            cv.Optional(
                CONF_ACK_TIMEOUT, default="3s"
            ): cv.positive_time_period_milliseconds,
            cv.Optional(CONF_STATUS): text_sensor.text_sensor_schema(
                icon="mdi:bluetooth-transfer",
                entity_category="diagnostic",
            ),
        }
    )
    .extend(esp32_ble_tracker.ESP_BLE_DEVICE_SCHEMA)
    .extend(cv.COMPONENT_SCHEMA),
    cv.has_at_least_one_key(CONF_KEY, CONF_KEY_INPUT),
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await esp32_ble_tracker.register_client(var, config)
    esp32_ble.register_bt_logger(BTLoggers.GATT, BTLoggers.SMP)

    if (mac := config.get(CONF_MAC_ADDRESS)) is not None:
        cg.add(var.set_config_address(mac.as_hex))
    for prefix in config[CONF_NAME_PREFIXES]:
        cg.add(var.add_name_prefix(prefix))
    cg.add(var.set_auto_select_window(config[CONF_AUTO_SELECT_WINDOW]))

    if (key_hex := config.get(CONF_KEY)) is not None:
        cg.add(var.set_key(cg.ArrayInitializer(*bytes.fromhex(key_hex))))
    cg.add(var.set_passcode(config[CONF_PASSCODE]))
    cg.add(var.set_connect_timeout(config[CONF_CONNECT_TIMEOUT]))
    cg.add(var.set_challenge_timeout(config[CONF_CHALLENGE_TIMEOUT]))
    cg.add(var.set_challenge_nudge(config[CONF_CHALLENGE_NUDGE]))
    cg.add(var.set_auth_settle(config[CONF_AUTH_SETTLE]))
    cg.add(var.set_ack_timeout(config[CONF_ACK_TIMEOUT]))

    if status_config := config.get(CONF_STATUS):
        sens = await text_sensor.new_text_sensor(status_config)
        cg.add(var.set_status_text_sensor(sens))
    if key_status_config := config.get(CONF_KEY_STATUS):
        sens = await text_sensor.new_text_sensor(key_status_config)
        cg.add(var.set_key_status_text_sensor(sens))
    if key_input_config := config.get(CONF_KEY_INPUT):
        # Hex (64) or setup-QR text (~46); allow separators and whitespace.
        key_text = await text.new_text(key_input_config, min_length=0, max_length=128)
        await cg.register_component(key_text, key_input_config)
        await cg.register_parented(key_text, var)
    if nearby_config := config.get(CONF_NEARBY):
        sens = await text_sensor.new_text_sensor(nearby_config)
        cg.add(var.set_nearby_text_sensor(sens))
    if device_input_config := config.get(CONF_DEVICE_INPUT):
        device_text = await text.new_text(device_input_config, min_length=0, max_length=32)
        await cg.register_component(device_text, device_input_config)
        await cg.register_parented(device_text, var)
        cg.add(var.set_device_text(device_text))
    if find_config := config.get(CONF_FIND_BUTTON):
        find = await button.new_button(find_config)
        await cg.register_parented(find, var)


@automation.register_action(
    "sscs_garage.send",
    SendAction,
    cv.Schema(
        {
            cv.GenerateID(): cv.use_id(SSCSGarage),
            cv.Required(CONF_COMMAND): cv.enum(COMMANDS, lower=True),
        }
    ),
    synchronous=True,
)
async def send_action_to_code(config, action_id, template_arg, args):
    var = cg.new_Pvariable(action_id, template_arg)
    await cg.register_parented(var, config[CONF_ID])
    cg.add(var.set_command(config[CONF_COMMAND]))
    return var
