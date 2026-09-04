"""Tests for ionemo_drivers/registry.py.

DRIVER_REGISTRY is a module-level global, and `_load_builtin_drivers()` calls
`importlib.import_module()`, which is a no-op for a module already in
`sys.modules` -- it does NOT re-run that module's top-level `register_driver()`
call. Since some driver module is virtually guaranteed to already be imported
by the time this file runs (this repo's own test suite imports several
directly), tests that need to observe *fresh* registration mock
`importlib.import_module` itself rather than relying on real (re-)imports.
Tests that just want "the real registry has these drivers" call
`_load_builtin_drivers()` against the real, unpatched `DRIVER_REGISTRY` --
correct whether that's the first call in the process or a no-op repeat.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from ionemo_drivers import registry
from ionemo_drivers.base import BaseDriver, DeviceType
from ionemo_drivers.registry import (
    _load_builtin_drivers,
    _load_external_drivers,
    get_driver,
    get_drivers_for_type,
    load_all_drivers,
    register_driver,
)


class _DummyDriver(BaseDriver):
    """A minimal stand-in class -- never instantiated, only used as a registry value."""


class TestRegisterAndGetDriver:
    def test_round_trips_through_the_registry(self, monkeypatch) -> None:
        monkeypatch.setattr(registry, "DRIVER_REGISTRY", {})
        register_driver("dummy", _DummyDriver)
        assert get_driver("dummy") is _DummyDriver

    def test_unknown_name_returns_none(self, monkeypatch) -> None:
        monkeypatch.setattr(registry, "DRIVER_REGISTRY", {})
        assert get_driver("nope") is None

    def test_registering_the_same_name_twice_overwrites(self, monkeypatch) -> None:
        monkeypatch.setattr(registry, "DRIVER_REGISTRY", {})

        class _Other(BaseDriver):
            pass

        register_driver("dummy", _DummyDriver)
        register_driver("dummy", _Other)
        assert get_driver("dummy") is _Other


class TestGetDriversForType:
    def test_returns_the_real_builtin_grid_meter_driver(self) -> None:
        # _load_builtin_drivers() is idempotent against the real registry --
        # correct whether some other test already triggered it or not.
        _load_builtin_drivers()
        drivers = get_drivers_for_type(DeviceType.GRID_METER)
        assert "homewizard_p1" in drivers

    def test_only_returns_drivers_matching_the_requested_type(self) -> None:
        _load_builtin_drivers()
        grid_drivers = get_drivers_for_type(DeviceType.GRID_METER)
        ev_drivers = get_drivers_for_type(DeviceType.EV_CHARGER)
        assert "alfen_eve" not in grid_drivers
        assert "alfen_eve" in ev_drivers

    def test_unknown_device_type_returns_empty_dict(self) -> None:
        assert get_drivers_for_type("not_a_real_device_type") == {}

    def test_empty_registry_returns_empty_dict_for_a_valid_type(self, monkeypatch) -> None:
        monkeypatch.setattr(registry, "DRIVER_REGISTRY", {})
        assert get_drivers_for_type(DeviceType.PV_INVERTER) == {}


class TestLoadBuiltinDrivers:
    def test_registers_all_four_builtin_drivers(self) -> None:
        # Not monkeypatched to a fresh dict: importlib.import_module() is a
        # no-op for a module already in sys.modules (near-guaranteed by now,
        # since this repo's own suite imports the driver modules directly
        # elsewhere) -- it would NOT re-run that module's register_driver()
        # call, making a "fresh registry" version of this test order-dependent
        # and flaky. Calling the real function against the real registry is
        # correct either way: first call ever, or a confirming no-op repeat.
        _load_builtin_drivers()
        assert {"homewizard_p1", "aurora_rs485", "alfen_eve", "daikin_brp"} <= set(
            registry.DRIVER_REGISTRY
        )

    def test_one_broken_module_does_not_block_the_others(self, caplog) -> None:
        # Fully mocks import_module (no real import) so this only tests the
        # try/except loop's own control flow -- unaffected by whether any
        # driver module happens to already be cached in sys.modules.
        attempted: list[str] = []

        def _mock_import(module_path: str):
            attempted.append(module_path)
            if module_path.endswith("aurora_rs485"):
                raise ImportError("simulated broken driver module")

        with (
            patch(
                "ionemo_drivers.registry.importlib.import_module",
                side_effect=_mock_import,
            ),
            caplog.at_level("WARNING"),
        ):
            _load_builtin_drivers()

        assert attempted == [
            "ionemo_drivers.grid.homewizard_p1",
            "ionemo_drivers.pv.aurora_rs485",
            "ionemo_drivers.ev.alfen_eve",
            "ionemo_drivers.ac.daikin_brp",
        ]
        assert (
            "Could not load builtin driver ionemo_drivers.pv.aurora_rs485"
            in caplog.text
        )


class TestLoadExternalDrivers:
    def test_registers_a_discovered_entry_point(self, monkeypatch) -> None:
        monkeypatch.setattr(registry, "DRIVER_REGISTRY", {})
        fake_ep = MagicMock()
        fake_ep.name = "some_vendor_driver"
        fake_ep.load.return_value = _DummyDriver

        with patch("ionemo_drivers.registry.entry_points", return_value=[fake_ep]):
            _load_external_drivers()

        assert registry.DRIVER_REGISTRY["some_vendor_driver"] is _DummyDriver

    def test_a_failing_entry_point_is_skipped_not_fatal(self, monkeypatch, caplog) -> None:
        monkeypatch.setattr(registry, "DRIVER_REGISTRY", {})
        broken_ep = MagicMock()
        broken_ep.name = "broken_driver"
        broken_ep.load.side_effect = RuntimeError("bad plugin")
        good_ep = MagicMock()
        good_ep.name = "good_driver"
        good_ep.load.return_value = _DummyDriver

        with (
            patch(
                "ionemo_drivers.registry.entry_points",
                return_value=[broken_ep, good_ep],
            ),
            caplog.at_level("WARNING"),
        ):
            _load_external_drivers()

        assert "broken_driver" not in registry.DRIVER_REGISTRY
        assert registry.DRIVER_REGISTRY["good_driver"] is _DummyDriver
        assert "Failed to load external driver" in caplog.text

    def test_no_entry_points_leaves_registry_unchanged(self, monkeypatch) -> None:
        monkeypatch.setattr(registry, "DRIVER_REGISTRY", {"existing": _DummyDriver})

        with patch("ionemo_drivers.registry.entry_points", return_value=[]):
            _load_external_drivers()

        assert registry.DRIVER_REGISTRY == {"existing": _DummyDriver}


class TestLoadAllDrivers:
    def test_calls_both_builtin_and_external_loading(self, monkeypatch) -> None:
        monkeypatch.setattr(registry, "DRIVER_REGISTRY", {})
        with (
            patch("ionemo_drivers.registry._load_builtin_drivers") as mock_builtin,
            patch("ionemo_drivers.registry._load_external_drivers") as mock_external,
        ):
            load_all_drivers()

        mock_builtin.assert_called_once()
        mock_external.assert_called_once()
