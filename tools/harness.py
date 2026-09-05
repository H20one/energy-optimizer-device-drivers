#!/usr/bin/env python3
"""Reference test harness for driver developers.

Exercises a driver the way the host application does — discovery, config schema,
construction, then polled reads — without needing the application itself. The app is
a paid product and its image is not public, so this is the supported way to answer
"will my driver actually be picked up and polled correctly?" before opening a PR.

Deliberately minimal. It prints what a driver returns and nothing more: no dashboard,
no historical storage, no optimizer, no unit conversion, no pretty formatting of
device data. Those are downstream consumers of whatever shape a driver returns and
have no business in a tool whose only job is to show you that shape truthfully. If
you find yourself wanting to add one, you want the app, not this.

Stdlib only, so it runs anywhere `pip install ionemo-drivers` does.

    python tools/harness.py list
    python tools/harness.py schema homewizard_p1
    python tools/harness.py discover homewizard_p1
    python tools/harness.py poll homewizard_p1 --config ip=192.0.2.10
    python tools/harness.py poll aurora_rs485 --config port=/dev/ttyUSB0 --config address=2

`poll` is the one that matters most: it is the loop the app's scheduler runs, so a
driver that survives `poll` for a few minutes is a driver that will survive in the app.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from ionemo_drivers.base import DRIVER_CALL_TIMEOUT, BaseDriver
from ionemo_drivers.contract_validation import validate_contract_data
from ionemo_drivers.registry import DRIVER_REGISTRY, get_driver, load_all_drivers


def _dump(value: Any) -> str:
    """Render a driver's return value as-is, without interpreting it."""
    try:
        return json.dumps(value, indent=2, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(value)


def _parse_config(pairs: list[str]) -> dict[str, Any]:
    """Turn repeated --config key=value into the dict a driver's __init__ takes.

    Values arrive as strings from the command line. Ints are converted because
    several drivers take numeric config (an RS-485 address, a baud rate) and would
    otherwise get "2" where they expect 2 — a mismatch the app never produces, so the
    harness should not invent it either.
    """
    config: dict[str, Any] = {}
    for pair in pairs:
        key, sep, raw = pair.partition("=")
        if not sep:
            raise SystemExit(f"--config expects key=value, got {pair!r}")
        try:
            config[key] = int(raw)
        except ValueError:
            config[key] = raw
    return config


def _require_driver(driver_id: str) -> type[BaseDriver]:
    driver_cls = get_driver(driver_id)
    if driver_cls is None:
        known = ", ".join(sorted(DRIVER_REGISTRY)) or "none"
        raise SystemExit(f"Unknown driver {driver_id!r}. Registered: {known}")
    return driver_cls


def cmd_list(_: argparse.Namespace) -> int:
    """Show every driver the registry can see, builtin or pip-installed."""
    if not DRIVER_REGISTRY:
        print("No drivers registered.")
        return 1
    width = max(len(d) for d in DRIVER_REGISTRY)
    for driver_id in sorted(DRIVER_REGISTRY):
        cls = DRIVER_REGISTRY[driver_id]
        print(
            f"{driver_id:<{width}}  {cls.device_type:<12} "
            f"{cls.connection_type:<9} {cls.manufacturer} {cls.name}"
        )
    return 0


def cmd_schema(args: argparse.Namespace) -> int:
    """Show the config fields the Add Device wizard would render for this driver."""
    driver_cls = _require_driver(args.driver_id)
    fields = driver_cls.config_schema()
    print(_dump(fields))
    required = [f["key"] for f in fields if f.get("required")]
    print(
        f"\n{len(fields)} field(s); required: {', '.join(required) or 'none'}",
        file=sys.stderr,
    )
    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    """Run the driver's own scan, exactly as the wizard's discovery step does."""
    driver_cls = _require_driver(args.driver_id)
    scan = driver_cls.discover_quick() if args.quick else driver_cls.discover()

    print(_dump({"devices": scan.devices, "warnings": scan.warnings}))
    if not scan.devices:
        # Not a failure: plenty of drivers cannot discover at all (a serial device on
        # an unknown bus address), and the app treats an empty result as "offer manual
        # entry", not as an error.
        print(
            "\nNo devices found. If this driver cannot auto-discover, that is fine — "
            "the app falls back to manual configuration.",
            file=sys.stderr,
        )
    return 0


def cmd_poll(args: argparse.Namespace) -> int:
    """Construct the driver and read from it on a loop, like the app's scheduler.

    Also checks each reading against the published data contract for its device type,
    which is the check that most often catches a driver that "works" but returns the
    wrong shape — a missing required key, or None where a real number is required.
    """
    driver_cls = _require_driver(args.driver_id)
    driver = driver_cls(_parse_config(args.config))

    print(
        f"Polling {driver_cls.driver_id} every {args.interval}s "
        f"({'forever' if args.count == 0 else f'{args.count} time(s)'}). Ctrl-C to stop.",
        file=sys.stderr,
    )

    reads = 0
    failures = 0
    try:
        while args.count == 0 or reads < args.count:
            started = time.monotonic()
            data = driver.get_data()
            elapsed = time.monotonic() - started
            reads += 1

            print(f"\n--- read {reads}  ({elapsed:.2f}s)  status={driver.get_status()}")
            if data is None:
                failures += 1
                # None is the contract's way of saying "communication failed" — a
                # driver must return it rather than raise. last_error is where the
                # reason belongs.
                print(f"get_data() returned None. last_error: {driver.last_error}")
            else:
                print(_dump(data))
                problems = validate_contract_data(driver_cls.device_type, data)
                if problems:
                    failures += 1
                    print("CONTRACT VIOLATIONS:", file=sys.stderr)
                    for problem in problems:
                        print(f"  - {problem}", file=sys.stderr)

            if elapsed > DRIVER_CALL_TIMEOUT:
                # The app gives up on a read after this long, so a driver that
                # regularly exceeds it will look broken there even if it works here.
                print(
                    f"SLOW: took {elapsed:.2f}s, over the app's "
                    f"{DRIVER_CALL_TIMEOUT}s budget.",
                    file=sys.stderr,
                )

            if args.count == 0 or reads < args.count:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)

    print(f"\n{reads} read(s), {failures} with problems.", file=sys.stderr)
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="harness",
        description="Exercise an ionemo driver the way the host app does.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="list registered drivers").set_defaults(func=cmd_list)

    p_schema = sub.add_parser("schema", help="show a driver's config fields")
    p_schema.add_argument("driver_id")
    p_schema.set_defaults(func=cmd_schema)

    p_discover = sub.add_parser("discover", help="run the driver's network scan")
    p_discover.add_argument("driver_id")
    p_discover.add_argument(
        "--quick",
        action="store_true",
        help="call discover_quick() instead, the wizard's default first attempt",
    )
    p_discover.set_defaults(func=cmd_discover)

    p_poll = sub.add_parser("poll", help="read from the driver on a loop")
    p_poll.add_argument("driver_id")
    p_poll.add_argument(
        "--config",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="driver config, repeatable (see the `schema` command)",
    )
    p_poll.add_argument("--interval", type=float, default=5.0, help="seconds between reads")
    p_poll.add_argument("--count", type=int, default=0, help="number of reads (0 = forever)")
    p_poll.set_defaults(func=cmd_poll)

    args = parser.parse_args(argv)
    load_all_drivers()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
