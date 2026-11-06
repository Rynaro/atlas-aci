"""ACI-S39 isolation: pins, tripwires, the http.py surface, lazy import, parameters, documents."""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import importlib.metadata
import inspect
import json
import re
import subprocess
import sys
from pathlib import Path

import test_s38_support as s38
import test_s39_support as sup

from atlas_aci.config import Config
from atlas_aci.protocol import http

TESTS = Path(__file__).resolve().parents[1]
ROOT = TESTS.parents[1]
SRC = ROOT / "mcp-server" / "src" / "atlas_aci"
HTTP_PY = SRC / "protocol" / "http.py"
DOC = ROOT / "hosts" / "http-transport.md"
COMPAT = ROOT / "hosts" / "protocol-compatibility.md"
TOOLS_SHA = "94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56"
TOOLS_BYTES = 1631472
GRAPH_OUTPUT_SHA = "713ec5b6b2af963d556ddc3829dfd6919d52b3be2a9d70d918a5a6e270d4cf60"
PYPROJECT_SHA = "981f0784d6bc5c1502c91ab27657beb483460b97e066cd8846f51e98f1d7d92f"
UV_LOCK_SHA = "87ba265e76f382aaa4387da7ca5d89dc85eb3699b35ee4315e981302b19646fd"
SERVER_PY_SHA = "21393e61dfbe9ec4ae93f55f7b9c0abf17029681b24f75395d143c955eeb13f7"
MARKERS = re.compile(
    r"EXTRACTED|INFERRED|AMBIGUOUS|god_node|label_propagation|rationale_for"
    r"|CREATE TABLE IF NOT EXISTS edges"
)
BANNED = {"certified", "guaranteed", "proven", "seamless", "universal"}
UNTESTED = "untested; negotiated revision not verified"
IMPORT_ALLOW: dict[str, set[str] | None] = {
    "__future__": None,
    "asyncio": None,
    "contextlib": None,
    "importlib.metadata": None,
    "ipaddress": None,
    "json": None,
    "signal": None,
    "socket": None,
    "threading": None,
    "collections.abc": None,
    "typing": None,
    "structlog": None,
    "uvicorn": None,
    "mcp": {"types"},
    "mcp.types": {"CallToolResult", "Tool"},
    "mcp.server.lowlevel": {"Server"},
    "mcp.server.streamable_http_manager": {"StreamableHTTPSessionManager"},
    "mcp.server.transport_security": {"TransportSecuritySettings"},
    "atlas_aci.config": {"Config"},
    "atlas_aci.server": {"build_server_state", "build_tool_manifest", "call_tool_result"},
    "atlas_aci.protocol.resources": {"RESOURCE_TEMPLATE", "read_resource"},
    "atlas_aci.protocol.revisions": {"VERIFIED_SDK", "negotiate"},
    "atlas_aci.query.leases": {"LeaseRegistry"},
}
TYPE_CHECKING_ONLY = {
    "atlas_aci.codegraph": {"CodeGraph"},
    "atlas_aci.enforcement": {"Enforcement"},
    "atlas_aci.memex": {"Memex"},
}
BASE_SERVE_PARAMS = {
    "graph_root",
    "repo",
    "memex_root",
    "max_bytes_per_call",
    "before_graph_root",
    "before_repo",
    "impact_max_diff_entities",
    "impact_max_diff_occurrences",
    "impact_max_changed_files",
}
NEW_SERVE_PARAMS = {
    "transport",
    "host",
    "port",
    "max_inflight",
    "max_body_bytes",
    "allowed_origins",
}
BASE_PROTOCOL = {
    "__init__.py",
    "context.py",
    "graph.py",
    "impact.py",
    "resources.py",
    "results.py",
    "revisions.py",
}


def words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower()))


# ------------------------------------------------------------------- tripwires
def test_sdk_and_server_tripwires() -> None:
    import inspect as _inspect

    import sse_starlette.sse as sse
    import uvicorn
    from mcp import types
    from mcp.server import streamable_http_manager as manager

    from atlas_aci.protocol import revisions

    requirements = {
        re.split(r"[ ;<>=!~\[(]", requirement, maxsplit=1)[0].lower().replace("_", "-")
        for requirement in importlib.metadata.requires("mcp") or []
    }
    assert {"uvicorn", "starlette", "sse-starlette"} <= requirements
    assert importlib.metadata.version("mcp") == "1.30.0" == revisions.VERIFIED_SDK[1]
    assert importlib.metadata.version("uvicorn") == "0.44.0"
    assert importlib.metadata.version("starlette") == "1.3.1"
    assert importlib.metadata.version("sse-starlette") == "3.3.4"
    wrapped = getattr(uvicorn.Server.capture_signals, "__wrapped__", None)
    assert wrapped is not None and inspect.isgeneratorfunction(wrapped)  # a contextmanager
    assert http.AtlasUvicornServer.capture_signals is not uvicorn.Server.capture_signals
    # Signal drain depends on the patched handle_exit that sse_starlette installs on import.
    assert sse.AppStatus.original_handler is not None
    assert uvicorn.Server.handle_exit is sse.AppStatus.handle_exit
    parameters = _inspect.signature(manager.StreamableHTTPSessionManager).parameters
    assert {"stateless", "json_response", "max_request_body_size", "security_settings"} <= set(
        parameters
    )
    assert types.DEFAULT_NEGOTIATED_VERSION == "2025-03-26"


def test_dependency_files_are_byte_identical_to_the_pins() -> None:
    for name, expected in (("pyproject.toml", PYPROJECT_SHA), ("uv.lock", UV_LOCK_SHA)):
        data = (ROOT / "mcp-server" / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == expected, name
    import tomllib

    declared = tomllib.loads((ROOT / "mcp-server" / "pyproject.toml").read_text())["project"][
        "dependencies"
    ]
    declared_names = {re.split(r"[ <>=!~\[;]", d, maxsplit=1)[0].lower() for d in declared}
    assert declared_names and not declared_names & {"uvicorn", "starlette", "sse-starlette"}


FRESH = """
import hashlib, json, sys
import atlas_aci.protocol.http
from pathlib import Path
from atlas_aci.config import Config
from atlas_aci.server import build_tool_manifest
from atlas_aci.protocol import graph, results
from atlas_aci.query import admission, impact_admission, paging
from atlas_aci.enforcement import READ_ONLY_TOOLS
from atlas_aci.storage import evidence as ev
root = Path(sys.argv[1])
manifest = build_tool_manifest(Config(repo=root, memex_root=root / "memex"))
raw = (json.dumps(manifest, indent=2) + "\\n").encode()
canon = lambda v: json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
print(json.dumps({
    "tools_sha": hashlib.sha256(raw).hexdigest(), "tools_bytes": len(raw),
    "budgets": [admission.MINIMUM_BUDGET, paging.COLLECTION_MINIMUM_BUDGET,
                results.FIXED_ERROR_ENVELOPE_CAP, impact_admission.IMPACT_MINIMUM_BUDGET],
    "tools": len(READ_ONLY_TOOLS), "relations": sorted(ev.RELATIONS),
    "graph_output": hashlib.sha256(canon(graph.GRAPH_OUTPUT_SCHEMA).encode()).hexdigest(),
}))
"""


def test_tools_json_and_budgets_in_a_fresh_interpreter_importing_http_first(
    tmp_path: Path,
) -> None:
    done = subprocess.run(
        [sys.executable, "-c", FRESH, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=120,
        env=s38.child_env(0),
    )
    assert done.returncode == 0, done.stderr[-600:]
    seen = json.loads(done.stdout.splitlines()[-1])
    assert seen["tools_sha"] == TOOLS_SHA and seen["tools_bytes"] == TOOLS_BYTES
    assert seen["budgets"] == [3436, 3448, 797, 7730] and seen["tools"] == 7
    assert seen["relations"] == [
        "call",
        "construct",
        "extend",
        "import",
        "include",
        "prepend",
        "rationale_for",
        "superclass",
    ]
    assert seen["graph_output"] == GRAPH_OUTPUT_SHA


def test_server_py_is_byte_identical_to_the_base() -> None:
    data = (SRC / "server.py").read_bytes()
    assert hashlib.sha256(data).hexdigest() == SERVER_PY_SHA
    text = data.decode()
    assert text.splitlines()[19] == "from mcp.server.stdio import stdio_server"
    assert sum(1 for line in text.splitlines() if MARKERS.search(line)) == 3


# ------------------------------------------------------------------- http.py surface
def imported(tree: ast.Module) -> list[tuple[str, set[str], bool]]:
    """(module, names, under TYPE_CHECKING) for every import in the module."""
    rows: list[tuple[str, set[str], bool]] = []

    def visit(nodes: list[ast.stmt], guarded: bool) -> None:
        for node in nodes:
            if isinstance(node, ast.Import):
                rows.extend((alias.name, set(), guarded) for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                rows.append((node.module or "", {a.name for a in node.names}, guarded))
            elif isinstance(node, ast.If) and ast.unparse(node.test) == "TYPE_CHECKING":
                visit(node.body, True)
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                visit(node.body, guarded)

    visit(tree.body, False)
    return rows


def test_http_module_imports_are_within_the_allow_list() -> None:
    tree = ast.parse(HTTP_PY.read_text())
    rows = imported(tree)
    assert rows
    for module, names, guarded in rows:
        if guarded:
            assert module in TYPE_CHECKING_ONLY, module
            assert names <= TYPE_CHECKING_ONLY[module], (module, names)
            continue
        assert module in IMPORT_ALLOW, module
        allowed = IMPORT_ALLOW[module]
        if allowed is not None and names:
            assert names <= allowed, (module, names - allowed)
    top_level = {module for module, _, guarded in rows if not guarded}
    assert not {"os", "tempfile", "subprocess", "sqlite3", "pathlib", "starlette"} & top_level
    assert not any(m.startswith("starlette") or m.startswith("sse_starlette") for m in top_level)


def test_http_module_makes_no_forbidden_calls_or_logs_off_the_lifecycle() -> None:
    source = HTTP_PY.read_text()
    tree = ast.parse(source)
    assert not MARKERS.search(source)
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert called.isdisjoint({"open", "print", "exec", "eval", "input", "__import__", "compile"})
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert names.isdisjoint({"os", "tempfile", "subprocess", "sqlite3"})
    logged: list[tuple[str, str]] = []
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "log"
            ):
                logged.append((function.name, ast.unparse(node.args[0])))
    # Only run_http logs, and only twice (nested functions are walked under their parents too).
    assert {name for name, _ in logged} == {"run_http"}
    assert sorted(set(event for _, event in logged)) == ["'http_listening'", "'http_stopped'"]


def test_reason_vocabularies_are_closed_and_unique() -> None:
    assert len(http.REJECTION_REASONS) == len(set(http.REJECTION_REASONS)) == 6
    assert len(http.LOOPBACK_REASONS) == len(set(http.LOOPBACK_REASONS)) == 7
    assert set(http.REJECTION_REASONS) == set(http._REJECTIONS)
    assert not set(http.REJECTION_REASONS) & set(http.LOOPBACK_REASONS)
    assert (http.ENDPOINT_PATH, http.RETRY_AFTER_SECONDS) == ("/mcp", 1)
    assert http.TICKET_SCOPE_KEY == "atlas_aci.inflight_ticket"


def test_lazy_import_keeps_http_out_of_the_cli_module() -> None:
    code = (
        "import sys\n"
        "import atlas_aci.__main__\n"
        "names = ['atlas_aci.protocol.http', 'uvicorn', 'starlette', 'sse_starlette',"
        " 'atlas_aci.server']\n"
        "print([n for n in names if any(m == n or m.startswith(n + '.') for m in sys.modules)])\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        env=s38.child_env(0),
    )
    assert done.returncode == 0, done.stderr[-400:]
    assert done.stdout.strip() == "[]"
    # `mcp.server` already loads uvicorn, so importing atlas_aci.server alone (stdio) loads it too.
    code = code.replace("import atlas_aci.__main__", "import atlas_aci.server")
    done = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        env=s38.child_env(0),
    )
    assert "uvicorn" in done.stdout and "atlas_aci.protocol.http" not in done.stdout


def test_serve_parameters_are_base_plus_the_http_options() -> None:
    from atlas_aci.__main__ import cli

    names = {param.name for param in cli.commands["serve"].params}
    assert names == BASE_SERVE_PARAMS | NEW_SERVE_PARAMS
    assert not names & {"frameworks", "rails_table_map"}


def test_protocol_package_is_base_plus_http() -> None:
    names = {path.name for path in (SRC / "protocol").glob("*.py")}
    assert names == BASE_PROTOCOL | {"http.py"}
    assert "http" not in (SRC / "protocol" / "__init__.py").read_text()


def test_config_fields_are_appended_last_and_positional_construction_is_unchanged() -> None:
    fields = [f.name for f in dataclasses.fields(Config)]
    assert fields[:2] == ["repo", "memex_root"]
    assert fields[-5:] == [
        "http_host",
        "http_port",
        "http_max_inflight",
        "http_max_body_bytes",
        "http_allowed_origins",
    ]


def test_the_support_module_has_only_stdlib_top_level_imports_and_no_tests() -> None:
    tree = ast.parse((TESTS / "slices" / "test_s39_support.py").read_text())
    roots = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            roots |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            roots.add((node.module or "").split(".")[0])
    assert roots and roots <= set(sys.stdlib_module_names), roots - set(sys.stdlib_module_names)
    tests = [
        n.name
        for n in tree.body
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name.startswith("test_")
    ]
    assert tests == []
    assert hasattr(sup, "production_main")


def test_the_traversal_gate_replaces_only_sleep() -> None:
    """The H2 hook swaps `traversal.asyncio` for a namespace with one member, so that is all
    the module may use of asyncio."""
    tree = ast.parse((SRC / "query" / "traversal.py").read_text())
    used = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "asyncio"
    }
    assert used == {"sleep"}


# ------------------------------------------------------------------- documents
def test_the_http_document_states_what_was_observed() -> None:
    text = DOC.read_text()
    assert text.isascii() and not words(text) & BANNED
    for token in (*http.REJECTION_REASONS, *http.LOOPBACK_REASONS):
        assert token in text, token
    for token in (
        "--transport",
        "--host",
        "--port",
        "--max-inflight",
        "--max-body-bytes",
        "--allowed-origins",
        "127.0.0.1",
        "8765",
        "1048576",
        "16777216",
        "268435456",
        "403",
        "404",
        "405",
        "413",
        "421",
        "503",
        "Retry-After",
        "2025-03-26",
        "notifications/cancelled",
        "SHOULD NOT",
        "S40",
        "enforcement.py:77",
        "enforcement.py:76",
        "http_listening",
        "http_stopped",
        "3c4d91b7e832d9b74338d56eed03403ccc662123",
        "test execution",
        "--network host",
        "mcp-protocol-version",
        "isError",
    ):
        assert token in text, token
    for host in ("Claude Code", "GitHub Copilot", "Copilot", "Cursor"):
        lines = [line for line in text.splitlines() if host in line]
        assert lines and all(UNTESTED in line for line in lines), host


def test_the_compatibility_document_points_to_the_http_document() -> None:
    text = COMPAT.read_text()
    assert "hosts/http-transport.md" in text
    assert "HTTP" in text and "SSE" in text
    assert "stdio only" in text and text.isascii()
    assert not words(text) & BANNED


def _bullets() -> tuple[list[str], list[int]]:
    lines = (ROOT / "CHANGELOG.md").read_text().splitlines()
    added = lines.index("### Added", lines.index("## [Unreleased]"))
    return lines, [i for i, line in enumerate(lines) if line.startswith("- **") and i > added]


def test_the_changelog_carries_one_entry_at_index_eight() -> None:
    lines, bullets = _bullets()
    unreleased = lines.index("## [Unreleased]")
    first = next(line for line in lines[unreleased + 1 :] if line.startswith("### "))
    assert first == "### Security"
    assert lines[bullets[6]].startswith("- **MCP protocol-revision compatibility adapter")
    assert lines[bullets[7]].startswith("- **OpenTelemetry trace import")
    assert lines[bullets[8]].startswith("- **Loopback Streamable HTTP transport (ACI-S39).**")
    assert lines[bullets[9]].startswith("- **Rails: direct job enqueues")
    assert sum("(ACI-S39)" in line for line in lines) == 1
    entry = "\n".join(lines[bullets[8] : bullets[9]])
    assert not any(line.startswith("- **") for line in lines[bullets[8] + 1 : bullets[9]])
    assert entry.isascii() and not words(entry) & BANNED
    for needle in ("hosts/http-transport.md", "loopback only", "disconnect", "cancels", "S40"):
        assert needle in entry, needle
    assert "(ACI-S38)" not in entry and "ACI-S26" not in entry


def test_the_readme_carries_one_row_after_the_rails_row() -> None:
    readme = (ROOT / "README.md").read_text().splitlines()
    rails = next(
        i for i, line in enumerate(readme) if line.startswith("| [`docs/frameworks/rails.md`]")
    )
    row = readme[rails + 1]
    assert row.startswith("| [`hosts/http-transport.md`](hosts/http-transport.md) |")
    assert row.count("|") == 3 and row.isascii()
    assert sum("hosts/http-transport.md" in line for line in readme) == 1
    assert readme[rails + 2].startswith("| [`SETUP.md`]")
    assert "protocol-compatibility.md" not in row and "canary-missions.md" not in row
    assert not words(row) & BANNED


def test_these_tests_are_the_only_new_test_files() -> None:
    names = sorted(p.name for p in (TESTS / "slices").glob("test_s39_*.py"))
    assert names == [
        "test_s39_cancellation.py",
        "test_s39_contract.py",
        "test_s39_isolation.py",
        "test_s39_loopback.py",
        "test_s39_support.py",
        "test_s39_transport.py",
    ]
