# SSCs Garage — context for Claude Code

Controls a Sysdes SSCs garage shutter over BLE. Two implementations of the same protocol:

- **Home Assistant integration** (`custom_components/sscs_garage`, domain `sscs_garage`): runs in
  HA; the M5 Atom S3 Lite is a plain ESPHome Bluetooth proxy (`proxy/atom-s3-proxy.yaml`).
  Install/switch-over/rollback: `docs/integration.md`.
- **ESPHome external component** (`components/sscs_garage`, `sscs-atom-s3.yaml`): runs on the
  Atom S3 (ESP32-S3, ESP-IDF). Kept as the fallback; don't delete it.

Protocol facts are in `docs/protocol.md`; only the `passcode = -1` path is verified on hardware.

## Rules

- Never print, log or commit the 32-byte BLE key, API key, OTA/Wi-Fi passwords, or derived tokens.
- Don't flash firmware, send commands to the shutter, or change Home Assistant config without
  the user asking. It is a garage door.
- `cc 00` means "command received", not "door finished moving".

## Design — integration

- `protocol.py`: pure Python port of `sscs_protocol.*` (no HA imports). Keep the two in step;
  both are tested against the vectors from `tests/reference.py`.
- `client.py` (`SSCSClient`): one session per command, same flow and timings as the component,
  via `bluetooth.async_ble_device_from_address(connectable=True)` + `establish_connection`.
  `send()` returns at once; results go to listeners. Sessions run under an asyncio lock; a newer
  command replaces one not yet written (`_written` is set before the "Sending" status, whose
  listeners may call `send()`), one arriving after the write runs next.
- Config flow: Bluetooth discovery or a list of nearby SSCs devices → key step (hex or R1/MS QR
  text). unique_id = BLE address. Key in entry data as hex; only the fingerprint is ever shown.
  Options: state binary_sensor (optional), `open_when` on/off, travel time.
- `cover.py`: assumed_state cover; state from the sensor, else assumed; reverts an assumed
  move whose command was never delivered. `sensor.py`: BLE status, key fingerprint.

## Design — ESPHome component

- `sscs_protocol.*`: pure functions, no ESPHome deps; host-tested in `tests/`.
- `sscs_garage.*`: its own `esp32_ble_client::BLEClientBase` (no `ble_client:` block, no MAC
  in YAML). It tracks `SSCs` adverts, auto-selects a lone shutter or takes one chosen in HA
  (stored in NVS), and only lets the base class connect while a command session waits.
  It owns the tracker's scan (`update_scanning_()`): on while searching (no shutter chosen),
  while a session is CONNECTING, or always when the `scan_switch` ("Always scan") is on. Phase
  timeouts use the scheduler. Runs entirely on the main loop; one session per command.
- Key and chosen shutter are stored in NVS; HA entities can set them but never read the key back.
- `cover/`: cover with runtime-selectable state source (0 = assumed, 1..N = binary sensors)
  plus a select entity that persists the choice.

## Commands

- `make -C tests` — C++ token test vs `tests/reference.py`.
- `wsl bash tools/test-ha.sh` — integration tests (`tests/ha`). HA doesn't run on Windows and
  2026.10 needs Python ≥ 3.14.2, so they run in WSL (`~/venvs/sscs-garage-ha`, made by the script
  with uv). Run WSL commands from a script file: PowerShell expands `$VARS` in inline `bash -c`.
- Firmware is built with the ESPHome dashboard on Home Assistant (target ESPHome 2026.10.x), or
  locally with `.\.venv\Scripts\esphome` from PowerShell (not Git Bash: ESP-IDF rejects MSYS).
- Validate `proxy/atom-s3-proxy.yaml` with `esphome config` only, against placeholder secrets.
