"""Regression tests for issue #123: graph_query latency, status ceiling, cancellation.

Three defects, one file:

1. Every DSL call paid a full re-authentication of the published generation (every row, the whole
   source pack) and `callers_of`/`subclasses_of` an O(refs x edges) probe. The guards here assert
   *work done* (authentication runs, statements issued, query plans) — never wall-clock.
2. `op=status` materialized an unbounded per-file projection and its telemetry described the
   pre-bound payload. The guards assert the emitted bytes and the truncation signal.
3. Graph work ran inline in the event loop with no cancellation point. The guards run a genuinely
   unbounded SQLite statement and assert it is interrupted by a cancel and by the deadline while
   the loop stays responsive.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest

import atlas_aci.codegraph as codegraph_module
from atlas_aci.codegraph import CodeGraph, authentication_in_progress, clear_authentication_memo
from atlas_aci.config import Config
from atlas_aci.enforcement import Enforcement, ToolError
from atlas_aci.graph_work import GraphGuard, checkpoint
from atlas_aci.memex import Memex
from atlas_aci.protocol.graph import VERSION as GRAPH_VERSION
from atlas_aci.server import apply_central_bounds, call_tool_result, dispatch_tool_call

PROTOCOL = "2025-11-25"
RUNAWAY_SQL = (
    "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 1000000000000) "
    "SELECT count(*) FROM c"
)


def _rb(repo: Path, rel: str, content: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _repo(tmp_path: Path, files: int = 6) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _rb(repo, "app/hub.rb", "class Hub\n  def record_vote(n)\n  end\nend\n")
    for i in range(files):
        _rb(
            repo,
            f"app/m{i}.rb",
            f"class Mod{i} < Hub\n  def call\n    record_vote({i})\n    Hub.new\n  end\nend\n",
        )
    return repo


def _state(
    tmp_path: Path, *, files: int = 6, **config_overrides: Any
) -> tuple[Config, Enforcement, Memex, CodeGraph]:
    repo = _repo(tmp_path, files)
    config = Config(repo=repo, memex_root=tmp_path / "memex", **config_overrides)
    CodeGraph(repo=repo).build()
    clear_authentication_memo()
    graph = CodeGraph(repo=repo, read_only=True, query_limit=config.max_bound_field_elements + 1)
    return config, Enforcement(config), Memex(config.memex_root), graph


@pytest.fixture(autouse=True)
def _fresh_memo() -> Any:
    clear_authentication_memo()
    yield
    clear_authentication_memo()


async def _legacy(
    config: Config, enforcement: Enforcement, memex: Memex, graph: CodeGraph, dsl: str
) -> dict[str, Any]:
    return await dispatch_tool_call(
        "graph_query", {"query": dsl}, config, enforcement, memex, graph
    )


async def _call(
    config: Config,
    enforcement: Enforcement,
    memex: Memex,
    graph: CodeGraph,
    arguments: dict[str, Any],
) -> Any:
    return await call_tool_result(
        "graph_query", arguments, config, enforcement, memex, graph, protocol_version=PROTOCOL
    )


# ---------------------------------------------------------------------------------------------
# 1. Latency: fixed per-query cost is gone, and cost no longer scales with the corpus.
# ---------------------------------------------------------------------------------------------


async def test_generation_is_authenticated_once_not_per_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The O(corpus) authentication (`_generation_data` + binding closure + pack hashing) runs on
    the first lease only; every later call, for every DSL verb, reuses the verdict."""
    config, enforcement, memex, graph = _state(tmp_path)
    full = {"generation": 0, "binding": 0}
    real_generation = CodeGraph._generation_data
    real_binding = CodeGraph._snapshot_binding

    def counting_generation(self: CodeGraph, *args: Any, **kwargs: Any) -> Any:
        full["generation"] += 1
        return real_generation(self, *args, **kwargs)

    def counting_binding(self: CodeGraph, *args: Any, **kwargs: Any) -> Any:
        full["binding"] += 1
        return real_binding(self, *args, **kwargs)

    monkeypatch.setattr(CodeGraph, "_generation_data", counting_generation)
    monkeypatch.setattr(CodeGraph, "_snapshot_binding", counting_binding)

    verbs = [
        "definitions_of:Hub",
        "callers_of:record_vote",
        "subclasses_of:Hub",
        "god_nodes:",
        "communities:",
        "rationale:",
    ]
    for dsl in verbs:
        await _legacy(config, enforcement, memex, graph, dsl)
    first_pass = dict(full)
    for dsl in verbs * 3:
        await _legacy(config, enforcement, memex, graph, dsl)

    assert first_pass["generation"] >= 1 and first_pass["binding"] >= 1
    assert full == first_pass, "a warm call re-authenticated the generation"
    graph.close()


async def test_leased_queries_do_not_revalidate_observations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_historical_projection` used to re-parse and re-validate every observation against every
    occurrence/evidence row on EACH search (twice per definitions_of)."""
    config, enforcement, memex, graph = _state(tmp_path)
    await _legacy(config, enforcement, memex, graph, "definitions_of:Hub")  # warm the memo
    validations: list[bool] = []
    real = CodeGraph._observation_records

    def counting(self: CodeGraph, conn: Any, validate: bool = True, *a: Any, **k: Any) -> Any:
        validations.append(validate)
        return real(self, conn, validate, *a, **k)

    monkeypatch.setattr(CodeGraph, "_observation_records", counting)
    for dsl in ("definitions_of:Hub", "callers_of:record_vote", "rationale:"):
        await _legacy(config, enforcement, memex, graph, dsl)
    assert validations == [], "a leased query re-read the observation records"
    graph.close()


def _statements(reader: CodeGraph, run: Any) -> list[str]:
    seen: list[str] = []
    reader.db.set_trace_callback(seen.append)
    try:
        run()
    finally:
        reader.db.set_trace_callback(None)
    return seen


@pytest.mark.parametrize("call_sites", [3, 40])
def test_point_lookup_statement_count_is_independent_of_corpus_size(
    tmp_path: Path, call_sites: int
) -> None:
    """Cost scales with the answer, not the corpus: the number of SQL statements for a point
    lookup is the same whether the name has 3 or 40 references (the old unresolved-reference
    count probed `edges` once per reference)."""
    _config, _enforcement, _memex, graph = _state(tmp_path, files=call_sites)
    with graph.reader_lease() as reader:
        counts = {
            dsl: len(_statements(reader, lambda dsl=dsl: reader.query(dsl)))
            for dsl in ("definitions_of:Hub", "callers_of:record_vote", "subclasses_of:Hub")
        }
    # Fixed budget per verb, however large the corpus. Recorded at 3 call sites; a regression to
    # per-reference statements would scale with `call_sites` and blow straight through it.
    assert counts["definitions_of:Hub"] <= 6
    assert counts["callers_of:record_vote"] <= 6
    assert counts["subclasses_of:Hub"] <= 6
    graph.close()


def test_unresolved_reference_count_never_probes_per_reference(tmp_path: Path) -> None:
    """The old statement was a correlated NOT EXISTS: for EVERY reference of a name it probed
    `edges` through an index that only narrows to edges of the same name, i.e. refs x edges work
    (measured: 3.5 s at 2,000 references to one name on a 1.2M-row table; effectively unbounded
    at the tens of thousands a common name has). A fixture this small does not make the planner
    pick the bad plan, so pin the SHAPE instead: only flat, index-driven selects run, and the
    per-reference matching is done outside SQL."""
    _config, _enforcement, _memex, graph = _state(tmp_path, files=8)
    with graph.reader_lease() as reader:
        statements = _statements(
            reader, lambda: reader.unresolved_ref_count("record_vote", ("call", "construct"))
        )
        selects = [sql for sql in statements if sql.lstrip().upper().startswith("SELECT")]
        assert selects, statements
        for sql in selects:
            assert "EXISTS" not in sql.upper() and sql.upper().count("SELECT") == 1, sql
            plan = " | ".join(row[3] for row in reader.db.execute("EXPLAIN QUERY PLAN " + sql))
            assert "USING" in plan and "SCAN" not in plan, (sql, plan)
    graph.close()


def test_point_lookups_use_indexes(tmp_path: Path) -> None:
    """Pin the access paths: name lookups are index SEARCHes, never table SCANs."""
    _config, _enforcement, _memex, graph = _state(tmp_path)
    with graph.reader_lease() as reader:
        plans = {
            "symbols": ("SELECT * FROM symbols WHERE name = ? LIMIT ?", ("Hub", 201)),
            "refs": ("SELECT * FROM refs WHERE callee_name = ? LIMIT ?", ("record_vote", 201)),
            "edges": (
                "SELECT * FROM edges WHERE callee_name = ? AND relation IN (?, ?) "
                "ORDER BY source_path, source_line, source_col, callee_name LIMIT ?",
                ("record_vote", "call", "construct", 201),
            ),
            "unresolved": (
                "SELECT source_path, source_line, source_col, lang, relation FROM edges "
                "WHERE callee_name = ?",
                ("record_vote",),
            ),
        }
        for label, (sql, params) in plans.items():
            plan = " | ".join(
                row[3] for row in reader.db.execute("EXPLAIN QUERY PLAN " + sql, params)
            )
            assert "USING INDEX" in plan or "USING COVERING INDEX" in plan, (label, plan)
            assert "SCAN" not in plan, (label, plan)
    graph.close()


def test_unresolved_ref_count_matches_the_original_sql(tmp_path: Path) -> None:
    """The linear rewrite is an exact replacement for the correlated NOT EXISTS it replaced,
    including the `call` <-> `construct` relabeling rule."""
    _config, _enforcement, _memex, graph = _state(tmp_path, files=8)
    original = (
        "SELECT COUNT(*) FROM refs r WHERE callee_name = ? AND relation IN ({ph}) "
        "AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.source_path=r.path "
        "AND e.source_line=r.line AND e.source_col=r.col AND e.lang=r.lang "
        "AND e.callee_name=r.callee_name AND (e.relation=r.relation "
        "OR (r.relation='call' AND e.relation='construct')))"
    )
    with graph.reader_lease() as reader:
        # Drop a few edges so some references are genuinely unresolved.
        names = [r[0] for r in reader.db.execute("SELECT DISTINCT callee_name FROM refs")]
        assert names
        for relations in (("call", "construct"), ("superclass", "include", "extend", "prepend")):
            for name in names:
                sql = original.format(ph=",".join("?" for _ in relations))
                expected = reader.db.execute(sql, (name, *relations)).fetchone()[0]
                assert reader.unresolved_ref_count(name, relations) == expected, (name, relations)
    graph.close()


async def test_memo_follows_a_reindex(tmp_path: Path) -> None:
    """A re-published generation is a different file: the memo must not serve the old one."""
    config, enforcement, memex, graph = _state(tmp_path)
    before = await _legacy(config, enforcement, memex, graph, "definitions_of:Fresh")
    assert before["definitions"] == []
    _rb(config.repo, "app/fresh.rb", "class Fresh\nend\n")
    CodeGraph(repo=config.repo).build()
    after = await _legacy(config, enforcement, memex, graph, "definitions_of:Fresh")
    assert [d["name"] for d in after["definitions"]] == ["Fresh"]
    assert after["generation_id"] != before["generation_id"]
    graph.close()


async def test_in_place_tampering_forces_full_reauthentication(tmp_path: Path) -> None:
    """The memo is keyed by the identity of the exact file verified; editing that file in place
    must not keep serving the old verdict."""
    config, enforcement, memex, graph = _state(tmp_path)
    await _legacy(config, enforcement, memex, graph, "definitions_of:Hub")
    db_path = graph.db_path
    writer = sqlite3.connect(db_path)
    writer.execute("UPDATE manifest SET value = 'tampered' WHERE key = 'generation_id'")
    writer.commit()
    writer.close()
    result = await call_tool_result(
        "graph_query",
        {"query": "definitions_of:Hub"},
        config,
        enforcement,
        memex,
        graph,
        protocol_version=PROTOCOL,
    )
    assert result.isError
    assert json.loads(result.content[0].text)["error"] in {
        "INDEX_UNAVAILABLE",
        "SNAPSHOT_UNAVAILABLE",
    }
    graph.close()


async def test_whole_graph_analysis_is_memoized_per_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, enforcement, memex, graph = _state(tmp_path)
    calls = {"n": 0}
    real = CodeGraph.god_nodes

    def counting(self: CodeGraph) -> Any:
        calls["n"] += 1
        return real(self)

    monkeypatch.setattr(CodeGraph, "god_nodes", counting)
    first = await _legacy(config, enforcement, memex, graph, "god_nodes:")
    second = await _legacy(config, enforcement, memex, graph, "god_nodes:")
    assert calls["n"] == 1
    assert first["god_nodes"] == second["god_nodes"]
    assert first["resolved_edge_count"] == second["resolved_edge_count"]
    graph.close()


# ---------------------------------------------------------------------------------------------
# 2. status: bounded at the source, bounded at the chokepoint, honest telemetry.
# ---------------------------------------------------------------------------------------------

STATUS = {"schema_version": GRAPH_VERSION, "op": "status"}


async def test_status_is_bounded_with_truncation_signalled(tmp_path: Path) -> None:
    config, enforcement, memex, graph = _state(tmp_path, files=12, max_bound_field_elements=5)
    result = await _call(config, enforcement, memex, graph, dict(STATUS))
    payload = json.loads(result.content[0].text)
    assert payload["status"] == "ok", payload
    files = payload["data"]["completeness"]["files"]
    assert len(files) == 5
    lists = payload["bounds"]["lists"]
    listed = lists["/data/completeness/files"]
    assert listed["total_count"] >= 13 and listed["returned_count"] == 5 and listed["truncated"]
    assert payload["bounds"]["truncated"] is True
    assert payload["data"]["completeness"]["total_count"] == listed["total_count"]
    assert lists["/data/source/references"]["truncated"] is True
    graph.close()


async def test_status_projection_is_built_bounded_not_trimmed_afterwards(tmp_path: Path) -> None:
    """The producer must not materialize one row per file just to have them cut off."""
    _config, _enforcement, _memex, graph = _state(tmp_path, files=12)
    with graph.reader_lease() as reader:
        bounded = reader.published_status(limit=4)
        full = reader.published_status()
    files = bounded["completeness"]["files"]
    assert len(files) == 4 and files.total == len(full["completeness"]["files"])
    assert list(files) == full["completeness"]["files"][:4]
    refs = bounded["source"]["references"]
    assert len(refs) == 4 and refs.total == len(full["source"]["references"])
    assert list(refs) == full["source"]["references"][:4]
    assert bounded["completeness"]["stale_count"] == full["completeness"]["stale_count"]
    graph.close()


async def test_status_never_exceeds_the_absolute_ceiling(tmp_path: Path) -> None:
    """An oversized status shrinks to fit the ceiling (and says so); it is never emitted over it."""
    config, enforcement, memex, graph = _state(
        tmp_path, files=60, max_bound_field_elements=500, max_response_bytes=24 * 1024
    )
    result = await _call(config, enforcement, memex, graph, dict(STATUS))
    emitted = result.model_dump_json(by_alias=True, exclude_none=True).encode()
    assert len(emitted) <= config.max_response_bytes
    payload = json.loads(result.content[0].text)
    assert payload["status"] == "ok", payload
    assert payload["bounds"]["truncated"] is True
    returned = payload["bounds"]["lists"]["/data/completeness/files"]["returned_count"]
    assert 0 < returned < 61
    graph.close()


def test_central_chokepoint_applies_the_ceiling_to_every_response_shape(tmp_path: Path) -> None:
    """`apply_central_bounds` used to return any non-dict result untouched — no shape may skip
    the absolute byte ceiling."""
    config = Config(repo=tmp_path, memex_root=tmp_path / "memex", max_response_bytes=1024)
    enforcement = Enforcement(config)
    for oversized in ("x" * 4096, ["y" * 100] * 50, b"z" * 4096):
        with pytest.raises(ToolError) as excinfo:
            apply_central_bounds("graph_query", {"query": "god_nodes:"}, oversized, enforcement)
        assert excinfo.value.code == "RESPONSE_TOO_LARGE"
    assert apply_central_bounds("graph_query", {}, "ok", enforcement) == "ok"


async def test_telemetry_reports_emitted_bytes_and_overflow(tmp_path: Path) -> None:
    config, enforcement, memex, graph = _state(tmp_path, files=12, max_bound_field_elements=5)
    result = await _call(config, enforcement, memex, graph, dict(STATUS))
    emitted = len(result.model_dump_json(by_alias=True, exclude_none=True).encode())
    record = enforcement.records[-1]
    assert record.tool == "graph_query"
    assert record.bytes_out == emitted, "telemetry must describe the emitted result"
    assert record.overflow is True, "a truncated result must not be logged overflow=False"

    small = await _call(config, enforcement, memex, graph, {"query": "definitions_of:Nothing"})
    record = enforcement.records[-1]
    assert record.bytes_out == len(small.model_dump_json(by_alias=True, exclude_none=True).encode())
    assert record.overflow is False
    graph.close()


# ---------------------------------------------------------------------------------------------
# 3. Cancellation and the server-side deadline.
# ---------------------------------------------------------------------------------------------


class _RunawayQuery:
    """Patches CodeGraph.query with a statement that would run for days; records its fate."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.stopped = threading.Event()
        self.error: BaseException | None = None

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        outer = self

        def runaway(reader: CodeGraph, dsl: str) -> dict[str, Any]:
            outer.started.set()
            try:
                reader.db.execute(RUNAWAY_SQL).fetchone()
            except BaseException as exc:
                outer.error = exc
                raise
            finally:
                outer.stopped.set()
            return {"definitions": [], "references": []}

        monkeypatch.setattr(CodeGraph, "query", runaway)


async def test_cancellation_interrupts_the_running_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, enforcement, memex, graph = _state(tmp_path, graph_query_deadline_s=0)
    runaway = _RunawayQuery()
    runaway.install(monkeypatch)
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    ticking = asyncio.create_task(ticker())
    task = asyncio.create_task(_legacy(config, enforcement, memex, graph, "definitions_of:Hub"))
    deadline = time.monotonic() + 10
    while not runaway.started.is_set():
        assert time.monotonic() < deadline, "the query never started"
        await asyncio.sleep(0.01)
    # The event loop is not held by the query: the ticker keeps running while it burns CPU.
    before = ticks
    await asyncio.sleep(0.3)
    assert ticks - before >= 5, "the graph query is blocking the event loop"

    cancelled_at = time.monotonic()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # The statement was actually stopped (not left spinning on a worker), promptly.
    assert runaway.stopped.is_set()
    assert isinstance(runaway.error, sqlite3.OperationalError), runaway.error
    assert time.monotonic() - cancelled_at < 5
    ticking.cancel()
    graph.close()


async def test_deadline_returns_a_structured_error_and_stops_the_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, enforcement, memex, graph = _state(tmp_path, graph_query_deadline_s=0.5)
    runaway = _RunawayQuery()
    runaway.install(monkeypatch)

    started = time.monotonic()
    result = await _call(config, enforcement, memex, graph, {"query": "definitions_of:Hub"})
    elapsed = time.monotonic() - started

    payload = json.loads(result.content[0].text)
    assert result.isError
    assert payload["error"] == "GRAPH_DEADLINE_EXCEEDED"
    assert payload["retry_hint"] == "narrower_scope"
    assert elapsed < 5, "the deadline did not bound the call"
    assert runaway.stopped.is_set(), "the worker was left running after the deadline"
    assert enforcement.records[-1].overflow is True

    monkeypatch.undo()  # the pool and the memoized lease are healthy afterwards
    ok = await _call(config, enforcement, memex, graph, {"query": "definitions_of:Hub"})
    assert not ok.isError
    assert [d["name"] for d in json.loads(ok.content[0].text)["definitions"]] == ["Hub"]
    graph.close()


async def test_deadline_applies_to_structured_operations_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, enforcement, memex, graph = _state(tmp_path, graph_query_deadline_s=0.4)
    runaway = _RunawayQuery()

    def runaway_status(reader: CodeGraph, limit: int | None = None) -> dict[str, Any]:
        runaway.started.set()
        try:
            reader.db.execute(RUNAWAY_SQL).fetchone()
        finally:
            runaway.stopped.set()
        return {}

    monkeypatch.setattr(CodeGraph, "published_status", runaway_status)
    result = await _call(config, enforcement, memex, graph, dict(STATUS))
    payload = json.loads(result.content[0].text)
    assert result.isError and payload["status"] == "error"
    assert payload["error"]["code"] == "GRAPH_DEADLINE_EXCEEDED"
    assert runaway.stopped.is_set()
    graph.close()


async def test_a_slow_cold_authentication_hits_the_deadline_and_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, enforcement, memex, graph = _state(tmp_path, graph_query_deadline_s=0.3)
    release = threading.Event()
    real = CodeGraph._authenticate_full

    def slow(self: CodeGraph) -> Any:
        release.wait(10)
        return real(self)

    monkeypatch.setattr(CodeGraph, "_authenticate_full", slow)
    started = time.monotonic()
    result = await _call(config, enforcement, memex, graph, {"query": "definitions_of:Hub"})
    assert time.monotonic() - started < 5
    payload = json.loads(result.content[0].text)
    assert payload["error"] == "GRAPH_DEADLINE_EXCEEDED"
    assert "still being authenticated" in payload["message"]
    assert authentication_in_progress()

    release.set()  # the single flight finishes in the background and warms the memo
    for _ in range(200):
        if not authentication_in_progress():
            break
        await asyncio.sleep(0.05)
    assert not authentication_in_progress()
    ok = await _call(config, enforcement, memex, graph, {"query": "definitions_of:Hub"})
    assert not ok.isError
    graph.close()


async def test_event_loop_stays_responsive_during_a_cold_authentication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, enforcement, memex, graph = _state(tmp_path, graph_query_deadline_s=0)
    real = CodeGraph._authenticate_full

    def slow(self: CodeGraph) -> Any:
        time.sleep(0.5)
        return real(self)

    monkeypatch.setattr(CodeGraph, "_authenticate_full", slow)
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    ticking = asyncio.create_task(ticker())
    await _legacy(config, enforcement, memex, graph, "definitions_of:Hub")
    ticking.cancel()
    assert ticks >= 15, "reader acquisition blocked the event loop"
    graph.close()


# ---------------------------------------------------------------------------------------------
# Building blocks.
# ---------------------------------------------------------------------------------------------


def test_guard_deadline_and_checkpoint() -> None:
    guard = GraphGuard(0.05)
    assert not guard.should_stop()
    time.sleep(0.08)
    assert guard.should_stop() and guard.reason == "deadline"
    unbounded = GraphGuard(None)
    assert unbounded.remaining() is None and not unbounded.should_stop()
    unbounded.abort("cancelled")
    assert unbounded.should_stop() and unbounded.reason == "cancelled"
    checkpoint()  # outside guarded work: a no-op


def test_progress_handler_aborts_a_runaway_statement_in_a_thread() -> None:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    guard = GraphGuard(None)
    guard.attach(conn)
    outcome: list[BaseException] = []

    def work() -> None:
        try:
            conn.execute(RUNAWAY_SQL).fetchone()
        except BaseException as exc:
            outcome.append(exc)

    worker = threading.Thread(target=work)
    worker.start()
    time.sleep(0.1)
    guard.abort("cancelled")
    worker.join(5)
    assert not worker.is_alive()
    assert outcome and isinstance(outcome[0], sqlite3.OperationalError)
    conn.close()


def test_config_validates_the_new_knobs(tmp_path: Path) -> None:
    for bad in (-1, 99999, "20", True):
        with pytest.raises(ValueError):
            Config(repo=tmp_path, memex_root=tmp_path / "m", graph_query_deadline_s=bad)  # type: ignore[arg-type]
    for bad_workers in (0, 65, 1.5):
        with pytest.raises(ValueError):
            Config(repo=tmp_path, memex_root=tmp_path / "m", graph_workers=bad_workers)  # type: ignore[arg-type]
    assert Config(repo=tmp_path, memex_root=tmp_path / "m").graph_query_deadline_s == 20.0
    assert Config(repo=tmp_path, memex_root=tmp_path / "m", graph_query_deadline_s=0)


def test_module_exposes_the_memo_controls() -> None:
    assert callable(codegraph_module.clear_authentication_memo)


def test_serve_exposes_the_deadline_option() -> None:
    from click.testing import CliRunner

    from atlas_aci.__main__ import cli

    result = CliRunner().invoke(cli, ["serve", "--help"])
    assert result.exit_code == 0
    assert "--graph-query-deadline" in result.output
    bad = CliRunner().invoke(cli, ["serve", "--repo", ".", "--graph-query-deadline", "99999"])
    assert bad.exit_code != 0
