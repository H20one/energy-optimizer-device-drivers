"""HomeWizard P1 Dongle — Grid Meter Driver.

Maps the HomeWizard Energy P1 dongle HTTP API to the GridMeterDriver contract.
API Documentation: https://homewizard-energy-api.readthedocs.io/
"""

import logging
import time
from typing import Any

import requests
from requests.exceptions import RequestException

from energy_optimizer_drivers.base import (
    ConfigField,
    ConnectionType,
    DeviceType,
    DiscoveryResult,
    GridMeterData,
    GridMeterDriver,
)
from energy_optimizer_drivers.registry import register_driver

logger = logging.getLogger(__name__)


def _probe_homewizard(ip: str) -> dict[str, Any] | None:
    """Probe a single IP address for a HomeWizard energy socket/P1 device.

    Returns ``{"ip": ip}`` (plus ``"identity"``: the device's serial, when
    present in the response) when a valid device is found, else ``None``.
    Safe to call concurrently from a thread pool. Module-level (not nested in
    discover()) so it's directly unit-testable.
    """
    try:
        r = requests.get(f"http://{ip}/api", timeout=2.5)  # NOSONAR
        if r.status_code == 200:
            data = r.json()
            if data.get("product_type") in ("HWE-P1", "HWE-SKT", "HWE-WTR"):
                serial = data.get("serial")
                return {"ip": ip, "identity": serial} if serial else {"ip": ip}
    except Exception:
        pass
    return None


class HomewizardP1Driver(GridMeterDriver):
    """Grid meter driver for HomeWizard P1 dongle."""

    driver_id = "homewizard_p1"
    name = "HomeWizard P1 Dongle"
    manufacturer = "HomeWizard"
    builder = "H20one"
    device_type = DeviceType.GRID_METER
    connection_type = ConnectionType.WIFI

    def __init__(self, config: dict[str, Any]) -> None:
        self._ip: str = config["ip"]
        self._timeout: int = config.get("timeout", 10)
        self._base_url: str = f"http://{self._ip}"  # NOSONAR
        self._last_error: str | None = None
        self._last_success: float | None = None

    @classmethod
    def setup_guide(cls) -> str | None:
        return (
            "## HomeWizard P1 Dongle Setup\n\n"
            "The P1 dongle plugs into the **P1 port** on your smart meter "
            "(the small RJ12 socket). It reads energy data over the "
            "DSMR/eMUB telegram protocol and exposes it via WiFi.\n\n"
            "### What you need\n\n"
            "- A HomeWizard P1 dongle (HWE-P1)\n"
            "- Your smart meter\u2019s P1 port (RJ12)\n"
            "- The dongle connected to the same WiFi network as your "
            "Ionemo base\n\n"
            "\n### 1. Install the dongle\n\n"
            "1. Plug the P1 dongle into the P1 port on your smart meter.\n"
            "2. The LED will blink while it connects to WiFi.\n"
            "3. If it\u2019s a new dongle, use the **HomeWizard Energy** app "
            "to pair it with your WiFi network first.\n\n"
            "\n### 2. Enable the Local API\n\n"
            "The local API must be enabled for Ionemo to "
            "communicate with the dongle:\n\n"
            "1. Open the **HomeWizard Energy** app on your phone.\n"
            "2. Tap your P1 meter \u2192 Settings (\u2699).\n"
            "3. Enable **Local API**.\n\n"
            "\n### 3. Find the IP address\n\n"
            "In the same settings screen, scroll down to see the "
            "dongle\u2019s IP address (e.g. `192.168.1.42`). You can also "
            "check your router\u2019s DHCP client list.\n\n"
            "\n### 4. Automatic detection\n\n"
            "Ionemo scans your local network for HomeWizard "
            "devices automatically. If your dongle is online and the API is "
            "enabled, it will appear in the discovered devices list.\n"
        )

    @classmethod
    def config_schema(cls) -> list[ConfigField]:
        return [
            {
                "key": "ip",
                "label": "IP Address",
                "type": "text",
                "required": True,
                "placeholder": "192.168.1.x",
                "hint": (
                    "Open the HomeWizard Energy app \u2192 tap your P1 meter"
                    " \u2192 Settings (\u2699) \u2192 scroll to IP address."
                    " This device must be on the same network as the server."
                ),
            },
        ]

    @classmethod
    def discover(cls) -> DiscoveryResult:
        """Scan the local network for HomeWizard P1 devices.

        Uses the HomeWizard local API: devices respond to GET /api with
        product info including product_type and serial.
        Scans common LAN ranges (last octet 1-254) on the host's subnet.
        """
        import socket
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from concurrent.futures import TimeoutError as FuturesTimeout

        found: list[dict[str, Any]] = []

        # Determine the host's local IP to derive the subnet.
        # Connecting a UDP socket to a public IP (Google DNS) doesn't send
        # any traffic — it only triggers the OS to resolve the default route
        # so we can read back which local interface IP would be used.
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))  # NOSONAR — no data sent, route lookup only
            local_ip = s.getsockname()[0]
            s.close()
        except OSError:
            logger.warning("Discovery: could not determine local IP")
            return DiscoveryResult(
                warnings=[
                    "Could not determine the local network address. "
                    "Make sure your Ionemo base is connected to your "
                    "home network."
                ]
            )

        subnet_prefix = ".".join(local_ip.split(".")[:3])

        ips = [
            f"{subnet_prefix}.{i}"
            for i in range(1, 255)
            if f"{subnet_prefix}.{i}" != local_ip
        ]

        # Use an explicit pool (not a context manager) so we can call
        # shutdown(wait=False, cancel_futures=True) on timeout.  The context
        # manager always calls shutdown(wait=True), which would block for up to
        # 12 s (ceil(253/50) × 2.5 s) after the as_completed timeout fires.
        # Lower concurrency than a raw port-scanner would use — this still sweeps
        # the full /24 (an unavoidable, consent-gated footprint on whatever network
        # this runs on — the main app requires explicit user consent before
        # triggering any discover() call), but 15 concurrent connections looks
        # meaningfully less like an attack tool to network monitoring than 50,
        # at a barely-noticeable cost on a local, low-latency LAN.
        pool = ThreadPoolExecutor(max_workers=15, thread_name_prefix="discovery")
        try:
            futures = {pool.submit(_probe_homewizard, ip): ip for ip in ips}
            try:
                for future in as_completed(futures, timeout=15):
                    result = future.result()
                    if result:
                        found.append(result)
                        # DEBUG, not INFO: result["ip"] must not be logged at
                        # INFO or above (SECURITY.md §6.1). Discovery
                        # results are already surfaced to the user in the UI.
                        logger.debug(
                            "Discovery: found HomeWizard device at %s", result["ip"]
                        )
            except FuturesTimeout:
                # Scan did not finish within 15 s — accept partial results.
                logger.warning("Discovery: network scan timed out after 15 s")
        finally:
            # Cancel queued futures that haven't started yet.  In-progress
            # _probe() calls run to their own 2.5 s timeout and then stop.
            pool.shutdown(wait=False, cancel_futures=True)

        if not found:
            return DiscoveryResult(
                warnings=[
                    "No HomeWizard devices found on the network "
                    f"({subnet_prefix}.x). Make sure the P1 dongle is "
                    "powered on and connected to the same WiFi network."
                ]
            )

        return DiscoveryResult(devices=found)

    def get_status(self) -> str:
        if self._last_error:
            return "error"
        if self._last_success is None:
            return "disabled"
        return "connected"

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def get_data(self) -> GridMeterData | None:
        """Fetch data from P1 dongle and map to grid meter contract."""
        try:
            response = requests.get(
                f"{self._base_url}/api/v1/data", timeout=self._timeout
            )
            response.raise_for_status()
            data = response.json()
            self._last_error = None
            self._last_success = time.monotonic()
            return self._map_to_contract(data)
        except RequestException as e:
            self._last_error = str(e)
            logger.warning("P1 read failed: %s", e)
            return None

    def _map_to_contract(self, raw: dict[str, Any]) -> GridMeterData:
        """Map HomeWizard API response to GridMeterData contract."""
        import_t1 = raw.get("total_power_import_t1_kwh", 0.0)
        import_t2 = raw.get("total_power_import_t2_kwh", 0.0)
        export_t1 = raw.get("total_power_export_t1_kwh", 0.0)
        export_t2 = raw.get("total_power_export_t2_kwh", 0.0)

        return GridMeterData(
            # Required
            grid_power_w=raw.get("active_power_w", 0.0),
            import_total_kwh=import_t1 + import_t2,
            export_total_kwh=export_t1 + export_t2,
            # Optional
            import_t1_kwh=import_t1,
            import_t2_kwh=import_t2,
            export_t1_kwh=export_t1,
            export_t2_kwh=export_t2,
            gas_total_m3=raw.get("total_gas_m3"),
            voltage_l1_v=raw.get("active_voltage_l1_v"),
            voltage_l2_v=raw.get("active_voltage_l2_v"),
            voltage_l3_v=raw.get("active_voltage_l3_v"),
            current_l1_a=raw.get("active_current_l1_a"),
            current_l2_a=raw.get("active_current_l2_a"),
            current_l3_a=raw.get("active_current_l3_a"),
            frequency_hz=raw.get("active_frequency_hz"),
        )


register_driver("homewizard_p1", HomewizardP1Driver)
