"""Tests for the H3 schema-epoch DB substrate (D1).

Covers: the epoch-namespaced DB path, index-path-only sweep/rebuild, a
stale/mismatched epoch never triggering a write, the EXPECTED_DDL_HASH
pairing, the atomic-rename single-writer-lock build path, and the
rationale-relation DDL folded into this epoch (AC-A4-6).

The twentieth defect (checker/coordinator): "serve fails fast on epoch
mismatch" is true, but "fails fast" does not mean "at startup" — every
reader assumes it does. `serve` itself always starts unconditionally; it
is a TOOL CALL that touches the code graph (`search_symbol`,
`graph_query`) that checks the epoch and returns the structured
`INDEX_UNAVAILABLE` error, only at that point. The tests below that
exercise this walk the actual functions `run_stdio` wires together
(`build_server_state`, `dispatch_tool_call`) end to end, rather than
checking `CodeGraph.epoch_ok()` in isolation and trusting a prose
description of what happens above it — the exact gap that let a false
"fails fast [at startup]" claim survive in README.md/CHANGELOG.md
despite every criterion this file enforces passing cleanly.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from atlas_aci.codegraph import (
    EXPECTED_DDL_HASH,
    SCHEMA,
    SCHEMA_EPOCH,
    CodeGraph,
    ddl_hash,
)
from atlas_aci.config import Config
from atlas_aci.enforcement import ToolError
from atlas_aci.server import build_server_state, dispatch_tool_call


def _write_ruby(repo: Path, rel: str, content: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


# ---- AC-H-9 ----


def test_db_path_is_epoch_namespaced(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    graph = CodeGraph(repo=repo)
    assert graph.db_path.name == f"graph.{SCHEMA_EPOCH}.db"
    assert graph.db_path != repo / ".atlas" / "graph.db"
    assert isinstance(SCHEMA_EPOCH, int)


# ---- AC-H-10 ----


def test_index_sweeps_stale_epoch_files_serve_does_not(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_ruby(repo, "app/a.rb", "class A\nend\n")

    atlas_dir = repo / ".atlas"
    atlas_dir.mkdir()
    stale = atlas_dir / "graph.0.db"
    stale.write_bytes(b"stale-bytes-not-a-real-db")

    # serve (read_only) must never touch it, even on repeated checks.
    reader = CodeGraph(repo=repo, read_only=True)
    assert reader.epoch_ok() is False  # nothing built yet at the current epoch
    assert stale.exists()
    reader2 = CodeGraph(repo=repo, read_only=True)
    assert reader2.epoch_ok() is False
    assert stale.exists(), "serve/read-only paths must never sweep"

    # index (write path) sweeps it as part of build().
    writer = CodeGraph(repo=repo)
    writer.build()
    assert not stale.exists()
    assert writer.db_path.exists()


# ---- AC-H-11 ----


def test_index_rebuilds_serve_fails_fast_on_epoch_mismatch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_ruby(repo, "app/a.rb", "class A\nend\n")

    writer = CodeGraph(repo=repo)
    writer.build()
    assert writer.epoch_ok() is True

    # Corrupt the in-DB manifest epoch without touching the filename — the
    # F1 belt-and-suspenders scenario.
    conn = sqlite3.connect(writer.db_path)
    conn.execute("UPDATE manifest SET value = '999999' WHERE key = 'epoch'")
    conn.commit()
    conn.close()

    reader = CodeGraph(repo=repo, read_only=True)
    assert reader.epoch_ok() is False  # this is what makes the tool layer fail fast

    before = writer.db_path.stat().st_mtime_ns

    # index (write path) detects the mismatch and forces a full rebuild even
    # though `since` was requested.
    writer2 = CodeGraph(repo=repo)
    stats = writer2.build(since="HEAD")
    assert writer2.epoch_ok() is True
    assert stats["files_indexed"] == 1  # proves it was a full rebuild, not a no-op skip
    assert writer.db_path.stat().st_mtime_ns != before


# ---- AC-H-12 ----


def test_expected_ddl_hash_matches_current_ddl() -> None:
    assert ddl_hash(SCHEMA) == EXPECTED_DDL_HASH, (
        "SCHEMA changed without recomputing EXPECTED_DDL_HASH — bump "
        "SCHEMA_EPOCH and paste the new hash (see codegraph.py docstring)."
    )


# ---- AC-H-16 ----


def test_read_only_connection_is_actually_sqlite_mode_ro(tmp_path: Path) -> None:
    """Direct proof, not a chmod proxy (the checker's finding): the
    read-only CodeGraph's `db` connection is opened in SQLite's own
    ``mode=ro``, so a write attempt fails at the SQLite layer itself —
    independent of OS permission bits. This matters because root ignores
    mode bits entirely, and a real ``--read-only`` ``:ro`` bind mount
    enforces read-only via ``EROFS`` at the kernel/mount level, a mechanism
    ``chmod`` cannot simulate. `mode=ro` is what actually protects a
    deployment regardless of who the container runs as."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_ruby(repo, "app/a.rb", "class A\nend\n")
    writer = CodeGraph(repo=repo)
    writer.build()

    reader = CodeGraph(repo=repo, read_only=True)
    with pytest.raises(sqlite3.OperationalError):
        reader.db.execute("INSERT INTO manifest(key, value) VALUES ('x', 'y')")


def test_serve_read_only_mount_zero_writes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_ruby(repo, "app/a.rb", "class A\n  def call\n  end\nend\n")

    writer = CodeGraph(repo=repo)
    writer.build()
    atlas_dir = repo / ".atlas"

    before = {p: p.stat().st_mtime_ns for p in atlas_dir.rglob("*") if p.is_file()}
    before_names = {p.name for p in atlas_dir.iterdir()}

    # Simulate a `docker run --read-only -v repo:/repo:ro` mount.
    atlas_dir.chmod(0o555)
    for f in atlas_dir.iterdir():
        if f.is_file():
            f.chmod(0o444)
    try:
        reader = CodeGraph(repo=repo, read_only=True)
        assert reader.epoch_ok() is True
        result = reader.search_symbol("A")
        assert result["definitions"]

        after = {p: p.stat().st_mtime_ns for p in atlas_dir.rglob("*") if p.is_file()}
        after_names = {p.name for p in atlas_dir.iterdir()}
        assert before == after, "read-only serve path must perform zero writes under .atlas"
        assert before_names == after_names, "read-only serve path must create no new files"
    finally:
        atlas_dir.chmod(0o755)
        for f in atlas_dir.iterdir():
            if f.is_file():
                f.chmod(0o644)


def test_serve_fails_fast_on_mismatch_without_writing(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_ruby(repo, "app/a.rb", "class A\nend\n")

    writer = CodeGraph(repo=repo)
    writer.build()

    conn = sqlite3.connect(writer.db_path)
    conn.execute("UPDATE manifest SET value = '999999' WHERE key = 'epoch'")
    conn.commit()
    conn.close()

    atlas_dir = repo / ".atlas"
    before_names = sorted(p.name for p in atlas_dir.iterdir())

    atlas_dir.chmod(0o555)
    try:
        reader = CodeGraph(repo=repo, read_only=True)
        assert reader.epoch_ok() is False  # the tool layer turns this into a ToolError
        after_names = sorted(p.name for p in atlas_dir.iterdir())
        assert before_names == after_names, "epoch mismatch must never trigger a write"
    finally:
        atlas_dir.chmod(0o755)


# ---- The twentieth defect: the migration path, walked end to end ----
#
# The two tests below exercise `build_server_state`/`dispatch_tool_call` --
# the ACTUAL functions `run_stdio` wires together, the same ones every
# `test_server.py` dispatch test calls -- rather than asserting `epoch_ok()`
# in isolation and trusting a description of what happens above it. That
# gap is exactly what let README.md/CHANGELOG.md both claim "serve ...
# fails fast" in a way every reader took to mean "at startup," when the
# real sequence is: serve starts unconditionally; a tool call that
# touches the code graph is where the error actually surfaces.


async def test_serve_starts_on_a_stale_v04_tree_tool_call_returns_index_unavailable(
    tmp_path: Path,
) -> None:
    """The exact scenario a real v0.4.0-to-v2.0.0 upgrader hits: a bare
    `.atlas/graph.db` (the OLD, pre-epoch filename) with no epoch-named
    file alongside it. The current code never looks at that file at all
    -- its own epoch-named path simply doesn't exist, indistinguishable
    from "never indexed." Walks: (1) `build_server_state` -- what
    `run_stdio` calls before ever entering its stdio loop -- succeeds
    without raising, sweeping, or rebuilding; (2) a `search_symbol` AND a
    `graph_query` call each independently return a structured
    `INDEX_UNAVAILABLE` ToolError naming `atlas-aci index`; (3) zero
    writes under `.atlas` throughout, and the stale file is untouched."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_ruby(repo, "app/a.rb", "class A\nend\n")
    atlas_dir = repo / ".atlas"
    atlas_dir.mkdir()
    stale_content = "a stale pre-epoch v0.4.0 .atlas/graph.db artifact"
    (atlas_dir / "graph.db").write_text(stale_content)

    config = Config(repo=repo, memex_root=tmp_path / "memex")
    before_names = sorted(p.name for p in atlas_dir.iterdir())

    # "serve itself always starts" -- must not raise.
    enforcement, memex, code_graph = build_server_state(config)
    assert code_graph.epoch_ok() is False

    with pytest.raises(ToolError) as search_symbol_exc:
        await dispatch_tool_call(
            "search_symbol", {"name": "A"}, config, enforcement, memex, code_graph
        )
    assert search_symbol_exc.value.code == "INDEX_UNAVAILABLE"
    assert "atlas-aci index" in search_symbol_exc.value.message

    with pytest.raises(ToolError) as graph_query_exc:
        await dispatch_tool_call(
            "graph_query",
            {"query": "definitions_of:A"},
            config,
            enforcement,
            memex,
            code_graph,
        )
    assert graph_query_exc.value.code == "INDEX_UNAVAILABLE"
    assert "atlas-aci index" in graph_query_exc.value.message

    after_names = sorted(p.name for p in atlas_dir.iterdir())
    assert before_names == after_names, "a stale/missing-epoch serve path must write nothing"
    assert (atlas_dir / "graph.db").read_text() == stale_content, "the stale file must be untouched"


async def test_serve_starts_on_a_corrupt_manifest_epoch_tool_call_returns_index_unavailable(
    tmp_path: Path,
) -> None:
    """The AC-H-11 corrupt-manifest variant, walked the same end-to-end
    way: a real `graph.<epoch>.db` file present, but its in-DB manifest
    row disagrees with `SCHEMA_EPOCH`. Same result: serve starts, a tool
    call returns `INDEX_UNAVAILABLE`, nothing is written."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_ruby(repo, "app/a.rb", "class A\nend\n")

    writer = CodeGraph(repo=repo)
    writer.build()
    conn = sqlite3.connect(writer.db_path)
    conn.execute("UPDATE manifest SET value = '999999' WHERE key = 'epoch'")
    conn.commit()
    conn.close()

    config = Config(repo=repo, memex_root=tmp_path / "memex")
    atlas_dir = repo / ".atlas"
    before_names = sorted(p.name for p in atlas_dir.iterdir())

    enforcement, memex, code_graph = build_server_state(config)
    assert code_graph.epoch_ok() is False

    with pytest.raises(ToolError) as exc_info:
        await dispatch_tool_call(
            "search_symbol", {"name": "A"}, config, enforcement, memex, code_graph
        )
    assert exc_info.value.code == "INDEX_UNAVAILABLE"
    assert "atlas-aci index" in exc_info.value.message

    after_names = sorted(p.name for p in atlas_dir.iterdir())
    assert before_names == after_names, "a corrupt-manifest serve path must write nothing"


# ---- AC-H-17 ----


def test_concurrent_index_atomic_rename_no_corruption(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    for i in range(5):
        _write_ruby(repo, f"app/m{i}.rb", f"class M{i}\nend\n")

    errors: list[BaseException] = []

    def _run() -> None:
        try:
            CodeGraph(repo=repo).build()
        except BaseException as exc:  # want to see anything, not just Exception
            errors.append(exc)

    threads = [threading.Thread(target=_run) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent index runs raised: {errors!r}"

    final = CodeGraph(repo=repo, read_only=True)
    assert final.epoch_ok() is True
    conn = sqlite3.connect(f"file:{final.db_path}?mode=ro", uri=True)
    try:
        (integrity,) = conn.execute("PRAGMA integrity_check").fetchone()
        assert integrity == "ok"
        count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    finally:
        conn.close()
    assert count == 5

    # No leftover temp build files.
    atlas_dir = repo / ".atlas"
    leftovers = [p.name for p in atlas_dir.glob(".graph.*.db.tmp.*")]
    assert leftovers == []


# ---- AC-A1-1 (materialized edge table exists under the v2 epoch) ----


def test_edges_table_present_v2(tmp_path: Path) -> None:
    """A1 (P1): the v2-epoch schema includes a materialized call/
    inheritance edge table carrying source, target, relation type,
    confidence, and candidate set. Supersedes P0's
    `test_edges_table_not_yet_present`, which pinned the *opposite* fact
    (H3 must not implement A1 early) — that guard's job is done; A1 owns
    this DDL now."""
    repo = tmp_path / "repo"
    repo.mkdir()
    graph = CodeGraph(repo=repo)
    graph.build()
    tables = {
        row[0]
        for row in graph.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "edges" in tables

    cols = {row[1] for row in graph.db.execute("PRAGMA table_info(edges)").fetchall()}
    assert {
        "relation",
        "source_path",
        "source_line",
        "callee_name",
        "confidence",
        "target_path",
        "target_line",
        "target_name",
        "candidates",
    } <= cols


# ---- AC-A1-9 (refs.enclosing DROPPED) ----


def test_refs_enclosing_dropped_no_always_null_column(tmp_path: Path) -> None:
    """F10: the legacy always-NULL `refs.enclosing` column is omitted from
    the v2 schema entirely — not merely left unpopulated. Caller context is
    carried by the materialized edge's source endpoint instead
    (AC-A1-10)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    graph = CodeGraph(repo=repo)
    graph.build()

    cols = {row[1] for row in graph.db.execute("PRAGMA table_info(refs)").fetchall()}
    assert "enclosing" not in cols
    assert {"callee_name", "relation", "qualified", "path", "line"} <= cols


# ---- AC-A4-6 ----


def test_rationale_relation_separate_no_confidence(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    graph = CodeGraph(repo=repo)
    graph.build()

    cols = {row[1] for row in graph.db.execute("PRAGMA table_info(rationale)").fetchall()}
    assert "confidence" not in cols
    assert {"path", "line", "text", "target_path", "target_line", "target_name"} <= cols

    tables = {
        row[0]
        for row in graph.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {"rationale", "refs", "symbols"} <= tables, (
        "rationale must be its own relation, separate from refs/symbols"
    )
