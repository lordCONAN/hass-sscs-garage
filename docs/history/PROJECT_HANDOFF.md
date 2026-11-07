# SSCs Garage Shutter — Technical Project History

**Prepared:** 2026-11-03  
**Source:** Prior troubleshooting and development discussions, mainly August–September 2025.  
**Scope:** Work already performed, test results, implementation details, and known limitations. This is a historical summary, **not the current installed source code**.

## 1. Result established

We reverse-engineered the Bluetooth Low Energy (BLE) interface of a Sysdes / Smart Garage shutter controller and built a working standalone controller with **ESPHome on an M5 Atom S3 Lite (ESP32-S3)**.

The user confirmed on **2025-09-01**: **“The esphome is working perfectly.”**

The ESP32 communicates directly with the shutter over BLE and exposes an ESPHome cover in Home Assistant. This replaced earlier experiments using a Windows BLE script and a custom Home Assistant integration.

**Confirmed ESPHome cover entity:** `cover.sscs_atom_s3_garage_shutter`.

## 2. Devices and software used

| Component | Recorded details |
| --- | --- |
| Garage peripheral | Sysdes Smart Garage; advertisement name/prefix `SSCs` |
| Observed BLE address | (redacted; differs per unit) |
| ESP controller | M5 Atom S3 Lite (ESP32-S3) |
| Firmware | ESPHome, Arduino ESP32 framework |
| BLE library | NimBLE-Arduino 1.x (`^1.4.2` in historical YAML) |
| Crypto | mbedTLS SHA-256 |
| Home Assistant host | (details omitted) |
| Separate state sensor | Zigbee contact via Zigbee2MQTT |
| Contact entity | `binary_sensor.garage_shutter_sensor_contact` |

The exact firmware, ESPHome and library versions installed today have not been supplied in this conversation.

## 3. BLE protocol established from reverse engineering and testing

We inspected the Android app (`com.sysdes.smagara`), including class `SDSGbleCtrl`, related GATT methods, and references to native routines `sgMakeConnChkCode()` and `sscHARF128()`.

### GATT UUIDs

```text
Service: 00003333-0000-1000-8000-00805f9b34fb
CMD:     00009001-0000-1000-8000-00805f9b34fb
DATA:    00009002-0000-1000-8000-00805f9b34fb
```

### BLE commands

```text
OPEN / UP:    c9 00 00 00
STOP:         c9 00 00 01
CLOSE / DOWN: c9 00 00 02
Command ACK:  cc 00
Connected/status notification observed: cb 00 ...
```

### Authenticated command sequence (tested with passcode `-1`)

1. Discover the shutter's BLE service and CMD/DATA characteristics.
2. Subscribe to notifications; the **20-byte initial FIRST packet arrives on CMD**, not DATA. The first 16 bytes contain a fresh random challenge; an example four-byte tail is `12 10 09 00`.
3. Compute the 20-byte authentication token using the controller's 32-byte secret key and the received challenge.
4. Write the token to CMD with response.
5. Observe connected/status indication (`cb 00 ...` in captures), send the desired `c9 00 00 xx` command to CMD with response.
6. Observe CMD notification `cc 00` for command acknowledgment; disconnect.

For the tested `passcode = -1` path, the reconstructed token algorithm is:

```python
from hashlib import sha256

def token20(key32: bytes, challenge16: bytes) -> bytes:
    assert len(key32) == 32 and len(challenge16) == 16
    digest = sha256(key32 + challenge16).digest()
    core16 = bytes(digest[i] ^ digest[i + 16] for i in range(16))
    return core16 + b"\xff\xff\xff\xff"
```

**Important distinction:** The `sscHARF128` operation XORs the two 16-byte halves of the 32-byte result **without reversing them** when given a destination buffer. The formula above was supported by working physical tests. The body of native `sgMakeConnChkCode` was not directly available in the discussion, so its complete behavior should not be inferred beyond the tested path. Other passcode modes were inspected at a high level but **not validated on hardware**.

## 4. Windows proof of concept

A Python script using **Bleak** successfully authenticated and commanded the actual shutter. One recorded successful interaction contained:

```text
Connected: True
FIRST: 1c5babd45efcc8cfcdee65565840983e12100900
CHAL16: 1c5babd45efcc8cfcdee65565840983e
FLAGS: 0x00091012
Sent up: c9000000
Command ACK: cc00
```

The test physically operated the shutter.

Two caveats from Windows testing:

- Windows WinRT reconnects sometimes failed with `Could not get GATT services: Unreachable`.
- One debugging script accidentally re-consumed the FIRST notification while printing an `OK20` value. That printed value was **not** independent proof of authentication success; the actual shutter action and `cc00` ACK were better evidence.

A historical Windows test script was referred to, but its actual contents are **not included** with this handoff.

## 5. Home Assistant integration experiments

We first attempted a native custom integration (`custom_components/sscs_garage`) communicating through Home Assistant Bluetooth / an ESPHome Bluetooth proxy. The entity initially showed unavailable, and later the integration could not resolve the BLE device. Various discovery and callback issues were encountered. The integration did **not** become the confirmed working solution.

The missing resolution in that custom integration did **not** prove that Home Assistant's Bluetooth proxy infrastructure cannot initiate BLE GATT connections; that earlier conclusion was too broad.

We then moved control to an **M5 Atom S3 Lite**, which performs the BLE connection locally and presents a native ESPHome cover to Home Assistant.

## 6. ESPHome implementation that worked

Historical source structure:

```text
/config/esphome/
  sscs_atom_s3.yaml
  custom/
    sscs_garage_s3.h
```

The working approach used ESPHome's supported C++ `esphome: includes:` facility, rather than the removed `custom_component:` platform.

The historic YAML configured:

- `esp32: board: esp32-s3-devkitc-1`, with Arduino framework.
- The included C++ header `custom/sscs_garage_s3.h`.
- NimBLE-Arduino through PlatformIO library dependencies.
- Build configuration for device name prefix `SSCs`, passcode `-1`, and a private hexadecimal BLE key.
- An ESPHome **template cover** called `Garage Shutter`, with open, close and stop actions invoking `sg_send_up()`, `sg_send_down()`, and `sg_send_stop()`.
- Separate test buttons were also included during development.

The C++ implementation used a dedicated FreeRTOS task for BLE work, allowing Home Assistant / ESPHome command handling to return without waiting for a full BLE session. The routine scanned for the peripheral, connected, subscribed to notifications, waited for FIRST, performed SHA-256/XOR authentication, wrote the command, listened for ACK, and disconnected.

Some versions of the header included a fallback single-byte `INI=0` write if no FIRST notification arrived after subscribing. Whether that fallback remains in the currently installed working header is unknown.

### Compile errors resolved during development

- mbedTLS function names on the installed toolchain were `mbedtls_sha256_starts`, `mbedtls_sha256_update`, `mbedtls_sha256_finish`, not the `_ret` variants.
- `NimBLEClient::connect` could accept an appropriate `NimBLEAddress` or pointer; passing a `NimBLEAdvertisedDevice` **by value** failed compilation.
- A FreeRTOS worker task argument struct needed to be declared at suitable scope; a prior local type caused a compiler error (`'P' does not name a type`).

The last replacement header provided during that debugging fixed these compile errors. The subsequent user report confirmed the ESPHome solution worked; **we do not have a copy of the final flashed header** to verify every line.

### Code-review considerations apparent from historical versions

These are potential issues identified in earlier revisions, **not confirmed bugs in the currently installed version**:

- Simultaneous cover/button requests might start overlapping BLE tasks without a guard.
- Shared notification flags / scan results might be accessed across tasks without synchronization.
- A scan callback allocated on the stack could leave a dangling registered pointer after scanning.
- Notification lambdas captured stack-local variables; callback teardown should be checked for races.
- `cc00` indicates command receipt/acknowledgment, not necessarily completed shutter travel.
- BLE connection failure, FIRST timeout, token failure, and ACK timeout need clearly separated error handling.
- Porting to a different NimBLE or ESPHome version may change API signatures or behavior.

None of these points invalidates the user-confirmed working installation.

## 7. Home Assistant cover state and configuration organization

The ESPHome entity was originally **optimistic**: it sent movement commands but did not itself read the shutter's physical position. The user separately identified a Zigbee contact entity:

```text
Control: cover.sscs_atom_s3_garage_shutter
Contact: binary_sensor.garage_shutter_sensor_contact
```

We drafted an HA template-cover example to combine command forwarding with the Zigbee contact's open/closed signal. The proposed entity was `cover.garage_shutter_fused`, possibly in `/config/packages/garage.yaml`. **There was no confirmation in the conversation that this fused cover was deployed.** The example used the then-discussed legacy `cover: - platform: template` syntax, which should not be assumed to be valid in all later HA versions. Sensor polarity and the exact physical end state sensed were not verified.

The user also chose the **Home Assistant packages** approach to organize `configuration.yaml`, with the added entry:

```yaml
homeassistant:
  packages: !include_dir_named packages
```

A draft `packages/garage.yaml` was provided. Whether that package exists in the current instance is unknown.

## 8. Observed status versus unknowns

**Established in the discussion:**

- The BLE protocol and command bytes above were used to operate real hardware.
- The M5 Atom S3 Lite ESPHome controller worked by 2025-09-01.
- The working ESPHome cover's entity ID was provided.
- A separate Zigbee contact entity was identified.

**Not established without actual files or current logs:**

- Exact final contents of the flashed ESPHome YAML and C++ header.
- Exact versions of Home Assistant, ESPHome, Arduino core, and NimBLE-Arduino now in use.
- Whether a `garage.yaml` package or fused cover is installed.
- Presence of later code changes, optimizations, or additional protections.
- Whether a contact sensor's `on`/`off` readings correspond to fully open, fully closed, or simply not-at-one-end.

## 9. Source files that contain the actual implementation

The handoff bundle contains **documentation only**. The files below exist on the user's Home Assistant instance (if paths have not changed) and were **not available in this conversation for bundling**:

```text
/config/esphome/sscs_atom_s3.yaml
/config/esphome/custom/sscs_garage_s3.h
```

If the firmware now uses extra C++ files, those are part of the implementation as well. A sanitized `/config/configuration.yaml` and `/config/packages/garage.yaml` (if present) would document the Home Assistant integration. Do **not** infer the C++ source from the historical description in place of examining the actual working files.

### Sensitive data

Past chat snippets contained real BLE authentication material, database credentials, and third-party API tokens. These have **not** been reproduced in this handoff. Strip or replace them before uploading source files. Keep secrets out of version control and logs; use local secret injection where feasible. Previously exposed external credentials should be reviewed for rotation.
