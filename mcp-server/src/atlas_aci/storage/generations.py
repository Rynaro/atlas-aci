"""Retained generations: the on-disk farm, the retention policy, capture and verification (ACI-S41).

A retained generation is a same-filesystem hardlink farm ``<atlas_dir>/generations/<gid>/`` holding
the captured ``graph.9.db``, the snapshot manifest and pack (``source-snapshots/``), an empty
``lease.lock`` and ``meta.json``. The farm is immutable after publication. Hardlinks share inodes
with the live store: a farm never copies bytes, and bit-rot on a shared inode hits both names.

Nothing here edits the live store (``graph.9.db``, ``source-snapshots/``): this module only reads it
and links to it. The query server never imports this module; it reads only the current generation.

Private coupling to ``CodeGraph`` (guarded by the L-PARITY tests; ``codegraph.py`` is frozen):
``_generation_data``, ``_snapshot_binding``, ``_snapshot_context``, ``_acquisition_observations``,
``_acquisition_retrieval_documents``, plus ``atlas_aci.indexing.retrieval.expected`` and
``descriptor``. ``verify_generation`` repeats the validation steps of ``CodeGraph.reader_lease`` on
a farm instead of on the live store.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from atlas_aci.codegraph import SCHEMA_EPOCH, CodeGraph
from atlas_aci.indexing import retrieval as ret
from atlas_aci.storage import evidence as ev
from atlas_aci.storage import snapshots as snap

FORMAT_META = "atlas-aci-generation-meta.v1"
FORMAT_POLICY = "atlas-aci-retention.v1"
GID = re.compile(r"[0-9a-f]{64}")
DB_NAME = f"graph.{SCHEMA_EPOCH}.db"
SNAP_DIR = "source-snapshots"
STORE_REASONS = (
    "cross_device",
    "writer_busy",
    "index_unavailable",
    "meta_invalid",
    "capture_conflict",
    "leased_invalid",
    "gc_busy",
)
POLICY_REASONS = (
    "unreadable",
    "too_large",
    "not_json",
    "duplicate_key",
    "unknown_field",
    "wrong_type",
    "out_of_range",
    "bad_format",
)
CAPTURE_SEAMS = ("capture_post_link", "capture_pre_rename")
META_CAP = 64 * 1024
DOC_CAP = 1024 * 1024
MAX_SAFE_INT = 2**53
PROBE_ATTEMPTS = 3
PROBE_BACKOFF_SECONDS = 0.02
WRITER_POLL_SECONDS = 0.05
_META_KINDS = frozenset({"build", "import", "legacy_conversion", "scip_import", "coverage_import"})
_META_KEYS = frozenset(
    {
        "format",
        "generation_id",
        "snapshot_id",
        "kind",
        "repository",
        "namespace",
        "seq",
        "captured_at",
        "db_sha256",
        "db_size",
        "pack_sha256",
        "pack_size",
    }
)

Hooks = Mapping[str, Callable[[], None]]


class StoreError(Exception):
    """A typed store failure. The text is fixed: it never carries a path or file content."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"store error ({reason})")


class PolicyError(ValueError):
    def __init__(self, reason: str, field: str):
        self.reason = reason
        self.field = field
        super().__init__(f"invalid retention policy ({reason} at {field or '/'})")


class GenerationInvalid(ValueError):
    """A farm failed verification. The text is fixed."""

    def __init__(self) -> None:
        super().__init__("generation invalid")


@dataclass(frozen=True)
class RetentionPolicy:
    keep_last: int = 2
    max_age_seconds: int | None = None
    max_bytes: int | None = None
    max_cache_bytes: int | None = None
    max_concurrent_jobs: int = 1
    checkpoint_summary_interval: int = 64

    def as_dict(self) -> dict[str, Any]:
        return {
            "keep_last": self.keep_last,
            "max_age_seconds": self.max_age_seconds,
            "max_bytes": self.max_bytes,
            "max_cache_bytes": self.max_cache_bytes,
            "max_concurrent_jobs": self.max_concurrent_jobs,
            "checkpoint_summary_interval": self.checkpoint_summary_interval,
        }


@dataclass(frozen=True)
class Namespace:
    repo: Path
    graph_root: Path | None
    atlas_dir: Path

    @property
    def identity(self) -> str:
        return hashlib.sha256(str(self.repo).encode("utf-8")).hexdigest()

    @property
    def generations(self) -> Path:
        return self.atlas_dir / "generations"


@dataclass(frozen=True)
class Meta:
    generation_id: str
    valid: bool = True
    snapshot_id: str | None = None
    kind: str = ""
    repository: str = ""
    namespace: str = ""
    seq: int = 0
    captured_at: int = 0
    db_sha256: str = ""
    db_size: int = 0
    pack_sha256: str | None = None
    pack_size: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "format": FORMAT_META,
            "generation_id": self.generation_id,
            "snapshot_id": self.snapshot_id,
            "kind": self.kind,
            "repository": self.repository,
            "namespace": self.namespace,
            "seq": self.seq,
            "captured_at": self.captured_at,
            "db_sha256": self.db_sha256,
            "db_size": self.db_size,
            "pack_sha256": self.pack_sha256,
            "pack_size": self.pack_size,
        }


@dataclass
class Verified:
    generation_id: str
    snapshot_id: str | None
    db: sqlite3.Connection
    source: snap.SourceLease | None

    def close(self) -> None:
        with contextlib.suppress(sqlite3.Error):
            self.db.close()
        if self.source is not None:
            self.source.close()


def fire(hooks: Hooks | None, name: str) -> None:
    if hooks is not None and name in hooks:
        hooks[name]()


# ------------------------------------------------------------------------------ files and JSON
def canonical_line(value: Any) -> bytes:
    return (ev.canonical(value) + "\n").encode("utf-8")


def sha256_file(path: Path) -> str:
    return sha256_at(None, path)


def sha256_at(dir_fd: int | None, name: str | Path) -> str:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    fd = os.open(name, flags, dir_fd=dir_fd)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise GenerationInvalid
        digest = hashlib.sha256()
        position = 0
        while True:
            block = os.pread(fd, snap.CHUNK, position)
            if not block:
                return digest.hexdigest()
            digest.update(block)
            position += len(block)
    finally:
        os.close(fd)


def fsync_dir(fd: int) -> None:
    with contextlib.suppress(OSError):
        os.fsync(fd)


def open_dir(path: Path) -> int:
    """Open an absolute directory without following any component; never creates."""
    return snap.directory(path)


def open_subdir(parent_fd: int, name: str) -> int:
    return os.open(
        name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent_fd
    )


def lstat_or_none(path: Path | str, dir_fd: int | None = None) -> os.stat_result | None:
    try:
        return os.stat(path, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


class _DuplicateKey(ValueError):
    pass


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise _DuplicateKey(key)
        result[key] = value
    return result


def _constant(name: str) -> Any:
    raise ValueError("non-finite number")


def load_json_bytes(data: bytes) -> Any:
    """Strict parse: UTF-8, no duplicate keys, no NaN or Infinity."""
    return json.loads(data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)


def read_bounded(dir_fd: int | None, name: str | Path, cap: int) -> bytes:
    """Read a regular file of at most ``cap`` bytes without following a symlink."""
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dir_fd)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("not a regular file")
        data = b""
        while len(data) <= cap:
            block = os.read(fd, cap + 1 - len(data))
            if not block:
                break
            data += block
        if len(data) > cap:
            raise OverflowError("too large")
        return data
    finally:
        os.close(fd)


def read_json(dir_fd: int | None, name: str | Path, cap: int) -> Any:
    return load_json_bytes(read_bounded(dir_fd, name, cap))


def write_bytes_atomic(dir_fd: int, name: str, data: bytes) -> None:
    """tmp (O_EXCL, 0600), fsync, ``os.replace``, fsync the directory."""
    temp = ".tmp." + secrets.token_hex(8)
    fd = os.open(
        temp,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o644,
        dir_fd=dir_fd,
    )
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp, dir_fd=dir_fd)
        raise
    fsync_dir(dir_fd)


def write_json_atomic(dir_fd: int, name: str, value: Any) -> None:
    """Canonical JSON plus a newline, written atomically."""
    write_bytes_atomic(dir_fd, name, canonical_line(value))


def remove_tree(
    parent_fd: int, name: str, on_first_unlink: Callable[[], None] | None = None
) -> None:
    """Remove ``name`` (a file, symlink or directory) below ``parent_fd``; never follows a link."""
    state = {"first": on_first_unlink}

    def unlink_note(child: str, fd: int) -> None:
        os.unlink(child, dir_fd=fd)
        callback = state["first"]
        if callback is not None:
            state["first"] = None
            callback()

    def walk(fd: int, child: str) -> None:
        try:
            info = os.stat(child, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if not stat.S_ISDIR(info.st_mode):
            with contextlib.suppress(FileNotFoundError):
                unlink_note(child, fd)
            return
        sub = os.open(child, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
        try:
            for entry in sorted(os.listdir(sub)):
                walk(sub, entry)
        finally:
            os.close(sub)
        with contextlib.suppress(FileNotFoundError):
            os.rmdir(child, dir_fd=fd)

    walk(parent_fd, name)


# ------------------------------------------------------------------------------------ policy
def _policy_int(value: Any, field: str, low: int, high: int, nullable: bool) -> int | None:
    if value is None and nullable:
        return None
    if type(value) is not int:
        raise PolicyError("wrong_type", field)
    if not low <= value <= high:
        raise PolicyError("out_of_range", field)
    return value


def parse_policy(data: bytes) -> RetentionPolicy:
    try:
        document = load_json_bytes(data)
    except _DuplicateKey:
        raise PolicyError("duplicate_key", "") from None
    except ValueError:
        raise PolicyError("not_json", "") from None
    if not isinstance(document, dict):
        raise PolicyError("wrong_type", "")
    if document.get("format") != FORMAT_POLICY:
        raise PolicyError("bad_format", "/format")
    known = {
        "format",
        "keep_last",
        "max_age_seconds",
        "max_bytes",
        "max_cache_bytes",
        "max_concurrent_jobs",
        "checkpoint_summary_interval",
    }
    for key in sorted(document):
        if key not in known:
            raise PolicyError("unknown_field", "/" + key)
    defaults = RetentionPolicy()
    keep = defaults.keep_last
    if "keep_last" in document:
        keep = _policy_int(document["keep_last"], "/keep_last", 1, 64, False) or keep
    jobs = defaults.max_concurrent_jobs
    if "max_concurrent_jobs" in document:
        jobs = (
            _policy_int(document["max_concurrent_jobs"], "/max_concurrent_jobs", 1, 16, False)
            or jobs
        )
    interval = defaults.checkpoint_summary_interval
    if "checkpoint_summary_interval" in document:
        interval = (
            _policy_int(
                document["checkpoint_summary_interval"],
                "/checkpoint_summary_interval",
                1,
                4096,
                False,
            )
            or interval
        )
    return RetentionPolicy(
        keep_last=keep,
        max_age_seconds=(
            _policy_int(document["max_age_seconds"], "/max_age_seconds", 0, 31536000, True)
            if "max_age_seconds" in document
            else None
        ),
        max_bytes=(
            _policy_int(document["max_bytes"], "/max_bytes", 0, MAX_SAFE_INT, True)
            if "max_bytes" in document
            else None
        ),
        max_cache_bytes=(
            _policy_int(document["max_cache_bytes"], "/max_cache_bytes", 0, MAX_SAFE_INT, True)
            if "max_cache_bytes" in document
            else None
        ),
        max_concurrent_jobs=jobs,
        checkpoint_summary_interval=interval,
    )


def load_policy(ns: Namespace) -> RetentionPolicy:
    """``<atlas_dir>/retention.json`` (operator-written, <= 64 KiB); absent means the defaults."""
    path = ns.atlas_dir / "retention.json"
    try:
        data = read_bounded(None, path, META_CAP)
    except FileNotFoundError:
        return RetentionPolicy()
    except OverflowError:
        raise PolicyError("too_large", "") from None
    except (OSError, ValueError):
        raise PolicyError("unreadable", "") from None
    return parse_policy(data)


# ----------------------------------------------------------------------------------- namespace
def namespace(repo: Path, graph_root: Path | None) -> Namespace:
    """Resolve the store namespace exactly as ``CodeGraph`` does; creates nothing."""
    graph = CodeGraph(repo, read_only=True, graph_root=graph_root)
    try:
        return Namespace(repo=graph.repo, graph_root=graph.graph_root, atlas_dir=graph.atlas_dir)
    finally:
        graph.close()


@contextmanager
def writer_lock(
    ns: Namespace, *, wait_seconds: float = 30.0, tick: Callable[[], None] | None = None
) -> Iterator[None]:
    """The writer lock shared with ``CodeGraph.build`` (``.index.lock``): EX with an NB poll."""
    if not ns.atlas_dir.is_dir():
        raise StoreError("index_unavailable")
    fd = os.open(ns.atlas_dir / ".index.lock", os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o644)
    try:
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EACCES):
                    raise
            if time.monotonic() >= deadline:
                raise StoreError("writer_busy")
            if tick is not None:
                tick()
            time.sleep(WRITER_POLL_SECONDS)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------------- current
def _current_info(ns: Namespace) -> tuple[str, str | None, str] | None:
    """(generation_id, snapshot_id, kind) of the live generation, read through a reader lease."""
    from atlas_aci.enforcement import ToolError

    graph = CodeGraph(ns.repo, read_only=True, graph_root=ns.graph_root)
    try:
        with graph.reader_lease() as reader:
            row = reader.db.execute(
                "SELECT value FROM manifest WHERE key='generation_inputs'"
            ).fetchone()
            kind = str(json.loads(row[0])["kind"])
            return reader._lease_generation_id, reader.snapshot_id, kind
    except ToolError as exc:
        if exc.code == "INDEX_UNAVAILABLE":
            return None
        raise StoreError("index_unavailable") from None
    finally:
        graph.close()


def current_generation(ns: Namespace) -> tuple[str, str | None] | None:
    """(generation_id, snapshot_id) of the live generation, or None when nothing is published."""
    info = _current_info(ns)
    return None if info is None else (info[0], info[1])


# ------------------------------------------------------------------------------------- meta
def parse_meta(value: Any, name: str) -> Meta:
    if not isinstance(value, dict) or set(value) != _META_KEYS:
        raise ValueError("meta keys")
    hexa = ev.hash_value
    gid = value["generation_id"]
    sid, pack, size = value["snapshot_id"], value["pack_sha256"], value["pack_size"]
    ok = (
        value["format"] == FORMAT_META
        and gid == name
        and hexa(gid)
        and (sid is None or hexa(sid))
        and isinstance(value["kind"], str)
        and value["kind"] in _META_KINDS
        and isinstance(value["repository"], str)
        and hexa(value["namespace"])
        and type(value["seq"]) is int
        and value["seq"] >= 1
        and type(value["captured_at"]) is int
        and value["captured_at"] >= 0
        and hexa(value["db_sha256"])
        and type(value["db_size"]) is int
        and value["db_size"] >= 0
        and (pack is None) == (size is None) == (sid is None)
        and (pack is None or (hexa(pack) and type(size) is int and size >= 0))
    )
    if not ok:
        raise ValueError("meta values")
    return Meta(
        generation_id=gid,
        snapshot_id=sid,
        kind=value["kind"],
        repository=value["repository"],
        namespace=value["namespace"],
        seq=value["seq"],
        captured_at=value["captured_at"],
        db_sha256=value["db_sha256"],
        db_size=value["db_size"],
        pack_sha256=pack,
        pack_size=size,
    )


def read_meta(farm_fd: int, name: str) -> Meta:
    return parse_meta(read_json(farm_fd, "meta.json", META_CAP), name)


def read_meta_at(ns: Namespace, gid: str) -> Meta | None:
    """The valid meta of ``generations/<gid>``, or None when absent or invalid."""
    try:
        gens = open_dir(ns.generations)
    except OSError:
        return None
    try:
        try:
            farm = open_subdir(gens, gid)
        except OSError:
            return None
        try:
            return read_meta(farm, gid)
        except (OSError, ValueError, OverflowError):
            return None
        finally:
            os.close(farm)
    finally:
        os.close(gens)


def list_generations(ns: Namespace) -> list[Meta]:
    """Every 64-hex directory under ``generations/``; an invalid meta gives ``valid=False``."""
    try:
        gens = open_dir(ns.generations)
    except OSError:
        return []
    found: list[Meta] = []
    try:
        for name in sorted(os.listdir(gens)):
            if GID.fullmatch(name) is None:
                continue
            info = lstat_or_none(name, gens)
            if info is None:
                continue
            if not stat.S_ISDIR(info.st_mode):
                found.append(Meta(generation_id=name, valid=False))
                continue
            try:
                farm = open_subdir(gens, name)
            except OSError:
                found.append(Meta(generation_id=name, valid=False))
                continue
            try:
                found.append(read_meta(farm, name))
            except (OSError, ValueError, OverflowError):
                found.append(Meta(generation_id=name, valid=False))
            finally:
                os.close(farm)
    finally:
        os.close(gens)
    return found


def farm_files(ns: Namespace, gid: str) -> list[tuple[int, int, int]]:
    """(st_dev, st_ino, st_size) of every file of a farm (for byte accounting)."""
    farm = ns.generations / gid
    rows: list[tuple[int, int, int]] = []
    for path in (farm / DB_NAME, farm / "meta.json", farm / "lease.lock"):
        info = lstat_or_none(path)
        if info is not None and stat.S_ISREG(info.st_mode):
            rows.append((info.st_dev, info.st_ino, info.st_size))
    try:
        names = sorted(os.listdir(farm / SNAP_DIR))
    except OSError:
        names = []
    for name in names:
        info = lstat_or_none(farm / SNAP_DIR / name)
        if info is not None and stat.S_ISREG(info.st_mode):
            rows.append((info.st_dev, info.st_ino, info.st_size))
    return rows


def distinct_bytes(rows: list[tuple[int, int, int]]) -> int:
    return sum(size for _key, size in {(dev, ino): size for dev, ino, size in rows}.items())


# ---------------------------------------------------------------------------------- capture
def ensure_dir(path: Path) -> None:
    """Create one directory below an existing parent (checked first: no spurious mkdir)."""
    if lstat_or_none(path) is None:
        with contextlib.suppress(FileExistsError):
            os.mkdir(path)


def _farm_matches_live(
    ns: Namespace, gid: str, sid: str | None, live: os.stat_result
) -> Meta | None:
    """The valid farm meta when its db is the same inode as the live db (an idempotent capture)."""
    meta = read_meta_at(ns, gid)
    if meta is None or meta.snapshot_id != sid:
        return None
    info = lstat_or_none(ns.generations / gid / DB_NAME)
    if info is None or (info.st_dev, info.st_ino) != (live.st_dev, live.st_ino):
        return None
    return meta


def capture_current(
    ns: Namespace,
    *,
    now: int,
    locked: bool,
    hooks: Hooks | None = None,
    gc_held: bool = False,
) -> Meta | None:
    """Link the live generation into ``generations/<gid>`` (a hardlink farm); idempotent.

    Precondition: the writer lock is held (``locked=True``), or it is taken here. An existing farm
    for the same id whose db is a different inode than the live db is retired and recaptured (equal
    content can be rebuilt into a new inode), unless a reader holds it: ``capture_conflict``.
    """
    if not locked:
        with writer_lock(ns):
            return capture_current(ns, now=now, locked=True, hooks=hooks, gc_held=gc_held)
    info = _current_info(ns)
    if info is None:
        return None
    gid, sid, kind = info
    live_db = ns.atlas_dir / DB_NAME
    live = os.stat(live_db)
    gens = ns.generations
    ensure_dir(gens)
    existing = lstat_or_none(gens / gid)
    if existing is not None:
        meta = _farm_matches_live(ns, gid, sid, live)
        if meta is not None:
            return meta
        from atlas_aci.storage import retention

        retired = retention.retire_locked(ns, gid) if gc_held else retention.retire_invalid(ns, gid)
        if not retired:
            raise StoreError("capture_conflict")
    if os.stat(gens).st_dev != os.stat(ns.atlas_dir).st_dev or live.st_dev != os.stat(gens).st_dev:
        raise StoreError("cross_device")
    seq = 1 + max((m.seq for m in list_generations(ns) if m.valid), default=0)
    stage_name = ".stage." + secrets.token_hex(8)
    gens_fd = open_dir(gens)
    stage_fd = -1
    live_fd = -1
    try:
        os.mkdir(stage_name, dir_fd=gens_fd)
        stage_fd = open_subdir(gens_fd, stage_name)
        live_fd = open_dir(ns.atlas_dir)
        pack_sha: str | None = None
        pack_size: int | None = None
        try:
            os.link(
                DB_NAME, DB_NAME, src_dir_fd=live_fd, dst_dir_fd=stage_fd, follow_symlinks=False
            )
            if sid is not None:
                os.mkdir(SNAP_DIR, dir_fd=stage_fd)
                snap_live = open_subdir(live_fd, SNAP_DIR)
                snap_stage = open_subdir(stage_fd, SNAP_DIR)
                try:
                    manifest = "manifest." + sid + ".json"
                    pack_info = json.loads(read_bounded(snap_live, manifest, DOC_CAP))["pack"]
                    pack_sha, pack_size = pack_info["sha256"], pack_info["size"]
                    pack = "pack." + str(pack_sha) + ".bin"
                    for name in (manifest, pack):
                        os.link(
                            name,
                            name,
                            src_dir_fd=snap_live,
                            dst_dir_fd=snap_stage,
                            follow_symlinks=False,
                        )
                    fsync_dir(snap_stage)
                finally:
                    os.close(snap_live)
                    os.close(snap_stage)
        except OSError as exc:
            if exc.errno == errno.EXDEV:
                raise StoreError("cross_device") from None
            raise
        linked = os.stat(DB_NAME, dir_fd=stage_fd, follow_symlinks=False)
        if (linked.st_dev, linked.st_ino) != (live.st_dev, live.st_ino):
            raise StoreError("capture_conflict")
        fire(hooks, "capture_post_link")
        os.close(
            os.open(
                "lease.lock",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o644,
                dir_fd=stage_fd,
            )
        )
        meta = Meta(
            generation_id=gid,
            snapshot_id=sid,
            kind=kind,
            repository=str(ns.repo),
            namespace=ns.identity,
            seq=seq,
            captured_at=now,
            db_sha256=sha256_at(stage_fd, DB_NAME),
            db_size=linked.st_size,
            pack_sha256=pack_sha,
            pack_size=pack_size,
        )
        write_json_atomic(stage_fd, "meta.json", meta.as_dict())
        fsync_dir(stage_fd)
        fire(hooks, "capture_pre_rename")
        try:
            os.rename(stage_name, gid, src_dir_fd=gens_fd, dst_dir_fd=gens_fd)
        except OSError as exc:
            if exc.errno in (errno.EEXIST, errno.ENOTEMPTY):
                raise StoreError("capture_conflict") from None
            raise
        stage_name = ""
        fsync_dir(gens_fd)
        return meta
    finally:
        for fd in (stage_fd, live_fd):
            if fd >= 0:
                os.close(fd)
        if stage_name:
            with contextlib.suppress(OSError):
                remove_tree(gens_fd, stage_name)
        os.close(gens_fd)


# --------------------------------------------------------------------------------- verification
def verify_generation(ns: Namespace, farm: Path, meta: Meta) -> Verified:
    """Repeat ``CodeGraph.reader_lease`` validation on a farm; returns the open handles.

    Any failure raises ``GenerationInvalid`` (fixed text) after closing every handle.
    """
    conn: sqlite3.Connection | None = None
    source: snap.SourceLease | None = None
    reader: CodeGraph | None = None
    try:
        db_path = farm / DB_NAME
        info = os.lstat(db_path)
        if not stat.S_ISREG(info.st_mode) or info.st_size != meta.db_size:
            raise GenerationInvalid
        if sha256_file(db_path) != meta.db_sha256:
            raise GenerationInvalid
        conn = sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        reader = CodeGraph(ns.repo, read_only=True, graph_root=ns.graph_root)
        reader._db = conn
        data = reader._generation_data(conn, retain_observations=True)
        if data is None or data[1] != meta.generation_id:
            raise GenerationInvalid
        inputs = data[0]
        pointer = inputs.get("snapshot", {})
        if pointer.get("state") == "available":
            binding = reader._snapshot_binding(conn, reader._acquisition_observations)
            source = snap.acquire(farm, pointer["id"], repository=ns.repo, expected={**binding})
            context = source.manifest["context"]
            if any(context[k] != value for k, value in reader._snapshot_context(inputs).items()):
                raise GenerationInvalid
            if source.snapshot_id != meta.snapshot_id:
                raise GenerationInvalid
        elif meta.snapshot_id is not None:
            raise GenerationInvalid
        if inputs["format"] == "atlas-generation.v5":
            descriptor = inputs["retrieval"]
            wanted, coverage = ret.expected(conn, source, descriptor["coverage"]["source_policy"])
            if reader._acquisition_retrieval_documents != wanted or descriptor != ret.descriptor(
                wanted, coverage
            ):
                raise GenerationInvalid
        result = Verified(meta.generation_id, meta.snapshot_id, conn, source)
        conn = None
        source = None
        return result
    except GenerationInvalid:
        raise
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error, AttributeError):
        raise GenerationInvalid from None
    finally:
        if reader is not None:
            reader._db = None
            reader._source_lease = None
            reader.close()
        if conn is not None:
            with contextlib.suppress(sqlite3.Error):
                conn.close()
        if source is not None:
            source.close()
