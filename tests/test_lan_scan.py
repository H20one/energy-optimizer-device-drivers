"""Tests for ionemo_drivers/lan_scan.py.

Regression coverage for a real bug found via a live deployment: the default
scan_timeout (15s) didn't leave enough time for the full /24 sweep to reach
addresses numerically late in the range, given max_workers=15 and each
driver's own ~2.5s per-probe timeout for a non-responding address (confirmed
live: such an address doesn't fail fast -- no ARP reply means the connection
attempt hangs for the full per-request timeout). Real, reachable devices on
higher addresses were silently never probed at all, not merely slow to find.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, mock_open, patch

from ionemo_drivers.lan_scan import (
    _discover_live_hosts,
    reset_live_host_cache,
    scan_subnet,
)

_FAKE_ARP_TABLE = (
    "IP address       HW type     Flags       HW address            Mask     Device\n"
    "192.0.2.10       0x1         0x2         aa:bb:cc:dd:ee:01     *        eth0\n"
    "192.0.2.11       0x1         0x0         00:00:00:00:00:00     *        eth0\n"
    "192.0.2.12       0x1         0x2         aa:bb:cc:dd:ee:03     *        eth0\n"
)


class TestScanTimeoutCoversTheWholeSubnet:
    def test_default_timeout_leaves_room_for_a_full_worst_case_sweep(self) -> None:
        """With the documented worst case (max_workers=15, ~2.5s per
        non-responding address), the full 253-address /24 needs up to
        ceil(253/15) * 2.5s =~ 42.2s. The default must cover that with some
        margin, or addresses late in the range are silently never reached --
        exactly the bug this test guards against."""
        import inspect

        default_timeout = inspect.signature(scan_subnet).parameters["scan_timeout"].default
        worst_case_seconds = -(-253 // 15) * 2.5  # ceil(253/15) * 2.5
        assert default_timeout >= worst_case_seconds

    def test_finds_a_device_at_an_address_late_in_the_range(self) -> None:
        """Companion to the arithmetic test above: confirms the full address
        range is actually submitted to the scan and a match near the end of
        it (.250, not just early addresses) is found. Every probe here
        returns instantly, so this doesn't itself exercise the timeout
        math -- it only guards against the range being truncated some other
        way (e.g. an off-by-one in the address-list construction)."""
        found_ip = "192.0.2.250"  # near the end of the scanned range

        def fake_probe(ip: str) -> dict[str, str] | None:
            if ip == found_ip:
                return {"ip": ip}
            return None  # instant "not found" -- keeps this test fast

        with patch("ionemo_drivers.lan_scan.socket.socket") as mock_socket_cls:
            mock_sock = MagicMock()
            mock_sock.getsockname.return_value = ("192.0.2.99", 0)
            mock_socket_cls.return_value = mock_sock

            result = scan_subnet(
                fake_probe,
                lambda _subnet: "not found",
                max_workers=15,
            )

        assert result.devices == [{"ip": found_ip}]


class TestScanSubnetBasics:
    def test_returns_a_warning_when_local_ip_cannot_be_determined(self) -> None:
        with patch("socket.socket", side_effect=OSError("network unreachable")):
            result = scan_subnet(lambda ip: None, lambda _subnet: "not found")

        assert result.devices == []
        assert "local network address" in result.warnings[0]

    def test_found_devices_are_returned_and_not_found_message_receives_the_subnet(
        self,
    ) -> None:
        captured_subnets: list[str] = []

        def not_found_message(subnet: str) -> str:
            captured_subnets.append(subnet)
            return f"nothing on {subnet}"

        with patch("ionemo_drivers.lan_scan.socket.socket") as mock_socket_cls:
            mock_sock = MagicMock()
            mock_sock.getsockname.return_value = ("192.0.2.99", 0)
            mock_socket_cls.return_value = mock_sock

            result = scan_subnet(lambda ip: None, not_found_message)

        assert result.devices == []
        assert captured_subnets == ["192.0.2"]
        assert result.warnings == ["nothing on 192.0.2"]

    def test_excludes_the_hosts_own_ip_from_the_scan(self) -> None:
        probed_ips: list[str] = []

        def probe(ip: str) -> None:
            probed_ips.append(ip)
            return None

        with patch("ionemo_drivers.lan_scan.socket.socket") as mock_socket_cls:
            mock_sock = MagicMock()
            mock_sock.getsockname.return_value = ("192.0.2.99", 0)
            mock_socket_cls.return_value = mock_sock

            scan_subnet(probe, lambda _subnet: "not found")

        assert "192.0.2.99" not in probed_ips
        assert len(probed_ips) == 253  # 254 minus the host's own address


class TestDiscoverLiveHosts:
    """_discover_live_hosts() -- the quick=True pre-filter's own logic,
    isolated from scan_subnet(). No real network or /proc access -- both the
    socket connect() and the /proc/net/arp read are mocked."""

    def test_returns_addresses_with_a_resolved_mac(self) -> None:
        with (
            patch("ionemo_drivers.lan_scan.socket.socket"),
            patch(
                "ionemo_drivers.lan_scan.open",
                mock_open(read_data=_FAKE_ARP_TABLE),
            ),
            patch("ionemo_drivers.lan_scan.time.sleep"),
        ):
            result = _discover_live_hosts(["192.0.2.10", "192.0.2.11", "192.0.2.12"])

        assert result == {"192.0.2.10", "192.0.2.12"}

    def test_ignores_a_resolved_address_not_in_the_requested_list(self) -> None:
        with (
            patch("ionemo_drivers.lan_scan.socket.socket"),
            patch(
                "ionemo_drivers.lan_scan.open",
                mock_open(read_data=_FAKE_ARP_TABLE),
            ),
            patch("ionemo_drivers.lan_scan.time.sleep"),
        ):
            # 192.0.2.10 has a real MAC in the fake table but isn't requested.
            result = _discover_live_hosts(["192.0.2.12"])

        assert result == {"192.0.2.12"}

    def test_skips_a_malformed_line_without_raising(self) -> None:
        malformed_table = (
            "IP address       HW type     Flags       HW address            Mask     Device\n"
            "not enough fields\n"
            "192.0.2.12       0x1         0x2         aa:bb:cc:dd:ee:03     *        eth0\n"
        )
        with (
            patch("ionemo_drivers.lan_scan.socket.socket"),
            patch(
                "ionemo_drivers.lan_scan.open",
                mock_open(read_data=malformed_table),
            ),
            patch("ionemo_drivers.lan_scan.time.sleep"),
        ):
            result = _discover_live_hosts(["192.0.2.12"])

        assert result == {"192.0.2.12"}

    def test_returns_none_when_proc_net_arp_is_unreadable(self) -> None:
        with (
            patch("ionemo_drivers.lan_scan.socket.socket"),
            patch(
                "ionemo_drivers.lan_scan.open",
                side_effect=OSError("no such file"),
            ),
            patch("ionemo_drivers.lan_scan.time.sleep"),
        ):
            result = _discover_live_hosts(["192.0.2.10"])

        assert result is None

    def test_nudges_every_candidate_address(self) -> None:
        connected_to: list[tuple[str, int]] = []

        def _fake_socket(*_args, **_kwargs):
            sock = MagicMock()
            sock.connect.side_effect = lambda addr: connected_to.append(addr)
            return sock

        with (
            patch(
                "ionemo_drivers.lan_scan.socket.socket",
                side_effect=_fake_socket,
            ),
            patch(
                "ionemo_drivers.lan_scan.open",
                mock_open(read_data=_FAKE_ARP_TABLE),
            ),
            patch("ionemo_drivers.lan_scan.time.sleep"),
        ):
            _discover_live_hosts(["192.0.2.10", "192.0.2.11", "192.0.2.12"])

        assert connected_to == [
            ("192.0.2.10", 80),
            ("192.0.2.11", 80),
            ("192.0.2.12", 80),
        ]

    def test_one_broken_nudge_does_not_abort_the_others(self) -> None:
        def _fake_socket(*_args, **_kwargs):
            sock = MagicMock()
            return sock

        call_count = 0

        def _connect_side_effect(addr):
            nonlocal call_count
            call_count += 1
            if addr[0] == "192.0.2.11":
                raise OSError("network unreachable")

        with (
            patch(
                "ionemo_drivers.lan_scan.socket.socket",
                side_effect=lambda *a, **k: MagicMock(
                    connect=MagicMock(side_effect=_connect_side_effect)
                ),
            ),
            patch(
                "ionemo_drivers.lan_scan.open",
                mock_open(read_data=_FAKE_ARP_TABLE),
            ),
            patch("ionemo_drivers.lan_scan.time.sleep"),
        ):
            result = _discover_live_hosts(["192.0.2.10", "192.0.2.11", "192.0.2.12"])

        assert call_count == 3
        assert result == {"192.0.2.10", "192.0.2.12"}

    def test_sockets_are_closed_after_reading(self) -> None:
        created_socks: list[MagicMock] = []

        def _fake_socket(*_args, **_kwargs):
            sock = MagicMock()
            created_socks.append(sock)
            return sock

        with (
            patch(
                "ionemo_drivers.lan_scan.socket.socket",
                side_effect=_fake_socket,
            ),
            patch(
                "ionemo_drivers.lan_scan.open",
                mock_open(read_data=_FAKE_ARP_TABLE),
            ),
            patch("ionemo_drivers.lan_scan.time.sleep"),
        ):
            _discover_live_hosts(["192.0.2.10"])

        assert created_socks
        for sock in created_socks:
            sock.close.assert_called_once()


class TestScanSubnetQuickMode:
    """scan_subnet(quick=True) -- the pre-filter's integration into the real scan."""

    def test_quick_scan_only_probes_pre_filtered_addresses(self) -> None:
        probed: list[str] = []

        def probe(ip: str):
            probed.append(ip)
            return None

        with (
            patch("ionemo_drivers.lan_scan.socket.socket") as mock_socket_cls,
            patch(
                "ionemo_drivers.lan_scan._discover_live_hosts",
                return_value={"192.0.2.10", "192.0.2.200"},
            ),
        ):
            mock_sock = MagicMock()
            mock_sock.getsockname.return_value = ("192.0.2.99", 0)
            mock_socket_cls.return_value = mock_sock

            scan_subnet(probe, lambda _subnet: "not found", quick=True)

        assert sorted(probed) == ["192.0.2.10", "192.0.2.200"]

    def test_quick_scan_falls_back_to_a_full_scan_when_prefilter_unavailable(
        self,
    ) -> None:
        probed: list[str] = []

        def probe(ip: str):
            probed.append(ip)
            return None

        with (
            patch("ionemo_drivers.lan_scan.socket.socket") as mock_socket_cls,
            patch(
                "ionemo_drivers.lan_scan._discover_live_hosts",
                return_value=None,
            ),
        ):
            mock_sock = MagicMock()
            mock_sock.getsockname.return_value = ("192.0.2.99", 0)
            mock_socket_cls.return_value = mock_sock

            scan_subnet(probe, lambda _subnet: "not found", quick=True)

        assert len(probed) == 253  # every address, same as quick=False

    def test_default_quick_is_false_and_never_calls_the_prefilter(self) -> None:
        with (
            patch("ionemo_drivers.lan_scan.socket.socket") as mock_socket_cls,
            patch(
                "ionemo_drivers.lan_scan._discover_live_hosts"
            ) as mock_prefilter,
        ):
            mock_sock = MagicMock()
            mock_sock.getsockname.return_value = ("192.0.2.99", 0)
            mock_socket_cls.return_value = mock_sock

            scan_subnet(lambda ip: None, lambda _subnet: "not found")

        mock_prefilter.assert_not_called()


class TestSharedLiveHostSweep:
    """N51: concurrent drivers share one ARP sweep instead of repeating it each.

    run_discovery() launches every registered driver at once and each sweeps the same
    /24. Which addresses are alive has nothing to do with which driver is asking, so
    before this the pre-filter ran once per driver over the same 254 addresses --
    a cost linear in driver count, in a package designed for third-party drivers.
    """

    def _run_concurrent_scans(self, count: int) -> int:
        """Run `count` quick scans at once; return how many ARP sweeps happened."""
        sweeps = 0
        lock = threading.Lock()

        def _counting_sweep(ips, wait_s=0.4):
            nonlocal sweeps
            with lock:
                sweeps += 1
            time.sleep(0.02)  # make overlap real, not incidental scheduling luck
            return {"192.0.2.10"}

        with (
            patch("ionemo_drivers.lan_scan._discover_live_hosts", _counting_sweep),
            patch("ionemo_drivers.lan_scan.socket.socket") as mock_sock,
        ):
            mock_sock.return_value.getsockname.return_value = ("192.0.2.1", 0)
            with ThreadPoolExecutor(max_workers=count) as pool:
                list(
                    pool.map(
                        lambda _: scan_subnet(
                            probe=lambda ip: None,
                            not_found_message=lambda prefix: "none",
                            quick=True,
                            max_workers=2,
                        ),
                        range(count),
                    )
                )
        return sweeps

    def test_eight_concurrent_scans_perform_one_sweep(self):
        reset_live_host_cache()
        assert self._run_concurrent_scans(8) == 1

    def test_cache_can_be_dropped_so_a_later_scan_re_checks_the_network(self):
        """A rescan after the TTL must genuinely look again — someone may have just
        plugged the device in, and answering from the previous sweep would miss it."""
        reset_live_host_cache()
        assert self._run_concurrent_scans(3) == 1
        reset_live_host_cache()
        assert self._run_concurrent_scans(3) == 1  # swept again, not served stale

    def test_unreadable_arp_is_cached_too_rather_than_retried_per_driver(self):
        reset_live_host_cache()
        calls = 0

        def _unavailable(ips, wait_s=0.4):
            nonlocal calls
            calls += 1
            return None

        with (
            patch("ionemo_drivers.lan_scan._discover_live_hosts", _unavailable),
            patch("ionemo_drivers.lan_scan.socket.socket") as mock_sock,
        ):
            mock_sock.return_value.getsockname.return_value = ("192.0.2.1", 0)
            for _ in range(4):
                scan_subnet(
                    probe=lambda ip: None,
                    not_found_message=lambda prefix: "none",
                    quick=True,
                    max_workers=2,
                )

        assert calls == 1, "a host without /proc/net/arp retried the sweep per driver"
