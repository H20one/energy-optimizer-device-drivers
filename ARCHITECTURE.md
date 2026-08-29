# Architecture

## What this is

A pip-installable Python package of device driver plugins for
[energy-optimizer](https://github.com/H20one/energy-optimizer), a self-hosted home-energy dashboard.
Each driver adapts one device's protocol (HTTP, HTTPS, or RS-485 serial) into a fixed data contract
the main app understands.

## Why a separate repo, and what kind of split this actually is

Two things motivated pulling this out of the main app's repo: making the driver layer genuinely
public/open to outside contributors without exposing any of the main app's own code, and giving
drivers their own release cycle independent of the dashboard app.

There were two ways to do that split, and it's worth being precise about which one this is, since
they give very different guarantees:

- **A true network-service split** — drivers run as their own process/container, and the main app
  calls them over HTTP instead of importing Python classes. This gives real OS-level process
  isolation: a driver literally cannot touch the main app's memory, database, or secrets, even if it
  tried. It was considered and rejected for this project specifically because drivers are polled
  constantly (every 5–10 seconds per device, with the RS-485 driver alone issuing ~8 serial commands
  per poll) — turning every one of those into a network round-trip, redesigning timeout/error
  handling around network fallibility, and moving RS-485 USB passthrough to a new container was a
  real cost with no benefit for this project's actual scale.
- **This repo: a separate installable package, still running in-process.** The main app `pip
  install`s a pinned version of this package and calls drivers exactly as it always has — a plain
  Python method call, zero latency change, zero new failure mode. This is what's actually running.

**What this design does and doesn't guarantee:** this repo gives **complete separation of logic and
contract** — `base.py` is the only thing either side knows about the other; a driver never sees
anything about Flask, SQLite, encryption, or scheduling, and the main app never reaches into a
specific driver's internals. What it does **not** give is separation of **runtime trust** — once
installed, this package's code executes with the exact same OS-level privileges as the rest of the
main app's process. `SECURITY.md`'s rules (no imports from the app's internal code, no outbound
internet, etc.) are enforced by static analysis (`tests/test_security_compliance.py`), not by any
process wall — a driver that deliberately tried to violate them could still succeed. The static
checks and PR review are the actual defense here, not an OS-level boundary. Keep this in mind when
writing a driver: the checks catch common mistakes, but review and the contract itself are what
actually keep this safe to depend on.

## The contract (`energy_optimizer_drivers/base.py`)

The **only** shared dependency between this repo and the main app. A driver:
- receives a plain `config: dict` in `__init__` (the user's settings from the Add Device wizard,
  decrypted by the main app — this package never sees the encryption layer),
- returns a plain `TypedDict` from `get_data()` matching its device type's contract
  (`docs/contracts/{device_type}.md`),
- never raises from `get_data()`/`discover()`/any setter — always catches internally and returns
  `None`/`False`/an empty result instead.

Four device types exist today (`GRID_METER`, `PV_INVERTER`, `EV_CHARGER`, `AC_UNIT`), each with its
own ABC in `base.py` and contract doc. **A new device type cannot be added by a contributor alone —
it always needs a maintainer first.** See "Changes that need a maintainer, not just a PR" below for
why and how to request one.

## Changes that need a maintainer, not just a PR

Most of this repo is safe to change freely within a driver you're adding or fixing. A specific,
narrow set of changes reaches outside this repo into the main `energy-optimizer` app in ways a
contributor here has no visibility into — opening a PR for any of these without discussing it first
will very likely get closed, not merged silently:

- **Adding a new `DeviceType`** (`base.py`). The main app has hand-written support for each existing
  type — a dedicated typed accessor, scheduler polling job, and UI card — none of which lives in
  this repo or updates itself. A new type here with no matching support there does nothing.
- **Changing an ABC's method signature** in `base.py` (`get_data()`, `discover()`, `set_current()`,
  the AC setters, etc.). The main app calls these by exact name and signature; a mismatched change
  breaks every existing driver from the app's side, not just yours.
- **Changing `DRIVER_CALL_TIMEOUT`** or any other contract-level constant in `base.py`. The main
  app's own polling/scheduling logic is tuned around this value.
- **Renaming the `energy_optimizer.drivers` entry-point group** (`registry.py`) that external
  third-party driver packages register under. Renaming it silently breaks discovery for every
  external driver, including the main app's own.
- **Renaming the top-level `energy_optimizer_drivers` package.**

If you need one of these — most commonly a new device type — open an issue describing the device
and why it doesn't fit an existing type before writing any code. A maintainer needs to plan the
matching main-app change before a new type here is useful for anything.

Everything else — a new driver for an existing device type, a bug fix, improving `discover()` for an
existing driver, adding tests — is a normal PR, no separate discussion needed.

## How the main app consumes this package

`energy-optimizer`'s `requirements.txt` pins a released tag of this repo
(`git+https://github.com/H20one/energy-optimizer-device-drivers.git@vX.Y.Z`) — never an unpinned
branch, for reproducible builds. At startup, `energy_optimizer_drivers.registry.load_all_drivers()`
imports the four builtin driver modules (each registers itself at import time) and separately
discovers any externally pip-installed third-party drivers via the `energy_optimizer.drivers` entry
point group — a driver author doesn't have to get merged into this repo at all to work with the main
app; they can ship and version their own package independently.

The RS-485 USB passthrough (`/dev/ttyUSB0` → `/dev/ttyUSB1`, `dialout` group membership) lives
entirely in the main app's `Dockerfile`/`docker-compose.override.yml`, not here — this package is
just Python code installed into that same container; it has no deployment or hardware-passthrough
concerns of its own.

## What this repo deliberately does not do

- No deployment, no Dockerfile, no running service — this package is pure Python, installed
  in-process into the main app's container.
- No knowledge of how device config is stored or encrypted at rest (that's entirely the main app's
  concern — `src/devices/__init__.py`'s Fernet encryption, PBKDF2 key derivation, etc.).
- No knowledge of scheduling/polling cadence — the main app's scheduler decides how often
  `get_data()` gets called; this package just needs to answer within its 15-second contract whenever
  it's asked.
- No cross-driver coordination — each driver is fully independent; one driver's failure/timeout
  never affects another's ability to be polled.
