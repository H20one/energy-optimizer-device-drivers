"""Runtime validation of driver-returned data against its TypedDict contract.

`GridMeterData`/`PVInverterData`/`EVChargerData`/`ACUnitData` in `base.py` are
`TypedDict`s — a purely static-typing construct. Python erases them at runtime,
so nothing stops a driver's `get_data()` from actually returning a dict that's
missing a required key or has the wrong type in one; a static type checker
would catch that, but this repo doesn't run one in CI, and even one only
checks the code that *constructs* the dict, not a value produced dynamically
from a parsed device response.

`validate_contract_data()` closes that gap by checking an actual returned
dict against its contract at runtime, using the `Required[...]` markers in
`base.py` (via `TypedDict.__required_keys__`/`__optional_keys__`) and each
field's declared type. It's meant to be called from driver test suites right
after a mocked `get_data()` call — see `tests/test_daikin_brp.py`,
`tests/test_alfen_driver.py`, and `tests/test_aurora_driver.py` for examples.

This only checks structure and type — the generic, mechanically-checkable
part of each docs/contracts/*.md file. It does NOT check semantic rules like
"single-phase meters must report L2/L3 as None, not 0.0" — those need
device-specific knowledge and stay policy-only, enforced by review, same as
SECURITY.md §1.4.
"""

import types
from typing import Any, Union, get_type_hints

from .base import ACUnitData, DeviceType, EVChargerData, GridMeterData, PVInverterData

_CONTRACT_BY_DEVICE_TYPE: dict[DeviceType, type] = {
    DeviceType.GRID_METER: GridMeterData,
    DeviceType.PV_INVERTER: PVInverterData,
    DeviceType.EV_CHARGER: EVChargerData,
    DeviceType.AC_UNIT: ACUnitData,
}


def validate_contract_data(device_type: DeviceType, data: dict[str, Any]) -> list[str]:
    """Check `data` against the TypedDict data contract for `device_type`.

    Returns a list of human-readable violation descriptions — empty if `data`
    fully complies. Never raises on a non-compliant `data`; callers decide
    whether to assert/fail on a non-empty result.
    """
    contract = _CONTRACT_BY_DEVICE_TYPE[device_type]
    required_keys = contract.__required_keys__
    optional_keys = contract.__optional_keys__
    hints = get_type_hints(contract)

    violations = []

    unknown_keys = set(data) - required_keys - optional_keys
    if unknown_keys:
        violations.append(f"unexpected key(s) not in the {contract.__name__} contract: {sorted(unknown_keys)}")

    for key in sorted(required_keys):
        if key not in data:
            violations.append(f"missing required key {key!r}")
        elif data[key] is None:
            violations.append(f"required key {key!r} must not be None")
        elif not _matches_type(data[key], hints[key]):
            violations.append(
                f"required key {key!r} expected {hints[key]}, got {type(data[key]).__name__}"
            )

    for key in sorted(optional_keys):
        if key in data and data[key] is not None and not _matches_type(data[key], hints[key]):
            violations.append(
                f"optional key {key!r} expected {hints[key]}, got {type(data[key]).__name__}"
            )

    return violations


def _matches_type(value: Any, expected: Any) -> bool:
    """Best-effort runtime type check, handling `X | None` unions."""
    origin = getattr(expected, "__origin__", None)
    if origin is Union or isinstance(expected, types.UnionType):
        members = getattr(expected, "__args__", ())
        return any(_matches_type(value, member) for member in members if member is not type(None))
    if expected is float:
        # bool is technically an int subclass in Python; never accept it for a numeric field.
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected is int:
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, expected)
