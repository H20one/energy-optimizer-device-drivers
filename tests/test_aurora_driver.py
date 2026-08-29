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

from energy_optimizer_drivers.base import DeviceType
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
                "could not open port /dev/ttyUSB1: No such file or directory"
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
