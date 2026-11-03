# SSCs Garage

ESPHome firmware that drives a Sysdes "SSCs" Smart Garage shutter controller over
Bluetooth LE, from an M5 Atom S3 Lite, and exposes it to Home Assistant as a cover.

This is a clean rewrite of the 2025 controller. The protocol is unchanged and
documented in [docs/protocol.md](docs/protocol.md); the original reverse-engineering
notes are in [docs/history/](docs/history/).

## What changed from the old firmware

| Old (`sscs_garage_s3.h`) | New (`components/sscs_garage`) |
| --- | --- |
| Arduino + NimBLE-Arduino 1.x, `includes:` header | Native ESPHome external component on ESP-IDF, uses ESPHome's `ble_client` |
| A FreeRTOS task per button press; overlapping presses collide | One state machine on the main loop; a newer command replaces one not yet sent |
| Shared flags across tasks, stack-captured callbacks | No tasks, no shared state, nothing outlives the session |
| Token logged at INFO | Keys and tokens never logged |
| Failures only in the log | `Garage BLE status` text sensor; cover reverts if a command was never delivered |
| Optimistic cover only | Selectable state source (assumed, Zigbee contact, future sensors) |
| Secrets inline in YAML | `!secret` |

## Layout

```
sscs-atom-s3.yaml          device config (copy to /config/esphome/)
secrets.example.yaml       keys the YAML expects
components/sscs_garage/    the external component
  sscs_protocol.*          pure protocol code (unit-tested on PC)
  sscs_garage.*            BLE session hub
  cover/                   cover + state-source select
tests/                     host test of the token code against a Python reference
tools/sscs_cli.py          Bleak command-line tool for bench testing from a PC
docs/                      protocol and history
```

## Deploy

1. Copy `sscs-atom-s3.yaml` and the `components/` folder into `/config/esphome/` on Home Assistant
   (Samba: `\\homeassistant\config\esphome\`).
2. Add the keys from `secrets.example.yaml` to `/config/esphome/secrets.yaml`. Reuse the current
   API key and OTA password so HA keeps the existing device.
3. In the ESPHome dashboard, **Validate**, then **Install**. Keep the old YAML/header until the new build is proven.

It is the same physical device, so the new YAML replaces `sscs-atom-s3.yaml` there.

## Cover state sources

The cover's open/closed state comes from one selectable source:

- **Assumed**: whatever the last delivered command implies.
- **Any binary sensor** listed under `state_sources`: today the Zigbee contact
  (`binary_sensor.garage_shutter_sensor_contact`, imported with `platform: homeassistant`).

Pick the active one with the **Garage state source** select in HA; the choice survives reboots.
To add the planned BLE/camera sensor later, import its HA entity as another
`binary_sensor` and add it to `state_sources` — no C++ changes.

`open_when` sets polarity (HA door/garage contacts are `ON` when open). A contact at one
end only says "closed" vs "not closed", so the cover stays `assumed_state` and HA keeps
both buttons enabled. `travel_time` is how long the cover shows opening/closing.

## Testing

```
make -C tests          # token code vs Python reference (needs g++ and OpenSSL headers)
```

`tools/sscs_cli.py` sends one command from a PC (`pip install bleak`, key in `SSCS_KEY`).
It moves a real door: only run it when you can see the shutter.

## Safety

This operates a garage door. Don't run commands, flash firmware, or change config
unattended, and treat `cc 00` as "received", not "finished moving".
