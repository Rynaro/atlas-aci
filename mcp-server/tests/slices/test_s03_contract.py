"""ACI-S03 generation lifecycle through real SQLite, parser and dispatcher."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest

import atlas_aci.server as server_module
from atlas_aci.codegraph import CodeGraph
from atlas_aci.config import Config
from atlas_aci.enforcement import ToolError
from atlas_aci.server import build_server_state, dispatch_tool_call


def setup(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "example.py").write_text("def old_name():\n    pass\n")
    writer = CodeGraph(repo=repo)
    writer.build()
    config = Config(repo=repo, memex_root=tmp_path / "memex")
    enforcement, memex, graph = build_server_state(config)
    return repo, writer, config, enforcement, memex, graph


async def test_new_request_new_generation(tmp_path: Path) -> None:
    repo, writer, config, enforcement, memex, graph = setup(tmp_path)
    old = await dispatch_tool_call(
        "search_symbol", {"name": "old_name"}, config, enforcement, memex, graph
    )
    assert old["definitions"]
    (repo / "example.py").write_text("def new_name():\n    pass\n")
    writer.build()
    assert graph.epoch_ok()
    new = await dispatch_tool_call(
        "search_symbol", {"name": "new_name"}, config, enforcement, memex, graph
    )
    print(json.dumps({"before": old, "after": new}, sort_keys=True))
    assert new["definitions"], "a new production request must not read the cached prior inode"
    assert old["generation_id"] != new["generation_id"]


@pytest.mark.parametrize("publisher", ["full", "incremental", "import"])
async def test_active_request_pinned(tmp_path: Path, publisher: str) -> None:
    repo, writer, config, enforcement, memex, graph = setup(tmp_path)
    entered, proceed = asyncio.Event(), asyncio.Event()

    async def request():
        with graph.reader_lease() as lease:
            before = lease.search_symbol("old_name")
            generation = lease.generation_id
            entered.set()
            await proceed.wait()
            after = lease.search_symbol("old_name")
            assert lease.generation_id == generation
            return before, after, generation

    pending = asyncio.create_task(request())
    await entered.wait()
    (repo / "example.py").write_text("def new_name():\n    pass\n")
    if publisher == "import":
        other = tmp_path / "other"
        other.mkdir()
        (other / "other.py").write_text("def new_name():\n    pass\n")
        exported = CodeGraph(repo=other)
        exported.build()
        artifact = tmp_path / "export.jsonl"
        exported.export_jsonl(artifact)
        writer.import_jsonl(artifact)
    else:
        writer.build(since="HEAD" if publisher == "incremental" else None)
    current = await dispatch_tool_call(
        "search_symbol", {"name": "new_name"}, config, enforcement, memex, graph
    )
    proceed.set()
    before, after, generation = await pending
    assert before["definitions"] and before == after
    assert current["definitions"]
    assert current["generation_id"] != generation


async def test_failed_publish(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, writer, config, enforcement, memex, graph = setup(tmp_path)
    first = await dispatch_tool_call(
        "search_symbol", {"name": "old_name"}, config, enforcement, memex, graph
    )
    original = CodeGraph._run_build
    (repo / "example.py").write_text("def new_name():\n    pass\n")

    def fail(self, connection, get_parser, since):
        original(self, connection, get_parser, since)
        raise RuntimeError("after staging changed, before publication")

    monkeypatch.setattr(CodeGraph, "_run_build", fail)
    for since in (None, "HEAD"):
        with pytest.raises(RuntimeError, match="after staging"):
            writer.build(since=since)
        result = await dispatch_tool_call(
            "search_symbol", {"name": "old_name"}, config, enforcement, memex, graph
        )
        assert result == first
        assert not list(writer.atlas_dir.glob(".graph.*.tmp.*"))
    monkeypatch.setattr(CodeGraph, "_run_build", original)
    writer.build(since="HEAD")
    new = await dispatch_tool_call(
        "search_symbol", {"name": "new_name"}, config, enforcement, memex, graph
    )
    assert new["definitions"]


async def test_same_epoch_replacement(tmp_path: Path) -> None:
    repo, writer, config, enforcement, memex, graph = setup(tmp_path)
    old = await dispatch_tool_call(
        "search_symbol", {"name": "old_name"}, config, enforcement, memex, graph
    )
    path = repo / "example.py"
    stat = path.stat()
    path.write_text("def new_name():\n    pass\n")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    result = writer.build(since="HEAD")
    assert result["files_indexed"] == 1
    new = await dispatch_tool_call(
        "search_symbol", {"name": "new_name"}, config, enforcement, memex, graph
    )
    assert new["definitions"]
    assert new["generation_id"] != old["generation_id"]


async def test_reader_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _repo, _writer, config, enforcement, memex, graph = setup(tmp_path)
    original = server_module.search_symbol
    opened = []
    arrived, proceed = asyncio.Event(), asyncio.Event()

    async def paused(arguments, cfg, enf, reader):
        result = await original(arguments, cfg, enf, reader)
        opened.append((reader, reader.db))
        arrived.set()
        await proceed.wait()
        return result

    monkeypatch.setattr(server_module, "search_symbol", paused)
    task = asyncio.create_task(
        dispatch_tool_call("search_symbol", {"name": "old_name"}, config, enforcement, memex, graph)
    )
    await arrived.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    reader, connection = opened[0]
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")
    with pytest.raises(RuntimeError, match="closed"):
        reader.db.execute("SELECT 1")
    reader.close()
    proceed.set()
    await dispatch_tool_call(
        "search_symbol", {"name": "old_name"}, config, enforcement, memex, graph
    )
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened[-1][1].execute("SELECT 1")

    async def failing(arguments, cfg, enf, reader):
        await original(arguments, cfg, enf, reader)
        opened.append((reader, reader.db))
        raise ValueError("tool failure")

    monkeypatch.setattr(server_module, "search_symbol", failing)
    with pytest.raises(ValueError, match="tool failure"):
        await dispatch_tool_call(
            "search_symbol", {"name": "old_name"}, config, enforcement, memex, graph
        )
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened[-1][1].execute("SELECT 1")


def test_generation_replay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, writer, *_ = setup(tmp_path)
    first = writer.generation_id
    writer.build()
    assert writer.generation_id == first
    writer.build(since="HEAD")
    assert writer.generation_id == first
    languages = CodeGraph(repo=repo, langs=["python"])
    languages.build(since="HEAD")
    assert languages.generation_id != first
    changed = languages.generation_id
    producer = languages._producer_inputs()
    monkeypatch.setattr(
        languages, "_producer_inputs", lambda: {**producer, "codegraph_sha256": "0" * 64}
    )
    result = languages.build(since="HEAD")
    assert result["files_indexed"] == 1
    assert languages.generation_id != changed
    other = tmp_path / "other"
    other.mkdir()
    (other / "example.py").write_bytes((repo / "example.py").read_bytes())
    other_graph = CodeGraph(repo=other)
    other_graph.build()
    assert other_graph.generation_id != first
    exported = tmp_path / "source.jsonl"
    writer.export_jsonl(exported)
    imported = CodeGraph(repo=tmp_path / "cold")
    imported.import_jsonl(exported)
    identifier = imported.generation_id
    imported.import_jsonl(exported)
    assert imported.generation_id == identifier
    assert identifier != first


def test_consumed_buffer_hash_and_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, writer, *_ = setup(tmp_path)
    path = repo / "example.py"
    source = path.read_bytes()
    original = writer._extract

    def edit_after_parse(tree, buffer, relative, language):
        path.write_text("def later_name():\n    pass\n")
        return original(tree, buffer, relative, language)

    monkeypatch.setattr(writer, "_extract", edit_after_parse)
    writer.build()
    inputs = json.loads(
        writer.db.execute("SELECT value FROM manifest WHERE key='generation_inputs'").fetchone()[0]
    )
    assert inputs["source_hashes"]["example.py"] == hashlib.sha256(source).hexdigest()
    assert writer.search_symbol("old_name")["definitions"]
    assert writer.search_symbol("later_name")["definitions"] == []
    prior = writer.generation_id
    monkeypatch.setattr(writer, "_extract", original)
    original_read = Path.read_bytes

    def fail_read(p):
        if p == path:
            raise OSError("eligible source unreadable")
        return original_read(p)

    monkeypatch.setattr(Path, "read_bytes", fail_read)
    with pytest.raises(OSError, match="eligible source"):
        writer.build(since="HEAD")
    assert writer.generation_id == prior


def test_legacy_missing_metadata_rebuild_and_import_inheritance(tmp_path: Path) -> None:
    repo, writer, *_ = setup(tmp_path)
    writer.db.execute("DELETE FROM manifest WHERE key='generation_id'")
    writer.db.commit()
    reader = CodeGraph(repo=repo, read_only=True)
    before = {p.name: p.read_bytes() for p in writer.atlas_dir.iterdir() if p.is_file()}
    with pytest.raises(ToolError) as missing, reader.reader_lease():
        pass
    assert missing.value.code == "INDEX_UNAVAILABLE"
    assert before == {p.name: p.read_bytes() for p in writer.atlas_dir.iterdir() if p.is_file()}
    assert writer.build(since="HEAD")["files_indexed"] == 1
    exported = tmp_path / "portable.jsonl"
    writer.export_jsonl(exported)
    writer.import_jsonl(exported)
    assert writer.build(since="HEAD")["files_indexed"] == 1
    assert writer.search_symbol("old_name")["definitions"]


def test_concurrent_readers_keep_their_own_generation(tmp_path: Path) -> None:
    import concurrent.futures
    import threading

    repo, writer, *_ = setup(tmp_path)
    acquired = threading.Barrier(3)
    proceed = threading.Event()

    def request():
        factory = CodeGraph(repo=repo, read_only=True)
        with factory.reader_lease() as lease:
            before = lease.search_symbol("old_name")
            identifier = lease.generation_id
            acquired.wait(timeout=10)
            assert proceed.wait(timeout=10)
            assert lease.search_symbol("old_name") == before
            return identifier, bool(before["definitions"])

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(request) for _ in range(2)]
        acquired.wait(timeout=10)
        (repo / "example.py").write_text("def new_name():\n    pass\n")
        writer.build()
        with CodeGraph(repo=repo, read_only=True).reader_lease() as current:
            assert current.search_symbol("new_name")["definitions"]
            new_id = current.generation_id
        proceed.set()
        results = [future.result(timeout=10) for future in pending]
    assert results[0] == results[1]
    assert results[0][1] and results[0][0] != new_id


def test_import_and_incremental_publishers_share_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import concurrent.futures
    import threading

    import atlas_aci.codegraph as module

    repo, writer, *_ = setup(tmp_path)
    old_id = writer.generation_id
    artifact = tmp_path / "portable.jsonl"
    writer.export_jsonl(artifact)
    (repo / "example.py").write_text("def new_name():\n    pass\n")
    ready, proceed, import_attempted, import_staged = (threading.Event() for _ in range(4))
    roles = threading.local()
    original_build, original_stage, original_flock = (
        CodeGraph._run_build,
        CodeGraph._stage_path,
        module.fcntl.flock,
    )

    def paused_build(self, connection, get_parser, since):
        result = original_build(self, connection, get_parser, since)
        ready.set()
        assert proceed.wait(timeout=10)
        return result

    def stage(self):
        if getattr(roles, "name", None) == "import":
            import_staged.set()
        return original_stage(self)

    def flock(fd, operation):
        if getattr(roles, "name", None) == "import" and operation == module.fcntl.LOCK_EX:
            import_attempted.set()
        return original_flock(fd, operation)

    monkeypatch.setattr(CodeGraph, "_run_build", paused_build)
    monkeypatch.setattr(CodeGraph, "_stage_path", stage)
    monkeypatch.setattr(module.fcntl, "flock", flock)

    def publish_build():
        roles.name = "build"
        return CodeGraph(repo=repo).build(since="HEAD")

    def publish_import():
        roles.name = "import"
        return CodeGraph(repo=repo).import_jsonl(artifact)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        build_future = pool.submit(publish_build)
        assert ready.wait(timeout=10)
        with CodeGraph(repo=repo, read_only=True).reader_lease() as during:
            assert during.generation_id == old_id
            assert during.search_symbol("old_name")["definitions"]
            assert during.search_symbol("new_name")["definitions"] == []
        import_future = pool.submit(publish_import)
        assert import_attempted.wait(timeout=10)
        assert not import_staged.is_set()
        proceed.set()
        build_future.result(timeout=10)
        import_future.result(timeout=10)
    assert import_staged.is_set()
    with CodeGraph(repo=repo, read_only=True).reader_lease() as after:
        assert after.search_symbol("old_name")["definitions"]
        assert after.generation_id != old_id


async def test_generation_metadata_is_byte_capped_and_releases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _repo, _writer, config, enforcement, memex, graph = setup(tmp_path)
    config.max_response_bytes = 100
    connections = []
    original = server_module.search_symbol

    async def observe(arguments, cfg, enf, reader):
        connections.append(reader.db)
        return await original(arguments, cfg, enf, reader)

    monkeypatch.setattr(server_module, "search_symbol", observe)
    with pytest.raises(ToolError) as error:
        await dispatch_tool_call(
            "search_symbol", {"name": "not_present"}, config, enforcement, memex, graph
        )
    assert error.value.code == "RESPONSE_TOO_LARGE"
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")


def test_staging_initialization_failure_cleans_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import atlas_aci.codegraph as module

    _repo, writer, *_ = setup(tmp_path)
    artifact = tmp_path / "portable.jsonl"
    writer.export_jsonl(artifact)
    previous = writer.generation_id
    with monkeypatch.context() as patch:
        patch.setattr(module, "SCHEMA", "THIS IS INVALID SQL")
        with pytest.raises(sqlite3.OperationalError):
            writer.import_jsonl(artifact)
    assert not list(writer.atlas_dir.glob(".graph.*.tmp.*"))
    assert writer.generation_id == previous


@pytest.mark.parametrize(
    "kind,field",
    [
        ("build", "configuration"),
        ("build", "producer"),
        ("build", "source_hashes"),
        ("import", "artifact"),
        ("import", "importer"),
    ],
)
def test_incomplete_self_consistent_provenance_is_unavailable(
    tmp_path: Path, kind: str, field: str
) -> None:
    repo, writer, *_ = setup(tmp_path)
    if kind == "import":
        artifact = tmp_path / "portable.jsonl"
        writer.export_jsonl(artifact)
        writer.import_jsonl(artifact)
    connection = writer.db
    inputs = json.loads(
        connection.execute("SELECT value FROM manifest WHERE key='generation_inputs'").fetchone()[0]
    )
    inputs[field] = {}
    raw = json.dumps(inputs, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    identifier = hashlib.sha256(raw.encode()).hexdigest()
    connection.executemany(
        "UPDATE manifest SET value=? WHERE key=?",
        [(raw, "generation_inputs"), (identifier, "generation_id")],
    )
    connection.commit()
    before = {
        str(p): (p.stat().st_mtime_ns, p.read_bytes())
        for p in writer.atlas_dir.rglob("*")
        if p.is_file()
    }
    with pytest.raises(ToolError) as error, CodeGraph(repo=repo, read_only=True).reader_lease():
        pass
    assert error.value.code == "INDEX_UNAVAILABLE"
    assert before == {
        str(p): (p.stat().st_mtime_ns, p.read_bytes())
        for p in writer.atlas_dir.rglob("*")
        if p.is_file()
    }
    assert writer.build(since="HEAD")["files_indexed"] == 1


@pytest.mark.parametrize("publisher", ["build", "import"])
def test_stage_open_failure_cleans_allocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, publisher: str
) -> None:
    _repo, writer, *_ = setup(tmp_path)
    artifact = tmp_path / "portable.jsonl"
    writer.export_jsonl(artifact)
    previous = writer.generation_id
    original_connect = sqlite3.connect

    def fail_stage(database, *args, **kwargs):
        if isinstance(database, Path) and database.name.startswith(".graph."):
            return original_connect(
                Path(str(database) + ".missing").as_uri() + "?mode=ro", uri=True
            )
        return original_connect(database, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", fail_stage)
        with pytest.raises(sqlite3.OperationalError):
            writer.build() if publisher == "build" else writer.import_jsonl(artifact)
    assert not list(writer.atlas_dir.glob(".graph.*.tmp.*"))
    assert writer.generation_id == previous
    writer.build(since="HEAD")


def test_empty_repository_and_source_free_empty_import(tmp_path: Path) -> None:
    repo = tmp_path / "empty"
    repo.mkdir()
    writer = CodeGraph(repo=repo)
    writer.build()
    first = writer.generation_id
    writer.build(since="HEAD")
    assert writer.generation_id == first
    with writer.reader_lease() as lease:
        assert lease.search_symbol("missing") == {"definitions": [], "references": []}
    artifact = tmp_path / "empty.jsonl"
    writer.export_jsonl(artifact)
    imported = CodeGraph(repo=tmp_path / "cold")
    imported.import_jsonl(artifact)
    with imported.reader_lease() as lease:
        assert lease.search_symbol("missing") == {"definitions": [], "references": []}
    identifier = imported.generation_id
    imported.import_jsonl(artifact)
    assert imported.generation_id == identifier


@pytest.mark.parametrize("outcome", ["normal", "error", "cancel", "invalid"])
async def test_request_handle_closed_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    _repo, writer, config, enforcement, memex, graph = setup(tmp_path)
    if outcome == "invalid":
        writer.db.execute("DELETE FROM manifest WHERE key='generation_id'")
        writer.db.commit()
    original_connect = sqlite3.connect
    original_tool = server_module.search_symbol
    connections, readers = [], []
    entered, proceed = asyncio.Event(), asyncio.Event()

    class Connection(sqlite3.Connection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.close_count = 0

        def close(self):
            self.close_count += 1
            super().close()

    def connect(*args, **kwargs):
        result = original_connect(*args, factory=Connection, **kwargs)
        connections.append(result)
        return result

    async def tool(arguments, cfg, enf, reader):
        result = await original_tool(arguments, cfg, enf, reader)
        readers.append(reader)
        if outcome == "error":
            raise ValueError("controlled error after real read")
        if outcome == "cancel":
            entered.set()
            await proceed.wait()
        return result

    monkeypatch.setattr(sqlite3, "connect", connect)
    monkeypatch.setattr(server_module, "search_symbol", tool)

    async def request():
        return await dispatch_tool_call(
            "search_symbol", {"name": "old_name"}, config, enforcement, memex, graph
        )

    if outcome == "invalid":
        with pytest.raises(ToolError):
            await request()
    elif outcome == "error":
        with pytest.raises(ValueError, match="controlled error"):
            await request()
    elif outcome == "cancel":
        pending = asyncio.create_task(request())
        await entered.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    else:
        assert (await request())["definitions"]
    assert len(connections) == 1 and connections[0].close_count == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")
    for reader in readers:
        reader.close()
    assert connections[0].close_count == 1
