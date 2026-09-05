# Contributing a Driver

This guide explains how to create a new driver for the Ionemo app.

---

## Prerequisites

- A real, commercially available device (brand + model)
- Access to the device's API documentation or protocol specification
- Python 3.13+

Install this repo's own package in editable mode plus the exact dev toolchain CI runs against —
same `ruff`/`basedpyright`/`pytest` versions, same `pyrightconfig.json` (already in the repo, so it
applies automatically once `basedpyright` is installed) — so what passes locally passes in CI:

```bash
pip install -e .
pip install -r requirements-dev.txt
```

---

## Step 1: Choose the Device Type

Drivers must target one of the fixed device types defined in `DeviceType`:

| Enum Value               | Folder          | ABC to subclass    |
| ------------------------ | --------------- | ------------------ |
| `DeviceType.GRID_METER`  | `ionemo_drivers/grid/` | `GridMeterDriver`  |
| `DeviceType.PV_INVERTER` | `ionemo_drivers/pv/`   | `PVInverterDriver` |
| `DeviceType.EV_CHARGER`  | `ionemo_drivers/ev/`   | `EVChargerDriver`  |
| `DeviceType.AC_UNIT`     | `ionemo_drivers/ac/`   | `ACUnitDriver`     |

**Your device doesn't fit any of these four?** You can't add a new device type by yourself, even
though `base.py` lives in this repo — the main `ionemo-app` has hand-written support
(a typed accessor, a scheduler job, a UI card) for each existing type that doesn't exist yet for a
new one, and none of that lives here. See
[ARCHITECTURE.md](ARCHITECTURE.md#changes-that-need-a-maintainer-not-just-a-pr) for the full list of
changes like this, and open an issue describing your device before writing any code — a maintainer
needs to plan the matching main-app change first.

---

## Step 2: Read the Data Contract

Before writing any code, read the contract doc for your device type:

- [Grid Meter Contract](docs/contracts/grid_meter.md)
- [PV Inverter Contract](docs/contracts/pv_inverter.md)
- [EV Charger Contract](docs/contracts/ev_charger.md)
- [AC Unit Contract](docs/contracts/ac_unit.md)

These define:

- Which fields are **required** (must never be `None`)
- Which fields are **optional** (`None` = device doesn't support it)
- Valid values for enum-like fields (e.g. charger `state`)
- How the app uses each field

---

## Step 3: Create the Driver File

Create a new file in the appropriate folder:

```
ionemo_drivers/{type}/my_device.py
```

Use a filename based on `{manufacturer}_{model}` in snake_case.

---

## Step 4: Implement the Driver Class

```python
"""Manufacturer Model — Device Type Driver.

Brief description of the device and how it communicates.
"""

import logging
from typing import Any

from ionemo_drivers.base import (
    ConfigField,
    ConnectionType,
    DeviceType,
    GridMeterData,       # or PVInverterData, EVChargerData, ACUnitData
    GridMeterDriver,     # or PVInverterDriver, EVChargerDriver, ACUnitDriver
)
from ionemo_drivers.registry import register_driver

logger = logging.getLogger(__name__)


class MyDeviceDriver(GridMeterDriver):
    """Grid meter driver for Manufacturer Model."""

    # ── Identity (required) ───────────────────────────────────────────────
    driver_id = "manufacturer_model"           # Unique, snake_case
    name = "Manufacturer Model X"              # Exact product name
    manufacturer = "Manufacturer"              # Brand name
    builder = "YourName"                       # Who built this driver (optional)
    device_type = DeviceType.GRID_METER        # Must match the ABC
    connection_type = ConnectionType.WIFI      # How it talks to the device

    # ── Init ──────────────────────────────────────────────────────────────

    def __init__(self, config: dict[str, Any]) -> None:
        """Instantiate with user-provided config from the Add Device wizard."""
        self._ip: str = config["ip"]
        self._last_error: str | None = None
        self._last_success: float | None = None

    # ── Config Schema ─────────────────────────────────────────────────────

    @classmethod
    def config_schema(cls) -> list[ConfigField]:
        """Define what the user needs to fill in to connect this device."""
        return [
            {"key": "ip", "label": "IP Address", "type": "text", "required": True},
        ]

    # ── Status ────────────────────────────────────────────────────────────

    def get_status(self) -> str:
        if self._last_error:
            return "error"
        if self._last_success is None:
            return "disabled"
        return "connected"

    @property
    def last_error(self) -> str | None:
        return self._last_error

    # ── Data ──────────────────────────────────────────────────────────────

    def get_data(self) -> GridMeterData | None:
        """Fetch data and map to the contract. Return None on failure."""
        try:
            # ... communicate with device ...
            # ... map response to contract ...
            return GridMeterData(
                # Required — must always have real values
                grid_power_w=...,
                import_total_kwh=...,
                export_total_kwh=...,
                # Optional — None if device doesn't support
                gas_total_m3=None,
                voltage_l1_v=...,
                ...
            )
        except Exception as e:
            self._last_error = str(e)
            logger.warning("MyDevice read failed: %s", e)
            return None


# Register at import time — this is how the registry discovers the driver
register_driver("manufacturer_model", MyDeviceDriver)
```

**EV charger drivers** additionally implement one setter: `set_current(self, amps: float) -> bool`.

**AC unit drivers** additionally implement three setters, not one — `set_mode(self, mode: str)`,
`set_temperature(self, temp_c: float)`, `set_fan_speed(self, speed: str)`, all returning `bool`.
See `ionemo_drivers/ac/daikin_brp.py` for a real worked example (the only builtin driver with a
multi-setter interface today) and `docs/contracts/ac_unit.md` for the `mode` enum and a note on
why `fan_speed` deliberately does NOT have a fixed cross-brand vocabulary.

---

## Step 5: Register in the Registry (builtin drivers only)

Add your module to the `builtin_modules` list in `ionemo_drivers/registry.py`:

```python
builtin_modules = [
    "ionemo_drivers.grid.homewizard_p1",
    "ionemo_drivers.pv.aurora_rs485",
    "ionemo_drivers.ev.alfen_eve",
    "ionemo_drivers.ac.daikin_brp",
    "ionemo_drivers.grid.my_device",       # ← add here
]
```

---

## Step 6: For External (pip-installed) Drivers

If your driver lives in a separate package, skip Step 5 and instead declare an entry point:

```toml
# pyproject.toml of your external driver package
[project]
name = "ionemo-driver-mydevice"
dependencies = ["ionemo-drivers"]    # needs access to ionemo_drivers.base

[project.entry-points."ionemo.drivers"]
manufacturer_model = "my_package.driver:MyDeviceDriver"
```

The app discovers it automatically at startup via `importlib.metadata.entry_points()`.

---

## Rules

### Identity

- `driver_id` must be unique across all drivers (builtin + external)
- `name` must be the real product name (e.g. "Shelly Pro 3EM", not "My Energy Meter")
- `manufacturer` must be the real brand name
- `builder` is optional — set it to your name or organization (defaults to "Unknown" if omitted)
- `device_type` must use the `DeviceType` enum — no raw strings
- `connection_type` must use the `ConnectionType` enum

### Data Contract

- **Required fields must never be `None`** — if you can't read them, return `None` from `get_data()`
- Optional fields: use `None` for unsupported, `0.0` for supported-but-zero
- EV charger `state` must be one of: `"available"`, `"connected"`, `"charging"`, `"error"`
- EV charger single-phase: set `current_l2_a = 0.0`, `current_l3_a = 0.0` (not `None`)

### Error Handling

- Never let exceptions escape from `get_data()` or `set_current()` — catch and return `None`/`False`
- Store error details in `self._last_error` for status reporting
- Use `logger.warning()` for recoverable errors, not `logger.error()`

### Dependencies

- Only import from `ionemo_drivers.base`, `ionemo_drivers.registry`, and
  `ionemo_drivers.cert_store` (for HTTPS drivers)
- **Never** import from `ionemo-app`'s own `src/`, `config/`, or other driver modules — even
  though this is now a separate repo, the drivers package still ends up pip-installed into the same
  process as the main app in production, so this isn't just a style rule, it's a real boundary
- External libraries (e.g. `requests`, `pyserial`) are fine — declare them in your package deps
- Lazy-import heavy/optional deps (e.g. `import serial` inside the method that needs it)
- For HTTPS devices with self-signed certificates, use `ionemo_drivers.cert_store.resolve_verify()` in `__init__` — do not implement your own TLS pinning or call `ssl.get_server_certificate()` directly

### Config Schema

- Mark secrets (passwords, API keys) as `type: "password"` — the app encrypts these at rest
- Provide sensible `default` values where possible
- Use `placeholder` for format hints (e.g. "192.168.1.x")

### Serial / RS-485 Port Paths

For serial drivers, **expose the port path as an optional `config_schema()` field**, with a sane
default (e.g. `/dev/ttyUSB0`). Do not hardcode it as a class constant. This used to be the opposite
recommendation — reasoning that "the port is mapped to a fixed path via `docker-compose.override.yml`
and a udev symlink, so users never need to change it" — but that's a fact about one specific
deployment's own Docker device-mapping choice, not a fact this package (a generic, installable
driver library other people's base stations also use) can assume holds everywhere. A different
deployment with a plain device passthrough, or a base station with more than one USB-serial adapter,
needs a different path, and a hardcoded constant gives them no way to fix it short of forking the
driver. See `pv/aurora_rs485.py`'s `port` field (and its `_DEFAULT_PORT` docstring) for the pattern —
found and fixed via a hygiene sweep after it turned out the "fixed" path only ever worked because of
a matching remap in the `ionemo-app` repo's `docker-compose.override.yml`.

### Discovery (optional)

- Override `discover()` if the device supports network scanning (mDNS, HTTP, etc.)
- Return a list of pre-filled config dicts that the user can pick from
- Serial/RS-485 devices typically can't be discovered — leave the default `[]`
- If your driver is LAN-based and uses the shared `lan_scan.scan_subnet()` helper, also override
  `discover_quick()` to forward `quick=True` into your own `scan_subnet()` call — a fast
  host-presence pre-filter then runs before the slow per-address probe, so most of a home network
  (genuinely unused addresses) gets skipped entirely instead of paying its full per-address
  timeout. This is purely additive: `BaseDriver`'s default `discover_quick()` just calls
  `discover()` unchanged, so skipping this override is fine too — see `grid/homewizard_p1.py` or
  `ac/daikin_brp.py` for the pattern if you do want it.

### Setup Guide (optional)

Override `setup_guide()` to return a Markdown string that helps users connect the device. The guide is rendered in the Add Device wizard using **[marked.js](https://marked.js.org/)** with GitHub Flavored Markdown (GFM) support.

**Supported formatting:**

- Headings (`##`, `###`)
- Bold, italic, inline code
- Bullet lists and numbered lists
- GFM tables (`| Col | Col |`)
- Code blocks (fenced with triple backticks)
- Links

**Best practices:**

- Start with a `## Title` heading
- Use `### Numbered Steps` for the installation flow
- Keep it concise — users see this in a scrollable popup, not a full page
- Use tables for pin mappings or parameter lists
- Don't use images (not supported in the popup context)

---

## Testing

Write tests for your driver in `tests/test_{driver_id}.py`. Mock all network/serial I/O.

### Trying your driver against real hardware

`tools/harness.py` runs your driver the way the host application does — discovery, config
schema, construction, then polled reads — without needing the application itself:

```bash
python tools/harness.py list                       # every driver the registry can see
python tools/harness.py schema my_driver           # the fields the Add Device wizard renders
python tools/harness.py discover my_driver         # your discover(), as the wizard calls it
python tools/harness.py poll my_driver --config ip=192.168.1.50
```

`poll` is the one that matters. It is the loop the app's scheduler runs, and it checks every
reading against the published data contract for your device type, so it catches the common case
of a driver that talks to its hardware correctly but returns the wrong shape — a missing required
key, or `None` where a real number is required. It also warns when a read exceeds the app's
`DRIVER_CALL_TIMEOUT` budget, which is the difference between "works on my bench" and "times out
in the app".

The harness only prints what your driver returns. There is deliberately no dashboard, storage or
unit conversion in it — those belong to the app, and a test tool that reformats your data cannot
show you truthfully what your driver actually produced.

**Never use real captured data from your own device — always fabricate example data shaped to
match the protocol.** This isn't just a privacy rule (see [SECURITY.md](SECURITY.md) §1.4) — a
driver written and tested against your one specific unit's real responses tends to quietly assume
that unit's firmware version, region, or configuration, which then breaks for anyone else with the
same device. Testing against fabricated data that matches the documented format keeps the driver
correct for the whole device family, not just the copy you own.

```python
from unittest.mock import patch
from ionemo_drivers.grid.my_device import MyDeviceDriver

def test_get_data_success():
    driver = MyDeviceDriver({"ip": "192.168.1.100"})
    with patch("requests.get") as mock_get:
        mock_get.return_value.json.return_value = {...}
        mock_get.return_value.status_code = 200
        data = driver.get_data()
    assert data is not None
    assert data["grid_power_w"] == 450.0
    assert data["import_total_kwh"] > 0

def test_get_data_failure():
    driver = MyDeviceDriver({"ip": "192.168.1.100"})
    with patch("requests.get", side_effect=ConnectionError):
        data = driver.get_data()
    assert data is None
    assert driver.last_error is not None
```

---

## Checklist Before Submitting

- [ ] `driver_id` is unique and snake_case
- [ ] `name` / `manufacturer` are real product/brand names
- [ ] `device_type` and `connection_type` use the enums
- [ ] `get_data()` returns the correct TypedDict with all required fields
- [ ] `get_data()` returns `None` on any failure (never raises)
- [ ] `config_schema()` marks passwords as `type: "password"`
- [ ] No imports from `src/` or `config/`
- [ ] No real serial numbers, MAC addresses, device/room names, or deployment IPs anywhere in the
      diff — test fixtures use fabricated data shaped to match the protocol, not a real capture
- [ ] Tests pass with mocked I/O
- [ ] `basedpyright` reports 0 errors (CI enforces this — see Prerequisites for the install command)

---

## Submitting Your Driver

1. **Fork this repo**, add your driver on a branch (`ionemo_drivers/{type}/my_device.py`
   for a builtin-style addition — see Step 5 — or your own separate package for an external one, see
   Step 6), and open a pull request against `main`.
2. **CI runs automatically** on every PR: `tests/test_contract_compliance.py` (structural checks —
   identity attributes, method signatures, ABC hierarchy) and `tests/test_security_compliance.py`
   (static analysis against [SECURITY.md](SECURITY.md)'s rules — forbidden imports/calls, credential
   logging, missing timeouts, outbound-internet calls, etc.). Both must pass before review.
3. **A maintainer may request an AI review** by adding the `ai-review` label, which runs
   `.github/agents/driver-reviewer.agent.md` against the diff and posts the result as a PR
   comment (`.github/workflows/driver-review.yml`). It covers what static analysis cannot —
   data contract correctness against the relevant `docs/contracts/{device_type}.md`,
   `discover()`/`get_data()` never raising, whether warnings are actually useful to a
   non-technical person.

   **It is advisory, not a gate, and it does not run automatically.** Two reasons, both
   deliberate. This repository is public and takes contributions from forks: a fork's PR gets
   no repository secrets, so an automatic API-key review would silently do nothing in exactly
   the case it exists for, and the trigger that *does* get secrets
   (`pull_request_target`) combined with untrusted code is a well-known way to get a repo
   compromised. And the model reads attacker-controllable diff text, so it can be talked out of
   reporting something. A maintainer applying a label is a person deciding to run it on a
   specific PR; the result informs their read rather than replacing it.

   If no `ANTHROPIC_API_KEY` secret is configured the job skips with a notice, and the
   deterministic suites in step 2 still gate the PR as normal.
4. **A maintainer does the final review** — CI passing is necessary, not sufficient; a human
   still confirms the driver is safe and correct before merging, especially
   for anything the static checks structurally can't verify. This repo's own tests mock all
   network/serial I/O by design (see "Testing" above), so nothing here ever runs a driver against a
   real device — that only happens if the maintainer owns matching hardware, by temporarily pointing
   the main app's **ACC** deployment (never production) at the PR's branch or commit:
   ```
   git+https://github.com/H20one/ionemo-drivers.git@<branch-or-commit-sha>#egg=ionemo-drivers
   ```
   in `requirements.txt` on `ionemo-app`'s `acceptance` branch, redeploying, and confirming
   discover()/get_data()/the setters behave correctly against the live device. That pin gets
   reverted to a released tag immediately after — it's a one-off validation step that stays on
   `acceptance`, never something that reaches `ionemo-app`'s `main` branch; see that repo's own
   `requirements.txt` rule ("never an unpinned branch, for reproducible builds") for why this is a
   temporary exception, not a contradiction of it. For a driver whose hardware the maintainer doesn't
   have, this step isn't possible; review then relies more on a careful read of the vendor's
   protocol docs and the contributor's own testing.
5. Once merged, a builtin-style driver ships in the next tagged release of this package (see
   [CHANGELOG.md](CHANGELOG.md)) — `ionemo-app` picks it up when it bumps its pinned dependency
   version. An external (pip-installed, entry-point-based) driver is independent of this repo's
   release cycle entirely — it's discovered at runtime via `importlib.metadata.entry_points()`, so it
   ships whenever *you* publish your own package.
