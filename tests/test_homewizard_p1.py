"""Tests for ionemo_drivers/grid/homewizard_p1.py — HomewizardP1Driver.

_probe_homewizard's identity extraction is covered separately in
test_driver_discover_identity.py; this file covers the driver class itself:
get_data()/get_status() and discover()'s network-scan orchestration.
All response bodies below are fabricated example data shaped to match the
real HomeWizard API format, not captured from any real device.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from ionemo_drivers.base import DeviceType
from ionemo_drivers.contract_validation import validate_contract_data
from ionemo_drivers.grid.homewizard_p1 import HomewizardP1Driver

# RFC 5737 TEST-NET-1 — reserved for documentation/testing, never a real deployment.
_TEST_IP = "192.0.2.10"

_FULL_RESPONSE = {
    "active_power_w": -450.0,
    "total_power_import_t1_kwh": 5200.0,
    "total_power_import_t2_kwh": 3034.5,
    "total_power_export_t1_kwh": 1400.0,
    "total_power_export_t2_kwh": 700.3,
    "total_gas_m3": 1543.21,
    "active_voltage_l1_v": 231.2,
    "active_voltage_l2_v": 230.8,
    "active_voltage_l3_v": 229.5,
    "active_current_l1_a": 3.2,
    "active_current_l2_a": 1.1,
    "active_current_l3_a": 0.5,
    "active_frequency_hz": 50.01,
}

# Single-phase meter with no gas connection — optional fields legitimately absent.
_SINGLE_PHASE_RESPONSE = {
    "active_power_w": 1200.0,
    "total_power_import_t1_kwh": 4500.0,
    "total_power_import_t2_kwh": 0.0,
    "total_power_export_t1_kwh": 300.0,
    "total_power_export_t2_kwh": 0.0,
    "active_voltage_l1_v": 232.1,
    "active_current_l1_a": 5.2,
    "active_frequency_hz": 50.0,
}


def _make_response(json_body: dict, status: int = 200) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_body
    if status >= 400:
        r.raise_for_status.side_effect = requests.exceptions.HTTPError(f"{status}")
    else:
        r.raise_for_status = MagicMock()
    return r


@pytest.fixture
def driver() -> HomewizardP1Driver:
    return HomewizardP1Driver({"ip": _TEST_IP, "timeout": 2})


# ---------------------------------------------------------------------------
# get_data()
# ---------------------------------------------------------------------------


class TestGetData:
    def test_returns_grid_meter_data(self, driver: HomewizardP1Driver) -> None:
        with patch("requests.get", return_value=_make_response(_FULL_RESPONSE)):
            data = driver.get_data()

        assert data is not None
        assert data["grid_power_w"] == pytest.approx(-450.0)
        assert data["import_total_kwh"] == pytest.approx(8234.5)
        assert data["export_total_kwh"] == pytest.approx(2100.3)
        assert data.get("gas_total_m3") == pytest.approx(1543.21)
        assert validate_contract_data(DeviceType.GRID_METER, data) == []

    def test_single_phase_no_gas_leaves_optional_fields_none(
        self, driver: HomewizardP1Driver
    ) -> None:
        with patch("requests.get", return_value=_make_response(_SINGLE_PHASE_RESPONSE)):
            data = driver.get_data()

        assert data is not None
        assert data.get("gas_total_m3") is None
        assert data.get("voltage_l2_v") is None
        assert data.get("voltage_l3_v") is None
        assert data.get("current_l2_a") is None
        assert data.get("current_l3_a") is None
        assert validate_contract_data(DeviceType.GRID_METER, data) == []

    def test_uses_v1_data_endpoint(self, driver: HomewizardP1Driver) -> None:
        with patch("requests.get", return_value=_make_response(_FULL_RESPONSE)) as mock_get:
            driver.get_data()

        assert mock_get.call_args.args[0] == f"http://{_TEST_IP}/api/v1/data"

    def test_sets_last_success_and_connected_status(self, driver: HomewizardP1Driver) -> None:
        with patch("requests.get", return_value=_make_response(_FULL_RESPONSE)):
            driver.get_data()

        assert driver.last_error is None
        assert driver.get_status() == "connected"

    def test_returns_none_on_connection_error(self, driver: HomewizardP1Driver) -> None:
        with patch(
            "requests.get",
            side_effect=requests.exceptions.ConnectionError("refused"),
        ):
            data = driver.get_data()

        assert data is None
        assert driver.get_status() == "error"
        assert driver.last_error is not None

    def test_returns_none_on_http_error_status(self, driver: HomewizardP1Driver) -> None:
        with patch("requests.get", return_value=_make_response({}, status=500)):
            data = driver.get_data()

        assert data is None
        assert driver.get_status() == "error"

    def test_returns_none_on_timeout(self, driver: HomewizardP1Driver) -> None:
        with patch("requests.get", side_effect=requests.exceptions.Timeout("timed out")):
            data = driver.get_data()

        assert data is None
        assert driver.get_status() == "error"


# ---------------------------------------------------------------------------
# get_status() / last_error
# ---------------------------------------------------------------------------


class TestGetStatus:
    def test_fresh_driver_is_disabled(self, driver: HomewizardP1Driver) -> None:
        assert driver.get_status() == "disabled"
        assert driver.last_error is None

    def test_recovers_to_connected_after_a_later_success(
        self, driver: HomewizardP1Driver
    ) -> None:
        with patch(
            "requests.get",
            side_effect=requests.exceptions.ConnectionError("refused"),
        ):
            driver.get_data()
        assert driver.get_status() == "error"

        with patch("requests.get", return_value=_make_response(_FULL_RESPONSE)):
            driver.get_data()

        assert driver.get_status() == "connected"
        assert driver.last_error is None


# ---------------------------------------------------------------------------
# discover() — network-scan orchestration (probe matching itself is covered
# in test_driver_discover_identity.py)
# ---------------------------------------------------------------------------


class TestDiscover:
    def _mock_local_socket(self, local_ip: str) -> MagicMock:
        mock_sock = MagicMock()
        mock_sock.getsockname.return_value = (local_ip, 0)
        return mock_sock

    def test_finds_matching_device(self) -> None:
        target_ip = "192.0.2.77"

        def fake_probe(ip: str) -> dict | None:
            return {"ip": ip, "identity": "aabbccddeeff"} if ip == target_ip else None

        with (
            patch("socket.socket", return_value=self._mock_local_socket("192.0.2.50")),
            patch(
                "ionemo_drivers.grid.homewizard_p1._probe_homewizard",
                side_effect=fake_probe,
            ),
        ):
            result = HomewizardP1Driver.discover()

        assert result.devices == [{"ip": target_ip, "identity": "aabbccddeeff"}]
        assert result.warnings == []

    def test_no_devices_found_returns_warning(self) -> None:
        with (
            patch("socket.socket", return_value=self._mock_local_socket("192.0.2.50")),
            patch(
                "ionemo_drivers.grid.homewizard_p1._probe_homewizard",
                return_value=None,
            ),
        ):
            result = HomewizardP1Driver.discover()

        assert result.devices == []
        assert len(result.warnings) == 1
        assert "No HomeWizard devices found" in result.warnings[0]

    def test_local_ip_lookup_failure_returns_warning(self) -> None:
        with patch("socket.socket", side_effect=OSError("network unreachable")):
            result = HomewizardP1Driver.discover()

        assert result.devices == []
        assert len(result.warnings) == 1
        assert "local network address" in result.warnings[0]


class TestDiscoverQuick:
    """discover_quick() -- must forward quick=True to scan_subnet(), unlike
    discover() itself, which stays zero-argument (test_contract_compliance.py
    enforces this) and always uses quick=False."""

    def test_discover_forwards_quick_false(self) -> None:
        # scan_subnet is imported lazily inside _run_discovery(), so it must
        # be patched at its source module, not homewizard_p1's namespace.
        with patch("ionemo_drivers.lan_scan.scan_subnet") as mock_scan:
            HomewizardP1Driver.discover()

        assert mock_scan.call_args.kwargs["quick"] is False

    def test_discover_quick_forwards_quick_true(self) -> None:
        with patch("ionemo_drivers.lan_scan.scan_subnet") as mock_scan:
            HomewizardP1Driver.discover_quick()

        assert mock_scan.call_args.kwargs["quick"] is True
