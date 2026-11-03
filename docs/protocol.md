# SSCs Smart Garage BLE protocol

What is known about the Sysdes Smart Garage controller (Android app
`com.sysdes.smagara`, class `SDSGbleCtrl`, native `libsd_ble.so`). Established by
decompiling the app, Frida hooks, HCI captures, and working hardware tests
(Windows Bleak script and the ESP32 controller, confirmed 2025-09-01).

## Device

| Item | Value |
| --- | --- |
| Advertised name | starts with `SSCs` (some units `SSCg`) |
| Observed address | `AA:BB:CC:DD:EE:FF` (stable 2023–2025, not guaranteed) |
| Pairing / encryption | none |
| Concurrent connections | one; close the phone app while testing |

## GATT

| Role | UUID (16-bit on the Bluetooth base) |
| --- | --- |
| Service | `0x3333` |
| CMD | `0x9001`: challenge, status and ACK notifications; all writes |
| DATA | `0x9002`: subscribed like the app does; no traffic used |

## Session (verified)

1. Connect, discover, subscribe to DATA, then CMD.
2. The controller notifies a **20-byte challenge on CMD**: 16 random bytes + 4-byte little-endian flags
   (example tail `12 10 09 00` → flags `0x00091012`).
   Some historical firmware sent `00 00` to CMD if nothing arrived; it is kept as a fallback ("nudge").
3. Write the 20-byte token to CMD (write with response).
4. Controller notifies status `cb 00 …`.
5. Write a 4-byte command to CMD (with response).
6. Controller notifies `cc 00`: **command received**, not "movement finished".
7. Disconnect.

## Commands

| Command | Bytes |
| --- | --- |
| Up / open | `c9 00 00 00` |
| Stop | `c9 00 00 01` |
| Down / close | `c9 00 00 02` |

## Token

```
digest   = SHA256(key32 || challenge16)
token0_15 = digest[0..15] XOR digest[16..31]       # sscHARF128 with dst buffer: no reversal
token16_19 = tail (little-endian)
```

Tail by passcode mode:

| Passcode | Tail | Status |
| --- | --- | --- |
| `-1` (none) | `FF FF FF FF` | **verified on hardware** |
| set, `flags & 1` | first 4 bytes of `SHA256(challenge16 \|\| passcodeLE4)` as LE u32; `FFFFFFFF` → `FFFFFFFE` | reconstructed from app, unverified |
| set, `!(flags & 1)` | passcode as LE u32 | reconstructed from app, unverified |

`key32` is per-installation and comes from the app (`connect(byte[] key32, long passcode, …)`).
Never log or commit it, and avoid logging tokens.

## Setup QR code

The sheet shipped with the shutter has a QR code that *is* the key (byte-mode QR, ASCII text),
handled in the app by `frgQRcamera` → `Globals.add_garage`:

```
<2-char type header><Base58(32-byte key)>        Bitcoin alphabet (BRBase58Decode in libnative-lib.so)
```

| Header | Meaning (`SDSGbleConst.sscSGqrH_*`) | Key |
| --- | --- | --- |
| `R1` | remote, slot 1 (the ① on the sheet) | Base58 bytes used as-is. **Verified**: matches the working key |
| `MS` | motor system | Base58 bytes used as-is (same code path; not seen in practice) |
| `R2`–`R4` | remote slots 2–4 | bytes further transformed by native `RM8000upkeyDecode`; unknown |
| `0…` | encrypted share code (38 bytes, mixed with a hash of `HashRandom`) | unsupported |
| `MW`, `Z*`, `T*`, `C*` | window motor, Zigbee bridge, transponder, remocon | other devices |

## Open questions

- What the controller does with a wrong token (disconnect? silence?). The firmware treats both as a failed session.
- Whether several commands can be sent in one authenticated session. The firmware uses one session per command.
- Meaning of the flag bits other than bit 0, and of the `cb 00` status payload.
