"""Abstract Base Classes defining the driver contracts.

This module is the ONLY shared dependency between the app and drivers.
All drivers import from here and implement these interfaces.

See docs/contracts/ for the full data contract specification per device type.
Required vs optional fields below are marked with `Required[...]`/plain-optional
so the distinction is checkable at runtime, not just documented in comments —
see `energy_optimizer_drivers.contract_validation.validate_contract_data()`.
"""

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any, Required, TypedDict

# ═══════════════════════════════════════════════════════════════════════════════
# DEVICE TYPE ENUM
# ═══════════════════════════════════════════════════════════════════════════════


class DeviceType(StrEnum):
    """Fixed set of device types that drivers can be created for.

    Adding a new type requires a corresponding ABC in this module
    and a contract doc in docs/contracts/.
    """

    GRID_METER = "grid_meter"
    PV_INVERTER = "pv_inverter"
    EV_CHARGER = "ev_charger"
    AC_UNIT = "ac_unit"
    # Future:
    # BATTERY = "battery"
    # SMART_SOCKET = "smart_socket"


class ConnectionType(StrEnum):
    """How the driver communicates with the physical device."""

    WIFI = "wifi"
    ETHERNET = "ethernet"
    SERIAL = "serial"
    MODBUS = "modbus"


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG SCHEMA FIELD TYPE
# ═══════════════════════════════════════════════════════════════════════════════


class _ConfigFieldRequired(TypedDict):
    """Required keys for ConfigField."""

    key: str
    label: str
    type: str
    required: bool


class ConfigField(_ConfigFieldRequired, total=False):
    """Schema for a single configuration field shown in the Add Device wizard.

    Required keys:
        key: Internal config key name (stored in DB).
        label: Human-readable label for the UI form.
        type: One of "text", "password", "number", "select".
        required: Whether the field must be filled in.

    Optional keys:
        options: List of allowed values (only for type="select").
        default: Pre-filled default value.
        placeholder: Hint text shown in the input field.
        hint: Helper text shown below the input (guidance for non-technical users).
    """

    options: list[str]
    default: str | int | float | None
    placeholder: str
    hint: str


# ═══════════════════════════════════════════════════════════════════════════════
# DISCOVERY RESULT TYPE
# ═══════════════════════════════════════════════════════════════════════════════


class DiscoveryResult:
    """Result of a driver's discover() call.

    Drivers MUST return a DiscoveryResult from discover(). The app uses this
    structured response to display found devices and communicate warnings to
    the user.

    Attributes:
        devices: List of config dicts for discovered devices. Each dict
                 contains the keys matching the driver's config_schema
                 (e.g. {"address": 2, "baudrate": 19200}). May also include
                 an optional "identity" key: a value stable across IP changes
                 (serial number, MAC address) that the app uses to re-match a
                 device to its existing config after a reconnect scan. Omit
                 it if the driver has no such stable identifier available.
        warnings: List of user-facing warning messages explaining issues
                  encountered during discovery. Examples:
                  - "No USB-to-RS485 adapter detected"
                  - "Serial port is busy — device may already be configured"
                  - "No devices responded on the network"
                  Warnings should be concise, non-technical, and actionable.
    """

    __slots__ = ("devices", "warnings")

    def __init__(
        self,
        devices: list[dict[str, Any]] | None = None,
        warnings: list[str] | None = None,
    ) -> None:
        self.devices: list[dict[str, Any]] = devices or []
        self.warnings: list[str] = warnings or []


# ═══════════════════════════════════════════════════════════════════════════════
# DATA CONTRACT RETURN TYPES
# ═══════════════════════════════════════════════════════════════════════════════


class GridMeterData(TypedDict, total=False):
    """Data contract for grid meters. See docs/contracts/grid_meter.md."""

    # REQUIRED — must always be a real number, never None
    grid_power_w: Required[float]  # Signed: + import, - export
    import_total_kwh: Required[float]  # Lifetime import counter (sum of all tariffs)
    export_total_kwh: Required[float]  # Lifetime export counter (sum of all tariffs)

    # OPTIONAL — None means device doesn't support it, 0.0 means supported but zero
    import_t1_kwh: float | None  # Peak/day tariff counter
    import_t2_kwh: float | None  # Off-peak/night tariff counter
    export_t1_kwh: float | None  # Peak export counter
    export_t2_kwh: float | None  # Off-peak export counter
    gas_total_m3: float | None  # Gas meter (if connected via P1)
    voltage_l1_v: float | None  # Phase 1 voltage
    voltage_l2_v: float | None  # Phase 2 voltage (None if single-phase)
    voltage_l3_v: float | None  # Phase 3 voltage (None if single-phase)
    current_l1_a: float | None  # Phase 1 current
    current_l2_a: float | None  # Phase 2 current
    current_l3_a: float | None  # Phase 3 current
    frequency_hz: float | None  # Grid frequency


class PVInverterData(TypedDict, total=False):
    """Data contract for PV inverters. See docs/contracts/pv_inverter.md."""

    # REQUIRED — must always be a real number, never None
    solar_power_w: Required[float]  # Current AC output (0.0 at night)
    daily_energy_wh: Required[float]  # Energy produced today (resets at midnight)
    total_energy_wh: Required[float]  # Lifetime energy counter

    # OPTIONAL — None means device doesn't support it
    temperature_c: float | None  # Inverter temperature
    dc_voltage_v: float | None  # DC input voltage (string 1)
    dc_current_a: float | None  # DC input current (string 1)
    grid_voltage_v: float | None  # AC grid voltage as seen by inverter
    grid_frequency_hz: float | None  # AC grid frequency


class EVChargerData(TypedDict, total=False):
    """Data contract for EV chargers. See docs/contracts/ev_charger.md."""

    # REQUIRED — must always be a real value, never None
    state: Required[str]  # "available" | "connected" | "charging" | "error"
    power_w: Required[float]  # Active power draw (0.0 when idle)
    current_l1_a: Required[float]  # Phase 1 current (0.0 if single-phase and idle)
    current_l2_a: Required[float]  # Phase 2 current (0.0 if single-phase)
    current_l3_a: Required[float]  # Phase 3 current (0.0 if single-phase)
    energy_total_kwh: Required[float]  # Lifetime energy counter
    max_current_a: Required[float]  # Configured max current for this station

    # OPTIONAL — None means device doesn't support it
    session_energy_kwh: float | None  # Energy in current charging session
    voltage_l1_v: float | None  # Phase 1 voltage
    voltage_l2_v: float | None  # Phase 2 voltage
    voltage_l3_v: float | None  # Phase 3 voltage


class ACUnitData(TypedDict, total=False):
    """Data contract for AC units. See docs/contracts/ac_unit.md."""

    # REQUIRED — must always be a real value, never None
    mode: Required[str]  # "off" | "cool" | "heat" | "fan" | "dry" | "auto"
    power_w: Required[float]  # Current power draw in W (0.0 when off)
    temperature_c: Required[float]  # Measured room temperature
    target_temp_c: Required[float]  # Set-point temperature

    # OPTIONAL — None means device doesn't support it
    fan_speed: str | None  # "auto" | "low" | "medium" | "high"
    humidity_pct: float | None  # Relative indoor humidity (%)


# ═══════════════════════════════════════════════════════════════════════════════
# BASE DRIVER ABC
# ═══════════════════════════════════════════════════════════════════════════════


# Default maximum seconds a get_data() call may block before the app
# considers it timed out.  Drivers SHOULD enforce their own timeout ≤ this
# value, but the application also wraps calls defensively.
DRIVER_CALL_TIMEOUT: int = 15


class BaseDriver(ABC):
    """Base interface all drivers must implement.

    Class-level identity constants (must be set by every driver):
        driver_id: str              — Unique key for registry/DB (e.g. "homewizard_p1")
        name: str                   — Product name shown in UI (e.g. "HomeWizard P1 Dongle")
        manufacturer: str           — Brand name for grouping (e.g. "HomeWizard")
        device_type: DeviceType     — From the DeviceType enum
        connection_type: ConnectionType — From the ConnectionType enum

    Contract:
        - get_data() MUST NOT block for longer than DRIVER_CALL_TIMEOUT seconds.
          Use appropriate timeouts in network/serial calls (e.g. requests timeout,
          serial read timeout). The application wraps calls defensively, but a
          well-behaved driver should enforce its own timeout internally.
        - get_data() returns None on communication failure (never raises).
        - get_status() must be non-blocking (return cached state).
    """

    driver_id: str
    name: str
    manufacturer: str
    builder: str = "Unknown"
    device_type: DeviceType
    connection_type: ConnectionType

    @classmethod
    @abstractmethod
    def config_schema(cls) -> list[ConfigField]:
        """Return fields needed for configuration.

        The app renders these as a form in the Add Device wizard.
        The submitted values are stored (encrypted) in the devices table
        and passed to __init__(config) when the driver is instantiated.
        """

    @classmethod
    def discover(cls) -> DiscoveryResult:
        """Scan for devices and return structured results with warnings.

        The app calls this during the Add Device wizard to auto-detect
        devices on the network or bus. Drivers that support discovery
        MUST override this method.

        Returns:
            DiscoveryResult with:
            - devices: list of config dicts (matching config_schema keys)
              that can be passed directly to __init__(config).
            - warnings: list of user-facing messages explaining any issues.

        Contract:
            - MUST take no arguments besides cls (enforced by
              test_contract_compliance.py) -- see discover_quick() below for
              the extension point that needs an alternate fast behavior.
            - MUST return a DiscoveryResult (never raise an exception).
            - MUST catch all internal exceptions and convert them to warnings.
            - warnings MUST be concise, non-technical, and actionable.
            - If the hardware is unavailable, return an empty result with a
              warning explaining why (e.g. "No USB adapter detected").
            - If the port/resource is busy, warn that the device may already
              be configured.
            - If discovery completes but finds nothing, a warning like
              "No devices found on the network" helps the user understand
              that the scan worked but nothing responded.
            - MUST NOT block longer than whatever ceiling the driver itself
              documents (e.g. scan_subnet()'s own scan_timeout, currently
              60s) -- there's no single fixed number across every driver.

        Default implementation returns an empty result (no discovery support).
        """
        return DiscoveryResult()

    @classmethod
    def discover_quick(cls) -> DiscoveryResult:
        """Fast variant of discover(), for a wizard's default scan attempt.

        Entirely optional and additive -- NOT part of discover()'s own
        contract, which stays zero-argument (see above). The base
        implementation here just calls discover() unchanged, so any driver
        that doesn't override this still works correctly, only without the
        speed benefit -- the app can always call discover_quick()
        unconditionally, with no need to check whether a given driver
        supports it first.

        LAN-based drivers using energy_optimizer_drivers.lan_scan.scan_subnet()
        should override this to forward quick=True to their own scan_subnet()
        call (see grid/homewizard_p1.py, ac/daikin_brp.py for the pattern) --
        a fast host-presence pre-filter runs before the slow per-address
        probe, so a genuinely unused address is skipped entirely instead of
        paying its full per-probe timeout. Drivers with nothing to
        pre-filter (e.g. RS-485/serial bus addressing) simply don't override
        this and get the same full scan either way.
        """
        return cls.discover()

    @classmethod
    def setup_guide(cls) -> str | None:
        """Optional: return a Markdown setup guide for this driver.

        When provided, the Add Device wizard shows a help button that
        opens the guide. Use it to explain:
        - What physical hardware/connections are required
        - How to find the configuration values the user must provide
        - Any prerequisites (e.g. enabling an API on the device)

        Default implementation returns None (no guide available).
        """
        return None

    @abstractmethod
    def get_status(self) -> str:
        """Return current connection status (non-blocking, returns cached state).

        Returns one of: 'connected' | 'error' | 'sleeping' | 'disabled'
        """

    @property
    @abstractmethod
    def last_error(self) -> str | None:
        """Most recent error message, or None if healthy."""


# ═══════════════════════════════════════════════════════════════════════════════
# DEVICE-TYPE ABCs
# ═══════════════════════════════════════════════════════════════════════════════


class GridMeterDriver(BaseDriver):
    """ABC for grid meter drivers.

    Required fields in get_data(): grid_power_w, import_total_kwh, export_total_kwh
    Optional fields: see GridMeterData TypedDict.

    If get_data() returns None, the device is unreachable.
    If it returns a dict, ALL required fields must be present and non-None.
    """

    @abstractmethod
    def get_data(self) -> GridMeterData | None:
        """Fetch current grid data. Returns None on communication failure."""


class PVInverterDriver(BaseDriver):
    """ABC for PV inverter drivers.

    Required fields in get_data(): solar_power_w, daily_energy_wh, total_energy_wh
    Optional fields: see PVInverterData TypedDict.

    If get_data() returns None, the device is unreachable / sleeping.
    If it returns a dict, ALL required fields must be present and non-None.
    """

    @abstractmethod
    def get_data(self) -> PVInverterData | None:
        """Fetch current solar data. Returns None on communication failure."""


class EVChargerDriver(BaseDriver):
    """ABC for EV charger drivers.

    Required fields in get_data(): state, power_w, current_l1/l2/l3_a,
                                    energy_total_kwh, max_current_a
    Optional fields: see EVChargerData TypedDict.

    If get_data() returns None, the device is unreachable.
    If it returns a dict, ALL required fields must be present and non-None.
    """

    @abstractmethod
    def get_data(self) -> EVChargerData | None:
        """Fetch current charger data. Returns None on communication failure."""

    @abstractmethod
    def set_current(self, amps: float) -> bool:
        """Set the charging current limit.

        Args:
            amps: Target current in amps. 0 = pause charging.

        Returns:
            True if the command was accepted by the device.
        """


class ACUnitDriver(BaseDriver):
    """ABC for AC unit drivers.

    Required fields in get_data(): mode, power_w, temperature_c, target_temp_c
    Optional fields: see ACUnitData TypedDict.

    If get_data() returns None, the device is unreachable.
    If it returns a dict, ALL required fields must be present and non-None.
    """

    @abstractmethod
    def get_data(self) -> ACUnitData | None:
        """Fetch current AC unit data. Returns None on communication failure."""

    @abstractmethod
    def set_mode(self, mode: str) -> bool:
        """Set the operating mode (off / cool / heat / fan / dry / auto).

        Returns:
            True if the command was accepted by the device.
        """

    @abstractmethod
    def set_temperature(self, temp_c: float) -> bool:
        """Set the target temperature set-point in °C.

        Returns:
            True if the command was accepted by the device.
        """

    @abstractmethod
    def set_fan_speed(self, speed: str) -> bool:
        """Set the fan speed (auto / low / medium / high).

        Returns:
            True if the command was accepted by the device.
        """
