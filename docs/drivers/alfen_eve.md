# Alfen EVE — EV Charger Driver Reference

**Driver ID:** `alfen_eve`
**Device type:** `ev_charger`
**Connection:** WiFi / Ethernet (HTTPS REST)
**Manufacturer:** Alfen
**Supported models:** EVE Single Pro-line (NG910 series), firmware 6.x and later

> For the data contract this driver must satisfy, see [ev_charger.md](../contracts/ev_charger.md).

---

## Configuration

| Field        | Type     | Required | Default | Description                                                   |
| ------------ | -------- | :------: | ------- | ------------------------------------------------------------- |
| `ip`         | text     |   Yes    | —       | IP address of the charger on your local network               |
| `password`   | password |   Yes    | —       | Password for the chosen user level (set during commissioning) |
| `user_level` | select   |    No    | `admin` | One of: `admin`, `installer`, `sca`                           |
| `timeout`    | number   |    No    | `10`    | HTTP request timeout in seconds                               |

The **admin** level gives full read + current-control access. `installer` and `sca` are read-only for energy monitoring purposes.

> **Advanced overrides:** A `ca_cert_path` key can be written directly to the stored device config
> to force a specific certificate file, and a `cert_store_dir` key can override where the pinned
> certificate is saved (defaults to `data/certs`). Neither is exposed in the setup UI — TOFU
> handles cert pinning automatically for all normal installations.

---

## How the Driver Communicates

The driver maintains a **persistent authenticated session** across polls (every 5 seconds by default). It logs in once and reuses the session cookie until the charger returns HTTP 401, at which point it transparently re-authenticates.

```
POST /api/login              — Authenticate once; session cookie reused across polls
GET  /api/prop?cat=meter1    — Read voltage, current, energy meter
GET  /api/prop?ids=3600_1    — Read OCPP socket state
GET  /api/prop?ids=2068_0    — Read configured max current
```

Three separate property reads are required per cycle because the charger drops the second property ID when combining IDs from different object instances in a single request.

A `threading.Lock` serialises concurrent access from the poller job (`get_data`, every 5 s) and the optimizer job (`set_current`, every 15 s), which run in separate APScheduler threads.

### TLS — automatic certificate pinning (TOFU)

The charger uses a unique self-signed certificate that does not include the device's IP address as a Subject Alternative Name (SAN). Standard CA-style verification therefore fails with "IP address mismatch".

The driver uses **trust on first use (TOFU)** fingerprint pinning via `drivers.cert_store`:

1. On first connection, `ssl.get_server_certificate()` fetches the charger's cert without verifying it (the one-time leap of faith).
2. The cert is saved to `data/certs/alfen_<ip>.pem`.
3. A SHA-256 fingerprint of the cert DER is computed and mounted on the session via `_PinnedCertAdapter`.
4. All subsequent connections verify the server presents the **exact same certificate** by fingerprint — no hostname or SAN checking is performed.

If the charger is unreachable during the TOFU fetch, the driver stays offline and retries on every poll until it succeeds.

### Current control

To set the charging current, the driver writes property `2129_0` (dynamic current setpoint):

```
POST /api/prop   — body: {"2129_0": {"id": "2129_0", "value": <amps>}}
```

Setting `amps = 0` pauses charging. On HTTP 401 the driver drops the session and retries the command once with a fresh login.

---

## Property Reference

### Meter properties — `GET /api/prop?cat=meter1`

| Property ID | Description           | Unit |
| ----------- | --------------------- | ---- |
| `2221_3`    | Voltage L1            | V    |
| `2221_4`    | Voltage L2            | V    |
| `2221_5`    | Voltage L3            | V    |
| `2221_A`    | Current L1            | A    |
| `2221_B`    | Current L2            | A    |
| `2221_C`    | Current L3            | A    |
| `2221_11`   | Power factor          | —    |
| `2221_16`   | Lifetime energy meter | kWh  |

### State properties — queried individually

| Property ID | Description                                          |
| ----------- | ---------------------------------------------------- |
| `3600_1`    | OCPP socket state (0 = available, 3 = charging, ...) |
| `2068_0`    | Configured maximum current (A)                       |
| `2129_0`    | Dynamic current setpoint — **write target**          |

---

## Data Mapping

Power is not directly available from the API. The driver calculates it as:

```
power_w = (V_L1 × I_L1 + V_L2 × I_L2 + V_L3 × I_L3) × power_factor
```

Charging state is determined by total current across all phases exceeding 0.5 A, which is more reliable than the OCPP state alone.

| Contract field     | Source                                             |
| ------------------ | -------------------------------------------------- |
| `state`            | Current > 0.5 A → `charging`; OCPP state otherwise |
| `power_w`          | Calculated (voltage × current × PF)                |
| `current_l1_a`     | `2221_A`                                           |
| `current_l2_a`     | `2221_B`                                           |
| `current_l3_a`     | `2221_C`                                           |
| `energy_total_kwh` | `2221_16`                                          |
| `max_current_a`    | `2068_0`                                           |
| `voltage_l1_v`     | `2221_3`                                           |
| `voltage_l2_v`     | `2221_4`                                           |
| `voltage_l3_v`     | `2221_5`                                           |

---

## Discovery

The Alfen EVE does not respond to network broadcast discovery. The IP address must be entered manually during device configuration.

---

## Security Notes

- The charger uses a self-signed TLS certificate. Verification is **not** disabled — it's pinned
  via trust-on-first-use fingerprinting (see "TLS — automatic certificate pinning" above). If you
  need CA-style verification instead, provide an explicit `ca_cert_path`.
- Ionemo only reads data and sends current-control commands — it does not modify charger configuration.
- Change the default admin password via the charger's web interface (`https://<ip>`).
- Place the charger on an isolated network segment if your security policy requires it.

---

## Troubleshooting

### Connection refused or timeout

1. Confirm the charger is reachable: `curl -k https://<ip>/api`
2. Verify the IP address is correct (check your router's DHCP client list).
3. Confirm the charger's network port is 443 and not firewalled.

### Authentication failure (401)

1. Test login directly:
   ```bash
   curl -k -X POST https://<ip>/api/login \
     -H "Content-Type: application/json" \
     -d '{"username":"admin","password":"<password>"}'
   ```
2. Passwords are case-sensitive.
3. If you changed the password via the web interface, update the device configuration in Ionemo.

### Concurrent session error

The charger allows only one active session per user level. This driver keeps a **persistent
session** and reuses it across polls rather than logging out after each cycle (see "How the Driver
Communicates" above), so it won't collide with itself — but a session left open by another tool
(the charger's own web UI, a second Ionemo instance, etc.) can still trigger this. If a
previous session was interrupted, try waiting 60 seconds for it to expire.

### Power reads as zero while charging

Power is calculated from voltage × current × power factor. If any phase reads 0 V or the power factor reads 0, the result will be zero. Verify the charger has grid power on all three phases via the charger's web console.
