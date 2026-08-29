# AC Unit Driver Contract

## Device Type: `ac_unit`

AC unit drivers read status and control split-system or ducted air conditioners via local network APIs (WiFi, Ethernet).

---

## Data Contract — `get_data() → ACUnitData | None`

Returns `None` when the device is unreachable. Otherwise returns a dict with:

### Required Fields (must always be a real value)

| Field           | Type    | Unit | Description                                                     |
| --------------- | ------- | ---- | --------------------------------------------------------------- |
| `mode`          | `str`   | —    | One of: `"off"`, `"cool"`, `"heat"`, `"fan"`, `"dry"`, `"auto"` |
| `power_w`       | `float` | W    | Current power draw (0.0 when off)                               |
| `temperature_c` | `float` | °C   | Measured room temperature                                       |
| `target_temp_c` | `float` | °C   | Active set-point temperature                                    |

### Optional Fields (`None` = device doesn't support it)

| Field          | Type            | Unit | Description                                     |
| -------------- | --------------- | ---- | ----------------------------------------------- |
| `fan_speed`    | `str \| None`   | —    | Driver-specific — see "Fan Speed Values" below  |
| `humidity_pct` | `float \| None` | %    | Measured indoor relative humidity               |

---

## Mode Values

| Mode     | Meaning                                           |
| -------- | ------------------------------------------------- |
| `"off"`  | Unit is powered off                               |
| `"cool"` | Cooling mode — compressor runs to cool the room   |
| `"heat"` | Heating mode — compressor runs to heat the room   |
| `"fan"`  | Fan-only mode — no compressor, air circulation    |
| `"dry"`  | Dehumidification mode — gentle cooling            |
| `"auto"` | Automatic mode — unit selects cool/heat as needed |

Map the device's native modes to these values. If the device reports an unknown mode, use the closest match or `"auto"`.

---

## Fan Speed Values

Unlike `mode`, `fan_speed` is **deliberately not a fixed cross-brand enum**. Different manufacturers
expose genuinely different fan speed granularity (some have 3 steps, some 5, some a numeric range,
some just "auto"/"quiet"/"strong") and forcing them into one shared vocabulary would either lose
real device capability or require lossy remapping in both directions. Instead:

- `get_data()`'s `fan_speed` value and `set_fan_speed()`'s accepted values are whatever strings the
  driver itself defines — they just need to round-trip consistently (a value returned by
  `get_data()` should always be accepted by `set_fan_speed()`).
- The frontend renders whatever speed values a given driver reports, rather than assuming a fixed
  set of buttons.
- **Reference implementation** (`drivers/ac/daikin_brp.py`): `"auto"`, `"silent"`, and the numeric
  steps `"1"` through `"5"`. This is what actually ships today — if you're building a second AC
  driver, match this vocabulary where your device has equivalent steps so the frontend's existing
  fan-speed UI works without changes, but don't invent values your device can't really do.

---

## Control Contract

### `set_mode(mode: str) → bool`

Sets the operating mode. `mode` must be one of the six defined values above.

Returns `True` if the command was accepted by the device, `False` on failure.

### `set_temperature(temp_c: float) → bool`

Sets the target set-point temperature in °C. Typical valid range: 16–30 °C.

Drivers should clamp the value to the device's supported range before sending.

Returns `True` if the command was accepted.

### `set_fan_speed(speed: str) → bool`

Sets the fan speed. Valid values are driver-specific — see "Fan Speed Values" above. Return `False`
(never raise) if `speed` isn't one of the values this driver supports.

Returns `True` if the command was accepted.

---

## Timeout & Error Handling Contract

1. `get_data()` **must not block** for longer than `DRIVER_CALL_TIMEOUT` (15 seconds).
2. On communication failure, return `None` — **never raise** an exception.
3. `get_status()` must be **non-blocking** (return cached state from the last `get_data()` attempt).
4. `set_mode()`, `set_temperature()`, and `set_fan_speed()` must each use their own timeout (≤ 10 seconds) and return `False` on failure — never raise.

---

## Discovery Contract — `discover() → DiscoveryResult`

Drivers that support auto-detection override the `discover()` classmethod.

### Return Value

`DiscoveryResult` with two fields:

| Field      | Type         | Description                                        |
| ---------- | ------------ | -------------------------------------------------- |
| `devices`  | `list[dict]` | Config dicts matching `config_schema()` keys       |
| `warnings` | `list[str]`  | User-facing messages explaining issues encountered |

### Rules

1. **Must return a `DiscoveryResult`** — never raise an exception.
2. **Catch all internal exceptions** and convert them to warnings.
3. Warnings must be **concise, non-technical, and actionable**.
4. **Must not block for more than 30 seconds** total.

---

## Validation Rules

1. If `get_data()` returns a dict, all **required** fields must be present and non-None.
2. `mode` must be one of the six defined values.
3. `power_w` must be `>= 0.0`. Report `0.0` when the unit is off, not `None`.
4. `temperature_c` is the room sensor reading, not the set-point.
5. `target_temp_c` should match what the device is actively targeting. When the unit is off, return the last set-point (not `0.0`).

---

## How the App Uses These Fields

| Field               | Consumer               | Purpose                                 |
| ------------------- | ---------------------- | --------------------------------------- |
| `mode`              | Devices slide          | Display current mode, highlight control |
| `power_w`           | Devices slide          | Display current power draw              |
| `temperature_c`     | Devices slide          | Display room temperature                |
| `target_temp_c`     | Devices slide          | Sync temperature slider position        |
| `fan_speed`         | Devices slide          | Highlight active fan speed button       |
| `set_mode()`        | Devices slide controls | Mode selector buttons                   |
| `set_temperature()` | Devices slide controls | Temperature slider                      |
| `set_fan_speed()`   | Devices slide controls | Fan speed buttons                       |

---

## Example Return Value

```python
# Daikin BRP adapter (cooling at 22°C) — see drivers/docs/drivers/daikin_brp.md
{
    "mode": "cool",
    "power_w": 0.0,          # BRP local API doesn't expose instantaneous power — always 0.0
    "temperature_c": 24.5,
    "target_temp_c": 22.0,
    "fan_speed": "3",        # Daikin's vocabulary: "auto" | "silent" | "1".."5"
    "humidity_pct": 58.0,
}
```

```python
# Generic WiFi AC unit (off, no humidity sensor)
{
    "mode": "off",
    "power_w": 0.0,
    "temperature_c": 21.3,
    "target_temp_c": 22.0,
    "fan_speed": None,      # Device doesn't expose fan speed
    "humidity_pct": None,   # No humidity sensor
}
```

---

## Built-in Drivers

| Driver ID     | Device                        | Connection | Reference                                                    |
| -------------- | ------------------------------ | ----------- | -------------------------------------------------------------- |
| `daikin_brp`  | Daikin split system (BRP local API, LAN) | WiFi       | [drivers/docs/drivers/daikin_brp.md](../drivers/daikin_brp.md) |
