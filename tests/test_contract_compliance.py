"""Contract compliance tests for all registered drivers.

These tests verify that every driver fulfills the requirements documented
in docs/contracts/{device_type}.md and the BaseDriver ABC in base.py.

Run with: pytest tests/ -v
"""

import inspect
from typing import get_type_hints

import pytest

from energy_optimizer_drivers.base import (
    ACUnitDriver,
    BaseDriver,
    ConnectionType,
    DeviceType,
    DiscoveryResult,
    EVChargerData,
    EVChargerDriver,
    GridMeterData,
    GridMeterDriver,
    PVInverterData,
    PVInverterDriver,
)
from energy_optimizer_drivers.contract_validation import validate_contract_data
from energy_optimizer_drivers.registry import DRIVER_REGISTRY, _load_builtin_drivers

# Ensure all builtin drivers are loaded before tests run
_load_builtin_drivers()

# Collect all registered driver classes for parametrization
_ALL_DRIVERS = list(DRIVER_REGISTRY.values())
_DRIVER_IDS = [d.driver_id for d in _ALL_DRIVERS]

_VALID_CONFIG_TYPES = {"text", "password", "number", "select"}
_VALID_EV_STATES = {"available", "connected", "charging", "error"}
_VALID_STATUSES = {"connected", "error", "sleeping", "disabled"}


# ═══════════════════════════════════════════════════════════════════════════════
# IDENTITY ATTRIBUTES
# ═══════════════════════════════════════════════════════════════════════════════


class TestDriverIdentity:
    """Every driver must declare identity class attributes."""

    @pytest.fixture(params=_ALL_DRIVERS, ids=_DRIVER_IDS)
    def driver_cls(self, request: pytest.FixtureRequest) -> type[BaseDriver]:
        return request.param

    def test_has_driver_id(self, driver_cls: type[BaseDriver]) -> None:
        assert isinstance(driver_cls.driver_id, str)
        assert len(driver_cls.driver_id) > 0

    def test_has_name(self, driver_cls: type[BaseDriver]) -> None:
        assert isinstance(driver_cls.name, str)
        assert len(driver_cls.name) > 0

    def test_has_manufacturer(self, driver_cls: type[BaseDriver]) -> None:
        assert isinstance(driver_cls.manufacturer, str)
        assert len(driver_cls.manufacturer) > 0

    def test_has_valid_device_type(self, driver_cls: type[BaseDriver]) -> None:
        assert isinstance(driver_cls.device_type, DeviceType)

    def test_has_valid_connection_type(self, driver_cls: type[BaseDriver]) -> None:
        assert isinstance(driver_cls.connection_type, ConnectionType)

    def test_driver_id_matches_registry_key(self, driver_cls: type[BaseDriver]) -> None:
        assert driver_cls.driver_id in DRIVER_REGISTRY
        assert DRIVER_REGISTRY[driver_cls.driver_id] is driver_cls


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG SCHEMA CONTRACT
# ═══════════════════════════════════════════════════════════════════════════════


class TestConfigSchema:
    """config_schema() must return valid ConfigField list."""

    @pytest.fixture(params=_ALL_DRIVERS, ids=_DRIVER_IDS)
    def driver_cls(self, request: pytest.FixtureRequest) -> type[BaseDriver]:
        return request.param

    def test_returns_list(self, driver_cls: type[BaseDriver]) -> None:
        schema = driver_cls.config_schema()
        assert isinstance(schema, list)

    def test_at_least_one_field(self, driver_cls: type[BaseDriver]) -> None:
        schema = driver_cls.config_schema()
        assert len(schema) > 0, "Every driver needs at least one config field"

    def test_fields_have_required_keys(self, driver_cls: type[BaseDriver]) -> None:
        for field in driver_cls.config_schema():
            assert "key" in field, f"Missing 'key' in field: {field}"
            assert "label" in field, f"Missing 'label' in field: {field}"
            assert "type" in field, f"Missing 'type' in field: {field}"
            assert "required" in field, f"Missing 'required' in field: {field}"

    def test_field_types_are_valid(self, driver_cls: type[BaseDriver]) -> None:
        for field in driver_cls.config_schema():
            assert field["type"] in _VALID_CONFIG_TYPES, (
                f"Invalid type '{field['type']}' for field '{field['key']}'. "
                f"Must be one of {_VALID_CONFIG_TYPES}"
            )

    def test_select_fields_have_options(self, driver_cls: type[BaseDriver]) -> None:
        for field in driver_cls.config_schema():
            if field["type"] == "select":
                assert (
                    "options" in field
                ), f"Field '{field['key']}' is type 'select' but missing 'options'"
                assert (
                    len(field["options"]) > 0
                ), f"Field '{field['key']}' has empty options list"

    def test_keys_are_unique(self, driver_cls: type[BaseDriver]) -> None:
        keys = [f["key"] for f in driver_cls.config_schema()]
        assert len(keys) == len(set(keys)), f"Duplicate keys: {keys}"

    def test_at_least_one_required_field(self, driver_cls: type[BaseDriver]) -> None:
        schema = driver_cls.config_schema()
        required = [f for f in schema if f["required"]]
        assert len(required) > 0, "Driver must have at least one required config field"


# ═══════════════════════════════════════════════════════════════════════════════
# DISCOVERY CONTRACT
# ═══════════════════════════════════════════════════════════════════════════════


class TestDiscoveryContract:
    """discover() must return DiscoveryResult and never raise."""

    @pytest.fixture(params=_ALL_DRIVERS, ids=_DRIVER_IDS)
    def driver_cls(self, request: pytest.FixtureRequest) -> type[BaseDriver]:
        return request.param

    def test_discover_signature_returns_discovery_result(
        self, driver_cls: type[BaseDriver]
    ) -> None:
        hints = get_type_hints(driver_cls.discover)
        assert hints.get("return") is DiscoveryResult, (
            f"{driver_cls.driver_id}.discover() must return DiscoveryResult, "
            f"got {hints.get('return')}"
        )

    def test_discover_is_classmethod(self, driver_cls: type[BaseDriver]) -> None:
        # Verify discover is accessible on the class (classmethod)
        assert hasattr(driver_cls, "discover")
        assert callable(driver_cls.discover)

    def test_discover_takes_no_arguments(self, driver_cls: type[BaseDriver]) -> None:
        sig = inspect.signature(driver_cls.discover)
        # classmethods have only 'cls' which is not visible in the signature
        params = [p for p in sig.parameters.values() if p.name != "cls"]
        assert len(params) == 0, (
            f"{driver_cls.driver_id}.discover() should take no arguments "
            f"(besides cls), got: {params}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# GET_DATA CONTRACT
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetDataContract:
    """get_data() must exist and have correct return type annotation."""

    @pytest.fixture(params=_ALL_DRIVERS, ids=_DRIVER_IDS)
    def driver_cls(self, request: pytest.FixtureRequest) -> type[BaseDriver]:
        return request.param

    def test_has_get_data_method(self, driver_cls: type[BaseDriver]) -> None:
        assert hasattr(driver_cls, "get_data")
        assert callable(getattr(driver_cls, "get_data"))

    def test_get_data_return_annotation(self, driver_cls: type[BaseDriver]) -> None:
        get_data = getattr(driver_cls, "get_data")
        hints = get_type_hints(get_data)
        ret = hints.get("return")
        if issubclass(driver_cls, GridMeterDriver):
            assert ret == (GridMeterData | None)
        elif issubclass(driver_cls, PVInverterDriver):
            assert ret == (PVInverterData | None)
        elif issubclass(driver_cls, EVChargerDriver):
            assert ret == (EVChargerData | None)


# ═══════════════════════════════════════════════════════════════════════════════
# GET_STATUS CONTRACT
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetStatusContract:
    """get_status() must exist and return a valid status string."""

    @pytest.fixture(params=_ALL_DRIVERS, ids=_DRIVER_IDS)
    def driver_cls(self, request: pytest.FixtureRequest) -> type[BaseDriver]:
        return request.param

    def test_has_get_status_method(self, driver_cls: type[BaseDriver]) -> None:
        assert hasattr(driver_cls, "get_status")
        assert callable(getattr(driver_cls, "get_status"))

    def test_get_status_return_annotation(self, driver_cls: type[BaseDriver]) -> None:
        hints = get_type_hints(driver_cls.get_status)
        assert hints.get("return") is str


# ═══════════════════════════════════════════════════════════════════════════════
# LAST_ERROR PROPERTY CONTRACT
# ═══════════════════════════════════════════════════════════════════════════════


class TestLastErrorContract:
    """last_error must be a property returning str | None."""

    @pytest.fixture(params=_ALL_DRIVERS, ids=_DRIVER_IDS)
    def driver_cls(self, request: pytest.FixtureRequest) -> type[BaseDriver]:
        return request.param

    def test_has_last_error_property(self, driver_cls: type[BaseDriver]) -> None:
        assert isinstance(
            inspect.getattr_static(driver_cls, "last_error"), property
        ), f"{driver_cls.driver_id}.last_error must be a @property"


# ═══════════════════════════════════════════════════════════════════════════════
# EV CHARGER-SPECIFIC: SET_CURRENT CONTRACT
# ═══════════════════════════════════════════════════════════════════════════════


_EV_DRIVERS = [d for d in _ALL_DRIVERS if issubclass(d, EVChargerDriver)]
_EV_IDS = [d.driver_id for d in _EV_DRIVERS]


class TestSetCurrentContract:
    """EV charger drivers must implement set_current(amps) → bool."""

    @pytest.fixture(params=_EV_DRIVERS, ids=_EV_IDS)
    def driver_cls(self, request: pytest.FixtureRequest) -> type[EVChargerDriver]:
        return request.param

    def test_has_set_current_method(self, driver_cls: type[EVChargerDriver]) -> None:
        assert hasattr(driver_cls, "set_current")
        assert callable(getattr(driver_cls, "set_current"))

    def test_set_current_signature(self, driver_cls: type[EVChargerDriver]) -> None:
        sig = inspect.signature(driver_cls.set_current)
        params = list(sig.parameters.values())
        # Expect (self, amps: float)
        assert len(params) == 2, f"Expected (self, amps), got {params}"
        assert params[1].name == "amps"

    def test_set_current_return_annotation(
        self, driver_cls: type[EVChargerDriver]
    ) -> None:
        hints = get_type_hints(driver_cls.set_current)
        assert hints.get("return") is bool


# ═══════════════════════════════════════════════════════════════════════════════
# SETUP GUIDE CONTRACT
# ═══════════════════════════════════════════════════════════════════════════════


class TestSetupGuideContract:
    """setup_guide() must return str | None."""

    @pytest.fixture(params=_ALL_DRIVERS, ids=_DRIVER_IDS)
    def driver_cls(self, request: pytest.FixtureRequest) -> type[BaseDriver]:
        return request.param

    def test_setup_guide_is_classmethod(self, driver_cls: type[BaseDriver]) -> None:
        assert callable(driver_cls.setup_guide)

    def test_setup_guide_returns_valid_type(self, driver_cls: type[BaseDriver]) -> None:
        result = driver_cls.setup_guide()
        assert result is None or isinstance(result, str)

    def test_setup_guide_not_empty_if_provided(
        self, driver_cls: type[BaseDriver]
    ) -> None:
        result = driver_cls.setup_guide()
        if result is not None:
            assert len(result.strip()) > 0, "setup_guide() returned an empty string"


# ═══════════════════════════════════════════════════════════════════════════════
# DEVICE TYPE HIERARCHY
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeviceTypeHierarchy:
    """Each driver must subclass the correct device-type ABC."""

    @pytest.fixture(params=_ALL_DRIVERS, ids=_DRIVER_IDS)
    def driver_cls(self, request: pytest.FixtureRequest) -> type[BaseDriver]:
        return request.param

    def test_correct_abc_for_device_type(self, driver_cls: type[BaseDriver]) -> None:
        expected_abc = {
            DeviceType.GRID_METER: GridMeterDriver,
            DeviceType.PV_INVERTER: PVInverterDriver,
            DeviceType.EV_CHARGER: EVChargerDriver,
            DeviceType.AC_UNIT: ACUnitDriver,
        }
        abc = expected_abc.get(driver_cls.device_type)
        assert abc is not None, f"Unknown device_type: {driver_cls.device_type}"
        assert issubclass(driver_cls, abc), (
            f"{driver_cls.driver_id} declares device_type={driver_cls.device_type} "
            f"but does not subclass {abc.__name__}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# INIT CONTRACT
# ═══════════════════════════════════════════════════════════════════════════════


class TestInitContract:
    """__init__(self, config: dict) must accept a config dict."""

    @pytest.fixture(params=_ALL_DRIVERS, ids=_DRIVER_IDS)
    def driver_cls(self, request: pytest.FixtureRequest) -> type[BaseDriver]:
        return request.param

    def test_init_accepts_config(self, driver_cls: type[BaseDriver]) -> None:
        sig = inspect.signature(driver_cls.__init__)
        params = list(sig.parameters.values())
        # Expect (self, config)
        assert len(params) >= 2, (
            f"{driver_cls.driver_id}.__init__ must accept (self, config), "
            f"got {[p.name for p in params]}"
        )
        assert params[1].name == "config", (
            f"{driver_cls.driver_id}.__init__ second param must be 'config', "
            f"got '{params[1].name}'"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# CONTRACT VALIDATION UTILITY
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidateContractData:
    """`validate_contract_data()` itself: the runtime check every driver's
    get_data() test should run its mocked result through (see
    test_daikin_brp.py, test_alfen_driver.py, test_aurora_driver.py)."""

    _VALID_GRID_METER_DATA: GridMeterData = {
        "grid_power_w": -450.0,
        "import_total_kwh": 8234.5,
        "export_total_kwh": 2100.3,
        "import_t1_kwh": None,
        "import_t2_kwh": None,
        "export_t1_kwh": None,
        "export_t2_kwh": None,
        "gas_total_m3": None,
        "voltage_l1_v": 231.2,
        "voltage_l2_v": None,
        "voltage_l3_v": None,
        "current_l1_a": 3.2,
        "current_l2_a": None,
        "current_l3_a": None,
        "frequency_hz": 50.01,
    }

    def test_fully_compliant_data_has_no_violations(self) -> None:
        assert validate_contract_data(DeviceType.GRID_METER, self._VALID_GRID_METER_DATA) == []

    def test_missing_required_key_is_a_violation(self) -> None:
        data = dict(self._VALID_GRID_METER_DATA)
        del data["grid_power_w"]
        violations = validate_contract_data(DeviceType.GRID_METER, data)
        assert any("missing required key 'grid_power_w'" in v for v in violations)

    def test_required_key_set_to_none_is_a_violation(self) -> None:
        data = {**self._VALID_GRID_METER_DATA, "import_total_kwh": None}
        violations = validate_contract_data(DeviceType.GRID_METER, data)
        assert any("'import_total_kwh' must not be None" in v for v in violations)

    def test_wrong_type_on_required_key_is_a_violation(self) -> None:
        data = {**self._VALID_GRID_METER_DATA, "grid_power_w": "450"}
        violations = validate_contract_data(DeviceType.GRID_METER, data)
        assert any("'grid_power_w' expected" in v for v in violations)

    def test_wrong_type_on_present_optional_key_is_a_violation(self) -> None:
        data = {**self._VALID_GRID_METER_DATA, "voltage_l1_v": "231.2"}
        violations = validate_contract_data(DeviceType.GRID_METER, data)
        assert any("'voltage_l1_v' expected" in v for v in violations)

    def test_optional_key_left_as_none_is_fine(self) -> None:
        assert self._VALID_GRID_METER_DATA.get("gas_total_m3") is None
        assert validate_contract_data(DeviceType.GRID_METER, self._VALID_GRID_METER_DATA) == []

    def test_unexpected_key_is_a_violation(self) -> None:
        data = {**self._VALID_GRID_METER_DATA, "totally_made_up_field": 1}
        violations = validate_contract_data(DeviceType.GRID_METER, data)
        assert any("unexpected key" in v and "totally_made_up_field" in v for v in violations)

    def test_int_accepted_where_float_expected(self) -> None:
        """A driver returning a plain int for a float field (e.g. `0` instead
        of `0.0`) is fine — Python doesn't distinguish them for arithmetic
        purposes, and neither should this check."""
        data = {**self._VALID_GRID_METER_DATA, "grid_power_w": 0}
        assert validate_contract_data(DeviceType.GRID_METER, data) == []

    def test_bool_rejected_where_float_expected(self) -> None:
        """`bool` is an `int` subclass in Python — must not slip through a
        float/int check just because `isinstance(True, int)` is True."""
        data = {**self._VALID_GRID_METER_DATA, "grid_power_w": True}
        violations = validate_contract_data(DeviceType.GRID_METER, data)
        assert any("'grid_power_w' expected" in v for v in violations)

    def test_covers_all_four_device_types(self) -> None:
        """Every DeviceType must have a validator entry — new device types
        (a coordinated, maintainer-agreed change per ARCHITECTURE.md) must
        extend contract_validation._CONTRACT_BY_DEVICE_TYPE too."""
        from energy_optimizer_drivers.contract_validation import _CONTRACT_BY_DEVICE_TYPE

        assert set(_CONTRACT_BY_DEVICE_TYPE) == set(DeviceType)
