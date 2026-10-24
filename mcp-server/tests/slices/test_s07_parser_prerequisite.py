"""First supporting proof: real extraction, one-predecessor closure, portability."""

from __future__ import annotations
import hashlib
import json
from pathlib import Path
import pytest
from atlas_aci.codegraph import CodeGraph
from atlas_aci.storage import evidence as ev


def make(tmp_path):
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg/__init__.py").write_text("")
    (repo / "pkg/a.py").write_text("from .b import B\ndef A():\n B()\n")
    (repo / "pkg/b.py").write_text("def B():\n pass\n")
    graph = CodeGraph(repo)
    graph.build()
    assert graph.query("callers_of:B")["edges"]
    return repo, graph


def test_first_proof_historical_target_closure_and_fresh_isolation(tmp_path: Path):
    repo, graph = make(tmp_path)
    original = graph.evidence_records()
    old_b = (repo / "pkg/b.py").read_bytes()
    old_call = next(r for r in original if r["type"] == "assertion" and r["relation"] == "call")
    (repo / "pkg/a.py").write_text("def A(:\n B()\n")
    (repo / "pkg/b.py").write_bytes(b"\n\n" + old_b)
    (repo / "pkg/c.py").write_text("from .a import A\ndef C():\n A()\n")
    stats = graph.build()
    assert stats["extraction_state"] == "partial" and stats["files_failed"] == 1
    observations = graph.observation_records()
    failed = next(r for r in observations if r["type"] == "observation" and r["path"] == "pkg/a.py")
    assert failed["state"] == "failed" and failed["reason"] == "parse_error"
    assert (
        failed["observed_source_sha256"]
        == hashlib.sha256((repo / "pkg/a.py").read_bytes()).hexdigest()
    )
    history = next(
        r for r in observations if r["type"] == "history" and r["id"] == failed["historical_id"]
    )
    assert old_call in history["records"]
    old_target = next(
        r
        for r in history["records"]
        if r["type"] == "projection" and r["table"] == "symbols" and r["row"]["name"] == "B"
    )
    old_occurrence = next(
        r
        for r in history["records"]
        if r["type"] == "occurrence" and r["id"] == old_target["links"]["occurrence_id"]
    )
    assert (
        old_occurrence["line"] == 1
        and old_occurrence["source_sha256"] == hashlib.sha256(old_b).hexdigest()
    )
    assert any(
        r["type"] == "evidence" and r["occurrence_id"] == old_occurrence["id"]
        for r in history["records"]
    )
    assert graph.search_symbol("B")["definitions"][0]["line_start"] == 3
    assert not graph.query("callers_of:A")["edges"]
    assert graph.search_symbol("A")["references"][0]["unresolved_reason"] == "stale_target"
    assert not graph.confident_edges()
    assert graph.communities()["communities"] == []
    assert graph.god_nodes()["god_nodes"] == []
    artifact = tmp_path / "partial.jsonl"
    graph.export_jsonl(artifact)
    destination = tmp_path / "no-source"
    destination.mkdir()
    imported = CodeGraph(destination)
    imported.import_jsonl(artifact)
    assert imported.observation_records() == observations
    replay = tmp_path / "replay.jsonl"
    imported.export_jsonl(replay)
    assert replay.read_bytes() == artifact.read_bytes()
    imported.import_jsonl(replay)
    assert imported.observation_records() == observations and not imported.confident_edges()
    assert not list(destination.glob("*.py"))
    imported.close()
    graph.close()


def test_first_failure_has_no_invented_history_and_recovery(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    bad = repo / "bad.py"
    bad.write_text("def bad(:\n pass\n")
    (repo / "good.py").write_text("def good():\n pass\n")
    graph = CodeGraph(repo)
    graph.build()
    first = graph.observation_records()
    failed = next(r for r in first if r["type"] == "observation" and r["path"] == "bad.py")
    assert failed["historical_id"] is None and failed["retained_record_ids"] == []
    assert not any(
        r["type"] == "occurrence" and r["path"] == "bad.py" for r in graph.evidence_records()
    )
    graph.build()
    assert graph.observation_records() == first
    bad.write_text("def bad():\n pass\n")
    graph.build()
    assert not any(r["type"] == "history" for r in graph.observation_records())
    assert all(r["state"] == "indexed" for r in graph.observation_records())
    assert graph.search_symbol("bad")["definitions"]
    graph.close()


def test_contradictory_portable_stale_observation_rejected(tmp_path: Path):
    repo, graph = make(tmp_path)
    (repo / "pkg/a.py").write_text("def broken(:\n pass\n")
    graph.build()
    artifact = tmp_path / "bad.jsonl"
    graph.export_jsonl(artifact)
    rows = [json.loads(line) for line in artifact.read_text().splitlines()]
    header = rows.pop(0)
    observed = next(r for r in rows if r["type"] == "observation" and r["historical_id"])
    observed["state"] = "indexed"
    observed["reason"] = None
    observed["location"] = None
    observed["id"] = ev.identifier(observed)
    rows.sort(key=lambda r: (r["type"], r["id"]))
    body = "".join(ev.canonical(r) + "\n" for r in rows)
    header["content_hash"] = hashlib.sha256(body.encode()).hexdigest()
    artifact.write_text(ev.canonical(header) + "\n" + body)
    generation = graph.generation_id
    before = graph.db_path.read_bytes()
    with pytest.raises(ValueError):
        graph.import_jsonl(artifact)
    assert graph.generation_id == generation and graph.db_path.read_bytes() == before
    assert not list(graph.atlas_dir.glob("*.tmp.*"))
    graph.close()


@pytest.mark.parametrize(
    "mutation", ["indexed_hash", "failed_active_facts", "missing_indexed_observation"]
)
def test_current_observations_must_match_active_source_evidence(tmp_path: Path, mutation: str):
    repo, graph = make(tmp_path)
    (repo / "pkg/a.py").write_text("def A(:\n B()\n")
    (repo / "pkg/b.py").write_text("\n\ndef B():\n pass\n")
    graph.build()
    artifact = tmp_path / "contradiction.jsonl"
    graph.export_jsonl(artifact)
    rows = [json.loads(line) for line in artifact.read_text().splitlines()]
    header = rows.pop(0)
    observation = next(r for r in rows if r["type"] == "observation" and r["path"] == "pkg/b.py")
    assert observation["state"] == "indexed"
    assert any(
        r["type"] == "evidence" and r["source_sha256"] == observation["observed_source_sha256"]
        for r in rows
    )
    if mutation == "indexed_hash":
        observation["observed_source_sha256"] = "0" * 64
    elif mutation == "failed_active_facts":
        observation.update(
            state="failed",
            reason="parse_error",
            location={"line": 1, "column": 0, "end_line": 1, "end_column": 1},
        )
    else:
        rows.remove(observation)
    observation["id"] = ev.identifier(observation)
    rows.sort(key=lambda r: (r["type"], r["id"]))
    body = "".join(ev.canonical(r) + "\n" for r in rows)
    header.update(content_hash=hashlib.sha256(body.encode()).hexdigest(), record_count=len(rows))
    artifact.write_text(ev.canonical(header) + "\n" + body)
    before = graph.db_path.read_bytes()
    generation = graph.generation_id
    with pytest.raises(ValueError):
        graph.import_jsonl(artifact)
    assert graph.db_path.read_bytes() == before and graph.generation_id == generation
    assert not list(graph.atlas_dir.glob("*.tmp.*"))
    graph.close()


def test_explicit_v1_adapter_keeps_observation_verification_unknown(tmp_path: Path):
    _repo, graph = make(tmp_path)
    artifact = tmp_path / "v1.jsonl"
    graph.export_jsonl(artifact)
    rows = [json.loads(line) for line in artifact.read_text().splitlines()]
    header = rows.pop(0)
    rows = [r for r in rows if r["type"] not in {"observation", "history"}]
    body = "".join(ev.canonical(r) + "\n" for r in rows)
    header.update(
        format=ev.FORMAT,
        content_hash=hashlib.sha256(body.encode()).hexdigest(),
        record_count=len(rows),
    )
    artifact.write_text(ev.canonical(header) + "\n" + body)
    destination = tmp_path / "v1-source-free"
    destination.mkdir()
    imported = CodeGraph(destination)
    imported.import_jsonl(artifact)
    assert imported.observation_records() == []
    assert imported.query("callers_of:B")["edges"]
    assert imported._generation_data(imported.db)[0]["source_provenance"] == "unavailable"
    imported.close()
    graph.close()
