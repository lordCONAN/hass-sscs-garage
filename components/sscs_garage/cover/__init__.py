import esphome.codegen as cg
from esphome.components import binary_sensor, cover, select
import esphome.config_validation as cv
from esphome.const import CONF_ID, CONF_NAME, CONF_RESTORE_VALUE, CONF_SENSOR

from .. import CONF_SSCS_GARAGE_ID, SSCSGarage, sscs_garage_ns

DEPENDENCIES = ["sscs_garage"]
AUTO_LOAD = ["select", "binary_sensor"]

CONF_TRAVEL_TIME = "travel_time"
CONF_STATE_SOURCES = "state_sources"
CONF_OPEN_WHEN = "open_when"
CONF_DEFAULT_STATE_SOURCE = "default_state_source"
CONF_STATE_SOURCE_SELECT = "state_source_select"

ASSUMED = "Assumed"

SSCSCover = sscs_garage_ns.class_("SSCSCover", cover.Cover, cg.Component)
StateSourceSelect = sscs_garage_ns.class_(
    "StateSourceSelect", select.Select, cg.Component
)

STATE_SOURCE_SCHEMA = cv.Schema(
    {
        cv.Required(CONF_NAME): cv.string_strict,
        cv.Required(CONF_SENSOR): cv.use_id(binary_sensor.BinarySensor),
        # Which sensor state means "not closed". HA door/garage contacts report ON when open.
        cv.Optional(CONF_OPEN_WHEN, default="ON"): cv.one_of("ON", "OFF", upper=True),
    }
)


def _validate_sources(config):
    names = [ASSUMED] + [s[CONF_NAME] for s in config.get(CONF_STATE_SOURCES, [])]
    if len(set(n.lower() for n in names)) != len(names):
        raise cv.Invalid(
            f"state source names must be unique and not '{ASSUMED}'",
            path=[CONF_STATE_SOURCES],
        )
    if (default := config.get(CONF_DEFAULT_STATE_SOURCE)) is not None:
        if default not in names:
            raise cv.Invalid(
                f"must be one of: {', '.join(names)}",
                path=[CONF_DEFAULT_STATE_SOURCE],
            )
    return config


CONFIG_SCHEMA = cv.All(
    cover.cover_schema(SSCSCover, device_class="garage")
    .extend(
        {
            cv.GenerateID(CONF_SSCS_GARAGE_ID): cv.use_id(SSCSGarage),
            cv.Optional(
                CONF_TRAVEL_TIME, default="30s"
            ): cv.positive_time_period_milliseconds,
            cv.Optional(CONF_STATE_SOURCES, default=[]): cv.ensure_list(
                STATE_SOURCE_SCHEMA
            ),
            # Defaults to the first state source, or Assumed when there are none.
            cv.Optional(CONF_DEFAULT_STATE_SOURCE): cv.string_strict,
            cv.Optional(CONF_STATE_SOURCE_SELECT): select.select_schema(
                StateSourceSelect,
                entity_category="config",
                icon="mdi:garage-alert",
            ).extend(
                {
                    cv.Optional(CONF_RESTORE_VALUE, default=True): cv.boolean,
                }
            )
            .extend(cv.COMPONENT_SCHEMA),
        }
    )
    .extend(cv.COMPONENT_SCHEMA),
    _validate_sources,
)


async def to_code(config):
    var = await cover.new_cover(config)
    await cg.register_component(var, config)

    hub = await cg.get_variable(config[CONF_SSCS_GARAGE_ID])
    cg.add(var.set_hub(hub))
    cg.add(var.set_travel_time(config[CONF_TRAVEL_TIME]))

    sources = config[CONF_STATE_SOURCES]
    names = [ASSUMED] + [s[CONF_NAME] for s in sources]
    for src in sources:
        sens = await cg.get_variable(src[CONF_SENSOR])
        cg.add(var.add_state_source(sens, src[CONF_OPEN_WHEN] == "ON"))

    default = config.get(CONF_DEFAULT_STATE_SOURCE)
    default_index = names.index(default) if default else (1 if sources else 0)
    cg.add(var.set_default_source(default_index))

    if sel_config := config.get(CONF_STATE_SOURCE_SELECT):
        sel = await select.new_select(sel_config, options=names)
        await cg.register_component(sel, sel_config)
        cg.add(sel.set_cover(var))
        cg.add(sel.set_restore_value(sel_config[CONF_RESTORE_VALUE]))
        cg.add(var.set_source_select(sel))
