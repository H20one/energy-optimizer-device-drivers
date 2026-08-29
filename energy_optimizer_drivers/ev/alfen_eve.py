"""Alfen EVE Single Pro-line — EV Charger Driver.

Maps the Alfen EVE HTTPS REST API to the EVChargerDriver contract.
Keeps a persistent authenticated session and re-logs in only on failure.
"""

import logging
import threading
import time
from pathlib import Path
from typing import Any

import requests
from requests.exceptions import RequestException

from energy_optimizer_drivers.base import (
    ConfigField,
    ConnectionType,
    DeviceType,
    DiscoveryResult,
    EVChargerData,
    EVChargerDriver,
)
from energy_optimizer_drivers.cert_store import configure_session_tls, resolve_verify
from energy_optimizer_drivers.registry import register_driver

logger = logging.getLogger(__name__)

_METER_CATEGORY = "meter1"
_OCPP_STATE_ID = "3600_1"
_MAX_CURRENT_ID = "2068_0"
_DYNAMIC_CURRENT_ID = "2129_0"
_CHARGING_CURRENT_THRESHOLD_A = 0.5

# Property IDs returned by /api/prop?cat=meter1
_PROP_V1 = "2221_3"  # Voltage L1 (V)
_PROP_V2 = "2221_4"  # Voltage L2 (V)
_PROP_V3 = "2221_5"  # Voltage L3 (V)
_PROP_I1 = "2221_A"  # Current L1 (A)
_PROP_I2 = "2221_B"  # Current L2 (A)
_PROP_I3 = "2221_C"  # Current L3 (A)
_PROP_PF = "2221_11"  # Power Factor
_PROP_KWH = "2221_16"  # Lifetime energy meter (kWh)


class AlfenEveDriver(EVChargerDriver):
    """EV charger driver for Alfen EVE stations."""

    driver_id = "alfen_eve"
    name = "Alfen EVE Single Pro-line"
    manufacturer = "Alfen"
    builder = "H20one"
    device_type = DeviceType.EV_CHARGER
    connection_type = ConnectionType.WIFI

    def __init__(self, config: dict[str, Any]) -> None:
        self._ip: str = config["ip"]
        self._user_level: str = config.get("user_level", "admin")
        self._password: str = config["password"]
        self._timeout: int = config.get("timeout", 10)
        self._base_url: str = f"https://{self._ip}"
        self._last_error: str | None = None
        self._last_success: float | None = None

        # SSL verification is delegated to the cert store (TOFU pinning).
        # resolve_verify() returns a cert path if one is already pinned or
        # explicitly provided, or False if the charger is unreachable for TOFU.
        # If False, _login() will retry TOFU on every poll until it succeeds.
        explicit_cert = config.get("ca_cert_path", "")
        self._cert_store_dir: Path = Path(config.get("cert_store_dir", "data/certs"))
        self._verify: str | bool = resolve_verify(
            self._ip, explicit_cert, self._cert_store_dir, self._timeout
        )

        # Persistent session — authenticated once and reused across polls.
        # _session is None until the first successful login.
        # _session_lock serialises concurrent access from the poller job
        # (get_data, every 5 s) and the optimizer job (set_current, every 15 s)
        # which run in separate threads of the APScheduler thread pool.
        self._session: requests.Session | None = None
        self._session_lock = threading.Lock()

    @classmethod
    def setup_guide(cls) -> str | None:
        return (
            "## Alfen EVE Charger Setup\n\n"
            "This driver connects to the Alfen EVE\u2019s built-in HTTPS REST API "
            "over your local network. The charger uses a self-signed TLS "
            "certificate by default.\n\n"
            "### What you need\n\n"
            "- An Alfen EVE charger connected to your local network (Ethernet or WiFi)\n"
            "- The charger\u2019s IP address\n"
            "- The admin/installer password (set during commissioning)\n\n"
            "\n### 1. Network connection\n\n"
            "The Alfen EVE must be on the same LAN as the Energy Optimizer. "
            "Most installations use a wired Ethernet connection between the "
            "charger and your router.\n\n"
            "\n### 2. Find the IP address\n\n"
            "You can find the charger\u2019s IP in several ways:\n\n"
            "- **Charger display:** Navigate to Settings \u2192 Network on the "
            "charger\u2019s touchscreen (if equipped).\n"
            "- **Router admin panel:** Look for a device named \u201cAlfen\u201d or "
            "\u201cACE\u201d in your router\u2019s DHCP client list.\n"
            "- **Alfen ACE Service Installer app:** Connect via Bluetooth "
            "and check the network configuration.\n\n"
            "\n### 3. Credentials\n\n"
            "The charger has three access levels:\n\n"
            "| Level | Permissions |\n"
            "|-------|-------------|\n"
            "| admin | Full control (recommended) |\n"
            "| installer | Configuration access |\n"
            "| sca | Limited read-only |\n\n"
            "Use the **admin** level for full functionality including "
            "current control. The password was set during commissioning \u2014 "
            "check with your installer if unknown.\n\n"
            "\n### 4. TLS Certificate (auto-pinned)\n\n"
            "The charger uses a unique self-signed certificate. On first connection the "
            "driver automatically retrieves and saves it to `data/certs/` "
            "(**trust on first use**). All later connections verify the server presents "
            "the exact same certificate by SHA-256 fingerprint, so a man-in-the-middle "
            "attack would have to occur on the very first connection to succeed.\n\n"
            "No configuration is needed — this happens automatically.\n\n"
            "\n### 5. No automatic discovery\n\n"
            "Unlike some other devices, the Alfen charger does not support "
            "network broadcast discovery. You\u2019ll need to enter the IP "
            "address and credentials manually.\n"
        )

    @classmethod
    def config_schema(cls) -> list[ConfigField]:
        return [
            {"key": "ip", "label": "IP Address", "type": "text", "required": True},
            {
                "key": "user_level",
                "label": "User Level",
                "type": "select",
                "required": True,
                "options": ["admin", "installer", "sca"],
                "default": "admin",
            },
            {
                "key": "password",
                "label": "Password",
                "type": "password",
                "required": True,
            },
            {
                "key": "timeout",
                "label": "Timeout (seconds)",
                "type": "number",
                "required": False,
                "default": 10,
            },
        ]

    @classmethod
    def discover(cls) -> DiscoveryResult:
        """Discovery not yet implemented for Alfen chargers."""
        return DiscoveryResult()

    def get_status(self) -> str:
        if self._last_error:
            return "error"
        if self._last_success is None:
            return "disabled"
        return "connected"

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def get_data(self) -> EVChargerData | None:
        """Fetch charger data and map to EV charger contract."""
        with self._session_lock:
            try:
                session = self._ensure_session()
                if session is None:
                    return None

                props = self._read_all_properties(session)

                self._last_error = None
                self._last_success = time.monotonic()

                # Per-phase currents and voltages
                i1 = float(props.get(_PROP_I1) or 0)
                i2 = float(props.get(_PROP_I2) or 0)
                i3 = float(props.get(_PROP_I3) or 0)
                v1 = float(props.get(_PROP_V1) or 0)
                v2 = float(props.get(_PROP_V2) or 0)
                v3 = float(props.get(_PROP_V3) or 0)
                pf = float(props.get(_PROP_PF) or 1.0)

                # Power = Σ(Vn × In) × PF — Alfen has no direct power register
                power_w = (v1 * i1 + v2 * i2 + v3 * i3) * pf

                max_current_a = float(props.get(_MAX_CURRENT_ID) or 32)

                # Sanity check: discard impossible readings
                max_possible_w = max_current_a * 3 * 230 * abs(pf)
                if power_w > max_possible_w * 1.1:
                    logger.warning(
                        "Discarding impossible charger power: %.0f W (max %.0f W)",
                        power_w,
                        max_possible_w,
                    )
                    power_w = 0.0

                energy_kwh = float(props.get(_PROP_KWH) or 0)

                # OCPP state is an integer (0 = Available); default to 0 if absent/null
                ocpp_state = int(props.get(_OCPP_STATE_ID) or 0)
                state = self._map_ocpp_state(ocpp_state, i1 + i2 + i3)

                return EVChargerData(
                    state=state,
                    power_w=round(power_w, 1),
                    current_l1_a=round(i1, 2),
                    current_l2_a=round(i2, 2),
                    current_l3_a=round(i3, 2),
                    energy_total_kwh=round(energy_kwh, 3),
                    max_current_a=round(max_current_a, 1),
                    session_energy_kwh=None,
                    voltage_l1_v=self._round_voltage(v1),
                    voltage_l2_v=self._round_voltage(v2),
                    voltage_l3_v=self._round_voltage(v3),
                )
            except Exception as e:
                self._last_error = str(e)
                logger.warning("Alfen read failed: %s", e)
                # Discard the session so the next call forces a fresh login.
                self._session = None
                return None

    def set_current(self, amps: float) -> bool:
        """Set the dynamic charging current limit."""
        with self._session_lock:
            try:
                session = self._ensure_session()
                if session is None:
                    return False

                url = f"{self._base_url}/api/prop"
                payload = {
                    _DYNAMIC_CURRENT_ID: {"id": _DYNAMIC_CURRENT_ID, "value": amps}
                }
                resp = session.post(url, json=payload, timeout=self._timeout)
                if resp.status_code == 401:
                    # Session expired — drop it and retry once.
                    self._session = None
                    session = self._ensure_session()
                    if session is None:
                        return False
                    resp = session.post(url, json=payload, timeout=self._timeout)
                return resp.status_code == 200
            except RequestException as e:
                self._last_error = str(e)
                logger.warning("Alfen set_current failed: %s", e)
                self._session = None
                return False

    def _ensure_session(self) -> requests.Session | None:
        """Return the active session, creating and authenticating a new one if needed."""
        if self._session is not None:
            return self._session
        return self._login()

    def _login(self) -> requests.Session | None:
        """Create a new session and authenticate with the Alfen charger.

        If no certificate is configured, attempts TOFU via the cert store:
        pins the charger's certificate on first connection and uses it for
        all subsequent calls.
        """
        if not self._verify:
            self._verify = resolve_verify(
                self._ip, "", self._cert_store_dir, self._timeout
            )
        if not self._verify:
            self._last_error = (
                "TLS certificate not yet pinned — charger unreachable for TOFU"
            )
            logger.warning("Alfen: %s", self._last_error)
            return None

        try:
            session = requests.Session()
            configure_session_tls(session, self._ip, str(self._verify))
            url = f"{self._base_url}/api/login"
            payload = {"username": self._user_level, "password": self._password}
            resp = session.post(url, json=payload, timeout=self._timeout)
            if resp.status_code == 200:
                self._session = session
                return session
            self._last_error = f"Login failed: HTTP {resp.status_code}"
            return None
        except RequestException as e:
            self._last_error = f"Login error: {e}"
            return None

    def _read_all_properties(self, session: requests.Session) -> dict[str, Any]:
        """Fetch meter + state + config properties and merge into one {id: value} dict.

        Three separate requests are required: the API only returns the first
        matching property when IDs from different object instances are batched.
        On HTTP 401 the persistent session is discarded so the next poll re-logs in.
        """
        combined: list[dict[str, Any]] = []
        for params in [
            {"cat": _METER_CATEGORY},
            {"ids": _OCPP_STATE_ID},
            {"ids": _MAX_CURRENT_ID},
        ]:
            try:
                resp = session.get(
                    f"{self._base_url}/api/prop",
                    params=params,
                    timeout=self._timeout,
                )
                if resp.status_code == 401:
                    self._session = None
                    raise RequestException("Session expired (401)")
                if resp.status_code == 200:
                    combined.extend(resp.json().get("properties", []))
            except (RequestException, ValueError) as e:
                logger.debug("Alfen property fetch failed (%s): %s", params, e)
                raise
        return {entry["id"]: entry.get("value") for entry in combined}

    @staticmethod
    def _round_voltage(v: float) -> float | None:
        """Return voltage rounded to 1 decimal, or None if zero/missing."""
        return round(v, 1) if v else None

    @staticmethod
    def _map_ocpp_state(ocpp: int, current_a: float) -> str:
        """Map integer OCPP status to one of the four contract states.

        Alfen OCPP state for socket 1 (3600_1) is an integer:
          0 = Available (no cable), non-zero = cable present / charging.
        Physical current is used as the ground truth for charging status.
        """
        if current_a > _CHARGING_CURRENT_THRESHOLD_A:
            return "charging"
        if ocpp != 0:
            return "connected"
        return "available"


register_driver("alfen_eve", AlfenEveDriver)
