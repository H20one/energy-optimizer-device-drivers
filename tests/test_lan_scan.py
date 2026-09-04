"""Tests for energy_optimizer_drivers/lan_scan.py.

Regression coverage for a real bug found via a live deployment: the default
scan_timeout (15s) didn't leave enough time for the full /24 sweep to reach
addresses numerically late in the range, given max_workers=15 and each
driver's own ~2.5s per-probe timeout for a non-responding address (confirmed
live: such an address doesn't fail fast -- no ARP reply means the connection
attempt hangs for the full per-request timeout). Real, reachable devices on
higher addresses were silently never probed at all, not merely slow to find.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from energy_optimizer_drivers.lan_scan import scan_subnet


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

        with patch("energy_optimizer_drivers.lan_scan.socket.socket") as mock_socket_cls:
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

        with patch("energy_optimizer_drivers.lan_scan.socket.socket") as mock_socket_cls:
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

        with patch("energy_optimizer_drivers.lan_scan.socket.socket") as mock_socket_cls:
            mock_sock = MagicMock()
            mock_sock.getsockname.return_value = ("192.0.2.99", 0)
            mock_socket_cls.return_value = mock_sock

            scan_subnet(probe, lambda _subnet: "not found")

        assert "192.0.2.99" not in probed_ips
        assert len(probed_ips) == 253  # 254 minus the host's own address
