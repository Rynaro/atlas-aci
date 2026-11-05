"""ACI-S38 isolation: pins, tripwires, import surface, fixture integrity and the documents."""

import ast
import hashlib
import importlib.metadata
import inspect
import json
import re
import subprocess
import sys
from pathlib import Path

import test_s38_support as sup

from atlas_aci import server
from atlas_aci.config import Config
from atlas_aci.protocol import revisions as rev

TESTS = Path(__file__).resolve().parents[1]
ROOT = TESTS.parents[1]
SRC = ROOT / "mcp-server" / "src" / "atlas_aci"
FIXTURES = TESTS / "fixtures" / "s38"
DOC = ROOT / "hosts" / "protocol-compatibility.md"
TOOLS_SHA = "94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56"
TOOLS_BYTES = 1631472
GRAPH_OUTPUT_SHA = "713ec5b6b2af963d556ddc3829dfd6919d52b3be2a9d70d918a5a6e270d4cf60"
PYPROJECT_SHA = "c0cfa084bb005c49229181defeb8b27273e0bb1f5a0ac2d0eb82f85d534cb36d"
UV_LOCK_SHA = "6b820f14327cd71951abe8d4a41b2d1962af21b33b492469de82553a31ca2962"
MARKERS = re.compile(
    r"EXTRACTED|INFERRED|AMBIGUOUS|god_node|label_propagation|rationale_for"
    r"|CREATE TABLE IF NOT EXISTS edges"
)
BANNED = {"certified", "guaranteed", "proven", "seamless", "universal"}
UNTESTED = "untested; negotiated revision not verified"
BASE_SIGNATURES = {
    "run_stdio": "(config: 'Config') -> 'None'",
    "build_tool_manifest": (
        "(config: 'Config', protocol_version: 'str' = '2025-11-25') -> 'list[dict[str, Any]]'"
    ),
    "call_tool_result": (
        "(name: 'str', arguments: 'dict[str, Any]', config: 'Config', enforcement: 'Enforcement', "
        "memex: 'Memex', code_graph: 'CodeGraph', *, protocol_version: 'str' = '2025-11-25') "
        "-> 'CallToolResult'"
    ),
    "dispatch_tool_call": (
        "(name: 'str', arguments: 'dict[str, Any]', config: 'Config', enforcement: 'Enforcement', "
        "memex: 'Memex', code_graph: 'CodeGraph', *, structured: 'bool' = True) "
        "-> 'dict[str, Any]'"
    ),
    "build_server_state": "(config: 'Config') -> 'tuple[Enforcement, Memex, CodeGraph]'",
}


def words(text):
    return set(re.findall(r"[a-z]+", text.lower()))


# ------------------------------------------------------------------- the SDK matrix
def test_sdk_matrix_tripwire():
    import mcp.server.session as server_session
    import mcp.shared.version as shared_version
    from mcp import types

    assert importlib.metadata.version("mcp") == "1.27.0" == rev.VERIFIED_SDK[1]
    assert list(rev.REVISION_IDS) == shared_version.SUPPORTED_PROTOCOL_VERSIONS
    assert types.LATEST_PROTOCOL_VERSION == rev.SELECTED == "2025-11-25"
    assert server_session.SUPPORTED_PROTOCOL_VERSIONS is shared_version.SUPPORTED_PROTOCOL_VERSIONS
    lock = (ROOT / "mcp-server" / "uv.lock").read_text()
    assert re.search(r'name = "mcp"\nversion = "1\.27\.0"\n', lock)
    assert {r.status for r in rev.REVISIONS} == set(rev.STATUSES)
    assert [r.id for r in rev.REVISIONS if r.status == "selected"] == [rev.SELECTED]


def test_dependency_files_are_byte_identical_to_the_pins():
    for name, expected in (("pyproject.toml", PYPROJECT_SHA), ("uv.lock", UV_LOCK_SHA)):
        data = (ROOT / "mcp-server" / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == expected, name
    assert "jsonschema" not in (ROOT / "mcp-server" / "pyproject.toml").read_text()


FRESH = """
import hashlib, json, sys
import atlas_aci.protocol.revisions
early = sorted(m for m in sys.modules if m.startswith("atlas_aci"))
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
    "early": early, "tools": len(READ_ONLY_TOOLS), "relations": sorted(ev.RELATIONS),
    "graph_output": hashlib.sha256(canon(graph.GRAPH_OUTPUT_SCHEMA).encode()).hexdigest(),
}))
"""


def test_tools_json_and_budgets_in_a_fresh_interpreter_importing_revisions_first(tmp_path):
    done = subprocess.run(
        [sys.executable, "-c", FRESH, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=120,
        env=sup.child_env(0),
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
    assert "atlas_aci.protocol.revisions" in seen["early"]
    assert not {"atlas_aci.protocol.graph", "atlas_aci.protocol.results"} & set(seen["early"])


def test_tools_json_in_this_interpreter(tmp_path):
    manifest = server.build_tool_manifest(Config(repo=tmp_path, memex_root=tmp_path / "m"))
    raw = (json.dumps(manifest, indent=2) + "\n").encode()
    assert (hashlib.sha256(raw).hexdigest(), len(raw)) == (TOOLS_SHA, TOOLS_BYTES)
    assert len(manifest) == 7
    from atlas_aci.protocol.graph import GRAPH_OUTPUT_SCHEMA

    canon = sup.canon(GRAPH_OUTPUT_SCHEMA).encode()
    assert hashlib.sha256(canon).hexdigest() == GRAPH_OUTPUT_SHA


# ------------------------------------------------------------------- source surface
def test_server_py_anchors_and_signatures():
    text = (SRC / "server.py").read_text()
    assert text.splitlines()[19] == "from mcp.server.stdio import stdio_server"
    assert sum(1 for line in text.splitlines() if MARKERS.search(line)) == 3
    assert "from atlas_aci.protocol.revisions import negotiate" in text
    assert "negotiated_version" not in text and "protocol_negotiated" not in text
    for name, expected in BASE_SIGNATURES.items():
        assert str(inspect.signature(getattr(server, name))) == expected, name


def test_revisions_module_is_pure():
    text = (SRC / "protocol" / "revisions.py").read_text()
    assert len(MARKERS.findall(text)) == 0
    tree = ast.parse(text)
    allowed = {
        "__future__": None,
        "collections.abc": {"Sequence"},
        "dataclasses": {"dataclass"},
        "json": None,
        "typing": {"Any"},
        "mcp": {"types"},
        "mcp.shared": {"version"},
        "pydantic": {"ValidationError"},
    }
    seen = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            seen += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            seen.append(node.module)
            names = allowed.get(node.module, set())
            assert node.module in allowed, node.module
            assert names is None or {a.name for a in node.names} <= names
    assert seen and set(seen) <= set(allowed) and "atlas_aci" not in " ".join(map(str, seen))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert called.isdisjoint({"open", "print", "exec", "eval", "input", "__import__"})
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert attributes.isdisjoint({"write", "read", "info", "warning", "error", "exception"})


def test_the_support_module_has_only_stdlib_top_level_imports():
    tree = ast.parse((TESTS / "slices" / "test_s38_support.py").read_text())
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


def test_new_runtime_surface_is_limited_to_the_protocol_package():
    names = {path.name for path in (SRC / "protocol").glob("*.py")}
    assert "revisions.py" in names
    revisions_import = "from atlas_aci.protocol.revisions import"
    results = (SRC / "protocol" / "results.py").read_text()
    assert results.count(revisions_import) == 2
    assert "SUPPORTED_PROTOCOL_VERSIONS" not in results


# ------------------------------------------------------------------- fixtures
def test_fixture_tree_equals_the_manifest_exactly():
    manifest = json.loads((FIXTURES / "MANIFEST.v1.json").read_text())
    assert (
        manifest["format"] == "atlas-s38-fixtures.v1" and manifest["base_commit"] == sup.BASE_COMMIT
    )
    assert manifest["sdk"] == {"mcp": "1.27.0"} and manifest["captured_with"]
    listed = {row["path"]: row for row in manifest["files"]}
    actual = {
        p.relative_to(FIXTURES).as_posix()
        for p in FIXTURES.rglob("*")
        if p.is_file() and p.name != "MANIFEST.v1.json"
    }
    assert listed and set(listed) == actual
    for path, row in listed.items():
        data = (FIXTURES / path).read_bytes()
        assert (hashlib.sha256(data).hexdigest(), len(data)) == (row["sha256"], row["bytes"])
    assert sum(row["bytes"] for row in listed.values()) <= 2 * 1024 * 1024
    assert sorted(p for p in listed if p.startswith("transcripts/")) == sorted(
        f"transcripts/{case}.v1.json" for case in sup.CASES
    )
    for case in sup.CASES:
        transcript = json.loads((FIXTURES / "transcripts" / f"{case}.v1.json").read_text())
        assert transcript["format"] == sup.TRANSCRIPT_FORMAT and transcript["case"] == case
        assert transcript["sdk"] == {"mcp": "1.27.0"} and transcript["notifications"] == []
        assert transcript["exit"] == {"code": 0, "closed_by": "client"}
        assert transcript["fixture"] == "s38-mini.v1" and transcript["host"] == "raw-line"
        assert "/tmp/" not in json.dumps(transcript) and "/private/" not in json.dumps(transcript)


# ------------------------------------------------------------------- documents
def test_the_hosts_document_states_what_was_observed():
    text = DOC.read_text()
    assert text.isascii() and not words(text) & BANNED
    for revision in rev.REVISIONS:
        assert revision.id in text and revision.status in text and revision.family in text
    for token in (*rev.OUTCOMES, *rev.REASONS, *rev.WIRE, rev.RECORD_FORMAT, rev.SELECTED):
        assert token in text, token
    for name, _ in sup.NORMALISATION_RULES:
        assert re.search(rf"\b{name}\b", text), name
    for token in (
        "1.27.0",
        "HTTP",
        "SSE",
        "batching",
        "logging/setLevel",
        "foo/bar",
        "-32602",
        "-32601",
        "tasks",
        "sampling",
        "elicitation",
        "prompts",
        "completions",
        "pagination",
        "strict-schema",
        "not supported: mcp 1.27.0 does not offer it",
        "selected = newest SDK-supported",
        "The server emits no negotiation record",
        "Unsupported protocol version",
        "3c4d91b7e832d9b74338d56eed03403ccc662123",
        "Apache-2.0",
        "KNOWN_DEVIATIONS",
        "$defs",
        "definitions",
    ):
        assert token in text, token
    assert frozenset() == sup.KNOWN_DEVIATIONS
    assert "`KNOWN_DEVIATIONS`: none" in text
    for line in sup.render_conversation_table():
        assert line in text.splitlines(), line
    for host in ("Claude Code", "GitHub Copilot", "Copilot", "Cursor"):
        lines = [line for line in text.splitlines() if host in line]
        assert lines and all(UNTESTED in line for line in lines), host
    batching_row = next(line for line in text.splitlines() if line.startswith("| 2025-03-26"))
    assert "batching unsupported" in batching_row
    # Every golden named in the matrix exists.
    for case in sup.LIFECYCLE_CASES:
        assert f"transcripts/{case}.v1.json" in text
        assert (FIXTURES / "transcripts" / f"{case}.v1.json").is_file()


def _bullets():
    lines = (ROOT / "CHANGELOG.md").read_text().splitlines()
    added = lines.index("### Added", lines.index("## [Unreleased]"))
    return lines, [i for i, line in enumerate(lines) if line.startswith("- **") and i > added]


def test_the_changelog_carries_one_entry_at_index_six():
    lines, bullets = _bullets()
    for index, prefix in enumerate(
        (
            "Cross-repository package links (ACI-S30).",
            "Git co-change correlation (ACI-S33).",
            "ADR rationale review signals (ACI-S34).",
            "Verified test-outcome feedback (ACI-S35).",
            "Leiden community experiment (ACI-S36)",
            "Python dependency surface",
        )
    ):
        assert lines[bullets[index]].startswith(f"- **{prefix}"), index
    assert lines[bullets[6]].startswith(
        "- **MCP protocol-revision compatibility adapter (ACI-S38).**"
    )
    assert sum("(ACI-S38)" in line for line in lines) == 1
    entry = "\n".join(lines[bullets[6] : bullets[7]])
    assert not any(line.startswith("- **") for line in lines[bullets[6] + 1 : bullets[7]])
    assert entry.isascii() and not words(entry) & BANNED
    assert "hosts/protocol-compatibility.md" in entry and "real hosts are untested" in entry
    for earlier in ("ACI-S30", "ACI-S33", "ACI-S34", "ACI-S35", "ACI-S36", "ACI-S28", "ACI-S27"):
        assert earlier not in entry
    assert lines[bullets[7]].startswith("- **OpenTelemetry trace import")


def test_the_readme_carries_one_row_after_the_community_row():
    readme = (ROOT / "README.md").read_text().splitlines()
    after = next(
        i for i, line in enumerate(readme) if "docs/experiments/community-comparison.md" in line
    )
    row = readme[after + 1]
    assert "hosts/protocol-compatibility.md" in row
    assert row.startswith("|") and row.count("|") == 3 and row.isascii()
    assert sum("hosts/protocol-compatibility.md" in line for line in readme) == 1
    assert readme[after + 2].startswith("| [`docs/frameworks/rails.md`]")
    assert "canary-missions.md" not in row and not words(row) & BANNED
