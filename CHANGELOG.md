# Changelog

All notable changes to the device drivers package are documented here.

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
  live" — replaced with clearly fabricated example data.** If you cloned this repo at `v0.1.0` or
  `v0.1.1`, that data is present in your local copy's history; please discard those clones. See the
  project's own advisory/release notes for this version for further detail.

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
