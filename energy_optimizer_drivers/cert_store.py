"""TLS certificate store for driver infrastructure.

Provides trust-on-first-use (TOFU) certificate pinning for drivers that
communicate with local devices using self-signed TLS certificates.

Drivers delegate all filesystem operations here, keeping their own source
files free of write-mode I/O (required by the driver security contract).

## How it works

1. On first connection ``resolve_verify()`` fetches the charger's certificate
   (one unauthenticated TLS grab) and saves it to ``data/certs/``.
2. On all subsequent connections ``configure_session_tls()`` mounts a
   ``_PinnedCertAdapter`` on the ``requests.Session``.  The adapter verifies
   the server presents the **exact** certificate we pinned, by comparing
   SHA-256 fingerprints **without** hostname / IP-SAN checking.

This avoids the "IP address mismatch" error that occurs when self-signed
device certificates list a hostname (or nothing) instead of an IP address.
"""

import hashlib
import logging
import re
import ssl
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter

logger = logging.getLogger(__name__)


# ── Fingerprint-pinning adapter ───────────────────────────────────────────────


class _PinnedCertAdapter(HTTPAdapter):
    """HTTPAdapter that verifies the server cert by SHA-256 fingerprint.

    Unlike CA-style verification, this does NOT check the hostname or IP
    against the cert's Subject Alternative Names — self-signed device certs
    (e.g. Alfen EVE) almost never include the device's IP address as a SAN.

    Security properties:
    - Verifies the server presents the exact certificate we pinned at setup.
    - A MITM must intercept the very first (TOFU) connection to succeed.
    - All subsequent connections are fully verified by fingerprint.
    """

    def __init__(self, fingerprint: str) -> None:
        self._fingerprint = fingerprint  # SHA-256 hex digest of the pinned cert DER
        super().__init__()

    def init_poolmanager(
        self, connections: int, maxsize: int, block: bool = False, **kwargs: Any
    ) -> None:
        # assert_fingerprint is checked by urllib3 after the TLS handshake,
        # independently of CA chain or hostname verification.
        kwargs["assert_fingerprint"] = self._fingerprint
        super().init_poolmanager(connections, maxsize, block, **kwargs)

    def send(self, request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:  # type: ignore[override]
        # Disable requests' own CA / hostname verification — our fingerprint
        # check (via assert_fingerprint in the pool) replaces it entirely.
        kwargs["verify"] = False
        return super().send(request, **kwargs)


def configure_session_tls(session: requests.Session, ip: str, verify: str) -> None:
    """Mount the fingerprint-pinning TLS adapter on *session* for the device at *ip*.

    Call this immediately after creating a ``requests.Session`` and before
    the first request. The adapter is mounted for all ``https://<ip>`` URLs.

    ``verify`` must be the path to a pinned certificate (from ``resolve_verify``).
    If no certificate is available yet (TOFU failed), do not create the session
    at all — ``_login()`` checks for an empty ``verify`` and returns ``None``.
    """
    pem = Path(verify).read_text()
    der = ssl.PEM_cert_to_DER_cert(pem)
    fingerprint = hashlib.sha256(der).hexdigest()
    session.mount(f"https://{ip}", _PinnedCertAdapter(fingerprint))


# ── TOFU cert resolution ──────────────────────────────────────────────────────


def resolve_verify(
    ip: str,
    explicit_cert: str,
    store_dir: Path,
    timeout: int,
) -> str | bool:
    """Return a cert path (or ``False``) to use as the TLS verification source.

    Priority:
    1. ``explicit_cert`` if provided by the user in device config.
    2. A previously TOFU-pinned cert in ``store_dir``.
    3. Fetch and pin the cert now (TOFU), returning the new path.
       Returns ``False`` if pinning fails (network unreachable).

    The returned value should be passed to ``configure_session_tls()`` rather
    than used directly as ``verify=`` in ``requests`` calls — the latter
    triggers hostname checking which fails for IP-addressed self-signed certs.
    """
    if explicit_cert:
        return explicit_cert

    pinned_path = _pinned_path(ip, store_dir)
    if pinned_path.exists():
        # DEBUG, not INFO: pinned_path's filename embeds the IP (see
        # _pinned_path below), so logging the path is an indirect IP
        # disclosure — must not be logged at INFO or above
        # (SECURITY.md §6.1). Fires on every TLS connection setup.
        logger.debug("cert_store: using pinned certificate from %s", pinned_path)
        return str(pinned_path)

    return _pin_cert(ip, pinned_path, timeout)


def _pinned_path(ip: str, store_dir: Path) -> Path:
    safe_ip = re.sub(r"[^a-zA-Z0-9]", "_", ip)
    return store_dir / f"alfen_{safe_ip}.pem"


def _pin_cert(ip: str, dest: Path, timeout: int) -> str | bool:
    """Fetch and save the device's certificate via an unauthenticated TLS grab."""
    try:
        pem = ssl.get_server_certificate((ip, 443), timeout=timeout)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(pem)
        # DEBUG, not INFO — dest's filename embeds the IP, same reasoning as above.
        logger.debug("cert_store: pinned certificate saved to %s", dest)
        return str(dest)
    except OSError as e:
        # IP intentionally omitted — must not be logged at INFO or above
        # (SECURITY.md §6.1). The exception message and the fact
        # that pinning failed are still useful without it.
        logger.warning(
            "cert_store: could not pin certificate, "
            "refusing connection until reachable: %s",
            e,
        )
        return False
