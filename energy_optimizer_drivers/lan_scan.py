"""Shared local-subnet discovery scan.

Extracted from grid/homewizard_p1.py and ac/daikin_brp.py, which independently
implemented the identical "derive the local /24 subnet, probe every address
concurrently with a bounded thread pool, accept partial results on timeout"
scan -- down to matching comments (daikin_brp.py explicitly referenced
homewizard_p1.py's as "the matching comment"). Drivers using RS-485/serial
(aurora_rs485.py) have nothing in common with this and are unaffected.
"""

from __future__ import annotations

import logging
import socket
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import Any

from energy_optimizer_drivers.base import DiscoveryResult

logger = logging.getLogger(__name__)


def scan_subnet(
    probe: Callable[[str], dict[str, Any] | None],
    not_found_message: Callable[[str], str],
    *,
    label: str = "Discovery",
    thread_name_prefix: str = "discovery",
    max_workers: int = 15,
    scan_timeout: float = 15.0,
) -> DiscoveryResult:
    """Scan every address on the host's local /24 subnet, calling *probe(ip)*
    concurrently for each, and return a DiscoveryResult.

    *probe* returns ``{"ip": ip, ...}`` for a found device or ``None`` --
    same contract each driver's own single-IP probe function already follows
    (e.g. ``_probe_homewizard``, ``_probe_daikin``). It should apply its own
    short per-request timeout; this function only bounds the *overall* scan.

    *not_found_message* builds the "nothing found" warning from the derived
    subnet prefix (e.g. ``"192.168.1"``) -- some drivers mention the subnet
    in the message, some don't; the caller decides, this just supplies it.

    *label* prefixes log lines (e.g. ``"HomeWizard discovery"``) so drivers
    stay distinguishable in logs despite sharing this implementation.
    """
    found: list[dict[str, Any]] = []

    # Determine the host's local IP to derive the subnet. Connecting a UDP
    # socket to a public IP (Google DNS) doesn't send any traffic -- it only
    # triggers the OS to resolve the default route, so we can read back which
    # local interface IP would be used.
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))  # NOSONAR — no data sent, route lookup only
        local_ip = s.getsockname()[0]
        s.close()
    except OSError:
        logger.warning("%s: could not determine local IP", label)
        return DiscoveryResult(
            warnings=[
                "Could not determine the local network address. "
                "Make sure your Ionemo base is connected to your home network."
            ]
        )

    subnet_prefix = ".".join(local_ip.split(".")[:3])

    ips = [
        f"{subnet_prefix}.{i}"
        for i in range(1, 255)
        if f"{subnet_prefix}.{i}" != local_ip
    ]

    # Use an explicit pool (not a context manager) so we can call
    # shutdown(wait=False, cancel_futures=True) on timeout. The context
    # manager always calls shutdown(wait=True), which would block for up to
    # 12 s (ceil(253/50) x 2.5 s) after the as_completed timeout fires.
    # Lower concurrency than a raw port-scanner would use -- this still sweeps
    # the full /24 (an unavoidable, consent-gated footprint on whatever
    # network this runs on -- the main app requires explicit user consent
    # before triggering any discover() call), but 15 concurrent connections
    # looks meaningfully less like an attack tool to network monitoring than
    # 50, at a barely-noticeable cost on a local, low-latency LAN.
    pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=thread_name_prefix)
    try:
        futures = {pool.submit(probe, ip): ip for ip in ips}
        try:
            for future in as_completed(futures, timeout=scan_timeout):
                result = future.result()
                if result:
                    found.append(result)
                    # DEBUG, not INFO: result["ip"] must not be logged at INFO
                    # or above (SECURITY.md §6.1). Discovery results are
                    # already surfaced to the user in the UI.
                    logger.debug("%s: found device at %s", label, result["ip"])
        except FuturesTimeout:
            # Scan did not finish within scan_timeout -- accept partial results.
            logger.warning("%s: network scan timed out after %.0f s", label, scan_timeout)
    finally:
        # Cancel queued futures that haven't started yet. In-progress probe()
        # calls run to their own timeout and then stop.
        pool.shutdown(wait=False, cancel_futures=True)

    if not found:
        return DiscoveryResult(warnings=[not_found_message(subnet_prefix)])

    return DiscoveryResult(devices=found)
