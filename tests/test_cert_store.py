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

import ssl
from unittest.mock import MagicMock, patch

from energy_optimizer_drivers.cert_store import (
    _PinnedCertAdapter,
    _pin_cert,
    _pinned_path,
    configure_session_tls,
    resolve_verify,
)

# Not a real certificate -- PEM_cert_to_DER_cert only requires valid base64
# between the BEGIN/END markers, it doesn't parse or validate the DER content.
_FAKE_PEM = "-----BEGIN CERTIFICATE-----\nZmFrZSBjZXJ0IGJ5dGVz\n-----END CERTIFICATE-----\n"


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


class TestPinCert:
    def test_fetches_and_saves_the_certificate_on_success(self, tmp_path) -> None:
        dest = tmp_path / "device_192_0_2_10.pem"
        with patch(
            "energy_optimizer_drivers.cert_store.ssl.get_server_certificate",
            return_value=_FAKE_PEM,
        ) as mock_fetch:
            result = _pin_cert("192.0.2.10", dest, timeout=5)

        mock_fetch.assert_called_once_with(("192.0.2.10", 443), timeout=5)
        assert result == str(dest)
        assert dest.read_text() == _FAKE_PEM

    def test_creates_missing_parent_directories(self, tmp_path) -> None:
        dest = tmp_path / "nested" / "dir" / "device_192_0_2_10.pem"
        with patch(
            "energy_optimizer_drivers.cert_store.ssl.get_server_certificate",
            return_value=_FAKE_PEM,
        ):
            _pin_cert("192.0.2.10", dest, timeout=5)

        assert dest.exists()

    def test_returns_false_and_does_not_raise_when_unreachable(self, tmp_path, caplog) -> None:
        dest = tmp_path / "device_192_0_2_10.pem"
        with patch(
            "energy_optimizer_drivers.cert_store.ssl.get_server_certificate",
            side_effect=OSError("connection refused"),
        ):
            result = _pin_cert("192.0.2.10", dest, timeout=5)

        assert result is False
        assert not dest.exists()

    def test_does_not_log_the_ip_on_failure(self, caplog, tmp_path) -> None:
        dest = tmp_path / "device_192_0_2_10.pem"
        with (
            patch(
                "energy_optimizer_drivers.cert_store.ssl.get_server_certificate",
                side_effect=OSError("connection refused"),
            ),
            caplog.at_level("WARNING"),
        ):
            _pin_cert("192.0.2.10", dest, timeout=5)

        assert "192.0.2.10" not in caplog.text


class TestConfigureSessionTls:
    def test_mounts_a_pinned_adapter_for_the_device_ip(self, tmp_path) -> None:
        cert_path = tmp_path / "device_192_0_2_10.pem"
        cert_path.write_text(_FAKE_PEM)
        session = MagicMock()

        configure_session_tls(session, "192.0.2.10", str(cert_path))

        session.mount.assert_called_once()
        prefix_arg, adapter_arg = session.mount.call_args.args
        assert prefix_arg == "https://192.0.2.10"
        assert isinstance(adapter_arg, _PinnedCertAdapter)

    def test_fingerprint_matches_the_certificate_the_pem_encodes(self, tmp_path) -> None:
        import hashlib

        cert_path = tmp_path / "device_192_0_2_10.pem"
        cert_path.write_text(_FAKE_PEM)
        session = MagicMock()

        configure_session_tls(session, "192.0.2.10", str(cert_path))

        der = ssl.PEM_cert_to_DER_cert(_FAKE_PEM)
        expected_fingerprint = hashlib.sha256(der).hexdigest()
        adapter = session.mount.call_args.args[1]
        assert adapter._fingerprint == expected_fingerprint


class TestPinnedCertAdapter:
    def test_init_poolmanager_injects_the_fingerprint(self) -> None:
        adapter = _PinnedCertAdapter("deadbeef")
        with patch(
            "energy_optimizer_drivers.cert_store.HTTPAdapter.init_poolmanager"
        ) as mock_super:
            adapter.init_poolmanager(10, 10)

        assert mock_super.call_args.kwargs["assert_fingerprint"] == "deadbeef"

    def test_send_forces_verify_false(self) -> None:
        adapter = _PinnedCertAdapter("deadbeef")
        request = MagicMock()
        with patch(
            "energy_optimizer_drivers.cert_store.HTTPAdapter.send"
        ) as mock_super:
            mock_super.return_value = "the-response"
            result = adapter.send(request)

        assert mock_super.call_args.kwargs["verify"] is False
        assert result == "the-response"

    def test_send_overrides_a_caller_supplied_verify_value(self) -> None:
        adapter = _PinnedCertAdapter("deadbeef")
        request = MagicMock()
        with patch(
            "energy_optimizer_drivers.cert_store.HTTPAdapter.send"
        ) as mock_super:
            adapter.send(request, verify=True)

        assert mock_super.call_args.kwargs["verify"] is False
