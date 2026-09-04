"""Daikin BRP WiFi Adapter — AC Unit Driver.

Uses the Daikin local HTTP API exposed by BRP-series WiFi adapters
(BRP072A21, BRP072Bxx, BRP072Cx, BRP069Cx) to read and control
Daikin split-system air conditioners over the local network.

No authentication is required — the adapter accepts requests from
any device on the same local network segment.

API endpoints (all HTTP GET):
    /common/basic_info        — probe / device identity
    /aircon/get_sensor_info   — room temperature, humidity, outdoor temp
    /aircon/get_control_info  — current operating state (pow/mode/stemp/f_rate/…)
    /aircon/set_control_info  — apply control changes (all params required)

Response format: plain text, comma-separated key=value pairs:
    ret=OK,pow=1,mode=3,stemp=22.0,shum=0,f_rate=A,f_dir=0,…

Limitations:
    - Instantaneous power (power_w) is not available from the local API.
      power_w is always reported as 0.0 W.
    - Temperature set-point is limited to 10–32 °C in 0.5 °C steps.
    - BRP084 (Onecta cloud-only adapter) and units with no WiFi adapter
      are not compatible.
"""

import logging
import time
from typing import Any
from urllib.parse import urlencode

import requests
from requests.exceptions import RequestException

from energy_optimizer_drivers.base import (
    ACUnitData,
    ACUnitDriver,
    ConfigField,
    ConnectionType,
    DeviceType,
    DiscoveryResult,
)
from energy_optimizer_drivers.registry import register_driver

logger = logging.getLogger(__name__)

# ─── Mode translation tables ──────────────────────────────────────────────────

# Daikin mode integer → contract mode string  (pow=0 is handled separately)
_DAIKIN_MODE_TO_STR: dict[str, str] = {
    "0": "auto",
    "1": "auto",
    "2": "dry",
    "3": "cool",
    "4": "heat",
    "6": "fan",
}

# Contract mode string → (pow, mode) for set_control_info
_MODE_TO_DAIKIN: dict[str, tuple[str, str]] = {
    "off": ("0", "0"),
    "auto": ("1", "1"),
    "cool": ("1", "3"),
    "heat": ("1", "4"),
    "fan": ("1", "6"),
    "dry": ("1", "2"),
}

# ─── Fan speed translation tables ─────────────────────────────────────────────

# Daikin f_rate → contract fan_speed string
_DAIKIN_FRATE_TO_STR: dict[str, str] = {
    "A": "auto",
    "B": "silent",  # quiet / silent
    "3": "1",
    "4": "2",
    "5": "3",
    "6": "4",
    "7": "5",
}

# Contract fan_speed string → Daikin f_rate
_FAN_TO_DAIKIN: dict[str, str] = {
    "auto": "A",
    "silent": "B",
    "1": "3",
    "2": "4",
    "3": "5",
    "4": "6",
    "5": "7",
}

# Modes where Daikin uses stemp="--" / shum="--" (no setpoint applicable)
_MODES_WITHOUT_STEMP: frozenset[str] = frozenset({"fan"})

# Sentinel values used by the adapter when no setpoint applies (fan mode)
_STEMP_NO_SETPOINT = "--"
_SHUM_NO_SETPOINT = "--"

# Fallback set-point when switching back from a mode that used stemp="M"
_FALLBACK_STEMP = "22.0"

# Min/max set-point supported by the Daikin BRP API (°C)
_STEMP_MIN = 10.0
_STEMP_MAX = 32.0

# Daikin BRP local API endpoint paths
_PATH_BASIC_INFO = "/common/basic_info"
_PATH_SENSOR_INFO = "/aircon/get_sensor_info"
_PATH_CONTROL_INFO = "/aircon/get_control_info"
_PATH_SET_CONTROL = "/aircon/set_control_info"


def _probe_daikin(ip: str) -> dict[str, Any] | None:
    """Probe a single IP address for a Daikin BRP adapter.

    Returns ``{"ip": ip}`` (plus ``"identity"`` when the adapter's ``mac``
    field is present — confirmed present on real BRP073A-class adapters) when
    a valid adapter is found, else ``None``. Safe to call concurrently from a
    thread pool.
    """
    try:
        r = requests.get(f"http://{ip}{_PATH_BASIC_INFO}", timeout=2.5)  # NOSONAR
        if r.status_code == 200:
            info = _parse_daikin_response(r.text)
            if info.get("ret") == "OK" and info.get("type") == "aircon":
                mac = info.get("mac")
                return {"ip": ip, "identity": mac} if mac else {"ip": ip}
    except Exception:
        pass
    return None


def _parse_daikin_response(text: str) -> dict[str, str]:
    """Parse a Daikin response string into a dict.

    Daikin adapters respond with comma-separated key=value pairs:
        ret=OK,pow=1,mode=3,stemp=22.0,shum=0,f_rate=A,f_dir=0

    Args:
        text: Raw response body from the adapter.

    Returns:
        Dict of all parsed key/value pairs.
    """
    result: dict[str, str] = {}
    for part in text.strip().split(","):
        if "=" in part:
            key, _, value = part.partition("=")
            result[key.strip()] = value.strip()
    return result


# Only these six fields are accepted by set_control_info.
# get_control_info returns many read-only extras (b_mode, b_stemp, b_shum,
# adv, dt1–dt7, dh1–dh7, en_hol, …) that cause ret=PARAM NG when echoed back.
_CONTROL_WRITABLE_FIELDS: frozenset[str] = frozenset(
    {"pow", "mode", "stemp", "shum", "f_rate", "f_dir"}
)


def _control_params_from_response(response: dict[str, str]) -> dict[str, str]:
    """Build the set_control_info payload from a get_control_info response.

    Returns only the six writable fields accepted by set_control_info.
    Extra read-only fields returned by get_control_info (schedule temps,
    backup mode, holiday mode, etc.) are dropped to avoid ret=PARAM NG.

    Args:
        response: Parsed get_control_info response dict.

    Returns:
        Dict ready to pass to set_control_info (via urlencode).
    """
    return {k: v for k, v in response.items() if k in _CONTROL_WRITABLE_FIELDS}


class DaikinBrpDriver(ACUnitDriver):
    """AC unit driver for Daikin BRP-series WiFi adapters.

    Communicates with the Daikin local HTTP API (no cloud, no authentication).
    Compatible with BRP072A21, BRP072B, BRP072C, and BRP069Cx adapters.
    """

    driver_id = "daikin_brp"
    name = "Daikin BRP WiFi Adapter"
    manufacturer = "Daikin"
    builder = "H20one"
    device_type = DeviceType.AC_UNIT
    connection_type = ConnectionType.WIFI

    # Seconds before the cached get_control_info is considered stale.
    _CONTROL_CACHE_TTL = 60.0

    def __init__(self, config: dict[str, Any]) -> None:
        self._ip: str = config["ip"]
        self._timeout: int = config.get("timeout", 3)
        self._base_url: str = f"http://{self._ip}"  # NOSONAR
        self._last_error: str | None = None
        self._last_success: float | None = None
        # Cache of the last get_control_info response to avoid a redundant GET
        # before every SET command.  Invalidated after each successful write.
        self._control_cache: dict[str, str] | None = None
        self._control_cache_ts: float = 0.0

    @classmethod
    def setup_guide(cls) -> str | None:
        """Return a Markdown setup guide for the Daikin BRP adapter."""
        return (
            "## Daikin BRP WiFi Adapter Setup\n\n"
            "This driver connects to your Daikin air conditioner through its "
            "**BRP-series WiFi adapter** using the local HTTP API. "
            "No cloud account or internet connection is required.\n\n"
            "### Compatible adapters\n\n"
            "| Adapter | Notes |\n"
            "|---------|-------|\n"
            "| BRP072A21 | Older units |\n"
            "| BRP072Bxx | Common |\n"
            "| BRP072Cxx | Most common |\n"
            "| BRP069C4x | US / specific models |\n\n"
            "**Not compatible:** BRP084 (Onecta cloud-only) or units with no "
            "WiFi adapter installed.\n\n"
            "### 1. Install the WiFi adapter\n\n"
            "If not already installed, fit the BRP adapter into the WiFi "
            "interface slot on the indoor unit (see your unit\u2019s manual). "
            "Pair it with your WiFi network using the **Daikin Online "
            "Controller** or **ONECTA** app.\n\n"
            "### 2. Find the IP address\n\n"
            "- Check your router\u2019s DHCP client list for a device named "
            "\u201cDaikin\u201d or \u201cBRP072\u201d.\n"
            "- Or use the automatic discovery in the wizard below.\n\n"
            "### 3. Assign a static IP (recommended)\n\n"
            "Reserve the adapter\u2019s MAC address in your router\u2019s DHCP "
            "settings so the IP does not change after a router reboot.\n\n"
            "### 4. Known limitations\n\n"
            "- **Power monitoring:** The BRP adapter does not expose real-time "
            "power consumption. Power is always shown as 0 W.\n"
            "- **Temperature range:** Set-point is limited to 10\u201332\u00b0C "
            "in 0.5\u00b0C steps.\n"
            "- **Fan mode:** No temperature set-point applies in fan-only mode.\n"
        )

    @classmethod
    def config_schema(cls) -> list[ConfigField]:
        """Return the configuration fields for the Add Device wizard."""
        return [
            {
                "key": "room",
                "label": "Room / Location",
                "type": "text",
                "required": False,
                "placeholder": "e.g. Living room, Bedroom",
                "hint": (
                    "Optional label shown on the device card to identify this unit. "
                    "Not fetched from the device \u2014 enter it yourself."
                ),
            },
            {
                "key": "ip",
                "label": "IP Address",
                "type": "text",
                "required": True,
                "placeholder": "192.168.1.50",  # NOSONAR
                "hint": (
                    "The local IP address of the BRP WiFi adapter. "
                    "Check your router\u2019s DHCP client list."
                ),
            },
            {
                "key": "timeout",
                "label": "Timeout (seconds)",
                "type": "number",
                "required": False,
                "default": 3,
            },
        ]

    @classmethod
    def discover(cls) -> DiscoveryResult:
        """Scan the local network for Daikin BRP adapters.

        Probes each address on the local /24 subnet using
        GET /common/basic_info. Daikin adapters respond with
        ret=OK,type=aircon,… when reachable.
        """
        return cls._run_discovery(quick=False)

    @classmethod
    def discover_quick(cls) -> DiscoveryResult:
        """Fast variant of discover() -- see BaseDriver.discover_quick()."""
        return cls._run_discovery(quick=True)

    @classmethod
    def _run_discovery(cls, quick: bool) -> DiscoveryResult:
        from energy_optimizer_drivers.lan_scan import scan_subnet

        return scan_subnet(
            _probe_daikin,
            lambda _subnet_prefix: (
                "No Daikin BRP adapters found on the local network. "
                "Make sure the adapter is powered on and connected to "
                "the same network as your Ionemo base."
            ),
            label="Daikin discovery",
            thread_name_prefix="daikin_disc",
            quick=quick,
        )

    # ── Status ────────────────────────────────────────────────────────────────

    def get_status(self) -> str:
        """Return the current connection status (non-blocking)."""
        if self._last_error:
            return "error"
        if self._last_success is None:
            return "disabled"
        return "connected"

    @property
    def last_error(self) -> str | None:
        """Most recent error message, or None if healthy."""
        return self._last_error

    # ── Internal HTTP helpers ─────────────────────────────────────────────────

    def _get(self, path: str) -> dict[str, str] | None:
        """GET a Daikin endpoint and return the parsed response dict.

        Returns None and sets last_error on any communication or protocol failure.

        Args:
            path: URL path, e.g. '/aircon/get_sensor_info'.
        """
        try:
            r = requests.get(f"{self._base_url}{path}", timeout=self._timeout)
            r.raise_for_status()
            parsed = _parse_daikin_response(r.text)
            if parsed.get("ret") != "OK":
                self._last_error = f"Adapter returned ret={parsed.get('ret')!r}"
                # IP intentionally omitted — must not be logged at INFO or
                # above (SECURITY.md §6.1).
                logger.warning("Daikin %s: unexpected ret=%r", path, parsed.get("ret"))
                return None
            # Keep the control-info cache warm.
            if path == _PATH_CONTROL_INFO:
                self._control_cache = parsed
                self._control_cache_ts = time.monotonic()
            return parsed
        except RequestException as exc:
            self._last_error = str(exc)
            logger.warning("Daikin %s failed: %s", path, exc)
            return None

    def _get_control_cached(self) -> dict[str, str] | None:
        """Return a fresh get_control_info dict, using the in-memory cache when possible.

        The cache is valid for ``_CONTROL_CACHE_TTL`` seconds.  After a
        successful :meth:`_send_control` the cache is updated with the
        params that were written, so consecutive commands (e.g. set mode
        then set temp) avoid a redundant network round-trip.

        Returns:
            Parsed control dict, or None on failure.
        """
        age = time.monotonic() - self._control_cache_ts
        if self._control_cache is not None and age < self._CONTROL_CACHE_TTL:
            return dict(self._control_cache)
        return self._get(_PATH_CONTROL_INFO)

    def _send_control(self, params: dict[str, str]) -> bool:
        """Send a set_control_info request with the given parameters.

        Args:
            params: Full control parameter dict (all fields required by the adapter).

        Returns:
            True if the adapter confirmed the change with ret=OK.
        """
        try:
            url = f"{self._base_url}{_PATH_SET_CONTROL}?{urlencode(params)}"
            # DEBUG, not INFO: fires on every AC command, and the IP must not
            # be logged at INFO or above (SECURITY.md §6.1).
            logger.debug("Daikin set_control_info params=%s", params)
            r = requests.get(url, timeout=self._timeout)
            r.raise_for_status()
            parsed = _parse_daikin_response(r.text)
            if parsed.get("ret") == "OK":
                # Update the cache so the next setter call doesn't need a GET.
                self._control_cache = dict(params)
                self._control_cache_ts = time.monotonic()
                return True
            # Adapter rejected the command — invalidate the cache so the next
            # command re-fetches fresh state instead of retrying bad params.
            self._control_cache = None
            self._last_error = f"set_control_info ret={parsed.get('ret')!r}"
            logger.warning("Daikin set_control_info ret=%r", parsed.get("ret"))
            return False
        except RequestException as exc:
            self._last_error = str(exc)
            # Invalidate the cache so the next command re-fetches fresh state.
            self._control_cache = None
            logger.warning("Daikin set_control_info failed: %s", exc)
            return False

    # ── Data ──────────────────────────────────────────────────────────────────

    def get_data(self) -> ACUnitData | None:
        """Fetch current AC unit state from the adapter.

        Calls get_sensor_info (temperatures, humidity) and get_control_info
        (operating mode, fan speed, set-point) in sequence. Returns None if
        either call fails.
        """
        try:
            sensor = self._get(_PATH_SENSOR_INFO)
            control = self._get(_PATH_CONTROL_INFO)
            if sensor is None or control is None:
                return None

            # ── Mode ──────────────────────────────────────────────────────────
            pow_val = control.get("pow", "0")
            if pow_val == "0":
                mode = "off"
            else:
                daikin_mode = control.get("mode", "1")
                mode = _DAIKIN_MODE_TO_STR.get(daikin_mode, "auto")

            # ── Room temperature ─────────────────────────────────────────────
            htemp_str = sensor.get("htemp", "")
            try:
                temperature_c = float(htemp_str)
            except ValueError:
                self._last_error = f"Invalid htemp value: {htemp_str!r}"
                logger.warning("Daikin: could not parse htemp=%r", htemp_str)
                return None

            # ── Target temperature ───────────────────────────────────────────
            # Fan mode uses stemp="M" (not applicable). Fall back to a sane default.
            stemp_str = control.get("stemp", _FALLBACK_STEMP)
            try:
                target_temp_c = float(stemp_str)
            except ValueError:
                target_temp_c = float(_FALLBACK_STEMP)

            # ── Fan speed ─────────────────────────────────────────────────────
            f_rate = control.get("f_rate", "A")
            fan_speed: str | None = _DAIKIN_FRATE_TO_STR.get(f_rate)

            # ── Humidity ──────────────────────────────────────────────────────
            # The "hhum" field is "-" when the sensor is not fitted.
            hhum_str = sensor.get("hhum", "-")
            humidity_pct: float | None
            try:
                humidity_pct = float(hhum_str)
            except ValueError:
                humidity_pct = None

            self._last_error = None
            self._last_success = time.monotonic()

            return ACUnitData(
                mode=mode,
                power_w=0.0,  # BRP adapters do not expose real-time power
                temperature_c=round(temperature_c, 1),
                target_temp_c=round(target_temp_c, 1),
                fan_speed=fan_speed,
                humidity_pct=round(humidity_pct, 1) if humidity_pct is not None else None,
            )
        except Exception as e:
            # Second line of defense beyond _get()'s own RequestException catch —
            # covers anything else (e.g. a response-decode issue) that isn't a
            # RequestException, matching every other builtin driver's
            # "drivers never raise" contract (SECURITY.md §5.1).
            self._last_error = str(e)
            logger.warning("Daikin get_data failed: %s", e)
            return None

    # ── Control ───────────────────────────────────────────────────────────────

    def set_mode(self, mode: str) -> bool:
        """Set the operating mode (off / cool / heat / fan / dry / auto).

        Uses the cached get_control_info response when available so no
        extra network round-trip is needed before the SET command.

        Args:
            mode: One of the contract mode strings.

        Returns:
            True if the adapter accepted the change.
        """
        if mode not in _MODE_TO_DAIKIN:
            logger.warning("Daikin: unknown mode %r", mode)
            return False

        try:
            current = self._get_control_cached()
            if current is None:
                return False

            pow_val, mode_val = _MODE_TO_DAIKIN[mode]
            params = _control_params_from_response(current)
            params["pow"] = pow_val
            params["mode"] = mode_val

            if mode in _MODES_WITHOUT_STEMP:
                # Fan mode: Daikin uses stemp="--" / shum="--" (no set-point applicable)
                params["stemp"] = _STEMP_NO_SETPOINT
                params["shum"] = _SHUM_NO_SETPOINT
            elif params.get("stemp") in ("--", "M"):
                # Switching back from fan (or another no-setpoint mode) to a
                # temperature-based mode — restore a sane numeric set-point.
                params["stemp"] = _FALLBACK_STEMP
                params["shum"] = "0"

            return self._send_control(params)
        except Exception as e:
            self._last_error = str(e)
            logger.warning("Daikin set_mode failed: %s", e)
            return False

    def set_temperature(self, temp_c: float) -> bool:
        """Set the target temperature set-point.

        Clamps to 10–32 °C and rounds to the nearest 0.5 °C step.
        Turns the unit on if it is currently off.

        Args:
            temp_c: Desired set-point in °C.

        Returns:
            True if the adapter accepted the change.
        """
        try:
            current = self._get_control_cached()
            if current is None:
                return False

            clamped = max(_STEMP_MIN, min(_STEMP_MAX, temp_c))
            rounded = round(clamped * 2) / 2  # round to nearest 0.5
            params = _control_params_from_response(current)
            params["stemp"] = f"{rounded:.1f}"
            params["shum"] = "0"
            # Turn on if currently off
            if params.get("pow") == "0":
                params["pow"] = "1"
            # Fan mode uses no numeric stemp — switch to auto when setting a temperature
            if params.get("mode") == "6":
                params["mode"] = "1"  # auto

            return self._send_control(params)
        except Exception as e:
            self._last_error = str(e)
            logger.warning("Daikin set_temperature failed: %s", e)
            return False

    def set_fan_speed(self, speed: str) -> bool:
        """Set the fan speed (auto / low / medium / high).

        Args:
            speed: One of the contract fan speed strings.

        Returns:
            True if the adapter accepted the change.
        """
        daikin_rate = _FAN_TO_DAIKIN.get(speed)
        if daikin_rate is None:
            logger.warning("Daikin: unknown fan speed %r", speed)
            return False

        try:
            current = self._get_control_cached()
            if current is None:
                return False

            params = _control_params_from_response(current)
            params["f_rate"] = daikin_rate
            return self._send_control(params)
        except Exception as e:
            self._last_error = str(e)
            logger.warning("Daikin set_fan_speed failed: %s", e)
            return False


register_driver(DaikinBrpDriver.driver_id, DaikinBrpDriver)
