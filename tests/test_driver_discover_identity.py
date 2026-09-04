"""Tests for the "identity" (serial/MAC) extraction added to discover() probes.

Covers ionemo_drivers/grid/homewizard_p1.py's _probe_homewizard and
ionemo_drivers/ac/daikin_brp.py's _probe_daikin — the two drivers
that support device-reconnect-by-identity in the main ionemo-app.
All response shapes below are fabricated example data shaped to match each
device's real response format, not captured from any real device.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from ionemo_drivers.ac.daikin_brp import _probe_daikin
from ionemo_drivers.grid.homewizard_p1 import _probe_homewizard


class TestProbeHomewizardIdentity:
    def test_returns_identity_when_serial_present(self):
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {
            "product_type": "HWE-P1",
            "serial": "aabbccddeeff",
        }
        with patch(
            "ionemo_drivers.grid.homewizard_p1.requests.get", return_value=mock_response
        ):
            result = _probe_homewizard("192.0.2.178")

        assert result == {"ip": "192.0.2.178", "identity": "aabbccddeeff"}

    def test_omits_identity_when_serial_absent(self):
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {"product_type": "HWE-P1"}
        with patch(
            "ionemo_drivers.grid.homewizard_p1.requests.get", return_value=mock_response
        ):
            result = _probe_homewizard("192.0.2.178")

        assert result is not None
        assert result == {"ip": "192.0.2.178"}
        assert "identity" not in result

    def test_returns_none_for_non_homewizard_product(self):
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {
            "product_type": "SOME-OTHER-DEVICE",
            "serial": "deadbeef0000",
        }
        with patch(
            "ionemo_drivers.grid.homewizard_p1.requests.get", return_value=mock_response
        ):
            result = _probe_homewizard("192.0.2.99")

        assert result is None

    def test_returns_none_on_connection_error(self):
        with patch(
            "ionemo_drivers.grid.homewizard_p1.requests.get",
            side_effect=ConnectionError("network unreachable"),
        ):
            result = _probe_homewizard("192.0.2.178")

        assert result is None


class TestProbeDaikinIdentity:
    # Fabricated example data shaped to match a real device's basic_info response
    # format, not captured from any real device.
    _SAMPLE_BASIC_INFO = (
        "ret=OK,type=aircon,reg=eu,dst=1,ver=4_2_303,rev=30610C5A,pow=0,err=0,"
        "location=0,name=%4c%69%76%69%6e%67%20%52%6f%6f%6d,icon=0,instform=0,"
        "method=polling,port=30050,id=00000000-0000-0000-0000-000000000000,pw=,"
        "lpw_flag=0,adp_kind=3,pv=3.2,cpv=3,cpv_minor=20,led=1,en_setzone=1,"
        "mac=001122334455,ssid=DaikinAP00000,adp_mode=run,en_hol=0,radio1=-75,"
        "grp_name=,en_grp=0,sec_type=WPA2"
    )

    def test_returns_identity_from_real_device_response(self):
        mock_response = MagicMock(status_code=200, text=self._SAMPLE_BASIC_INFO)
        with patch("ionemo_drivers.ac.daikin_brp.requests.get", return_value=mock_response):
            result = _probe_daikin("192.0.2.153")

        assert result == {"ip": "192.0.2.153", "identity": "001122334455"}

    def test_omits_identity_when_mac_absent(self):
        mock_response = MagicMock(status_code=200, text="ret=OK,type=aircon,pow=0")
        with patch("ionemo_drivers.ac.daikin_brp.requests.get", return_value=mock_response):
            result = _probe_daikin("192.0.2.153")

        assert result is not None
        assert result == {"ip": "192.0.2.153"}
        assert "identity" not in result

    def test_returns_none_when_not_aircon(self):
        mock_response = MagicMock(status_code=200, text="ret=OK,type=other")
        with patch("ionemo_drivers.ac.daikin_brp.requests.get", return_value=mock_response):
            result = _probe_daikin("192.0.2.99")

        assert result is None

    def test_returns_none_on_connection_error(self):
        with patch(
            "ionemo_drivers.ac.daikin_brp.requests.get",
            side_effect=ConnectionError("network unreachable"),
        ):
            result = _probe_daikin("192.0.2.153")

        assert result is None
