"""ACI-S19 comparison semantics: candidates, failures, rejections, fast path, scope."""

from __future__ import annotations

import base64
import gzip
import json
from pathlib import Path
from typing import ClassVar

import pytest
from test_s16_support import INDEX, MANIFEST, workspace
from test_s19_support import (
    build,
    diff,
    git,
    init_git,
    inventory,
    one,
    records,
    same_path_pair,
    write_tree,
)

from atlas_aci.codegraph import CodeGraph
from atlas_aci.query import diff as symbol_diff
from atlas_aci.query import diff_syntax as syntax
from atlas_aci.query.diff import DiffRejected, DiffUsageError, Side

ROOT = Path(__file__).resolve().parents[3]
TOTAL = (
    "def compute_total(items, tax):\n"
    "    subtotal = sum(items)\n"
    "    return subtotal + subtotal * tax\n"
)


# ---- rename / move candidates
def test_unique_rename_is_a_candidate_and_removed_added_are_kept(tmp_path):
    change_set = diff(
        tmp_path, {"a.py": TOTAL}, {"a.py": TOTAL.replace("compute_total", "compute_sum")}
    )
    assert len(change_set["candidates"]) == 1
    candidate = change_set["candidates"][0]
    gone = one(change_set, "compute_total", "removed")
    born = one(change_set, "compute_sum", "added")
    assert candidate["relation"] == "rename"
    assert candidate["basis"] == "identical_normalized_signature_and_body"
    assert (candidate["before_record_id"], candidate["after_record_id"]) == (gone["id"], born["id"])
    assert candidate["before_entity_id"] != candidate["after_entity_id"]
    assert change_set["summary"]["candidates"] == 1
    assert change_set["summary"]["removed"] == change_set["summary"]["added"] == 1


def test_file_move_is_a_move_candidate(tmp_path):
    change_set = diff(tmp_path, {"a.py": TOTAL}, {"a.py": None, "pkg/b.py": TOTAL})
    assert [c["relation"] for c in change_set["candidates"]] == ["move"]
    candidate = change_set["candidates"][0]
    assert candidate["before"]["path"] == "a.py" and candidate["after"]["path"] == "pkg/b.py"
    assert candidate["before"]["name"] == candidate["after"]["name"] == "compute_total"
    assert change_set["summary"]["removed"] == change_set["summary"]["added"] == 1


def test_rename_and_move_together(tmp_path):
    change_set = diff(
        tmp_path,
        {"a.py": TOTAL},
        {"a.py": None, "b.py": TOTAL.replace("compute_total", "compute_sum")},
    )
    assert [c["relation"] for c in change_set["candidates"]] == ["rename_and_move"]


def test_ambiguous_matches_are_not_candidates(tmp_path):
    twin = (
        TOTAL.replace("compute_total", "twin_a") + "\n\n" + TOTAL.replace("compute_total", "twin_b")
    )
    change_set = diff(tmp_path, {"a.py": twin}, {"a.py": TOTAL})
    assert change_set["summary"]["removed"] == 2 and change_set["summary"]["added"] == 1
    assert change_set["candidates"] == []
    both = (
        TOTAL.replace("compute_total", "born_a") + "\n\n" + TOTAL.replace("compute_total", "born_b")
    )
    other = tmp_path / "second"
    other.mkdir()
    change_set = diff(other, {"a.py": TOTAL}, {"a.py": both})
    assert change_set["summary"]["added"] == 2 and change_set["candidates"] == []


def test_different_body_or_recursion_is_not_a_candidate(tmp_path):
    changed = TOTAL.replace("compute_total", "compute_total2").replace("subtotal * tax", "tax")
    change_set = diff(tmp_path, {"a.py": TOTAL}, {"a.py": changed})
    assert change_set["summary"]["removed"] == change_set["summary"]["added"] == 1
    assert change_set["candidates"] == []
    recursive = (
        "def fact(n):\n    if n < 2:\n        return 1\n    return n * fact(n - 1) + n - n * 2\n"
    )
    renamed = recursive.replace("fact", "fact2")
    other = tmp_path / "recursive"
    other.mkdir()
    change_set = diff(other, {"a.py": recursive}, {"a.py": renamed})
    assert change_set["summary"]["removed"] == change_set["summary"]["added"] == 1
    assert change_set["candidates"] == []


def test_trivial_bodies_never_pair(tmp_path):
    change_set = diff(tmp_path, {"a.py": "def f():\n    pass\n"}, {"a.py": "def g():\n    pass\n"})
    assert change_set["summary"]["removed"] == change_set["summary"]["added"] == 1
    assert change_set["candidates"] == []
    lone = tmp_path / "lone"
    lone.mkdir()
    change_set = diff(
        lone, {"a.py": "def f(x):\n    return x\n"}, {"a.py": "def g(x):\n    return x\n"}
    )
    assert change_set["candidates"] == []
    structs = tmp_path / "structs"
    structs.mkdir()
    change_set = diff(structs, {"a.rs": "struct A;\n"}, {"a.rs": "struct B;\n"})
    assert change_set["summary"]["removed"] == change_set["summary"]["added"] == 1
    assert change_set["candidates"] == []
    empty = tmp_path / "empty"
    empty.mkdir()
    change_set = diff(empty, {"a.js": "function f() {}\n"}, {"a.js": "function g() {}\n"})
    assert change_set["candidates"] == []
    assert syntax.MIN_CANDIDATE_TOKENS == 6


def test_candidates_need_the_same_kind(tmp_path):
    body = "let s = items;\n    s + s * tax\n"
    free = f"fn compute(items: i32, tax: i32) -> i32 {{\n    {body}}}\n"
    inner = "fn compute(items: i32, tax: i32) -> i32"
    method = f"struct S;\nimpl S {{\n    {inner} {{\n        {body}    }}\n}}\n"
    change_set = diff(tmp_path, {"a.rs": free}, {"a.rs": method})
    removed = one(change_set, "compute", "removed")
    added = one(change_set, "compute", "added")
    assert (removed["kind"], added["kind"]) == ("function", "method")
    assert change_set["candidates"] == []


# ---- failed extraction
GOOD = "def keep(a):\n    return a\n\n\ndef other(b):\n    return b\n"


def test_parse_failure_after_is_one_sided_unknown_and_partial(tmp_path):
    change_set = diff(
        tmp_path, {"a.py": GOOD, "ok.py": "def fine():\n    return 1\n"}, {"a.py": "def keep(:\n"}
    )
    assert change_set["partial_paths"] == ["a.py"]
    found = [r for r in change_set["changes"] if r["path"] == "a.py"]
    assert sorted(r["name"] for r in found) == ["keep", "other"]
    for record in found:
        assert record["category"] == "unknown" and record["reason"] == "extraction_failed_after"
        assert record["after"] is None and record["before"]["path"] == "a.py"
    assert not records(change_set, "added") and not records(change_set, "removed")
    assert change_set["summary"]["unknown"] == 2 and change_set["summary"]["unchanged"] == 1


def test_parse_failure_before_is_one_sided_unknown_and_partial(tmp_path):
    change_set = diff(tmp_path, {"a.py": "def keep(:\n"}, {"a.py": GOOD})
    assert change_set["partial_paths"] == ["a.py"]
    assert {r["reason"] for r in change_set["changes"]} == {"extraction_failed_before"}
    assert all(r["before"] is None and r["after"] for r in change_set["changes"])
    assert not records(change_set, "added") and len(change_set["changes"]) == 2


def test_parse_failure_on_both_sides_is_only_partial(tmp_path):
    change_set = diff(tmp_path, {"a.py": "def keep(:\n"}, {"a.py": "def keep(::\n"})
    assert change_set["partial_paths"] == ["a.py"] and change_set["changes"] == []


# ---- rejections
def test_index_unavailable_and_same_namespace(tmp_path):
    repo, before, after = same_path_pair(tmp_path, {"a.py": GOOD}, {"a.py": GOOD})
    empty = tmp_path / "no-graph"
    with pytest.raises(DiffRejected) as info:
        symbol_diff.compare(Side(repo, empty), after)
    assert info.value.reason == "index_unavailable" and not empty.exists()
    with pytest.raises(DiffUsageError):
        symbol_diff.compare(before, before)
    with pytest.raises(DiffUsageError):  # a graph root inside the checkout is refused cleanly
        symbol_diff.compare(Side(repo, repo / "inside"), after)


def test_jsonl_imported_side_has_no_source_snapshot(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    write_tree(repo, {"a.py": GOOD})
    build(repo, tmp_path / "native")
    graph = CodeGraph(repo, graph_root=tmp_path / "native")
    artifact = tmp_path / "graph.jsonl"
    graph.export_jsonl(artifact)
    graph.close()
    imported = CodeGraph(repo, graph_root=tmp_path / "imported")
    imported.import_jsonl(artifact)
    imported.close()
    watched = [tmp_path / "native", tmp_path / "imported", repo]
    state = inventory(*watched)
    for first, second in (
        (Side(repo, tmp_path / "native"), Side(repo, tmp_path / "imported")),
        (Side(repo, tmp_path / "imported"), Side(repo, tmp_path / "native")),
    ):
        with pytest.raises(DiffRejected) as info:
            symbol_diff.compare(first, second)
        assert info.value.reason == "source_snapshot_unavailable"
    assert inventory(*watched) == state


def test_configuration_mismatch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git(repo)
    write_tree(repo, {"a.py": GOOD, "a.js": "function f() {}\n"})
    build(repo, tmp_path / "all")
    build(repo, tmp_path / "python", langs=["python"])
    with pytest.raises(DiffRejected) as info:
        symbol_diff.compare(Side(repo, tmp_path / "all"), Side(repo, tmp_path / "python"))
    assert info.value.reason == "configuration_mismatch"


def test_producer_mismatch_by_input_fabrication(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    write_tree(repo, {"a.py": GOOD})
    build(repo, tmp_path / "real")
    original = CodeGraph._producer_inputs

    def fabricated(self):
        inputs = original(self)
        return {**inputs, "queries_sha256": "0" * 64}

    monkeypatch.setattr(CodeGraph, "_producer_inputs", fabricated)
    build(repo, tmp_path / "other")
    monkeypatch.undo()
    with pytest.raises(DiffRejected) as info:
        symbol_diff.compare(Side(repo, tmp_path / "real"), Side(repo, tmp_path / "other"))
    assert info.value.reason == "producer_mismatch"


def test_running_grammar_versions_must_match_the_sides(tmp_path, monkeypatch):
    _repo, before, after = same_path_pair(tmp_path, {"a.py": GOOD}, {"a.py": GOOD + "\n"})
    real = symbol_diff.importlib.metadata.version
    monkeypatch.setattr(
        symbol_diff.importlib.metadata,
        "version",
        lambda name: "0.0.0" if name == "tree-sitter" else real(name),
    )
    with pytest.raises(DiffRejected) as info:
        symbol_diff.compare(before, after)
    assert info.value.reason == "producer_mismatch"


def test_schema_mismatch_epoch8_against_epoch9(tmp_path):
    fixture = json.loads(
        gzip.decompress((ROOT / "mcp-server/tests/fixtures/s17/native8.json.gz").read_bytes())
    )
    repo = Path(fixture["original_repository"])
    old = CodeGraph(repo, graph_root=tmp_path / "old8")
    for name, item in fixture["native_files"].items():
        target = old.atlas_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(item["base64"]))
    sources = {path: base64.b64decode(raw) for path, raw in fixture["source_files"].items()}
    new = CodeGraph(repo, graph_root=tmp_path / "new9")
    new.build(overlays=sources)
    old.close()
    new.close()
    with pytest.raises(DiffRejected) as info:
        symbol_diff.compare(Side(repo, tmp_path / "old8"), Side(repo, tmp_path / "new9"))
    assert info.value.reason == "schema_mismatch"


def test_rejection_closed_set_is_documented_and_complete():
    assert symbol_diff.REJECTIONS == (
        "index_unavailable",
        "source_snapshot_unavailable",
        "identity_kind_mismatch",
        "repository_identity_mismatch",
        "schema_mismatch",
        "configuration_mismatch",
        "producer_mismatch",
    )
    docs = (ROOT / "atlas/docs/symbol-diff.md").read_text()
    for reason in (*symbol_diff.REJECTIONS, *symbol_diff.UNKNOWN_REASONS):
        assert reason in docs


# ---- SCIP and fast path
def test_scip_entities_are_excluded(tmp_path):
    repo = workspace(tmp_path)
    build(repo, tmp_path / "plain")
    derived = CodeGraph(repo, graph_root=tmp_path / "scip")
    derived.build()
    derived.import_scip(INDEX, MANIFEST)
    derived.close()
    probe = CodeGraph(repo, read_only=True, graph_root=tmp_path / "scip")
    with probe.reader_lease() as reader:
        kinds = {
            json.loads(row[0])["kind"] for row in reader.db.execute("SELECT payload FROM entities")
        }
    probe.close()
    assert any(kind.startswith("scip_") for kind in kinds)
    change_set = symbol_diff.compare(Side(repo, tmp_path / "plain"), Side(repo, tmp_path / "scip"))
    assert change_set["changes"] == [] and change_set["summary"]["unchanged"] > 0
    assert not any(kind.startswith("scip_") for kind in {r["kind"] for r in change_set["changes"]})


class Spy(syntax.FileAnalysis):
    parsed: ClassVar[list[tuple[str, bytes]]] = []

    def __init__(self, language, source, entities):
        Spy.parsed.append((language, source))
        super().__init__(language, source, entities)


def test_unchanged_files_are_never_parsed(tmp_path):
    steady = "def steady(a):\n    return a\n"
    _repo, before, after = same_path_pair(
        tmp_path,
        {"steady.py": steady, "edit.py": "def e(a):\n    return a\n", "gone.py": steady},
        {"edit.py": "def e(a):\n    return a + 1\n", "gone.py": None},
    )
    Spy.parsed = []
    change_set = symbol_diff.compare(before, after, analysis_factory=Spy)
    sources = sorted(source for _language, source in Spy.parsed)
    assert steady.encode() in sources  # only gone.py (removed entity needs facts)
    assert sources.count(steady.encode()) == 1
    assert len(Spy.parsed) == 3  # edit.py twice (both sides) + gone.py once; steady.py never
    assert change_set["summary"]["unchanged"] == 1
    assert [r["name"] for r in records(change_set, "body")] == ["e"]


# ---- syntax-level fail-closed reasons
def entity(source, name, kind="function"):
    start = source.index(b"def ")
    return syntax.EntityInput("e1", kind, name, start, len(source.rstrip(b"\n")))


def test_declaration_not_found_and_parse_error_and_non_utf8():
    source = b"def f(a):\n    return a\n"
    wrong = syntax.EntityInput("e", "function", "f", 1, 5)
    assert syntax.FileAnalysis("python", source, [wrong]).placements["e"].reason == (
        "declaration_node_not_found"
    )
    broken = b"def f(:\n    return a\n"
    with pytest.raises(syntax.Unknown) as info:
        analysis = syntax.FileAnalysis("python", broken, [entity(broken, "f")])
        analysis.facts("e1")
    assert info.value.reason in {"parse_error_in_declaration", "declaration_node_not_found"}
    latin = 'def f():\n    return "é"\n'.encode("latin-1")
    with pytest.raises(syntax.Unknown) as info:
        syntax.FileAnalysis("python", latin, [entity(latin, "f")]).facts("e1")
    assert info.value.reason in {"non_utf8_declaration", "parse_error_in_declaration"}


def test_unmapped_child_fails_closed(monkeypatch):
    source = b"def f(a):\n    return a\n"
    row = syntax.ROWS["python"]["function_definition"]
    monkeypatch.setitem(
        syntax.ROWS["python"],
        "function_definition",
        syntax.Row(row.kinds, frozenset({"name", "body"}), body="field"),
    )
    with pytest.raises(syntax.Unknown) as info:
        syntax.FileAnalysis("python", source, [entity(source, "f")]).facts("e1")
    assert info.value.reason == "unmapped_declaration_child"


def test_uncovered_text_fails_closed(monkeypatch):
    source = b"def f(a):\n    return 'a  b'\n"
    monkeypatch.setitem(syntax.ATOMIC_TYPES, "python", frozenset())
    analysis = syntax.FileAnalysis("python", source, [entity(source, "f")])
    # Without the atomic literal rule the string interior is still covered by its
    # children; hide one so a gap appears and the check must refuse to guess.
    real = analysis._text_gap
    monkeypatch.setattr(
        analysis, "_text_gap", lambda start, end: True if end > start else real(start, end)
    )
    with pytest.raises(syntax.Unknown) as info:
        analysis.facts("e1")
    assert info.value.reason == "uncovered_source_text"


def test_git_helpers_available():
    assert git is not None
