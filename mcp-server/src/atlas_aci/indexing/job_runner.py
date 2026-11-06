"""The job process: an in-process build with checkpoints, cancellation and fault seams (ACI-S41).

``atlas-aci jobs submit|resume`` is the job process. The build runs here, in that process, through a
``CodeGraph`` subclass (``codegraph.py`` is frozen and fingerprinted, so it carries no hooks). No
subprocess or spawn call exists in this module: the process boundary, SIGKILL and SIGTERM semantics
are those of the CLI process itself.

Checkpoints are the content-addressed extraction-cache entries, recorded in an append-only,
fsynced ``checkpoint.log`` with a chained digest (a torn tail is ignored) plus a constant-size
``checkpoint.json`` summary. A resume re-runs the build: cache hits prove continuation.

Cancellation is a ``cancel`` marker file or SIGTERM (the handler only sets a flag). It is checked
while waiting for the writer lock, after every durable checkpoint write, after staging and right
before the commit (``os.replace``). After the commit nothing raises: the job completes with
``cancel: too_late``.

Fault seams (``JOB_SEAMS``) are callables in a hooks mapping. The environment variable
``ATLAS_ACI_JOB_FAULTS`` is read only by ``run_from_cli``.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import hashlib
import json
import os
import select
import signal
import sqlite3
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import structlog

from atlas_aci.codegraph import LANG_BY_EXT, CodeGraph
from atlas_aci.indexing import extraction_cache as xc
from atlas_aci.indexing import jobs
from atlas_aci.storage import evidence as ev
from atlas_aci.storage import generations as gen
from atlas_aci.storage import snapshots as snap

log = structlog.get_logger()

JOB_SEAMS = (
    "runner_start",
    "predecessor_captured",
    "checkpoint",
    "mid_publish",
    "staged",
    "pre_replace",
    "post_replace",
    "post_retire",
    "captured",
    "post_retain",
    "pre_receipt",
    "pre_complete",
)
FAULT_ENV = "ATLAS_ACI_JOB_FAULTS"
FAULT_ACTIONS = ("kill", "block", "raise")
FAULT_SPEC_CAP = 1024
CHECKPOINT_LOG_CAP = 256 * 1024 * 1024
SIGTERM_FLAG = threading.Event()
WRITER_POLL_SECONDS = 0.05


class JobCancelled(Exception):
    """Cancellation honoured before the commit."""


class InjectedFault(Exception):
    """A ``raise`` fault seam fired."""


class SourceChanged(Exception):
    """The staged generation does not hash the tree the job identity names."""


class CaptureFailed(Exception):
    """The generation could not be captured into a retained farm."""


class FaultSpecError(ValueError):
    """An invalid ``ATLAS_ACI_JOB_FAULTS`` document (the CLI maps it to a usage error)."""


# ------------------------------------------------------------------------------------ faults
def parse_faults(text: str | None) -> dict[str, Callable[[], None]] | None:
    """Turn a canonical-JSON fault spec ``{seam: {"action": A, "at": n?}}`` into hook callables."""
    if text is None or text == "":
        return None
    if len(text.encode("utf-8")) > FAULT_SPEC_CAP:
        raise FaultSpecError("invalid fault specification")
    try:
        spec = gen.load_json_bytes(text.encode("utf-8"))
    except ValueError:
        raise FaultSpecError("invalid fault specification") from None
    allowed = {*JOB_SEAMS, *gen.CAPTURE_SEAMS}
    if not isinstance(spec, dict) or not spec:
        raise FaultSpecError("invalid fault specification")
    hooks: dict[str, Callable[[], None]] = {}
    for seam, entry in spec.items():
        if (
            seam not in allowed
            or not isinstance(entry, dict)
            or not set(entry) <= {"action", "at"}
            or entry.get("action") not in FAULT_ACTIONS
            or ("at" in entry and (type(entry["at"]) is not int or entry["at"] < 1))
        ):
            raise FaultSpecError("invalid fault specification")
        hooks[seam] = _make_hook(seam, entry["action"], entry.get("at"))
    return hooks


def _make_hook(seam: str, action: str, at: int | None) -> Callable[[], None]:
    calls = [0]

    def hook() -> None:
        calls[0] += 1
        if at is not None and calls[0] != at:
            return
        if action == "kill":
            # PID 1 ignores a signal it has no handler for: exit hard right after.
            os.kill(os.getpid(), signal.SIGKILL)
            os._exit(137)
        if action == "raise":
            raise InjectedFault
        sys.stdout.write(f"ATLAS_JOB_SEAM {seam}\n")
        sys.stdout.flush()
        sys.stdin.readline()

    return hook


# -------------------------------------------------------------------------------------- run
class Run:
    """Everything one job attempt shares: the identity, the job document, the log and the hooks."""

    def __init__(
        self,
        spec: jobs.JobSpec,
        policy: gen.RetentionPolicy,
        store: jobs.JobStore,
        ident: dict[str, Any],
        doc: dict[str, Any],
        *,
        now: int,
        hooks: dict[str, Any] | None,
        signals: bool,
        continuing: bool,
    ):
        self.spec = spec
        self.ns = spec.ns
        self.policy = policy
        self.store = store
        self.ident = ident
        self.doc = doc
        self.now = now
        self.hooks = hooks
        self.signals = signals
        self.continuing = continuing
        self.count = 0
        self.chain = chain_start(store.job)
        self.log_fd = -1
        self.staged_gid: str | None = None
        self.stats: dict[str, Any] | None = None
        self.captured = False
        self.capture_error: BaseException | None = None
        self.mid_publish_fired = False

    def fire(self, name: str) -> None:
        gen.fire(self.hooks, name)

    def cancelled(self) -> bool:
        return self.store.cancel_requested() or (self.signals and SIGTERM_FLAG.is_set())

    def check_cancel(self) -> None:
        if self.cancelled():
            raise JobCancelled

    def persist(self, **changes: Any) -> None:
        self.doc = {**self.doc, **changes}
        self.store.write_job(self.doc)
        self.write_summary()

    def write_summary(self) -> None:
        self.store.write(
            "checkpoint.json",
            {
                "format": jobs.FORMAT_CHECKPOINT,
                "job_id": self.store.job,
                "attempt": self.doc["attempt"],
                "count": self.count,
                "chain": self.chain,
            },
        )

    def reset_log(self) -> None:
        self.close_log()
        for name in ("checkpoint.log", "checkpoint.json"):
            with contextlib.suppress(FileNotFoundError):
                os.unlink(self.store.path(name))
        self.count = 0
        self.chain = chain_start(self.store.job)

    def close_log(self) -> None:
        if self.log_fd >= 0:
            os.close(self.log_fd)
            self.log_fd = -1

    def append(self, line: dict[str, Any]) -> None:
        if self.log_fd < 0:
            created = gen.lstat_or_none(self.store.path("checkpoint.log")) is None
            self.log_fd = os.open(
                self.store.path("checkpoint.log"),
                os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o644,
            )
            if created:
                directory = gen.open_dir(self.store.dir)
                try:
                    gen.fsync_dir(directory)
                finally:
                    os.close(directory)
        number = self.count + 1
        entry = {"n": number, **line}
        chain = chain_next(self.chain, entry)
        os.write(self.log_fd, gen.canonical_line({**entry, "chain": chain}))
        os.fsync(self.log_fd)
        self.count = number
        self.chain = chain

    def checkpoint(self, expected: dict[str, Any], complete: bool) -> None:
        self.append(
            {
                "path": expected["path"],
                "source_sha256": expected["source_sha256"],
                "key": xc.digest(expected),
                "state": "complete" if complete else "failed",
            }
        )
        if self.count % self.policy.checkpoint_summary_interval == 0:
            self.write_summary()
        self.fire("checkpoint")
        self.check_cancel()

    def validate_checkpoints(self, graph: CodeGraph) -> int:
        """The longest valid prefix of the log, judged by the predicate ``lookup`` applies.

        A line is valid when it parses, chains, names a source whose hash matches, carries the key
        of the current build inputs, and its cache entry passes ``ExtractionCache.lookup`` in the
        recorded state. The log is truncated to that prefix (a torn tail is ignored)."""
        path = self.store.path("checkpoint.log")
        try:
            raw = gen.read_bounded(None, path, CHECKPOINT_LOG_CAP)
        except (FileNotFoundError, OverflowError, ValueError, OSError):
            self.count, self.chain = 0, chain_start(self.store.job)
            return 0
        base = graph._build_inputs()
        cache = xc.ExtractionCache(graph.atlas_dir)
        chain = chain_start(self.store.job)
        count = 0
        good = 0
        pieces = raw.split(b"\n")
        for piece in pieces[:-1]:
            try:
                item = gen.load_json_bytes(piece)
            except ValueError:
                break
            if not self._line_valid(item, count, chain, graph, base, cache):
                break
            count += 1
            chain = item["chain"]
            good += len(piece) + 1
        if good != len(raw):
            fd = os.open(path, os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                os.ftruncate(fd, good)
                os.fsync(fd)
            finally:
                os.close(fd)
        self.count, self.chain = count, chain
        return count

    @staticmethod
    def _line_valid(
        item: Any,
        count: int,
        chain: str,
        graph: CodeGraph,
        base: dict[str, Any],
        cache: xc.ExtractionCache,
    ) -> bool:
        keys = {"n", "path", "source_sha256", "key", "state", "chain"}
        if not isinstance(item, dict) or set(item) != keys:
            return False
        if type(item["n"]) is not int or item["n"] != count + 1:
            return False
        if item["state"] not in ("complete", "failed") or not ev.relative_path(item["path"]):
            return False
        body = {k: v for k, v in item.items() if k != "chain"}
        if item["chain"] != chain_next(chain, body):
            return False
        lang = LANG_BY_EXT.get(Path(item["path"]).suffix)
        if lang is None:
            return False
        try:
            source, _info = snap.safe_source(graph.repo, item["path"])
        except (OSError, ValueError):
            return False
        if hashlib.sha256(source).hexdigest() != item["source_sha256"]:
            return False
        expected = xc.inputs(source, item["path"], lang, base)
        if xc.digest(expected) != item["key"]:
            return False
        _packet, reason = cache.lookup(expected, source)
        return reason == ("hit" if item["state"] == "complete" else "failed")


def chain_start(job: str) -> str:
    return hashlib.sha256(("atlas-aci-job-checkpoint.v1\0" + job).encode()).hexdigest()


def chain_next(previous: str, body: dict[str, Any]) -> str:
    return hashlib.sha256((previous + ev.canonical(body)).encode()).hexdigest()


class CheckpointingCache(xc.ExtractionCache):
    """The extraction cache, recording a checkpoint after every durable write."""

    def __init__(self, atlas_dir: Path, run: Run):
        super().__init__(atlas_dir)
        self._run = run

    def write(
        self, expected: dict[str, Any], packet: xc.ExtractionPacket | None, failure: Any = None
    ) -> bool:
        ok = super().write(expected, packet, failure)
        if ok:
            self._run.checkpoint(expected, packet is not None)
        return ok


class _JobGraph(CodeGraph):
    """``CodeGraph`` with the seams a job needs. Overrides run inside the writer lock."""

    _run: Run

    def bind(self, run: Run) -> _JobGraph:
        self._run = run
        self._staged = False
        self._pre_replace_done = False
        return self

    @contextmanager
    def _writer_lock(self) -> Iterator[None]:
        """The base body with a non-blocking poll and a cancel check each tick."""
        if self.read_only or self._closed:
            raise RuntimeError("Index publication requires an open writable CodeGraph")
        self._validate_graph_namespace()
        self.atlas_dir.mkdir(parents=True, exist_ok=True)
        with (self.atlas_dir / ".index.lock").open("a+") as lock_file:
            while True:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EAGAIN, errno.EACCES):
                        raise
                self._run.check_cancel()
                time.sleep(WRITER_POLL_SECONDS)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _publication_base(self) -> tuple[str | None, str | None]:
        base = super()._publication_base()
        run = self._run
        try:
            meta = gen.capture_current(run.ns, now=run.now, locked=True, hooks=run.hooks)
        except gen.StoreError as exc:
            # A leased farm of the same generation id already holds equal content for readers.
            if exc.reason != "capture_conflict":
                raise CaptureFailed from None
            meta = None
        except OSError:
            raise CaptureFailed from None
        run.persist(base_generation_id=meta.generation_id if meta is not None else base[0])
        run.fire("predecessor_captured")
        return base

    def _run_build(
        self, conn: sqlite3.Connection, get_parser: Any, since: str | None
    ) -> dict[str, Any]:
        stats = super()._run_build(conn, get_parser, since)
        run = self._run
        identifier = conn.execute(
            "SELECT value FROM manifest WHERE key='generation_id'"
        ).fetchone()[0]
        inputs = json.loads(
            conn.execute("SELECT value FROM manifest WHERE key='generation_inputs'").fetchone()[0]
        )
        if jobs.tree_digest(inputs["source_hashes"]) != run.ident["source_tree_sha256"]:
            raise SourceChanged
        run.staged_gid = identifier
        run.stats = stats
        run.persist(
            state="staged", staged_generation_id=identifier, snapshot_id=stats["snapshot_id"]
        )
        run.fire("staged")
        run.check_cancel()
        self._staged = True
        return stats

    def _close_db(self) -> None:
        if self._staged and not self._pre_replace_done:
            self._pre_replace_done = True
            self._run.fire("pre_replace")
            self._run.check_cancel()  # the last point before the commit (os.replace)
        super()._close_db()

    def _retire_previous_native(self) -> None:
        run = self._run
        run.persist(state="published", published_generation_id=run.staged_gid)
        run.fire("post_replace")
        super()._retire_previous_native()
        run.fire("post_retire")
        try:
            gen.capture_current(run.ns, now=run.now, locked=True, hooks=run.hooks)
        except (gen.StoreError, OSError) as exc:
            run.capture_error = exc
            log.warning("job_capture_deferred", reason="capture_failed")
            return
        run.captured = True
        run.persist(state="captured")
        run.fire("captured")


_PATCHED = False


@contextmanager
def patched(run: Run) -> Iterator[None]:
    """Rebind the module-level seams for one build. Module-global: no second in-process build while
    it is active (the in-process tests run twins in subprocesses)."""
    global _PATCHED
    if _PATCHED:
        raise RuntimeError("patched() is not reentrant")
    _PATCHED = True
    cache_class = xc.ExtractionCache
    install = snap.Builder._install
    retain = snap.retain

    def cache_factory(atlas_dir: Path) -> CheckpointingCache:
        return CheckpointingCache(atlas_dir, run)

    def wrapped_install(self: snap.Builder, store_fd: int, name: str, data_fd: int) -> None:
        before = len(self.installed)
        install(self, store_fd, name, data_fd)
        if len(self.installed) > before and not run.mid_publish_fired:
            run.mid_publish_fired = True
            run.fire("mid_publish")

    def wrapped_retain(atlas_dir: Path, identifier: str | None) -> None:
        retain(atlas_dir, identifier)
        run.fire("post_retain")

    setattr(xc, "ExtractionCache", cache_factory)  # noqa: B010
    setattr(snap.Builder, "_install", wrapped_install)  # noqa: B010
    setattr(snap, "retain", wrapped_retain)  # noqa: B010
    try:
        yield
    finally:
        setattr(xc, "ExtractionCache", cache_class)  # noqa: B010
        setattr(snap.Builder, "_install", install)  # noqa: B010
        setattr(snap, "retain", retain)  # noqa: B010
        _PATCHED = False


def _receipt(run: Run, outcome: str, generation: str, snapshot: str | None) -> dict[str, Any]:
    stats = run.stats if outcome == "built" and run.stats is not None else None
    cache = None
    if stats is not None:
        raw = stats["extraction_cache"]
        cache = {key: int(raw[key]) for key in jobs.CACHE_KEYS}
    return {
        "format": jobs.FORMAT_RECEIPT,
        "job_id": run.store.job,
        "attempt": run.doc["attempt"],
        "outcome": outcome,
        "generation_id": generation,
        "snapshot_id": snapshot,
        "base_generation_id": run.doc["base_generation_id"],
        "resumed_from": run.doc["resumed_from"],
        "extraction_cache": cache,
        "files_indexed": int(stats["files_indexed"]) if stats is not None else None,
        "cancel": "too_late" if run.cancelled() else "none",
    }


def _finish(run: Run, receipt: dict[str, Any]) -> dict[str, Any]:
    run.fire("pre_receipt")
    run.store.write("receipt.json", receipt)
    run.fire("pre_complete")
    run.persist(state="completed", cancel=receipt["cancel"], failure_reason=None)
    return receipt


def reconcile(run: Run) -> dict[str, Any] | None:
    """An earlier attempt died after the commit: its staged generation is the live one. Under the
    writer lock, repeat the catalog update (``snap.retain``) and the capture, then complete."""
    staged = run.doc["staged_generation_id"]
    if not run.continuing or staged is None:
        return None
    with gen.writer_lock(run.ns):
        current = gen.current_generation(run.ns)
        if current is None or current[0] != staged:
            return None
        if current[1] is not None:
            try:
                snap.retain(run.ns.atlas_dir, current[1])
            except (OSError, ValueError):
                raise CaptureFailed from None
        try:
            gen.capture_current(run.ns, now=run.now, locked=True, hooks=run.hooks)
        except (gen.StoreError, OSError):
            raise CaptureFailed from None
    run.persist(state="captured", published_generation_id=staged, snapshot_id=current[1])
    return _finish(run, _receipt(run, "recovered", staged, current[1]))


def run_job(run: Run) -> dict[str, Any]:
    """Build in this process; returns the written receipt."""
    run.persist(state="running")
    run.fire("runner_start")
    run.check_cancel()
    graph = _JobGraph(
        run.spec.ns.repo,
        langs=list(run.spec.langs) or None,
        graph_root=run.spec.ns.graph_root,
        frameworks=run.spec.frameworks,
        rails_table_map=run.spec.rails_table_map,
    ).bind(run)
    try:
        if run.continuing:
            resumed = run.validate_checkpoints(graph)
        else:
            run.reset_log()
            resumed = None
        run.persist(resumed_from=resumed)
        with patched(run):
            graph.build(since=None)
    finally:
        run.close_log()
        graph.close()
    generation, snapshot = run.staged_gid, run.doc["snapshot_id"]
    if generation is None:
        raise RuntimeError("build returned without a staged generation")
    if not run.captured:
        # The commit happened; the capture failed or was deferred. Retry once under the lock.
        try:
            gen.capture_current(run.ns, now=run.now, locked=False, hooks=run.hooks)
        except (gen.StoreError, OSError):
            raise CaptureFailed from None
        run.persist(state="captured")
    return _finish(run, _receipt(run, "built", generation, snapshot))


def execute(
    spec: jobs.JobSpec,
    policy: gen.RetentionPolicy,
    store: jobs.JobStore,
    ident: dict[str, Any],
    doc: dict[str, Any],
    *,
    now: int,
    hooks: dict[str, Any] | None,
    signals: bool,
    continuing: bool,
    decline: str | None,
) -> dict[str, Any]:
    """Reconcile or run one attempt (slot and run.lock held); returns the result envelope."""
    run = Run(
        spec,
        policy,
        store,
        ident,
        doc,
        now=now,
        hooks=hooks,
        signals=signals,
        continuing=continuing,
    )
    job = store.job
    try:
        receipt = reconcile(run)
        if receipt is None:
            receipt = run_job(run)
    except JobCancelled:
        run.persist(state="cancelled", cancel="honoured")
        raise jobs.JobError("JOB_CANCELLED", job_id=job) from None
    except InjectedFault:
        run.persist(state="failed", failure_reason="injected_fault")
        raise jobs.JobError("JOB_FAILED", reason="injected_fault", job_id=job) from None
    except SourceChanged:
        run.persist(state="failed", failure_reason="source_changed")
        raise jobs.JobError("JOB_FAILED", reason="source_changed", job_id=job) from None
    except CaptureFailed:
        run.persist(state="failed", failure_reason="capture_failed")
        raise jobs.JobError("JOB_FAILED", reason="capture_failed", job_id=job) from None
    except gen.StoreError as exc:
        raise jobs.store_error(exc) from None
    except OSError:
        raise
    except Exception:
        log.warning("job_build_failed", job=job)
        run.persist(state="failed", failure_reason="build_error")
        raise jobs.JobError("JOB_FAILED", reason="build_error", job_id=job) from None
    finally:
        run.close_log()
    return jobs.result_envelope(spec.ns, job, receipt, reused=False, decline=decline)


# ---------------------------------------------------------------------------------------- cli
def _on_term(signum: int, frame: Any) -> None:
    SIGTERM_FLAG.set()


def run_from_cli(
    action: str,
    ns: gen.Namespace,
    *,
    now: int,
    spec: jobs.JobSpec | None = None,
    job: str | None = None,
) -> dict[str, Any]:
    """The only reader of ``ATLAS_ACI_JOB_FAULTS``; installs (and restores) the SIGTERM handler."""
    hooks = parse_faults(os.environ.get(FAULT_ENV))
    SIGTERM_FLAG.clear()
    previous = signal.signal(signal.SIGTERM, _on_term)
    try:
        if action == "submit" and spec is not None:
            return jobs.submit(spec, now=now, hooks=hooks, install_signals=True)
        if action == "resume" and job is not None:
            return jobs.resume(ns, job, now=now, hooks=hooks, install_signals=True)
        raise ValueError("unknown job action")
    finally:
        signal.signal(signal.SIGTERM, previous)


def hold_lease(stream: Any = None) -> None:
    """Block until stdin reaches EOF or SIGTERM (the SIGTERM handler is installed here, so the
    holder also stops when it is PID 1 of a container)."""
    source = stream if stream is not None else sys.stdin
    SIGTERM_FLAG.clear()
    previous = signal.signal(signal.SIGTERM, _on_term)
    try:
        descriptor = source.fileno()
        while not SIGTERM_FLAG.is_set():
            ready, _w, _x = select.select([descriptor], [], [], 0.2)
            if ready and not os.read(descriptor, 4096):
                return
    finally:
        signal.signal(signal.SIGTERM, previous)
