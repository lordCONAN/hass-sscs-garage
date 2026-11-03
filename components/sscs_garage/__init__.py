"""SSCs (Sysdes Smart Garage) BLE shutter controller hub."""

import re

from esphome import automation
import esphome.codegen as cg
from esphome.components import ble_client, text_sensor
import esphome.config_validation as cv
from esphome.const import CONF_ID, CONF_STATUS

CODEOWNERS = ["@anthony"]
DEPENDENCIES = ["ble_client"]
AUTO_LOAD = ["text_sensor"]
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

sscs_garage_ns = cg.esphome_ns.namespace("sscs_garage")
SSCSGarage = sscs_garage_ns.class_(
    "SSCSGarage", cg.Component, ble_client.BLEClientNode
)
Command = sscs_garage_ns.namespace("protocol").enum("Command", is_class=True)
COMMANDS = {
    "up": Command.UP,
    "open": Command.UP,
    "stop": Command.STOP,
    "down": Command.DOWN,
    "close": Command.DOWN,
}
SendAction = sscs_garage_ns.class_("SendAction", automation.Action)


def validate_key(value):
    value = cv.string_strict(value)
    cleaned = re.sub(r"[\s:\-]", "", value).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", cleaned):
        # Never echo the value back: it is a secret.
        raise cv.Invalid("key must be 64 hex characters (32 bytes)")
    return cleaned


CONFIG_SCHEMA = (
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(SSCSGarage),
            cv.Required(CONF_KEY): cv.sensitive(validate_key),
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
    .extend(ble_client.BLE_CLIENT_SCHEMA)
    .extend(cv.COMPONENT_SCHEMA)
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await ble_client.register_ble_node(var, config)

    key = bytes.fromhex(config[CONF_KEY])
    cg.add(var.set_key(cg.ArrayInitializer(*key)))
    cg.add(var.set_passcode(config[CONF_PASSCODE]))
    cg.add(var.set_connect_timeout(config[CONF_CONNECT_TIMEOUT]))
    cg.add(var.set_challenge_timeout(config[CONF_CHALLENGE_TIMEOUT]))
    cg.add(var.set_challenge_nudge(config[CONF_CHALLENGE_NUDGE]))
    cg.add(var.set_auth_settle(config[CONF_AUTH_SETTLE]))
    cg.add(var.set_ack_timeout(config[CONF_ACK_TIMEOUT]))

    if status_config := config.get(CONF_STATUS):
        sens = await text_sensor.new_text_sensor(status_config)
        cg.add(var.set_status_text_sensor(sens))


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
