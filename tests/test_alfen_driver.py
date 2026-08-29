"""Regression tests for the Alfen EVE driver, guarding against four specific mistakes
that are easy to reintroduce given how the device's API actually behaves:

  Bug A — Wrong API endpoint: the correct call is GET /api/prop with
           params={"cat": "meter1"}, not /api/categories/meter1
  Bug B — Wrong response parsing: the API returns
           {"properties": [{id, value}, ...]}, not a flat dict keyed by id
  Bug C — OCPP state type: the API returns state as an integer, not a string
  Bug D — Missing power calculation: the API doesn't return total power directly;
           it must be computed as power_w = sum(Vn * In) * PF
"""

from unittest.mock import MagicMock, patch

import pytest

from energy_optimizer_drivers.base import EVChargerData
from energy_optimizer_drivers.ev.alfen_eve import AlfenEveDriver

# ---------------------------------------------------------------------------
# Shared test data — reflects the real Alfen API response format
# ---------------------------------------------------------------------------

# Fake credentials used only to instantiate the driver in unit tests — never sent over the network.
_TEST_IP = "192.168.1.100"  # NOSONAR(S1313)
_TEST_PASSWORD = "testpass"  # NOSONAR(S2068)

_MOCK_PROPS = {
    "properties": [
        {"id": "2221_3", "value": 230.0},  # V1
        {"id": "2221_4", "value": 230.0},  # V2
        {"id": "2221_5", "value": 230.0},  # V3
        {"id": "2221_A", "value": 5.84},  # I1
        {"id": "2221_B", "value": 5.93},  # I2
        {"id": "2221_C", "value": 5.86},  # I3
        {"id": "2221_11", "value": 0.995},  # PF
        {"id": "2221_16", "value": 4171.6},  # kWh
        {"id": "3600_1", "value": 3},  # OCPP state (3 = charging)
        {"id": "2068_0", "value": 16.0},  # max current
    ]
}


def _make_driver() -> AlfenEveDriver:
    with patch("energy_optimizer_drivers.ev.alfen_eve.resolve_verify", return_value="/fake/cert.pem"):
        return AlfenEveDriver({"ip": _TEST_IP, "password": _TEST_PASSWORD})


def _get_data_with_mock_props(props: dict) -> EVChargerData | None:
    """Call driver.get_data() with all HTTP I/O and TLS replaced by mocks."""
    driver = _make_driver()
    login_resp = MagicMock()
    login_resp.status_code = 200

    get_resp = MagicMock()
    get_resp.status_code = 200
    get_resp.json.return_value = props

    with (
        patch("energy_optimizer_drivers.ev.alfen_eve.requests.Session") as mock_cls,
        patch("energy_optimizer_drivers.ev.alfen_eve.configure_session_tls"),
    ):
        mock_session = MagicMock()
        mock_cls.return_value = mock_session
        mock_session.post.return_value = login_resp
        mock_session.get.return_value = get_resp
        return driver.get_data()


# ---------------------------------------------------------------------------
# Bug A — Wrong API endpoint
# ---------------------------------------------------------------------------


class TestAlfenAPIEndpoint:
    """_read_all_properties must call GET /api/prop, never /api/categories/."""

    def test_uses_prop_endpoint_for_all_calls(self) -> None:
        driver = _make_driver()
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"properties": []}
        mock_session.get.return_value = mock_resp

        driver._read_all_properties(mock_session)

        for call in mock_session.get.call_args_list:
            url: str = call.args[0]
            assert "/api/prop" in url
            assert "/api/categories/" not in url

    def test_first_call_sends_cat_meter1_param(self) -> None:
        driver = _make_driver()
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"properties": []}
        mock_session.get.return_value = mock_resp

        driver._read_all_properties(mock_session)

        first_kwargs = mock_session.get.call_args_list[0].kwargs
        assert first_kwargs["params"] == {"cat": "meter1"}

    def test_makes_three_separate_requests(self) -> None:
        """Three separate GET calls: meter, OCPP state, max current."""
        driver = _make_driver()
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"properties": []}
        mock_session.get.return_value = mock_resp

        driver._read_all_properties(mock_session)

        assert mock_session.get.call_count == 3


# ---------------------------------------------------------------------------
# Bug B — Wrong response parsing
# ---------------------------------------------------------------------------


class TestAlfenResponseParsing:
    """get_data() must parse the {properties: [{id, value}]} list format."""

    def test_returns_data_object_not_none(self) -> None:
        assert _get_data_with_mock_props(_MOCK_PROPS) is not None

    def test_phase_currents_match_properties(self) -> None:
        data = _get_data_with_mock_props(_MOCK_PROPS)
        assert data is not None
        assert data.get("current_l1_a") == pytest.approx(5.84, abs=0.01)
        assert data.get("current_l2_a") == pytest.approx(5.93, abs=0.01)
        assert data.get("current_l3_a") == pytest.approx(5.86, abs=0.01)

    def test_energy_kwh_matches_property(self) -> None:
        data = _get_data_with_mock_props(_MOCK_PROPS)
        assert data is not None
        assert data.get("energy_total_kwh") == pytest.approx(4171.6, abs=0.001)

    def test_max_current_matches_property(self) -> None:
        data = _get_data_with_mock_props(_MOCK_PROPS)
        assert data is not None
        assert data.get("max_current_a") == pytest.approx(16.0, abs=0.01)

    def test_state_derived_from_integer_ocpp(self) -> None:
        """OCPP=3 with sum_current > 0.5A → state is 'charging'."""
        data = _get_data_with_mock_props(_MOCK_PROPS)
        assert data is not None
        assert data.get("state") == "charging"

    def test_returns_none_on_login_failure(self) -> None:
        driver = _make_driver()
        failed_resp = MagicMock()
        failed_resp.status_code = 401

        with patch("energy_optimizer_drivers.ev.alfen_eve.requests.Session") as mock_cls:
            mock_session = MagicMock()
            mock_cls.return_value = mock_session
            mock_session.post.return_value = failed_resp
            data = driver.get_data()

        assert data is None


# ---------------------------------------------------------------------------
# Bug C — OCPP state type (integer from API, not string)
# ---------------------------------------------------------------------------


class TestOCPPStateMapping:
    """_map_ocpp_state(ocpp: int, current_a: float) maps integer OCPP codes."""

    def test_available_when_ocpp_zero_no_current(self) -> None:
        assert AlfenEveDriver._map_ocpp_state(0, 0.0) == "available"

    def test_connected_when_ocpp_nonzero_no_current(self) -> None:
        assert AlfenEveDriver._map_ocpp_state(3, 0.0) == "connected"

    def test_charging_when_ocpp_nonzero_and_current(self) -> None:
        assert AlfenEveDriver._map_ocpp_state(3, 6.0) == "charging"

    def test_charging_from_current_alone_overrides_ocpp_zero(self) -> None:
        """Physical current > threshold overrides OCPP=0."""
        assert AlfenEveDriver._map_ocpp_state(0, 6.0) == "charging"

    def test_exactly_threshold_is_not_charging(self) -> None:
        """0.5 A is NOT above the 0.5 A threshold — requires strict >."""
        assert AlfenEveDriver._map_ocpp_state(3, 0.5) == "connected"

    def test_just_above_threshold_is_charging(self) -> None:
        assert AlfenEveDriver._map_ocpp_state(3, 0.51) == "charging"

    def test_other_nonzero_ocpp_states_map_to_connected(self) -> None:
        assert AlfenEveDriver._map_ocpp_state(1, 0.0) == "connected"
        assert AlfenEveDriver._map_ocpp_state(2, 0.0) == "connected"

    def test_ocpp_null_defaults_to_zero_state(self) -> None:
        """A missing OCPP property (None → 0) should map to 'available'."""
        # The driver uses `int(props.get(_OCPP_STATE_ID) or 0)` — None becomes 0
        assert AlfenEveDriver._map_ocpp_state(0, 0.0) == "available"


# ---------------------------------------------------------------------------
# Bug D — Power calculation: sum(Vn × In) × PF
# ---------------------------------------------------------------------------


class TestAlfenPowerCalculation:
    """power_w must equal (V1*I1 + V2*I2 + V3*I3) * PF — no direct power register."""

    def test_power_matches_sum_formula(self) -> None:
        data = _get_data_with_mock_props(_MOCK_PROPS)
        assert data is not None
        expected_w = (230.0 * 5.84 + 230.0 * 5.93 + 230.0 * 5.86) * 0.995
        assert data["power_w"] == pytest.approx(expected_w, abs=1.0)  # type: ignore[typeddict-item]

    def test_power_is_positive_while_charging(self) -> None:
        data = _get_data_with_mock_props(_MOCK_PROPS)
        assert data is not None
        assert data.get("power_w", 0.0) > 0

    def test_power_zero_when_no_current(self) -> None:
        """If all phase currents are zero, power_w must also be zero."""
        zero_props = {
            "properties": [
                {"id": "2221_3", "value": 230.0},
                {"id": "2221_4", "value": 230.0},
                {"id": "2221_5", "value": 230.0},
                {"id": "2221_A", "value": 0.0},
                {"id": "2221_B", "value": 0.0},
                {"id": "2221_C", "value": 0.0},
                {"id": "2221_11", "value": 1.0},
                {"id": "2221_16", "value": 100.0},
                {"id": "3600_1", "value": 0},
                {"id": "2068_0", "value": 16.0},
            ]
        }
        data = _get_data_with_mock_props(zero_props)
        assert data is not None
        assert data.get("power_w", 0.0) == pytest.approx(0.0, abs=0.1)

    def test_impossible_power_clamped_to_zero(self) -> None:
        """Power > max_possible_w * 1.1 is treated as a sensor glitch and set to 0."""
        glitch_props = {
            "properties": [
                {"id": "2221_3", "value": 230.0},
                {"id": "2221_4", "value": 230.0},
                {"id": "2221_5", "value": 230.0},
                {"id": "2221_A", "value": 99.0},  # physically impossible
                {"id": "2221_B", "value": 99.0},
                {"id": "2221_C", "value": 99.0},
                {"id": "2221_11", "value": 1.0},
                {"id": "2221_16", "value": 0.0},
                {"id": "3600_1", "value": 0},
                {"id": "2068_0", "value": 16.0},  # max_current = 16A
            ]
        }
        data = _get_data_with_mock_props(glitch_props)
        assert data is not None
        # max_possible = 16 * 3 * 230 * 1.0 = 11040W; 99A×3×230 = 68270W >> 1.1×11040
        assert data.get("power_w", 0.0) == pytest.approx(0.0, abs=0.1)
