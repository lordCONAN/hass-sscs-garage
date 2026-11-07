# SSCs Garage

Controls a Sysdes "SSCs" Smart Garage shutter controller over Bluetooth LE and exposes
it to Home Assistant as a cover: as a Home Assistant integration (installable with HACS)
working through an ESPHome Bluetooth proxy, or as ESPHome firmware on an M5 Atom S3 Lite.

> **Coded with [Claude Code](https://claude.com/claude-code).** The code, tests and docs in
> this repository were written with Claude Code, Anthropic's AI coding agent, at the
> direction of the repository owner. The BLE protocol comes from earlier reverse-engineering
> of the shutter ([docs/history/](docs/history/)). Only the no-passcode (`passcode = -1`) path has been
> verified on real hardware, and the Home Assistant integration is new: try it with the door
> in view before relying on it.

This is a clean rewrite of the 2025 controller. The protocol is unchanged and
documented in [docs/protocol.md](docs/protocol.md); the original reverse-engineering
notes are in [docs/history/](docs/history/).

There are two ways to run it:

- **Home Assistant integration** (`custom_components/sscs_garage`): the protocol runs in HA and
  the Atom S3 is a plain ESPHome Bluetooth proxy (`proxy/atom-s3-proxy.yaml`). See
  [docs/integration.md](docs/integration.md) for install, switch-over and rollback.
- **ESPHome component** (`components/sscs_garage`, `sscs-atom-s3.yaml`): the protocol runs on
  the Atom S3. Kept as the fallback; the rest of this README describes it.

## What changed from the old firmware

| Old (`sscs_garage_s3.h`) | New (`components/sscs_garage`) |
| --- | --- |
| Arduino + NimBLE-Arduino 1.x, `includes:` header | Native ESPHome external component on ESP-IDF, uses ESPHome's `ble_client` |
| A FreeRTOS task per button press; overlapping presses collide | One state machine on the main loop; a newer command replaces one not yet sent |
| Shared flags across tasks, stack-captured callbacks | No tasks, no shared state, nothing outlives the session |
| Token logged at INFO | Keys and tokens never logged |
| Failures only in the log | `Garage BLE status` text sensor; cover reverts if a command was never delivered |
| Optimistic cover only | Selectable state source (assumed, Zigbee contact, future sensors) |
| MAC address compiled in | Shutter found by name, auto-selected or chosen from HA |
| Secrets inline in YAML | `!secret`, or enter the key from HA (hex or setup QR text) |

## Layout

```
custom_components/sscs_garage/   Home Assistant integration
  protocol.py                    pure protocol code (port of sscs_protocol.*)
  client.py                      BLE session (one per command)
  config_flow.py, cover.py, sensor.py
proxy/atom-s3-proxy.yaml         plain Bluetooth proxy for the Atom S3 (with the integration)
sscs-atom-s3.yaml                ESPHome component device config (copy to /config/esphome/)
secrets.example.yaml             keys the YAMLs expect
components/sscs_garage/          the ESPHome external component
  sscs_protocol.*                pure protocol code (unit-tested on PC)
  sscs_garage.*                  BLE session hub
  cover/                         cover + state-source select
tests/                           host test of the token code against a Python reference
tests/ha/                        integration tests (pytest, Linux/WSL)
tools/sscs_cli.py                Bleak command-line tool for bench testing from a PC
tools/test-ha.sh                 runs tests/ha in WSL/Linux with Python 3.14
docs/                            protocol, integration, Home Assistant setup, history
```

## Deploy (ESPHome component)

1. Copy `sscs-atom-s3.yaml` and the `components/` folder into `/config/esphome/` on Home Assistant
   (Samba: `\\homeassistant\config\esphome\`).
2. Add the keys from `secrets.example.yaml` to `/config/esphome/secrets.yaml`. Reuse the current
   API key and OTA password so HA keeps the existing device. `garage_ble_key` is optional if you
   will enter the key from HA instead (then remove the `key:` line).
3. Create the `binary_sensor.garage_shutter_state` helper in HA ([docs/home-assistant.md](docs/home-assistant.md)).
4. In the ESPHome dashboard, **Validate**, then **Install**. Keep the old YAML/header until the new build is proven.

It is the same physical device, so the new YAML replaces `sscs-atom-s3.yaml` there.

## Choosing the shutter, the state sensor, and entering the key

Both are done in the Home Assistant UI; see [docs/home-assistant.md](docs/home-assistant.md).

- **State:** the device reads one HA entity, `binary_sensor.garage_shutter_state`, which you
  create as a Template binary sensor helper. Its template decides which real sensor counts
  (Zigbee contact today, a BLE/camera sensor later, or a fallback chain), so changing it needs
  no firmware rebuild. The **Garage state source** select switches between that and `Assumed`.
  The cover stays `assumed_state` so HA keeps both buttons enabled, and `travel_time` is how
  long it shows opening/closing.
- **Shutter:** found by its `SSCs` Bluetooth name. The only one nearby is picked
  automatically; with several, choose from **Nearby shutters** via **Shutter address**.
  **Find shutter** starts the search again.
- **Key:** the **Shutter key** box accepts the 64-hex key or the text of the setup QR code
  (`R1…`). It is stored on the device, never sent back to HA, and identified by a fingerprint
  in **Shutter key status**. A key entered here wins over one in the YAML.

Local binary sensors (a reed switch, say) can still be added as extra `state_sources`.

## Testing

```
make -C tests             # C++ token code vs Python reference (needs g++ and OpenSSL headers)
wsl bash tools/test-ha.sh # integration: protocol.py vs the same vectors, session, flows, cover
```

Home Assistant doesn't run on Windows, so the integration tests run in WSL or on Linux
(Python 3.14, which HA 2026.10 needs); see [docs/integration.md](docs/integration.md#testing).

`tools/sscs_cli.py` sends one command from a PC (`pip install bleak`, key in `SSCS_KEY`).
It moves a real door: only run it when you can see the shutter.

## Safety

This operates a garage door. Don't run commands, flash firmware, or change config
unattended, and treat `cc 00` as "received", not "finished moving".

## License

[MIT](LICENSE). Not affiliated with or endorsed by Sysdes.
