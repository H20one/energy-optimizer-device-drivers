# Daikin BRP WiFi Adapter — AC Unit Driver Reference

**Driver ID:** `daikin_brp`
**Device type:** `ac_unit`
**Connection:** WiFi (HTTP, local API, no authentication)
**Manufacturer:** Daikin
**Supported models:** Split-system air conditioners fitted with a BRP072A21, BRP072Bxx, BRP072Cxx,
or BRP069C4x WiFi adapter. **Not compatible:** BRP084 (Onecta cloud-only adapter) or units with no
WiFi adapter installed.

> For the data contract this driver must satisfy, see [ac_unit.md](../contracts/ac_unit.md).

---

## Configuration

| Field     | Type   | Required | Default | Description                                              |
| --------- | ------ | :------: | ------- | ---------------------------------------------------------- |
| `room`    | text   |    No    | —       | Optional label shown on the device card (not read from the device — you type it) |
| `ip`      | text   |   Yes    | —       | IP address of the BRP adapter on your local network       |
| `timeout` | number |    No    | `3`     | HTTP request timeout in seconds                           |

No password or API key is required — the BRP adapter's local API accepts requests from any device
on the same network segment.

---

## How the Driver Communicates

All calls are plain HTTP GET against the adapter, no authentication:

```
GET /common/basic_info        — probe / device identity (used by discover())
GET /aircon/get_sensor_info   — room temperature, humidity, outdoor temp
GET /aircon/get_control_info  — current operating state (pow/mode/stemp/f_rate/…)
GET /aircon/set_control_info  — apply control changes (all writable fields required)
```

Responses are plain text, comma-separated `key=value` pairs, e.g.:

```
ret=OK,pow=1,mode=3,stemp=22.0,shum=0,f_rate=A,f_dir=0,…
```

`get_data()` calls `get_sensor_info` and `get_control_info` in sequence and returns `None` if
either call fails or `ret` isn't `OK`.

### Control-info caching

`get_control_info` returns many read-only extra fields (`b_mode`, `b_stemp`, schedule/holiday
fields, etc.) that the adapter rejects (`ret=PARAM NG`) if echoed back verbatim in a `set_control_info`
call. The driver:

1. Filters any `get_control_info` response down to the six fields `set_control_info` actually
   accepts (`pow`, `mode`, `stemp`, `shum`, `f_rate`, `f_dir`) before writing.
2. Caches the last `get_control_info` response in memory for 60 seconds, so calling `set_mode()`
   immediately followed by `set_temperature()` doesn't need two redundant GETs — the second setter
   reuses the cached state and merges its own change in.
3. Invalidates the cache immediately after any failed write (network error or `ret != OK`), so the
   next command re-fetches fresh state rather than retrying against stale/rejected params.

---

## Mode Mapping

| Contract `mode` | Daikin `pow` | Daikin `mode` |
| ---------------- | :----------: | :-----------: |
| `"off"`          | `0`          | `0`            |
| `"auto"`         | `1`          | `1`            |
| `"dry"`          | `1`          | `2`            |
| `"cool"`         | `1`          | `3`            |
| `"heat"`         | `1`          | `4`            |
| `"fan"`          | `1`          | `6`            |

Reading: `pow=0` always maps to `"off"` regardless of `mode`. Daikin's own `mode=0` (a legacy
auto-alias) also maps to `"auto"`, same as `mode=1`.

Fan mode (`mode=6`) uses `stemp="--"`/`shum="--"` (no set-point applies) instead of a numeric
value — the driver substitutes a fallback value (`22.0`) when reading `target_temp_c` in that
state, and restores a numeric set-point automatically when switching back to a temperature-based
mode.

## Fan Speed Mapping

| Contract `fan_speed` | Daikin `f_rate` |
| ---------------------- | :--------------: |
| `"auto"`               | `A`               |
| `"silent"`              | `B`               |
| `"1"`                  | `3`               |
| `"2"`                  | `4`               |
| `"3"`                  | `5`               |
| `"4"`                  | `6`               |
| `"5"`                  | `7`               |

See `docs/contracts/ac_unit.md`'s "Fan Speed Values" section for why this vocabulary is
driver-specific rather than a fixed cross-brand enum.

---

## Data Mapping

| Contract field   | Source                                                              |
| ----------------- | -------------------------------------------------------------------- |
| `mode`            | `pow` + `mode` from `get_control_info` (see Mode Mapping above)      |
| `power_w`          | Always `0.0` — the BRP local API does not expose real-time power     |
| `temperature_c`    | `htemp` from `get_sensor_info`                                       |
| `target_temp_c`    | `stemp` from `get_control_info` (fallback `22.0` in fan mode)        |
| `fan_speed`        | `f_rate` from `get_control_info` (see Fan Speed Mapping above)       |
| `humidity_pct`     | `hhum` from `get_sensor_info`; `None` if the adapter reports `"-"` (no humidity sensor fitted) |

---

## Control Contract Details

- **`set_temperature()`** clamps to 10–32 °C, rounds to the nearest 0.5 °C step, and turns the
  unit on if it's currently off. If the unit is in fan mode, it switches to `auto` first, since fan
  mode has no numeric set-point.
- **`set_mode()`** and **`set_fan_speed()`** reject unrecognized values by returning `False` — they
  never raise.
- All three setters fetch (or reuse the cached) `get_control_info` first, since `set_control_info`
  requires every writable field in a single request, not just the one being changed.

---

## Discovery

The driver scans the local `/24` subnet (derived from the Energy Optimizer's own outbound route,
same technique as the other builtin drivers) using a 50-thread pool, probing
`GET /common/basic_info` on each address with a 2.5 s timeout. A host is treated as a match when
it responds with `ret=OK` and `type=aircon`. The scan has an overall 15-second timeout.

---

## Troubleshooting

### No data / connection errors

1. Confirm the adapter's WiFi LED indicates a successful network connection (see your adapter's
   manual — indicator behavior varies by BRP model).
2. Test the API directly: `curl http://<ip>/common/basic_info`
3. Verify the IP address is correct (check your router's DHCP client list for "Daikin" or
   "BRP072").
4. Assign the adapter a static IP or a DHCP reservation — the local API has no way to notify the
   Energy Optimizer of an IP change.

### Power always reads 0 W

This is expected — the BRP local API does not expose instantaneous power consumption. `power_w` is
hardcoded to `0.0` for this driver; there is no fix or workaround via this API.

### Commands rejected (`ret=PARAM NG`)

Usually means a stale/invalid parameter was echoed back to `set_control_info`. This shouldn't
happen in normal use since the driver filters to only the six writable fields, but if you're
seeing it after a firmware update, the adapter's writable-field set may have changed — check your
adapter model against the compatibility list above.

### Fan-only mode won't accept a temperature

Expected — fan mode has no set-point. Calling `set_temperature()` while in fan mode switches the
unit to `auto` mode as part of applying the change, matching how the physical remote behaves.
