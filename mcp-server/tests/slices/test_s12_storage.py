"""Real native schema upgrades, exact old readers and actual indexed SQL plans."""

import base64
import gzip
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from atlas_aci.codegraph import SCHEMA_EPOCH, CodeGraph

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "mcp-server/tests/fixtures/s12"
INDEXES = {
    "out": "idx_assertions_source_relation_id",
    "in": "idx_assertions_target_relation_id",
    "name": "idx_entities_name_id",
}


def native7(tmp_path):
    """Run exact archived baseline producer at this test's own real source identity."""
    package = tmp_path / "old-producer"
    archive = json.loads(gzip.decompress((FIXTURES / "baseline-source.json.gz").read_bytes()))
    for name, item in archive["files"].items():
        data = base64.b64decode(item["base64"])
        assert hashlib.sha256(data).hexdigest() == item["sha256"]
        target = package / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    repo = tmp_path / "repo"
    repo.mkdir()
    original = json.loads((FIXTURES / "baseline-provenance.json").read_text())["identity"][
        "captured_original_source"
    ]
    for name, data in original.items():
        (repo / name).write_text(data)
    script = """import json,sys,structlog
from pathlib import Path
from atlas_aci.codegraph import CodeGraph,SCHEMA_EPOCH
structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
assert SCHEMA_EPOCH==7
module=Path(sys.modules[CodeGraph.__module__].__file__).resolve()
assert module.is_relative_to(Path(sys.argv[2]).resolve())
g=CodeGraph(Path(sys.argv[1]));g.build()
(g.repo/'history.py').write_text('def broken(:\\n pass\\n')
g.build()
with g.reader_lease() as r:
 print(json.dumps({'generation_id':r.generation_id,'snapshot_id':r.snapshot_id}))
g.close()
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(repo), str(package)],
        env={**os.environ, "PYTHONPATH": str(package), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return repo, json.loads(result.stdout)


def test_native_epoch_and_indexed_keysets(tmp_path):
    assert SCHEMA_EPOCH == 9
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def A():\n B(); B()\ndef B():\n A()\n")
    graph = CodeGraph(repo)
    graph.build()
    try:
        with graph.reader_lease() as reader:
            row = reader.db.execute("SELECT payload FROM assertions ORDER BY id LIMIT 1").fetchone()
            assertion = json.loads(row[0])
            cases = [
                (
                    "out",
                    "SELECT id,payload FROM assertions "
                    "INDEXED BY idx_assertions_source_relation_id "
                    "WHERE json_extract(payload,'$.source_entity_id')=? "
                    "AND json_extract(payload,'$.relation')=? AND id>? ORDER BY id LIMIT 1",
                    (assertion["source_entity_id"], assertion["relation"], ""),
                ),
                (
                    "in",
                    "SELECT id,payload FROM assertions "
                    "INDEXED BY idx_assertions_target_relation_id "
                    "WHERE json_extract(payload,'$.target_entity_id')=? "
                    "AND json_extract(payload,'$.relation')=? AND id>? ORDER BY id LIMIT 1",
                    (assertion["target_entity_id"], assertion["relation"], ""),
                ),
                (
                    "name",
                    "SELECT id,payload FROM entities INDEXED BY idx_entities_name_id "
                    "WHERE json_extract(payload,'$.name')=? AND id>? ORDER BY id LIMIT 1",
                    ("A", ""),
                ),
            ]
            for name, sql, args in cases:
                plan = [row[3] for row in reader.db.execute("EXPLAIN QUERY PLAN " + sql, args)]
                assert any("SEARCH" in step and INDEXES[name] in step for step in plan)
                assert all("SCAN" not in step and "TEMP B-TREE" not in step for step in plan)
                assert reader.db.execute(sql, args).fetchone() is not None
    finally:
        graph.close()


def test_genuine_native7_original_identity_readonly(tmp_path):
    fixture = json.loads(gzip.decompress((FIXTURES / "native7.json.gz").read_bytes()))
    graph = CodeGraph(Path(fixture["repository"]), graph_root=tmp_path / "graphs")
    for name, data in fixture["files"].items():
        path = graph.atlas_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(data))
    before = {
        str(p.relative_to(graph.atlas_dir)): p.read_bytes()
        for p in graph.atlas_dir.rglob("*")
        if p.is_file()
    }
    try:
        with graph.reader_lease() as reader:
            assert reader.generation_id == fixture["identity"]["generation_id"]
            assert reader.snapshot_id == fixture["identity"]["snapshot_id"]
            for query, expected in fixture["queries"].items():
                assert reader.query(query) == expected
            assert reader.read_source("history.py").decode() == "def broken(:\n pass\n"
        assert {
            str(p.relative_to(graph.atlas_dir)): p.read_bytes()
            for p in graph.atlas_dir.rglob("*")
            if p.is_file()
        } == before
        assert not (graph.atlas_dir / "graph.8.db").exists()
        assert not graph.db_path.exists()
    finally:
        graph.close()


def test_staged_upgrade_preserves_history_and_held_source(tmp_path, monkeypatch):
    repo, identity = native7(tmp_path)
    graph = CodeGraph(repo)
    old = graph.atlas_dir / "graph.7.db"
    original = old.read_bytes()
    operation = graph._run_build
    try:
        with graph.reader_lease() as held:
            assert held.generation_id == identity["generation_id"]
            old_history = [r for r in held.observation_records() if r["type"] == "history"]
            old_hash = next(
                r["source_sha256"]
                for r in old_history[0]["records"]
                if r["type"] == "occurrence" and r["path"] == "history.py"
            )
            old_source = held.read_source("history.py", old_hash)

            def fail_after_real_work(*args, **kwargs):
                operation(*args, **kwargs)
                raise RuntimeError("controlled failure before native9 publication")

            with monkeypatch.context() as patch:
                patch.setattr(graph, "_run_build", fail_after_real_work)
                with pytest.raises(RuntimeError, match="before native9"):
                    graph.build(since="HEAD")
            assert old.read_bytes() == original and not (graph.atlas_dir / "graph.8.db").exists()
            assert not graph.db_path.exists()
            assert held.read_source("history.py", old_hash) == old_source
            assert not list(graph.atlas_dir.glob(".graph.*.tmp.*"))
            graph.build(since="HEAD")
            assert graph.db_path.name == f"graph.{SCHEMA_EPOCH}.db" and graph.db_path.exists()
            assert not old.exists()
            assert held.read_source("history.py", old_hash) == old_source
            with graph.reader_lease() as current:
                assert current.generation_id != identity["generation_id"]
                assert [
                    r for r in current.observation_records() if r["type"] == "history"
                ] == old_history
                assert current.read_source("history.py", old_hash) == old_source
    finally:
        graph.close()


def test_portable7_import_publishes_native8_without_rewriting_evidence(tmp_path):
    repo = tmp_path / "imported"
    repo.mkdir()
    graph = CodeGraph(repo)
    artifact = FIXTURES / "baseline-evidence.jsonl"
    graph.import_jsonl(artifact)
    try:
        assert graph.db_path.name == f"graph.{SCHEMA_EPOCH}.db"
        output = tmp_path / "roundtrip.jsonl"
        graph.export_jsonl(output)
        assert output.read_bytes() == artifact.read_bytes()
        with graph.reader_lease() as reader:
            assert reader.snapshot_id is None
    finally:
        graph.close()


@pytest.mark.parametrize("epoch", [6, 10, True, "7", "8", "9"])
def test_source_manifest_schema_epoch_is_closed(epoch):
    from atlas_aci.storage import snapshots

    fixture = json.loads(gzip.decompress((FIXTURES / "native7.json.gz").read_bytes()))
    name = next(name for name in fixture["files"] if "/manifest." in name)
    manifest = json.loads(base64.b64decode(fixture["files"][name]))
    snapshots.validate_manifest(manifest)
    manifest["context"]["schema"]["epoch"] = epoch
    with pytest.raises(ValueError, match="schema"):
        snapshots.validate_manifest(manifest)


def test_genuine_epoch6_reader_and_writer_conversion(tmp_path):
    raw = gzip.decompress((ROOT / "mcp-server/tests/fixtures/s07/epoch6.db.gz").read_bytes())
    with sqlite3.connect(":memory:") as conn:
        conn.deserialize(raw)
        old_meta = dict(conn.execute("SELECT key,value FROM manifest"))
    original_repo = Path(json.loads(old_meta["generation_inputs"])["repository"])
    graph = CodeGraph(original_repo, graph_root=tmp_path / "graphs")
    path = graph.atlas_dir / "graph.6.db"
    path.write_bytes(raw)
    try:
        with graph.reader_lease() as held:
            assert held._lease_schema_epoch == 6 and held.snapshot_id is None
            assert held.generation_id == old_meta["generation_id"]
            assert held.query("definitions_of:A")["definitions"]
            graph.migrate_legacy()
            assert held.query("definitions_of:A")["definitions"]
            assert path.read_bytes() == raw
            with graph.reader_lease() as current:
                assert current._lease_schema_epoch == SCHEMA_EPOCH and current.snapshot_id is None
                assert current.query("definitions_of:A")["definitions"]
    finally:
        graph.close()


@pytest.mark.parametrize(
    "schema",
    [
        {
            "epoch": 7,
            "ddl_hash": "23aee629ec89a6bd420ed3719e2e42de9d0a456b52e1b29aa3760bc05a0eb26a",
        },
        {"epoch": 8, "ddl_hash": "0" * 64},
    ],
)
def test_rehashed_foreign_schema_fails_own_graph_binding(tmp_path, monkeypatch, schema):
    from atlas_aci.enforcement import ToolError
    from atlas_aci.storage import snapshots

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def A():\n pass\n")
    graph = CodeGraph(repo)
    graph.build()
    with graph.reader_lease() as reader:
        token = reader.snapshot_id
    store = graph.atlas_dir / "source-snapshots"
    manifest = json.loads((store / ("manifest." + token + ".json")).read_text())
    manifest["context"]["schema"] = schema
    snapshots.validate_manifest(manifest)
    new_token = snapshots.digest(manifest)
    (store / ("manifest." + new_token + ".json")).write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    )
    with sqlite3.connect(graph.db_path) as conn:
        values = dict(conn.execute("SELECT key,value FROM manifest"))
        inputs = json.loads(values["generation_inputs"])
        inputs["snapshot"]["id"] = new_token
        inputs["snapshot"]["manifest_sha256"] = new_token
        raw = json.dumps(inputs, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        identifier = hashlib.sha256(raw.encode()).hexdigest()
        coverage = json.loads(values["coverage_snapshot"])
        coverage["generation_id"] = identifier
        for key, value in [
            ("generation_inputs", raw),
            ("generation_id", identifier),
            ("coverage_snapshot", json.dumps(coverage, sort_keys=True, separators=(",", ":"))),
        ]:
            conn.execute("UPDATE manifest SET value=? WHERE key=?", (value, key))
    descriptors = []
    actual = snapshots.acquire

    def observed(*args, **kwargs):
        lease = actual(*args, **kwargs)
        descriptors.append(lease.fd)
        return lease

    monkeypatch.setattr(snapshots, "acquire", observed)
    try:
        with (
            pytest.raises(ToolError, match="Graph/source provenance contradiction"),
            graph.reader_lease(),
        ):
            raise AssertionError("foreign source context exposed")
        assert len(descriptors) == 1
        with pytest.raises(OSError):
            os.fstat(descriptors[0])
    finally:
        graph.close()


def test_present_dangling8_never_falls_back_to_valid7(tmp_path):
    from atlas_aci.enforcement import ToolError

    fixture = json.loads(gzip.decompress((FIXTURES / "native7.json.gz").read_bytes()))
    graph = CodeGraph(Path(fixture["repository"]), graph_root=tmp_path / "graphs")
    for name, data in fixture["files"].items():
        path = graph.atlas_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(data))
    current = graph.atlas_dir / "graph.8.db"
    current.symlink_to(graph.atlas_dir / "missing-eight")
    try:
        with pytest.raises(ToolError), graph.reader_lease():
            raise AssertionError("present malformed8 exposed old7")
        current.unlink()
        with graph.reader_lease() as reader:
            assert reader._lease_schema_epoch == 7
    finally:
        graph.close()


def test_present_dangling7_never_falls_back_to_valid6(tmp_path):
    from atlas_aci.enforcement import ToolError

    raw = gzip.decompress((ROOT / "mcp-server/tests/fixtures/s07/epoch6.db.gz").read_bytes())
    with sqlite3.connect(":memory:") as conn:
        conn.deserialize(raw)
        inputs = json.loads(
            dict(conn.execute("SELECT key,value FROM manifest"))["generation_inputs"]
        )
    graph = CodeGraph(Path(inputs["repository"]), graph_root=tmp_path / "graphs")
    (graph.atlas_dir / "graph.6.db").write_bytes(raw)
    previous = graph.atlas_dir / "graph.7.db"
    previous.symlink_to(graph.atlas_dir / "missing-seven")
    try:
        with pytest.raises(ToolError), graph.reader_lease():
            raise AssertionError("present malformed7 exposed old6")
        previous.unlink()
        with graph.reader_lease() as reader:
            assert reader._lease_schema_epoch == 6
    finally:
        graph.close()


@pytest.mark.parametrize("kind", ["directory", "fifo", "symlink"])
def test_nonregular_native_candidate_rejected_before_open(tmp_path, kind):
    repo = tmp_path / "repo"
    repo.mkdir()
    graph = CodeGraph(repo)
    candidate = graph.db_path
    if kind == "directory":
        candidate.mkdir()
    elif kind == "fifo":
        os.mkfifo(candidate)
    else:
        target = graph.atlas_dir / "regular-target"
        target.write_bytes(b"not followed")
        candidate.symlink_to(target)
    try:
        with pytest.raises(ValueError, match="regular"):
            graph._read_db_path()
    finally:
        graph.close()
