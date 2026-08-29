# Driver Security Contract

This document defines the security rules and data safety requirements that **every driver** (builtin or third-party) must adhere to. Violations will be flagged by the automated driver reviewer agent and will block integration.

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

---

## Enforcement

1. **Automated**: The `@driver-reviewer` agent scans all driver code for violations.
2. **Static**: The `tests/test_security_compliance.py` suite enforces the automated subset
   of the rules above (see the "(automated — ...)" annotations throughout this document for exactly
   which); `tests/test_contract_compliance.py` is a separate suite that validates
   *structural* requirements (identity attributes, method signatures, ABC hierarchy) — it contains
   no security checks itself.
3. **Review**: All driver contributions require review against this security contract, including
   the rules marked "policy only" above that the test suite can't catch.
4. **Runtime**: The application sandboxes driver calls with timeouts and exception guards.

---

## Reporting Violations

If you discover a security violation in a driver (builtin or third-party), report it by opening an issue with the `security` label. Do not include exploit details in public issues — use responsible disclosure.
