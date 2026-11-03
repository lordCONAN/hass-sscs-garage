# Home Assistant setup

Everything here is done in the Home Assistant UI; nothing needs a firmware rebuild.

## Entities the device creates

| Entity | What it is |
| --- | --- |
| **Garage Shutter** (cover) | Open / close / stop. |
| **Garage state source** (select) | `Assumed` (last command) or `Home Assistant` (the helper below). |
| **Shutter key** (text, config) | Write-only box for the shutter key. |
| **Shutter key status** (diagnostic) | Where the key came from and its fingerprint, never the key itself. |
| **Garage BLE status** (diagnostic) | Result of the last command, e.g. `up: acknowledged`, `down: shutter not found`. |

## Choosing which sensor gives open/closed

The device reads one entity, `binary_sensor.garage_shutter_state`. Create it as a helper:

1. **Settings → Devices & services → Helpers → Create helper → Template → Template a binary sensor**.
2. Name it **Garage shutter state** (this gives the entity ID above) and set device class **Garage door**.
3. `on` means open (not closed), `off` means closed. Paste one of the templates below.

Change the template whenever you like; the device follows within a second.

**Zigbee contact only (today):**

```jinja
{% set c = states('binary_sensor.garage_shutter_sensor_contact') %}
{{ c == 'on' if c in ['on', 'off'] else none }}
```

**Camera/BLE sensor first, Zigbee contact as fallback (later):**

```jinja
{% set v = states('binary_sensor.garage_shutter_vision') %}
{% set c = states('binary_sensor.garage_shutter_sensor_contact') %}
{% if v in ['on', 'off'] %}{{ v == 'on' }}
{% elif c in ['on', 'off'] %}{{ c == 'on' }}
{% else %}{{ none }}{% endif %}
```

**Pick the source from a dropdown:** create a **Dropdown** helper `input_select.garage_state_from`
with options `Zigbee` and `Vision`, then:

```jinja
{% set src = 'binary_sensor.garage_shutter_vision' if is_state('input_select.garage_state_from', 'Vision')
   else 'binary_sensor.garage_shutter_sensor_contact' %}
{% set s = states(src) %}
{{ s == 'on' if s in ['on', 'off'] else none }}
```

Returning `none` when the real sensor is unavailable keeps the template from claiming "closed"
when it simply doesn't know. The device then keeps its last known state. Until the helper
exists or reports, the cover behaves as if `Assumed` were selected.

## Entering the shutter key

Open the device page, find **Shutter key**, and enter either:

- the 64-character hex key, or
- the text of the setup QR code from the sheet that came with the shutter. Scan it with any
  phone QR scanner (Google Lens, a scanner app) and paste the result. It starts with `R1`.

The key is stored on the device and survives reboots and firmware updates. The box clears
itself afterwards and Home Assistant never receives the key back. Check
**Shutter key status**: it shows `Set from HA, fingerprint xxxxxxxx`. The same key always
gives the same fingerprint, so you can compare the QR code with an older hex copy without
revealing either.

Enter `clear` to forget the stored key and fall back to the one in the YAML (if any).

Not supported: QR codes starting `R2`–`R4` (other remote slots, which the app transforms with
an unknown native routine) and codes starting `0` (encrypted share codes from another phone).
The status shows why a code was rejected; the previous key stays in use.

Note: Home Assistant may keep the value you typed in its event history for that one entry,
even though the entity itself never shows it.
