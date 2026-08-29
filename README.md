# Energy Optimizer — Device Drivers

Drivers are self-contained adapters that connect physical energy devices (meters, inverters,
chargers, AC units) to the [Energy Optimizer](https://github.com/H20one/energy-optimizer) app. Each
driver translates a device-specific protocol into a fixed data contract the app understands.

This package is installed as a dependency by the main app and runs **in-process** inside it — not as
a separate service. It is a completely standalone repo so that:
- the driver layer can be public and open to outside contributors without exposing any of the main
  app's own code, and
- adding, reviewing, and releasing a driver doesn't require touching the main app's repo at all.

**A driver only ever sees a plain `config` dict in and returns a plain typed dict out — nothing here
knows anything about Flask, SQLite, encryption, scheduling, or HTTP routes.** That contract
(`base.py`) is the *only* thing shared between this repo and the main app.

**New here?** Read this file top-to-bottom, then follow [CONTRIBUTING.md](CONTRIBUTING.md) to write
your first driver.

---

## Architecture

```
energy-optimizer-device-drivers/
├── energy_optimizer_drivers/    ← the installable package
│   ├── base.py              ← ABCs, TypedDicts, enums (the contract)
│   ├── registry.py          ← Driver discovery and registration
│   ├── cert_store.py        ← Shared TOFU TLS certificate pinning for HTTPS drivers
│   ├── grid/                ← Grid meter drivers
│   │   └── homewizard_p1.py
│   ├── pv/                  ← PV inverter drivers
│   │   └── aurora_rs485.py
│   ├── ev/                  ← EV charger drivers
│   │   └── alfen_eve.py     ← uses cert_store.py for TLS pinning
│   └── ac/                  ← AC unit drivers
│       └── daikin_brp.py
├── tests/                   ← Automated compliance tests
│   ├── test_contract_compliance.py
│   └── test_security_compliance.py
├── docs/
│   ├── contracts/           ← Data contract docs per device type
│   │   ├── grid_meter.md
│   │   ├── pv_inverter.md
│   │   ├── ev_charger.md
│   │   └── ac_unit.md
│   └── drivers/             ← Built-in driver reference docs
│       ├── homewizard_p1.md
│       ├── aurora_rs485.md
│       ├── alfen_eve.md
│       └── daikin_brp.md
├── SECURITY.md               ← Security rules all drivers must follow
├── CONTRIBUTING.md           ← Step-by-step guide to writing a driver
└── .github/agents/driver-reviewer.agent.md   ← AI reviewer that runs on every PR
```

---

## How It Works

```
┌─────────────────────────────┐      ┌──────────────┐      ┌────────────────┐
│  Energy Optimizer app        │----->|    Registry  │----->│  Your Driver   │
│  (a separate repo — pip-     │      │ (registry.py)│      │  (grid/xx.py)  │
│   installs this package)     │      │              │      │                │
└─────────────────────────────┘      └──────────────┘      └────────────────┘
         │                                                     │
         │  calls get_data() on a schedule                     │  talks to device
         │  calls get_status() for UI                          │  via network/serial
         │  calls set_current()/set_mode()/etc. for control    │
         ▼                                                     ▼
   Dashboard UI                                        Physical Device
```

1. The main app's `requirements.txt` pins a released version of this package (e.g.
   `git+https://github.com/H20one/energy-optimizer-device-drivers.git@v0.1.0`).
2. `energy_optimizer_drivers.registry.load_all_drivers()` imports builtin driver modules (each calls
   `register_driver()` at import time) and discovers any externally pip-installed drivers via the
   `energy_optimizer.drivers` entry point group.
3. When a user configures a device via the UI, the app instantiates the driver with the user's
   config (encrypted at rest by the app — this package never sees the encryption layer).
4. The app calls `get_data()` on a schedule to poll the device — every 5–10 seconds depending on
   device type, so `get_data()` must be fast and must never block past its 15-second contract.
5. The return value must conform to the typed data contract for that device type.

---

## Supported Device Types

| Type        | Enum                     | ABC                | Return Type      | Purpose                            |
| ----------- | ------------------------ | ------------------ | ----------------- | ----------------------------------- |
| Grid Meter  | `DeviceType.GRID_METER`  | `GridMeterDriver`  | `GridMeterData`   | Read power import/export            |
| PV Inverter | `DeviceType.PV_INVERTER` | `PVInverterDriver` | `PVInverterData`  | Read solar production               |
| EV Charger  | `DeviceType.EV_CHARGER`  | `EVChargerDriver`  | `EVChargerData`   | Read status + control current       |
| AC Unit     | `DeviceType.AC_UNIT`     | `ACUnitDriver`     | `ACUnitData`      | Read status + control mode/temp/fan |

Each type has a dedicated ABC in `energy_optimizer_drivers/base.py` and a full data contract doc in
`docs/contracts/`. `battery` and `smart_socket` are commented-out placeholders in `DeviceType`, not
real, buildable types yet.

---

## What a Driver Must Implement

Every driver must:

| Method / Attribute | Description                                            |
| ------------------- | ------------------------------------------------------ |
| `driver_id`         | Unique string identifier (snake_case)                  |
| `name`               | Real product name shown in UI                          |
| `manufacturer`       | Real brand name                                        |
| `builder`            | Who built the driver (optional, defaults to "Unknown") |
| `device_type`        | `DeviceType` enum value                                |
| `connection_type`    | `ConnectionType` enum value                            |
| `__init__(config)`   | Accept a dict of user-provided settings                |
| `config_schema()`    | Return list of `ConfigField` dicts for the setup form  |
| `get_data()`         | Poll device, return typed data or `None` on failure    |
| `get_status()`       | Return cached status string (non-blocking)             |
| `last_error`         | Property: last error message or `None`                 |

EV charger drivers additionally implement `set_current(amps) -> bool`. AC unit drivers additionally
implement three setters — `set_mode()`, `set_temperature()`, `set_fan_speed()` — see
[CONTRIBUTING.md](CONTRIBUTING.md) for full signatures and a worked example.

Optional overrides (both classmethods): `discover()` (auto-detect devices) and `setup_guide()`
(Markdown help text for the setup wizard).

---

## Key Rules

1. **Drivers ONLY import from `energy_optimizer_drivers.base`** — never from the main app's `src/`,
   `config/`, or other drivers. This isn't just style: this package ends up pip-installed into the
   same Python process as the main app in production, so it's a real boundary, not a folder
   convention.
2. **Never raise from `get_data()` or `set_current()`** — catch everything, return `None`/`False`.
3. **All network/serial calls must have explicit timeouts** ≤ 15 seconds.
4. **Required data fields must always be real numbers** — if you can't read them, return `None` (whole dict).
5. **Optional fields use `None`** for "device doesn't support it" and `0.0` for "supported but zero".
6. **No outbound internet** — drivers talk to LAN-local devices only.
7. **No filesystem writes, no subprocess, no eval/exec** — see [SECURITY.md](SECURITY.md).

---

## Automated Validation

Every driver is checked automatically by two test suites, both run in CI on every pull request (see
[CONTRIBUTING.md](CONTRIBUTING.md#submitting-your-driver) for the full review process):

### Contract Compliance (`tests/test_contract_compliance.py`)

Verifies identity attributes, config schema structure, method signatures, return type annotations,
and ABC hierarchy for all registered drivers.

### Security Compliance (`tests/test_security_compliance.py`)

Static AST analysis that catches forbidden imports (`subprocess`, `pickle`), dangerous calls
(`eval`, `exec`, `print`), credential logging, IP-address logging at INFO level or above, external
URLs, filesystem writes, missing network timeouts, and global SSL disabling. Scans both the per-type
driver folders and root-level infra files (`base.py`, `cert_store.py`, `registry.py`) —
`cert_store.py` is exempt from the filesystem-write check specifically, since TOFU cert pinning is
its documented job (see [SECURITY.md](SECURITY.md) §3.2). This can only scan files physically in
this repo — see [SECURITY.md](SECURITY.md)'s "Note on automated enforcement" for what that does and
doesn't cover for externally pip-installed drivers.

Run both with:

```bash
pytest tests/ -v
```

There is also an AI-powered **driver-reviewer agent**
(`.github/agents/driver-reviewer.agent.md`) that performs a deeper review during pull requests,
covering things static analysis structurally can't (data-contract correctness against the relevant
device type's contract doc, sensible error handling, whether a protocol was actually implemented
correctly).

---

## How Drivers Are Loaded

1. **Builtin drivers** — modules listed in `energy_optimizer_drivers/registry.py`'s
   `_load_builtin_drivers()` are imported at startup. Each module calls `register_driver()` at
   import time.
2. **External drivers** (your own separate pip-installed package) — discovered via the
   `energy_optimizer.drivers` entry point group. See
   [CONTRIBUTING.md](CONTRIBUTING.md#step-6-for-external-pip-installed-drivers) for details.

---

## Quick Links

| Document                                                       | What it covers                             |
| ---------------------------------------------------------------- | ------------------------------------------- |
| [CONTRIBUTING.md](CONTRIBUTING.md)                              | Step-by-step guide to writing a new driver, and how a PR actually gets reviewed |
| [SECURITY.md](SECURITY.md)                                      | Security rules and forbidden patterns      |
| [docs/contracts/grid_meter.md](docs/contracts/grid_meter.md)    | Grid meter data contract                   |
| [docs/contracts/pv_inverter.md](docs/contracts/pv_inverter.md)  | PV inverter data contract                  |
| [docs/contracts/ev_charger.md](docs/contracts/ev_charger.md)    | EV charger data contract                   |
| [docs/contracts/ac_unit.md](docs/contracts/ac_unit.md)          | AC unit data contract                      |
| [energy_optimizer_drivers/cert_store.py](energy_optimizer_drivers/cert_store.py) | TOFU TLS certificate pinning for HTTPS drivers with self-signed certs |
| [energy_optimizer_drivers/base.py](energy_optimizer_drivers/base.py) | Source of truth — ABCs and TypedDicts      |
| [ARCHITECTURE.md](ARCHITECTURE.md)                               | Why this is a separate repo, and exactly what is/isn't shared with the main app |
