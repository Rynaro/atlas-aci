"""Rust native grammar boundaries and existing cache/reader compatibility."""

import pytest
from tree_sitter_language_pack import get_parser

from atlas_aci.codegraph import CodeGraph


@pytest.mark.parametrize(
    "before,after",
    [
        ('r#" "#', 'r#""#'),
        ('" "', '""'),
        ('b" "', 'b""'),
        ('br#" "#', 'br#""#'),
        ('c" "', 'c""'),
        ("' '", "'\\t'"),
    ],
)
def test_impl_literal_content_is_identity(tmp_path, before, after):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "lib.rs"
    template = "struct W<const N: usize>;\nimpl W<{{ {literal}.len() }}> {{ fn close() {{}} }}\n"
    original = template.format(literal=before).encode()
    assert not get_parser("rust").parse(original).root_node.has_error
    source.write_bytes(original)
    graph = CodeGraph(repo)
    graph.build()
    old = next(
        r for r in graph.evidence_records() if r["type"] == "entity" and r["name"] == "close"
    )
    changed = template.format(literal=after).encode()
    assert not get_parser("rust").parse(changed).root_node.has_error
    source.write_bytes(changed)
    graph.build(since="HEAD")
    new = next(
        r for r in graph.evidence_records() if r["type"] == "entity" and r["name"] == "close"
    )
    assert new["id"] != old["id"]
    assert new["owners"] != old["owners"]
    assert before in next(o["name"] for o in old["owners"] if o["kind"] == "impl")
    assert after in next(o["name"] for o in new["owners"] if o["kind"] == "impl")
    # Outside trivia is ignored; a literal's own whitespace is not trivia.
    source.write_bytes(b"\n/* outside */\n" + changed.replace(b"impl W", b"impl /* header */ W"))
    graph.build(since="HEAD")
    again = next(
        r for r in graph.evidence_records() if r["type"] == "entity" and r["name"] == "close"
    )
    assert again["id"] == new["id"]
    graph.close()


@pytest.mark.parametrize("owner", ["struct A; impl A", "trait A"])
def test_associated_const_nested_function_is_free(tmp_path, owner):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = (owner + " { const X: () = { fn nested() {} }; }\n").encode()
    assert not get_parser("rust").parse(source).root_node.has_error
    (repo / "lib.rs").write_bytes(source)
    graph = CodeGraph(repo)
    graph.build()
    entity = next(
        r for r in graph.evidence_records() if r["type"] == "entity" and r["name"] == "nested"
    )
    assert entity["kind"] == "function"
    graph.close()


def test_direct_method_and_foreign_signature_classification(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = b"""struct A;
impl A { fn direct(&self) { fn nested() {} } }
trait T { fn signature(&self); fn defaulted(&self) {} }
unsafe extern "C" { fn external(); }
"""
    assert not get_parser("rust").parse(source).root_node.has_error
    (repo / "lib.rs").write_bytes(source)
    graph = CodeGraph(repo)
    graph.build()
    entities = [
        r for r in graph.evidence_records() if r["type"] == "entity" and r["kind"] != "file"
    ]
    kinds = {r["name"]: r["kind"] for r in entities}
    assert {
        name: kinds[name] for name in ("direct", "signature", "defaulted", "nested", "external")
    } == {
        "direct": "method",
        "signature": "method",
        "defaulted": "method",
        "nested": "function",
        "external": "function",
    }
    records = graph.evidence_records()
    for entity in entities:
        assert (
            sum(
                r["type"] == "occurrence"
                and r.get("entity_id") == entity["id"]
                and r.get("kind") == "definition"
                for r in records
            )
            == 1
        )
    graph.close()


def test_capability_versions_and_disabled_generation(tmp_path):
    import json
    from pathlib import Path

    from test_s15_contract import workspace

    root = Path(__file__).resolve().parents[3]
    historical = json.loads(
        (root / ".spectra/evidence/aci-s15/qualification/historical-capabilities.json").read_text()
    )
    assert {row["capabilities"]["format"] for row in historical} == {
        "atlas-capabilities.v1",
        "atlas-capabilities.v2",
    }
    for row in historical:
        assert CodeGraph._valid_capabilities(
            row["capabilities"], row["producer"], row["configuration"]
        )
        rust = next(item for item in row["capabilities"]["languages"] if item["language"] == "rust")
        assert rust["extraction"] == {"state": "unsupported", "reason": "no_extraction_query"}
        assert rust["definitions"] == rust["relations"] == []
    graph = CodeGraph(workspace(tmp_path), langs=["python"])
    graph.build()
    payload = graph.status()
    runtime = next(r for r in payload["runtime"]["languages"] if r["language"] == "rust")
    indexed = next(
        r for r in payload["indexed"]["capabilities"]["languages"] if r["language"] == "rust"
    )
    assert runtime["extraction"]["state"] == "partial"
    assert indexed["extraction"] == {"state": "unsupported", "reason": "language_disabled"}
    assert all(row["state"] == "disabled" for row in graph.observation_records())
    graph.close()


@pytest.mark.parametrize(
    "mutation",
    ["missing_limit", "extra_limit", "semantic", "construct", "class_kind", "old_producer"],
)
def test_rust_capabilities_are_closed_and_truthful(tmp_path, mutation):
    import copy
    import json
    from pathlib import Path

    from test_s15_contract import workspace

    graph = CodeGraph(workspace(tmp_path))
    base = graph._build_inputs()
    caps = graph._capabilities(base["configuration"]["languages"])
    assert CodeGraph._valid_capabilities(caps, base["producer"], base["configuration"])
    rust = next(r for r in caps["languages"] if r["language"] == "rust")
    if mutation == "missing_limit":
        del caps["limitations"]["rust_macro_expansion"]
    elif mutation == "extra_limit":
        caps["limitations"]["fictional"] = "yes"
    elif mutation == "semantic":
        rust["semantic_binding"] = {"state": "complete", "reason": "compiler"}
    elif mutation == "construct":
        rust["relations"] = ["call", "construct"]
    elif mutation == "class_kind":
        rust["definitions"] = sorted([*rust["definitions"], "class"])
    else:
        root = Path(__file__).resolve().parents[3]
        old = json.loads(
            (
                root / ".spectra/evidence/aci-s15/qualification/historical-capabilities.json"
            ).read_text()
        )[1]
        base = copy.deepcopy(base)
        base["producer"] = old["producer"]
        caps["producer"] = old["producer"]
    assert not CodeGraph._valid_capabilities(caps, base["producer"], base["configuration"])
    graph.close()


def test_rust_same_stat_and_same_language_binding_reuse(tmp_path, monkeypatch):
    import os

    from test_s09_contract import counted

    counts = counted(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    a = repo / "a.rs"
    a.write_text("fn first() { missing_a(); }\n")
    (repo / "b.rs").write_text("fn second() { missing_b(); }\n")
    graph = CodeGraph(repo)
    graph.build()
    before = a.stat()
    a.write_text("fn first() { missing_c(); }\n")
    os.utime(a, ns=(before.st_atime_ns, before.st_mtime_ns))
    called = []
    real = CodeGraph._resolve_binding_unit

    def unit(self, conn, path, *args):
        called.append(path)
        return real(self, conn, path, *args)

    monkeypatch.setattr(CodeGraph, "_resolve_binding_unit", unit)
    counts.update(parser=0, extract=0)
    stats = graph.build(since="HEAD")
    assert counts == {"parser": 1, "extract": 1}
    assert called == ["a.rs"]
    assert stats["binding_resolution"]["reused_paths"] == ["b.rs"]
    first = tmp_path / "incremental.jsonl"
    graph.export_jsonl(first)
    graph.build()
    second = tmp_path / "full.jsonl"
    graph.export_jsonl(second)
    assert first.read_bytes() == second.read_bytes()
    graph.close()


def test_rust_failed_retry_recovery_and_parser_failure(tmp_path, monkeypatch):
    import tree_sitter_language_pack as pack
    from test_s09_contract import counted

    from atlas_aci.storage import evidence as ev

    counts = counted(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "lib.rs"
    source.write_text("fn good() {}\n")
    graph = CodeGraph(repo)
    graph.build()
    source.write_text("fn good(\n")
    counts.update(parser=0, extract=0)
    stats = graph.build(since="HEAD")
    assert counts == {"parser": 1, "extract": 0}
    assert stats["extraction_state"] == "partial" and stats["files_failed"] == 1
    assert not graph.search_symbol("good")["definitions"]
    assert graph.search_symbol("good")["historical_definitions"]
    counts.update(parser=0, extract=0)
    graph.build(since="HEAD")
    assert counts == {"parser": 1, "extract": 0}
    source.write_text("fn good() {}\n")
    graph.build(since="HEAD")
    assert graph.search_symbol("good")["definitions"]
    assert not graph.search_symbol("good").get("historical_definitions")
    original_bytes = graph.db_path.read_bytes()
    original_records = graph.evidence_records()
    source.write_text("fn good() {}\nfn newer() {}\n")
    real_get = pack.get_parser

    class Broken:
        def __init__(self, parser):
            self.parser = parser

        def parse(self, source):
            self.parser.parse(source)
            raise RuntimeError("controlled Rust parser exception after native parse")

    with graph.reader_lease() as held:
        with monkeypatch.context() as patch:
            patch.setattr(pack, "get_parser", lambda language: Broken(real_get(language)))
            with pytest.raises(RuntimeError, match="Cannot index eligible source"):
                graph.build(since="HEAD")
        assert graph.db_path.read_bytes() == original_bytes
        assert list(ev.records(held.db)) == original_records
        assert not list(graph.atlas_dir.glob(".graph.*.tmp.*"))
        graph.build(since="HEAD")
        assert list(ev.records(held.db)) == original_records
    assert graph.search_symbol("newer")["definitions"]
    graph.close()


def test_rust_portable_readonly_and_path_bound_cache(tmp_path, monkeypatch):
    from atlas_aci.indexing import extraction_cache as xc

    repo = tmp_path / "repo"
    (repo / "one/src").mkdir(parents=True)
    (repo / "two/src").mkdir(parents=True)
    for path in (repo / "one/src/lib.rs", repo / "two/src/lib.rs"):
        path.write_text("pub fn identical() {}\n")
    graph = CodeGraph(repo, graph_root=tmp_path / "graphs")
    graph.build()
    definitions = graph.search_symbol("identical")["definitions"]
    assert len(definitions) == 2 and len({row["entity_id"] for row in definitions}) == 2
    assert not (repo / ".atlas").exists()
    source_before = {str(p.relative_to(repo)): p.read_bytes() for p in repo.rglob("*.rs")}
    cache = graph.atlas_dir / "extraction-cache"
    cache_before = {
        str(p.relative_to(cache)): (p.stat().st_mtime_ns, p.read_bytes())
        for p in cache.rglob("*.json")
    }
    artifact = tmp_path / "export.jsonl"
    graph.export_jsonl(artifact)
    destination = tmp_path / "source-free"
    destination.mkdir()
    imported = CodeGraph(destination)
    imported.import_jsonl(artifact)
    assert imported.evidence_records() == graph.evidence_records()
    replay = tmp_path / "replay.jsonl"
    imported.export_jsonl(replay)
    assert replay.read_bytes() == artifact.read_bytes()
    assert imported.status()["completeness"]["state"] == "unknown"

    def forbidden(*args):
        raise AssertionError("reader used extraction cache")

    with monkeypatch.context() as patch:
        patch.setattr(xc.ExtractionCache, "lookup", forbidden)
        patch.setattr(xc.ExtractionCache, "write", forbidden)
        reader = CodeGraph(repo, graph_root=tmp_path / "graphs", read_only=True)
        with reader.reader_lease() as lease:
            assert len(lease.search_symbol("identical")["definitions"]) == 2
            lease.status()
            lease.export_jsonl(tmp_path / "readonly.jsonl")
        reader.close()
    assert {str(p.relative_to(repo)): p.read_bytes() for p in repo.rglob("*.rs")} == source_before
    assert {
        str(p.relative_to(cache)): (p.stat().st_mtime_ns, p.read_bytes())
        for p in cache.rglob("*.json")
    } == cache_before
    imported.close()
    graph.close()


def test_current_and_historical_producer_module_sets(tmp_path):
    import copy

    from atlas_aci.storage import evidence as ev

    graph = CodeGraph(tmp_path)
    current = graph._producer_inputs()
    assert set(current["modules"]) == {
        "storage/evidence.py",
        "providers/python_imports.py",
        "indexing/extraction_cache.py",
        "resolvers/dependencies.py",
        "extractors/rust.py",
    }
    for count in (2, 3, 4, 5):
        old = copy.deepcopy(current)
        old["modules"] = {key: current["modules"][key] for key in list(current["modules"])[:count]}
        ev.validate_record(ev.producer(old))
    current["modules"]["arbitrary.py"] = "1" * 64
    with pytest.raises(ValueError, match="module set"):
        ev.validate_record(ev.producer(current))
    graph.close()


def test_rust_forged_extension_snapshot_is_unknown(tmp_path):
    import json
    import sqlite3

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "lib.rs").write_text("fn main() {}\n")
    graph = CodeGraph(repo)
    graph.build()
    database = graph.db_path
    graph.close()
    with sqlite3.connect(database) as conn:
        coverage = json.loads(
            conn.execute("SELECT value FROM manifest WHERE key='coverage_snapshot'").fetchone()[0]
        )
        rust = next(
            row for row in coverage["capabilities"]["languages"] if row["language"] == "rust"
        )
        rust["extensions"] = [".py"]
        conn.execute(
            "UPDATE manifest SET value=? WHERE key='coverage_snapshot'", (json.dumps(coverage),)
        )
    reader = CodeGraph(repo, read_only=True)
    try:
        result = reader.status()
        assert result["indexed"]["capabilities"] is None
        assert result["completeness"]["state"] == "unknown"
    finally:
        reader.close()
