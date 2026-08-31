# Changelog

All notable changes to the device drivers package are documented here.

## 0.1.13 — 2026-09-01

### Changed
- Rebranded from "Energy Optimizer" to **Ionemo** everywhere it appeared: `README.md`,
  `CONTRIBUTING.md`, `ARCHITECTURE.md`, `pyproject.toml`'s description, the package's own
  `__init__.py` docstring, and every built-in driver's setup guide / discovery-warning text
  (`homewizard_p1.py`, `aurora_rs485.py`, `alfen_eve.py`, `daikin_brp.py`) and matching
  `docs/contracts/*.md` / `docs/drivers/*.md` examples — these strings are user-facing (shown in
  the Add Device wizard), not just internal docs. Warnings about the physical device/hardware now
  say "Ionemo base" and warnings about the software say "Ionemo", per the naming convention decided
  alongside the rebrand (see `energy-optimizer`'s `docs/IMPROVEMENT_ROADMAP.md` N33).

## 0.1.12 — 2026-08-29

### Added
- `basedpyright` now actually runs in CI (`.github/workflows/ci.yml`) — previously it was only a
  checklist item in `CONTRIBUTING.md` ("reports 0 errors"), unenforced and easy to skip.
- `requirements-dev.txt` — this repo had no shared, versioned dev-tooling file at all; CI installed
  `pytest`/`pytest-cov`/`ruff` as loose unpinned lines directly in the workflow YAML, with nothing a
  contributor could install from to reliably match CI locally. Now `pip install -r
  requirements-dev.txt` gets the exact same `ruff`/`basedpyright`/`pytest` versions CI uses, and
  `pyrightconfig.json` (already committed) applies automatically once `basedpyright` is installed —
  same config, not just the same tool name.
- `CONTRIBUTING.md`'s Prerequisites section now gives the install command explicitly; the
  `basedpyright` checklist item now says CI enforces it instead of implying it's honor-system.
- `SECURITY.md`'s "Enforcement" section now lists `basedpyright` among what CI actually runs.

## 0.1.11 — 2026-08-29

### Changed
- `cert_store.resolve_verify()`/`_pinned_path()` take a new `prefix` parameter (defaulting to
  `"device"`) instead of hardcoding `"alfen_"` — this module is shared TOFU-pinning infrastructure
  for any HTTPS driver, not Alfen-specific, and the pinned filename should say which driver a cert
  belongs to. `alfen_eve.py` now passes its own `driver_id`. Not a collision fix (the IP already
  makes filenames unique) — a clarity fix, since a future second HTTPS driver's pinned certs would
  otherwise be filed under a misleading `alfen_*` name.
- Added `tests/test_cert_store.py` — this module had no dedicated tests at all before now.

**Operational note**: any already-deployed Alfen charger's pinned cert (`data/certs/alfen_<ip>.pem`)
won't match the new expected filename (`alfen_eve_<ip>.pem`) and will be silently re-pinned via TOFU
on the next connection — automatic, harmless, but a real filesystem change worth knowing about
before this version reaches a live install.

## 0.1.10 — 2026-08-29

### Fixed
- `validate_contract_data()` typed its `data` parameter as `dict[str, Any]`, but a
  `GridMeterData`/etc. TypedDict is not statically assignable to `dict[str, Any]` (TypedDicts
  aren't subtypes of `dict` under static type checking, due to mutability/invariance) — every
  call site passing a driver's real `get_data()` result was a type error. Changed to
  `Mapping[str, Any]`, which a TypedDict does satisfy. Also fixed several test-file call sites
  that indexed an optional TypedDict field directly (`data["gas_total_m3"]`) instead of `.get(...)`
  — valid at runtime here since this repo's convention always sets optional fields to `None`
  rather than omitting them, but not something a type checker can know, so it flagged them as
  potentially-absent-key accesses.
- Added `pyrightconfig.json` (`typeCheckingMode: "standard"`, matching `energy-optimizer`'s own) —
  `basedpyright` previously had no config here and ran under its much stricter default preset,
  which is not what `CONTRIBUTING.md`'s "0 errors" checklist item was ever measured against. Under
  standard mode this repo is genuinely 0 errors/0 warnings.

## 0.1.9 — 2026-08-29

### Added
- `tests/test_public_api_stability.py` — freezes the exact things
  `ARCHITECTURE.md`'s "Changes that need a maintainer" section warns about (ABC method signatures,
  `DRIVER_CALL_TIMEOUT`, the `energy_optimizer.drivers` entry-point group name) so a change to any of
  them fails this repo's own CI immediately, intentional or not, instead of only being caught by
  someone reading the doc. Deliberately narrow — it does not and cannot catch every way a change here
  could break the main app (that would mean running that private repo's test suite against this
  code, which isn't set up); see the file's docstring for the exact boundary.

### Changed
- `ARCHITECTURE.md`'s "Why a separate repo" section no longer frames the design as a choice between
  two options — it explains the implemented in-process-package approach directly. The rejected
  network-service alternative added length without changing anything about how this repo actually
  works.
- Each item in "Changes that need a maintainer" now says explicitly whether it's checked by
  `test_public_api_stability.py` or not (and why, for the two that aren't) — previously the section
  asserted these were breaking changes without saying whether anything actually verified that.
- Moved the RS-485 USB passthrough note into "What this repo deliberately does not do" (as a concrete
  example under the existing "no deployment" point) and named the specific driver it explains
  (`aurora_rs485.py`'s fixed `/dev/ttyUSB1` path) — previously it sat disconnected from any driver
  under "How the main app consumes this package," without saying which driver it was about.

## 0.1.8 — 2026-08-29

### Added
- `tests/test_homewizard_p1.py` — the last builtin driver without its own behavior test file
  (flagged as a known gap in 0.1.7). Covers `HomewizardP1Driver.get_data()`/`get_status()` (including
  single-phase/no-gas responses where optional fields are legitimately absent) and `discover()`'s
  network-scan orchestration (device found, none found, local-IP lookup failure). All response
  bodies are fabricated, matching the format used throughout this repo's other driver tests.
  `_probe_homewizard`'s own identity-extraction logic was already covered separately in
  `test_driver_discover_identity.py` and isn't duplicated here. Every `get_data()` success-path test
  also asserts `validate_contract_data(DeviceType.GRID_METER, data) == []`, so all four builtin
  drivers are now exercised against the runtime contract check added in 0.1.7.

## 0.1.7 — 2026-08-29

### Added
- New `energy_optimizer_drivers.contract_validation.validate_contract_data()` — checks a driver's
  returned dict against its `docs/contracts/{device_type}.md` data contract **at runtime**: every
  required field present and non-None, no unexpected keys, and correct types throughout. Until now,
  the required/optional split in `base.py`'s `GridMeterData`/`PVInverterData`/`EVChargerData`/
  `ACUnitData` TypedDicts was comment-only — Python erases `TypedDict` at runtime, so nothing
  actually checked a driver's real output against it, and this repo doesn't run a static type
  checker in CI either. This only covers the generic, mechanically-checkable part of each contract
  (structure and type) — semantic rules like "single-phase meters must report L2/L3 as `None`, not
  `0.0`" still need device-specific judgment and stay policy-only, same as `SECURITY.md` §1.4.
- `base.py`'s data TypedDicts now mark each required field with `Required[...]` instead of a
  comment, so the distinction is introspectable at runtime (`__required_keys__`) — what
  `validate_contract_data()` reads. Not a breaking change: the field set and types are unchanged.
- Wired the new check into the existing mocked `get_data()` tests for the three drivers that have
  behavior test suites (`test_daikin_brp.py`, `test_alfen_driver.py`, `test_aurora_driver.py`).
  `homewizard_p1` has no dedicated behavior test file yet — a pre-existing gap, not introduced or
  closed by this change — so it isn't covered by this check either, for now.
- `docs/contracts/*.md` each cross-reference `validate_contract_data()` under their required/optional
  field tables, so the doc and the code enforcing it point at each other.

## 0.1.6 — 2026-08-29

### Changed
- `CLAUDE.md` has been purged entirely from git history, not just untracked going forward (as of
  0.1.5). It contained no secrets or private data — this was done purely so the repo's history
  doesn't carry a file that's no longer part of the public repo, not a security response. **Every
  commit and tag in this repo was rewritten as a result** — if you cloned this repo before this
  release, discard that clone and re-clone; the old history is no longer compatible with what's on
  the remote.

## 0.1.5 — 2026-08-29

### Changed
- `CLAUDE.md` (AI-assisted development working notes) is no longer tracked in git — added to
  `.gitignore`. Its content was purely internal process notes (versioning reminders, cross-repo
  coordination rules) that consistently pointed to `ARCHITECTURE.md`/`SECURITY.md`/`CONTRIBUTING.md`
  rather than duplicating them, so contributors lose nothing by it not being in the repo.
  `.github/agents/driver-reviewer.agent.md` is unaffected and remains tracked as-is — it's an active
  part of the documented PR review process (see `SECURITY.md`'s "Enforcement"), not internal-only
  notes, and removing it would actually remove a piece of process this repo's own docs describe.
- `ARCHITECTURE.md`'s "What this repo deliberately does not do" no longer points to `CLAUDE.md` for
  the no-deployment note, since that file is no longer visible to contributors — the point is stated
  inline instead.

## 0.1.4 — 2026-08-29

### Fixed
- `SECURITY.md`'s "Enforcement" section overstated what's actually automated. The
  `driver-reviewer.agent.md` checklist is **not** wired into CI or any GitHub Actions workflow —
  it only applies when a human or AI assistant is deliberately asked to use it, despite previously
  being described as "the automated driver reviewer agent." Corrected to clearly separate what
  `.github/workflows/ci.yml` actually runs on every push/PR (ruff + the two pytest compliance
  suites) from what requires someone to actively invoke it. Also explicitly annotated the new §1.4
  rule (no real device data) as policy-only — it's not just currently uncovered by automation, it
  fundamentally cannot be: no static check can distinguish a fabricated hex string from a real one.

## 0.1.3 — 2026-08-29

### Added
- New zero-tolerance rule, `SECURITY.md` §1.4: no real device data (serial numbers, MAC addresses,
  device/room names, deployment IPs) may ever be committed anywhere in this repo, including test
  fixtures — fabricated data shaped to match the protocol only. This is a correctness rule as much
  as a privacy one: a driver written against one real device's actual responses tends to quietly
  assume that unit's specific firmware/region/config, which then doesn't generalize to the rest of
  the device family it's supposed to support. Cross-referenced from `CONTRIBUTING.md`'s testing
  section and checklist, `CLAUDE.md`, and the driver-reviewer agent's zero-tolerance list.

## 0.1.2 — 2026-08-29

### Security
- **Removed real device data (a device serial number, a device MAC address, and a real room name)
  that had been accidentally committed in a test fixture, mislabeled as "real payloads captured
  live" — replaced with clearly fabricated example data.** If you cloned this repo before this
  release, that data is present in your local copy's history; please discard that clone.

### Changed
- Full documentation pass for public readability: removed several references to internal-only
  tracking codes and non-public documents that a reader outside the project has no way to resolve,
  clarified collaborator-facing phrasing into plain documentation, and fixed a number of file paths
  left over from the original extraction that pointed at a directory structure this repo doesn't
  actually have.
- Added an explicit, consolidated list of changes that require maintainer coordination before a PR
  will be considered (new device types, ABC signature changes, contract constants, the entry-point
  group name) — previously only "you can't add a device type" was stated, with no explanation of why
  or what to do instead. See `ARCHITECTURE.md`'s "Changes that need a maintainer, not just a PR".

## 0.1.1 — 2026-08-29

### Added
- Per-driver behavior test suites moved over from `energy-optimizer`'s `tests/`
  (`test_alfen_driver.py`, `test_aurora_driver.py`, `test_daikin_brp.py`,
  `test_driver_discover_identity.py`) — these test driver internals directly and belong here now,
  not in the app repo testing code that no longer lives there.

### Fixed
- Added missing `__init__.py` to each device-type subpackage (`grid/`, `pv/`, `ev/`, `ac/`) and to
  `tests/` — present in the original `drivers/` tree but missed in the initial 0.1.0 extraction.
  Imports worked anyway via Python's implicit namespace packages, but this matches the original
  structure exactly rather than relying on that.

## 0.1.0 — 2026-08-29

### Added
- Initial extraction from `energy-optimizer`'s `drivers/` directory into this standalone,
  pip-installable package — no driver logic changed, only the import path (`drivers.*` →
  `energy_optimizer_drivers.*`) and packaging.
- Four builtin drivers: `homewizard_p1` (grid meter), `aurora_rs485` (PV inverter, RS-485), `alfen_eve`
  (EV charger), `daikin_brp` (AC unit).
- CI runs `test_contract_compliance.py` + `test_security_compliance.py` on every push/PR.
