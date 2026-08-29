# Changelog

All notable changes to the device drivers package are documented here.

## 0.1.0 — 2026-08-29

### Added
- Initial extraction from `energy-optimizer`'s `drivers/` directory into this standalone,
  pip-installable package (N25 in `energy-optimizer`'s roadmap) — no driver logic changed, only the
  import path (`drivers.*` → `energy_optimizer_drivers.*`) and packaging.
- Four builtin drivers: `homewizard_p1` (grid meter), `aurora_rs485` (PV inverter, RS-485), `alfen_eve`
  (EV charger), `daikin_brp` (AC unit).
- CI runs `test_contract_compliance.py` + `test_security_compliance.py` on every push/PR.
