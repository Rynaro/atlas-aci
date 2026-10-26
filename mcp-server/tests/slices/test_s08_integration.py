"""Strict packed-source closure, failures and owned descriptor lifecycle."""

import asyncio
import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest
from test_s08_contract import NEW, OLD, make

from atlas_aci.codegraph import CodeGraph
from atlas_aci.config import Config
from atlas_aci.enforcement import ToolError
from atlas_aci.server import build_server_state, dispatch_tool_call
from atlas_aci.storage import evidence as ev
from atlas_aci.storage import snapshots as snap


@pytest.mark.parametrize(
    "mutation",
    [
        "bool",
        "negative",
        "overlap",
        "overflow",
        "duplicate",
        "trailing",
        "range",
        "foreign",
        "schema",
        "producer",
        "current",
    ],
)
@pytest.mark.parametrize("version", ["v1", "v2"])
def test_strict_manifest_rejects_corruption(tmp_path, mutation, version):
    if version == "v1":
        from test_s08_lease_footprint import restore_v1

        g, _fixture = restore_v1(tmp_path)
    else:
        _repo, g = make(tmp_path)
    with g.reader_lease() as reader:
        value = json.loads(json.dumps(reader._source_lease.manifest))
    if mutation == "bool":
        value["pack"]["index"][0]["offset"] = True
    elif mutation == "negative":
        value["pack"]["index"][0]["length"] = -1
    elif mutation == "overlap":
        value["pack"]["index"][0]["offset"] -= 1
    elif mutation == "overflow":
        value["pack"]["index"][0]["length"] = 2**64
    elif mutation == "duplicate":
        value["pack"]["index"].append(dict(value["pack"]["index"][0]))
    elif mutation == "trailing":
        value["pack"]["size"] += 1
    elif mutation == "range":
        if version == "v1":
            value["ranges"][0]["end"] = value["ranges"][0]["size"] + 1
        else:
            value["bindings"][0]["max_end"] = value["bindings"][0]["size"] + 1
    elif mutation == "foreign":
        value["bindings"][0]["path"] = "../outside.py"
    elif mutation == "schema":
        value["context"]["schema"]["epoch"] = True
    elif mutation == "producer":
        value["context"]["producer"] = {}
    else:
        value["current"][0]["sha256"] = "0" * 64
    with pytest.raises((ValueError, KeyError, TypeError)):
        snap.validate_manifest(value)
    g.close()


@pytest.mark.parametrize("mutation", ["foreign", "missing_current"])
@pytest.mark.parametrize("version", ["v1", "v2"])
def test_coherently_rehashed_foreign_binding_fails_before_exposure(tmp_path, mutation, version):
    if version == "v1":
        from test_s08_lease_footprint import restore_v1

        g, _fixture = restore_v1(tmp_path)
    else:
        _repo, g = make(tmp_path)
    with g.reader_lease() as reader:
        value = json.loads(json.dumps(reader._source_lease.manifest))
        selected_db = Path(reader.db.execute("PRAGMA database_list").fetchone()[2])
        assert selected_db.is_file()
    if mutation == "missing_current":
        value["current"] = (
            [r for r in value["current"] if r["path"] != "b.py"] if version == "v1" else []
        )
    else:
        for row in value["current"] + value["bindings"]:
            if row["path"] == "a.py":
                row["path"] = "foreign.py"
        value["current"].sort(key=lambda r: r["path"])
        for row in value.get("ranges", []):
            if row["path"] == "a.py":
                row["path"] = "foreign.py"
        value["bindings"].sort(key=lambda r: (r["path"], r["sha256"]))
    snap.validate_manifest(value)
    raw = ev.canonical(value).encode()
    identifier = hashlib.sha256(raw).hexdigest()
    (g.atlas_dir / "source-snapshots" / f"manifest.{identifier}.json").write_bytes(raw)
    with sqlite3.connect(selected_db.as_uri() + "?mode=rw", uri=True) as conn:
        inputs = json.loads(
            conn.execute("SELECT value FROM manifest WHERE key='generation_inputs'").fetchone()[0]
        )
        inputs["snapshot"] = {"state": "available", "id": identifier, "manifest_sha256": identifier}
        conn.execute(
            "UPDATE manifest SET value=? WHERE key='generation_inputs'", (ev.canonical(inputs),)
        )
        conn.execute(
            "UPDATE manifest SET value=? WHERE key='generation_id'", (snap.digest(inputs),)
        )
    with sqlite3.connect(selected_db.as_uri() + "?mode=ro", uri=True) as installed:
        assert (
            json.loads(
                installed.execute(
                    "SELECT value FROM manifest WHERE key='generation_inputs'"
                ).fetchone()[0]
            )
            == inputs
        )
        assert installed.execute("SELECT value FROM manifest WHERE key='generation_id'").fetchone()[
            0
        ] == snap.digest(inputs)
    assert (g.atlas_dir / "source-snapshots" / f"manifest.{identifier}.json").read_bytes() == raw
    with pytest.raises(ToolError, match="closure"), g.reader_lease():
        pytest.fail("incoherent snapshot exposed")
    g.close()


def test_prune_during_unexposed_acquisition_retries_latest(tmp_path, monkeypatch):
    _repo, g = make(tmp_path)
    original = snap.acquire
    triggered = []

    def racing(*args, **kwargs):
        if not triggered and kwargs.get("expected") is not None:
            triggered.append(True)
            g.build(overlays={"a.py": NEW})
            g.build(overlays={"a.py": b"def latest(): pass\n"})
        return original(*args, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(snap, "acquire", racing)
        with g.reader_lease() as reader:
            assert reader.read_source("a.py") == b"def latest(): pass\n"
            assert reader.search_symbol("latest")["definitions"]
    assert triggered == [True]
    g.close()


def test_historical_and_current_hash_selection(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    path = repo / "a.py"
    first = b"def B(): pass\ndef A(): B()\n"
    path.write_bytes(first)
    g = CodeGraph(repo)
    g.build()
    path.write_text("def broken(:\n pass\n")
    g.build()
    with g.reader_lease() as reader:
        assert reader.read_source("a.py") == path.read_bytes()
        assert reader.read_source("a.py", hashlib.sha256(first).hexdigest()) == first
        assert reader.query("callers_of:B")["historical_edges"]
    path.write_bytes(first)
    g.build(since="HEAD")
    with g.reader_lease() as reader:
        assert reader.read_source("a.py") == first
    g.close()


def test_one_pack_fd_per_lease_and_exactly_once_release(tmp_path):
    repo, g = make(tmp_path)
    for number in range(30):
        (repo / f"f{number}.py").write_text(f"def target{number}(): pass\n")
    g.build()
    baseline = len(os.listdir("/dev/fd"))
    with g.reader_lease() as first:
        db = first.db
        fd = first._source_lease.fd
        assert len(os.listdir("/dev/fd")) == baseline + 2
        with g.reader_lease() as second:
            other = second._source_lease.fd
            assert other != fd
            first.close()
            first.close()
            with pytest.raises(sqlite3.ProgrammingError):
                db.execute("SELECT 1")
            with pytest.raises(OSError):
                os.fstat(fd)
            assert second.read_source("f29.py") == b"def target29(): pass\n"
        with pytest.raises(OSError):
            os.fstat(other)
    assert len(os.listdir("/dev/fd")) == baseline
    g.close()


def test_controlled_cancellation_releases_actual_graph_and_pack(tmp_path, monkeypatch):
    import atlas_aci.server as server

    repo, g = make(tmp_path)
    config = Config(repo=repo, memex_root=tmp_path / "memex")
    enforcement, memex, factory = build_server_state(config)
    held = {}

    async def run():
        entered = asyncio.Event()
        waiting = asyncio.Event()

        async def parked(arguments, config, enforcement, reader):
            held.update(db=reader.db, fd=reader._source_lease.fd)
            entered.set()
            await waiting.wait()

        monkeypatch.setattr(server, "search_symbol", parked)
        task = asyncio.create_task(
            dispatch_tool_call(
                "search_symbol", {"name": "old_target"}, config, enforcement, memex, factory
            )
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    try:
        asyncio.run(run())
        with pytest.raises(sqlite3.ProgrammingError):
            held["db"].execute("SELECT 1")
        with pytest.raises(OSError):
            os.fstat(held["fd"])
    finally:
        factory.close()
        g.close()


@pytest.mark.parametrize("component", ["source", "store", "manifest", "pack"])
def test_symlinks_never_redirect_snapshot_or_source(tmp_path, component):
    repo, g = make(tmp_path)
    outside = tmp_path / "outside"
    outside.write_bytes(NEW)
    if component == "source":
        (repo / "a.py").unlink()
        (repo / "a.py").symlink_to(outside)
        before = g.db_path.read_bytes()
        with pytest.raises(OSError):
            g.build()
        assert g.db_path.read_bytes() == before
    else:
        with g.reader_lease() as reader:
            identifier = reader.snapshot_id
            pack = reader._source_lease.manifest["pack"]["sha256"]
        root = g.atlas_dir / "source-snapshots"
        if component == "store":
            saved = root.with_name("saved")
            root.rename(saved)
            root.symlink_to(saved, target_is_directory=True)
        else:
            file = root / (
                f"manifest.{identifier}.json" if component == "manifest" else f"pack.{pack}.bin"
            )
            raw = file.read_bytes()
            other = tmp_path / "copy"
            other.write_bytes(raw)
            file.unlink()
            file.symlink_to(other)
        with pytest.raises(ToolError), g.reader_lease():
            pytest.fail("redirected source lease exposed")
    assert outside.read_bytes() == NEW
    g.close()


def test_nonregular_pack_does_not_block_acquisition(tmp_path):
    import subprocess
    import sys

    repo, g = make(tmp_path)
    with g.reader_lease() as reader:
        sha = reader._source_lease.manifest["pack"]["sha256"]
    path = g.atlas_dir / "source-snapshots" / f"pack.{sha}.bin"
    path.unlink()
    os.mkfifo(path)
    code = """import sys
from pathlib import Path
from atlas_aci.codegraph import CodeGraph
from atlas_aci.enforcement import ToolError
print('ACQUIRE',flush=True)
try:
 with CodeGraph(Path(sys.argv[1]),read_only=True).reader_lease():
  raise AssertionError('exposed FIFO')
except ToolError as e:
 assert e.code=='SNAPSHOT_UNAVAILABLE'
 print('REJECTED',flush=True)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(repo)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout.readline().strip() == "ACQUIRE"
        output, errors = process.communicate(timeout=2)
        assert process.returncode == 0 and output.strip() == "REJECTED", errors
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        g.close()


def test_source_builder_allocation_failure_cleans_stage_and_fds(tmp_path, monkeypatch):
    _repo, g = make(tmp_path)
    before = g.db_path.read_bytes()
    count = len(os.listdir("/dev/fd"))
    original = snap.os.mkdir

    def failing(path, *args, **kwargs):
        if str(path).startswith(".sources-stage."):
            raise OSError("controlled source stage allocation")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(snap.os, "mkdir", failing)
        with pytest.raises(OSError, match="controlled source stage allocation"):
            g.build()
    assert g.db_path.read_bytes() == before
    assert not list(g.atlas_dir.glob("*.tmp.*"))
    assert not list(g.atlas_dir.glob(".sources-stage.*"))
    assert len(os.listdir("/dev/fd")) == count
    g.close()


def test_import_retains_only_previous_source_snapshot(tmp_path):
    _repo, g = make(tmp_path)
    with g.reader_lease() as held:
        g.build(overlays={"a.py": b"def second(): pass\n"})
        artifact = tmp_path / "artifact.jsonl"
        g.export_jsonl(artifact)
        g.import_jsonl(artifact)
        assert len(list((g.atlas_dir / "source-snapshots").glob("manifest.*.json"))) == 1
        assert held.read_source("a.py") == OLD
        with g.reader_lease() as imported:
            assert imported.snapshot_id is None
            with pytest.raises(ToolError):
                imported.read_source("a.py")
    g.close()


def test_closed_reader_cannot_reopen_source(tmp_path):
    _repo, g = make(tmp_path)
    with g.reader_lease() as reader:
        token = reader.snapshot_id
        reader.close()
        with pytest.raises(RuntimeError), reader.source_lease(token):
            pytest.fail("closed reader reopened")
    g.close()


def test_explicit_mapping_overlay_and_live_binary_compatibility(tmp_path, monkeypatch):
    from types import MappingProxyType

    repo, g = make(tmp_path)
    g.build(overlays=MappingProxyType({"new.py": b"def unsaved(): pass\n"}))
    assert not (repo / "new.py").exists()
    g.close()


def test_live_binary_is_not_read(tmp_path, monkeypatch):
    from pathlib import Path

    repo, g = make(tmp_path)
    binary = repo / "image.bin"
    binary.write_bytes(b"not decoded")
    config = Config(repo=repo, memex_root=tmp_path / "memex")
    enforcement, memex, factory = build_server_state(config)
    original = Path.read_bytes

    def no_binary_read(path):
        if path == binary:
            raise AssertionError("live binary must not be read")
        return original(path)

    try:
        with monkeypatch.context() as m:
            m.setattr(Path, "read_bytes", no_binary_read)
            result = asyncio.run(
                dispatch_tool_call(
                    "view_file",
                    {"path": "image.bin", "start_line": 1, "end_line": 2},
                    config,
                    enforcement,
                    memex,
                    factory,
                )
            )
        assert result["error"] == "BINARY_CONTENT" and "size=11" in result["message"]
    finally:
        factory.close()
        g.close()


def test_explicit_null_snapshot_never_falls_back_live(tmp_path):
    repo, g = make(tmp_path)
    config = Config(repo=repo, memex_root=tmp_path / "memex")
    enforcement, memex, factory = build_server_state(config)
    try:
        with pytest.raises(ToolError):
            asyncio.run(
                dispatch_tool_call(
                    "view_file",
                    {"path": "a.py", "start_line": 1, "end_line": 2, "snapshot_id": None},
                    config,
                    enforcement,
                    memex,
                    factory,
                )
            )
    finally:
        factory.close()
        g.close()


def test_snapshot_view_preserves_central_line_element_and_byte_caps(tmp_path):
    repo, g = make(tmp_path)
    content = b"one\ntwo\nthree\nfour\n"
    g.build(overlays={"window.py": content})
    config = Config(
        repo=repo, memex_root=tmp_path / "memex", max_lines_per_view=2, max_bound_field_elements=1
    )
    enforcement, memex, factory = build_server_state(config)
    try:
        with g.reader_lease() as reader:
            token = reader.snapshot_id
        result = asyncio.run(
            dispatch_tool_call(
                "view_file",
                {"path": "window.py", "start_line": 1, "end_line": 100, "snapshot_id": token},
                config,
                enforcement,
                memex,
                factory,
            )
        )
        assert result["lines"] == ["one\n"] and result["truncated"]
        assert result["truncated_fields"] == ["lines"] and result["returned_count"] == {"lines": 1}
        assert (
            result["source_mode"] == "snapshot"
            and result["source_sha256"] == hashlib.sha256(content).hexdigest()
        )
        config.max_response_bytes = 64
        with pytest.raises(ToolError):
            asyncio.run(
                dispatch_tool_call(
                    "view_file",
                    {"path": "window.py", "start_line": 1, "end_line": 1, "snapshot_id": token},
                    config,
                    enforcement,
                    memex,
                    factory,
                )
            )
    finally:
        factory.close()
        g.close()


def test_readonly_and_missing_modern_pack_never_repair_or_fallback(tmp_path, monkeypatch):
    repo, g = make(tmp_path)
    with g.reader_lease() as reader:
        token = reader.snapshot_id
        pack = reader._source_lease.manifest["pack"]["sha256"]
    (g.atlas_dir / "source-snapshots" / f"pack.{pack}.bin").unlink()
    before = {
        str(p.relative_to(g.atlas_dir)): (p.stat().st_mtime_ns, p.read_bytes())
        for p in g.atlas_dir.rglob("*")
        if p.is_file()
    }
    with pytest.raises(ToolError), g.reader_lease():
        pytest.fail("missing modern source accepted")
    with pytest.raises(ToolError), g.source_lease(token):
        pytest.fail("source fell back live")
    after = {
        str(p.relative_to(g.atlas_dir)): (p.stat().st_mtime_ns, p.read_bytes())
        for p in g.atlas_dir.rglob("*")
        if p.is_file()
    }
    assert before == after and (repo / "a.py").read_bytes() == OLD
    g.close()


def test_missing_index_preserves_structured_error_without_writes(tmp_path):
    repo = tmp_path / "empty"
    repo.mkdir()
    config = Config(repo=repo, memex_root=tmp_path / "memex")
    enforcement, memex, factory = build_server_state(config)
    try:
        with pytest.raises(ToolError) as caught:
            asyncio.run(
                dispatch_tool_call(
                    "search_symbol", {"name": "missing"}, config, enforcement, memex, factory
                )
            )
        assert caught.value.code == "INDEX_UNAVAILABLE"
        assert not (repo / ".atlas").exists()
    finally:
        factory.close()


def test_imported_history_keeps_explicit_unavailable_original_bytes(tmp_path):
    _repo, original = make(tmp_path)
    artifact = tmp_path / "original.jsonl"
    original.export_jsonl(artifact)
    target = tmp_path / "source-free"
    target.mkdir()
    imported = CodeGraph(target)
    imported.import_jsonl(artifact)
    failed = b"def broken(:\n pass\n"
    (target / "a.py").write_bytes(failed)
    imported.build()
    with imported.reader_lease() as reader:
        assert reader.snapshot_id and reader.read_source("a.py") == failed
        assert reader.search_symbol("old_target")["historical_definitions"]
        with pytest.raises(ToolError):
            reader.read_source("a.py", hashlib.sha256(OLD).hexdigest())
        binding = next(
            row
            for row in reader._source_lease.manifest["bindings"]
            if row["sha256"] == hashlib.sha256(OLD).hexdigest()
        )
        assert binding["available"] is False
    imported.close()
    original.close()
