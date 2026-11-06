"""Durable index jobs: identity, the job store, submit/resume/cancel/status and reuse (ACI-S41).

A job is identified by the sha256 of a canonical identity object (repository, namespace, languages,
frameworks, table-map digest, schema, producer inputs, skip patterns and a digest of the source
tree). Nothing time-based and no ``since`` marker is part of it, and the database file hash is never
used (it carries a random publication token).

The process that runs ``atlas-aci jobs submit|resume`` is the job process: ``job_runner`` builds in
that process, so no new subprocess spawn site exists. Computing an identity reads every source file
once (also for a reuse check); that cost is the price of a content-addressed identity.

Submit/resume on an existing job, by derived state:

* completed: reuse when the retained output verifies; otherwise a new attempt (``attempt += 1``)
* failed or cancelled: ``submit`` starts a new attempt (``attempt += 1``); ``resume`` refuses
* accepted/running/staged/published/captured with ``run.lock`` held: ``JOB_RUNNING``
* the same states with ``run.lock`` free (a dead runner, "interrupted"): continue from the last
  valid checkpoint (``resumed_from``); ``jobs status`` lists it with the code ``JOB_INTERRUPTED``
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from atlas_aci.codegraph import LANG_BY_EXT, CodeGraph
from atlas_aci.enforcement import ToolError
from atlas_aci.storage import evidence as ev
from atlas_aci.storage import generations as gen
from atlas_aci.storage import leases, retention
from atlas_aci.storage import snapshots as snap

FORMAT_JOB = "atlas-aci-index-job.v1"
FORMAT_IDENTITY = "atlas-aci-index-job-identity.v1"
FORMAT_CHECKPOINT = "atlas-aci-job-checkpoint.v1"
FORMAT_RECEIPT = "atlas-aci-index-job-receipt.v1"
FORMAT_RESULT = "atlas-aci-job-result.v1"
FORMAT_STATUS = "atlas-aci-jobs-status.v1"
FORMAT_ERROR = "atlas-aci-job-error.v1"
JOB_STATES = (
    "accepted",
    "running",
    "staged",
    "published",
    "captured",
    "completed",
    "cancelled",
    "failed",
)
TERMINAL = ("completed", "cancelled", "failed")
CANCEL = ("none", "requested", "honoured", "too_late")
CANCEL_OUTCOMES = ("requested", "cancelled", "too_late", "not_running")
FAILURE_REASONS = ("build_error", "source_changed", "capture_failed", "injected_fault")
DECLINE_REASONS = (
    "no_receipt",
    "output_retired",
    "output_raced",
    "output_digest_invalid",
    "identity_mismatch",
)
JOB_ERRORS = (
    "BUSY",
    "JOB_RUNNING",
    "JOB_UNKNOWN",
    "JOB_NOT_RESUMABLE",
    "JOB_FAILED",
    "JOB_INTERRUPTED",
    "JOB_CANCELLED",
    "GC_BUSY",
    "WRITER_BUSY",
    "CROSS_DEVICE",
    "STORE_INVALID",
)
JOB_ID = gen.GID
STATUS_LIST_CAP = 64
STATUS_GENERATIONS_CAP = 256
LOCK_ATTEMPTS = 3
LOCK_BACKOFF_SECONDS = 0.02
JOB_KEYS = frozenset(
    {
        "format",
        "job_id",
        "identity",
        "graph_root",
        "state",
        "attempt",
        "base_generation_id",
        "staged_generation_id",
        "published_generation_id",
        "snapshot_id",
        "failure_reason",
        "cancel",
        "resumed_from",
        "last_decline",
    }
)
RECEIPT_KEYS = frozenset(
    {
        "format",
        "job_id",
        "attempt",
        "outcome",
        "generation_id",
        "snapshot_id",
        "base_generation_id",
        "resumed_from",
        "extraction_cache",
        "files_indexed",
        "cancel",
    }
)
CACHE_KEYS = ("parser_invocations", "disk_hits", "misses", "write_failures")
MAP_FILE = "rails-table-map.json"


class JobError(Exception):
    """A typed job failure. The text is fixed: it never carries a path or file content."""

    def __init__(
        self,
        code: str,
        retry_hint: str = "none",
        *,
        reason: str | None = None,
        job_id: str | None = None,
        generation_id: str | None = None,
    ):
        self.code = code
        self.retry_hint = retry_hint
        self.reason = reason
        self.job_id = job_id
        self.generation_id = generation_id
        super().__init__(f"job error ({code})")

    def envelope(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "format": FORMAT_ERROR,
            "code": self.code,
            "retry_hint": self.retry_hint,
        }
        if self.reason is not None:
            value["reason"] = self.reason
        if self.job_id is not None:
            value["job_id"] = self.job_id
        if self.generation_id is not None:
            value["generation_id"] = self.generation_id
        return value


STORE_ERROR_CODES = {
    "cross_device": "CROSS_DEVICE",
    "writer_busy": "WRITER_BUSY",
    "gc_busy": "GC_BUSY",
    "index_unavailable": "INDEX_UNAVAILABLE",
    "meta_invalid": "STORE_INVALID",
    "capture_conflict": "STORE_INVALID",
    "leased_invalid": "STORE_INVALID",
}


def store_error(exc: gen.StoreError) -> JobError:
    code = STORE_ERROR_CODES[exc.reason]
    hint = "retry_later" if code in {"WRITER_BUSY", "GC_BUSY"} else "none"
    return JobError(code, hint, reason=exc.reason)


@dataclass(frozen=True)
class JobSpec:
    ns: gen.Namespace
    langs: tuple[str, ...]
    frameworks: tuple[str, ...]
    rails_table_map: bytes | None


def _graph(spec: JobSpec, *, read_only: bool = True) -> CodeGraph:
    return CodeGraph(
        spec.ns.repo,
        langs=list(spec.langs) or None,
        read_only=read_only,
        graph_root=spec.ns.graph_root,
        frameworks=spec.frameworks,
        rails_table_map=spec.rails_table_map,
    )


def tree_digest(hashes: dict[str, str]) -> str:
    rows = [[rel, hashes[rel]] for rel in sorted(hashes)]
    return hashlib.sha256(ev.canonical(rows).encode()).hexdigest()


def source_hashes(graph: CodeGraph) -> dict[str, str]:
    """The set ``CodeGraph.build`` records as ``source_hashes``: every recognised source file."""
    hashes: dict[str, str] = {}
    for path in graph._iter_source_files():
        if path.suffix not in LANG_BY_EXT:
            continue
        rel = str(path.relative_to(graph.repo))
        data, _info = snap.safe_source(graph.repo, rel)
        hashes[rel] = hashlib.sha256(data).hexdigest()
    return hashes


def identity(spec: JobSpec) -> dict[str, Any]:
    """The canonical identity object of a job (an unreadable source gives ``JOB_FAILED``)."""
    graph = _graph(spec)
    try:
        base = graph._build_inputs()
        try:
            hashes = source_hashes(graph)
        except (OSError, ValueError):
            raise JobError("JOB_FAILED", reason="build_error") from None
        table = (
            hashlib.sha256(spec.rails_table_map).hexdigest()
            if spec.rails_table_map is not None
            else None
        )
        return {
            "format": FORMAT_IDENTITY,
            "repository": str(spec.ns.repo),
            "namespace": spec.ns.identity,
            "languages": base["configuration"]["languages"],
            "frameworks": sorted(graph.frameworks),
            "rails_table_map_sha256": table,
            "schema": base["schema"],
            "producer": base["producer"],
            "skip_patterns": base["configuration"]["skip_patterns"],
            "source_tree_sha256": tree_digest(hashes),
            "source_files": len(hashes),
        }
    finally:
        graph.close()


def job_id(ident: dict[str, Any]) -> str:
    return hashlib.sha256(ev.canonical(ident).encode()).hexdigest()


# ------------------------------------------------------------------------------------- store
class JobStore:
    """``<atlas_dir>/jobs/<job_id>/{job.json, checkpoint.*, receipt.json, cancel, run.lock}``."""

    def __init__(self, ns: gen.Namespace, job: str):
        self.ns = ns
        self.job = job
        self.dir = ns.atlas_dir / "jobs" / job

    def path(self, name: str) -> Path:
        return self.dir / name

    def exists(self) -> bool:
        return gen.lstat_or_none(self.dir) is not None

    def create(self) -> None:
        if gen.lstat_or_none(self.ns.atlas_dir) is None:
            os.makedirs(self.ns.atlas_dir, exist_ok=True)
        gen.ensure_dir(self.ns.atlas_dir / "jobs")
        gen.ensure_dir(self.dir)

    def write(self, name: str, value: Any) -> None:
        fd = gen.open_dir(self.dir)
        try:
            gen.write_json_atomic(fd, name, value)
        finally:
            os.close(fd)

    def write_bytes(self, name: str, data: bytes) -> None:
        fd = gen.open_dir(self.dir)
        try:
            gen.write_bytes_atomic(fd, name, data)
        finally:
            os.close(fd)

    def write_job(self, doc: dict[str, Any]) -> None:
        validate_job(doc, self.job)
        self.write("job.json", doc)

    def read_job(self) -> dict[str, Any] | None:
        try:
            doc = gen.read_json(None, self.path("job.json"), gen.DOC_CAP)
        except FileNotFoundError:
            return None
        except (OSError, ValueError, OverflowError):
            raise JobError("STORE_INVALID", reason="job_invalid", job_id=self.job) from None
        try:
            validate_job(doc, self.job)
        except ValueError:
            raise JobError("STORE_INVALID", reason="job_invalid", job_id=self.job) from None
        assert isinstance(doc, dict)
        return doc

    def read_receipt(self) -> dict[str, Any]:
        doc = gen.read_json(None, self.path("receipt.json"), gen.DOC_CAP)
        validate_receipt(doc, self.job)
        assert isinstance(doc, dict)
        return doc

    def cancel_requested(self) -> bool:
        return gen.lstat_or_none(self.path("cancel")) is not None

    def request_cancel(self) -> None:
        fd = os.open(
            self.path("cancel"),
            os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o644,
        )
        os.close(fd)

    def clear_cancel(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(self.path("cancel"))

    def open_run_lock(self, *, create: bool) -> int | None:
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | (os.O_CREAT if create else 0)
        try:
            return os.open(self.path("run.lock"), flags, 0o644)
        except FileNotFoundError:
            return None


def _flock_retry(fd: int, operation: int) -> bool:
    for attempt in range(LOCK_ATTEMPTS):
        try:
            fcntl.flock(fd, operation | fcntl.LOCK_NB)
            return True
        except OSError as exc:
            if exc.errno not in (errno.EAGAIN, errno.EACCES):
                raise
        if attempt + 1 < LOCK_ATTEMPTS:
            time.sleep(LOCK_BACKOFF_SECONDS)
    return False


def runner_alive(store: JobStore) -> bool:
    """True when a process holds the job's run.lock (a read-only probe)."""
    try:
        fd = os.open(
            store.path("run.lock"), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        )
    except OSError:
        return False
    try:
        if _flock_retry(fd, fcntl.LOCK_EX):
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        return True
    finally:
        os.close(fd)


def validate_job(doc: Any, job: str) -> None:
    if not isinstance(doc, dict) or set(doc) != JOB_KEYS:
        raise ValueError("job keys")
    hexa = ev.hash_value

    def optional_hex(value: Any) -> bool:
        return value is None or hexa(value)

    ok = (
        doc["format"] == FORMAT_JOB
        and doc["job_id"] == job
        and isinstance(doc["identity"], dict)
        and (doc["graph_root"] is None or isinstance(doc["graph_root"], str))
        and doc["state"] in JOB_STATES
        and type(doc["attempt"]) is int
        and doc["attempt"] >= 1
        and optional_hex(doc["base_generation_id"])
        and optional_hex(doc["staged_generation_id"])
        and optional_hex(doc["published_generation_id"])
        and optional_hex(doc["snapshot_id"])
        and (doc["failure_reason"] is None or doc["failure_reason"] in FAILURE_REASONS)
        and doc["cancel"] in CANCEL
        and (
            doc["resumed_from"] is None
            or (type(doc["resumed_from"]) is int and doc["resumed_from"] >= 0)
        )
        and (doc["last_decline"] is None or doc["last_decline"] in DECLINE_REASONS)
    )
    if not ok:
        raise ValueError("job values")


def validate_receipt(doc: Any, job: str) -> None:
    if not isinstance(doc, dict) or set(doc) != RECEIPT_KEYS:
        raise ValueError("receipt keys")
    cache = doc["extraction_cache"]
    ok = (
        doc["format"] == FORMAT_RECEIPT
        and doc["job_id"] == job
        and type(doc["attempt"]) is int
        and doc["attempt"] >= 1
        and doc["outcome"] in ("built", "recovered")
        and ev.hash_value(doc["generation_id"])
        and (doc["snapshot_id"] is None or ev.hash_value(doc["snapshot_id"]))
        and (doc["base_generation_id"] is None or ev.hash_value(doc["base_generation_id"]))
        and (
            doc["resumed_from"] is None
            or (type(doc["resumed_from"]) is int and doc["resumed_from"] >= 0)
        )
        and (
            cache is None
            or (
                isinstance(cache, dict)
                and set(cache) == set(CACHE_KEYS)
                and all(type(cache[k]) is int and cache[k] >= 0 for k in CACHE_KEYS)
            )
        )
        and (
            doc["files_indexed"] is None
            or (type(doc["files_indexed"]) is int and doc["files_indexed"] >= 0)
        )
        and doc["cancel"] in ("none", "too_late")
    )
    if not ok:
        raise ValueError("receipt values")


# ----------------------------------------------------------------------------------- results
def _current_id(ns: gen.Namespace) -> str | None:
    try:
        current = gen.current_generation(ns)
    except (gen.StoreError, OSError, ToolError):
        return None
    return None if current is None else current[0]


def result_envelope(
    ns: gen.Namespace,
    job: str,
    receipt: dict[str, Any],
    *,
    reused: bool,
    decline: str | None,
) -> dict[str, Any]:
    """The submit/resume result. ``current_generation_id`` and ``receipt_is_current`` are computed
    read-only; the receipt bytes are the stored ones. A reuse exit 0 does not imply that the live
    index equals the tree: compare ``receipt_is_current``."""
    current = _current_id(ns)
    return {
        "format": FORMAT_RESULT,
        "job_id": job,
        "reused": reused,
        "decline_reason": decline,
        "receipt": receipt,
        "current_generation_id": current,
        "receipt_is_current": current is not None and current == receipt["generation_id"],
    }


def try_reuse(
    ns: gen.Namespace, store: JobStore, ident: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    """(receipt, None) when the stored output verifies, else (None, decline reason)."""
    try:
        receipt = store.read_receipt()
    except (OSError, ValueError, OverflowError):
        return None, "no_receipt"
    gid = receipt["generation_id"]
    if gen.lstat_or_none(ns.generations / gid) is None:
        return None, "output_retired"
    try:
        lease = leases.acquire(ns, gid)
    except ToolError as exc:
        return None, "output_raced" if exc.code == "EXPIRED_REFERENCE" else "output_digest_invalid"
    except (OSError, ValueError, gen.StoreError):
        return None, "output_digest_invalid"
    try:
        if lease.origin != "retained" or lease.snapshot_id != receipt["snapshot_id"]:
            return None, "output_raced"
        try:
            row = lease.db.execute(
                "SELECT value FROM manifest WHERE key='generation_inputs'"
            ).fetchone()
            inputs = json.loads(row[0])
            bound = (
                tree_digest(inputs["source_hashes"]) == ident["source_tree_sha256"]
                and inputs["producer"] == ident["producer"]
                and inputs["repository"] == ident["repository"]
                and inputs["configuration"]["languages"] == ident["languages"]
            )
        except (KeyError, TypeError, ValueError, IndexError):
            bound = False
        if not bound:
            return None, "identity_mismatch"
        return receipt, None
    finally:
        lease.close()


# ------------------------------------------------------------------------------- submit/resume
def _fresh_doc(job: str, ident: dict[str, Any], graph_root: Path | None) -> dict[str, Any]:
    return {
        "format": FORMAT_JOB,
        "job_id": job,
        "identity": ident,
        "graph_root": None if graph_root is None else str(graph_root),
        "state": "accepted",
        "attempt": 1,
        "base_generation_id": None,
        "staged_generation_id": None,
        "published_generation_id": None,
        "snapshot_id": None,
        "failure_reason": None,
        "cancel": "none",
        "resumed_from": None,
        "last_decline": None,
    }


def acquire_slot(ns: gen.Namespace, capacity: int) -> int | None:
    """EX|NB on ``jobs/slots/<i>.lock``; None when every slot is held (BUSY)."""
    if gen.lstat_or_none(ns.atlas_dir) is None:
        os.makedirs(ns.atlas_dir, exist_ok=True)
    gen.ensure_dir(ns.atlas_dir / "jobs")
    gen.ensure_dir(ns.atlas_dir / "jobs" / "slots")
    for index in range(capacity):
        fd = os.open(
            ns.atlas_dir / "jobs" / "slots" / f"{index}.lock",
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o644,
        )
        if _flock_retry(fd, fcntl.LOCK_EX):
            return fd
        os.close(fd)
    return None


def _release(fd: int) -> None:
    with contextlib.suppress(OSError):
        fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


def submit(
    spec: JobSpec,
    *,
    now: int,
    hooks: dict[str, Any] | None = None,
    install_signals: bool = False,
) -> dict[str, Any]:
    """Run (or reuse) the index job for this spec; returns the result envelope."""
    ns = spec.ns
    policy = gen.load_policy(ns)
    ident = identity(spec)
    job = job_id(ident)
    store = JobStore(ns, job)
    doc = store.read_job() if store.exists() else None
    decline: str | None = None
    if doc is not None and doc["state"] == "completed":
        receipt, decline = try_reuse(ns, store, ident)
        if receipt is not None:
            return result_envelope(ns, job, receipt, reused=True, decline=None)
    return _launch(
        spec,
        policy,
        store,
        ident,
        doc,
        now=now,
        hooks=hooks,
        signals=install_signals,
        decline=decline,
    )


def resume(
    ns: gen.Namespace,
    job: str,
    *,
    now: int,
    hooks: dict[str, Any] | None = None,
    install_signals: bool = False,
) -> dict[str, Any]:
    """Continue an interrupted job from its last valid checkpoint."""
    policy = gen.load_policy(ns)
    store = JobStore(ns, job)
    doc = store.read_job() if store.exists() else None
    if doc is None:
        raise JobError("JOB_UNKNOWN", job_id=job)
    if doc["state"] in TERMINAL:
        raise JobError("JOB_NOT_RESUMABLE", job_id=job)
    if runner_alive(store):
        raise JobError("JOB_RUNNING", "retry_later", job_id=job)
    ident = doc["identity"]
    table: bytes | None = None
    if ident.get("rails_table_map_sha256") is not None:
        try:
            table = gen.read_bounded(None, store.path(MAP_FILE), 1048576)
        except (OSError, OverflowError, ValueError):
            raise JobError("STORE_INVALID", reason="job_invalid", job_id=job) from None
        if hashlib.sha256(table).hexdigest() != ident["rails_table_map_sha256"]:
            raise JobError("STORE_INVALID", reason="job_invalid", job_id=job)
    try:
        spec = JobSpec(ns, tuple(ident["languages"]), tuple(ident["frameworks"]), table)
    except (KeyError, TypeError):
        raise JobError("STORE_INVALID", reason="job_invalid", job_id=job) from None
    return _launch(
        spec, policy, store, ident, doc, now=now, hooks=hooks, signals=install_signals, decline=None
    )


def _launch(
    spec: JobSpec,
    policy: gen.RetentionPolicy,
    store: JobStore,
    ident: dict[str, Any],
    doc: dict[str, Any] | None,
    *,
    now: int,
    hooks: dict[str, Any] | None,
    signals: bool,
    decline: str | None,
) -> dict[str, Any]:
    from atlas_aci.indexing import job_runner

    ns = spec.ns
    job = store.job
    if doc is not None and doc["state"] not in TERMINAL and runner_alive(store):
        raise JobError("JOB_RUNNING", "retry_later", job_id=job)
    slot = acquire_slot(ns, policy.max_concurrent_jobs)
    if slot is None:
        raise JobError("BUSY", "retry_later", job_id=job)
    run_fd: int | None = None
    try:
        store.create()
        run_fd = store.open_run_lock(create=True)
        if run_fd is None or not _flock_retry(run_fd, fcntl.LOCK_EX):
            raise JobError("JOB_RUNNING", "retry_later", job_id=job)
        if decline == "output_digest_invalid":
            _retire_invalid_output(ns, store, doc)
        continuing = doc is not None and doc["state"] not in TERMINAL
        if spec.rails_table_map is not None and gen.lstat_or_none(store.path(MAP_FILE)) is None:
            store.write_bytes(MAP_FILE, spec.rails_table_map)
        if doc is None:
            doc = _fresh_doc(job, ident, ns.graph_root)
        else:
            doc = {
                **doc,
                "attempt": doc["attempt"] + 1,
                "state": "accepted",
                "failure_reason": None,
                "cancel": "none",
                "last_decline": decline,
            }
            if not continuing:
                doc.update(
                    base_generation_id=None,
                    staged_generation_id=None,
                    published_generation_id=None,
                    snapshot_id=None,
                    resumed_from=None,
                )
        store.clear_cancel()
        store.write_job(doc)
        return job_runner.execute(
            spec,
            policy,
            store,
            ident,
            doc,
            now=now,
            hooks=hooks,
            signals=signals,
            continuing=continuing,
            decline=decline,
        )
    finally:
        if run_fd is not None:
            _release(run_fd)
        _release(slot)


def _retire_invalid_output(ns: gen.Namespace, store: JobStore, doc: dict[str, Any] | None) -> None:
    try:
        gid = store.read_receipt()["generation_id"]
    except (OSError, ValueError, OverflowError):
        return
    try:
        retired = retention.retire_invalid(ns, gid)
    except gen.StoreError as exc:
        raise store_error(exc) from None
    if not retired:
        raise JobError("JOB_FAILED", reason="capture_failed", job_id=store.job, generation_id=gid)


# ------------------------------------------------------------------------------------ cancel
def cancel(ns: gen.Namespace, job: str) -> dict[str, Any]:
    """Cancel a job: ``requested`` (runner alive), ``cancelled`` (dead runner), ``too_late`` or
    ``not_running``. Only the run.lock holder writes job.json."""
    store = JobStore(ns, job)
    doc = store.read_job() if store.exists() else None
    if doc is None:
        raise JobError("JOB_UNKNOWN", job_id=job)
    if doc["state"] == "completed":
        outcome = "too_late"
    elif doc["state"] in TERMINAL:
        outcome = "not_running"
    elif runner_alive(store):
        store.request_cancel()
        outcome = "too_late" if doc["state"] in ("published", "captured") else "requested"
    else:
        run_fd = store.open_run_lock(create=False)
        if run_fd is None or not _flock_retry(run_fd, fcntl.LOCK_EX):
            if run_fd is not None:
                os.close(run_fd)
            store.request_cancel()
            outcome = "requested"
        else:
            try:
                store.write_job({**doc, "state": "cancelled", "cancel": "honoured"})
                store.clear_cancel()
                outcome = "cancelled"
            finally:
                _release(run_fd)
    assert outcome in CANCEL_OUTCOMES
    return {"format": "atlas-aci-job-cancel.v1", "job_id": job, "outcome": outcome}


# ------------------------------------------------------------------------------------ status
def _farm_status(ns: gen.Namespace, current: str | None) -> dict[str, Any]:
    metas = sorted(
        gen.list_generations(ns), key=lambda m: (-m.seq if m.valid else 1, m.generation_id)
    )
    rows: list[dict[str, Any]] = []
    all_files: list[tuple[int, int, int]] = []
    leased = 0
    for meta in metas:
        files = gen.farm_files(ns, meta.generation_id)
        all_files.extend(files)
        held = leases.probe_leased(ns.generations / meta.generation_id)
        leased += int(held)
        rows.append(
            {
                "generation_id": meta.generation_id,
                "seq": meta.seq,
                "snapshot_id": meta.snapshot_id,
                "bytes": gen.distinct_bytes(files),
                "leased": held,
                "current": meta.generation_id == current,
            }
        )
    return {
        "retained": {
            "generations": len(metas),
            "bytes": gen.distinct_bytes(all_files),
            "leased": leased,
        },
        "generations": rows[:STATUS_GENERATIONS_CAP],
        "generations_truncated": len(rows) > STATUS_GENERATIONS_CAP,
    }


def status(ns: gen.Namespace) -> dict[str, Any]:
    """Canonical metrics; read-only (creates nothing)."""
    policy = gen.load_policy(ns)
    counts = {"running": 0, "interrupted": 0, "completed": 0, "cancelled": 0, "failed": 0}
    phases = dict.fromkeys(("accepted", "running", "staged", "published", "captured"), 0)
    running: list[str] = []
    interrupted: list[dict[str, str]] = []
    try:
        names = sorted(os.listdir(ns.atlas_dir / "jobs"))
    except OSError:
        names = []
    for name in names:
        if JOB_ID.fullmatch(name) is None:
            continue
        store = JobStore(ns, name)
        try:
            doc = store.read_job()
        except JobError:
            continue
        if doc is None:
            continue
        state = doc["state"]
        if state in TERMINAL:
            counts[state] += 1
            continue
        phases[state] += 1
        if runner_alive(store):
            counts["running"] += 1
            running.append(name)
        else:
            counts["interrupted"] += 1
            interrupted.append({"job_id": name, "code": "JOB_INTERRUPTED"})
    busy = 0
    for index in range(policy.max_concurrent_jobs):
        path = ns.atlas_dir / "jobs" / "slots" / f"{index}.lock"
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        except OSError:
            continue
        try:
            if _flock_retry(fd, fcntl.LOCK_EX):
                fcntl.flock(fd, fcntl.LOCK_UN)
            else:
                busy += 1
        finally:
            os.close(fd)
    current = _current_id(ns)
    last_gc: dict[str, Any] | None = None
    try:
        receipt = gen.read_json(None, ns.generations / "gc-receipt.json", gen.DOC_CAP)
        last_gc = {
            "retained_digest": receipt["retained_digest"],
            "retired": len(receipt["retired"]),
            "now": receipt["now"],
        }
    except (OSError, ValueError, OverflowError, KeyError, TypeError):
        last_gc = None
    farms = _farm_status(ns, current)
    return {
        "format": FORMAT_STATUS,
        "namespace": ns.identity,
        "current_generation_id": current,
        "jobs": counts,
        "phases": phases,
        "running_jobs": running[:STATUS_LIST_CAP],
        "interrupted_jobs": interrupted[:STATUS_LIST_CAP],
        "slots": {"capacity": policy.max_concurrent_jobs, "busy": busy},
        **farms,
        "last_gc": last_gc,
        "policy": policy.as_dict(),
    }


EXIT_CODES = {
    "BUSY": 75,
    "JOB_RUNNING": 75,
    "GC_BUSY": 75,
    "WRITER_BUSY": 75,
    "EXPIRED_REFERENCE": 3,
    "INVALID_REFERENCE": 3,
    "JOB_CANCELLED": 4,
}


def exit_code(code: str) -> int:
    """0 is success and 2 is usage; every other failure is 1 unless it has its own code."""
    return EXIT_CODES.get(code, 1)


def error_for(exc: BaseException) -> JobError:
    """Map any expected failure to a typed ``JobError`` with fixed text."""
    if isinstance(exc, JobError):
        return exc
    if isinstance(exc, gen.StoreError):
        return store_error(exc)
    if isinstance(exc, ToolError):
        hint = "retry_current" if exc.code == "EXPIRED_REFERENCE" else "none"
        return JobError(exc.code, hint)
    if isinstance(exc, OSError):
        return JobError("STORE_INVALID", reason="os_error")
    return JobError("STORE_INVALID", reason="unexpected")
