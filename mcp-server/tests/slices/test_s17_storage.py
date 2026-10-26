"""Genuine old8 leases, native9 staged derivative and source-free portable7 import."""

import base64
import gzip
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from atlas_aci.codegraph import SCHEMA_EPOCH, CodeGraph
from atlas_aci.enforcement import ToolError
from atlas_aci.indexing import retrieval as ret

ROOT = Path(__file__).resolve().parents[3]


def old8(tmp_path):
    fixture = json.loads(
        gzip.decompress((ROOT / "mcp-server/tests/fixtures/s17/native8.json.gz").read_bytes())
    )
    graph = CodeGraph(Path(fixture["original_repository"]), graph_root=tmp_path / "graphs")
    for name, item in fixture["native_files"].items():
        raw = base64.b64decode(item["base64"])
        assert hashlib.sha256(raw).hexdigest() == item["sha256"]
        path = graph.atlas_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    return graph, fixture


def test_genuine_held8_upgrade9_overlay_and_replay(tmp_path):
    graph, fixture = old8(tmp_path)
    replacement = b"def B_new():\n pass\n"
    try:
        with graph.reader_lease() as held:
            assert held._lease_schema_epoch == 8 and held.generation_id == fixture["generation_id"]
            old_db = held.db
            old_source = held._source_lease
            old_fd = old_source.fd
            old_history = held.read_source(
                "history.py",
                hashlib.sha256(
                    base64.b64decode(fixture["historical_source"]["history.py"])
                ).hexdigest(),
            )
            graph.build(overlays={"b.py": replacement})
            assert SCHEMA_EPOCH == 9 and graph.db_path.name == "graph.9.db"
            assert not (graph.atlas_dir / "graph.8.db").exists()
            assert (
                old_db.execute("SELECT value FROM manifest WHERE key='generation_id'").fetchone()[0]
                == fixture["generation_id"]
            )
            assert held.read_source("b.py") == base64.b64decode(fixture["source_files"]["b.py"])
            with graph.reader_lease() as current:
                inputs = json.loads(
                    current.db.execute(
                        "SELECT value FROM manifest WHERE key='generation_inputs'"
                    ).fetchone()[0]
                )
                assert inputs["format"] == "atlas-generation.v5"
                assert (
                    inputs["retrieval"]["source_count"] == 2
                    and inputs["retrieval"]["coverage"]["state"] == "partial"
                )
                assert current.read_source("b.py") == replacement
                assert (
                    current.read_source("history.py", hashlib.sha256(old_history).hexdigest())
                    == old_history
                )
                docs = ret.stores(current.db, inputs["retrieval"])
                assert any(
                    d["kind"] == "source_file" and d["text"] == replacement.decode() for d in docs
                )
                assert not list(
                    current.db.execute(
                        "SELECT candidate_id FROM retrieval_fts WHERE retrieval_fts MATCH ?",
                        ('"previous"',),
                    )
                )
                identity = current.generation_id
                descriptor = inputs["retrieval"]
            graph.build(since="HEAD", overlays={"b.py": replacement})
            with graph.reader_lease() as current:
                assert current.generation_id == identity
                assert (
                    json.loads(
                        current.db.execute(
                            "SELECT value FROM manifest WHERE key='generation_inputs'"
                        ).fetchone()[0]
                    )["retrieval"]
                    == descriptor
                )
            assert old_source.fd == old_fd
        assert old_source.fd is None
        with pytest.raises(sqlite3.ProgrammingError):
            old_db.execute("SELECT 1")
    finally:
        graph.close()


def test_sourcefree_portable7_roundtrip_and_closed_v5(tmp_path):
    repo = tmp_path / "import"
    repo.mkdir()
    (repo / "b.py").write_text("this is not indexed evidence")
    graph = CodeGraph(repo)
    artifact = ROOT / ".spectra/evidence/aci-s17/native8-portable.jsonl"
    try:
        graph.import_jsonl(artifact)
        output = tmp_path / "roundtrip.jsonl"
        graph.export_jsonl(output)
        assert output.read_bytes() == artifact.read_bytes()
        with graph.reader_lease() as reader:
            assert reader._lease_schema_epoch == 9 and reader._source_lease is None
            inputs = json.loads(
                reader.db.execute(
                    "SELECT value FROM manifest WHERE key='generation_inputs'"
                ).fetchone()[0]
            )
            assert inputs["format"] == "atlas-generation.v5"
            assert inputs["snapshot"] == {"state": "unavailable", "reason": "import"}
            assert inputs["retrieval"]["source_count"] == 0
            assert inputs["retrieval"]["coverage"]["state"] == "unavailable"
        with sqlite3.connect(graph.db_path) as conn:
            inputs["format"] = "atlas-generation.v4"
            inputs.pop("retrieval")
            identifier = hashlib.sha256(
                __import__("atlas_aci.storage.evidence", fromlist=["canonical"])
                .canonical(inputs)
                .encode()
            ).hexdigest()
            conn.execute(
                "UPDATE manifest SET value=? WHERE key='generation_inputs'", (json.dumps(inputs),)
            )
            conn.execute("UPDATE manifest SET value=? WHERE key='generation_id'", (identifier,))
        with pytest.raises(ToolError, match="complete compatible"), graph.reader_lease():
            pytest.fail("native9 v4 accepted")
    finally:
        graph.close()


def test_failure_after_real_fts_work_preserves_old8_and_retries(tmp_path, monkeypatch):
    graph, fixture = old8(tmp_path)
    before = (graph.atlas_dir / "graph.8.db").read_bytes()
    original = ret.populate

    def failed(*args, **kwargs):
        original(*args, **kwargs)
        raise sqlite3.OperationalError("controlled derivative failure after real work")

    try:
        with graph.reader_lease() as held:
            monkeypatch.setattr(ret, "populate", failed)
            with pytest.raises(sqlite3.OperationalError, match="controlled derivative"):
                graph.build(overlays={"b.py": b"def updated():\n pass\n"})
            assert (graph.atlas_dir / "graph.8.db").read_bytes() == before
            assert not graph.db_path.exists()
            assert not list(graph.atlas_dir.glob(".graph.*.tmp.*"))
            assert not list(graph.atlas_dir.glob(".sources-stage.*"))
            assert held.read_source("b.py") == base64.b64decode(fixture["source_files"]["b.py"])
            monkeypatch.setattr(ret, "populate", original)
            graph.build()
            with graph.reader_lease() as current:
                assert current._lease_schema_epoch == 9
    finally:
        graph.close()


def test_present_native9_fts_id_corruption_never_falls_back(tmp_path):
    graph, fixture = old8(tmp_path)
    try:
        graph.build()
        with sqlite3.connect(graph.db_path) as conn:
            rows = conn.execute(
                "SELECT rowid,candidate_id FROM retrieval_fts ORDER BY rowid LIMIT 2"
            ).fetchall()
            for row, other in ((rows[0], rows[1]), (rows[1], rows[0])):
                conn.execute("UPDATE retrieval_fts_content SET c0=? WHERE id=?", (other[1], row[0]))
            assert conn.execute("PRAGMA integrity_check(retrieval_fts)").fetchall() == [("ok",)]
        # A complete older database is still present; malformed9 must dominate it.
        item = fixture["native_files"]["graph.8.db"]
        (graph.atlas_dir / "graph.8.db").write_bytes(base64.b64decode(item["base64"]))
        before = hashlib.sha256(graph.db_path.read_bytes()).hexdigest()
        with pytest.raises(ToolError, match="complete compatible"), graph.reader_lease():
            pytest.fail("corrupt native9 served")
        assert hashlib.sha256(graph.db_path.read_bytes()).hexdigest() == before
    finally:
        graph.close()


@pytest.mark.parametrize("empty", [False, True])
def test_old_canonical_v1_import_keeps_exact_portable_format(tmp_path, empty):
    from atlas_aci.storage import evidence as ev

    repo = tmp_path / "producer"
    repo.mkdir()
    if not empty:
        (repo / "a.py").write_text("def A():\n pass\n")
    graph = CodeGraph(repo)
    graph.build()
    artifact = tmp_path / "new.jsonl"
    graph.export_jsonl(artifact)
    graph.close()
    rows = [json.loads(line) for line in artifact.read_text().splitlines()]
    records = [r for r in rows[1:] if r["type"] not in {"observation", "history"}]
    body = b"".join((ev.canonical(r) + "\n").encode() for r in records)
    header = {
        **rows[0],
        "format": ev.FORMAT,
        "record_count": len(records),
        "content_hash": hashlib.sha256(body).hexdigest(),
    }
    old = tmp_path / "canonical-v1.jsonl"
    old.write_bytes((ev.canonical(header) + "\n").encode() + body)
    destination = tmp_path / "import"
    destination.mkdir()
    imported = CodeGraph(destination)
    try:
        imported.import_jsonl(old)
        output = tmp_path / "again.jsonl"
        imported.export_jsonl(output)
        assert output.read_bytes() == old.read_bytes()
        with imported.reader_lease() as reader:
            assert reader._source_lease is None
    finally:
        imported.close()


def test_modern_import_cannot_omit_coherently_rehashed_observations(tmp_path):
    from atlas_aci.storage import evidence as ev

    repo = tmp_path / "producer"
    repo.mkdir()
    (repo / "a.py").write_text("def A():\n pass\n")
    writer = CodeGraph(repo)
    writer.build()
    artifact = tmp_path / "modern.jsonl"
    writer.export_jsonl(artifact)
    writer.close()
    destination = tmp_path / "destination"
    destination.mkdir()
    graph = CodeGraph(destination)
    graph.import_jsonl(artifact)
    rows = [json.loads(line) for line in artifact.read_text().splitlines()]
    assert rows[0]["format"] == ev.OBSERVATION_FORMAT
    records = [r for r in rows[1:] if r["type"] not in {"observation", "history"}]
    body = b"".join((ev.canonical(r) + "\n").encode() for r in records)
    header = {
        **rows[0],
        "record_count": len(records),
        "content_hash": hashlib.sha256(body).hexdigest(),
    }
    forged = (ev.canonical(header) + "\n").encode() + body
    try:
        with sqlite3.connect(graph.db_path) as conn:
            inputs = json.loads(
                conn.execute("SELECT value FROM manifest WHERE key='generation_inputs'").fetchone()[
                    0
                ]
            )
            assert inputs["retrieval"]["coverage"]["source_policy"] == "import"
            inputs["observation_digest"] = hashlib.sha256(ev.canonical([]).encode()).hexdigest()
            inputs["artifact"] = {
                "sha256": hashlib.sha256(forged).hexdigest(),
                "content_hash": header["content_hash"],
                "record_count": len(records),
            }
            identifier = hashlib.sha256(ev.canonical(inputs).encode()).hexdigest()
            conn.execute("UPDATE manifest SET value='[]' WHERE key='source_observations'")
            conn.execute(
                "UPDATE manifest SET value=? WHERE key='generation_inputs'", (ev.canonical(inputs),)
            )
            conn.execute("UPDATE manifest SET value=? WHERE key='generation_id'", (identifier,))
        with pytest.raises(ToolError, match="complete compatible"), graph.reader_lease():
            pytest.fail("modern parsed source evidence accepted without observations")
    finally:
        graph.close()
