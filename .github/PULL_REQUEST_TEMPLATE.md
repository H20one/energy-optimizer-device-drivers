## What does this PR do?

<!-- New driver, bug fix, docs improvement, etc. -->

## Checklist

- [ ] `driver_id` is unique and snake_case
- [ ] `name` / `manufacturer` are real product/brand names
- [ ] `device_type` and `connection_type` use the enums
- [ ] `get_data()` returns the correct TypedDict with all required fields
- [ ] `get_data()` returns `None` on any failure (never raises)
- [ ] `config_schema()` marks passwords as `type: "password"`
- [ ] No imports from `src/` or `config/` (the main app's own code)
- [ ] No real serial numbers, MAC addresses, device/room names, or deployment IPs anywhere in the
      diff — test fixtures use fabricated data shaped to match the protocol, not a real capture
      (see [SECURITY.md](../SECURITY.md) §1.4)
- [ ] Tests pass with mocked I/O — no real hardware/network/serial calls in tests
- [ ] `basedpyright energy_optimizer_drivers/` reports 0 errors

## Does this PR add a new `DeviceType`, change an ABC signature in `base.py`, change
`DRIVER_CALL_TIMEOUT`, or rename the entry-point group or top-level package?

<!--
If yes: stop — see ARCHITECTURE.md's "Changes that need a maintainer, not just a PR" and open an
issue first instead of this PR. These need a coordinated change in the main energy-optimizer app
too, which a PR here can't do on its own.
-->

- [ ] No, none of the above apply to this PR.
