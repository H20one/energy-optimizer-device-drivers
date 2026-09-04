# PV Inverter Driver Contract

## Device Type: `pv_inverter`

PV inverter drivers read solar production data via protocols like RS-485, Modbus TCP, or local HTTP APIs.

---

## Data Contract — `get_data() → PVInverterData | None`

Returns `None` when the device is unreachable or sleeping (e.g. at night with no standby power). Otherwise returns a dict with:

### Required Fields (must always be a real number)

| Field             | Type    | Unit | Description                                           |
| ----------------- | ------- | ---- | ----------------------------------------------------- |
| `solar_power_w`   | `float` | W    | Current AC output power (0.0 at night / when idle)    |
| `daily_energy_wh` | `float` | Wh   | Energy produced today (resets at midnight or sunrise) |
| `total_energy_wh` | `float` | Wh   | Lifetime energy counter                               |

### Optional Fields (`None` = device doesn't support it)

| Field               | Type            | Unit | Description                             |
| ------------------- | --------------- | ---- | --------------------------------------- |
| `temperature_c`     | `float \| None` | °C   | Inverter internal temperature           |
| `dc_voltage_v`      | `float \| None` | V    | DC input voltage (string 1 / combined)  |
| `dc_current_a`      | `float \| None` | A    | DC input current (string 1 / combined)  |
| `grid_voltage_v`    | `float \| None` | V    | AC grid voltage as measured by inverter |
| `grid_frequency_hz` | `float \| None` | Hz   | AC grid frequency                       |

**Enforcement:** the required/optional split above is marked in `base.py`'s `PVInverterData` via
`Required[...]` and checkable at runtime with
`ionemo_drivers.contract_validation.validate_contract_data()` — see that module for what
it does and doesn't check.

---

## Night / Sleep Behaviour

Inverters that go fully offline at night should cause `get_data()` to return `None`. The app will:

- Record `solar_power_w = 0.0` for that snapshot
- Show "sleeping" status on the device card
- Not treat it as an error

Inverters that stay online but produce nothing should return:

```python
{"solar_power_w": 0.0, "daily_energy_wh": 0.0, "total_energy_wh": 123456.0, ...}
```

---

## Timeout & Error Handling Contract

1. `get_data()` **must not block** for longer than `DRIVER_CALL_TIMEOUT` (15 seconds). Set an internal serial/HTTP timeout ≤ this value.
2. On communication failure, return `None` — **never raise** an exception.
3. The app wraps all `get_data()` calls in a timeout guard (`safe_get_data()`) that kills calls exceeding 15 seconds, but well-behaved drivers should enforce their own timeout internally.
4. `get_status()` must be **non-blocking** (return cached state from the last `get_data()` attempt).

---

## Discovery Contract — `discover() → DiscoveryResult`

Drivers that support auto-detection override the `discover()` classmethod. The app calls this during the Add Device wizard to probe for devices on the RS-485 bus or local network.

### Return Value

`DiscoveryResult` with two fields:

| Field      | Type         | Description                                        |
| ---------- | ------------ | -------------------------------------------------- |
| `devices`  | `list[dict]` | Config dicts matching `config_schema()` keys       |
| `warnings` | `list[str]`  | User-facing messages explaining issues encountered |

### Rules

1. **Must return a `DiscoveryResult`** — never raise an exception.
2. **Catch all internal exceptions** and convert them to warnings.
3. Warnings must be **concise, non-technical, and actionable** (the user sees them directly).
4. If the hardware is unavailable (e.g. no USB adapter), return an empty result with a warning explaining why.
5. If the serial port is busy, warn that the device may already be configured.
6. If discovery completes but finds nothing, include a warning so the user knows the scan ran successfully but nothing responded.
7. **Must not block indefinitely.** LAN-based drivers using `lan_scan.scan_subnet()` inherit its own documented ceiling (currently 60s — see that function's own docstring for the authoritative, current number, since it has changed before). A driver with its own scan loop (like `aurora_rs485.py`'s RS-485 bus sweep) should apply a similarly bounded timeout.
8. Each dict in `devices` must contain keys that match the driver's `config_schema()` — the app passes them directly to `__init__(config)`.

### Quick mode (optional) — `discover_quick() → DiscoveryResult`

An optional, additive extension point — **not** part of `discover()`'s own contract above, which is unaffected either way. `BaseDriver.discover_quick()`'s default implementation just calls `discover()` unchanged, so a driver that doesn't override it keeps working exactly as before — the app calls `discover_quick()` unconditionally on every driver, no capability check needed.

LAN-based drivers using `scan_subnet()` should override this to forward `quick=True` into their own `scan_subnet()` call: a fast host-presence pre-filter runs first (nudges ARP resolution, then checks which addresses actually have a live host), so a genuinely unused address is skipped entirely instead of paying its full per-address probe timeout. `aurora_rs485.py` (RS-485/serial, no IP addressing) has nothing to pre-filter and correctly doesn't override this.

### Example

```python
# Success: found two inverters on the bus
DiscoveryResult(
    devices=[{"address": 2, "baudrate": 19200}, {"address": 5, "baudrate": 19200}],
    warnings=[],
)

# Failure: no adapter
DiscoveryResult(
    devices=[],
    warnings=["No USB-to-RS485 adapter detected. Make sure it is plugged into your Ionemo base."],
)
```

---

## Validation Rules

1. If `get_data()` returns a dict, all **required** fields must be present and non-None.
2. Returning `None` is valid at night — many inverters shut down completely when there's no sunlight. The app handles this gracefully (records `solar_power_w = 0.0`).
3. `solar_power_w` must be `>= 0.0`. Negative values are invalid (inverters don't consume power from AC side).
4. `daily_energy_wh` may lag behind `total_energy_wh` changes due to device-internal update intervals.
5. For multi-string inverters: report the combined DC values, or the primary string. Multi-string detail is a future extension.

---

## How the App Uses These Fields

| Field             | Consumer                                 | Purpose                                     |
| ----------------- | ---------------------------------------- | ------------------------------------------- |
| `solar_power_w`   | Recorder, Optimizer, Flow display        | Core energy balance                         |
| `daily_energy_wh` | Details display                          | Today's production summary                  |
| `total_energy_wh` | Recorder (gap backfill), Details display | Lifetime stats + reconnection gap detection |
| `temperature_c`   | Details display                          | Health monitoring                           |

---

## Example Return Value

```python
# Aurora Power-One (RS-485, producing)
{
    "solar_power_w": 2450.0,
    "daily_energy_wh": 8500.0,
    "total_energy_wh": 4_567_000.0,
    "temperature_c": 42.3,
    "dc_voltage_v": 380.5,
    "dc_current_a": 6.8,
    "grid_voltage_v": 231.0,
    "grid_frequency_hz": 50.01,
}
```

```python
# Enphase Microinverter (HTTP API, producing)
{
    "solar_power_w": 320.0,
    "daily_energy_wh": 1200.0,
    "total_energy_wh": 890_000.0,
    "temperature_c": None,          # Enphase doesn't expose this per micro
    "dc_voltage_v": None,           # Not available via Envoy API
    "dc_current_a": None,
    "grid_voltage_v": 230.5,
    "grid_frequency_hz": 50.00,
}
```

---

## Built-in Drivers

| Driver ID      | Device               | Connection | Reference                                     |
| -------------- | -------------------- | ---------- | --------------------------------------------- |
| `aurora_rs485` | Aurora Power-One PVI | RS-485     | [aurora_rs485.md](../drivers/aurora_rs485.md) |
