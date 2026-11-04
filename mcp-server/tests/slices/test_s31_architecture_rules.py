"""ACI-S31: declarative forbidden-import rules with bounded, provenance-bearing counterexamples.

Every tree is indexed for real (git repository, external graph root); nothing is mocked.
"""

from __future__ import annotations

import ast
import base64
import gzip
import hashlib
import inspect
import json
import os
import re
import sqlite3
import subprocess
import sys
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

import pytest
from click.testing import CliRunner
from test_s19_support import build, init_git, inventory, write_tree

from atlas_aci.__main__ import cli
from atlas_aci.architecture import checker, rules
from atlas_aci.codegraph import CodeGraph
from atlas_aci.enforcement import READ_ONLY_TOOLS
from atlas_aci.protocol import results
from atlas_aci.query import admission, impact_admission, paging
from atlas_aci.query.diff import Side
from atlas_aci.query.indexed import IndexedReader
from atlas_aci.storage import evidence as ev

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "mcp-server/tests/fixtures/architecture_rules"
SRC = ROOT / "mcp-server/src/atlas_aci/architecture"
DOCS = ROOT / "docs/architecture-rules.md"
BASE_TOOLS_SHA256 = "94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56"
BASE_TOOLS_BYTES = 1631472

A, B, B2, C = "pkg/domain/a.py", "pkg/domain/b.py", "pkg/domain/b2.py", "pkg/infra/c.py"
D, E = "pkg/domain/d.py", "pkg/domain/e.py"

INIT = {"pkg/__init__.py": "", "pkg/domain/__init__.py": "", "pkg/infra/__init__.py": ""}
CHAIN = {
    **INIT,
    A: "from .b import fb\nfrom .b2 import fb2\nfrom .d import fd\n",
    B: "from ..infra.c import fc\nfrom ..infra.c import fc_alt\n\n\ndef fb():\n    return 1\n",
    B2: "from ..infra.c import fc\n\n\ndef fb2():\n    return 1\n",
    D: "from .e import fe\n\n\ndef fd():\n    return 1\n",
    E: "from ..infra.c import fc\n\n\ndef fe():\n    return 1\n",
    C: "def fc():\n    return 1\n\n\ndef fc_alt():\n    return 2\n",
}
CLEAN = {
    **INIT,
    A: "from .b import fb\n",
    B: "def fb():\n    return 1\n",
    C: "from ..domain.b import fb\n\n\ndef fc():\n    return fb()\n",
    "other/__init__.py": "",
    "other/z.py": "import json\n",
    "tools/build.rb": "def build\nend\n",
}
LINE = {
    **INIT,
    A: "from .b import fb\n\n\ndef fa():\n    return 1\n",
    B: "from .c import fcc\n\n\ndef fb():\n    return 1\n",
    "pkg/domain/c.py": "from .d import fdd\n\n\ndef fcc():\n    return 1\n",
    D: "def fdd():\n    return 1\n",
    "pkg/infra/x.py": "def fx():\n    return 1\n",
}


@dataclass
class World:
    repo: Path
    root: Path

    @property
    def side(self) -> Side:
        return Side(self.repo, self.root)


def make_world(base: Path, files, name="w", langs=None) -> World:
    repo = base / name
    repo.mkdir()
    init_git(repo)
    write_tree(repo, files)
    root = base / (name + "-graph")
    build(repo, root, langs)
    return World(repo, root)


@pytest.fixture(scope="module")
def chain(tmp_path_factory):
    return make_world(tmp_path_factory.mktemp("s31-chain"), CHAIN)


@pytest.fixture(scope="module")
def clean(tmp_path_factory):
    return make_world(tmp_path_factory.mktemp("s31-clean"), CLEAN)


@pytest.fixture(scope="module")
def line(tmp_path_factory):
    return make_world(tmp_path_factory.mktemp("s31-line"), LINE)


def rule(rule_id, sources, targets, **extra):
    return {"id": rule_id, "relation": "import", "from": sources, "to": targets, **extra}


def document(*entries):
    return {"format": rules.FORMAT, "rules": list(entries)}


def raw_of(value) -> bytes:
    return value if isinstance(value, bytes) else json.dumps(value).encode()


def run(world, value, **limits):
    return checker.run(world.side, raw_of(value), limits or None)


def one(world, entry, **limits):
    report = run(world, document(entry), **limits)
    assert len(report["rules"]) == 1
    return report["rules"][0]


def invoke(world, rules_file, *extra, graph_root=None):
    root = world.root if graph_root is None else graph_root
    return CliRunner().invoke(
        cli,
        [
            "check-architecture",
            "--repo",
            str(world.repo),
            "--graph-root",
            str(root),
            "--rules",
            str(rules_file),
            *extra,
        ],
    )


def write_rules(tmp_path, value, name="rules.json") -> Path:
    path = tmp_path / name
    path.write_bytes(raw_of(value))
    return path


def observation_hashes(reader):
    row = reader.db.execute("SELECT value FROM manifest WHERE key='source_observations'").fetchone()
    return {
        item["path"]: item["observed_source_sha256"]
        for item in json.loads(row[0])
        if item["type"] == "observation"
    }


def import_edges(reader, path):
    found = []
    for identifier, payload in reader.db.execute(
        "SELECT id,payload FROM assertions WHERE path=? "
        "AND json_extract(payload,'$.relation')='import' ORDER BY id",
        (path,),
    ):
        item = json.loads(payload)
        target = item["target_entity_id"]
        where = ev.get(reader.db, "entity", target)["path"] if target else None
        found.append((identifier, item, where))
    return found


# ---- AC-S31-001 -------------------------------------------------------------------------


def test_architecture_violation_has_counterexample(chain, tmp_path):
    raw = (FIXTURES / "valid-domain-infra.json").read_bytes()
    report = checker.run(chain.side, raw)
    first, second = report["rules"]
    assert first["id"] == "domain-a-not-infra" and first["outcome"] == "violated"
    assert first["reasons"] == {} and first["counts"]["violating_pairs"] == 1
    assert first["description"] == "The domain entry module must not reach infrastructure."
    (witness,) = first["counterexamples"]
    # b wins the length-2 tie over b2 by path; d -> e -> c (length 3) is not chosen.
    assert witness["length"] == 2
    assert [n["path"] for n in witness["path"]["nodes"]] == [A, B, C]
    assert (witness["source_path"], witness["target_path"]) == (A, C)
    cli_result = invoke(chain, FIXTURES / "valid-domain-infra.json")
    assert cli_result.exit_code == 0, cli_result.stderr
    assert cli_result.stdout_bytes == checker.canonical_bytes(report)
    with ExitStack() as stack:
        reader = checker.open_snapshot(stack, chain.side)
        assert report["snapshot"]["snapshot_id"] == reader.snapshot_id
        assert report["snapshot"]["generation_id"] == reader._lease_generation_id
        hashes = observation_hashes(reader)
        bc = [i for i, _item, where in import_edges(reader, B) if where == C]
        assert len(bc) == 2
        assert witness["path"]["hops"][1]["assertion"]["id"] == min(bc)
        hops = witness["path"]["hops"]
        assert len(hops) == 2
        for hop, (before, after) in zip(hops, [(A, B), (B, C)], strict=True):
            assertion = hop["assertion"]
            assert hop["projection"] == "canonical_assertion_proof"
            assert hop["traversal_direction"] == "outgoing"
            assert assertion["relation"] == "import" and assertion["resolution"] == "INFERRED"
            assert ev.get(reader.db, "assertion", assertion["id"]) == assertion
            assert ev.get(reader.db, "evidence", hop["evidence"]["id"]) == hop["evidence"]
            assert ev.get(reader.db, "occurrence", hop["occurrence"]["id"]) == hop["occurrence"]
            assert ev.get(reader.db, "producer", hop["producer"]["id"]) == hop["producer"]
            assert ev.get(reader.db, "entity", assertion["source_entity_id"])["path"] == before
            assert ev.get(reader.db, "entity", assertion["target_entity_id"])["path"] == after
            assert hop["occurrence"]["path"] == before
            assert hop["evidence"]["source_sha256"] == hashes[before]
            assert hop["source_availability"] == "available"
        for node in witness["path"]["nodes"]:
            assert node["projection"] == "entity_identity"
            assert ev.get(reader.db, "entity", node["id"])["kind"] == "file"
    pairs = {(w["source_path"], w["target_path"]) for w in second["counterexamples"]}
    assert second["outcome"] == "violated" and second["counts"]["violating_pairs"] == 5
    assert pairs == {(A, C), (B, C), (B2, C), (D, C), (E, C)}
    by_source = {w["source_path"]: w for w in second["counterexamples"]}
    assert [n["path"] for n in by_source[D]["path"]["nodes"]] == [D, E, C]
    assert by_source[B]["length"] == 1


# ---- AC-S31-002 -------------------------------------------------------------------------


def test_architecture_complete_scope_can_satisfy(clean, tmp_path):
    report = checker.run(clean.side, (FIXTURES / "valid-domain-infra.json").read_bytes())
    assert len(report["rules"]) == 2
    for entry in report["rules"]:
        assert entry["outcome"] == "satisfied_within_scope", entry["id"]
        assert entry["reasons"] == {} and entry["boundaries"] == []
        assert entry["counterexamples"] == [] and entry["stops"] == []
        selection = entry["selection"]
        assert selection["sources_total"] >= 1 and selection["targets_total"] >= 1
        assert selection["sources_evaluated"] == selection["sources_total"]
        assert entry["counts"]["traversals_stopped"] == 0
        assert entry["counts"]["examined_edges"] >= 1
    assert report["summary"]["satisfied_within_scope"] == 2
    assert report["limitations"]["dynamic_imports_are_not_modeled"] is True
    assert report["limitations"]["satisfied_within_scope_is_not_a_proof_of_absence"] is True
    docs = DOCS.read_text()
    assert "dynamic_imports_are_not_modeled" in docs
    assert "satisfied_within_scope_is_not_a_proof_of_absence" in docs
    # Unreached boundaries stay out: other/z.py's absolute import and the ruby file.
    assert "other/z.py" not in json.dumps(report["rules"])
    assert "tools/build.rb" not in json.dumps(report["rules"])
    # Exit 0 whether or not a rule is violated: the exit code is not a compliance verdict.
    assert invoke(clean, FIXTURES / "valid-domain-infra.json").exit_code == 0
    # Positive control: the same tree with one forbidden import is violated.
    world = make_world(
        tmp_path, {**CLEAN, B: "from ..infra.c import fc\n\n\ndef fb():\n    return 1\n"}
    )
    entry = one(world, rule("control", ["pkg/domain/**"], ["pkg/infra/**"]))
    assert entry["outcome"] == "violated" and entry["counts"]["violating_pairs"] >= 1
    assert invoke(world, FIXTURES / "valid-domain-infra.json").exit_code == 0


# ---- AC-S31-003 -------------------------------------------------------------------------

ALL_DOMAIN = rule("domain-not-infra", ["pkg/domain/**"], ["pkg/infra/**"])
DYNAMIC = "import importlib\n\n\ndef fb():\n    return importlib.import_module('x')\n"
UNKNOWN_CASES = [
    (
        "absolute",
        {A: "import json\nfrom .b import fb\n"},
        None,
        "unresolved_import",
        A,
        "unsupported_absolute_import",
    ),
    (
        "alias",
        {A: "from .b import fb as other\n"},
        None,
        "unresolved_import",
        A,
        "unsupported_import_alias",
    ),
    (
        "control",
        {A: "try:\n    from .b import fb\nexcept ImportError:\n    pass\n"},
        None,
        "unresolved_import",
        A,
        "unsupported_import_control",
    ),
    (
        "depth1",
        {B: "import json\n\n\ndef fb():\n    return 1\n"},
        None,
        "unresolved_import",
        B,
        "unsupported_absolute_import",
    ),
    (
        "ruby",
        {"pkg/domain/legacy.rb": "def legacy\nend\n"},
        None,
        "import_language_unsupported",
        None,
        None,
    ),
    (
        "tsx",
        {"pkg/domain/view.tsx": "import a from './a'\nexport const V = () => null\n"},
        None,
        "import_language_unsupported",
        None,
        None,
    ),
    (
        "disabled",
        {"pkg/domain/legacy.rb": "def legacy\nend\n"},
        ["python"],
        "file_not_indexed",
        None,
        None,
    ),
    ("dynamic", {B: DYNAMIC}, None, "dynamic_import_call", B, "unsupported_absolute_import"),
]


@pytest.mark.parametrize(
    "name,files,langs,kind,where,unresolved",
    UNKNOWN_CASES,
    ids=[case[0] for case in UNKNOWN_CASES],
)
def test_architecture_incomplete_scope_is_unknown(
    tmp_path, name, files, langs, kind, where, unresolved
):
    world = make_world(tmp_path, {**CLEAN, **files}, langs=langs)
    entry = one(world, ALL_DOMAIN)
    assert entry["outcome"] == "unknown", entry["reasons"]
    assert kind in entry["reasons"] and entry["counterexamples"] == []
    found = [b for b in entry["boundaries"] if b["kind"] == kind]
    assert found, entry["boundaries"]
    if where is None:
        where = next(p for p in files if p.endswith((".rb", ".tsx")))
    assert any(b["path"] == where for b in found)
    boundary = next(b for b in found if b["path"] == where)
    if name == "disabled":
        assert boundary["observation_state"] == "disabled"
        assert boundary["observation_reason"] == "language_disabled"
    if name in {"ruby", "tsx"}:
        assert boundary["observation_state"] == "indexed" and boundary["assertion_id"] is None
    if name == "dynamic":
        assert boundary["relation"] == "call" and boundary["raw_target"] == "import_module"
    if unresolved is not None and name != "dynamic":
        assert boundary["relation"] == "import" and boundary["resolution"] == "UNRESOLVED"
        assert boundary["unresolved_reason"] == unresolved
    if boundary["assertion_id"] is not None:
        with ExitStack() as stack:
            reader = checker.open_snapshot(stack, world.side)
            assertion = ev.get(reader.db, "assertion", boundary["assertion_id"])
            evidence = ev.get(reader.db, "evidence", boundary["evidence_id"])
            occurrence = ev.get(reader.db, "occurrence", boundary["occurrence_id"])
            assert assertion["evidence_id"] == evidence["id"]
            assert evidence["occurrence_id"] == occurrence["id"]
            assert occurrence["line"] == boundary["line"] and occurrence["path"] == where


def test_architecture_unknown_without_boundaries_and_precedence(tmp_path):
    world = make_world(tmp_path, CLEAN)
    entry = one(world, rule("missing-target", ["pkg/domain/**"], ["pkg/missing/**"]))
    assert entry["outcome"] == "unknown" and entry["reasons"] == {"empty_target_selection": True}
    assert entry["selection"]["targets_total"] == 0 and entry["boundaries"] == []
    entry = one(world, rule("missing-source", ["pkg/missing/**"], ["pkg/infra/**"]))
    assert entry["reasons"] == {"empty_source_selection": True}
    assert entry["selection"]["sources_total"] == 0 and entry["selection"]["sources_evaluated"] == 0
    # A witness is sound regardless: violated, with the boundary still listed.
    chained = make_world(tmp_path, {**CHAIN, A: "import json\n" + CHAIN[A]}, name="mixed")
    entry = one(chained, rule("mixed", [A], ["pkg/infra/**"]))
    assert entry["outcome"] == "violated" and entry["counts"]["violating_pairs"] == 1
    assert "unresolved_import" in entry["reasons"]
    assert entry["counterexamples"][0]["length"] == 2
    # `violated` with non-empty reasons is sound but not exhaustive; exit 0 is not compliance.
    docs = DOCS.read_text()
    assert "sound but not exhaustive" in docs and "not mean" in docs


def test_architecture_failed_file_with_history_is_not_indexed(tmp_path):
    repo = tmp_path / "failed"
    repo.mkdir()
    init_git(repo)
    base = {
        **INIT,
        "pkg/domain/m.py": "def m():\n    return 1\n",
        "pkg/domain/n.py": "from .m import m\n",
    }
    write_tree(repo, base)
    root = tmp_path / "failed-graph"
    build(repo, root)
    write_tree(repo, {"pkg/domain/n.py": "from .m import m\ndef broken(:\n"})
    build(repo, root)
    world = World(repo, root)
    with ExitStack() as stack:
        reader = checker.open_snapshot(stack, world.side)
        listed = json.loads(
            reader.db.execute(
                "SELECT value FROM manifest WHERE key='source_observations'"
            ).fetchone()[0]
        )
        assert {"history", "observation"} <= {item["type"] for item in listed}
        failed = [i for i in listed if i["type"] == "observation" and i["state"] == "failed"]
        assert [i["path"] for i in failed] == ["pkg/domain/n.py"]
        assert failed[0]["historical_id"] is not None
    entry = one(world, rule("history", ["pkg/domain/n.py"], ["pkg/domain/m.py"]))
    # The stale import is never traversed: no violation, and the file is a named boundary.
    assert entry["outcome"] == "unknown" and entry["counterexamples"] == []
    (boundary,) = entry["boundaries"]
    assert boundary["kind"] == "file_not_indexed" and boundary["observation_state"] == "failed"
    assert boundary["observation_reason"] == "parse_error"


def test_architecture_non_code_files_scope_and_probe_false_positive(tmp_path):
    files = {
        **CLEAN,
        "pkg/domain/NOTES.md": "# notes\n",
        "pkg/domain/t.pyi": "def t() -> int: ...\n",
        "node_modules/x/y.py": "def y():\n    return 1\n",
    }
    world = make_world(tmp_path, files)
    entry = one(world, ALL_DOMAIN)
    # An indexed markdown file in a broad selection is a sound but noisy boundary.
    assert entry["outcome"] == "unknown"
    notes = [b for b in entry["boundaries"] if b["path"] == "pkg/domain/NOTES.md"]
    assert [b["kind"] for b in notes] == ["import_language_unsupported"]
    assert notes[0]["language"] == "markdown"
    # Files outside the indexed scope are silently outside every selection.
    for outside in ("pkg/domain/t.pyi", "node_modules/x/y.py"):
        entry = one(world, rule("outside", [outside], ["pkg/infra/**"]))
        assert entry["reasons"] == {"empty_source_selection": True}
    report = run(world, document(ALL_DOMAIN))
    scope = report["support"]["scope"]
    assert "python" in scope["languages"] and "markdown" in scope["languages"]
    assert "node_modules" in scope["skip_patterns"]
    # A locally defined name that is also a loader spelling is a documented false positive.
    shadow = make_world(
        tmp_path,
        {**CLEAN, A: "from .b import fb\n\n\ndef reload():\n    return 1\n\n\nreload()\n"},
        name="shadow",
    )
    entry = one(shadow, ALL_DOMAIN)
    assert entry["outcome"] == "unknown" and "dynamic_import_call" in entry["reasons"]
    assert "reload" in DOCS.read_text()


# ---- AC-S31-004 -------------------------------------------------------------------------

LINE_RULE = rule("line-a-not-x", [A], ["pkg/infra/**"])


def test_architecture_path_budget_is_visible(line, chain, tmp_path):
    base = one(line, LINE_RULE)
    assert base["outcome"] == "satisfied_within_scope" and base["reasons"] == {}
    assert base["counts"]["examined_edges"] == 3 and base["stops"] == []
    # A leaf at exactly max_depth is not a depth stop: nothing remains to explore.
    leaf = one(line, LINE_RULE, max_depth=3)
    assert leaf["outcome"] == "satisfied_within_scope" and leaf["reasons"] == {}
    # The true limit: pkg/domain/c.py still has an admissible import at max_depth.
    deep = one(line, LINE_RULE, max_depth=2)
    assert deep["outcome"] == "incomplete_due_to_budget"
    assert deep["reasons"] == {"depth_limit": True}
    (stop,) = deep["stops"]
    assert (stop["source_path"], stop["stop_reason"], stop["depth"]) == (A, "depth_limit", 2)
    assert one(line, LINE_RULE, max_depth=1)["reasons"] == {"depth_limit": True}
    nodes = one(line, LINE_RULE, max_visited_nodes=2)
    assert nodes["outcome"] == "incomplete_due_to_budget"
    assert nodes["reasons"] == {"visited_node_limit": True}
    assert [s["stop_reason"] for s in nodes["stops"]] == ["visited_node_limit"]
    assert nodes["stops"][0]["source_path"] == A and nodes["stops"][0]["visited_files"] == 2
    edges = one(line, LINE_RULE, max_examined_edges=1)
    assert edges["reasons"] == {"examined_edge_limit": True}
    assert edges["stops"][0]["examined_edges"] == 1
    children = rule("children", ["pkg/domain/*"], ["pkg/infra/**"])
    limited = one(line, children, max_sources_per_rule=1)
    assert limited["reasons"] == {"source_limit": True}
    assert limited["outcome"] == "incomplete_due_to_budget"
    assert limited["selection"]["sources_evaluated"] == 1 < limited["selection"]["sources_total"]
    whole = (FIXTURES / "valid-domain-infra.json").read_bytes()
    two = checker.run(chain.side, whole, {"max_total_examined_edges": 1})
    first, second = two["rules"]
    assert first["reasons"] == {"total_examined_edge_limit": True}
    assert second["selection"]["sources_evaluated"] == 0
    assert second["reasons"] == {"total_examined_edge_limit": True}
    assert second["outcome"] == "incomplete_due_to_budget" and two["work"]["examined_edges"] == 1
    # Precedence: a boundary and a budget stop together keep both reasons.
    broken = make_world(tmp_path, {**LINE, B: "import json\n" + LINE[B]}, name="line-boundary")
    mixed = one(broken, LINE_RULE, max_depth=2)
    assert mixed["outcome"] == "incomplete_due_to_budget"
    assert {"depth_limit", "unresolved_import"} <= set(mixed["reasons"])
    # A witness found before the stop stays sound: violated plus the budget reason.
    early = one(line, rule("early", [A], [B]), max_depth=2)
    assert early["outcome"] == "violated" and early["reasons"] == {"depth_limit": True}
    assert [n["path"] for n in early["counterexamples"][0]["path"]["nodes"]] == [A, B]
    result = invoke(line, write_rules(tmp_path, document(LINE_RULE)), "--max-depth", "2")
    assert result.exit_code == 0
    assert json.loads(result.stdout)["rules"][0]["reasons"] == {"depth_limit": True}


# ---- AC-S31-005 -------------------------------------------------------------------------

INVALID_FILES = [
    ("invalid-relation-call.json", "unsupported_relation", 0),
    ("invalid-selector-regex.json", "malformed_selector", 0),
    ("invalid-selector-midglob.json", "malformed_selector", 0),
    ("invalid-selector-dotdot.json", "malformed_selector", 0),
    ("invalid-selector-absolute.json", "malformed_selector", 0),
    ("invalid-selector-extglob.json", "malformed_selector", 0),
    ("invalid-selector-midstar.json", "malformed_selector", 0),
    ("invalid-selector-empty-segment.json", "malformed_selector", 0),
    ("invalid-selector-backslash.json", "malformed_selector", 0),
    ("invalid-duplicate-id.json", "duplicate_rule_id", 1),
    ("invalid-extra-key.json", "schema", 0),
    ("invalid-missing-to.json", "schema", 0),
    ("invalid-id-newline.json", "schema", 0),
    ("invalid-format-v2.json", "unsupported_format", None),
]
HEAD = '{"format": "' + rules.FORMAT + '", "rules": '
ONE = '[{"id": "r", "relation": "import", "from": ["pkg/a.py"], "to": ["pkg/b.py"]}]'
INLINE = {
    "rules-65": (
        json.dumps(
            document(*[rule(f"r{i}", [f"pkg/a{i}.py"], ["pkg/z.py"]) for i in range(65)])
        ).encode(),
        "too_large",
        None,
    ),
    "selectors-33": (
        json.dumps(document(rule("r", [f"pkg/a{i}.py" for i in range(33)], ["pkg/z.py"]))).encode(),
        "too_large",
        0,
    ),
    "bytes-256k": (b" " * (rules.MAX_DOCUMENT_BYTES + 1), "too_large", None),
    "bom": (b"\xef\xbb\xbf" + (HEAD + ONE + "}").encode(), "invalid_json", None),
    "duplicate-key": (
        (HEAD + ONE + ', "rules": ' + ONE + "}").encode(),
        "invalid_json",
        None,
    ),
    "nan": ((HEAD + "NaN}").encode(), "invalid_json", None),
    "infinity": ((HEAD + "[Infinity]}").encode(), "invalid_json", None),
    "not-utf8": (b"\xff\xfe{}", "invalid_json", None),
    "deep": (b"[" * 100000 + b"]" * 100000, "invalid_json", None),
    "nesting-at-bound": (
        b"[" * rules.MAX_NESTING_DEPTH + b"]" * rules.MAX_NESTING_DEPTH,
        "schema",
        None,
    ),
    "nesting-over-bound": (
        b"[" * (rules.MAX_NESTING_DEPTH + 1) + b"]" * (rules.MAX_NESTING_DEPTH + 1),
        "invalid_json",
        None,
    ),
    "nesting-objects-over-bound": (
        b'{"a":' * (rules.MAX_NESTING_DEPTH + 1) + b"1" + b"}" * (rules.MAX_NESTING_DEPTH + 1),
        "invalid_json",
        None,
    ),
    "surrogate-selector": (
        (HEAD + ONE.replace("pkg/a.py", "pkg/\\ud800.py") + "}").encode(),
        "invalid_json",
        None,
    ),
    "surrogate-description": (
        (HEAD + ONE.replace('"relation"', '"description": "\\udfff", "relation"') + "}").encode(),
        "invalid_json",
        None,
    ),
    "not-an-object": (b"[]", "schema", None),
    "empty-rules": ((HEAD + "[]}").encode(), "schema", None),
    "id-too-long": (
        json.dumps(document(rule("a" * 65, ["pkg/a.py"], ["pkg/b.py"]))).encode(),
        "schema",
        0,
    ),
    "relation-type": (
        json.dumps(document({**rule("r", ["pkg/a.py"], ["pkg/b.py"]), "relation": 1})).encode(),
        "schema",
        0,
    ),
    "unique-selectors": (
        json.dumps(document(rule("r", ["pkg/a.py", "pkg/a.py"], ["pkg/b.py"]))).encode(),
        "schema",
        0,
    ),
    "selector-513": (
        json.dumps(document(rule("r", ["a" * 513], ["pkg/b.py"]))).encode(),
        "schema",
        0,
    ),
}
LEAK = ("etc/passwd", "secrets", "x.py", "pkg/.*", "\\ud800", "infra")


def check_rejection(raw, reason, index, tmp_path, world):
    with pytest.raises(rules.InvalidRule) as info:
        rules.load(raw)
    assert info.value.reason == reason and info.value.rule_index == index
    assert set(info.value.as_dict()) == {"error", "reason", "rule_index", "rule_id"}
    assert info.value.as_dict()["error"] == "invalid_rule"
    assert str(info.value) == "invalid_rule: " + reason
    assert not any(token in str(info.value) for token in LEAK)
    ghost = tmp_path / "no-such-graph-root"
    result = invoke(world, write_rules(tmp_path, raw), graph_root=ghost)
    where = "" if index is None else f" (rule {index})"
    assert result.exit_code == 1 and result.stdout_bytes == b""
    assert result.stderr == f"Error: Architecture rules rejected: invalid_rule: {reason}{where}\n"
    assert not ghost.exists()
    assert str(tmp_path) not in result.stderr


@pytest.mark.parametrize("name,reason,index", INVALID_FILES, ids=[f[0] for f in INVALID_FILES])
def test_architecture_invalid_rule_rejected(name, reason, index, tmp_path):
    world = World(tmp_path, tmp_path / "graph")
    check_rejection((FIXTURES / name).read_bytes(), reason, index, tmp_path, world)
    assert rules.load((FIXTURES / "valid-domain-infra.json").read_bytes()).rules


@pytest.mark.parametrize("name", sorted(INLINE))
def test_architecture_invalid_documents_are_rejected(name, tmp_path):
    raw, reason, index = INLINE[name]
    check_rejection(raw, reason, index, tmp_path, World(tmp_path, tmp_path / "graph"))


def test_architecture_brackets_inside_strings_do_not_count_toward_depth():
    noisy = "[" * 100 + '\\" ' + "{" * 100 + "]]"
    text = HEAD + ONE.replace('"relation"', '"description": "' + noisy + '", "relation"') + "}"
    loaded = rules.load(text.encode())
    assert loaded.rules[0].description == "[" * 100 + '" ' + "{" * 100 + "]]"
    # An escaped backslash before the closing quote ends the string: depth counts again.
    over = HEAD + '[{"x": "\\\\"}' + "[" * rules.MAX_NESTING_DEPTH + "]" * rules.MAX_NESTING_DEPTH
    with pytest.raises(rules.InvalidRule) as info:
        rules.load((over + "]}").encode())
    assert info.value.reason == "invalid_json"


def test_architecture_rule_ids_and_echo_guard():
    assert rules.InvalidRule("schema", 0, "ok-id").rule_id == "ok-id"
    assert rules.InvalidRule("schema", 0, "a\n").rule_id is None
    assert rules.InvalidRule("schema", 0, "UPPER").rule_id is None
    assert rules.InvalidRule("schema", 0, None).rule_id is None
    with pytest.raises(ValueError):
        rules.InvalidRule("not-a-reason")
    for reason in rules.INVALID_RULE_REASONS:
        assert reason in DOCS.read_text()
    failed = pytest.raises(rules.InvalidRule)
    with failed as info:
        rules.load((FIXTURES / "invalid-duplicate-id.json").read_bytes())
    assert info.value.rule_id == "same-id" and info.value.rule_index == 1
    loaded = rules.load((FIXTURES / "valid-domain-infra.json").read_bytes())
    assert [r.id for r in loaded.rules] == ["domain-a-not-infra", "domain-not-infra"]
    assert (
        loaded.sha256
        == hashlib.sha256((FIXTURES / "valid-domain-infra.json").read_bytes()).hexdigest()
    )


SELECTOR_UNIVERSE = sorted(
    [
        "README.md",
        "pkg/__init__.py",
        "pkg/a.py",
        "pkg/sub/b.py",
        "pkg/sub/deep/c.py",
        "pkgx/y.py",
        "z.py",
        "pkg/caf\u00e9.py",
    ]
)
SELECTOR_CASES = [
    ("**", "all", "", SELECTOR_UNIVERSE),
    ("pkg/**", "tree", "pkg", [p for p in SELECTOR_UNIVERSE if p.startswith("pkg/")]),
    ("pkg/*", "children", "pkg", ["pkg/__init__.py", "pkg/a.py", "pkg/caf\u00e9.py"]),
    ("pkg/sub/**", "tree", "pkg/sub", ["pkg/sub/b.py", "pkg/sub/deep/c.py"]),
    ("pkg/sub/*", "children", "pkg/sub", ["pkg/sub/b.py"]),
    ("pkg/a.py", "literal", "pkg/a.py", ["pkg/a.py"]),
    ("pkg/caf\u00e9.py", "literal", "pkg/caf\u00e9.py", ["pkg/caf\u00e9.py"]),
    ("pkg/cafe\u0301.py", "literal", "pkg/cafe\u0301.py", []),  # exact code points, no NFC
    ("pkg/A.py", "literal", "pkg/A.py", []),  # no case folding
    ("pkg", "literal", "pkg", []),  # a directory literal matches nothing
    ("pkg/missing.py", "literal", "pkg/missing.py", []),
    ("z.py", "literal", "z.py", ["z.py"]),
    ("pkg/**/", None, None, None),
]
MALFORMED = [
    "",
    "*",
    "pkg/",
    "/pkg",
    "pkg/.*",
    "pkg/**/infra",
    "pkg/../secrets",
    "pkg/./a.py",
    ".",
    "..",
    "/etc/passwd",
    "pkg/*.py",
    "pkg/*/x.py",
    "pkg//x.py",
    "pkg\\x.py",
    "a?b",
    "a[b]",
    "a{b}",
    "a!b",
    "a\x00b",
    "a\x7fb",
    "a\tb",
    "C:/x",
    "x" * 513,
]


@pytest.mark.parametrize("text,kind,base,expected", SELECTOR_CASES)
def test_architecture_selector_grammar_and_matching(text, kind, base, expected):
    if kind is None:
        with pytest.raises(ValueError):
            rules.parse_selector(text)
        return
    selector = rules.parse_selector(text)
    assert (selector.kind, selector.base, selector.text) == (kind, base, text)
    assert rules.select(SELECTOR_UNIVERSE, selector) == expected
    assert kind in rules.SELECTOR_KINDS


@pytest.mark.parametrize("text", MALFORMED)
def test_architecture_malformed_selectors(text):
    with pytest.raises(ValueError):
        rules.parse_selector(text)


# ---- AC-S31-006 -------------------------------------------------------------------------


def test_architecture_rule_replay_is_idempotent(chain, line, tmp_path):
    raw = (FIXTURES / "valid-domain-infra.json").read_bytes()
    for world, value in ((chain, raw), (line, document(LINE_RULE))):
        data = raw_of(value)
        first, second = run(world, data), run(world, data)
        assert checker.canonical_bytes(first) == checker.canonical_bytes(second)
        assert first["report_id"] == checker.report_id(first)
        path = write_rules(tmp_path, data, "replay.json")
        result = invoke(world, path)
        assert result.exit_code == 0, result.stderr
        assert result.stdout_bytes == checker.canonical_bytes(first)
        twin = tmp_path / ("graph-again-" + world.repo.name)
        build(world.repo, twin)
        again = checker.run(Side(world.repo, twin), data)
        assert again["snapshot"] == first["snapshot"]
        assert checker.canonical_bytes(again) == checker.canonical_bytes(first)
        outputs = []
        for seed in ("1", "2"):
            done = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "atlas_aci",
                    "check-architecture",
                    "--repo",
                    str(world.repo),
                    "--graph-root",
                    str(world.root),
                    "--rules",
                    str(path),
                ],
                env={**os.environ, "PYTHONHASHSEED": seed},
                capture_output=True,
                check=False,
            )
            assert done.returncode == 0, done.stderr
            outputs.append(done.stdout)
        assert outputs[0] == outputs[1] == checker.canonical_bytes(first)
    full = run(chain, raw)
    capped = run(chain, raw, max_counterexamples_per_rule=1)
    assert capped["report_id"] != full["report_id"]
    whole = full["rules"][1]["counterexamples"]
    prefix = capped["rules"][1]["counterexamples"]
    assert len(whole) == 5 and len(prefix) == 1 and prefix == whole[:1]
    assert capped["rules"][1]["counts"]["violating_pairs"] == 5
    assert checker.canonical_bytes(capped)
    spaced = checker.run(chain.side, raw + b"\n")
    assert spaced["rules_sha256"] != full["rules_sha256"]
    assert spaced["report_id"] != full["report_id"]
    assert full["rules_sha256"] == hashlib.sha256(raw).hexdigest()


# ---- AC-S31-007 -------------------------------------------------------------------------


def test_architecture_check_is_read_only(chain, line, tmp_path):
    before = inventory(chain.repo, chain.root, line.repo, line.root)
    raw = (FIXTURES / "valid-domain-infra.json").read_bytes()
    checker.run(chain.side, raw)
    checker.run(line.side, raw_of(document(LINE_RULE)), {"max_depth": 1})
    assert invoke(chain, FIXTURES / "valid-domain-infra.json").exit_code == 0
    stopped = invoke(line, write_rules(tmp_path, document(LINE_RULE)), "--max-depth", "1")
    assert stopped.exit_code == 0
    assert json.loads(stopped.stdout)["rules"][0]["outcome"] == "incomplete_due_to_budget"
    refused = invoke(chain, FIXTURES / "invalid-relation-call.json")
    assert refused.exit_code == 1
    assert inventory(chain.repo, chain.root, line.repo, line.root) == before
    with ExitStack() as stack:
        reader = checker.open_snapshot(stack, chain.side)
        snapshot, generation = reader.snapshot_id, reader._lease_generation_id
        with pytest.raises(sqlite3.OperationalError):
            reader.db.execute("CREATE TABLE s31_probe(x)")
    assert inventory(chain.repo, chain.root, line.repo, line.root) == before
    with ExitStack() as stack:
        reader = checker.open_snapshot(stack, chain.side)
        assert (reader.snapshot_id, reader._lease_generation_id) == (snapshot, generation)


# ---- supporting: witness oracle, cycles, plans, snapshots, CLI, isolation --------------------


def oracle_edges(world):
    graph = CodeGraph(world.repo, graph_root=world.root, read_only=True)
    try:
        records = graph.evidence_records()
    finally:
        graph.close()
    kinds: dict[str, dict[str, dict]] = {}
    for item in records:
        kinds.setdefault(item["type"], {})[item["id"]] = item
    edges: dict[str, list[tuple[str, str, str]]] = {}
    for item in kinds["assertion"].values():
        if (
            item["relation"] != "import"
            or item["resolution"] not in checker.EDGE_RESOLUTIONS
            or item["target_entity_id"] is None
        ):
            continue
        evidence = kinds["evidence"][item["evidence_id"]]
        source = kinds["occurrence"][evidence["occurrence_id"]]["path"]
        edges.setdefault(source, []).append(
            (item["id"], source, kinds["entity"][item["target_entity_id"]]["path"])
        )
    return edges


def oracle_witness(edges, source, target):
    """Brute force: every simple path (closed into a cycle for source == target)."""
    found: list[list[tuple[str, str, str]]] = []

    def walk(node, trail, seen):
        for identifier, before, after in sorted(edges.get(node, [])):
            step = [*trail, (identifier, before, after)]
            if after == target:
                found.append(step)
            if after not in seen:
                walk(after, step, {*seen, after})

    walk(source, [], {source})
    if not found:
        return None
    shortest = min(len(p) for p in found)
    best = min(
        (p for p in found if len(p) == shortest),
        key=lambda p: [(step[1], step[0]) for step in reversed(p)],
    )
    return [step[0] for step in best], [best[0][1], *(step[2] for step in best)]


def test_architecture_witnesses_equal_the_brute_force_oracle(chain):
    edges = oracle_edges(chain)
    assert edges
    report = run(chain, document(rule("all", ["pkg/domain/**"], ["pkg/infra/**"])))
    examples = report["rules"][0]["counterexamples"]
    assert len(examples) == 5
    for item in examples:
        expected_ids, expected_paths = oracle_witness(
            edges, item["source_path"], item["target_path"]
        )
        assert [h["assertion"]["id"] for h in item["path"]["hops"]] == expected_ids
        assert [n["path"] for n in item["path"]["nodes"]] == expected_paths


CYCLE = {
    **INIT,
    A: "from .b import fb\n\n\ndef fa():\n    return 1\n",
    B: "from .a import fa\n\n\ndef fb():\n    return 1\n",
    "pkg/infra/x.py": "def fx():\n    return 1\n",
}


def test_architecture_cycles_terminate(tmp_path):
    world = make_world(tmp_path, CYCLE)
    entry = one(world, rule("self", [A], [A]))
    assert entry["outcome"] == "violated" and entry["counts"]["violating_pairs"] == 1
    assert entry["counterexamples"][0]["length"] == 2
    assert [n["path"] for n in entry["counterexamples"][0]["path"]["nodes"]] == [A, B, A]
    entry = one(world, rule("away", [A], ["pkg/infra/**"]))
    assert entry["outcome"] == "satisfied_within_scope" and entry["counts"]["visited_files"] == 2
    entry = one(world, rule("no-self", [A], [B]))
    assert entry["counterexamples"][0]["length"] == 1


def test_architecture_import_inside_a_def_is_a_file_level_edge(tmp_path):
    files = {**CHAIN, A: "def f():\n    from .b import fb\n    return fb\n"}
    world = make_world(tmp_path, files)
    entry = one(world, rule("indef", [A], ["pkg/infra/**"]))
    assert entry["outcome"] == "violated"
    assert [n["path"] for n in entry["counterexamples"][0]["path"]["nodes"]] == [A, B, C]
    with ExitStack() as stack:
        reader = checker.open_snapshot(stack, world.side)
        (_id, item, _target) = import_edges(reader, A)[0]
        assert ev.get(reader.db, "entity", item["source_entity_id"])["kind"] == "function"


def test_architecture_query_plans_use_the_path_index(chain):
    sample = (
        [
            (checker.ADJACENCY_SQL, (A,)),
            (checker.DYNAMIC_SQL, (A, *checker.DYNAMIC_IMPORT_CALLEES)),
        ],
        [(checker.OBSERVATIONS_SQL, ()), (checker.GENERATION_SQL, ())]
        + [(sql, ("0" * 64,)) for sql in checker.RECORD_SQL.values()],
    )
    with ExitStack() as stack:
        reader = checker.open_snapshot(stack, chain.side)

        def plan(sql, params):
            return [row[3] for row in reader.db.execute("EXPLAIN QUERY PLAN " + sql, params)]

        for sql, params in sample[0]:
            steps = plan(sql, params)
            assert any(
                s.startswith("SEARCH assertions USING INDEX idx_assertions_path") for s in steps
            ), steps
            assert any("USE TEMP B-TREE" in s for s in steps), steps
            assert not any(s.startswith("SCAN assertions") for s in steps), steps
        for sql, params in sample[1]:
            steps = plan(sql, params)
            assert steps and all(s.startswith("SEARCH") for s in steps), (sql, steps)
            assert not any("SCAN" in s for s in steps), steps
        assert reader.db.execute(checker.OBSERVATIONS_SQL).fetchone() is not None


def test_architecture_local_decode_equals_the_indexed_reader(chain):
    assert isinstance(inspect.getattr_static(IndexedReader, "_record"), staticmethod)
    with ExitStack() as stack:
        reader = checker.open_snapshot(stack, chain.side)
        seen = 0
        for kind, table in ev.TABLES.items():
            for row in reader.db.execute(f"SELECT id,payload FROM {table}"):
                assert checker.decode(row, kind) == IndexedReader._record(row, kind)
                seen += 1
        assert seen > 20
        assert checker.decode(None, "entity") is None
        tampered = ("0" * 64, reader.db.execute("SELECT payload FROM entities").fetchone()[0])
        with pytest.raises(ValueError):
            checker.decode(tampered, "entity")
        with pytest.raises(ValueError):
            IndexedReader._record(tampered, "entity")


def exported_world(world, tmp_path):
    artifact = tmp_path / "export.jsonl"
    source = CodeGraph(world.repo, graph_root=world.root)
    try:
        source.export_jsonl(artifact)
    finally:
        source.close()
    imported = World(world.repo, tmp_path / "imported-graph")
    target = CodeGraph(imported.repo, graph_root=imported.root)
    try:
        target.import_jsonl(artifact)
    finally:
        target.close()
    return imported


def test_architecture_null_snapshot_is_rejected(tmp_path):
    imported = exported_world(make_world(tmp_path, CHAIN, name="exp"), tmp_path)
    graph = CodeGraph(imported.repo, read_only=True, graph_root=imported.root)
    try:
        with graph.reader_lease() as reader:
            assert reader.snapshot_id is None
    finally:
        graph.close()
    raw = (FIXTURES / "valid-domain-infra.json").read_bytes()
    with pytest.raises(checker.ArchitectureRejected) as info:
        checker.run(imported.side, raw)
    assert info.value.reason == "source_snapshot_unavailable"
    result = invoke(imported, FIXTURES / "valid-domain-infra.json")
    assert result.exit_code == 1 and result.stdout_bytes == b""
    assert result.stderr == "Error: Architecture check rejected: source_snapshot_unavailable\n"
    assert str(tmp_path) not in result.stderr


def stage_native(tmp_path, fixture_name, repository_key, files_key, decode):
    fixture = json.loads(
        gzip.decompress((ROOT / "mcp-server/tests/fixtures" / fixture_name).read_bytes())
    )
    repo = Path(fixture[repository_key])
    graph = CodeGraph(repo, graph_root=tmp_path / "old-graph")
    try:
        for name, item in fixture[files_key].items():
            target = graph.atlas_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(decode(item)))
    finally:
        graph.close()
    return World(repo, tmp_path / "old-graph")


def test_architecture_older_epoch_is_a_typed_upgrade_rejection(tmp_path):
    seven = stage_native(tmp_path / "seven", "s12/native7.json.gz", "repository", "files", str)
    everything = document(rule("any", ["**"], ["**"]))
    with pytest.raises(checker.ArchitectureRejected) as info:
        checker.run(seven.side, raw_of(everything))
    assert info.value.reason == "index_upgrade_required"
    # The fixture's original repository path need not exist, so the CLI text is pinned by the
    # docs table and by the shared typed-rejection mapping rather than by a subprocess here.
    assert "Architecture check rejected: index_upgrade_required" in DOCS.read_text()
    # Epoch 8 carries the path indexes the checker needs and is accepted.
    eight = stage_native(
        tmp_path / "eight",
        "s17/native8.json.gz",
        "original_repository",
        "native_files",
        lambda item: item["base64"],
    )
    with ExitStack() as stack:
        reader = checker.open_snapshot(stack, eight.side)
        assert reader._lease_schema_epoch == 8
    assert (
        checker.run(eight.side, raw_of(everything))["rules"][0]["selection"]["sources_total"] >= 1
    )


def test_architecture_missing_or_unopenable_graphs_are_typed(chain, tmp_path):
    raw = (FIXTURES / "valid-domain-infra.json").read_bytes()
    ghost = tmp_path / "nowhere"
    with pytest.raises(checker.ArchitectureRejected) as info:
        checker.run(Side(chain.repo, ghost), raw)
    assert info.value.reason == "index_unavailable"
    result = invoke(chain, FIXTURES / "valid-domain-infra.json", graph_root=ghost)
    assert result.exit_code == 1 and result.stdout_bytes == b""
    assert result.stderr == "Error: Architecture check rejected: index_unavailable\n"
    occupied = tmp_path / "occupied"
    occupied.write_text("not a directory")
    with pytest.raises(checker.DiffUsageError):
        checker.run(Side(chain.repo, occupied), raw)
    result = invoke(chain, FIXTURES / "valid-domain-infra.json", graph_root=occupied)
    assert result.exit_code == 2 and result.stdout_bytes == b""
    assert "Error: The repository cannot be opened as a graph namespace." in result.stderr
    assert str(tmp_path) not in result.stderr


def test_architecture_cli_usage_and_fixed_error_texts(chain, tmp_path):
    good = FIXTURES / "valid-domain-infra.json"
    for flag, name in (
        ("--max-depth", "max_depth"),
        ("--max-visited-nodes", "max_visited_nodes"),
        ("--max-examined-edges", "max_examined_edges"),
        ("--max-total-examined-edges", "max_total_examined_edges"),
        ("--max-sources-per-rule", "max_sources_per_rule"),
        ("--max-counterexamples-per-rule", "max_counterexamples_per_rule"),
        ("--max-boundaries-per-rule", "max_boundaries_per_rule"),
    ):
        low, high = checker.LIMITS[name][1:]
        for bad in (str(low - 1), str(high + 1)):
            result = invoke(chain, good, flag, bad)
            assert result.exit_code == 2 and result.stdout_bytes == b"", (flag, bad)
            assert f"Error: Invalid architecture check limit: {name}\n" in result.stderr
        for edge in (str(low), str(high)):
            assert invoke(chain, good, flag, edge).exit_code == 0, (flag, edge)
    assert invoke(chain, good, "--max-depth", "deep").exit_code == 2
    missing = invoke(chain, tmp_path / "absent-rules.json")
    assert missing.exit_code == 1 and missing.stdout_bytes == b""
    assert missing.stderr == "Error: Architecture rules file cannot be read.\n"
    directory = invoke(chain, tmp_path)
    assert directory.exit_code == 1
    assert directory.stderr == "Error: Architecture rules file cannot be read.\n"
    # Limits are validated before the rules are read, and the rules before the graph.
    both = invoke(chain, tmp_path / "absent-rules.json", "--max-depth", "0")
    assert both.exit_code == 2
    assert "check-architecture" in cli.commands


def test_architecture_limits_table_and_normalizer():
    assert checker.normalize_limits() == {n: v[0] for n, v in checker.LIMITS.items()}
    assert checker.normalize_limits({"max_depth": None})["max_depth"] == 16
    for name, (_default, low, high) in checker.LIMITS.items():
        assert checker.normalize_limits({name: low})[name] == low
        assert checker.normalize_limits({name: high})[name] == high
        for bad in (low - 1, high + 1, True, 1.5, "1"):
            with pytest.raises(checker.ArchitectureUsageError) as info:
                checker.normalize_limits({name: bad})
            assert info.value.limit == name
    with pytest.raises(checker.ArchitectureUsageError):
        checker.normalize_limits({"deadline_ms": 5})


def test_architecture_validate_report_rejects_tampering(chain, clean):
    for world in (chain, clean):
        report = run(world, (FIXTURES / "valid-domain-infra.json").read_bytes())
        checker.validate_report(report)
        checker.validate_report(json.loads(checker.canonical_bytes(report)))
    report = run(chain, (FIXTURES / "valid-domain-infra.json").read_bytes())

    def tampered(change, rehash=True):
        copy = json.loads(json.dumps(report))
        change(copy)
        if rehash:
            copy["report_id"] = checker.report_id(copy)
        with pytest.raises(ValueError):
            checker.validate_report(copy)

    tampered(lambda r: r["rules"][0].update(outcome="satisfied_within_scope"))
    tampered(lambda r: r.update(report_id="0" * 64), rehash=False)
    tampered(lambda r: r["limitations"].pop("dynamic_imports_are_not_modeled"))
    tampered(lambda r: r.update(extra=1))
    tampered(lambda r: r["rules"][0]["counts"].update(violating_pairs=0))
    tampered(lambda r: r["rules"][1]["counterexamples"].reverse())
    tampered(lambda r: r["rules"][0]["counterexamples"][0].update(length=3))
    tampered(lambda r: r["summary"].update(violated=0))
    tampered(lambda r: r["rules"][0]["reasons"].update(empty_target_selection=True))


def test_architecture_surface_is_unchanged_and_cli_only(tmp_path):
    from test_s21_support import workspace

    assert len(READ_ONLY_TOOLS) == 7
    assert not any("architecture" in name for name in READ_ONLY_TOOLS)
    out = CliRunner().invoke(
        cli, ["tools", "--repo", str(workspace(tmp_path))], catch_exceptions=False
    )
    assert out.exit_code == 0
    raw = out.stdout.encode()
    assert len(raw) == BASE_TOOLS_BYTES and hashlib.sha256(raw).hexdigest() == BASE_TOOLS_SHA256
    assert "architecture" not in out.stdout.lower()
    assert "check-architecture" in cli.commands
    assert admission.MINIMUM_BUDGET == 3436
    assert paging.COLLECTION_MINIMUM_BUDGET == 3448
    assert results.FIXED_ERROR_ENVELOPE_CAP == 797
    assert impact_admission.IMPACT_MINIMUM_BUDGET == 7730


BANNED_IMPORTS = {
    "time",
    "datetime",
    "random",
    "uuid",
    "os",
    "subprocess",
    "multiprocessing",
    "threading",
    "importlib",
    "socket",
    "urllib",
    "http",
    "atlas_aci.server",
    "atlas_aci.tools",
}
BANNED_CALLS = {"eval", "exec", "compile", "__import__", "IndexedReader"}


def violations(source: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""] + [(node.module or "") + "." + a.name for a in node.names]
        else:
            names = []
        for name in names:
            if any(name == b or name.startswith(b + ".") for b in BANNED_IMPORTS):
                found.append("import " + name)
            if name.endswith(".navigate") or name == "navigate":
                found.append("import " + name)
        if isinstance(node, ast.Call):
            func = node.func
            called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            builtin = isinstance(func, ast.Name) and called in BANNED_CALLS
            if builtin or called == "IndexedReader":
                found.append("call " + called)
        if isinstance(node, ast.Name | ast.Attribute) and "navigate" in (
            getattr(node, "id", None),
            getattr(node, "attr", None),
        ):
            found.append("reference navigate")
    return found


def test_architecture_modules_have_no_clock_process_or_dynamic_path():
    modules = sorted(SRC.glob("*.py"))
    assert [m.name for m in modules] == ["__init__.py", "checker.py", "rules.py"]
    for module in modules:
        text = module.read_text()
        assert violations(text) == [], module.name
        assert text.isascii(), module.name
        assert "alter table" not in text.lower() and "nx.compose(" not in text
    planted = (
        "import time\nimport os.path\nfrom atlas_aci.tools import search_text\n"
        "eval('1')\nIndexedReader(1)\nfrom atlas_aci.query.traversal import navigate\n"
        "x.navigate\nimport importlib.metadata\n__import__('os')\n"
    )
    assert {
        "import time",
        "import os.path",
        "import atlas_aci.tools",
        "call eval",
        "call IndexedReader",
        "import atlas_aci.query.traversal.navigate",
        "reference navigate",
        "import importlib.metadata",
        "call __import__",
    } <= set(violations(planted))
    assert Path(__file__).read_text().isascii()


BANNED_VOCABULARY = {"safe", "verified", "proven", "guaranteed", "compliant"}


def tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^0-9a-zA-Z]+", text.lower()) if t}


def test_architecture_report_vocabulary_does_not_overclaim(chain, clean):
    structural = {"outcome", "kind", "stop_reason", "relation", "resolution", "format", "checker"}

    def hits(node, key=None):
        found = set()
        if isinstance(node, dict):
            for name, value in node.items():
                found |= tokens(str(name)) & BANNED_VOCABULARY
                found |= hits(value, name)
        elif isinstance(node, list):
            for value in node:
                found |= hits(value, key)
        elif isinstance(node, str) and key in structural:
            found |= tokens(node) & BANNED_VOCABULARY
        return found

    for world in (chain, clean):
        report = run(world, (FIXTURES / "valid-domain-infra.json").read_bytes())
        assert hits(report) == set()
    assert hits({"safe": 1, "outcome": "verified"}) == {"safe", "verified"}
    for token in (
        *checker.OUTCOMES,
        *checker.REASONS,
        *checker.LIMITATIONS,
        *rules.INVALID_RULE_REASONS,
    ):
        assert not tokens(token) & BANNED_VOCABULARY, token


def test_architecture_docs_name_every_outcome_reason_limitation_and_limit():
    docs = DOCS.read_text()
    names = [
        *checker.OUTCOMES,
        *checker.REASONS,
        *checker.REJECTIONS,
        *checker.LIMITATIONS,
        *checker.LIMITS,
        *rules.INVALID_RULE_REASONS,
        *checker.DYNAMIC_IMPORT_CALLEES,
        rules.FORMAT,
        checker.FORMAT,
        checker.CHECKER,
        "check-architecture",
        "idx_assertions_path",
        "code point",
        "support.scope",
    ]
    assert [n for n in names if n not in docs] == []
    assert "Architecture check rejected: index_upgrade_required" in docs
    assert "--fail-on-violation" in docs


def test_architecture_boundary_cap_counts_every_boundary(tmp_path):
    files = {**CLEAN, A: "import json\nimport sys\nimport re\nfrom .b import fb\n"}
    world = make_world(tmp_path, files)
    full = one(world, ALL_DOMAIN)
    assert full["counts"]["boundaries_total"] == 3 == len(full["boundaries"])
    keys = [(b["kind"], b["path"], b["assertion_id"]) for b in full["boundaries"]]
    assert keys == sorted(keys)
    capped = one(world, ALL_DOMAIN, max_boundaries_per_rule=1)
    assert capped["counts"]["boundaries_total"] == 3 and len(capped["boundaries"]) == 1
    assert capped["boundaries"] == full["boundaries"][:1]
    none = one(world, ALL_DOMAIN, max_boundaries_per_rule=0)
    assert none["boundaries"] == [] and none["counts"]["boundaries_total"] == 3
    assert none["outcome"] == "unknown" and none["reasons"] == {"unresolved_import": True}
