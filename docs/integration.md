# Home Assistant integration (`custom_components/sscs_garage`)

The shutter protocol runs inside Home Assistant, and the Atom S3 becomes a plain
ESPHome Bluetooth proxy ([proxy/atom-s3-proxy.yaml](../proxy/atom-s3-proxy.yaml)).
HA connects to the shutter through the proxy, or through any other Bluetooth adapter
or proxy in range.

The ESPHome component (`components/sscs_garage`, `sscs-atom-s3.yaml`) stays in the
repository as the fallback; see [Rollback](#rollback).

## What it does

The session is the same as the ESPHome component's ([protocol.md](protocol.md)): connect,
subscribe DATA then CMD, answer the 20-byte challenge (sending `00 00` if none arrives after
1.2 s), wait up to 300 ms for `cb 00`, write the command, then wait 3 s for `cc 00`. There
is one session per command. A newer command replaces one that hasn't been written yet; a
command arriving after the write is sent in a new session afterwards. `cc 00` means the
controller **received** the command, not that the door finished moving.

| Entity | What it is |
| --- | --- |
| **Garage Shutter** (cover, `cover.garage_shutter`) | Open / close / stop. Always `assumed_state`, so both buttons stay enabled. |
| **Door** (binary sensor, garage door) | Open/closed as the state sensor reports it, whatever the select says. Unknown without a state sensor or while it reports nothing. |
| **State source** (select, config) | `Assumed` or `State sensor`: what the cover follows. Only present when a state sensor is set; remembered across restarts. |
| **Active state source** (diagnostic) | What the cover is following now: the sensor's name, `Assumed`, or `Assumed (sensor unavailable)`. |
| **BLE status** (diagnostic) | `Connecting`, `Authenticating`, `Sending`, then the result, e.g. `up: acknowledged`, `down: shutter not found`. |
| **Key fingerprint** (diagnostic) | First 4 bytes of SHA-256 of the key: the same fingerprint the ESPHome firmware showed in **Shutter key status**. |

Entity IDs follow the name you give the shutter during setup (default **Garage Shutter**).

Open/closed comes from the **state sensor** chosen in the options (optional), while the
**State source** select is on `State sensor` (the default). On `Assumed`, or while the sensor
is unknown or unavailable, the state is assumed from the last command. If a command
never reached the shutter (not found, no challenge, write rejected, disconnected early), an
assumed opening/closing is undone. **Travel time** (default 30 s) is how long the cover
shows opening/closing.

The key is stored in the config entry (`/config/.storage/core.config_entries`, like other
integrations' credentials). It never appears in logs, entity states or the UI after setup;
use the fingerprint to tell keys apart.

## Install

With [HACS](https://hacs.xyz):

1. HACS → ⋮ → **Custom repositories** → repository
   `https://github.com/lordCONAN/hass-sscs-garage`, type **Integration** → **Add**.
2. Open **SSCs Garage Shutter** in HACS → **Download**.
3. Restart Home Assistant.

HACS installs only `custom_components/sscs_garage/` and offers an update when a new
release is published (see [Releasing](#releasing)). Without HACS, copy that folder to `/config/custom_components/sscs_garage/` (Samba:
`\\homeassistant\config\custom_components\`) and restart.

It needs no extra Python packages: it uses HA's own Bluetooth stack (`bleak`,
`bleak-retry-connector`).

## Switch-over

Do this when you can see the door, with the phone app closed (the controller accepts one
connection at a time).

1. **Install the integration** as above. If HA already hears the shutter (local adapter), it
   may offer it under **Discovered**; leave that until step 4.
2. **Prepare the proxy firmware.** Copy `proxy/atom-s3-proxy.yaml` to `/config/esphome/` and
   add `ble_proxy_api_key` (a new one: the ESPHome dashboard or
   `openssl rand -base64 32`), `ble_proxy_ota_password` and `ble_proxy_fallback_ap_password`
   to `/config/esphome/secrets.yaml`. `wifi_ssid`/`wifi_password` are shared.
3. **Flash the Atom S3 with it over USB.** It is a new device name with a new OTA password, so
   an OTA install from the dashboard won't find or authenticate to the old firmware. Either
   ESPHome dashboard → **Install → Manual download → Factory format**, then flash it with
   <https://web.esphome.io>, or from this repo with the Atom on USB:
   `esphome upload proxy/atom-s3-proxy.yaml --device COMx` (needs the secrets next to the YAML).
   The partition table (8 MB) is the same as `sscs-atom-s3.yaml`'s.
   (OTA does work if you set `ble_proxy_ota_password` to the current `sscs_ota_password`:
   `esphome upload proxy/atom-s3-proxy.yaml --device <its IP>`.)
4. **Adopt the proxy.** Settings → Devices & services: ESPHome discovers
   **atom-s3-ble-proxy**; configure it with `ble_proxy_api_key`. The old **sscs_atom_s3**
   device goes unavailable.
5. **Add the shutter.** Settings → Devices & services → **Discovered: SSCs Garage Shutter** →
   Configure (or **Add integration → SSCs Garage Shutter**, which lists the SSCs devices HA
   hears). Enter the key as 64 hex characters or the text of the setup QR code (`R1…`), and a
   name. Check that **Key fingerprint** matches the fingerprint the old firmware showed.
6. **Options** (the integration's **Configure** button): State sensor
   `binary_sensor.garage_shutter_state` (the template helper from
   [home-assistant.md](home-assistant.md) still works), open when **On**, travel time 30 s.
7. **Retire the old device.** Settings → Devices → **sscs_atom_s3** (ESPHome) → Delete. If
   automations or dashboards use `cover.sscs_atom_s3_garage_shutter`, either update them to
   `cover.garage_shutter` or, after deleting the old device, rename the new cover's entity ID
   to the old one.
8. **Test with the door in view.** Press **Stop** first; **BLE status** should show
   `stop: acknowledged`. Then open and close.

## Rollback

1. Settings → Devices & services → SSCs Garage Shutter → ⋮ → **Disable** (keeps its
   settings), so HA doesn't try to connect while the firmware does.
2. Flash `sscs-atom-s3.yaml` back over USB, as in switch-over step 3. OTA won't authenticate,
   because the proxy has a different OTA password, unless you made them equal.
3. ESPHome rediscovers **sscs_atom_s3**; configure it with `sscs_api_key` if you deleted it.
   Rename the cover's entity ID back if you moved it.
4. The firmware keeps its key and chosen shutter in NVS, which a USB flash of the factory
   image normally leaves alone. If **Shutter key status** says `Not set`, enter the key
   again (or rely on `garage_ble_key` in the YAML).

To switch forward again, flash the proxy and re-enable the integration.

## Testing

Home Assistant doesn't run on Windows (it needs `fcntl` and Linux-only Bluetooth packages),
and 2026.10 needs Python ≥ 3.14.2. Run the tests in WSL or on Linux:

```
wsl bash tools/test-ha.sh          # or: bash tools/test-ha.sh   (extra args go to pytest)
```

The first run creates `~/venvs/sscs-garage-ha` with `uv` and Python 3.14, installs
`pytest-homeassistant-custom-component` (which pins the HA version) and the `bluetooth`/`usb`
component requirements. Tests in `tests/ha/`:

- `test_protocol.py`: `protocol.py` against the vectors `tests/reference.py` generates for the
  C++ test (tokens, QR keys, rejection reasons, fingerprint).
- `test_session.py`: the session state machine against a scripted fake `BleakClient`
  (nudge, timeouts, rejected writes, disconnects, replacing and queueing commands, one
  session at a time, no key in the logs).
- `test_config_flow.py`, `test_cover.py`: discovery and manual setup, key errors, options,
  cover state from the sensor or assumed, revert on failure.

## Releasing

Versions are `MAJOR.MINOR.PATCH` ([semver](https://semver.org)); HACS shows the latest GitHub
release's tag. To release:

1. Set `"version"` in `custom_components/sscs_garage/manifest.json` (e.g. `0.2.0`) and commit.
2. `git tag v0.2.0 && git push origin main v0.2.0`.

The **Release** workflow (`.github/workflows/release.yml`) checks the tag matches the manifest
and publishes the release with notes generated from the commits.
