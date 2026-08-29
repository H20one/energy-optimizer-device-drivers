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

**Be precise about what this buys and doesn't buy** (this came up directly during the split's design
discussion, worth restating here so it isn't re-litigated from scratch later): this repo gives
**complete separation of logic and contract** — `base.py` is the only thing either side knows about
the other; a driver never sees anything about Flask, SQLite, encryption, or scheduling, and the main
app never reaches into a specific driver's internals. That was already true before the split (drivers
already only imported from `base.py`) and stays true now. What it does **not** give is separation of
**runtime trust** — once installed, this package's code executes with the exact same OS-level
privileges as the rest of the main app's process, same as before the split. `SECURITY.md`'s rules
(no imports from the app's `src`/`config`, no outbound internet, etc.) are enforced by static
analysis (`tests/test_security_compliance.py`), not by any process wall — a genuinely malicious
driver could still violate them if it tried hard enough. The static checks and PR review are the
actual defense here, not an OS boundary.

## The contract (`energy_optimizer_drivers/base.py`)

The **only** shared dependency between this repo and the main app. A driver:
- receives a plain `config: dict` in `__init__` (the user's settings from the Add Device wizard,
  decrypted by the main app — this package never sees the encryption layer),
- returns a plain `TypedDict` from `get_data()` matching its device type's contract
  (`docs/contracts/{device_type}.md`),
- never raises from `get_data()`/`discover()`/any setter — always catches internally and returns
  `None`/`False`/an empty result instead.

Four device types exist today (`GRID_METER`, `PV_INVERTER`, `EV_CHARGER`, `AC_UNIT`), each with its
own ABC in `base.py` and contract doc. Adding a fifth requires a deliberate change to both, not a
workaround in a single driver — see `CONTRIBUTING.md`.

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

- No deployment, no Dockerfile, no running service — see `CLAUDE.md`.
- No knowledge of how device config is stored or encrypted at rest (that's entirely the main app's
  concern — `src/devices/__init__.py`'s Fernet encryption, PBKDF2 key derivation, etc.).
- No knowledge of scheduling/polling cadence — the main app's scheduler decides how often
  `get_data()` gets called; this package just needs to answer within its 15-second contract whenever
  it's asked.
- No cross-driver coordination — each driver is fully independent; one driver's failure/timeout
  never affects another's ability to be polled.
