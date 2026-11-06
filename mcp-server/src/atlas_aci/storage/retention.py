"""Lease-aware garbage collection of retained generations (ACI-S41).

Roots are never retired: the current generation, every generation a reader holds (an exclusive,
non-blocking ``flock`` on the farm's ``lease.lock`` fails), the newest ``keep_last`` farms by
capture sequence (trimmed by ``max_age_seconds`` and ``max_bytes``) and the base of a running job. A
candidate is renamed away under the writer lock, while its exclusive lock is held (O(1)), and
deleted outside the lock. A crash leaves ``generations/retired/``, which the next run removes.

GC never touches the live store (``graph.9.db``, ``source-snapshots/``). A running build holds the
writer lock for its whole duration, so GC returns ``WRITER_BUSY`` during builds: the capture, the
orphan sweep and the cache enumeration all happen inside the writer lock.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import hashlib
import os
import secrets
import stat
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from atlas_aci.codegraph import SCHEMA_EPOCH
from atlas_aci.storage import evidence as ev
from atlas_aci.storage import generations as gen

GC_SEAMS = (
    "gc_post_recover",
    "gc_post_capture",
    "gc_post_sweep",
    "gc_post_reachability",
    "gc_post_lock_ex",
    "gc_post_rename",
    "gc_mid_delete",
    "gc_pre_receipt",
)
FORMAT_RECEIPT = "atlas-aci-gc-receipt.v1"
RETAIN_REASONS = ("current", "leased", "job_base", "window")
RETIRE_REASONS = ("outside_window", "max_age", "max_bytes", "invalid_meta")
TERMINAL_STATES = ("completed", "cancelled", "failed")
LOCK_ATTEMPTS = 3
LOCK_BACKOFF_SECONDS = 0.02
SWEEP_KEYS = (
    "graph_stage",
    "sources_stage",
    "attempt_temp",
    "cache_temp",
    "capture_stage",
    "job_temp",
)
_HEX64 = gen.GID


def _flock(fd: int, operation: int) -> bool:
    try:
        fcntl.flock(fd, operation | fcntl.LOCK_NB)
        return True
    except OSError as exc:
        if exc.errno in (errno.EAGAIN, errno.EACCES):
            return False
        raise


def _flock_retry(fd: int, operation: int) -> bool:
    """Non-blocking flock with a 3 x 20 ms backoff, so a concurrent status probe is absorbed."""
    for attempt in range(LOCK_ATTEMPTS):
        if _flock(fd, operation):
            return True
        if attempt + 1 < LOCK_ATTEMPTS:
            time.sleep(LOCK_BACKOFF_SECONDS)
    return False


def _open_gc_lock(ns: gen.Namespace) -> int:
    fd = os.open(
        ns.generations / "gc.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644
    )
    if not _flock_retry(fd, fcntl.LOCK_EX):
        os.close(fd)
        raise gen.StoreError("gc_busy")
    return fd


def _release(fd: int) -> None:
    with contextlib.suppress(OSError):
        fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


def _take_ex(gens_fd: int, gid: str) -> int | None:
    """EX|NB on a farm's lease.lock: an fd (held), -1 (no lock file: no reader can hold it), or
    None (a reader holds it)."""
    info = gen.lstat_or_none(gid, gens_fd)
    if info is None or not stat.S_ISDIR(info.st_mode):
        return -1
    try:
        farm_fd = gen.open_subdir(gens_fd, gid)
    except OSError:
        return -1
    try:
        try:
            fd = os.open(
                "lease.lock",
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                dir_fd=farm_fd,
            )
        except OSError:
            return -1
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            return -1
        if not _flock_retry(fd, fcntl.LOCK_EX):
            os.close(fd)
            return None
        return fd
    finally:
        os.close(farm_fd)


def _retired_name(gid: str) -> str:
    return f"retired/{gid}.{secrets.token_hex(6)}"


def _ensure_retired(ns: gen.Namespace) -> None:
    gen.ensure_dir(ns.generations / "retired")


def retire_locked(ns: gen.Namespace, gid: str) -> bool:
    """Retire one farm now. The caller holds ``gc.lock``. False when a reader holds the farm."""
    try:
        gens_fd = gen.open_dir(ns.generations)
    except FileNotFoundError:
        return True
    try:
        held = _take_ex(gens_fd, gid)
        if held is None:
            return False
        if gen.lstat_or_none(gid, gens_fd) is None:
            if held >= 0:
                _release(held)
            return True
        _ensure_retired(ns)
        name = _retired_name(gid)
        try:
            os.rename(gid, name, src_dir_fd=gens_fd, dst_dir_fd=gens_fd)
            gen.remove_tree(gens_fd, name)
        finally:
            if held >= 0:
                _release(held)
        return True
    finally:
        os.close(gens_fd)


def retire_invalid(ns: gen.Namespace, generation_id: str) -> bool:
    """Retire one invalid (or stale) farm under ``gc.lock`` (NB; ``gc_busy`` on contention).

    True when the farm is gone, False when a reader holds it. Recovery of ``retired/`` tolerates a
    concurrent removal (ENOENT) and cannot race this function because both hold ``gc.lock``.
    """
    if gen.GID.fullmatch(generation_id) is None:
        raise ValueError("generation id")
    if gen.lstat_or_none(ns.generations) is None:
        return True
    fd = _open_gc_lock(ns)
    try:
        return retire_locked(ns, generation_id)
    finally:
        _release(fd)


def _recover(ns: gen.Namespace) -> int:
    """Delete every ``retired/*`` left by a crashed run; tolerates entries that vanish."""
    try:
        retired_fd = gen.open_dir(ns.generations / "retired")
    except FileNotFoundError:
        return 0
    count = 0
    try:
        for name in sorted(os.listdir(retired_fd)):
            gen.remove_tree(retired_fd, name)
            count += 1
    finally:
        os.close(retired_fd)
    return count


def _remove_matching(dir_path: Path, prefix: str) -> int:
    count = 0
    try:
        fd = gen.open_dir(dir_path)
    except OSError:
        return 0
    try:
        for name in sorted(os.listdir(fd)):
            if name.startswith(prefix):
                gen.remove_tree(fd, name)
                count += 1
    finally:
        os.close(fd)
    return count


class _Job:
    def __init__(self, job_id: str, state: str, base: str | None, mtime: int, running: bool):
        self.job_id = job_id
        self.state = state
        self.base = base
        self.mtime = mtime
        self.running = running


def _job_dirs(ns: gen.Namespace) -> list[str]:
    try:
        names = sorted(os.listdir(ns.atlas_dir / "jobs"))
    except OSError:
        return []
    return [n for n in names if _HEX64.fullmatch(n)]


def _probe_run_lock(job_dir: Path) -> int | None:
    """EX|NB on a job's run.lock: an fd (free, held by us), -1 (no lock file) or None (running)."""
    try:
        fd = os.open(
            job_dir / "run.lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        )
    except OSError:
        return -1
    if not _flock_retry(fd, fcntl.LOCK_EX):
        os.close(fd)
        return None
    return fd


def _scan_jobs(ns: gen.Namespace) -> list[_Job]:
    jobs: list[_Job] = []
    for job_id in _job_dirs(ns):
        job_dir = ns.atlas_dir / "jobs" / job_id
        held = _probe_run_lock(job_dir)
        running = held is None
        if held is not None and held >= 0:
            _release(held)
        state, base, mtime = "", None, 0
        try:
            doc = gen.read_json(None, job_dir / "job.json", gen.DOC_CAP)
            state = str(doc.get("state", ""))
            candidate = doc.get("base_generation_id")
            base = candidate if isinstance(candidate, str) and _HEX64.fullmatch(candidate) else None
            mtime = int((job_dir / "job.json").stat().st_mtime)
        except (OSError, ValueError, OverflowError, AttributeError):
            pass
        jobs.append(_Job(job_id, state, base, mtime, running))
    return jobs


def _checkpoint_keys(job_dir: Path) -> set[str]:
    keys: set[str] = set()
    try:
        raw = gen.read_bounded(None, job_dir / "checkpoint.log", 64 * 1024 * 1024)
    except (OSError, OverflowError, ValueError):
        return keys
    for line in raw.split(b"\n"):
        if not line:
            continue
        try:
            item = gen.load_json_bytes(line)
        except ValueError:
            break
        if isinstance(item, dict) and isinstance(item.get("key"), str):
            keys.add(item["key"])
    return keys


def _sweep(ns: gen.Namespace) -> dict[str, int]:
    """Orphan staging, only under the writer lock (no build can be live)."""
    swept = dict.fromkeys(SWEEP_KEYS, 0)
    atlas = ns.atlas_dir
    swept["graph_stage"] = _remove_matching(atlas, f".graph.{SCHEMA_EPOCH}.db.tmp.")
    swept["sources_stage"] = _remove_matching(atlas, ".sources-stage.")
    swept["attempt_temp"] = _remove_matching(atlas, ".index-attempt.")
    cache = atlas / "extraction-cache" / "v1"
    try:
        shards = sorted(os.listdir(cache))
    except OSError:
        shards = []
    for shard in shards:
        swept["cache_temp"] += _remove_matching(cache / shard, ".entry-")
    swept["capture_stage"] = _remove_matching(ns.generations, ".stage.")
    for job_id in _job_dirs(ns):
        job_dir = atlas / "jobs" / job_id
        held = _probe_run_lock(job_dir)
        if held is None:
            continue
        try:
            swept["job_temp"] += _remove_matching(job_dir, ".tmp.")
        finally:
            if held >= 0:
                _release(held)
    return swept


def _cache_entries(ns: gen.Namespace) -> list[tuple[int, str, int, Path]]:
    rows: list[tuple[int, str, int, Path]] = []
    cache = ns.atlas_dir / "extraction-cache" / "v1"
    try:
        shards = sorted(os.listdir(cache))
    except OSError:
        return rows
    for shard in shards:
        try:
            names = sorted(os.listdir(cache / shard))
        except OSError:
            continue
        for name in names:
            if not name.endswith(".json") or _HEX64.fullmatch(name[:-5]) is None:
                continue
            info = gen.lstat_or_none(cache / shard / name)
            if info is not None and stat.S_ISREG(info.st_mode):
                rows.append((info.st_mtime_ns, name[:-5], info.st_size, cache / shard / name))
    return sorted(rows)


def _prune_cache(ns: gen.Namespace, bound: int, protected: set[str]) -> dict[str, int]:
    rows = _cache_entries(ns)
    total = sum(size for _m, _k, size, _p in rows)
    pruned = 0
    for _mtime, key, size, path in rows:
        if total <= bound:
            break
        if key in protected:
            continue
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)
        total -= size
        pruned += 1
    remaining = _cache_entries(ns)
    return {
        "entries": len(remaining),
        "bytes": sum(size for _m, _k, size, _p in remaining),
        "pruned": pruned,
    }


def retained_set(ns: gen.Namespace) -> dict[str, Any]:
    """The canonical retained artifact set, from disk: farms (by id) and the cache keys."""
    rows = []
    for meta in sorted(gen.list_generations(ns), key=lambda m: m.generation_id):
        if meta.valid:
            rows.append([meta.generation_id, meta.snapshot_id, meta.db_sha256, meta.pack_sha256])
        else:
            rows.append([meta.generation_id, None, None, None])
    keys = sorted(key for _m, key, _s, _p in _cache_entries(ns))
    return {
        "generations": rows,
        "cache_keys_sha256": hashlib.sha256(ev.canonical(keys).encode()).hexdigest(),
    }


def retained_digest(ns: gen.Namespace) -> str:
    return hashlib.sha256(ev.canonical(retained_set(ns)).encode()).hexdigest()


def gc(
    ns: gen.Namespace,
    *,
    policy: gen.RetentionPolicy,
    now: int,
    wait_seconds: float = 30.0,
    hooks: dict[str, Callable[[], None]] | None = None,
) -> dict[str, Any]:
    """One garbage-collection pass; returns (and writes) the ``atlas-aci-gc-receipt.v1``."""
    if not ns.atlas_dir.is_dir():
        raise gen.StoreError("index_unavailable")
    gen.ensure_dir(ns.generations)
    lock_fd = _open_gc_lock(ns)
    pending: list[tuple[str, str, int, str]] = []
    try:
        recovered = _recover(ns)
        gen.fire(hooks, "gc_post_recover")
        retained: dict[str, str] = {}
        retired: dict[str, str] = {}
        with gen.writer_lock(ns, wait_seconds=wait_seconds):
            captured = gen.capture_current(ns, now=now, locked=True, hooks=hooks, gc_held=True)
            current_gid = captured.generation_id if captured is not None else None
            gen.fire(hooks, "gc_post_capture")
            swept = _sweep(ns)
            gen.fire(hooks, "gc_post_sweep")
            metas = gen.list_generations(ns)
            valid = {m.generation_id: m for m in metas if m.valid}
            jobs = _scan_jobs(ns)
            roots: dict[str, str] = {}
            if current_gid is not None and current_gid in valid:
                roots[current_gid] = "current"
            for job in jobs:
                if job.running and job.base is not None and job.base in valid:
                    roots.setdefault(job.base, "job_base")
            ordered = sorted(valid.values(), key=lambda m: (-m.seq, m.generation_id))
            window = [m for m in ordered[: policy.keep_last]]
            dropped: dict[str, str] = {}
            if policy.max_age_seconds is not None:
                for meta in list(window):
                    if (
                        meta.generation_id not in roots
                        and now - meta.captured_at > policy.max_age_seconds
                    ):
                        window.remove(meta)
                        dropped[meta.generation_id] = "max_age"
            kept = {m.generation_id: m for m in window}
            if policy.max_bytes is not None:

                def total() -> int:
                    rows: list[tuple[int, int, int]] = []
                    for gid in {*roots, *kept}:
                        rows.extend(gen.farm_files(ns, gid))
                    return gen.distinct_bytes(rows)

                trimmable = sorted(
                    (m for m in kept.values() if m.generation_id not in roots),
                    key=lambda m: (m.seq, m.generation_id),
                )
                while trimmable and total() > policy.max_bytes:
                    victim = trimmable.pop(0)
                    del kept[victim.generation_id]
                    dropped[victim.generation_id] = "max_bytes"
            keep_ids = {*roots, *kept}
            for gid in keep_ids:
                retained[gid] = roots.get(gid, "window")
            candidates = sorted(
                (m for m in metas if m.generation_id not in keep_ids),
                key=lambda m: (m.seq if m.valid else -1, m.generation_id),
            )
            gen.fire(hooks, "gc_post_reachability")
            gens_fd = gen.open_dir(ns.generations)
            try:
                for meta in candidates:
                    gid = meta.generation_id
                    reason = (
                        "invalid_meta" if not meta.valid else dropped.get(gid, "outside_window")
                    )
                    held = _take_ex(gens_fd, gid)
                    if held is None:
                        retained[gid] = "leased"
                        continue
                    gen.fire(hooks, "gc_post_lock_ex")
                    _ensure_retired(ns)
                    name = _retired_name(gid)
                    os.rename(gid, name, src_dir_fd=gens_fd, dst_dir_fd=gens_fd)
                    pending.append((gid, name, held, reason))
                    retired[gid] = reason
                    gen.fire(hooks, "gc_post_rename")
            finally:
                os.close(gens_fd)
            cache: dict[str, int]
            if policy.max_cache_bytes is not None:
                protected: set[str] = set()
                for job in jobs:
                    abandoned = (
                        not job.running
                        and policy.max_age_seconds is not None
                        and now - job.mtime > policy.max_age_seconds
                    )
                    if job.state not in TERMINAL_STATES and not abandoned:
                        protected |= _checkpoint_keys(ns.atlas_dir / "jobs" / job.job_id)
                cache = _prune_cache(ns, policy.max_cache_bytes, protected)
            else:
                rows = _cache_entries(ns)
                cache = {"entries": len(rows), "bytes": sum(r[2] for r in rows), "pruned": 0}
        retired_fd = -1
        try:
            if pending:
                retired_fd = gen.open_dir(ns.generations / "retired")
            for _gid, name, held, _reason in pending:
                gen.remove_tree(
                    retired_fd, name.split("/", 1)[1], lambda: gen.fire(hooks, "gc_mid_delete")
                )
                if held >= 0:
                    _release(held)
            pending.clear()
        finally:
            if retired_fd >= 0:
                os.close(retired_fd)
        gen.fire(hooks, "gc_pre_receipt")
        retention_set = retained_set(ns)
        receipt: dict[str, Any] = {
            "format": FORMAT_RECEIPT,
            "now": now,
            "policy": policy.as_dict(),
            "current_generation_id": current_gid,
            "retained": [
                {"generation_id": gid, "reason": retained[gid]} for gid in sorted(retained)
            ],
            "retired": [{"generation_id": gid, "reason": retired[gid]} for gid in sorted(retired)],
            "recovered": recovered,
            "swept": swept,
            "cache": cache,
            "retained_set": retention_set,
            "retained_digest": hashlib.sha256(ev.canonical(retention_set).encode()).hexdigest(),
        }
        gens_fd = gen.open_dir(ns.generations)
        try:
            gen.write_json_atomic(gens_fd, "gc-receipt.json", receipt)
        finally:
            os.close(gens_fd)
        return receipt
    finally:
        for _gid, _name, held, _reason in pending:
            if held >= 0:
                _release(held)
        _release(lock_fd)
