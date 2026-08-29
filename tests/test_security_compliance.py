"""Security compliance tests for all driver code.

These tests statically scan driver source files for violations of the
security contract defined in drivers/SECURITY.md.

Catches: data exfiltration patterns, credential logging, forbidden
function calls, filesystem access, process spawning, and more.

Run with: pytest drivers/tests/ -v
"""

import ast
import re
from pathlib import Path

import pytest

from energy_optimizer_drivers.registry import _load_builtin_drivers

_load_builtin_drivers()

_DRIVERS_ROOT = Path(__file__).parent.parent
# Scan both the per-type driver implementations (drivers/{grid,pv,ev,ac}/*.py)
# and the root-level shared infrastructure (drivers/base.py, cert_store.py,
# registry.py) — the latter was previously invisible to this suite entirely,
# despite cert_store.py doing the most security-sensitive TLS/filesystem work
# in the whole driver layer.
_DRIVER_FILES = [
    f
    for f in list(_DRIVERS_ROOT.glob("*/[!_]*.py")) + list(_DRIVERS_ROOT.glob("[!_]*.py"))
    if "tests" not in f.parts
]
_DRIVER_FILE_IDS = [str(f.relative_to(_DRIVERS_ROOT)) for f in _DRIVER_FILES]

# cert_store.py is the documented, deliberate exception to the "no filesystem
# writes" rule (drivers/SECURITY.md §3.2: "the cert store handles all
# filesystem I/O on the driver's behalf" — that's its entire purpose, TOFU
# certificate pinning to data/certs/). Exempt it from that one check only;
# it's still scanned by every other rule in this file.
_FILES_CHECKED_FOR_FS_WRITES = [f for f in _DRIVER_FILES if f.name != "cert_store.py"]
_FILES_CHECKED_FOR_FS_WRITES_IDS = [
    str(f.relative_to(_DRIVERS_ROOT)) for f in _FILES_CHECKED_FOR_FS_WRITES
]

# Patterns that indicate security violations
_FORBIDDEN_CALLS = {
    "eval": "Dynamic code execution (eval) is forbidden",
    "exec": "Dynamic code execution (exec) is forbidden",
    "compile": "Dynamic code compilation is forbidden",
    "os.system": "Process spawning (os.system) is forbidden",
    "os.popen": "Process spawning (os.popen) is forbidden",
    "pickle.loads": "Unsafe deserialization (pickle) is forbidden",
    "pickle.load": "Unsafe deserialization (pickle) is forbidden",
    "marshal.loads": "Unsafe deserialization (marshal) is forbidden",
    "marshal.load": "Unsafe deserialization (marshal) is forbidden",
}

_FORBIDDEN_IMPORTS = {
    "subprocess": "Process spawning module is forbidden",
    "multiprocessing": "Multiprocessing module is forbidden",
    "pickle": "Unsafe deserialization module is forbidden",
    "marshal": "Unsafe deserialization module is forbidden",
    "shelve": "Unsafe deserialization module (shelve) is forbidden",
}


class _SecurityVisitor(ast.NodeVisitor):
    """AST visitor that collects security violations."""

    def __init__(self) -> None:
        self.violations: list[tuple[int, str]] = []

    def visit_Call(self, node: ast.Call) -> None:
        # Check for forbidden function calls
        func_name = self._get_call_name(node)
        if func_name in _FORBIDDEN_CALLS:
            self.violations.append((node.lineno, _FORBIDDEN_CALLS[func_name]))

        # Check for subprocess usage
        if func_name and func_name.startswith("subprocess."):
            self.violations.append(
                (node.lineno, f"Process spawning ({func_name}) is forbidden")
            )

        # Check for unsafe yaml.load
        if func_name == "yaml.load":
            # Check if Loader= is specified and safe
            has_safe_loader = any(
                (
                    isinstance(kw.value, ast.Attribute)
                    and "Safe" in getattr(kw.value, "attr", "")
                )
                or (
                    isinstance(kw.value, ast.Name)
                    and "Safe" in getattr(kw.value, "id", "")
                )
                for kw in node.keywords
                if kw.arg == "Loader"
            )
            if not has_safe_loader:
                self.violations.append(
                    (node.lineno, "yaml.load() without SafeLoader is forbidden")
                )

        # Check for print() statements
        if func_name == "print":
            self.violations.append(
                (node.lineno, "print() is forbidden — use the logging module")
            )

        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name in _FORBIDDEN_IMPORTS:
                self.violations.append((node.lineno, _FORBIDDEN_IMPORTS[alias.name]))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module and node.module.split(".")[0] in _FORBIDDEN_IMPORTS:
            self.violations.append(
                (node.lineno, _FORBIDDEN_IMPORTS[node.module.split(".")[0]])
            )
        self.generic_visit(node)

    @staticmethod
    def _get_call_name(node: ast.Call) -> str | None:
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name):
                return f"{node.func.value.id}.{node.func.attr}"
            return node.func.attr
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

_FS_WRITE_NAMES = {"write_text", "write_bytes", "mkdir", "unlink", "rmdir"}


def _collect_filesystem_violations(tree: ast.Module) -> list[str]:
    """Walk AST and return filesystem-write violation descriptions."""
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _SecurityVisitor._get_call_name(node)
        if not name:
            continue
        if name in ("open", "builtins.open"):
            mode = _get_open_mode(node)
            if any(c in mode for c in "wax+"):
                violations.append(
                    f"  Line {node.lineno}: open() with write mode '{mode}'"
                )
        elif name in _FS_WRITE_NAMES:
            violations.append(f"  Line {node.lineno}: {name}() — filesystem write")
    return violations


_LOG_CALLS_AT_OR_ABOVE_INFO = {
    "logger.info",
    "logger.warning",
    "logger.error",
    "logger.critical",
}


def _has_word(identifier: str, word: str) -> bool:
    """True if *word* appears as a standalone underscore-delimited component.

    Splitting on ``_`` (rather than using a bare ``\\bword\\b`` regex) matters
    because ``\\b`` does not treat ``_`` as a boundary character, so it would
    miss ``self._ip`` (attribute name ``_ip``) entirely. Word-splitting also
    avoids false positives like ``recipient`` or ``skip`` merely containing
    "ip" as a substring.
    """
    return word in re.split(r"_+", identifier.lower())


def _looks_like_ip_argument(node: ast.expr) -> bool:
    """True if *node* is an expression whose name suggests it holds an IP address.

    Deliberately narrow: only matches on "ip" as a distinct identifier word
    (``ip``, ``self._ip``, ``local_ip``, ``result["ip"]``, ...), not on
    "host" or "address" — a real false positive was found in
    ``aurora_rs485.py``, where an "address" variable is an RS-485 bus
    address (a small integer), not a network IP, and would otherwise be
    wrongly flagged.
    """
    if isinstance(node, ast.Name):
        return _has_word(node.id, "ip")
    if isinstance(node, ast.Attribute):
        return _has_word(node.attr, "ip")
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
        value = node.slice.value
        return isinstance(value, str) and _has_word(value, "ip")
    return False


def _get_open_mode(node: ast.Call) -> str:
    """Extract the mode argument from an open() call."""
    if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
        return str(node.args[1].value)
    for kw in node.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            return str(kw.value.value)
    return "r"


# ═══════════════════════════════════════════════════════════════════════════════
# STATIC ANALYSIS TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestForbiddenPatterns:
    """Scan driver source files for forbidden function calls and imports."""

    @pytest.fixture(params=_DRIVER_FILES, ids=_DRIVER_FILE_IDS)
    def driver_source(self, request: pytest.FixtureRequest) -> tuple[Path, ast.Module]:
        path = request.param
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        return path, tree

    def test_no_forbidden_calls_or_imports(
        self, driver_source: tuple[Path, ast.Module]
    ) -> None:
        path, tree = driver_source
        visitor = _SecurityVisitor()
        visitor.visit(tree)
        if visitor.violations:
            report = "\n".join(
                f"  Line {line}: {msg}" for line, msg in visitor.violations
            )
            pytest.fail(f"Security violations in {path.name}:\n{report}")


class TestNoCredentialLogging:
    """Drivers must never log credentials."""

    @pytest.fixture(params=_DRIVER_FILES, ids=_DRIVER_FILE_IDS)
    def driver_source(self, request: pytest.FixtureRequest) -> tuple[Path, str]:
        path = request.param
        source = path.read_text(encoding="utf-8")
        return path, source

    def test_no_password_in_log_calls(self, driver_source: tuple[Path, str]) -> None:
        path, source = driver_source
        # Look for logger calls that reference password/credential/token/secret/api_key
        pattern = (
            r"logger\.\w+\(.*(?:password|credential|token|secret|api_key|apikey).*\)"
        )
        matches = re.finditer(pattern, source, re.IGNORECASE)
        violations = []
        for match in matches:
            line_num = source[: match.start()].count("\n") + 1
            violations.append(f"  Line {line_num}: {match.group()[:80]}")
        if violations:
            pytest.fail(
                f"Potential credential logging in {path.name}:\n"
                + "\n".join(violations)
            )


class TestNoOutboundInternet:
    """Drivers must not make outbound internet connections."""

    @pytest.fixture(params=_DRIVER_FILES, ids=_DRIVER_FILE_IDS)
    def driver_source(self, request: pytest.FixtureRequest) -> tuple[Path, str]:
        path = request.param
        source = path.read_text(encoding="utf-8")
        return path, source

    def test_no_hardcoded_external_urls(self, driver_source: tuple[Path, str]) -> None:
        path, source = driver_source
        # Check for URLs pointing to external services (not local IPs/hostnames)
        # Allow: http://{ip}, https://{ip}, http://192.168.x.x, etc.
        # Flag: http://api.example.com, https://cloud.service.io, etc.
        external_url_pattern = (
            r"https?://(?![\{\$]|localhost|127\.0\.0\.1|192\.168\.|10\.|172\.(1[6-9]|2[0-9]|3[01])\."
            r")[a-zA-Z][\w.-]+\.\w+"
        )
        matches = list(re.finditer(external_url_pattern, source))
        # Filter out comments and docstrings containing documentation URLs
        violations = []
        for match in matches:
            line_start = source.rfind("\n", 0, match.start()) + 1
            line = source[line_start : source.find("\n", match.start())]
            stripped = line.strip()
            if stripped.startswith(("#", '"""', "'''")):
                continue
            # Skip lines that are clearly documentation references
            lower = stripped.lower()
            if "documentation" in lower or "docs" in lower or "reference" in lower:
                continue
            line_num = source[: match.start()].count("\n") + 1
            violations.append(f"  Line {line_num}: {match.group()}")
        if violations:
            pytest.fail(
                f"Potential outbound internet URLs in {path.name}:\n"
                + "\n".join(violations)
                + "\n\nDrivers may only communicate with LAN-local devices."
            )


class TestNoFileSystemWrites:
    """Drivers must not write to the filesystem.

    Exception: cert_store.py — see drivers/SECURITY.md §3.2, it's the
    designated shared filesystem I/O handler for TOFU cert pinning.
    """

    @pytest.fixture(params=_FILES_CHECKED_FOR_FS_WRITES, ids=_FILES_CHECKED_FOR_FS_WRITES_IDS)
    def driver_source(self, request: pytest.FixtureRequest) -> tuple[Path, ast.Module]:
        path = request.param
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        return path, tree

    def test_no_file_write_operations(
        self, driver_source: tuple[Path, ast.Module]
    ) -> None:
        path, tree = driver_source
        violations = _collect_filesystem_violations(tree)
        if violations:
            pytest.fail(
                f"Filesystem write operations in {path.name}:\n" + "\n".join(violations)
            )


class TestNetworkTimeouts:
    """All network calls must have explicit timeouts."""

    @pytest.fixture(params=_DRIVER_FILES, ids=_DRIVER_FILE_IDS)
    def driver_source(self, request: pytest.FixtureRequest) -> tuple[Path, str]:
        path = request.param
        source = path.read_text(encoding="utf-8")
        return path, source

    def test_requests_calls_have_timeout(self, driver_source: tuple[Path, str]) -> None:
        path, source = driver_source
        # Find requests.get/post/put/delete/patch calls without timeout
        pattern = r"(?:requests|session|self\._session|sess)\.\b(get|post|put|delete|patch|head)\b\([^)]*\)"
        violations = []
        for match in re.finditer(pattern, source, re.DOTALL):
            if "timeout" not in match.group():
                line_num = source[: match.start()].count("\n") + 1
                violations.append(
                    f"  Line {line_num}: {match.group()[:60]}... — missing timeout"
                )
        if violations:
            pytest.fail(
                f"Network calls without explicit timeout in {path.name}:\n"
                + "\n".join(violations)
            )


class TestNoGlobalSSLDisable:
    """Drivers must not globally disable SSL verification."""

    @pytest.fixture(params=_DRIVER_FILES, ids=_DRIVER_FILE_IDS)
    def driver_source(self, request: pytest.FixtureRequest) -> tuple[Path, str]:
        path = request.param
        source = path.read_text(encoding="utf-8")
        return path, source

    def test_no_global_ssl_monkey_patch(self, driver_source: tuple[Path, str]) -> None:
        path, source = driver_source
        # Check for ssl context manipulation or global verify=False
        patterns = [
            (r"ssl\._create_unverified_context", "ssl._create_unverified_context"),
            (
                r"ssl\.create_default_context\(\)\.check_hostname\s*=\s*False",
                "Disabling hostname check",
            ),
            (r"ssl\.CERT_NONE", "ssl.CERT_NONE — disabling certificate verification"),
        ]
        violations = []
        for pattern, description in patterns:
            for match in re.finditer(pattern, source):
                line_num = source[: match.start()].count("\n") + 1
                violations.append(f"  Line {line_num}: {description}")
        if violations:
            pytest.fail(
                f"Global SSL verification disabled in {path.name}:\n"
                + "\n".join(violations)
            )


class TestNoIPAddressLogging:
    """Drivers must not log IP addresses at INFO level or above.

    drivers/SECURITY.md §6.1: "No IPs logged at INFO or above" — DEBUG is
    fine. Only the *argument* expressions passed to a logger call are
    checked (e.g. the value behind a %s placeholder), never the literal
    message text itself — a message like "could not determine local IP"
    discloses nothing and must not be flagged.

    Note: this is a static, name-based check. It cannot catch IP data that
    reaches a log call *indirectly* (e.g. a file path string that happens to
    embed the IP, built up elsewhere from a variable with no "ip" in its own
    name) — that class of violation still requires manual review.
    """

    @pytest.fixture(params=_DRIVER_FILES, ids=_DRIVER_FILE_IDS)
    def driver_source(self, request: pytest.FixtureRequest) -> tuple[Path, ast.Module]:
        path = request.param
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        return path, tree

    def test_no_ip_argument_in_log_calls_above_debug(
        self, driver_source: tuple[Path, ast.Module]
    ) -> None:
        path, tree = driver_source
        violations: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func_name = _SecurityVisitor._get_call_name(node)
            if func_name not in _LOG_CALLS_AT_OR_ABOVE_INFO:
                continue
            # node.args[0] is the format string / message itself — only the
            # values interpolated into it (remaining positional args, plus
            # any keyword args such as exc_info=...) can leak data.
            candidates = list(node.args[1:]) + [kw.value for kw in node.keywords]
            if any(_looks_like_ip_argument(arg) for arg in candidates):
                violations.append(f"  Line {node.lineno}: {func_name}(...) logs what looks like an IP address")
        if violations:
            pytest.fail(
                f"Potential IP-address logging at INFO level or above in {path.name}:\n"
                + "\n".join(violations)
                + "\n\nUse logger.debug() instead, or redact the IP from the message."
            )
