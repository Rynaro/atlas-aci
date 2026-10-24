"""Independent old-byte oracle and real compact source closure controls."""

import gzip
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from atlas_aci.codegraph import CodeGraph
from atlas_aci.storage import evidence as ev
from atlas_aci.storage import snapshots as snap

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = ROOT / ".spectra/evidence/aci-s08/lease-footprint"


def old_oracle(tmp_path):
    raw = gzip.decompress(
        (
            ROOT
            / ".spectra/evidence/aci-s08/footprint-baseline/runtime-source"
            / "storage/snapshots.py.txt.gz"
        ).read_bytes()
    )
    assert (
        hashlib.sha256(raw).hexdigest()
        == "bd77fa8ce6114f744a733fb2644f1b406cfee5b053b8d1e8655dae7040dfb479"
    )
    path = tmp_path / "frozen_old_snapshot.py"
    path.write_bytes(raw)
    spec = importlib.util.spec_from_file_location("frozen_old_snapshot", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_compact_manifest_rejects_graph_contradictions(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_bytes(b"def target():\n pass\ndef caller():\n target()\n")
    graph = CodeGraph(repo)
    graph.build()
    try:
        with graph.reader_lease() as reader:
            manifest = reader._source_lease.manifest
            assert manifest["format"] == "atlas-source-snapshot.v2"
            assert "ranges" not in manifest
            for field in ("total", "occurrence", "evidence", "legacy"):
                forged = json.loads(json.dumps(manifest))
                forged["range_proof"][field] = True
                with pytest.raises(ValueError):
                    snap.validate_manifest(forged)
    finally:
        graph.close()


def restore_v1(tmp_path):
    import base64

    fixture = json.loads(gzip.decompress((EVIDENCE / "v1-fixture.json.gz").read_bytes()))
    graph = CodeGraph(Path(fixture["repository"]), graph_root=tmp_path / "external", read_only=True)
    graph.db_path.parent.mkdir(parents=True)
    for item in fixture["files"]:
        if not item["path"].startswith(".atlas/"):
            continue
        raw = base64.b64decode(item["base64"])
        assert len(raw) == item["bytes"] and hashlib.sha256(raw).hexdigest() == item["sha256"]
        target = graph.atlas_dir / item["path"].removeprefix(".atlas/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    return graph, fixture


def test_manifest_version_integrity_and_lifetime(tmp_path):
    import os

    graph, fixture = restore_v1(tmp_path)
    try:
        with graph.reader_lease() as first, graph.reader_lease() as second:
            assert first.snapshot_id == fixture["expected"]["snapshot_id"]
            assert first._source_lease.manifest["format"] == "atlas-source-snapshot.v1"
            first_fd, second_fd = first._source_lease.fd, second._source_lease.fd
            assert first.read_source("a.py") == b"def broken(:\n pass\n"
            historical = next(
                r for r in fixture["expected"]["observations"] if r["type"] == "history"
            )
            old_hash = next(
                r["source_sha256"] for r in historical["records"] if r["type"] == "occurrence"
            )
            assert b"def target" in first.read_source("a.py", old_hash)
            first.close()
            with pytest.raises(OSError):
                os.fstat(first_fd)
            assert os.fstat(second_fd)
            value = json.loads(json.dumps(second._source_lease.manifest))
            value["ranges"][0]["end"] = 10**9
            with pytest.raises(ValueError):
                snap.validate_manifest(value)
        with pytest.raises(OSError):
            os.fstat(second_fd)
    finally:
        graph.close()


def test_streaming_commitment_equivalence(tmp_path):
    import random
    from collections import Counter

    fixture = json.loads(gzip.decompress((EVIDENCE / "v1-fixture.json.gz").read_bytes()))
    expected = fixture["expected"]
    old = old_oracle(tmp_path)
    reference = old.closure(expected["records"], expected["observations"])
    range_bytes = ev.canonical(reference["ranges"]).encode()
    counts = Counter(row["kind"] for row in reference["ranges"])
    independent_proof = {
        "version": 1,
        "algorithm": "sha256-canonical-json-array-v1",
        "sha256": hashlib.sha256(b"atlas-source-ranges.v2\x00" + range_bytes).hexdigest(),
        "total": len(reference["ranges"]),
        "occurrence": counts["occurrence"],
        "evidence": counts["evidence"],
        "legacy": sum(r["provenance"] == "legacy_unavailable" for r in reference["ranges"]),
    }
    for seed in range(5):
        records = expected["records"] + expected["records"]
        random.Random(seed).shuffle(records)
        actual = snap.closure(records, expected["observations"])
        assert actual["graph_digest"] == reference["graph_digest"]
        assert actual["observation_digest"] == reference["observation_digest"]
        assert actual["range_proof"] == independent_proof
        for row in actual["source_bounds"]:
            matching = [
                r
                for r in reference["ranges"]
                if (r["path"], r["sha256"]) == (row["path"], row["sha256"])
            ]
            assert row["range_count"] == len(matching)
            assert row["min_start"] == min((r["start"] for r in matching), default=0)
            assert row["max_end"] == max((r["end"] for r in matching), default=0)
    empty = snap.closure([], [])
    assert empty["graph_digest"] == hashlib.sha256(b"[]").hexdigest()
    assert empty["range_proof"]["total"] == 0
    conflict = dict(expected["records"][0])
    conflict["unexpected_conflict"] = True
    with pytest.raises(ValueError, match="conflicting graph ID"):
        snap.closure(expected["records"] + [conflict], expected["observations"])
    conflict = dict(expected["records"][0])
    conflict["type"] = "producer"
    with pytest.raises(ValueError, match="conflicting graph ID"):
        snap.closure(expected["records"] + [conflict], expected["observations"])


def test_validation_scope_is_one_lease(tmp_path, monkeypatch):
    import sqlite3

    from atlas_aci.enforcement import ToolError

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_bytes(b"def target():\n pass\n")
    graph = CodeGraph(repo)
    graph.build()
    original = CodeGraph._generation_data
    calls = []

    def observed(self, *args, **kwargs):
        calls.append(id(self))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(CodeGraph, "_generation_data", observed)
    try:
        with graph.reader_lease() as first:
            assert first.epoch_ok() and first.epoch_ok()
            assert first.search_symbol("target")["definitions"]
            assert len(calls) == 1
        assert not first.epoch_ok()
        with graph.reader_lease() as second:
            assert second.epoch_ok()
            assert len(calls) == 2 and calls[0] != calls[1]
        # New request must validate the actual new handle, not a token cache.
        with sqlite3.connect(graph.db_path) as conn:
            conn.execute("UPDATE manifest SET value='{}' WHERE key='source_observations'")
        with pytest.raises(ToolError), graph.reader_lease():
            pytest.fail("prior request validation leaked across requests")
        assert len(calls) == 3
    finally:
        graph.close()


def install_manifest(graph, manifest):
    import sqlite3

    raw = ev.canonical(manifest).encode()
    token = hashlib.sha256(raw).hexdigest()
    (graph.atlas_dir / "source-snapshots" / ("manifest." + token + ".json")).write_bytes(raw)
    with sqlite3.connect(graph.db_path) as conn:
        inputs = json.loads(
            conn.execute("SELECT value FROM manifest WHERE key='generation_inputs'").fetchone()[0]
        )
        inputs["snapshot"] = {"state": "available", "id": token, "manifest_sha256": token}
        conn.execute(
            "UPDATE manifest SET value=? WHERE key='generation_inputs'", (ev.canonical(inputs),)
        )
        conn.execute(
            "UPDATE manifest SET value=? WHERE key='generation_id'", (snap.digest(inputs),)
        )


@pytest.mark.parametrize(
    "mutation", ["commitment", "counts", "bounds", "foreign", "raw_occurrence", "raw_evidence"]
)
def test_rehashed_compact_or_raw_range_contradiction(tmp_path, mutation):
    import sqlite3

    from atlas_aci.enforcement import ToolError

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_bytes(b"def target():\n pass\ndef caller():\n target()\n")
    graph = CodeGraph(repo)
    graph.build()
    try:
        with graph.reader_lease() as reader:
            manifest = json.loads(json.dumps(reader._source_lease.manifest))
        if mutation.startswith("raw_"):
            table = "occurrences" if mutation == "raw_occurrence" else "evidence"
            with sqlite3.connect(graph.db_path) as conn:
                identifier, raw = conn.execute(
                    "SELECT id,payload FROM " + table + " LIMIT 1"
                ).fetchone()
                value = json.loads(raw)
                value["end_byte"] = value["source_size"] + 1
                conn.execute(
                    "UPDATE " + table + " SET payload=? WHERE id=?",
                    (ev.canonical(value), identifier),
                )
        else:
            if mutation == "commitment":
                manifest["range_proof"]["sha256"] = "0" * 64
            elif mutation == "counts":
                manifest["range_proof"]["total"] += 2
                manifest["range_proof"]["occurrence"] += 1
                manifest["range_proof"]["evidence"] += 1
                manifest["bindings"][0]["range_count"] += 2
            elif mutation == "bounds":
                manifest["bindings"][0]["min_start"] = 1
            else:
                manifest["current"][0]["path"] = "foreign.py"
                manifest["bindings"][0]["path"] = "foreign.py"
            snap.validate_manifest(manifest)  # Coherent outer structure must be insufficient.
            install_manifest(graph, manifest)
        with pytest.raises(ToolError), graph.reader_lease():
            pytest.fail("contradictory own graph exposed")
    finally:
        graph.close()


def test_forged_portable_projection_still_rejects_and_preserves_generation(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_bytes(b"def target():\n pass\ndef caller():\n target()\n")
    graph = CodeGraph(repo)
    graph.build()
    path = tmp_path / "graph.jsonl"
    graph.export_jsonl(path)
    before = graph.db_path.read_bytes()
    values = [json.loads(line) for line in path.read_text().splitlines()]
    header, rows = values[0], values[1:]
    changed = 0
    for index, row in enumerate(rows):
        if row["type"] == "projection" and row["table"] == "edges":
            data = dict(row["row"])
            data["target_name"] = "forged"
            rows[index] = ev.record("projection", table="edges", row=data, links=row["links"])
            changed += 1
    assert changed == 1
    body = "".join(
        ev.canonical(row) + "\n" for row in sorted(rows, key=lambda r: (r["type"], r["id"]))
    )
    header["content_hash"] = hashlib.sha256(body.encode()).hexdigest()
    header["record_count"] = len(rows)
    path.write_text(ev.canonical(header) + "\n" + body)
    try:
        with pytest.raises(ValueError):
            graph.import_jsonl(path)
        assert graph.db_path.read_bytes() == before
        assert not list(graph.atlas_dir.glob(".graph.*.tmp.*"))
        assert not list(graph.atlas_dir.glob(".sources-stage.*"))
    finally:
        graph.close()


def test_genuine_legacy_unknown_ranges_match_old_byte_oracle(tmp_path):
    repo = tmp_path / "legacy-destination"
    repo.mkdir()
    graph = CodeGraph(repo)
    graph.import_jsonl(ROOT / "mcp-server/tests/fixtures/s07/epoch6.jsonl")
    old = old_oracle(tmp_path)
    try:
        with graph.reader_lease() as reader:
            assert reader.snapshot_id is None
            records = list(ev.records(reader.db))
            observations = reader._observation_records(reader.db)
            reference = old.closure(records, observations)
            actual = snap.closure(records, observations)
            assert reference["ranges"]
            assert all(row["provenance"] == "legacy_unavailable" for row in reference["ranges"])
            assert actual["range_proof"]["legacy"] == len(reference["ranges"])
            assert actual["source_bounds"] == []
            assert actual["graph_digest"] == reference["graph_digest"]
            assert actual["observation_digest"] == reference["observation_digest"]
            assert (
                actual["range_proof"]["sha256"]
                == hashlib.sha256(
                    b"atlas-source-ranges.v2\x00" + ev.canonical(reference["ranges"]).encode()
                ).hexdigest()
            )
    finally:
        graph.close()
