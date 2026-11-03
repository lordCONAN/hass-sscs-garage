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
- `sscs_garage.*`: `BLEClientNode` state machine. Runs entirely on the main loop (esp32_ble
  queues GATT events there). `ble_client` is kept disabled between commands and enabled to
  connect on the next advertisement; one session per command.
- `cover/`: cover with runtime-selectable state source (0 = assumed, 1..N = binary sensors)
  plus a select entity that persists the choice.

## Commands

- `make -C tests` — token test vs `tests/reference.py`.
- Firmware is built with the ESPHome dashboard on Home Assistant (target ESPHome 2026.10.x).
