"""Cross-process reader leases on retained generations (ACI-S41).

A reader lease is a shared ``flock`` on the pre-existing, empty ``lease.lock`` of a retained
generation farm. The kernel is the only arbiter: garbage collection takes an exclusive, non-blocking
``flock`` on the same inode before it renames a farm away, so a farm that a reader holds is never
retired, and a reader that loses the race gets an explicit, retryable expiry. A process that dies
drops its lease with its descriptors.

Reading is write-free: every open is read-only without following links, and nothing is created,
linked, renamed, unlinked or touched. This module serves retained generations through
``storage.leases`` only; the MCP query server still reads only the current generation.

Limits: flock must be enforced by the filesystem. Local Linux filesystems, overlayfs, named Docker
volumes and read-only bind mounts qualify; NFS and Docker Desktop host bind mounts do not.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import os
import sqlite3
import stat
import time
from collections.abc import Callable, Mapping
from typing import Any, Literal

from atlas_aci.codegraph import CodeGraph
from atlas_aci.enforcement import ToolError
from atlas_aci.storage import generations as gen
from atlas_aci.storage import snapshots as snap

LEASE_RETRY_HINT = "retry_current"
LEASE_SEAMS = ("pre_lock", "post_lock")
ACQUIRE_ATTEMPTS = 3
ACQUIRE_BACKOFF_SECONDS = 0.02
EXPIRED_MESSAGE = "Generation reference expired or retired."
INVALID_MESSAGE = "Invalid generation reference."
UNAVAILABLE_MESSAGE = "Generation unavailable."


def _expired() -> ToolError:
    return ToolError("EXPIRED_REFERENCE", EXPIRED_MESSAGE, LEASE_RETRY_HINT)


def _flock(fd: int, operation: int) -> bool:
    """Try one non-blocking flock; True when granted, False on contention."""
    try:
        fcntl.flock(fd, operation | fcntl.LOCK_NB)
        return True
    except OSError as exc:
        if exc.errno in (errno.EAGAIN, errno.EACCES):
            return False
        raise


def _flock_retry(fd: int, operation: int) -> bool:
    """Non-blocking flock with a short backoff (absorbs a status probe)."""
    for attempt in range(ACQUIRE_ATTEMPTS):
        if _flock(fd, operation):
            return True
        if attempt + 1 < ACQUIRE_ATTEMPTS:
            time.sleep(ACQUIRE_BACKOFF_SECONDS)
    return False


class GenerationLease:
    """A held lease: the farm is retained, readable and unchanged until ``close``."""

    def __init__(
        self,
        generation_id: str,
        snapshot_id: str | None,
        origin: Literal["retained", "live"],
        db: sqlite3.Connection,
        source: snap.SourceLease | None,
        lock_fd: int,
        closer: Callable[[], None] | None = None,
    ):
        self.generation_id = generation_id
        self.snapshot_id = snapshot_id
        self.origin = origin
        self.db = db
        self._source = source
        self._lock_fd = lock_fd
        self._closer = closer
        self._closed = False

    def read_source(self, path: str, source_sha256: str | None = None) -> bytes:
        if self._closed or self._source is None:
            raise ToolError(
                "SOURCE_UNAVAILABLE",
                "This generation has no authenticated source bytes.",
                "different_tool",
            )
        try:
            return self._source.read(path, source_sha256)
        except (ValueError, OSError) as exc:
            raise ToolError("SOURCE_UNAVAILABLE", "Source unavailable.", "different_tool") from exc

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(sqlite3.Error):
            self.db.close()
        if self._source is not None:
            self._source.close()
        if self._closer is not None:
            self._closer()
        if self._lock_fd >= 0:
            with contextlib.suppress(OSError):
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            os.close(self._lock_fd)
            self._lock_fd = -1

    def __enter__(self) -> GenerationLease:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def _live_lease(ns: gen.Namespace, generation_id: str) -> GenerationLease:
    """The farm is absent: serve the live generation when it is the requested one (FD-pinned)."""
    graph = CodeGraph(ns.repo, read_only=True, graph_root=ns.graph_root)
    manager = graph.reader_lease()
    try:
        reader = manager.__enter__()
    except ToolError as exc:
        graph.close()
        if exc.code == "INDEX_UNAVAILABLE":
            raise _expired() from None
        raise
    except BaseException:
        graph.close()
        raise

    def closer() -> None:
        manager.__exit__(None, None, None)
        graph.close()

    if reader._lease_generation_id != generation_id:
        closer()
        raise _expired()
    return GenerationLease(
        generation_id,
        reader.snapshot_id,
        "live",
        reader.db,
        reader._source_lease,
        -1,
        closer,
    )


def acquire(
    ns: gen.Namespace,
    generation_id: str,
    *,
    hooks: Mapping[str, Callable[[], None]] | None = None,
) -> GenerationLease:
    """Acquire a shared lease on a retained generation, or raise a typed ``ToolError``.

    Outcomes: a readable lease (origin ``retained`` for a farm, ``live`` for the current
    generation of a store that has no farm); ``INVALID_REFERENCE`` for a malformed id;
    ``EXPIRED_REFERENCE`` (``retry_hint=retry_current``) when the generation was retired or is being
    retired; ``SNAPSHOT_UNAVAILABLE`` when a held farm fails verification.
    """
    if not isinstance(generation_id, str) or gen.GID.fullmatch(generation_id) is None:
        raise ToolError("INVALID_REFERENCE", INVALID_MESSAGE, "none")
    try:
        gens_fd = gen.open_dir(ns.generations)
    except OSError:
        return _live_lease(ns, generation_id)
    farm_fd = lock_fd = -1
    try:
        try:
            farm_fd = gen.open_subdir(gens_fd, generation_id)
        except OSError as exc:
            if exc.errno in (errno.ENOENT, errno.ENOTDIR, errno.ELOOP):
                return _live_lease(ns, generation_id)
            raise
        try:
            lock_fd = os.open(
                "lease.lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=farm_fd
            )
        except OSError:
            raise _expired() from None
        gen.fire(hooks, "pre_lock")
        if not _flock_retry(lock_fd, fcntl.LOCK_SH):
            raise _expired()
        gen.fire(hooks, "post_lock")
        # The grant is valid only if the name and the lock file are still the ones we opened.
        named = gen.lstat_or_none(generation_id, gens_fd)
        held = os.fstat(farm_fd)
        if named is None or (named.st_dev, named.st_ino) != (held.st_dev, held.st_ino):
            raise _expired()
        locked = os.fstat(lock_fd)
        current = gen.lstat_or_none("lease.lock", farm_fd)
        if (
            current is None
            or not stat.S_ISREG(locked.st_mode)
            or (current.st_dev, current.st_ino) != (locked.st_dev, locked.st_ino)
        ):
            raise _expired()
        farm = ns.generations / generation_id
        try:
            meta = gen.read_meta(farm_fd, generation_id)
            verified = gen.verify_generation(ns, farm, meta)
        except (ValueError, OSError, OverflowError):
            raise ToolError("SNAPSHOT_UNAVAILABLE", UNAVAILABLE_MESSAGE, "different_tool") from None
        lease = GenerationLease(
            verified.generation_id,
            verified.snapshot_id,
            "retained",
            verified.db,
            verified.source,
            lock_fd,
        )
        lock_fd = -1
        return lease
    finally:
        if lock_fd >= 0:
            with contextlib.suppress(OSError):
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        if farm_fd >= 0:
            os.close(farm_fd)
        os.close(gens_fd)


def probe_leased(farm: Any) -> bool:
    """True when a reader holds the farm (an exclusive probe with immediate release; read-only).

    A missing ``lease.lock`` means no reader can hold it. The probe briefly contends with
    acquisition, which is why acquisition and GC retry a short backoff.
    """
    try:
        fd = os.open(farm / "lease.lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return False
    try:
        for attempt in range(ACQUIRE_ATTEMPTS):
            if _flock(fd, fcntl.LOCK_EX):
                fcntl.flock(fd, fcntl.LOCK_UN)
                return False
            if attempt + 1 < ACQUIRE_ATTEMPTS:
                time.sleep(ACQUIRE_BACKOFF_SECONDS)
        return True
    finally:
        os.close(fd)
