"""ACI-S40 isolation: pins, the frozen modules, import and AST rules, the lazy import, parameters,
the dependency tripwires and the documents."""

from __future__ import annotations

import ast
import hashlib
import importlib.metadata
import json
import re
import subprocess
import sys
from pathlib import Path

import test_s38_support as s38
import test_s39_isolation as iso39

from atlas_aci.protocol import http
from atlas_aci.query import authn, authz, shared_http

TESTS = Path(__file__).resolve().parents[1]
ROOT = TESTS.parents[1]
SRC = ROOT / "mcp-server" / "src" / "atlas_aci"
DOC = ROOT / "docs" / "operations" / "shared-http-deployment.md"
TRANSPORT_DOC = ROOT / "hosts" / "http-transport.md"
NEW = ("authz.py", "authn.py", "shared_http.py")
BANNED = iso39.BANNED
FROZEN = {
    "server.py": "21393e61dfbe9ec4ae93f55f7b9c0abf17029681b24f75395d143c955eeb13f7",
    "protocol/http.py": "6afbd23147c6304d9a1f646a82a2c27c937ffc1786b8f5500f8595b1fa0b7f5a",
    "config.py": "9593feb511711113b1c03885233a9da7615a9d7abdf8ab62a75cf345c9251871",
    "enforcement.py": "000eeb5ef65a6d098354708405b5288ff9d6ce2718701ab0d318c60b6558fda9",
    "codegraph.py": "5dbc8433d8a9bb3db80ebbe2d04d1bcfc3f6aa252ea9166e3c94e277e9527026",
    "query/leases.py": "017e8c3ea847f37f0ad548049f50dc1438dc0bb2442fd76bc54598fae92e90aa",
    "query/paging.py": "4924bbc6092e3204e1e805e6a5cc5255b524e55f90ce8260d9fa59d513c2801d",
    "query/impact.py": "7e4233218f36be9b51ff11989382fa6c16be301b5a55679f676d2c36bc6d09e9",
    "query/retrieval.py": "4b8d77c4db886cfe3501e21da2439e5cd3bc963a3e86c3cd9412fb0cf6fdbaa7",
    "memex.py": "2a350d68b494bc7e324a6bad63181f046891b3caa4ae5d53de0eb6d84c96f458",
    "protocol/resources.py": "e9857785d9b839301037c4d4c7cca39d44aa2b78d812d3abccb76648ebdf1744",
    "protocol/results.py": "6dd2ec426d75cffb40637e659476fdbe3ece48b0f0b6d5024f863ca5a468fbdb",
}
FORBIDDEN_MODULES = {
    "subprocess",
    "tempfile",
    "sqlite3",
    "socket",
    "ssl",
    "http.client",
    "urllib.request",
    "httpx",
    "requests",
}
FORBIDDEN_NAMES = {"print", "exec", "eval", "compile", "__import__", "open"}  # bare identifiers
FORBIDDEN_ATTRIBUTES = {"PyJWKClient"}


def words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower()))


# ------------------------------------------------------------------- pins and frozen modules
FRESH = """
import hashlib, json, sys
import atlas_aci.query.shared_http
import atlas_aci.query.authn
import atlas_aci.query.authz
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


def test_tools_json_and_budgets_in_a_fresh_interpreter_importing_the_shared_modules_first(tmp_path):
    done = subprocess.run(
        [sys.executable, "-c", FRESH, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=120,
        env=s38.child_env(0),
    )
    assert done.returncode == 0, done.stderr[-600:]
    seen = json.loads(done.stdout.splitlines()[-1])
    assert seen["tools_sha"] == iso39.TOOLS_SHA and seen["tools_bytes"] == iso39.TOOLS_BYTES
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
    assert seen["graph_output"] == iso39.GRAPH_OUTPUT_SHA


def test_the_frozen_modules_are_byte_identical_to_the_base():
    for name, expected in FROZEN.items():
        data = (SRC / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == expected, name
    assert (
        hashlib.sha256((ROOT / "mcp-server" / "pyproject.toml").read_bytes()).hexdigest()
        == iso39.PYPROJECT_SHA
    )
    assert (
        hashlib.sha256((ROOT / "mcp-server" / "uv.lock").read_bytes()).hexdigest()
        == iso39.UV_LOCK_SHA
    )


def test_the_query_package_gains_exactly_the_three_modules_and_protocol_gains_none():
    names = {p.name for p in (SRC / "query").glob("*.py")}
    assert set(NEW) <= names
    protocol = {p.name for p in (SRC / "protocol").glob("*.py")}
    assert protocol == iso39.BASE_PROTOCOL | {"http.py"}


# ------------------------------------------------------------------- AST rules
def tree_of(name: str) -> ast.Module:
    return ast.parse((SRC / "query" / name).read_text())


def test_the_shared_modules_import_no_network_process_or_temp_file_facility():
    for name in NEW:
        tree = tree_of(name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found = {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                found = {node.module or ""} | {f"{node.module}.{a.name}" for a in node.names}
            else:
                continue
            assert not found & FORBIDDEN_MODULES, (name, found)
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        assert not names & FORBIDDEN_NAMES, (name, names & FORBIDDEN_NAMES)
        assert not (names | attrs) & FORBIDDEN_ATTRIBUTES
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                assert (node.value.id, node.attr) not in {
                    ("os", "system"),
                    ("os", "popen"),
                    ("os", "execv"),
                    ("os", "spawnv"),
                }


def test_file_access_is_confined_to_the_hygienic_reader():
    uses = []
    for name in NEW:
        tree = tree_of(name)
        for func in ast.walk(tree):
            if isinstance(func, ast.FunctionDef):
                for node in ast.walk(func):
                    if (
                        isinstance(node, ast.Attribute)
                        and isinstance(node.value, ast.Name)
                        and node.value.id == "os"
                        and node.attr in {"open", "read", "fstat"}
                    ):
                        uses.append((name, func.name))
    assert {u for u in uses} == {("authz.py", "read_hygienic")}


def test_reason_tuples_are_closed_unique_and_disjoint_from_the_s39_reasons():
    for reasons in (
        shared_http.SHARED_CONFIG_REASONS,
        shared_http.SHARED_REJECTION_REASONS,
        authz.POLICY_REASONS,
        authz.DENIAL_REASONS,
        authz.KINDS,
        authn.AUTHN_REASONS,
    ):
        assert len(set(reasons)) == len(reasons) and all(isinstance(r, str) for r in reasons)
    assert not set(shared_http.SHARED_REJECTION_REASONS) & set(http.REJECTION_REASONS)
    assert set(shared_http.SHARED_REJECTION_REASONS) == set(shared_http._REJECTIONS)
    assert shared_http.PRINCIPAL_SCOPE_KEY == "atlas_aci.shared_principal"
    assert frozenset({"test_dry_run", "memex_read"}) == authz.NEVER_SHARED


# ------------------------------------------------------------------- lazy import and parameters
def test_lazy_import_keeps_the_shared_modules_out_of_the_cli_module():
    code = (
        "import sys\n"
        "import atlas_aci.__main__\n"
        "names = ['atlas_aci.query.shared_http', 'atlas_aci.query.authn', 'atlas_aci.query.authz',"
        " 'jwt', 'atlas_aci.server', 'atlas_aci.protocol.http']\n"
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


def test_serve_shared_parameters_and_the_untouched_serve_command():
    from atlas_aci.__main__ import cli

    assert {p.name for p in cli.commands["serve-shared"].params} == {"shared_config"}
    names = {param.name for param in cli.commands["serve"].params}
    assert names == iso39.BASE_SERVE_PARAMS | iso39.NEW_SERVE_PARAMS


def test_the_main_module_change_is_an_addition_only():
    text = (SRC / "__main__.py").read_text()
    assert text.count('@cli.command("serve-shared")') == 1
    assert text.count("atlas_aci.query.shared_http") == 1  # only the lazy import inside the command


# ------------------------------------------------------------------- dependency tripwires
def test_pyjwt_and_cryptography_are_locked_transitive_dependencies_of_mcp():
    requirements = {
        re.split(r"[ ;<>=!~\[(]", requirement, maxsplit=1)[0].lower().replace("_", "-")
        for requirement in importlib.metadata.requires("mcp") or []
    }
    assert "pyjwt" in requirements
    assert importlib.metadata.version("pyjwt") == "2.15.1"
    assert importlib.metadata.version("cryptography") == "50.0.2"
    assert authn.MIN_VERSIONS == {"pyjwt": (2, 15, 1), "cryptography": (50, 0, 2)}
    declared = (ROOT / "mcp-server" / "pyproject.toml").read_text()
    assert "pyjwt" in declared  # only as a constraint: pyproject is byte-pinned by S39
    authn.check_runtime_versions()


def test_the_support_module_has_only_stdlib_top_level_imports_and_no_tests():
    tree = ast.parse((TESTS / "slices" / "test_s40_support.py").read_text())
    roots = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            roots |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    stdlib = {
        "__future__",
        "asyncio",
        "base64",
        "contextlib",
        "hashlib",
        "hmac",
        "json",
        "os",
        "signal",
        "subprocess",
        "sys",
        "time",
        "collections",
        "dataclasses",
        "pathlib",
        "typing",
    }
    assert roots <= stdlib, roots - stdlib
    functions = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
    assert not [f for f in functions if f.startswith("test_")]


# ------------------------------------------------------------------- documents
def test_the_deployment_document_states_what_was_built_and_what_is_not_isolated():
    text = DOC.read_text()
    assert text.isascii() and not words(text) & BANNED
    reasons = (
        *shared_http.SHARED_CONFIG_REASONS,
        *shared_http.SHARED_REJECTION_REASONS,
        *authz.POLICY_REASONS,
        *authn.AUTHN_REASONS,
        *authz.DENIAL_REASONS,
    )
    for token in reasons:
        assert token in text, token
    fields = (
        "format",
        "listen.host",
        "listen.port",
        "listen.max_inflight",
        "listen.max_body_bytes",
        "listen.allowed_origins",
        "listen.max_inflight_per_principal",
        "identity.issuer",
        "identity.jwks_file",
        "identity.resource_base_url",
        "identity.leeway_seconds",
        "identity.max_token_bytes",
        "identity.max_token_lifetime_seconds",
        "identity.scopes_supported",
        "policy.file",
        "policy.reload_interval_seconds",
        "limits.max_calls_per_minute",
        "limits.max_calls_per_principal_per_minute",
        "limits.max_leases_per_principal",
        "limits.max_graph_leases",
        "limits.max_total_lease_state_bytes",
        "limits.max_records",
        "repositories[].id",
        "repositories[].repo",
        "repositories[].graph_root",
        "repositories[].memex_root",
        "repositories[].before_repo_id",
    )
    for token in fields:
        assert token in text, token
    defaults = (
        "127.0.0.1",
        "8765",
        "1048576",
        "86400",
        "8192",
        "1073741824",
        "17179869184",
        "`max(1, max_inflight // 2)`",
        "65536",
        "300",
        "2.15.1",
        "50.0.2",
    )
    for token in defaults:
        assert token in text, token
    for token in (
        "serve-shared",
        "--shared-config",
        "atlas-aci-shared.v1",
        "atlas-aci-shared-policy.v1",
        "shared_http_listening",
        "shared_http_stopped",
        "shared_policy_invalid",
        "WWW-Authenticate",
        "resource_metadata",
        "RFC 9728",
        "RFC 9068",
        "RS256",
        "PS256",
        "ES256",
        "EdDSA",
        "strict",
        "ripgrep",
        "-e",
        "--",
        "SCOPE_MISMATCH",
        "INVALID_REFERENCE",
        "FORBIDDEN",
        "IMPACT_UNCONFIGURED",
        "epoch",
        "digest",
        "same-size",
        "authorize_path",
        "2 *",
        "same working tree, distinct graph namespace and snapshot",
        "lease registry",
        "content-isolation",
        "--network host",
        "TLS",
        "Host",
        "rate-limit",
        "V1",
        "V2",
        "V3",
        "V4",
        "V5",
        "exit 2",
        "exit 1",
        "SIGTERM",
        "test_dry_run",
        "memex_read",
        "dotfiles",
        ".git",
        "live tree",
        "squat",
        "enumerat",
    ):
        assert token in text, token
    isolation = text.split("## 10. Not isolated", 1)[1].split("## 11.", 1)[0]
    for item in (
        "One process",
        "address space",
        "event loop",
        "Timing side channels",
        "Enforcement.record",
        "repository path",
        "tools/list",
        "per repository, not per principal",
        "S30",
        "disabled",
        "one route only",
        "dotfiles",
        "live tree",
    ):
        assert item in isolation, item


def test_the_transport_document_points_to_the_deployment_document_once():
    text = TRANSPORT_DOC.read_text()
    assert text.count("docs/operations/shared-http-deployment.md") == 1
    assert "loopback-only bind" in text and text.isascii() and not words(text) & BANNED
    for host in ("Claude Code", "GitHub Copilot", "Copilot", "Cursor"):
        lines = [line for line in text.splitlines() if host in line]
        assert all(iso39.UNTESTED in line for line in lines), host
    pointer = [line for line in text.splitlines() if "shared-http-deployment" in line]
    assert pointer and not any(h in "".join(pointer) for h in ("Claude", "Copilot", "Cursor"))


def test_the_changelog_carries_the_security_bullet_and_the_last_added_bullet():
    lines = (ROOT / "CHANGELOG.md").read_text().splitlines()
    unreleased = lines.index("## [Unreleased]")
    sections = [i for i, line in enumerate(lines) if line.startswith("### ") and i > unreleased]
    first, added, changed = sections[0], sections[1], sections[2]
    assert lines[first] == "### Security" and lines[added] == "### Added"
    assert lines[changed] == "### Changed"
    security = [line for line in lines[first:added] if line.startswith("- **")]
    assert security[0].startswith("- **Dependency floors raised")
    assert security[1].startswith("- **Ripgrep option injection closed in `search_text`.**")
    assert len(security) == 2
    bullets = [i for i in range(added, changed) if lines[i].startswith("- **")]
    last = bullets[-1]
    assert lines[last].startswith("- **Shared multi-principal HTTP mode (ACI-S40).**")
    body = "\n".join(lines[last:changed])
    assert not any(line.startswith("- ") for line in lines[last + 1 : changed])
    assert body.isascii() and not words(body) & BANNED
    for needle in ("serve-shared", "docs/operations/shared-http-deployment.md"):
        assert needle in body
    assert sum("(ACI-S40)" in line for line in lines) == 1
    assert "(ACI-S" not in "\n".join(lines[last + 1 : changed])
    injection = "\n".join(lines[first:added])
    assert "(ACI-S" not in injection.split("Ripgrep option injection")[1]
    for needle in ("-e PATTERN", "--", "--pre", "test_dry_run"):
        assert needle in injection


def test_the_readme_carries_one_row_after_the_setup_row():
    readme = (ROOT / "README.md").read_text().splitlines()
    setup = next(i for i, line in enumerate(readme) if line.startswith("| [`SETUP.md`]"))
    row = readme[setup + 1]
    assert row.startswith(
        "| [`docs/operations/shared-http-deployment.md`]"
        "(docs/operations/shared-http-deployment.md) |"
    )
    assert row.count("|") == 3 and row.isascii() and not words(row) & BANNED
    assert "hosts/http-transport.md" not in row
    assert sum("docs/operations/shared-http-deployment.md" in line for line in readme) == 1


def test_the_new_documents_and_tests_are_the_only_additions():
    names = sorted(p.name for p in (TESTS / "slices").glob("test_s40_*.py"))
    assert names == [
        "test_s40_access.py",
        "test_s40_authn.py",
        "test_s40_contract.py",
        "test_s40_injection.py",
        "test_s40_isolation.py",
        "test_s40_policy.py",
        "test_s40_support.py",
    ]
    assert {p.name for p in (ROOT / "docs" / "operations").glob("*.md")} == {
        "shared-http-deployment.md",
        "index-jobs-and-retention.md",
    }
