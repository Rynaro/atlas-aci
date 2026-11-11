"""Cancellable, deadline-bounded graph work off the event loop (issue #123).

Graph reads are synchronous SQLite/Python code. Run inline in an ``async`` tool handler they have
no cancellation point: a client disconnect cannot stop them (the task is only cancelled at the next
``await``) and they stall every other request on the loop while they run. This module runs such
work on a small worker pool and gives it two ways to stop:

* a ``sqlite3`` progress handler plus ``Connection.interrupt()`` aborts a statement that is running
  inside SQLite; and
* ``checkpoint()`` is a cooperative poll for pure-Python loops (whole-graph analyses).

One ``GraphGuard`` per call carries the deadline and the abort reason from the event loop to the
worker. Nothing here writes anything: it only reads connections it is handed.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from typing import Any, TypeVar

from atlas_aci.enforcement import ToolError

T = TypeVar("T")

# SQLite calls the progress handler every N virtual-machine instructions. Small enough that a
# runaway scan notices an abort within a few milliseconds, large enough to be unmeasurable.
PROGRESS_OPS = 2000

DEADLINE_CODE = "GRAPH_DEADLINE_EXCEEDED"


class GraphAborted(Exception):
    """The guard stopped this work (deadline or cancellation). Never escapes ``GraphRunner``."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class GraphGuard:
    """Deadline plus abort flag shared by the event loop and one worker thread."""

    def __init__(self, deadline_s: float | None):
        self._deadline = (
            time.monotonic() + deadline_s if deadline_s is not None and deadline_s > 0 else None
        )
        self._reason: str | None = None
        self._lock = threading.Lock()
        self._connections: list[sqlite3.Connection] = []

    @property
    def reason(self) -> str | None:
        return self._reason

    def remaining(self) -> float | None:
        if self._deadline is None:
            return None
        return max(0.0, self._deadline - time.monotonic())

    def should_stop(self) -> bool:
        if self._reason is not None:
            return True
        if self._deadline is not None and time.monotonic() >= self._deadline:
            self._reason = self._reason or "deadline"
            return True
        return False

    def check(self) -> None:
        if self.should_stop():
            raise GraphAborted(self._reason or "deadline")

    def attach(self, conn: sqlite3.Connection) -> None:
        """Make every statement on ``conn`` abortable. Safe to call from any thread."""
        with self._lock:
            self._connections.append(conn)
            stop = self._reason is not None
        conn.set_progress_handler(lambda: 1 if self.should_stop() else 0, PROGRESS_OPS)
        if stop:
            self._interrupt(conn)

    def detach(self, conn: sqlite3.Connection) -> None:
        with self._lock:
            if conn in self._connections:
                self._connections.remove(conn)
        with suppress(sqlite3.Error):  # already closed
            conn.set_progress_handler(None, 0)

    def abort(self, reason: str) -> None:
        with self._lock:
            if self._reason is None:
                self._reason = reason
            connections = list(self._connections)
        for conn in connections:
            self._interrupt(conn)

    @staticmethod
    def _interrupt(conn: sqlite3.Connection) -> None:
        with suppress(sqlite3.Error):  # closed already; nothing running
            conn.interrupt()


_current: contextvars.ContextVar[GraphGuard | None] = contextvars.ContextVar(
    "atlas_graph_guard", default=None
)


def checkpoint() -> None:
    """Cooperative cancellation point for pure-Python loops. No-op outside guarded work."""
    guard = _current.get()
    if guard is not None:
        guard.check()


def current_guard() -> GraphGuard | None:
    return _current.get()


_active: contextvars.ContextVar[tuple[GraphRunner, GraphGuard] | None] = contextvars.ContextVar(
    "atlas_graph_active", default=None
)


@contextlib.contextmanager
def activate(runner: GraphRunner, guard: GraphGuard) -> Iterator[None]:
    """Make `run_blocking` offload to ``runner`` under ``guard`` for the current task."""
    token = _active.set((runner, guard))
    try:
        yield
    finally:
        _active.reset(token)


async def run_blocking(fn: Callable[..., T], *args: Any) -> T:
    """Run a blocking graph read: on the active worker pool under its guard when the call is
    served by `server._graph_call`, inline otherwise (CLI paths and direct unit tests)."""
    active = _active.get()
    if active is None:
        return fn(*args)
    runner, guard = active
    return await runner.run(guard, fn, *args)


def deadline_error(guard: GraphGuard, *, warming: bool = False) -> ToolError:
    if warming:
        return ToolError(
            DEADLINE_CODE,
            "The graph index is still being authenticated after startup. Retry shortly.",
            "none",
        )
    return ToolError(
        DEADLINE_CODE,
        "Graph query exceeded the server-side deadline and was stopped. Narrow the query.",
        "narrower_scope",
    )


class GraphRunner:
    """A worker pool plus the cancellation protocol around it."""

    def __init__(self, workers: int):
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="atlas-graph")

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _submit(
        self, guard: GraphGuard, fn: Callable[..., T], args: tuple[Any, ...]
    ) -> asyncio.Future[T]:
        context = contextvars.copy_context()
        context.run(_current.set, guard)

        def work() -> T:
            guard.check()  # a task that waited in the queue past its deadline never starts
            try:
                return fn(*args)
            except sqlite3.OperationalError as exc:
                if guard.should_stop():  # our own interrupt, not a real SQL failure
                    raise GraphAborted(guard.reason or "deadline") from exc
                raise

        return asyncio.get_running_loop().run_in_executor(self._executor, context.run, work)

    async def run(
        self,
        guard: GraphGuard,
        fn: Callable[..., T],
        *args: Any,
        drain: bool = True,
        discard: Callable[[T], None] | None = None,
        warming: Callable[[], bool] | None = None,
    ) -> T:
        """Run ``fn(*args)`` on a worker, honouring the guard's deadline and cancellation.

        ``drain=True`` (queries): on abort, wait for the worker to actually stop before returning,
        so the caller can close the connection the worker was using without racing it.
        ``drain=False`` (reader acquisition, whose pure-Python authentication cannot be
        interrupted): return at once and let ``discard`` release whatever the worker eventually
        produces.
        """
        future = self._submit(guard, fn, args)
        try:
            # asyncio.wait, not wait_for(shield(...)): timing out or being cancelled must leave the
            # worker's future alone (it is settled below) and must not log its late result.
            done, _ = await asyncio.wait({future}, timeout=guard.remaining())
        except asyncio.CancelledError:
            guard.abort("cancelled")
            await self._settle(future, drain, discard)
            raise
        if not done:
            guard.abort("deadline")
            await self._settle(future, drain, discard)
            raise deadline_error(guard, warming=bool(warming and warming()))
        try:
            return future.result()
        except GraphAborted:
            raise deadline_error(guard) from None

    @staticmethod
    async def _settle(
        future: asyncio.Future[Any], drain: bool, discard: Callable[[Any], None] | None
    ) -> None:
        if drain:
            # Keep waiting even if we are cancelled again: closing a connection under a running
            # worker is worse than a slightly late return. The abort flag bounds the wait.
            while not future.done():
                try:
                    await asyncio.wait({future})
                except asyncio.CancelledError:
                    continue
        if future.done():
            if not future.cancelled() and future.exception() is None and discard is not None:
                discard(future.result())
            return

        def late(done: asyncio.Future[Any]) -> None:
            if done.cancelled() or done.exception() is not None:
                return  # also marks the exception retrieved: no "never retrieved" noise
            if discard is not None:
                discard(done.result())

        future.add_done_callback(late)
