"""Shared fixtures for the driver test suite."""

from __future__ import annotations

import pytest

from ionemo_drivers.lan_scan import reset_live_host_cache


@pytest.fixture(autouse=True)
def _clear_live_host_cache():
    """Drop the shared live-host sweep between tests.

    `scan_subnet(quick=True)` memoises which addresses are alive so that drivers
    scanning concurrently share one ARP sweep instead of repeating it each. That cache
    is process-wide by design, so without this a test that populated it would answer
    the next test's pre-filter -- which is exactly how it was caught: a test asserting
    the "/proc/net/arp unreadable, fall back to a full scan" path saw 2 probed
    addresses instead of 253, because an earlier test's cached set was still live.
    """
    reset_live_host_cache()
    yield
    reset_live_host_cache()
