"""Locks down the exact surface the main `ionemo-app` depends on.

That app is a separate, private repo — nothing here can run its test suite
directly, so this file is the next best thing for the four specific breaking
changes ARCHITECTURE.md's "Changes that need a maintainer, not just a PR"
warns about: an ABC method signature change, a changed `DRIVER_CALL_TIMEOUT`,
and a renamed entry-point group. Each test below freezes one of those and
will fail the moment it changes — deliberately or by accident — forcing
whoever changed it to notice and re-read that section before merging.

What this file does NOT do: catch every possible way a change here could
break the main app. It only covers the specific, named things ARCHITECTURE.md
already calls out. A truly exhaustive answer to "did this change break the
main app" would mean running that app's own test suite against this code —
which isn't set up (it would mean a public repo's CI checking out a private
one), not "already covered, just not mentioned."
"""

import inspect
from unittest.mock import patch

from ionemo_drivers import registry
from ionemo_drivers.base import (
    DRIVER_CALL_TIMEOUT,
    ACUnitDriver,
    BaseDriver,
    EVChargerDriver,
    GridMeterDriver,
    PVInverterDriver,
)


def _params(func) -> list[tuple[str, str]]:
    return [(p.name, p.kind.name) for p in inspect.signature(func).parameters.values()]


class TestBaseDriverSignatures:
    """The main app calls these by exact name and signature — a mismatch
    breaks every existing driver from the app's side, not just yours."""

    def test_config_schema_signature(self) -> None:
        assert _params(BaseDriver.config_schema) == []

    def test_discover_signature(self) -> None:
        assert _params(BaseDriver.discover) == []

    def test_get_status_signature(self) -> None:
        assert _params(BaseDriver.get_status) == [("self", "POSITIONAL_OR_KEYWORD")]
        assert inspect.signature(BaseDriver.get_status).return_annotation is str

    def test_last_error_is_a_property(self) -> None:
        assert isinstance(inspect.getattr_static(BaseDriver, "last_error"), property)


class TestDeviceTypeGetDataSignatures:
    def test_get_data_takes_only_self(self) -> None:
        for cls in (GridMeterDriver, PVInverterDriver, EVChargerDriver, ACUnitDriver):
            assert _params(cls.get_data) == [("self", "POSITIONAL_OR_KEYWORD")]


class TestEVChargerSetterSignatures:
    def test_set_current_signature(self) -> None:
        assert _params(EVChargerDriver.set_current) == [
            ("self", "POSITIONAL_OR_KEYWORD"),
            ("amps", "POSITIONAL_OR_KEYWORD"),
        ]
        assert inspect.signature(EVChargerDriver.set_current).return_annotation is bool


class TestACUnitSetterSignatures:
    def test_set_mode_signature(self) -> None:
        assert _params(ACUnitDriver.set_mode) == [
            ("self", "POSITIONAL_OR_KEYWORD"),
            ("mode", "POSITIONAL_OR_KEYWORD"),
        ]

    def test_set_temperature_signature(self) -> None:
        assert _params(ACUnitDriver.set_temperature) == [
            ("self", "POSITIONAL_OR_KEYWORD"),
            ("temp_c", "POSITIONAL_OR_KEYWORD"),
        ]

    def test_set_fan_speed_signature(self) -> None:
        assert _params(ACUnitDriver.set_fan_speed) == [
            ("self", "POSITIONAL_OR_KEYWORD"),
            ("speed", "POSITIONAL_OR_KEYWORD"),
        ]

    def test_all_three_setters_return_bool(self) -> None:
        for name in ("set_mode", "set_temperature", "set_fan_speed"):
            sig = inspect.signature(getattr(ACUnitDriver, name))
            assert sig.return_annotation is bool


class TestDriverCallTimeout:
    """The main app's polling/scheduling logic is tuned around this exact
    value. If you're deliberately changing it, update this test too — but
    read ARCHITECTURE.md's "Changes that need a maintainer" first."""

    def test_driver_call_timeout_value(self) -> None:
        assert DRIVER_CALL_TIMEOUT == 15


class TestEntryPointGroupName:
    """External driver packages register under this exact string
    (`ionemo.drivers`) via their own pyproject.toml. Renaming it
    silently breaks discovery for every external driver, including the main
    app's own — nothing would raise, they'd just stop being found."""

    def test_external_drivers_load_from_the_documented_group_name(self) -> None:
        with patch("ionemo_drivers.registry.entry_points") as mock_entry_points:
            mock_entry_points.return_value = []
            registry._load_external_drivers()

        mock_entry_points.assert_called_once_with(group="ionemo.drivers")
