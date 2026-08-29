# Changelog

All notable changes to the device drivers package are documented here.

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
  pip-installable package (N25 in `energy-optimizer`'s roadmap) — no driver logic changed, only the
  import path (`drivers.*` → `energy_optimizer_drivers.*`) and packaging.
- Four builtin drivers: `homewizard_p1` (grid meter), `aurora_rs485` (PV inverter, RS-485), `alfen_eve`
  (EV charger), `daikin_brp` (AC unit).
- CI runs `test_contract_compliance.py` + `test_security_compliance.py` on every push/PR.
