"""Regression tests for the Aurora RS-485 driver, guarding against three specific
mistakes that are easy to make given how the Aurora binary protocol actually works:

  Bug E — Frame length: a request must be exactly 10 bytes (8 data + 2 CRC).
           The CRC is computed over all 8 data bytes, not a subset.
  Bug F — Energy decoding: values must be read with struct.unpack(">I") (uint32),
           not (">f") (float) — using the wrong format silently produces garbage values
           rather than raising.
  Bug G — Response address check: byte 0 of a response is the Global Alarm State
           (0 = no alarms), NOT an echo of the request's target address — treating it
           as an address echo incorrectly rejects every valid response.
"""

import struct
from unittest.mock import MagicMock, patch

import pytest

from energy_optimizer_drivers.base import DeviceType, DiscoveryResult
from energy_optimizer_drivers.contract_validation import validate_contract_data
from energy_optimizer_drivers.pv.aurora_rs485 import AuroraRS485Driver, _crc16

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_driver(address: int = 2) -> AuroraRS485Driver:
    return AuroraRS485Driver({"address": address, "baudrate": 19200})


def _make_serial_mock(response: bytes) -> MagicMock:
    """Build a mock serial port that returns *response* from read()."""
    mock_serial = MagicMock()
    mock_serial.timeout = 1
    mock_serial.read.return_value = response
    return mock_serial


def _make_serial_mock_single_match() -> MagicMock:
    """Build a mock serial port where only the very first read() call returns
    a valid Aurora response -- every later call (any address/baud) returns a
    CRC-mismatching one. A constant response would match every probed
    address, since _probe_address() doesn't check that the response echoes
    back the requested address (see Bug G in this file's module docstring)."""
    import itertools

    good_response = _build_response(struct.pack(">f", 1.0))
    bad_response = bytes([0, 0, 0, 0, 0, 0, 0xFF, 0xFF])
    mock_serial = _make_serial_mock(bad_response)
    mock_serial.read.side_effect = itertools.chain(
        [good_response], itertools.repeat(bad_response)
    )
    return mock_serial


def _build_response(value_bytes: bytes, alarm: int = 0, tx_state: int = 0) -> bytes:
    """Build a valid 8-byte Aurora response with correct CRC.

    Aurora response layout (8 bytes total):
      byte 0: Global Alarm State  (0 = no alarms)
      byte 1: Tx state
      bytes 2-5: 4-byte payload (float for DSP, uint32 for energy)
      bytes 6-7: CRC-16 of bytes 0-5
    """
    assert len(value_bytes) == 4
    resp_data = bytes([alarm, tx_state]) + value_bytes
    crc_lo, crc_hi = _crc16(resp_data)
    return resp_data + bytes([crc_lo, crc_hi])


# ---------------------------------------------------------------------------
# CRC helper sanity check
# ---------------------------------------------------------------------------


class TestCRC16:
    """The _crc16 helper returns the correct CRC-16/X-25 over arbitrary bytes."""

    def test_returns_tuple_of_two_bytes(self) -> None:
        lo, hi = _crc16(b"\x02\x3b\x03\x00\x00\x00\x00\x00")
        assert 0 <= lo <= 255
        assert 0 <= hi <= 255

    def test_different_payloads_give_different_crcs(self) -> None:
        lo1, hi1 = _crc16(b"\x02\x3b\x03\x00\x00\x00\x00\x00")
        lo2, hi2 = _crc16(b"\x02\x3b\x04\x00\x00\x00\x00\x00")
        assert (lo1, hi1) != (lo2, hi2)

    def test_empty_gives_crc_zero(self) -> None:
        # X-25 CRC of empty bytes is 0xFFFF XOR 0xFFFF = 0x0000
        lo, hi = _crc16(b"")
        assert lo == 0
        assert hi == 0


# ---------------------------------------------------------------------------
# Bug E — Frame length: request must be exactly 10 bytes
# ---------------------------------------------------------------------------


class TestAuroraFrameConstruction:
    """_send_command must build a 10-byte frame: 8 data bytes + 2 CRC bytes."""

    def test_send_command_writes_10_byte_frame(self) -> None:
        driver = _make_driver()
        driver._serial = _make_serial_mock(_build_response(struct.pack(">f", 42.0)))

        driver._send_command(0x3B, subcmd=3)

        written: bytes = driver._serial.write.call_args[0][0]
        assert len(written) == 10

    def test_frame_first_byte_is_driver_address(self) -> None:
        driver = _make_driver(address=2)
        driver._serial = _make_serial_mock(_build_response(struct.pack(">f", 0.0)))

        driver._send_command(0x3B, subcmd=3)

        frame: bytes = driver._serial.write.call_args[0][0]
        assert frame[0] == 2

    def test_crc_appended_to_8_data_bytes(self) -> None:
        """Bytes 8-9 of the frame must equal _crc16(frame[0:8])."""
        driver = _make_driver(address=2)
        driver._serial = _make_serial_mock(_build_response(struct.pack(">f", 0.0)))

        driver._send_command(0x3B, subcmd=3)

        frame: bytes = driver._serial.write.call_args[0][0]
        expected_lo, expected_hi = _crc16(frame[:8])
        assert frame[8] == expected_lo
        assert frame[9] == expected_hi

    def test_crc_covers_all_8_data_bytes_not_6(self) -> None:
        """CRC must be over bytes 0-7 (8 bytes), not 0-5 (6 bytes) like in the response."""
        driver = _make_driver(address=2)
        driver._serial = _make_serial_mock(_build_response(struct.pack(">f", 0.0)))

        driver._send_command(0x4E, subcmd=0)  # energy command

        frame: bytes = driver._serial.write.call_args[0][0]
        # Verify 8-byte CRC
        lo_8, hi_8 = _crc16(frame[:8])
        assert frame[8] == lo_8
        assert frame[9] == hi_8
        # Verify only the 8-byte CRC — this is the regression guard
        assert frame[8] == lo_8  # 8-byte CRC is correct

    def test_frame_contains_command_byte(self) -> None:
        """Second byte of frame must be the command byte passed in."""
        driver = _make_driver(address=2)
        driver._serial = _make_serial_mock(_build_response(struct.pack(">f", 0.0)))

        driver._send_command(0x3B, subcmd=3)

        frame: bytes = driver._serial.write.call_args[0][0]
        assert frame[1] == 0x3B

    def test_subcmd_placed_at_byte_2(self) -> None:
        driver = _make_driver(address=2)
        driver._serial = _make_serial_mock(_build_response(struct.pack(">f", 0.0)))

        driver._send_command(0x3B, subcmd=21)

        frame: bytes = driver._serial.write.call_args[0][0]
        assert frame[2] == 21


# ---------------------------------------------------------------------------
# Bug F — Energy decoding must use uint32 (">I"), not float (">f")
# ---------------------------------------------------------------------------


class TestAuroraEnergyDecoding:
    """_read_energy decodes bytes 2-5 of the response as unsigned 32-bit integer."""

    def test_decodes_correct_uint32_value(self) -> None:
        energy_wh = 1_500_000  # 1.5 MWh in Wh
        driver = _make_driver()
        driver._serial = _make_serial_mock(
            _build_response(struct.pack(">I", energy_wh))
        )

        result = driver._read_energy(0)

        assert result == pytest.approx(1_500_000.0)

    def test_result_differs_from_float_interpretation(self) -> None:
        """The same bytes decoded as float give a wildly different (wrong) value."""
        energy_wh = 1_500_000
        energy_bytes = struct.pack(">I", energy_wh)

        driver = _make_driver()
        driver._serial = _make_serial_mock(_build_response(energy_bytes))

        result = driver._read_energy(0)

        float_misread = struct.unpack(">f", energy_bytes)[0]
        assert result == pytest.approx(1_500_000.0)
        assert result is not None
        assert abs(result - float_misread) > 100_000

    def test_zero_energy_returns_zero(self) -> None:
        driver = _make_driver()
        driver._serial = _make_serial_mock(_build_response(struct.pack(">I", 0)))

        result = driver._read_energy(0)

        assert result == pytest.approx(0.0)

    def test_large_lifetime_counter(self) -> None:
        """uint32 max (~4.3 GWh in Wh) must not overflow."""
        max_uint32 = 0xFFFFFFFF
        driver = _make_driver()
        driver._serial = _make_serial_mock(
            _build_response(struct.pack(">I", max_uint32))
        )

        result = driver._read_energy(0)

        assert result == pytest.approx(float(max_uint32), rel=1e-6)

    def test_daily_energy_period_sends_correct_command(self) -> None:
        """_read_energy uses _CMD_ENERGY (0x4E) — verify by inspecting frame byte 1."""
        driver = _make_driver(address=2)
        driver._serial = _make_serial_mock(_build_response(struct.pack(">I", 1000)))

        driver._read_energy(0)  # period 0 = daily

        frame: bytes = driver._serial.write.call_args[0][0]
        assert frame[1] == 0x4E  # _CMD_ENERGY


# ---------------------------------------------------------------------------
# Bug G — No response address check: byte 0 is alarm state, not address echo
# ---------------------------------------------------------------------------


class TestAuroraAlarmStateResponse:
    """byte 0 of the inverter response is the Global Alarm State, not the address."""

    def test_response_with_alarm_zero_is_accepted(self) -> None:
        """Driver at address 2; response byte 0 = 0 (no alarm) — must NOT be rejected.

        The old buggy code checked resp[0] != self._address, which would reject
        every valid response since alarm=0 never equals address=2.
        """
        driver = _make_driver(address=2)
        driver._serial = _make_serial_mock(
            _build_response(struct.pack(">f", 1234.5), alarm=0)
        )

        result = driver._send_command(0x3B, subcmd=3)

        assert result is not None
        assert result[0] == 0  # alarm state byte confirmed

    def test_byte_0_is_not_the_address(self) -> None:
        """Byte 0 of a valid response is 0 (no alarms), not 2 (address)."""
        driver = _make_driver(address=2)
        driver._serial = _make_serial_mock(
            _build_response(struct.pack(">f", 500.0), alarm=0)
        )

        result = driver._send_command(0x3B, subcmd=3)

        assert result is not None
        assert result[0] != 2  # NOT the address

    def test_valid_crc_with_nonzero_alarm_is_accepted(self) -> None:
        """Non-zero alarm byte = inverter warning, but frame is still valid."""
        driver = _make_driver(address=2)
        driver._serial = _make_serial_mock(
            _build_response(struct.pack(">f", 0.0), alarm=1)
        )

        result = driver._send_command(0x3B, subcmd=3)

        assert result is not None
        assert result[0] == 1  # alarm state reported back

    def test_bad_crc_still_rejected(self) -> None:
        """CRC errors are caught regardless of the alarm byte value."""
        driver = _make_driver(address=2)
        resp_data = bytes([0, 0, 0x45, 0x9A, 0x40, 0x00])
        bad_response = resp_data + bytes([0xFF, 0xFF])  # intentionally wrong CRC
        driver._serial = _make_serial_mock(bad_response)

        result = driver._send_command(0x3B, subcmd=3)

        assert result is None

    def test_short_response_returns_none(self) -> None:
        driver = _make_driver(address=2)
        driver._serial = _make_serial_mock(bytes([0, 0, 0, 0]))  # only 4 bytes

        result = driver._send_command(0x3B, subcmd=3)

        assert result is None

    def test_dsp_read_with_alarm_zero_returns_float(self) -> None:
        """_read_dsp must return the float value when alarm byte is 0 (not address)."""
        expected_w = 1400.5
        driver = _make_driver(address=2)
        driver._serial = _make_serial_mock(
            _build_response(struct.pack(">f", expected_w), alarm=0)
        )

        result = driver._read_dsp(3)  # DSP grid power

        assert result == pytest.approx(expected_w, rel=1e-4)


# ---------------------------------------------------------------------------
# get_status(): a genuinely missing port ("error") vs. the port opening fine
# but the inverter not responding ("sleeping" — indistinguishable from the
# inverter's normal overnight power-down, since a solar inverter with no
# sunlight simply stops responding rather than reporting an explicit state).
# ---------------------------------------------------------------------------


class TestAuroraGetStatus:
    def test_fresh_driver_is_sleeping(self) -> None:
        driver = _make_driver()
        assert driver.get_status() == "sleeping"

    def test_port_open_failure_is_a_real_error(self) -> None:
        """The device node itself doesn't exist (e.g. missing docker-compose
        device passthrough) — an unambiguous configuration error, not sleep."""
        driver = _make_driver()
        with patch(
            "serial.Serial",
            side_effect=OSError(
                "could not open port /dev/ttyUSB0: No such file or directory"
            ),
        ):
            result = driver.get_data()

        assert result is None
        assert driver.get_status() == "error"
        assert driver.last_error is not None
        assert "No such file or directory" in driver.last_error

    def test_port_open_but_no_response_is_still_sleeping(self) -> None:
        """The port exists but the inverter answers nothing — this is what a
        real inverter does every night, so it must stay "sleeping", not
        "error"."""
        driver = _make_driver()
        mock_serial = _make_serial_mock(b"")  # read() returns no bytes
        with patch("serial.Serial", return_value=mock_serial):
            result = driver.get_data()

        assert result is None
        assert driver.get_status() == "sleeping"

    def test_connected_after_successful_read(self) -> None:
        driver = _make_driver(address=2)
        response = _build_response(struct.pack(">f", 500.0), alarm=0)
        mock_serial = _make_serial_mock(response)
        with patch("serial.Serial", return_value=mock_serial):
            result = driver.get_data()

        assert result is not None
        assert driver.get_status() == "connected"
        assert validate_contract_data(DeviceType.PV_INVERTER, result) == []

    def test_going_quiet_after_a_prior_success_is_sleeping_not_error(self) -> None:
        """Regression test for the core bug: previously, _last_error was
        checked before _last_success in get_status(), so once an inverter had
        connected at least once, every subsequent no-response night made
        get_status() return "error" forever — the API layer then had to
        collapse "error" into "sleeping" to compensate, which also hid real
        errors (like a missing serial port) behind the same "Sleeping" badge.
        Now a no-response read after a prior success correctly stays
        "sleeping" without needing that API-level workaround."""
        driver = _make_driver(address=2)
        response = _build_response(struct.pack(">f", 500.0), alarm=0)
        mock_serial = _make_serial_mock(response)
        with patch("serial.Serial", return_value=mock_serial):
            assert driver.get_data() is not None
        assert driver.get_status() == "connected"

        driver._serial.read.return_value = b""  # inverter goes quiet overnight
        result = driver.get_data()

        assert result is None
        assert driver.get_status() == "sleeping"

    def test_port_reopens_after_a_previous_open_failure(self) -> None:
        driver = _make_driver()
        with patch("serial.Serial", side_effect=OSError("No such file or directory")):
            driver.get_data()
        assert driver.get_status() == "error"

        response = _build_response(struct.pack(">f", 500.0), alarm=0)
        mock_serial = _make_serial_mock(response)
        with patch("serial.Serial", return_value=mock_serial):
            result = driver.get_data()

        assert result is not None
        assert driver.get_status() == "connected"


# ---------------------------------------------------------------------------
# get_data(): the lock-timeout guard and the outer exception handler
# ---------------------------------------------------------------------------


class TestGetDataFailureModes:
    def test_lock_timeout_closes_the_port_and_returns_none(self) -> None:
        """A previous call stuck holding the lock -> reset the port and bail
        rather than chain-blocking behind it. Mocks acquire() itself rather
        than really contending for the lock -- get_data()'s real 10s timeout
        would otherwise make this test actually take 10 real seconds."""
        driver = _make_driver()
        driver._serial = _make_serial_mock(b"")
        # A real threading.Lock's acquire() is a read-only C attribute and
        # can't be patched in place -- swap the whole lock for a mock instead.
        mock_lock = MagicMock()
        mock_lock.acquire.return_value = False
        driver._lock = mock_lock
        result = driver.get_data()

        mock_lock.acquire.assert_called_once_with(timeout=10.0)
        assert result is None
        assert driver._serial is None
        assert driver.last_error == "Read timed out — serial port reset"

    def test_reset_serial_on_lock_timeout_tolerates_a_broken_close(self) -> None:
        """If the stuck port also fails to close cleanly, still reset state
        rather than propagating the close() error."""
        driver = _make_driver()
        broken_serial = MagicMock()
        broken_serial.close.side_effect = OSError("already gone")
        driver._serial = broken_serial

        driver._reset_serial_on_lock_timeout()

        assert driver._serial is None
        assert driver.last_error == "Read timed out — serial port reset"

    def test_unexpected_exception_during_read_is_caught(self) -> None:
        """Any exception mid-read (not just OSError from opening the port)
        must be caught, recorded, and the port discarded -- not propagated."""
        driver = _make_driver(address=2)
        mock_serial = _make_serial_mock(b"")
        mock_serial.write.side_effect = RuntimeError("simulated driver bug")
        with patch("serial.Serial", return_value=mock_serial):
            result = driver.get_data()

        assert result is None
        assert driver._serial is None
        assert driver.last_error == "simulated driver bug"

    def test_incomplete_read_without_a_prior_error_gets_a_synthesized_one(
        self,
    ) -> None:
        """Defensive fallback: if a read comes back incomplete without
        _send_command having already recorded a reason (structurally
        shouldn't happen today, since every _send_command failure path sets
        _last_error itself), get_data() still records *something* rather than
        silently returning None with last_error still unset."""
        driver = _make_driver(address=2)
        mock_serial = _make_serial_mock(b"")
        with (
            patch("serial.Serial", return_value=mock_serial),
            patch.object(driver, "_read_dsp", return_value=None),
            patch.object(driver, "_read_energy", return_value=None),
        ):
            result = driver.get_data()

        assert result is None
        assert driver.last_error is not None
        assert "did not respond" in driver.last_error


# ---------------------------------------------------------------------------
# discover(): _open_serial()'s three failure branches, _probe_address(), and
# the overall scan loop
# ---------------------------------------------------------------------------


class TestSetupGuideAndConfigSchema:
    def test_setup_guide_mentions_wiring_and_baud_rate(self) -> None:
        guide = AuroraRS485Driver.setup_guide()
        assert guide is not None
        assert "RS-485" in guide
        assert "19200" in guide

    def test_config_schema_requires_address_not_baudrate(self) -> None:
        schema = AuroraRS485Driver.config_schema()
        keys = {field["key"]: field for field in schema}
        assert keys["address"]["required"] is True
        assert keys["baudrate"]["required"] is False

    def test_config_schema_port_is_optional_and_defaults_to_ttyUSB0(self) -> None:
        """Regression guard: the port must stay a configurable field, not a
        hardcoded constant -- see aurora_rs485.py's _DEFAULT_PORT docstring
        for why (it's a fact about the base station's own USB topology, not
        about this inverter model)."""
        schema = AuroraRS485Driver.config_schema()
        keys = {field["key"]: field for field in schema}
        assert keys["port"]["required"] is False
        assert keys["port"].get("default") == "/dev/ttyUSB0"


class TestAuroraConfigurablePort:
    def test_defaults_to_ttyUSB0_when_not_configured(self) -> None:
        driver = AuroraRS485Driver({"address": 2})
        mock_serial = _make_serial_mock(b"")  # empty response -- only the open() call matters here
        with patch("serial.Serial", return_value=mock_serial) as mock_serial_cls:
            driver.get_data()

        assert mock_serial_cls.call_args.kwargs["port"] == "/dev/ttyUSB0"

    def test_uses_the_configured_port_override(self) -> None:
        driver = AuroraRS485Driver({"address": 2, "port": "/dev/aurora"})
        mock_serial = _make_serial_mock(b"")  # empty response -- only the open() call matters here
        with patch("serial.Serial", return_value=mock_serial) as mock_serial_cls:
            driver.get_data()

        assert mock_serial_cls.call_args.kwargs["port"] == "/dev/aurora"


class TestOpenSerial:
    def test_permission_error_reports_port_busy(self) -> None:
        mock_serial_mod = MagicMock()
        mock_serial_mod.Serial.side_effect = PermissionError("in use")

        result = AuroraRS485Driver._open_serial(mock_serial_mod, "/dev/ttyUSB0", 19200)

        assert isinstance(result, DiscoveryResult)
        assert "/dev/ttyUSB0" in result.warnings[0]
        assert "busy" in result.warnings[0]

    def test_file_not_found_returns_none_not_a_discovery_result(self) -> None:
        """None (not a DiscoveryResult) is the signal discover() uses to try
        the next candidate port silently -- a missing device at one of
        several possible paths isn't a real error worth its own warning."""
        mock_serial_mod = MagicMock()
        mock_serial_mod.Serial.side_effect = FileNotFoundError("no such device")

        result = AuroraRS485Driver._open_serial(mock_serial_mod, "/dev/ttyUSB0", 19200)

        assert result is None

    def test_other_exception_reports_a_generic_connection_problem(self) -> None:
        mock_serial_mod = MagicMock()
        mock_serial_mod.Serial.side_effect = RuntimeError("weird OS-level failure")

        result = AuroraRS485Driver._open_serial(mock_serial_mod, "/dev/ttyUSB0", 19200)

        assert isinstance(result, DiscoveryResult)
        assert "/dev/ttyUSB0" in result.warnings[0]

    def test_success_returns_the_serial_object_not_a_discovery_result(self) -> None:
        mock_serial_mod = MagicMock()
        opened = MagicMock()
        mock_serial_mod.Serial.return_value = opened

        result = AuroraRS485Driver._open_serial(mock_serial_mod, "/dev/ttyUSB0", 19200)

        assert result is opened


class TestProbeAddress:
    def test_returns_true_for_a_valid_response(self) -> None:
        response = _build_response(struct.pack(">f", 100.0), alarm=0)
        ser = _make_serial_mock(response)

        assert AuroraRS485Driver._probe_address(ser, 2) is True

    def test_returns_false_for_a_short_response(self) -> None:
        ser = _make_serial_mock(b"\x00\x00")

        assert AuroraRS485Driver._probe_address(ser, 2) is False

    def test_returns_false_for_a_bad_crc(self) -> None:
        ser = _make_serial_mock(bytes([0, 0, 0, 0, 0, 0, 0xFF, 0xFF]))

        assert AuroraRS485Driver._probe_address(ser, 2) is False


class TestDiscover:
    def test_returns_a_warning_when_pyserial_is_not_installed(self) -> None:
        with patch.dict("sys.modules", {"serial": None}):
            result = AuroraRS485Driver.discover()

        assert "pyserial is not installed" in result.warnings[0]

    def test_discover_quick_falls_back_to_discover_when_not_overridden(self) -> None:
        """AuroraRS485Driver has nothing to ARP-pre-filter (RS-485/serial,
        not IP-based) and correctly doesn't override discover_quick() --
        confirms BaseDriver's default (call discover() unchanged) actually
        kicks in for a real driver, not just in isolation against a mock."""
        with patch.dict("sys.modules", {"serial": None}):
            result = AuroraRS485Driver.discover_quick()

        assert "pyserial is not installed" in result.warnings[0]

    def test_open_failure_returns_the_open_serial_warning_with_empty_devices(
        self,
    ) -> None:
        mock_serial_mod = MagicMock()
        mock_serial_mod.Serial.side_effect = FileNotFoundError("no adapter")
        with patch.dict("sys.modules", {"serial": mock_serial_mod}):
            result = AuroraRS485Driver.discover()

        assert result.devices == []
        assert "No USB-to-RS485 adapter detected" in result.warnings[0]

    def test_finds_an_inverter_at_the_first_probed_baudrate(self) -> None:
        """discover() doesn't stop at the first find -- it exhaustively probes
        every baud rate too (skipping addresses already found). The very
        first read() call ever (address 1, baud 19200) succeeds; every other
        call, at any address/baud, fails -- avoids ambiguity from the
        already-found address being skipped in later baud-rate passes."""
        import itertools

        good_response = _build_response(struct.pack(">f", 1.0))
        bad_response = bytes([0, 0, 0, 0, 0, 0, 0xFF, 0xFF])

        mock_serial_mod = MagicMock()
        opened = _make_serial_mock(bad_response)
        opened.read.side_effect = itertools.chain(
            [good_response], itertools.repeat(bad_response)
        )
        mock_serial_mod.Serial.return_value = opened

        with patch.dict("sys.modules", {"serial": mock_serial_mod}):
            result = AuroraRS485Driver.discover()

        assert result.devices == [
            {"address": 1, "baudrate": 19200, "port": AuroraRS485Driver._DEFAULT_PORT}
        ]
        opened.close.assert_called()

    def test_no_response_on_any_address_or_baudrate_reports_a_warning(self) -> None:
        no_response = bytes()
        mock_serial_mod = MagicMock()
        opened = _make_serial_mock(no_response)
        mock_serial_mod.Serial.return_value = opened

        with patch.dict("sys.modules", {"serial": mock_serial_mod}):
            result = AuroraRS485Driver.discover()

        assert result.devices == []
        assert "No inverters responded" in result.warnings[0]

    # ── Multi-port scanning: a customer's adapter isn't guaranteed to land on
    #    the first candidate path, so discover() has to try several ────────

    def test_tries_the_next_candidate_port_when_the_first_is_missing(self) -> None:
        matching_mock = _make_serial_mock_single_match()

        def serial_side_effect(*args: object, **kwargs: object) -> MagicMock:
            if kwargs["port"] == "/dev/ttyUSB0":
                raise FileNotFoundError("no such device")
            return matching_mock

        mock_serial_mod = MagicMock()
        mock_serial_mod.Serial.side_effect = serial_side_effect

        with patch.dict("sys.modules", {"serial": mock_serial_mod}):
            result = AuroraRS485Driver.discover()

        assert result.devices == [
            {"address": 1, "baudrate": 19200, "port": "/dev/ttyUSB1"}
        ]

    def test_falls_back_to_ttyacm_when_no_ttyusb_path_exists(self) -> None:
        matching_mock = _make_serial_mock_single_match()

        def serial_side_effect(*args: object, **kwargs: object) -> MagicMock:
            if kwargs["port"] == "/dev/ttyACM0":
                return matching_mock
            raise FileNotFoundError("no such device")

        mock_serial_mod = MagicMock()
        mock_serial_mod.Serial.side_effect = serial_side_effect

        with patch.dict("sys.modules", {"serial": mock_serial_mod}):
            result = AuroraRS485Driver.discover()

        assert result.devices == [
            {"address": 1, "baudrate": 19200, "port": "/dev/ttyACM0"}
        ]

    def test_stops_at_the_first_successful_port_without_trying_later_candidates(
        self,
    ) -> None:
        mock_serial_mod = MagicMock()
        mock_serial_mod.Serial.return_value = _make_serial_mock_single_match()

        with patch.dict("sys.modules", {"serial": mock_serial_mod}):
            AuroraRS485Driver.discover()

        ports_tried = {c.kwargs["port"] for c in mock_serial_mod.Serial.call_args_list}
        assert ports_tried == {AuroraRS485Driver._DEFAULT_PORT}

    def test_busy_port_falls_back_to_the_next_candidate(self) -> None:
        matching_mock = _make_serial_mock_single_match()

        def serial_side_effect(*args: object, **kwargs: object) -> MagicMock:
            if kwargs["port"] == "/dev/ttyUSB0":
                raise PermissionError("in use")
            return matching_mock

        mock_serial_mod = MagicMock()
        mock_serial_mod.Serial.side_effect = serial_side_effect

        with patch.dict("sys.modules", {"serial": mock_serial_mod}):
            result = AuroraRS485Driver.discover()

        assert result.devices == [
            {"address": 1, "baudrate": 19200, "port": "/dev/ttyUSB1"}
        ]

    def test_a_port_that_opened_but_found_nothing_gives_a_specific_warning(
        self,
    ) -> None:
        """If one candidate genuinely exists (opens fine) but no inverter
        responds, that's a stronger, more actionable signal than "no adapter
        found at all" -- worth naming which port it was."""
        no_response = bytes()
        mock_serial_mod = MagicMock()
        mock_serial_mod.Serial.return_value = _make_serial_mock(no_response)

        with patch.dict("sys.modules", {"serial": mock_serial_mod}):
            result = AuroraRS485Driver.discover()

        assert "No inverters responded on" in result.warnings[0]
        assert "/dev/tty" in result.warnings[0]
