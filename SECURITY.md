# Driver Security Contract

This document defines the security rules and data safety requirements that **every driver** (builtin or third-party) must adhere to. Some violations are caught automatically by CI on every PR; others require a human or AI-assisted reviewer to actually check — see "Enforcement" at the bottom for exactly which is which. Either way, a violation blocks integration.

> **Zero tolerance: no real device data in this repo, ever.** A real serial number, MAC address,
> device name, hardware ID, or any other value that identifies a specific physical unit must never
> appear in source code, test fixtures, docstrings, comments, or documentation — not even as "a
> real example to be helpful." See §1.4 below for the full rule and why it exists independently of
> credential safety, which this is not the same thing as.

---

## Scope

These rules apply to all code inside `energy_optimizer_drivers/` — including builtin drivers, contributed drivers, and pip-installed plugins discovered via the `energy_optimizer.drivers` entry point.

**Note on automated enforcement:** `tests/test_security_compliance.py` can only scan files
physically present in this repo's `energy_optimizer_drivers/` tree (builtin drivers + root-level infra). It cannot
and does not scan externally pip-installed driver packages — those are only covered by this
document as policy and by manual/agent-assisted review (`.github/agents/driver-reviewer.agent.md`),
not by the automated test suite. See "Enforcement" at the bottom for exactly which rules below
have an automated check today and which are policy-only.

---

## 1. Data Handling Rules

### 1.1 No Data Exfiltration

Drivers **MUST NOT**:

- Send data to any external server, API, or endpoint not explicitly required by the physical device protocol.
- Open outbound connections to the internet (only LAN-local communication is permitted for device APIs).
- Include analytics, telemetry, tracking, or beacon functionality.
- Log, store, or transmit personally identifiable information (PII) beyond what is strictly needed for device communication.

### 1.2 Credential Safety

Drivers **MUST NOT**:

- Log credentials (passwords, API keys, tokens) at any log level — not even DEBUG.
- Store credentials in plaintext files, environment variables, or hardcoded values within the driver module.
- Transmit credentials in URL query strings (use request body or auth headers only).
- Retain credentials in memory longer than needed for the active request/session.

Drivers **MUST**:

- Accept credentials only through the `config` dict passed to `__init__()`.
- Mark credential fields with `"type": "password"` in `config_schema()` so the app encrypts them at rest.
- Use HTTPS or authenticated protocols when transmitting credentials over the network.
- Close/logout sessions after each operation cycle to minimize credential exposure window.

### 1.3 Data Minimization

Drivers **MUST**:

- Only read data required by their device-type data contract (e.g. GridMeterData fields).
- Not access or store historical data, user profiles, billing information, or household occupancy patterns beyond what the device naturally exposes for real-time monitoring.

Drivers **MUST NOT**:

- Aggregate or correlate data across multiple polling cycles for purposes other than the immediate return value.
- Create local files, databases, or caches outside of the data returned through `get_data()`.

### 1.4 No Real Device Data Anywhere in This Repo

**(policy only — no automated check, and this one fundamentally can't have one: nothing about a
fabricated hex string and a real captured one is syntactically different, so no static scan can
tell them apart. Enforcement is entirely PR review — the driver-reviewer agent and a human — see
"Enforcement" below.)**

This is distinct from credential safety (§1.2, about secrets a driver handles at *runtime*) — this
rule is about what gets *committed*, and it applies to test fixtures, docstrings, comments, example
config in docs, and PR descriptions, not just driver code that executes.

A driver is meant to work with **every device of the brand/model it targets**, not only the one
specific unit its author happens to own. Writing it against real captured data from your own device
is exactly how driver logic quietly ends up narrower than it should be — hardcoded assumptions that
happen to match your unit's firmware version, region, or configuration, which then breaks for anyone
else's. Fabricated data shaped to match the protocol forces you to write against the *format*, not
one instance of it.

**MUST NOT**, under any circumstance, including "as a realistic example":
- Commit a real serial number, MAC address, device ID, hostname, or any other value that identifies
  a specific physical unit — in test fixtures, docstrings, comments, or documentation.
- Commit a real device name, room label, or any other value a user assigned to their own device.
- Commit a real IP address from an actual deployment (use the `192.168.1.x` / `192.0.2.x`
  documentation ranges already used throughout this repo).

**MUST** instead:
- Use fabricated example data shaped to match the device's real response format — same field names,
  same value types, same general structure, invented values.
- If you need to test against something with realistic *shape* but can't fabricate it confidently
  (e.g. an unusual field encoding), describe the shape in the PR description for review rather than
  pasting a real captured response into a commit.
- Design driver logic (parsing, matching, discovery) against the device's **documented protocol**,
  not the specific quirks of the one unit you tested against — if your device has a firmware-specific
  quirk, handle it as an explicit, commented special case, not as the only code path.

---

## 2. Network Security Rules

### 2.1 Allowed Communication

| Scope                                    | Allowed                   |
| ---------------------------------------- | ------------------------- |
| Device on LAN (HTTP/HTTPS/Modbus/serial) | ✅                        |
| DNS resolution for LAN hostnames         | ✅                        |
| Outbound internet connections            | ❌                        |
| Listening sockets / starting servers     | ❌                        |
| Connecting to cloud APIs                 | ❌                        |
| mDNS/SSDP for discovery (LAN broadcast)  | ✅ (in `discover()` only) |

### 2.2 TLS / Certificate Handling

- Drivers communicating over HTTPS with a self-signed device certificate **MUST** use `energy_optimizer_drivers.cert_store.resolve_verify()` to handle TLS verification. This pins the certificate on first connection (trust on first use / TOFU) and verifies it on every subsequent connection.
- Drivers **MUST NOT** disable verification globally or monkey-patch certificate validation.
- Drivers **MUST NOT** call `urllib3.disable_warnings()` unconditionally — only suppress warnings when `resolve_verify()` returns `False` (i.e. pinning failed).
- If a `ca_cert_path` is provided in config, pass it to `resolve_verify()` — it takes priority over TOFU.
- Drivers **MUST NOT** implement their own certificate fetching or pinning logic; delegate entirely to `cert_store`.

### 2.3 Network Timeouts

- All network operations MUST have explicit timeouts (≤ 15 seconds for `get_data()`, ≤ 30 seconds for `discover()`).
- Drivers MUST NOT use infinite timeouts or blocking calls without a timeout parameter.

---

## 3. Code Safety Rules

### 3.1 No Dynamic Code Execution

Drivers **MUST NOT**:

- Use `eval()`, `exec()`, `compile()` on any input.
- Import modules dynamically based on user/device input (only static or lazy imports allowed).
- Deserialize untrusted data with `pickle`, `marshal`, `yaml.load()` (unsafe), or `shelve`.

### 3.2 No Filesystem Access

Drivers **MUST NOT**:

- Read or write files outside of the driver's own module directory.
- Access the application's database, config files, or other drivers' data.
- Create temporary files (use in-memory processing only).

Exceptions:

- Reading a CA certificate file path provided via `config` is permitted.
- Using `energy_optimizer_drivers.cert_store.resolve_verify()` for TOFU certificate pinning is permitted — the cert store handles all filesystem I/O on the driver's behalf.
- Serial port access (`/dev/ttyUSB*`) for RS-485/Modbus drivers is permitted.

### 3.3 No Process Spawning

Drivers **MUST NOT**:

- Use `subprocess`, `os.system()`, `os.popen()`, or any process spawning mechanism.
- Start threads beyond what is needed for the immediate operation (long-lived background threads are forbidden).

### 3.4 No Monkey-Patching

Drivers **MUST NOT**:

- Modify global state, module-level objects, or class attributes of other modules.
- Patch stdlib or third-party library internals.
- Override signal handlers.

---

## 4. Dependency Rules

### 4.1 Allowed Dependencies

- Drivers should minimize dependencies. Pure-protocol implementations are preferred.
- Allowed: `requests`, `pyserial`, `pymodbus`, standard library modules.
- Prohibited: `pickle`, `ctypes` (unless for serial port access), `socket` (raw — use `requests` or protocol libraries), `multiprocessing`.

### 4.2 No Vendored Binaries

- Drivers MUST NOT include compiled binaries, shared libraries (`.so`, `.dll`), or native extensions.
- All code must be pure Python (auditable).

---

## 5. Error Handling & Resilience

### 5.1 Graceful Failure

- Drivers MUST catch all exceptions internally in `get_data()` and `discover()`.
- Drivers MUST NOT allow exceptions to propagate to the application.
- Failed operations must return `None` (for `get_data()`) or an empty `DiscoveryResult` with warnings.

### 5.2 No Crash Vectors

- Drivers MUST validate device responses before parsing (check length, content-type, status codes).
- Drivers MUST handle malformed/unexpected device responses without crashing.
- Integer overflow, division by zero, and encoding errors from device data must be caught.

---

## 6. Privacy Rules

### 6.1 Logging

- Drivers MAY log at DEBUG/INFO/WARNING level for diagnostic purposes.
- Drivers MUST NOT log: IP addresses at INFO or above **(automated — `TestNoIPAddressLogging`)**,
  credentials at any level **(automated — `TestNoCredentialLogging`)**, energy consumption values
  at INFO or above, only aggregate data in normal operation **(policy only — no automated check
  exists for this specific rule; catch it in review)**.
- All log messages must use the `logging` module (no `print()` statements) **(automated —
  `TestForbiddenPatterns`)**.

### 6.2 Identifiers

- Drivers MUST NOT expose device serial numbers, MAC addresses, or unique identifiers in log
  messages above DEBUG level **(policy only — no automated check)**.
- Device identifiers in `discover()` results must only include what's needed for configuration
  (e.g. IP address), not tracking identifiers **(policy only — no automated check)**.
- This is about *runtime* logging behavior. For the separate, zero-tolerance rule about never
  *committing* a real identifier into this repo's code/tests/docs in the first place, see §1.4.

---

## Enforcement

1. **Actually automated, runs on every push/PR via `.github/workflows/ci.yml`**: `ruff check .`,
   `basedpyright` (type checking — catches a real class of bugs, not a security scanner on its own,
   but a driver that doesn't type-check cleanly is rejected same as one that fails lint or tests),
   and `pytest tests/`, which includes `tests/test_security_compliance.py` (enforces the rules
   annotated "(automated — ...)" throughout this document) and `tests/test_contract_compliance.py`
   (a separate suite validating *structural* requirements — identity attributes, method signatures,
   ABC hierarchy — it contains no security checks itself). This is the only enforcement that runs
   without anyone deliberately invoking it.
2. **Not automated, despite the name**: `.github/agents/driver-reviewer.agent.md` is a checklist
   for a human or AI assistant to apply *when asked* to review a PR — it is not wired into any
   GitHub Actions workflow and does not run by itself. Don't assume a PR has been checked against it
   just because it exists in this repo; someone has to actually invoke it.
3. **Review**: All driver contributions require a human (or a human-directed AI assistant using the
   agent checklist above) to review against this full contract, including every rule marked "policy
   only" that the test suite structurally cannot catch — most importantly §1.4, which by its nature
   never will be automatable.
4. **Runtime**: The application sandboxes driver calls with timeouts and exception guards.

---

## Reporting Violations

If you discover a security violation in a driver (builtin or third-party), report it by opening an issue with the `security` label. Do not include exploit details in public issues — use responsible disclosure.
