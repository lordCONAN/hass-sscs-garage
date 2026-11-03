# SSCs Garage — context for Claude Code

ESPHome external component (`components/sscs_garage`) that controls a Sysdes SSCs
garage shutter over BLE from an M5 Atom S3 Lite (ESP32-S3, ESP-IDF). Protocol facts
are in `docs/protocol.md`; only the `passcode = -1` path is verified on hardware.

## Rules

- Never print, log or commit the 32-byte BLE key, API key, OTA/Wi-Fi passwords, or derived tokens.
- Don't flash firmware, send commands to the shutter, or change Home Assistant config without
  the user asking. It is a garage door.
- `cc 00` means "command received", not "door finished moving".

## Design

- `sscs_protocol.*`: pure functions, no ESPHome deps; host-tested in `tests/`.
- `sscs_garage.*`: its own `esp32_ble_client::BLEClientBase` (no `ble_client:` block, no MAC
  in YAML). It tracks `SSCs` adverts, auto-selects a lone shutter or takes one chosen in HA
  (stored in NVS), and only lets the base class connect while a command session waits. Phase
  timeouts use the scheduler. Runs entirely on the main loop; one session per command.
- Key and chosen shutter are stored in NVS; HA entities can set them but never read the key back.
- `cover/`: cover with runtime-selectable state source (0 = assumed, 1..N = binary sensors)
  plus a select entity that persists the choice.

## Commands

- `make -C tests` — token test vs `tests/reference.py`.
- Firmware is built with the ESPHome dashboard on Home Assistant (target ESPHome 2026.10.x).
