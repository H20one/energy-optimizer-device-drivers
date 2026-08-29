"""Tests for energy_optimizer_drivers/cert_store.py.

Focused on _pinned_path()/resolve_verify()'s `prefix` parameter — this module
is shared TOFU-pinning infrastructure for any HTTPS driver, and the pinned
filename should identify which driver a cert belongs to rather than assuming
it's always the first driver that ever used this module (historically
hardcoded to "alfen_"). Full TLS handshake behavior (_PinnedCertAdapter,
configure_session_tls) is exercised indirectly via test_alfen_driver.py,
which mocks this module entirely.
"""

from __future__ import annotations

from unittest.mock import patch

from energy_optimizer_drivers.cert_store import _pinned_path, resolve_verify


class TestPinnedPath:
    def test_includes_the_given_prefix(self, tmp_path) -> None:
        path = _pinned_path("192.0.2.10", tmp_path, prefix="alfen_eve")
        assert path.name == "alfen_eve_192_0_2_10.pem"

    def test_defaults_to_a_generic_prefix_when_not_given(self, tmp_path) -> None:
        path = _pinned_path("192.0.2.10", tmp_path)
        assert path.name == "device_192_0_2_10.pem"

    def test_different_prefixes_at_the_same_ip_do_not_collide(self, tmp_path) -> None:
        a = _pinned_path("192.0.2.10", tmp_path, prefix="alfen_eve")
        b = _pinned_path("192.0.2.10", tmp_path, prefix="some_other_driver")
        assert a != b

    def test_sanitizes_unsafe_characters_in_the_prefix(self, tmp_path) -> None:
        path = _pinned_path("192.0.2.10", tmp_path, prefix="weird/driver.id")
        assert path.name == "weird_driver_id_192_0_2_10.pem"


class TestResolveVerifyUsesThePrefixedPath:
    def test_uses_an_existing_pinned_cert_matching_the_prefix(self, tmp_path) -> None:
        pinned = tmp_path / "alfen_eve_192_0_2_10.pem"
        pinned.write_text("fake pem contents")

        result = resolve_verify("192.0.2.10", "", tmp_path, timeout=2, prefix="alfen_eve")

        assert result == str(pinned)

    def test_explicit_cert_short_circuits_regardless_of_prefix(self, tmp_path) -> None:
        result = resolve_verify(
            "192.0.2.10", "/explicit/cert.pem", tmp_path, timeout=2, prefix="alfen_eve"
        )
        assert result == "/explicit/cert.pem"

    def test_falls_through_to_pinning_with_the_correct_prefixed_destination(
        self, tmp_path
    ) -> None:
        with patch("energy_optimizer_drivers.cert_store._pin_cert") as mock_pin:
            mock_pin.return_value = str(tmp_path / "some_driver_192_0_2_10.pem")
            resolve_verify("192.0.2.10", "", tmp_path, timeout=2, prefix="some_driver")

        dest_arg = mock_pin.call_args.args[1]
        assert dest_arg.name == "some_driver_192_0_2_10.pem"
