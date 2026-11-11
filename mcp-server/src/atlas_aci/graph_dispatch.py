"""Graph tool calls off the event loop, under a deadline (issue #123).

`graph_call` is `server.dispatch_tool_call`'s path for `search_symbol` and `graph_query`: it
acquires the reader lease and runs the blocking graph reads on `graph_work` workers under one
`GraphGuard`. Kept out of server.py so that module's frozen import header and anchors stay put.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from contextlib import ExitStack
from typing import Any

import structlog

from atlas_aci.codegraph import CodeGraph, authentication_in_progress
from atlas_aci.config import Config
from atlas_aci.enforcement import Enforcement, ToolError
from atlas_aci.graph_work import GraphGuard, GraphRunner, activate, deadline_error
from atlas_aci.protocol.graph import envelope, no_evidence_data

log = structlog.get_logger()


def _runner_for(config: Config, code_graph: CodeGraph) -> GraphRunner:
    runner = getattr(code_graph, "_graph_runner", None)
    if runner is None:
        runner = code_graph._graph_runner = GraphRunner(config.graph_workers)  # type: ignore[attr-defined]
    return runner  # type: ignore[no-any-return]


def _acquire_reader(code_graph: CodeGraph) -> tuple[Any, CodeGraph]:
    lease = code_graph.reader_lease()
    reader = lease.__enter__()
    return lease, reader


def _release_reader(acquired: tuple[Any, CodeGraph]) -> None:
    acquired[0].__exit__(None, None, None)


def start_graph_warmup(config: Config) -> threading.Thread:
    """Authenticate the published generation once, in the background, at server start.

    The first lease of a process pays the full, uncached authentication of the generation (every
    row and the source pack — tens of seconds at 10k+ files; see `codegraph._AUTH_MEMO`). Doing it
    here moves that cost off the first request: requests that arrive meanwhile wait on the same
    single flight and, if the deadline hits first, are told the index is still being
    authenticated. A repository with no index (or an unusable one) just logs and moves on; the
    per-request path reports it as it always did.
    """

    def warm() -> None:
        started = time.monotonic()
        # Its own read-only handle: the authentication memo is process-wide, so warming through
        # any CodeGraph on the same namespace warms the one the server uses.
        warmer = CodeGraph(
            repo=config.repo,
            read_only=True,
            query_limit=config.max_bound_field_elements + 1,
            graph_root=config.graph_root,
        )
        try:
            with warmer.reader_lease():
                pass
        except Exception as exc:
            log.info("graph_warmup_skipped", reason=type(exc).__name__)
            return
        finally:
            warmer.close()
        log.info("graph_warmup_done", ms=int((time.monotonic() - started) * 1000))

    thread = threading.Thread(target=warm, name="atlas-graph-warmup", daemon=True)
    thread.start()
    return thread


async def graph_call(
    stack: ExitStack,
    name: str,
    arguments: dict[str, Any],
    config: Config,
    enforcement: Enforcement,
    code_graph: CodeGraph,
    mode: str | None,
    structured: bool,
    tools: dict[str, Any],
) -> dict[str, Any]:
    """search_symbol / graph_query against a leased reader, off the event loop.

    Reader acquisition and every blocking graph read run on `graph_work` workers under one
    `GraphGuard`: a client disconnect (task cancellation) or the server-side deadline interrupts
    the in-flight SQLite statement and the worker is joined before the lease is closed.
    ``tools`` are the tool implementations as resolved by server.py at call time.
    """
    runner = _runner_for(config, code_graph)
    guard = GraphGuard(config.graph_query_deadline_s)

    try:
        acquired = await runner.run(
            guard,
            _acquire_reader,
            code_graph,
            drain=False,  # pure-Python authentication cannot be interrupted; see GraphRunner
            discard=_release_reader,
            warming=authentication_in_progress,
        )
    except ToolError as exc:
        if (
            exc.code == "GRAPH_DEADLINE_EXCEEDED"
            or mode != "structured"
            or arguments.get("op") != "status"
            or not code_graph.index_is_absent()
        ):
            raise
        if arguments.get("expected_generation_id") is not None:
            raise ToolError("SNAPSHOT_MISMATCH", "Requested generation does not match.") from None
        return envelope(no_evidence_data())
    stack.callback(_release_reader, acquired)
    reader = acquired[1]
    guard.attach(reader.db)
    try:
        if (
            mode == "structured"
            and arguments.get("expected_generation_id", reader.generation_id)
            != reader.generation_id
        ):
            raise ToolError("SNAPSHOT_MISMATCH", "Requested generation does not match.")
        remaining = guard.remaining()
        async with asyncio.timeout(remaining):
            # `activate` routes the tools' blocking reads (`run_blocking`) to the worker pool.
            with activate(runner, guard):
                if name == "search_symbol":
                    result = await tools["search_symbol"](arguments, config, enforcement, reader)
                elif mode == "structured" and arguments["op"] in {"context", "impact"}:
                    # The dispatch ExitStack owns (and closes) the served reader.
                    result = await tools["graph_query"](
                        arguments, config, enforcement, reader, structured=structured
                    )
                else:
                    result = await tools["graph_query"](arguments, config, enforcement, reader)
    except TimeoutError:
        raise deadline_error(guard) from None
    except sqlite3.OperationalError:
        if guard.should_stop():
            raise deadline_error(guard) from None
        raise
    finally:
        guard.detach(reader.db)
    if mode != "structured":
        result["generation_id"] = reader.generation_id
        result["snapshot_id"] = reader.snapshot_id
        result["source_available"] = reader.snapshot_id is not None
    return result
