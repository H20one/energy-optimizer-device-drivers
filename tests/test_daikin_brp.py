"""Tests for energy_optimizer_drivers/ac/daikin_brp.py — DaikinBrpDriver.

Extracted from energy-optimizer's tests/test_ac.py (the app-route/scheduler tests stayed there;
this class tests only the driver itself) as part of the drivers-repo split.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

# ─── Test IP constants (RFC 5737 TEST-NET-1 — reserved for documentation/testing) ─
_TEST_IP = "192.0.2.1"  # primary adapter under test
_TEST_LOCAL_IP = "192.0.2.100"  # simulated local-machine IP returned by getsockname

# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_response(body: str, status: int = 200) -> MagicMock:
    """Build a minimal mock requests.Response."""
    r = MagicMock()
    r.status_code = status
    r.text = body
    r.raise_for_status = MagicMock()
    return r


def _ok_resp(kv: dict) -> str:
    """Serialise a dict into the Daikin comma-separated key=value format."""
    pairs = ",".join(f"{k}={v}" for k, v in kv.items())
    return f"ret=OK,{pairs}"


_SENSOR_OK = _ok_resp({"htemp": "21.5", "hhum": "55", "otemp": "10.0"})
_CONTROL_OK = _ok_resp(
    {"pow": "1", "mode": "3", "stemp": "22.0", "shum": "0", "f_rate": "A", "f_dir": "0"}
)
_CONTROL_OFF = _ok_resp(
    {"pow": "0", "mode": "0", "stemp": "22.0", "shum": "0", "f_rate": "A", "f_dir": "0"}
)
_SET_OK = "ret=OK"
_SET_NG = "ret=PARAM NG"


class TestDaikinBrpDriver:
    """Unit tests for energy_optimizer_drivers/ac/daikin_brp.py."""

    @pytest.fixture
    def driver(self):
        from energy_optimizer_drivers.ac.daikin_brp import DaikinBrpDriver

        return DaikinBrpDriver({"ip": _TEST_IP, "timeout": 2})

    # ── get_data ──────────────────────────────────────────────────────────────

    def test_get_data_returns_ac_unit_data(self, driver):
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                _make_response(_SENSOR_OK),
                _make_response(_CONTROL_OK),
            ]
            data = driver.get_data()

        assert data is not None
        assert data["mode"] == "cool"
        assert data["temperature_c"] == pytest.approx(21.5)
        assert data["target_temp_c"] == pytest.approx(22.0)
        assert data["fan_speed"] == "auto"
        assert data["humidity_pct"] == pytest.approx(55.0)
        assert data["power_w"] == pytest.approx(0.0)

    def test_get_data_off_unit(self, driver):
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                _make_response(_SENSOR_OK),
                _make_response(_CONTROL_OFF),
            ]
            data = driver.get_data()

        assert data is not None
        assert data["mode"] == "off"

    def test_get_data_sets_last_success(self, driver):
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                _make_response(_SENSOR_OK),
                _make_response(_CONTROL_OK),
            ]
            driver.get_data()

        assert driver._last_success is not None
        assert driver._last_error is None
        assert driver.get_status() == "connected"

    def test_get_data_returns_none_on_sensor_failure(self, driver):
        from requests.exceptions import ConnectionError as ReqConnError

        with patch("requests.get", side_effect=ReqConnError("refused")):
            data = driver.get_data()

        assert data is None
        assert driver.last_error is not None
        assert driver.get_status() == "error"

    def test_get_data_returns_none_on_ret_not_ok(self, driver):
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                _make_response("ret=BUSY"),
                _make_response(_CONTROL_OK),
            ]
            data = driver.get_data()

        assert data is None

    def test_get_data_invalid_htemp(self, driver):
        sensor = _ok_resp({"htemp": "N/A", "hhum": "-"})
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                _make_response(sensor),
                _make_response(_CONTROL_OK),
            ]
            data = driver.get_data()

        assert data is None
        assert "htemp" in driver.last_error

    def test_get_data_missing_humidity_sensor(self, driver):
        sensor = _ok_resp({"htemp": "20.0", "hhum": "-"})
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                _make_response(sensor),
                _make_response(_CONTROL_OK),
            ]
            data = driver.get_data()

        assert data is not None
        assert data["humidity_pct"] is None

    def test_get_data_fan_mode_stemp_fallback(self, driver):
        control = _ok_resp(
            {
                "pow": "1",
                "mode": "6",
                "stemp": "--",
                "shum": "--",
                "f_rate": "A",
                "f_dir": "0",
            }
        )
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                _make_response(_SENSOR_OK),
                _make_response(control),
            ]
            data = driver.get_data()

        assert data is not None
        assert data["mode"] == "fan"
        assert data["target_temp_c"] == pytest.approx(
            22.0
        )  # falls back to _FALLBACK_STEMP

    def test_get_data_all_mode_mappings(self, driver):
        modes_and_expected = [
            ("0", "auto"),
            ("1", "auto"),
            ("2", "dry"),
            ("3", "cool"),
            ("4", "heat"),
            ("6", "fan"),
        ]
        for daikin_mode, expected_mode in modes_and_expected:
            stemp = "--" if daikin_mode == "6" else "22.0"
            shum = "--" if daikin_mode == "6" else "0"
            control = _ok_resp(
                {
                    "pow": "1",
                    "mode": daikin_mode,
                    "stemp": stemp,
                    "shum": shum,
                    "f_rate": "A",
                    "f_dir": "0",
                }
            )
            with patch("requests.get") as mock_get:
                mock_get.side_effect = [
                    _make_response(_SENSOR_OK),
                    _make_response(control),
                ]
                data = driver.get_data()
            assert data is not None, f"mode {daikin_mode} returned None"
            assert data["mode"] == expected_mode, f"mode {daikin_mode}"

    def test_get_data_all_fan_speed_mappings(self, driver):
        for f_rate, expected in [
            ("A", "auto"),
            ("B", "silent"),
            ("3", "1"),
            ("4", "2"),
            ("5", "3"),
            ("6", "4"),
            ("7", "5"),
        ]:
            control = _ok_resp(
                {
                    "pow": "1",
                    "mode": "3",
                    "stemp": "22.0",
                    "shum": "0",
                    "f_rate": f_rate,
                    "f_dir": "0",
                }
            )
            with patch("requests.get") as mock_get:
                mock_get.side_effect = [
                    _make_response(_SENSOR_OK),
                    _make_response(control),
                ]
                data = driver.get_data()
            assert data["fan_speed"] == expected, f"f_rate={f_rate}"

    def test_get_data_returns_none_on_non_request_exception(self, driver):
        """Regression test for H7: a non-RequestException failure (e.g. a
        response-decode error) must be caught and return None, not propagate.

        `_get()`'s own try/except only catches `RequestException` — before
        H7, nothing further up the call chain caught anything broader, so an
        error like this would have raised out of `get_data()` entirely,
        violating the "drivers never raise" contract every other builtin
        driver follows.
        """

        class _BadTextResponse:
            status_code = 200

            def raise_for_status(self):
                pass

            @property
            def text(self):
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

        with patch("requests.get", return_value=_BadTextResponse()):
            data = driver.get_data()

        assert data is None
        assert driver.last_error is not None
        assert driver.get_status() == "error"

    # ── get_status ────────────────────────────────────────────────────────────

    def test_get_status_initial_is_disabled(self, driver):
        assert driver.get_status() == "disabled"

    def test_get_status_connected_after_success(self, driver):
        driver._last_success = time.monotonic()
        assert driver.get_status() == "connected"

    def test_get_status_error_when_last_error_set(self, driver):
        driver._last_error = "Connection refused"
        assert driver.get_status() == "error"

    # ── Control cache ─────────────────────────────────────────────────────────

    def test_control_cache_populated_by_get(self, driver):
        with patch("requests.get", return_value=_make_response(_CONTROL_OK)):
            driver._get("/aircon/get_control_info")

        assert driver._control_cache is not None
        assert driver._control_cache["pow"] == "1"

    def test_control_cache_not_populated_for_other_paths(self, driver):
        with patch("requests.get", return_value=_make_response(_SENSOR_OK)):
            driver._get("/aircon/get_sensor_info")

        assert driver._control_cache is None

    def test_get_control_cached_uses_cache_when_fresh(self, driver):
        driver._control_cache = {
            "pow": "1",
            "mode": "3",
            "stemp": "22.0",
            "shum": "0",
            "f_rate": "A",
            "f_dir": "0",
        }
        driver._control_cache_ts = time.monotonic()

        with patch("requests.get") as mock_get:
            result = driver._get_control_cached()
            mock_get.assert_not_called()

        assert result is not None
        assert result["pow"] == "1"

    def test_get_control_cached_fetches_when_stale(self, driver):
        driver._control_cache = {"pow": "0"}
        # `_control_cache_ts` is compared against time.monotonic(), whose zero
        # point is arbitrary/system-dependent (e.g. often "time since boot" on
        # Linux) — NOT the Unix epoch. A literal 0.0 is only "ancient" if the
        # machine's monotonic clock already exceeds the cache TTL, which is
        # false on a freshly-booted CI runner. Go back further than the TTL
        # relative to the current monotonic reading instead, so this is
        # correct regardless of the underlying clock's absolute value.
        driver._control_cache_ts = time.monotonic() - driver._CONTROL_CACHE_TTL - 1

        with patch("requests.get", return_value=_make_response(_CONTROL_OK)):
            result = driver._get_control_cached()

        assert result is not None
        assert result["pow"] == "1"  # from fresh fetch

    def test_send_control_updates_cache_on_success(self, driver):
        params = {
            "pow": "1",
            "mode": "3",
            "stemp": "22.0",
            "shum": "0",
            "f_rate": "A",
            "f_dir": "0",
        }
        with patch("requests.get", return_value=_make_response(_SET_OK)):
            ok = driver._send_control(params)

        assert ok is True
        assert driver._control_cache == params

    def test_send_control_invalidates_cache_on_param_ng(self, driver):
        driver._control_cache = {"pow": "1"}
        params = {
            "pow": "1",
            "mode": "3",
            "stemp": "22.0",
            "shum": "0",
            "f_rate": "A",
            "f_dir": "0",
        }
        with patch("requests.get", return_value=_make_response(_SET_NG)):
            ok = driver._send_control(params)

        assert ok is False
        assert driver._control_cache is None

    def test_send_control_invalidates_cache_on_network_error(self, driver):
        from requests.exceptions import Timeout

        driver._control_cache = {"pow": "1"}
        params = {
            "pow": "1",
            "mode": "3",
            "stemp": "22.0",
            "shum": "0",
            "f_rate": "A",
            "f_dir": "0",
        }
        with patch("requests.get", side_effect=Timeout("timed out")):
            ok = driver._send_control(params)

        assert ok is False
        assert driver._control_cache is None
        assert driver.last_error is not None

    # ── set_mode ──────────────────────────────────────────────────────────────

    def test_set_mode_cool(self, driver):
        driver._control_cache = {
            "pow": "1",
            "mode": "3",
            "stemp": "22.0",
            "shum": "0",
            "f_rate": "A",
            "f_dir": "0",
        }
        driver._control_cache_ts = time.monotonic()
        with patch("requests.get", return_value=_make_response(_SET_OK)) as mock_get:
            ok = driver.set_mode("cool")

        assert ok is True
        qs = mock_get.call_args[0][0]
        assert "pow=1" in qs
        assert "mode=3" in qs

    def test_set_mode_off(self, driver):
        driver._control_cache = {
            "pow": "1",
            "mode": "3",
            "stemp": "22.0",
            "shum": "0",
            "f_rate": "A",
            "f_dir": "0",
        }
        driver._control_cache_ts = time.monotonic()
        with patch("requests.get", return_value=_make_response(_SET_OK)) as mock_get:
            ok = driver.set_mode("off")

        assert ok is True
        qs = mock_get.call_args[0][0]
        assert "pow=0" in qs

    def test_set_mode_fan_uses_stemp_sentinel(self, driver):
        driver._control_cache = {
            "pow": "1",
            "mode": "3",
            "stemp": "22.0",
            "shum": "0",
            "f_rate": "A",
            "f_dir": "0",
        }
        driver._control_cache_ts = time.monotonic()
        with patch("requests.get", return_value=_make_response(_SET_OK)) as mock_get:
            ok = driver.set_mode("fan")

        assert ok is True
        qs = mock_get.call_args[0][0]
        assert "stemp=--" in qs
        assert "shum=--" in qs

    def test_set_mode_from_fan_restores_setpoint(self, driver):
        """Switching back from fan mode restores a numeric stemp."""
        driver._control_cache = {
            "pow": "1",
            "mode": "6",
            "stemp": "--",
            "shum": "--",
            "f_rate": "A",
            "f_dir": "0",
        }
        driver._control_cache_ts = time.monotonic()
        with patch("requests.get", return_value=_make_response(_SET_OK)) as mock_get:
            ok = driver.set_mode("cool")

        assert ok is True
        qs = mock_get.call_args[0][0]
        assert "stemp=22.0" in qs

    def test_set_mode_unknown_returns_false(self, driver):
        ok = driver.set_mode("turbo")
        assert ok is False

    def test_set_mode_returns_false_when_no_cache_and_network_fail(self, driver):
        from requests.exceptions import ConnectionError as ReqConnError

        with patch("requests.get", side_effect=ReqConnError("down")):
            ok = driver.set_mode("cool")

        assert ok is False

    def test_set_mode_returns_false_on_non_request_exception(self, driver):
        """Regression test for H7 — see test_get_data_returns_none_on_non_request_exception."""

        class _BadTextResponse:
            status_code = 200

            def raise_for_status(self):
                pass

            @property
            def text(self):
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

        driver._control_cache = {
            "pow": "1",
            "mode": "3",
            "stemp": "22.0",
            "shum": "0",
            "f_rate": "A",
            "f_dir": "0",
        }
        driver._control_cache_ts = time.monotonic()
        with patch("requests.get", return_value=_BadTextResponse()):
            ok = driver.set_mode("cool")

        assert ok is False
        assert driver.last_error is not None

    # ── set_temperature ───────────────────────────────────────────────────────

    def test_set_temperature_clamps_to_min(self, driver):
        driver._control_cache = {
            "pow": "1",
            "mode": "3",
            "stemp": "22.0",
            "shum": "0",
            "f_rate": "A",
            "f_dir": "0",
        }
        driver._control_cache_ts = time.monotonic()
        with patch("requests.get", return_value=_make_response(_SET_OK)) as mock_get:
            driver.set_temperature(5.0)

        qs = mock_get.call_args[0][0]
        assert "stemp=10.0" in qs

    def test_set_temperature_clamps_to_max(self, driver):
        driver._control_cache = {
            "pow": "1",
            "mode": "3",
            "stemp": "22.0",
            "shum": "0",
            "f_rate": "A",
            "f_dir": "0",
        }
        driver._control_cache_ts = time.monotonic()
        with patch("requests.get", return_value=_make_response(_SET_OK)) as mock_get:
            driver.set_temperature(50.0)

        qs = mock_get.call_args[0][0]
        assert "stemp=32.0" in qs

    def test_set_temperature_rounds_to_half_degree(self, driver):
        driver._control_cache = {
            "pow": "1",
            "mode": "3",
            "stemp": "22.0",
            "shum": "0",
            "f_rate": "A",
            "f_dir": "0",
        }
        driver._control_cache_ts = time.monotonic()
        with patch("requests.get", return_value=_make_response(_SET_OK)) as mock_get:
            driver.set_temperature(21.3)

        qs = mock_get.call_args[0][0]
        assert "stemp=21.5" in qs

    def test_set_temperature_turns_on_when_off(self, driver):
        driver._control_cache = {
            "pow": "0",
            "mode": "0",
            "stemp": "22.0",
            "shum": "0",
            "f_rate": "A",
            "f_dir": "0",
        }
        driver._control_cache_ts = time.monotonic()
        with patch("requests.get", return_value=_make_response(_SET_OK)) as mock_get:
            driver.set_temperature(22.0)

        qs = mock_get.call_args[0][0]
        assert "pow=1" in qs

    def test_set_temperature_switches_out_of_fan_mode(self, driver):
        driver._control_cache = {
            "pow": "1",
            "mode": "6",
            "stemp": "--",
            "shum": "--",
            "f_rate": "A",
            "f_dir": "0",
        }
        driver._control_cache_ts = time.monotonic()
        with patch("requests.get", return_value=_make_response(_SET_OK)) as mock_get:
            driver.set_temperature(24.0)

        qs = mock_get.call_args[0][0]
        assert "mode=1" in qs  # auto (not fan)

    def test_set_temperature_returns_false_on_cache_miss_and_fail(self, driver):
        from requests.exceptions import ConnectionError as ReqConnError

        with patch("requests.get", side_effect=ReqConnError("down")):
            ok = driver.set_temperature(22.0)

        assert ok is False

    def test_set_temperature_returns_false_on_non_request_exception(self, driver):
        """Regression test for H7 — see test_get_data_returns_none_on_non_request_exception."""

        class _BadTextResponse:
            status_code = 200

            def raise_for_status(self):
                pass

            @property
            def text(self):
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

        driver._control_cache = {
            "pow": "1",
            "mode": "3",
            "stemp": "22.0",
            "shum": "0",
            "f_rate": "A",
            "f_dir": "0",
        }
        driver._control_cache_ts = time.monotonic()
        with patch("requests.get", return_value=_BadTextResponse()):
            ok = driver.set_temperature(22.0)

        assert ok is False
        assert driver.last_error is not None

    # ── set_fan_speed ─────────────────────────────────────────────────────────

    def test_set_fan_speed_high(self, driver):
        driver._control_cache = {
            "pow": "1",
            "mode": "3",
            "stemp": "22.0",
            "shum": "0",
            "f_rate": "A",
            "f_dir": "0",
        }
        driver._control_cache_ts = time.monotonic()
        with patch("requests.get", return_value=_make_response(_SET_OK)) as mock_get:
            ok = driver.set_fan_speed("5")  # level 5 → f_rate=7

        assert ok is True
        qs = mock_get.call_args[0][0]
        assert "f_rate=7" in qs

    def test_set_fan_speed_silent(self, driver):
        driver._control_cache = {
            "pow": "1",
            "mode": "3",
            "stemp": "22.0",
            "shum": "0",
            "f_rate": "7",
            "f_dir": "0",
        }
        driver._control_cache_ts = time.monotonic()
        with patch("requests.get", return_value=_make_response(_SET_OK)) as mock_get:
            ok = driver.set_fan_speed("silent")

        assert ok is True
        qs = mock_get.call_args[0][0]
        assert "f_rate=B" in qs

    def test_set_fan_speed_unknown_returns_false(self, driver):
        ok = driver.set_fan_speed("turbo_max")
        assert ok is False

    def test_set_fan_speed_returns_false_on_network_fail(self, driver):
        from requests.exceptions import ConnectionError as ReqConnError

        with patch("requests.get", side_effect=ReqConnError("down")):
            ok = driver.set_fan_speed("auto")

        assert ok is False

    def test_set_fan_speed_returns_false_on_non_request_exception(self, driver):
        """Regression test for H7 — see test_get_data_returns_none_on_non_request_exception."""

        class _BadTextResponse:
            status_code = 200

            def raise_for_status(self):
                pass

            @property
            def text(self):
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

        driver._control_cache = {
            "pow": "1",
            "mode": "3",
            "stemp": "22.0",
            "shum": "0",
            "f_rate": "A",
            "f_dir": "0",
        }
        driver._control_cache_ts = time.monotonic()
        with patch("requests.get", return_value=_BadTextResponse()):
            ok = driver.set_fan_speed("auto")

        assert ok is False
        assert driver.last_error is not None

    # ── _control_params_from_response ─────────────────────────────────────────

    def test_control_params_filters_to_writable_fields_only(self):
        from energy_optimizer_drivers.ac.daikin_brp import _control_params_from_response

        response = {
            "pow": "1",
            "mode": "3",
            "stemp": "22.0",
            "shum": "0",
            "f_rate": "A",
            "f_dir": "0",
            # read-only extras that cause PARAM NG:
            "b_mode": "3",
            "b_stemp": "22.0",
            "b_shum": "0",
            "adv": "",
            "dt1": "22.0",
            "dh1": "0",
            "en_hol": "0",
        }
        result = _control_params_from_response(response)

        assert set(result.keys()) == {"pow", "mode", "stemp", "shum", "f_rate", "f_dir"}

    # ── config_schema / setup_guide ───────────────────────────────────────────

    def test_config_schema_has_required_fields(self):
        from energy_optimizer_drivers.ac.daikin_brp import DaikinBrpDriver

        schema = DaikinBrpDriver.config_schema()
        keys = [f["key"] for f in schema]
        assert "ip" in keys
        assert "room" in keys
        assert "timeout" in keys

    def test_setup_guide_returns_markdown(self):
        from energy_optimizer_drivers.ac.daikin_brp import DaikinBrpDriver

        guide = DaikinBrpDriver.setup_guide()
        assert guide is not None
        assert "Daikin" in guide

    # ── discover ──────────────────────────────────────────────────────────────

    def test_discover_returns_found_device(self):
        from energy_optimizer_drivers.ac.daikin_brp import DaikinBrpDriver

        with patch("socket.socket") as mock_sock_cls, patch(
            "energy_optimizer_drivers.ac.daikin_brp._probe_daikin"
        ) as mock_probe:

            # Make getsockname return a local IP
            mock_sock = MagicMock()
            mock_sock.getsockname.return_value = (_TEST_LOCAL_IP, 0)
            mock_sock_cls.return_value = mock_sock

            # One adapter found at _TEST_IP
            mock_probe.side_effect = lambda ip: ({"ip": ip} if ip == _TEST_IP else None)

            result = DaikinBrpDriver.discover()

        assert len(result.devices) == 1
        assert result.devices[0]["ip"] == _TEST_IP

    def test_discover_no_adapters_returns_warning(self):
        from energy_optimizer_drivers.ac.daikin_brp import DaikinBrpDriver

        with patch("socket.socket") as mock_sock_cls, patch(
            "energy_optimizer_drivers.ac.daikin_brp._probe_daikin", return_value=None
        ):

            mock_sock = MagicMock()
            mock_sock.getsockname.return_value = (_TEST_LOCAL_IP, 0)
            mock_sock_cls.return_value = mock_sock

            result = DaikinBrpDriver.discover()

        assert result.devices == []
        assert len(result.warnings) > 0

    def test_discover_socket_error_returns_warning(self):
        from energy_optimizer_drivers.ac.daikin_brp import DaikinBrpDriver

        with patch("socket.socket") as mock_sock_cls:
            mock_sock = MagicMock()
            mock_sock.connect.side_effect = OSError("no route")
            mock_sock_cls.return_value = mock_sock

            result = DaikinBrpDriver.discover()

        assert result.devices == []
        assert len(result.warnings) > 0

    # ── _probe_daikin ─────────────────────────────────────────────────────────

    def test_probe_daikin_returns_ip_on_match(self):
        from energy_optimizer_drivers.ac.daikin_brp import _probe_daikin

        body = "ret=OK,type=aircon,reg=eu"
        with patch("requests.get", return_value=_make_response(body)):
            result = _probe_daikin(_TEST_IP)

        assert result == {"ip": _TEST_IP}

    def test_probe_daikin_returns_none_for_non_aircon(self):
        from energy_optimizer_drivers.ac.daikin_brp import _probe_daikin

        body = "ret=OK,type=heating"
        with patch("requests.get", return_value=_make_response(body)):
            result = _probe_daikin(_TEST_IP)

        assert result is None

    def test_probe_daikin_returns_none_on_exception(self):
        from energy_optimizer_drivers.ac.daikin_brp import _probe_daikin

        with patch("requests.get", side_effect=Exception("boom")):
            result = _probe_daikin(_TEST_IP)

        assert result is None
