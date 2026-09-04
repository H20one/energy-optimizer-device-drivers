# Grid Meter Driver Contract

## Device Type: `grid_meter`

Grid meters read the household electricity meter (and optionally gas) via protocols like P1, Modbus, or local HTTP APIs.

---

## Data Contract — `get_data() → GridMeterData | None`

Returns `None` when the device is unreachable. Otherwise returns a dict with:

### Required Fields (must always be a real number)

| Field              | Type    | Unit | Description                                                  |
| ------------------ | ------- | ---- | ------------------------------------------------------------ |
| `grid_power_w`     | `float` | W    | Signed: positive = importing from grid, negative = exporting |
| `import_total_kwh` | `float` | kWh  | Lifetime import counter (sum of all tariffs)                 |
| `export_total_kwh` | `float` | kWh  | Lifetime export counter (sum of all tariffs)                 |

### Optional Fields (`None` = device doesn't support it, `0.0` = supported but zero)

| Field           | Type            | Unit | Description                                  |
| --------------- | --------------- | ---- | -------------------------------------------- |
| `import_t1_kwh` | `float \| None` | kWh  | Peak/day tariff import counter               |
| `import_t2_kwh` | `float \| None` | kWh  | Off-peak/night tariff import counter         |
| `export_t1_kwh` | `float \| None` | kWh  | Peak tariff export counter                   |
| `export_t2_kwh` | `float \| None` | kWh  | Off-peak tariff export counter               |
| `gas_total_m3`  | `float \| None` | m³   | Gas meter reading (only if connected via P1) |
| `voltage_l1_v`  | `float \| None` | V    | Phase 1 voltage                              |
| `voltage_l2_v`  | `float \| None` | V    | Phase 2 voltage (None if single-phase)       |
| `voltage_l3_v`  | `float \| None` | V    | Phase 3 voltage (None if single-phase)       |
| `current_l1_a`  | `float \| None` | A    | Phase 1 current                              |
| `current_l2_a`  | `float \| None` | A    | Phase 2 current                              |
| `current_l3_a`  | `float \| None` | A    | Phase 3 current                              |
| `frequency_hz`  | `float \| None` | Hz   | Grid frequency                               |

**Enforcement:** the required/optional split above is marked in `base.py`'s `GridMeterData` via
`Required[...]` and checkable at runtime with
`energy_optimizer_drivers.contract_validation.validate_contract_data()` — see that module for what
it does and doesn't check.

---

## Timeout & Error Handling Contract

1. `get_data()` **must not block** for longer than `DRIVER_CALL_TIMEOUT` (15 seconds). Set an internal HTTP/serial timeout ≤ this value.
2. On communication failure, return `None` — **never raise** an exception.
3. The app wraps all `get_data()` calls in a timeout guard (`safe_get_data()`) that kills calls exceeding 15 seconds, but well-behaved drivers should enforce their own timeout internally.
4. `get_status()` must be **non-blocking** (return cached state from the last `get_data()` attempt).

---

## Discovery Contract — `discover() → DiscoveryResult`

Drivers that support auto-detection override the `discover()` classmethod. The app calls this during the Add Device wizard to find devices on the local network or bus.

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
4. If the hardware/network is unavailable, return an empty result with a warning explaining why.
5. If the resource is busy (e.g. port already open), warn that the device may already be configured.
6. If discovery completes but finds nothing, include a warning so the user knows the scan ran successfully but nothing responded.
7. **Must not block indefinitely.** LAN-based drivers using `lan_scan.scan_subnet()` inherit its own documented ceiling (currently 60s — see that function's own docstring for the authoritative, current number, since it has changed before). A driver with its own scan loop should apply a similarly bounded timeout.
8. Each dict in `devices` must contain keys that match the driver's `config_schema()` — the app passes them directly to `__init__(config)`.

### Quick mode (optional) — `discover_quick() → DiscoveryResult`

An optional, additive extension point — **not** part of `discover()`'s own contract above, which is unaffected either way. `BaseDriver.discover_quick()`'s default implementation just calls `discover()` unchanged, so a driver that doesn't override it keeps working exactly as before — the app calls `discover_quick()` unconditionally on every driver, no capability check needed.

LAN-based drivers using `scan_subnet()` should override this to forward `quick=True` into their own `scan_subnet()` call: a fast host-presence pre-filter runs first (nudges ARP resolution, then checks which addresses actually have a live host), so a genuinely unused address is skipped entirely instead of paying its full per-address probe timeout. See `grid/homewizard_p1.py` for the pattern. Drivers with nothing to pre-filter (serial/bus addressing) simply don't override this.

### Example

```python
# Success: found one device
DiscoveryResult(
    devices=[{"ip": "192.168.1.42"}],
    warnings=[],
)

# Failure: network issue
DiscoveryResult(
    devices=[],
    warnings=["Could not determine the local network address. Make sure your Ionemo base is connected to your home network."],
)
```

---

## Validation Rules

1. If `get_data()` returns a dict, all **required** fields must be present and non-None.
2. If a required field is missing or None, the app treats the driver as in error state.
3. `import_total_kwh` should be the sum of all tariff counters (T1 + T2 + ...) if the meter has split tariffs. This ensures the app always has a single reliable total.
4. For single-phase meters: only `voltage_l1_v` and `current_l1_a` should be populated. L2/L3 should be `None` (not `0.0`), since the device has no concept of those phases.

---

## How the App Uses These Fields

| Field              | Consumer                          | Purpose                         |
| ------------------ | --------------------------------- | ------------------------------- |
| `grid_power_w`     | Recorder, Optimizer, Flow display | Core energy balance calculation |
| `import_total_kwh` | Details display, Billing          | Cost calculations               |
| `export_total_kwh` | Details display, Billing          | Injection revenue               |
| `gas_total_m3`     | Recorder, Details display         | Gas usage tracking              |
| `voltage_l1_v`     | Details display                   | Grid quality monitoring         |
| `import_t1/t2_kwh` | Details display, Billing          | Day/night tariff breakdown      |

---

## Example Return Value

```python
# HomeWizard P1 Dongle (3-phase, with gas)
{
    "grid_power_w": -450.0,         # Exporting 450W
    "import_total_kwh": 8234.5,     # Sum of T1 + T2
    "export_total_kwh": 2100.3,
    "import_t1_kwh": 5200.0,
    "import_t2_kwh": 3034.5,
    "export_t1_kwh": 1400.0,
    "export_t2_kwh": 700.3,
    "gas_total_m3": 1543.21,
    "voltage_l1_v": 231.2,
    "voltage_l2_v": 230.8,
    "voltage_l3_v": 229.5,
    "current_l1_a": 3.2,
    "current_l2_a": 1.1,
    "current_l3_a": 0.5,
    "frequency_hz": 50.01,
}
```

```python
# Shelly EM (single-phase, no gas)
{
    "grid_power_w": 1200.0,
    "import_total_kwh": 4500.0,
    "export_total_kwh": 300.0,
    "import_t1_kwh": None,          # Shelly doesn't know tariffs
    "import_t2_kwh": None,
    "export_t1_kwh": None,
    "export_t2_kwh": None,
    "gas_total_m3": None,           # No gas meter
    "voltage_l1_v": 232.1,
    "voltage_l2_v": None,           # Single-phase
    "voltage_l3_v": None,
    "current_l1_a": 5.2,
    "current_l2_a": None,
    "current_l3_a": None,
    "frequency_hz": 50.00,
}
```

---

## Built-in Drivers

| Driver ID       | Device               | Connection | Reference                                       |
| --------------- | -------------------- | ---------- | ----------------------------------------------- |
| `homewizard_p1` | HomeWizard P1 Dongle | WiFi       | [homewizard_p1.md](../drivers/homewizard_p1.md) |
