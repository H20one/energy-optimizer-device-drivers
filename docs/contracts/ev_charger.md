# EV Charger Driver Contract

## Device Type: `ev_charger`

EV charger drivers communicate with charging stations to read status/metering and control the charging current.

---

## Data Contract — `get_data() → EVChargerData | None`

Returns `None` when the device is unreachable. Otherwise returns a dict with:

### Required Fields (must always be a real value)

| Field              | Type    | Unit | Description                                                   |
| ------------------ | ------- | ---- | ------------------------------------------------------------- |
| `state`            | `str`   | —    | One of: `"available"`, `"connected"`, `"charging"`, `"error"` |
| `power_w`          | `float` | W    | Active power draw (0.0 when idle)                             |
| `current_l1_a`     | `float` | A    | Phase 1 current (0.0 if idle or single-phase-idle)            |
| `current_l2_a`     | `float` | A    | Phase 2 current (0.0 for single-phase chargers)               |
| `current_l3_a`     | `float` | A    | Phase 3 current (0.0 for single-phase chargers)               |
| `energy_total_kwh` | `float` | kWh  | Lifetime energy counter                                       |
| `max_current_a`    | `float` | A    | Configured maximum current for this station                   |

### Optional Fields (`None` = device doesn't support it)

| Field                | Type            | Unit | Description                         |
| -------------------- | --------------- | ---- | ----------------------------------- |
| `session_energy_kwh` | `float \| None` | kWh  | Energy delivered in current session |
| `voltage_l1_v`       | `float \| None` | V    | Phase 1 voltage                     |
| `voltage_l2_v`       | `float \| None` | V    | Phase 2 voltage                     |
| `voltage_l3_v`       | `float \| None` | V    | Phase 3 voltage                     |

**Enforcement:** the required/optional split above is marked in `base.py`'s `EVChargerData` via
`Required[...]` and checkable at runtime with
`ionemo_drivers.contract_validation.validate_contract_data()` — see that module for what
it does and doesn't check.

---

## State Values

| State         | Meaning                                                       |
| ------------- | ------------------------------------------------------------- |
| `"available"` | No cable connected, ready for use                             |
| `"connected"` | Cable plugged in, not charging (waiting for schedule/command) |
| `"charging"`  | Actively delivering power to vehicle                          |
| `"error"`     | Fault condition (check `last_error` for details)              |

Drivers must map their device's native states to one of these four. If the native state doesn't clearly map (e.g. "finishing", "suspended"), use the closest match:

- "finishing" → `"connected"` (cable still in, not charging)
- "suspended by EV" → `"connected"`
- "suspended by EVSE" → `"connected"`

---

## Control Contract — `set_current(amps: float) → bool`

| Parameter      | Description                                                    |
| -------------- | -------------------------------------------------------------- |
| `amps = 0`     | Pause charging (set pilot signal to 0 or minimum)              |
| `amps = 6..32` | Set charging current to this value (hardware enforces min/max) |

Returns `True` if the command was accepted by the device. Returns `False` on communication failure.

The driver should:

- Clamp values above its hardware max (e.g. `min(amps, 32)`)
- Convert `amps < hardware_min` (typically 6A) to a pause command
- NOT handle rate limiting — the optimizer handles command intervals

---

## Timeout & Error Handling Contract

1. `get_data()` **must not block** for longer than `DRIVER_CALL_TIMEOUT` (15 seconds). Set an internal HTTP/Modbus timeout ≤ this value.
2. On communication failure, return `None` — **never raise** an exception.
3. The app wraps all `get_data()` calls in a timeout guard (`safe_get_data()`) that kills calls exceeding 15 seconds, but well-behaved drivers should enforce their own timeout internally.
4. `get_status()` must be **non-blocking** (return cached state from the last `get_data()` attempt).
5. `set_current()` should use its own timeout (≤ 10 seconds) and return `False` on failure — never raise.

---

## Discovery Contract — `discover() → DiscoveryResult`

Drivers that support auto-detection override the `discover()` classmethod. The app calls this during the Add Device wizard to find chargers on the local network.

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
4. If the network is unavailable, return an empty result with a warning explaining why.
5. If discovery completes but finds nothing, include a warning so the user knows the scan ran successfully but nothing responded.
6. **Must not block indefinitely.** LAN-based drivers using `lan_scan.scan_subnet()` inherit its own documented ceiling (currently 60s — see that function's own docstring for the authoritative, current number, since it has changed before). A driver with its own scan loop should apply a similarly bounded timeout.
7. Each dict in `devices` must contain keys that match the driver's `config_schema()` — the app passes them directly to `__init__(config)`.

### Quick mode (optional) — `discover_quick() → DiscoveryResult`

An optional, additive extension point — **not** part of `discover()`'s own contract above, which is unaffected either way. `BaseDriver.discover_quick()`'s default implementation just calls `discover()` unchanged, so a driver that doesn't override it keeps working exactly as before — the app calls `discover_quick()` unconditionally on every driver, no capability check needed.

LAN-based drivers using `scan_subnet()` should override this to forward `quick=True` into their own `scan_subnet()` call: a fast host-presence pre-filter runs first (nudges ARP resolution, then checks which addresses actually have a live host), so a genuinely unused address is skipped entirely instead of paying its full per-address probe timeout. Drivers with nothing to pre-filter (serial/bus addressing) simply don't override this.

### Example

```python
# Success: found a charger
DiscoveryResult(
    devices=[{"host": "192.168.1.100"}],
    warnings=[],
)

# Failure: network issue
DiscoveryResult(
    devices=[],
    warnings=["No EV chargers found on the network (192.168.1.x). Make sure the charger is powered on and connected to WiFi."],
)
```

---

## Validation Rules

1. If `get_data()` returns a dict, all **required** fields must be present and non-None.
2. `state` must be one of the four defined values.
3. For single-phase chargers: `current_l2_a` and `current_l3_a` must be `0.0` (not `None`). The device reports them — they're just always zero.
4. `power_w` should be calculated as `Σ(Vn × In) × PF` if the device doesn't report it directly.
5. `max_current_a` is the station's configured limit, not the cable or vehicle limit.

---

## How the App Uses These Fields

| Field                | Consumer                          | Purpose                                |
| -------------------- | --------------------------------- | -------------------------------------- |
| `state`              | Recorder, UI device card          | Display + recording                    |
| `power_w`            | Recorder, Optimizer, Flow display | Charger contribution to grid balance   |
| `current_l1/l2/l3_a` | Recorder, Details display         | Per-phase monitoring                   |
| `energy_total_kwh`   | Recorder                          | Session/lifetime tracking              |
| `max_current_a`      | Optimizer                         | Upper bound for `set_current()`        |
| `set_current()`      | Optimizer                         | Control charging based on surplus/peak |

---

## Example Return Value

```python
# Alfen EVE Single Pro-line (3-phase, charging) — this is what the real driver returns today
{
    "state": "charging",
    "power_w": 7360.0,
    "current_l1_a": 10.7,
    "current_l2_a": 10.6,
    "current_l3_a": 10.5,
    "energy_total_kwh": 1234.5,
    "max_current_a": 32.0,
    "session_energy_kwh": None,  # Alfen driver always hardcodes None — the charger's
                                  # local API doesn't expose a per-session energy counter
    "voltage_l1_v": 231.0,
    "voltage_l2_v": 230.5,
    "voltage_l3_v": 229.8,
}
```

```python
# Hypothetical single-phase charger with session tracking (illustrative only — no such
# driver ships today). Shows the single-phase current/voltage convention (L2/L3 zeroed
# or None) alongside a driver that DOES support session_energy_kwh.
{
    "state": "available",
    "power_w": 0.0,
    "current_l1_a": 0.0,
    "current_l2_a": 0.0,        # Single-phase: always 0.0
    "current_l3_a": 0.0,        # Single-phase: always 0.0
    "energy_total_kwh": 567.8,
    "max_current_a": 16.0,      # Single-phase max
    "session_energy_kwh": 0.0,  # This driver DOES track sessions; one just started
    "voltage_l1_v": 232.0,
    "voltage_l2_v": None,       # Single-phase: L2/L3 voltage not reported
    "voltage_l3_v": None,
}
```

---

## Built-in Drivers

| Driver ID   | Device                    | Connection      | Reference                               |
| ----------- | ------------------------- | --------------- | --------------------------------------- |
| `alfen_eve` | Alfen EVE Single Pro-line | WiFi / Ethernet | [alfen_eve.md](../drivers/alfen_eve.md) |
