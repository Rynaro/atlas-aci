"""Real legacy artifact, pinned status, failure taxonomy and lease boundaries."""

import asyncio
import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

import atlas_aci.server as server
from atlas_aci.codegraph import CodeGraph
from atlas_aci.config import Config
from atlas_aci.protocol.graph import GRAPH_OUTPUT_SCHEMA
from atlas_aci.storage import snapshots as snap

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "mcp-server/tests/fixtures/s11"
STATUS = {"schema_version": "atlas.graph.v1", "op": "status"}


def setup(tmp_path, *, indexed=True):
    repo = tmp_path / "repo"
    repo.mkdir()
    writer = None
    if indexed:
        (repo / "a.py").write_bytes(
            b"def target():\n return 1\ndef caller():\n # NOTE: stable relation\n target()\n"
        )
        (repo / "b.py").write_bytes(b"def original():\n return 1\n")
        writer = CodeGraph(repo)
        writer.build()
    config = Config(repo=repo, memex_root=tmp_path / "memex")
    enforcement, memex, factory = server.build_server_state(config)
    return repo, writer, config, enforcement, memex, factory


async def invoke(state, args, **kwargs):
    _repo, _writer, config, enforcement, memex, factory = state
    return await server.call_tool_result(
        "graph_query", args, config, enforcement, memex, factory, **kwargs
    )


def close(state):
    if state[1] is not None:
        state[1].close()
    state[-1].close()


def test_legacy_compatibility(tmp_path):
    state = setup(tmp_path, indexed=False)
    writer = CodeGraph(state[0])
    artifact = FIXTURE / "legacy-evidence.jsonl"
    assert (
        hashlib.sha256(artifact.read_bytes()).hexdigest()
        == "75253c5c6d4d8c88b626a12ab9373f3f865c9d4eb6f4d16134c0ec61a6fa86e7"
    )
    writer.import_jsonl(artifact)
    try:
        for expected in json.loads((FIXTURE / "legacy-baseline.json").read_text()):
            result = asyncio.run(invoke(state, expected["arguments"]))
            actual = json.loads(result.content[0].text)
            original = dict(expected["decoded"])
            assert actual["generation_id"] != original["generation_id"]
            actual.pop("generation_id")
            original.pop("generation_id")
            assert actual == original
            assert result.structuredContent == json.loads(result.content[0].text)
            assert result.isError == ("error" in original)
        exported = tmp_path / "roundtrip.jsonl"
        writer.export_jsonl(exported)
        assert exported.read_bytes() == artifact.read_bytes()
    finally:
        writer.close()
        close(state)


def test_structured_status(tmp_path):
    state = setup(tmp_path)
    repo, writer, *_ = state
    (repo / "b.py").write_bytes(b"def broken(:\n pass\n")
    writer.build()
    try:
        result = asyncio.run(invoke(state, STATUS))
        payload = result.structuredContent
        Draft202012Validator(GRAPH_OUTPUT_SCHEMA).validate(payload)
        assert not result.isError and payload == json.loads(result.content[0].text)
        assert payload["status"] == "ok" and payload["evidence_status"] == "partial"
        data = payload["data"]
        assert data["coverage_basis"] == "published_generation"
        assert (
            data["latest_attempt_included"] is False
            and data["runtime_capabilities_included"] is False
        )
        assert data["indexed"]["capabilities"]["representation"] == "bounded_projection"
        failed = next(row for row in data["completeness"]["files"] if row["path"] == "b.py")
        assert failed["stale"] is True and failed["history_id"] and failed["observation_id"]
        observed = next(
            row
            for row in data["source"]["references"]
            if row["path"] == "b.py" and row["provenance"] == "observed_source"
        )
        historical = next(
            row
            for row in data["source"]["references"]
            if row["path"] == "b.py" and row["provenance"] == "historical_indexed"
        )
        assert observed["extraction_state"] == "failed" and observed["stale"] is None
        assert (
            historical["stale"] is True and historical["source_sha256"] != observed["source_sha256"]
        )
        with state[-1].reader_lease() as reader:
            assert payload["snapshot_id"] == reader.snapshot_id
            for row in data["source"]["references"]:
                assert (
                    reader._source_lease.bindings[(row["path"], row["source_sha256"])]
                    == row["available"]
                )
                if row["available"]:
                    assert (
                        hashlib.sha256(
                            reader.read_source(row["path"], row["source_sha256"])
                        ).hexdigest()
                        == row["source_sha256"]
                    )
    finally:
        close(state)


def test_invalid_operation(tmp_path, monkeypatch):
    state = setup(tmp_path)
    calls = []

    def forbidden():
        calls.append(True)
        raise AssertionError("invalid input acquired graph")

    monkeypatch.setattr(state[-1], "reader_lease", forbidden)
    try:
        for args in [
            {"schema_version": "atlas.graph.v1", "op": "unknown"},
            {"schema_version": "future", "op": "status"},
            {"query": "rationale:", **STATUS},
            {"query": False},
            {**STATUS, "unexpected": "x" * 10000},
        ]:
            result = asyncio.run(invoke(state, args))
            assert result.isError
            data = json.loads(result.content[0].text)
            assert (data["error"]["code"] if "schema_version" in data else data["error"]) in {
                "INVALID_OPERATION",
                "UNSUPPORTED_SCHEMA_VERSION",
                "MIXED_REQUEST",
                "INVALID_INPUT",
            }
        assert calls == []
    finally:
        close(state)


def test_tool_error_flag(tmp_path, monkeypatch):
    state = setup(tmp_path)
    original = server.graph_query

    async def failure(*_args):
        raise RuntimeError("private-exception-" * 10000)

    try:
        monkeypatch.setattr(server, "graph_query", failure)
        result = asyncio.run(invoke(state, STATUS))
        assert result.isError
        assert result.structuredContent["error"]["code"] == "INTERNAL"
        assert "private-exception" not in result.content[0].text
        monkeypatch.setattr(server, "graph_query", original)
        legacy = asyncio.run(invoke(state, {"query": "not-a-query"}))
        assert legacy.isError
        with state[-1].reader_lease() as reader:
            pack = reader._source_lease.manifest["pack"]["sha256"]
        (state[-1].atlas_dir / "source-snapshots" / ("pack." + pack + ".bin")).unlink()
        result = asyncio.run(invoke(state, STATUS))
        assert result.isError and result.structuredContent["data"] is None
        assert result.structuredContent["error"]["code"] == "SNAPSHOT_UNAVAILABLE"
    finally:
        close(state)


def test_no_evidence_success(tmp_path):
    state = setup(tmp_path)
    try:
        result = asyncio.run(invoke(state, {"query": "definitions_of:Absent"}))
        assert not result.isError and json.loads(result.content[0].text)["definitions"] == []
    finally:
        close(state)
    absent = tmp_path / "absent"
    absent.mkdir()
    state = setup(absent, indexed=False)
    try:
        before = list(state[0].iterdir())
        result = asyncio.run(invoke(state, STATUS))
        assert not result.isError
        assert result.structuredContent["data"]["completeness"]["state"] == "unavailable"
        assert list(state[0].iterdir()) == before and not (state[0] / ".atlas").exists()
        imported = CodeGraph(state[0])
        imported.import_jsonl(FIXTURE / "legacy-evidence.jsonl")
        imported.close()
        result = asyncio.run(invoke(state, STATUS))
        assert not result.isError
        assert result.structuredContent["data"]["completeness"]["state"] == "unknown"
        assert result.structuredContent["data"]["source"]["state"] == "unavailable"
        assert result.structuredContent["data"]["source"]["references"] == []
        with sqlite3.connect(state[-1].db_path) as conn:
            conn.execute("UPDATE manifest SET value='{}' WHERE key='generation_inputs'")
        result = asyncio.run(invoke(state, STATUS))
        assert result.isError and result.structuredContent["data"] is None
    finally:
        close(state)


def test_structured_replay(tmp_path, monkeypatch):
    state = setup(tmp_path)
    repo, writer, *_ = state
    try:
        first = asyncio.run(invoke(state, STATUS))
        generation = first.structuredContent["generation_id"]
        (repo / "a.py").write_bytes(b"def target():\n return 2\ndef caller():\n target()\n")
        actual = snap.safe_source

        def denied(root, path):
            if path == "b.py":
                raise PermissionError("controlled real source read failure")
            return actual(root, path)

        with monkeypatch.context() as patch:
            patch.setattr(snap, "safe_source", denied)
            with pytest.raises(PermissionError):
                writer.build()
        assert writer.status()["completeness"]["state"] == "failed"
        with monkeypatch.context() as patch:
            patch.setattr(
                CodeGraph, "_capabilities", lambda *_args, **_kw: {"changed_runtime": True}
            )
            assert asyncio.run(invoke(state, STATUS)).content[0].text == first.content[0].text

        async def held_request():
            entered = asyncio.Event()
            release = asyncio.Event()
            original = server.graph_query
            readers = []

            async def blocked(args, config, enforcement, reader):
                readers.append(reader)
                entered.set()
                await release.wait()
                return await original(args, config, enforcement, reader)

            with monkeypatch.context() as patch:
                patch.setattr(server, "graph_query", blocked)
                task = asyncio.create_task(
                    invoke(state, {**STATUS, "expected_generation_id": generation})
                )
                await entered.wait()
                reader = readers[0]
                fd = reader._source_lease.fd
                conn = reader.db
                writer.build()
                assert reader.generation_id == generation
                release.set()
                result = await task
                assert result.content[0].text == first.content[0].text
                assert reader._closed
                with pytest.raises(OSError):
                    os.fstat(fd)
                with pytest.raises(sqlite3.ProgrammingError):
                    conn.execute("SELECT 1")

        asyncio.run(held_request())
        current = asyncio.run(invoke(state, STATUS))
        assert current.structuredContent["generation_id"] != generation
        guarded = asyncio.run(invoke(state, {**STATUS, "expected_generation_id": generation}))
        assert guarded.isError
        assert guarded.structuredContent["error"]["code"] == "SNAPSHOT_MISMATCH"
    finally:
        close(state)


def test_cancelled_status_closes_real_lease_once(tmp_path, monkeypatch):
    state = setup(tmp_path)
    readers = []
    closes = []
    actual_close = CodeGraph.close

    def counted_close(reader):
        if reader in readers:
            closes.append(reader)
        return actual_close(reader)

    async def exercise():
        entered = asyncio.Event()
        never = asyncio.Event()

        async def controlled_await(_args, _config, _enforcement, reader):
            readers.append(reader)
            entered.set()
            await never.wait()

        with monkeypatch.context() as patch:
            patch.setattr(server, "graph_query", controlled_await)
            patch.setattr(CodeGraph, "close", counted_close)
            task = asyncio.create_task(invoke(state, STATUS))
            await entered.wait()
            reader = readers[0]
            conn, fd = reader.db, reader._source_lease.fd
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert closes == [reader] and reader._closed
            with pytest.raises(OSError):
                os.fstat(fd)
            with pytest.raises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")

    try:
        asyncio.run(exercise())
    finally:
        close(state)


def test_status_caps_every_nested_list_and_keeps_full_counts(tmp_path):
    state = setup(tmp_path)
    try:
        full = asyncio.run(invoke(state, STATUS)).structuredContent
        state[2].max_bound_field_elements = 1
        bounded = asyncio.run(invoke(state, STATUS)).structuredContent
        Draft202012Validator(GRAPH_OUTPUT_SCHEMA).validate(bounded)
        assert bounded["data"]["completeness"]["counts"] == full["data"]["completeness"]["counts"]
        assert bounded["data"]["source"]["total_count"] == full["data"]["source"]["total_count"]
        visited = []

        def walk(original, projected, path=""):
            if isinstance(original, dict):
                for key, value in original.items():
                    walk(value, projected[key], path + "/" + key)
            elif isinstance(original, list):
                visited.append(path)
                assert len(projected) == min(1, len(original))
                assert bounded["bounds"]["lists"][path] == {
                    "total_count": len(original),
                    "returned_count": len(projected),
                    "truncated": len(original) > 1,
                }
                for i, value in enumerate(projected):
                    walk(original[i], value, path + "/" + str(i))

        walk(full["data"], bounded["data"], "/data")
        assert "/data/completeness/files" in visited
        assert "/data/source/references" in visited
        assert any(path.endswith("/definitions") for path in visited)
        assert any(path.endswith("/extensions") for path in visited)
        assert bounded["bounds"]["truncated"] is True
    finally:
        close(state)


def test_nonzero_test_process_and_error_text_are_not_tool_errors(tmp_path, monkeypatch):
    import sys

    state = setup(tmp_path)
    state[2].test_execution_enabled = True
    (state[0] / "test_owned.py").write_text("def test_failure():\n    assert False, 'error'\n")
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])
    try:
        result = asyncio.run(
            server.call_tool_result(
                "test_dry_run", {"path": "test_owned.py"}, state[2], state[3], state[4], state[5]
            )
        )
        payload = json.loads(result.content[0].text)
        assert not result.isError and payload["exit_code"] == 1
        assert "error" in payload["stdout"]
    finally:
        close(state)


def test_generated_invalid_status_cannot_escape_via_direct_sdk_result(tmp_path, monkeypatch):
    state = setup(tmp_path)

    async def malformed(*_args):
        return {"schema_version": "atlas.graph.v1", "status": "ok", "private": "x" * 100000}

    monkeypatch.setattr(server, "graph_query", malformed)
    try:
        result = asyncio.run(invoke(state, STATUS))
        assert result.isError
        assert result.structuredContent["error"]["code"] == "INVALID_RESULT"
        assert "private" not in result.content[0].text
    finally:
        close(state)


@pytest.mark.parametrize("operation_error", [False, True])
def test_normal_and_error_status_acquire_and_close_once(tmp_path, monkeypatch, operation_error):
    state = setup(tmp_path)
    readers, closes = [], []
    actual_operation, actual_close = server.graph_query, CodeGraph.close

    async def observed_operation(args, config, enforcement, reader):
        readers.append(reader)
        if operation_error:
            raise RuntimeError("controlled operation failure")
        return await actual_operation(args, config, enforcement, reader)

    def observed_close(reader):
        if reader in readers:
            closes.append(reader)
        return actual_close(reader)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(server, "graph_query", observed_operation)
            patch.setattr(CodeGraph, "close", observed_close)
            result = asyncio.run(invoke(state, STATUS))
            assert result.isError is operation_error
            assert len(readers) == 1 and closes == readers
            assert readers[0]._closed
    finally:
        close(state)


def test_genuine_epoch6_status_retains_unknown_coverage(tmp_path):
    import gzip

    state = setup(tmp_path, indexed=False)
    directory = state[-1].atlas_dir
    directory.mkdir(parents=True, exist_ok=True)
    fixture = ROOT / "mcp-server/tests/fixtures/s07/epoch6.db.gz"
    old_db = directory / "graph.6.db"
    original = gzip.decompress(fixture.read_bytes())
    old_db.write_bytes(original)
    # Same explicit destination reanchor as the genuine S07 legacy fixture:
    # retain all source/query facts and update only repository + derived ID.
    with sqlite3.connect(old_db) as connection:
        inputs = json.loads(
            connection.execute(
                "SELECT value FROM manifest WHERE key='generation_inputs'"
            ).fetchone()[0]
        )
        inputs["repository"] = str(state[0].resolve())
        raw = json.dumps(inputs, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        connection.execute("UPDATE manifest SET value=? WHERE key='generation_inputs'", (raw,))
        connection.execute(
            "UPDATE manifest SET value=? WHERE key='generation_id'",
            (hashlib.sha256(raw.encode()).hexdigest(),),
        )
    anchored = old_db.read_bytes()
    try:
        result = asyncio.run(invoke(state, STATUS))
        assert not result.isError
        payload = result.structuredContent
        assert payload["evidence_status"] == "unknown"
        assert payload["data"]["completeness"]["state"] == "unknown"
        assert payload["data"]["source"]["state"] == "unavailable"
        assert payload["data"]["source"]["references"] == []
        assert old_db.read_bytes() == anchored
        assert not state[-1].db_path.exists()
    finally:
        close(state)


def test_immutable_original_v1_coverage_adapter(tmp_path):
    import gzip

    original = gzip.decompress((ROOT / "mcp-server/tests/fixtures/s07/epoch6.db.gz").read_bytes())
    with sqlite3.connect(":memory:") as connection:
        connection.deserialize(original)
        manifest = dict(connection.execute("SELECT key,value FROM manifest"))
    inputs = json.loads(manifest["generation_inputs"])
    stored = json.loads(manifest["coverage_snapshot"])
    assert stored["capabilities"]["format"] == "atlas-capabilities.v1"
    assert "limitations" not in stored["capabilities"]
    # Preserve the original repository identity and every original byte/ID.
    # Only the external storage location is temporary; no original source writes.
    repo = Path(inputs["repository"])
    writer = CodeGraph(repo, graph_root=tmp_path / "graphs")
    old_db = writer.atlas_dir / "graph.6.db"
    old_db.write_bytes(original)
    config = Config(repo=repo, graph_root=tmp_path / "graphs", memex_root=tmp_path / "memex")
    enforcement, memex, factory = server.build_server_state(config)
    state = repo, writer, config, enforcement, memex, factory
    try:
        result = asyncio.run(invoke(state, STATUS))
        assert not result.isError
        payload = result.structuredContent
        Draft202012Validator(GRAPH_OUTPUT_SCHEMA).validate(payload)
        assert payload["generation_id"] == manifest["generation_id"]
        caps = payload["data"]["indexed"]["capabilities"]
        assert caps["stored_format"] == "atlas-capabilities.v1"
        assert caps["languages"] == stored["capabilities"]["languages"]
        assert caps["producers"] == stored["capabilities"]["producers"]
        assert caps["limitations"] is None
        assert payload["data"]["source"]["state"] == "unavailable"
        assert old_db.read_bytes() == original and not writer.db_path.exists()
    finally:
        close(state)
