"""Aurora Power-One Inverter — PV Inverter Driver.

Maps the Aurora RS-485 binary protocol to the PVInverterDriver contract.
Protocol reference: Power-One Aurora PVI Communication Protocol Rev. 5.1
"""

from __future__ import annotations

import logging
import struct
import threading
import time
from typing import Any

from energy_optimizer_drivers.base import (
    ConfigField,
    ConnectionType,
    DeviceType,
    DiscoveryResult,
    PVInverterData,
    PVInverterDriver,
)
from energy_optimizer_drivers.registry import register_driver

logger = logging.getLogger(__name__)

# ── Aurora protocol constants ─────────────────────────────────────────────────

_CMD_DSP = 0x3B
_CMD_ENERGY = 0x4E

_DSP_GRID_POWER_W = 3
_DSP_GRID_VOLTAGE_V = 1
_DSP_FREQ_HZ = 4
_DSP_DC_VOLTAGE_V = 23
_DSP_DC_CURRENT_A = 25
_DSP_TEMP_INV_C = 21

_ENERGY_DAILY_WH = 0
_ENERGY_TOTAL_WH = 4


def _crc16(data: bytes) -> tuple[int, int]:
    """CRC-16/X-25 used by the Aurora protocol."""
    crc = 0xFFFF
    for byte in data:
        for _ in range(8):
            if (crc & 1) ^ (byte & 1):
                crc = ((crc >> 1) ^ 0x8408) & 0xFFFF
            else:
                crc >>= 1
            byte >>= 1
    crc ^= 0xFFFF
    return crc & 0xFF, (crc >> 8) & 0xFF


class AuroraRS485Driver(PVInverterDriver):
    """PV inverter driver for Aurora Power-One via RS-485."""

    driver_id = "aurora_rs485"
    name = "Aurora Power-One PVI"
    manufacturer = "Power-One (ABB)"
    builder = "H20one"
    device_type = DeviceType.PV_INVERTER
    connection_type = ConnectionType.SERIAL

    # Default serial port when a device's config doesn't specify one (see
    # config_schema()'s "port" field). This is a Linux USB-serial enumeration
    # convention, not a fact about this inverter model -- the actual path
    # depends entirely on that specific base station's own USB topology (how
    # many other adapters are attached), so it must stay overridable rather
    # than hardcoded. The host app's own container has generic, identical
    # access to any USB-serial device regardless of installation (see the
    # main app repo's docker-compose.yml) -- nothing container-level to
    # configure per device, only which specific path this field should be.
    _DEFAULT_PORT = "/dev/ttyUSB0"
    _DEFAULT_BAUDRATE = 19200

    def __init__(self, config: dict[str, Any]) -> None:
        self._address: int = config["address"]
        self._baudrate: int = int(config.get("baudrate", self._DEFAULT_BAUDRATE))
        self._port: str = config.get("port", self._DEFAULT_PORT)
        self._last_error: str | None = None
        self._last_success: float | None = None
        self._serial: Any = None
        self._lock = threading.Lock()
        # Set only when the serial port itself can't be opened (e.g. the device
        # node is missing) — a genuine configuration/hardware-absence error, as
        # opposed to the port opening fine but the inverter not responding, which
        # is indistinguishable from the inverter's normal overnight sleep and is
        # reported as "sleeping" rather than "error" (see get_status()).
        self._port_unavailable = False

    @classmethod
    def setup_guide(cls) -> str | None:
        return (
            "## Aurora RS-485 Setup\n\n"
            "This driver connects to an Aurora (Power-One/ABB/FIMER) inverter via a "
            "USB-to-RS485 adapter. Communication uses **19200 baud, 8N1** "
            "(8 data bits, no parity, 1 stop bit).\n\n"
            "### What you need\n\n"
            "- A USB-to-RS485 adapter (FTDI or CH340 chipset recommended)\n"
            "- A shielded RS-485 cable (2 data wires + ground) between the "
            "adapter and the inverter's communication terminal block\n\n"
            "\n### 1. Physical Wiring\n\n"
            "Open the inverter's communication board and connect:\n\n"
            "| Inverter Terminal | RS-485 Adapter |\n"
            "|-------------------|----------------|\n"
            "| T/R+ | A (Data+) |\n"
            "| T/R- | B (Data-) |\n"
            "| GND / RNT | GND |\n\n"
            "Leave the 120\u03a9 termination resistor **OFF** unless the inverter "
            "is at the end of a long daisy-chain.\n\n"
            "\n### 2. Adapter Setup\n\n"
            "1. Plug the USB-to-RS485 adapter into your Ionemo base "
            "(always use the **same USB port** to keep the device path stable).\n"
            "2. If the adapter isn't recognized automatically, install the driver "
            "for your chipset (FTDI or CH340).\n"
            "3. Your Ionemo base station runs Linux, where the port defaults "
            "to `/dev/ttyUSB0` (the usual case with one adapter attached) -- "
            "this is a Linux device-path convention, not something specific "
            "to any one board, so it applies the same way on any Ionemo base "
            "regardless of model. If the default doesn't work, connect to "
            "the base station and run `ls /dev/ttyUSB*` to see what's "
            "actually there (some adapter chipsets show up as `/dev/ttyACM0` "
            "instead), then enter that in the **Serial Port** field below. "
            "Also override it if you have more than one USB-serial adapter "
            "and want to pick a specific one, or use a udev symlink for a "
            "name that survives a reboot.\n\n"
            "\n### 3. Inverter Settings\n\n"
            "Using the inverter's front panel or Aurora Manager TL software:\n\n"
            "- **Baud Rate:** 19200 (factory default)\n"
            "- **Parity:** None\n"
            "- **Stop bits:** 1\n"
            "- **Address:** 1\u201332 (each inverter on the bus needs a unique address)\n\n"
            "\n### 4. Automatic Detection\n\n"
            "Once wired, Ionemo automatically scans a handful of common serial "
            "ports (including the ttyACM device class some adapter chipsets use) "
            "and, on whichever one your adapter is actually on, the RS-485 bus "
            "(addresses 1\u201310) for your inverter. No manual configuration is "
            "needed in most cases \u2014 just confirm the discovered device. The "
            "**Serial Port** field below is only for the rare case discovery "
            "doesn't find it (e.g. more than one adapter attached).\n"
        )

    @classmethod
    def config_schema(cls) -> list[ConfigField]:
        return [
            {
                "key": "address",
                "label": "RS-485 Address",
                "type": "number",
                "required": True,
                "placeholder": "2",
                "hint": "The inverter's RS-485 address (1–254). "
                "Check your inverter display panel.",
            },
            {
                "key": "baudrate",
                "label": "Baud Rate",
                "type": "select",
                "required": False,
                "options": ["9600", "19200", "38400", "57600", "115200"],
                "default": "19200",
                "hint": "Factory default is 19200. Only change if you "
                "modified the inverter's communication settings.",
            },
            {
                "key": "port",
                "label": "Serial Port",
                "type": "text",
                "required": False,
                "default": cls._DEFAULT_PORT,
                "placeholder": "/dev/ttyUSB0",
                "hint": "Device path for the USB-to-RS485 adapter, as seen by "
                "your Ionemo base station (which runs Linux). /dev/ttyUSB0 is "
                "the usual case with a single adapter attached -- if that "
                "doesn't work, run `ls /dev/ttyUSB*` on the base station to "
                "see what's actually there (`/dev/ttyACM0` is common for some "
                "adapter chipsets), or check for more than one USB-serial "
                "adapter.",
            },
        ]

    # Baud rates to try during discovery (most common first)
    _PROBE_BAUDRATES = [19200, 9600, 38400, 57600, 115200]
    _PROBE_TIMEOUT = 0.3

    # Serial ports probed during discover(), in order. Covers the common
    # single-adapter case first (matching _DEFAULT_PORT), then a small range
    # for a second/third adapter on BOTH device classes an adapter chipset
    # might enumerate under (ttyUSB -- vendor-specific USB-serial chips like
    # FTDI/CH340/PL2303/CP210x -- and ttyACM -- the USB-IF's standard CDC-ACM
    # class). No reason to give one class more slots than the other: a
    # candidate that doesn't exist fails instantly (no per-baud-rate wait),
    # so scanning the full range costs virtually nothing when only one real
    # adapter is attached -- discover() also stops at the first candidate
    # that yields a result, since a customer's inverter bus is wired to
    # exactly one adapter.
    _PROBE_PORTS = [
        _DEFAULT_PORT,
        "/dev/ttyUSB1",
        "/dev/ttyUSB2",
        "/dev/ttyUSB3",
        "/dev/ttyACM0",
        "/dev/ttyACM1",
        "/dev/ttyACM2",
        "/dev/ttyACM3",
    ]

    @classmethod
    def _open_serial(cls, serial_mod: Any, port: str, baud: int) -> Any | DiscoveryResult | None:
        """Try to open the given serial port.

        Returns the opened Serial object on success; None if nothing exists at
        this specific path -- the expected outcome for most _PROBE_PORTS
        entries on a typical single-adapter setup, so callers should just try
        the next candidate rather than treat it as an error; or a
        DiscoveryResult describing a real problem (busy port, unexpected
        error) worth surfacing if no candidate ultimately succeeds.
        """
        try:
            return serial_mod.Serial(
                port=port,
                baudrate=baud,
                bytesize=serial_mod.EIGHTBITS,
                parity=serial_mod.PARITY_NONE,
                stopbits=serial_mod.STOPBITS_ONE,
                timeout=cls._PROBE_TIMEOUT,
            )
        except FileNotFoundError:
            return None
        except PermissionError:
            return DiscoveryResult(
                warnings=[f"{port} is busy — a device may already be using it."],
            )
        except Exception as e:
            logger.debug("Aurora discover: cannot open %s: %s", port, e)
            return DiscoveryResult(
                warnings=[f"Cannot open serial port {port}. Check the USB-to-RS485 adapter connection."]
            )

    @classmethod
    def discover(cls) -> DiscoveryResult:
        """Probe for Aurora inverters across a small set of common serial ports.

        For each candidate in _PROBE_PORTS that actually exists, tries all
        common baud rates and RS-485 addresses 1-10. Stops at the first
        candidate that yields any inverter.
        """
        try:
            import serial as serial_mod
        except ImportError:
            return DiscoveryResult(
                warnings=["pyserial is not installed — RS-485 not available."]
            )

        # The most useful warning to show if nothing is ultimately found --
        # "a port opened but nothing answered" always wins over "a port was
        # busy" or "no port existed at all", since it's the strongest signal
        # that real hardware is actually there. See the loop below for how
        # this gets set/overwritten.
        fallback_warning: str | None = None

        for port in cls._PROBE_PORTS:
            found: list[dict[str, Any]] = []
            port_opened = False

            for baud in cls._PROBE_BAUDRATES:
                result = cls._open_serial(serial_mod, port, baud)
                if result is None:
                    # Nothing at this path -- true for every baud rate on the
                    # same path, so there's no point retrying the rest.
                    break
                if isinstance(result, DiscoveryResult):
                    if fallback_warning is None:
                        fallback_warning = result.warnings[0]
                    break
                port_opened = True
                ser = result
                try:
                    for address in range(1, 11):
                        if any(d["address"] == address for d in found):
                            continue
                        if cls._probe_address(ser, address):
                            found.append(
                                {"address": address, "baudrate": baud, "port": port}
                            )
                            logger.info(
                                "Aurora discover: found inverter at %s, address %d, baud %d",
                                port,
                                address,
                                baud,
                            )
                finally:
                    ser.close()

            if found:
                return DiscoveryResult(devices=found)

            if port_opened:
                fallback_warning = (
                    f"No inverters responded on {port} (probed addresses 1\u201310)."
                    " Check the cable connection between the adapter and the inverter."
                )

        if fallback_warning:
            return DiscoveryResult(warnings=[fallback_warning])

        return DiscoveryResult(
            warnings=[
                "No USB-to-RS485 adapter detected. Make sure it is plugged into your Ionemo base."
            ]
        )

    @classmethod
    def _probe_address(cls, ser: Any, address: int) -> bool:
        """Send a DSP read to an address and check for a valid response."""
        # Aurora protocol uses 10-byte request frames:
        # 8 data bytes (address + command + 6 parameter bytes) + 2 CRC bytes.
        # CRC is computed over all 8 data bytes.
        payload = bytes([address, _CMD_DSP, _DSP_GRID_POWER_W, 0, 0, 0, 0, 0])
        crc_lo, crc_hi = _crc16(payload)
        frame = payload + bytes([crc_lo, crc_hi])

        ser.reset_input_buffer()
        ser.write(frame)
        resp = ser.read(8)

        if len(resp) != 8:
            return False
        # Verify CRC — byte 0 is alarm state, not an address echo,
        # so a valid CRC on the correct frame length is sufficient to confirm presence.
        expected_lo, expected_hi = _crc16(resp[:6])
        return resp[6] == expected_lo and resp[7] == expected_hi

    def get_status(self) -> str:
        if self._port_unavailable:
            return "error"
        if self._last_error:
            # Port opened fine but the inverter didn't respond — normal every
            # night when it powers down, so not distinguishable from real sleep.
            return "sleeping"
        if self._last_success is None:
            return "sleeping"
        return "connected"

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def _reset_serial_on_lock_timeout(self) -> None:
        """Close the serial port after a timed-out lock acquisition."""
        try:
            if self._serial is not None:
                self._serial.close()
        except Exception:
            pass
        self._serial = None
        self._last_error = "Read timed out — serial port reset"

    def get_data(self) -> PVInverterData | None:
        """Read inverter data via RS-485 and map to PV contract."""
        # Use a timed lock acquire so that if a previous call is stuck inside a
        # serial read (which can block for up to 1s × 8 commands = 8s), this call
        # does not chain-block indefinitely.  If we cannot acquire within 10s, the
        # previous thread is still live; close the port to let it fail fast and
        # return None — the scheduler will retry next cycle.
        if not self._lock.acquire(timeout=10.0):
            logger.warning(
                "Aurora lock not acquired within 10s — previous read still running; "
                "closing port to unblock"
            )
            self._reset_serial_on_lock_timeout()
            return None
        try:
            import serial

            if self._serial is None:
                try:
                    self._serial = serial.Serial(
                        port=self._port,
                        baudrate=self._baudrate,
                        bytesize=serial.EIGHTBITS,
                        parity=serial.PARITY_NONE,
                        stopbits=serial.STOPBITS_ONE,
                        timeout=1,
                    )
                except Exception as e:
                    self._last_error = str(e)
                    self._port_unavailable = True
                    logger.warning("Aurora port unavailable: %s", e)
                    return None
                self._port_unavailable = False

            power_w = self._read_dsp(_DSP_GRID_POWER_W)
            temp_c = self._read_dsp(_DSP_TEMP_INV_C)
            daily_wh = self._read_energy(_ENERGY_DAILY_WH)
            total_wh = self._read_energy(_ENERGY_TOTAL_WH)
            dc_voltage = self._read_dsp(_DSP_DC_VOLTAGE_V)
            dc_current = self._read_dsp(_DSP_DC_CURRENT_A)
            grid_voltage = self._read_dsp(_DSP_GRID_VOLTAGE_V)
            grid_freq = self._read_dsp(_DSP_FREQ_HZ)

            if power_w is None or daily_wh is None or total_wh is None:
                if self._last_error is None:
                    self._last_error = f"Inverter at address {self._address} did not respond to required commands"
                logger.info("Aurora read incomplete: %s", self._last_error)
                return None

            self._last_error = None
            self._last_success = time.monotonic()

            return PVInverterData(
                # Required
                solar_power_w=max(0.0, power_w),
                daily_energy_wh=daily_wh,
                total_energy_wh=total_wh,
                # Optional
                temperature_c=round(temp_c, 1) if temp_c is not None else None,
                dc_voltage_v=round(dc_voltage, 1) if dc_voltage is not None else None,
                dc_current_a=round(dc_current, 2) if dc_current is not None else None,
                grid_voltage_v=(
                    round(grid_voltage, 1) if grid_voltage is not None else None
                ),
                grid_frequency_hz=(
                    round(grid_freq, 2) if grid_freq is not None else None
                ),
            )
        except Exception as e:
            self._last_error = str(e)
            self._serial = None
            logger.warning("Aurora read failed: %s", e)
            return None
        finally:
            self._lock.release()

    def _send_command(self, cmd: int, subcmd: int = 0, param2: int = 0) -> bytes | None:
        """Send an Aurora request frame and read the 8-byte response.

        The Aurora protocol uses asymmetric frame sizes:
        - Request:  10 bytes — 8 data bytes + 2 CRC bytes (CRC over all 8 data bytes)
        - Response:  8 bytes — 6 data bytes + 2 CRC bytes (CRC over first 6 bytes)

        Records a specific failure reason in ``self._last_error`` for every
        non-success path so the UI can surface why the inverter is not reading.
        """
        assert self._serial is not None
        # Build 8 data bytes then append CRC of those 8 bytes → 10-byte frame
        payload = bytes([self._address, cmd, subcmd, param2, 0, 0, 0, 0])
        crc_lo, crc_hi = _crc16(payload)
        frame = payload + bytes([crc_lo, crc_hi])

        self._serial.reset_input_buffer()
        self._serial.write(frame)
        resp = self._serial.read(8)
        if len(resp) != 8:
            self._last_error = (
                f"No reply from inverter at address {self._address} "
                f"(got {len(resp)}/8 bytes within {self._serial.timeout}s)"
            )
            return None

        # Verify CRC
        expected_lo, expected_hi = _crc16(resp[:6])
        if resp[6] != expected_lo or resp[7] != expected_hi:
            self._last_error = (
                f"CRC mismatch in inverter reply (raw={resp.hex()}) — "
                "likely RS-485 noise, wrong baud, or A/B swapped"
            )
            return None
        return resp

    def _read_dsp(self, index: int) -> float | None:
        """Read a DSP measurement (returns IEEE-754 float)."""
        resp = self._send_command(_CMD_DSP, index)
        if resp is None:
            return None
        return struct.unpack(">f", resp[2:6])[0]

    def _read_energy(self, period: int) -> float | None:
        """Read a cumulated energy counter (Wh, returned as unsigned 32-bit integer)."""
        resp = self._send_command(_CMD_ENERGY, period)
        if resp is None:
            return None
        return float(struct.unpack(">I", resp[2:6])[0])


register_driver("aurora_rs485", AuroraRS485Driver)
