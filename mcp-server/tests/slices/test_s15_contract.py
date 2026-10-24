"""Labeled Rust syntax navigation; no compiler/macro/dispatch claim."""

import json
import shutil
from pathlib import Path

from atlas_aci.codegraph import CodeGraph

FIXTURE = Path(__file__).parents[1] / "fixtures/rust_workspace"


def workspace(tmp_path):
    repo = tmp_path / "workspace"
    shutil.copytree(FIXTURE, repo)
    return repo


def test_rust_declarations(tmp_path):
    graph = CodeGraph(workspace(tmp_path))
    graph.build()
    actual = [
        (r["path"], r["name"], r["kind"])
        for r in graph.evidence_records()
        if r["type"] == "entity" and r["kind"] != "file"
    ]
    expected = [tuple(row) for row in json.loads((FIXTURE / "declarations.json").read_text())]
    assert sorted(actual) == sorted(expected)
    assert not graph.search_symbol("generated_only")["definitions"]
    graph.close()


def test_impl_ownership(tmp_path):
    graph = CodeGraph(workspace(tmp_path))
    graph.build()
    records = graph.evidence_records()
    entities = [r for r in records if r["type"] == "entity" and r["kind"] != "file"]
    for entity in entities:
        definitions = [
            r
            for r in records
            if r["type"] == "occurrence"
            and r["kind"] == "definition"
            and r["entity_id"] == entity["id"]
        ]
        assert len(definitions) == 1
    implementations = {
        next(o["name"] for o in e["owners"] if o["kind"] == "impl")
        for e in entities
        if e["name"] == "execute" and any(o["kind"] == "impl" for o in e["owners"])
    }
    assert implementations == {
        "impl Alpha",
        "impl Beta",
        "impl Act for Alpha",
        "impl < T > Wrapper < T > where T : Clone",
        "impl Other",
    }
    repeats = [e for e in entities if e["name"] == "repeat"]
    assert len(repeats) == 2 and len({e["id"] for e in repeats}) == 2
    assert {next(o["ordinal"] for o in e["owners"] if o["kind"] == "impl") for e in repeats} == {
        1,
        2,
    }
    nested = next(e for e in entities if e["name"] == "nested")
    assert nested["kind"] == "function" and any(
        o["kind"] == "method" and o["name"] == "execute" for o in nested["owners"]
    )
    assert not any(e["kind"] == "impl" for e in entities)
    before_ids = {e["id"] for e in entities}
    source = graph.repo / "alpha/src/lib.rs"
    before = source.read_bytes()
    source.write_bytes(
        b"// leading whitespace and comment\n\n"
        + before.replace(b"impl Alpha {", b"impl /* owner comment */ Alpha {", 1).replace(
            b"where T: Clone", b"where /* bound comment */ T: Clone"
        )
    )
    graph.build(since="HEAD")
    after = graph.evidence_records()
    assert {e["id"] for e in after if e["type"] == "entity" and e["kind"] != "file"} == before_ids
    import hashlib

    assert all(
        o["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
        for o in after
        if o["type"] == "occurrence" and o["path"] == "alpha/src/lib.rs"
    )
    assert {
        o["id"] for o in records if o["type"] == "occurrence" and o["path"] == "alpha/src/lib.rs"
    }.isdisjoint(
        {o["id"] for o in after if o["type"] == "occurrence" and o["path"] == "alpha/src/lib.rs"}
    )
    graph.close()


def test_rust_call_occurrence(tmp_path):
    import hashlib
    from collections import Counter

    graph = CodeGraph(workspace(tmp_path))
    graph.build()
    records = graph.evidence_records()
    by_id = {r["id"]: r for r in records}
    calls = [r for r in records if r["type"] == "assertion" and r["relation"] == "call"]
    assert Counter(r["raw_target"] for r in calls) == Counter(
        {
            "free": 3,
            "nested": 1,
            "self.execute": 1,
            "Alpha::associated": 1,
            "obj.execute": 1,
            "generic::<u8>": 1,
            "(factory())": 1,
            "factory": 1,
            "super::free": 1,
        }
    )
    for call in calls:
        proof = by_id[call["evidence_id"]]
        occurrence = by_id[proof["occurrence_id"]]
        source = (graph.repo / occurrence["path"]).read_bytes()
        assert (
            source[occurrence["start_byte"] : occurrence["end_byte"]].decode() == call["raw_target"]
        )
        assert (
            proof["source_sha256"]
            == occurrence["source_sha256"]
            == hashlib.sha256(source).hexdigest()
        )
        assert by_id[call["source_entity_id"]]["kind"] in {"function", "method"}
    with graph.reader_lease() as lease:
        refs = {row["callee_name"]: dict(row) for row in lease.db.execute("SELECT * FROM refs")}
    assert refs["Alpha::associated"]["qualifier_name"] == "Alpha"
    assert refs["obj.execute"]["qualifier_name"] == "obj"
    assert refs["super::free"]["qualifier_name"] == "super"
    assert "parenthesized_expression" in json.loads(refs["(factory())"]["binding"])["identity"]
    graph.close()


def test_macro_gap(tmp_path):
    graph = CodeGraph(workspace(tmp_path))
    graph.build()
    payload = graph.status()
    runtime = payload["runtime"]
    assert runtime["format"] == "atlas-capabilities.v3"
    assert (
        runtime["limitations"]["rust_macro_expansion"]
        == "unsupported_including_derive_and_proc_macros"
    )
    assert runtime["limitations"]["rust_generated_code"] == "unmaterialized_outputs_unavailable"
    assert runtime["limitations"]["rust_cfg"] == "build_configuration_not_evaluated"
    assert runtime["limitations"]["rust_dispatch"] == "unresolved_without_type_or_trait_inference"
    language = next(row for row in runtime["languages"] if row["language"] == "rust")
    assert language["extraction"]["state"] == "partial"
    assert language["definitions"] == ["enum", "function", "method", "module", "struct", "trait"]
    assert (
        language["relations"] == ["call"] and language["semantic_binding"]["state"] == "unsupported"
    )
    assert not graph.search_symbol("generated_only")["definitions"]
    assert graph.search_symbol("cfg_observed")["definitions"]
    (graph.repo / "alpha/src/generated.rs").write_text("pub fn already_on_disk() {}\n")
    graph.build(since="HEAD")
    assert graph.search_symbol("already_on_disk")["definitions"]
    graph.close()


def test_no_trait_guess(tmp_path):
    graph = CodeGraph(workspace(tmp_path))
    (graph.repo / "other.py").write_text("def execute(): pass\ndef free(): pass\n")
    graph.build()
    assert not graph.confident_edges()
    assert not graph.query("callers_of:execute")["edges"]
    assert not graph.query("callers_of:free")["edges"]
    calls = [
        r for r in graph.evidence_records() if r["type"] == "assertion" and r["relation"] == "call"
    ]
    assert calls
    for call in calls:
        assert call["resolution"] == "UNRESOLVED"
        assert call["target_entity_id"] is None and call["candidate_ids"] == []
        assert call["unresolved_reason"] == "rust_semantic_binding_unavailable"
    assert graph.communities()["communities"] == []
    graph.close()


def test_rust_replay(tmp_path, monkeypatch):
    from test_s09_contract import counted

    counts = counted(monkeypatch)
    repo = workspace(tmp_path)
    graph = CodeGraph(repo)
    graph.build()
    assert counts == {"parser": 3, "extract": 3}
    before = graph.evidence_records(), graph.observation_records()
    artifact = tmp_path / "first.jsonl"
    graph.export_jsonl(artifact)
    graph.close()
    counts.update(parser=0, extract=0)
    writer = CodeGraph(repo)
    stats = writer.build()
    assert counts == {"parser": 0, "extract": 0}
    assert stats["extraction_cache"]["disk_hits"] == 3
    assert (writer.evidence_records(), writer.observation_records()) == before
    replay = tmp_path / "replay.jsonl"
    writer.export_jsonl(replay)
    assert replay.read_bytes() == artifact.read_bytes()
    stats = writer.build(since="HEAD")
    assert stats["binding_resolution"]["reused_units"] == 3
    assert stats["binding_resolution"]["mode"] == "affected"
    writer.export_jsonl(replay)
    assert replay.read_bytes() == artifact.read_bytes()
    for entry in (writer.atlas_dir / "extraction-cache/v1").glob("*/*.json"):
        payload = json.loads(entry.read_text())["packet"]
        assert (
            payload["assignments"]
            == payload["rationale"]
            == payload["imports"]
            == payload["bindings"]
            == []
        )
        assert not any(record["type"] == "assertion" for record in payload["records"])
        entry.unlink()
    counts.update(parser=0, extract=0)
    writer.build()
    assert counts == {"parser": 3, "extract": 3}
    writer.export_jsonl(replay)
    assert replay.read_bytes() == artifact.read_bytes()
    writer.close()
