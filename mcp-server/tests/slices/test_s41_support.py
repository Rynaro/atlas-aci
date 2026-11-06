"""Shared ACI-S41 helpers: trees, namespaces, CLI drivers, observers and the lock canary.

Stdlib only at the top level (atlas_aci is imported lazily inside the functions that need it, and
pytest is optional), so the production driver (`production_main`) also runs in the production
image. The file defines no test function.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import hashlib
import json
import os
import selectors
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

try:  # the production image has no pytest
    import pytest
except ImportError:  # pragma: no cover
    pytest = None  # type: ignore[assignment]

_PARENTS = Path(__file__).resolve().parents
TESTS = _PARENTS[1]  # (the production image mounts this directory at /s41)
ROOT = _PARENTS[3] if len(_PARENTS) > 3 else _PARENTS[-1]
SRC = ROOT / "mcp-server" / "src" / "atlas_aci"
LANGS = ("python",)
LOCAL_FILESYSTEMS = {"ext4", "xfs", "btrfs", "overlay", "tmpfs", "zfs", "f2fs", "ext3", "ext2"}
SHARED_FOLDER_FILESYSTEMS = {"virtiofs", "9p", "nfs", "nfs4", "cifs", "fuse.osxfs", "fuse.grpcfuse"}
WAIT = 15.0

# ------------------------------------------------------------------------------------- trees
_MARKERS = {
    1: {},
    2: {0: 100, 1: 100},
    3: {1: 200, 2: 200},
    4: {2: 300, 3: 300},
    5: {3: 400, 4: 400},
}


def tree_files(version: int) -> dict[str, bytes]:
    """Five small Python files; versions differ from v1 in exactly two files."""
    marks = _MARKERS[version]
    files = {}
    for i in range(5):
        body = (
            f"def f{i}(x):\n    return x + {i + marks.get(i, 0)}\n\n"
            f"class C{i}:\n    def m(self):\n        return f{i}(1)\n"
        )
        files[f"m{i}.py"] = body.encode()
    return files


def write_tree(root: Path, version: int) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for name, data in tree_files(version).items():
        (root / name).write_bytes(data)


class Ns:
    """One repository plus an external graph root, both resolved."""

    def __init__(self, tmp: Path, graphs: str = "graphs"):
        self.tmp = tmp.resolve()
        (self.tmp / "repo").mkdir(parents=True, exist_ok=True)
        self.repo = (self.tmp / "repo").resolve()
        self.graphs = (self.tmp / graphs).resolve()

    @property
    def ns(self) -> Any:
        from atlas_aci.storage import generations as gen

        return gen.namespace(self.repo, self.graphs)

    @property
    def atlas(self) -> Path:
        return Path(self.ns.atlas_dir)

    def spec(self, langs: tuple[str, ...] = LANGS, frameworks: tuple[str, ...] = (), table=None):
        from atlas_aci.indexing import jobs

        return jobs.JobSpec(self.ns, langs, frameworks, table)

    def args(self, *more: str) -> list[str]:
        return [*more, "--repo", str(self.repo), "--graph-root", str(self.graphs)]


def twin(ns: Ns, name: str = "twin") -> Ns:
    """A second graph root for the same repository path (same content gives the same ids)."""
    return Ns(ns.tmp, graphs=name)


def current(ns: Ns) -> tuple[str, str | None] | None:
    from atlas_aci.storage import generations as gen

    return gen.current_generation(ns.ns)


def write_policy(ns: Ns, **fields: Any) -> None:
    from atlas_aci.storage import generations as gen

    ns.atlas.mkdir(parents=True, exist_ok=True)
    doc = {"format": gen.FORMAT_POLICY, **fields}
    (ns.atlas / "retention.json").write_text(json.dumps(doc))


def run_job_inprocess(ns: Ns, version: int, now: int, **kwargs: Any) -> dict[str, Any]:
    """Write the tree and run a real in-process job; returns the result envelope."""
    from atlas_aci.indexing import jobs

    write_tree(ns.repo, version)
    return jobs.submit(ns.spec(**kwargs), now=now)


def build_farms(ns: Ns, versions: tuple[int, ...] = (1, 2, 3), now0: int = 1000) -> list[str]:
    """Real jobs, one per version; returns the generation ids (each a retained farm)."""
    return [
        run_job_inprocess(ns, v, now0 + 10 * n)["receipt"]["generation_id"]
        for n, v in enumerate(versions)
    ]


# --------------------------------------------------------------------------------------- cli
def child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("ATLAS_ACI_")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONHASHSEED"] = "0"
    env.update(extra or {})
    return env


def faults(spec: dict[str, Any]) -> dict[str, str]:
    from atlas_aci.indexing import job_runner

    return {job_runner.FAULT_ENV: json.dumps(spec, sort_keys=True, separators=(",", ":"))}


def cli(
    args: list[str],
    env: dict[str, str] | None = None,
    stdin: str | None = "",
    timeout: float = 90,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "atlas_aci", *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=child_env(env),
        cwd=str(ROOT / "mcp-server"),
    )


def spawn(args: list[str], env: dict[str, str] | None = None) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-m", "atlas_aci", *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=child_env(env),
        cwd=str(ROOT / "mcp-server"),
    )


def read_line(proc: subprocess.Popen[str], timeout: float = WAIT) -> str:
    """One stdout line, bounded; raises on timeout or EOF."""
    assert proc.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    try:
        if not selector.select(timeout):
            raise AssertionError("timed out waiting for a line")
        line = proc.stdout.readline()
    finally:
        selector.close()
    if not line:
        raise AssertionError("stdout closed")
    return line


def seam_wait(proc: subprocess.Popen[str], name: str, timeout: float = WAIT) -> None:
    """Wait for ``ATLAS_JOB_SEAM <name>`` (the child then blocks on stdin)."""
    end = time.monotonic() + timeout
    while True:
        line = read_line(proc, max(0.1, end - time.monotonic()))
        if line.strip() == f"ATLAS_JOB_SEAM {name}":
            return


def release(proc: subprocess.Popen[str]) -> None:
    assert proc.stdin is not None
    proc.stdin.write("\n")
    proc.stdin.flush()


def finish(proc: subprocess.Popen[str], timeout: float = 60) -> tuple[int, str, str]:
    if proc.stdin is not None and proc.stdin.closed:
        proc.stdin = None
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        raise AssertionError("child did not exit in time") from None
    return proc.returncode, out, err


def last_json(text: str) -> dict[str, Any]:
    lines = [line for line in text.splitlines() if line.startswith("{")]
    assert lines, text[-300:]
    value = json.loads(lines[-1])
    assert isinstance(value, dict)
    return value


def run_json(args: list[str], env: dict[str, str] | None = None, expect: int = 0) -> dict[str, Any]:
    done = cli(args, env=env)
    assert done.returncode == expect, (done.returncode, done.stdout[-400:], done.stderr[-400:])
    return last_json(done.stdout)


def python_c(code: str, *argv: str, env: dict[str, str] | None = None, timeout: float = 90):
    return subprocess.run(
        [sys.executable, "-c", code, *argv],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=child_env(env),
        cwd=str(ROOT / "mcp-server"),
    )


# ------------------------------------------------------------------------------ observers
def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def listing(
    atlas: Path, exclude: tuple[str, ...] = (), under: str = ""
) -> dict[str, tuple[str, int, int, int]]:
    """{relative path: (sha256, size, mtime_ns, inode)} of every regular file (no directories)."""
    base = atlas / under if under else atlas
    found: dict[str, tuple[str, int, int, int]] = {}
    if not base.exists():
        return found
    for path in sorted(base.rglob("*")):
        rel = str(path.relative_to(atlas))
        if path.is_symlink() or not path.is_file() or rel in exclude:
            continue
        info = path.stat()
        found[rel] = (sha256_of(path), info.st_size, info.st_mtime_ns, info.st_ino)
    return found


def content_listing(atlas: Path, exclude: tuple[str, ...] = (), under: str = "") -> dict[str, str]:
    return {k: v[0] for k, v in listing(atlas, exclude, under).items()}


class _FcntlProxy:
    """A stand-in for the ``fcntl`` module that records ``flock`` and delegates everything."""

    def __init__(self, spy: FlockSpy, label: str):
        self._spy = spy
        self._label = label

    def __getattr__(self, name: str) -> Any:
        return getattr(fcntl, name)

    def flock(self, fd: int, operation: int) -> None:
        names = []
        for bit, name in ((fcntl.LOCK_SH, "SH"), (fcntl.LOCK_EX, "EX"), (fcntl.LOCK_UN, "UN")):
            if operation & bit:
                names.append(name)
        if operation & fcntl.LOCK_NB:
            names.append("NB")
        label = "|".join(names)
        try:
            fcntl.flock(fd, operation)
        except OSError as exc:
            self._spy.record(
                self._label,
                label,
                "EWOULDBLOCK"
                if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK)
                else errno.errorcode.get(exc.errno or 0, "ERR"),
            )
            raise
        self._spy.record(self._label, label, "granted")


class FlockSpy:
    """Records ``flock`` calls made through ``leases.fcntl`` and ``retention.fcntl``.

    The module attributes are replaced by a proxy: the global ``fcntl.flock`` is never patched,
    so barriers and counts apply only to the intended call sites.
    """

    def __init__(self, monkeypatch: Any):
        from atlas_aci.storage import leases, retention

        self.events: list[tuple[str, str, str]] = []
        monkeypatch.setattr(leases, "fcntl", _FcntlProxy(self, "leases"))
        monkeypatch.setattr(retention, "fcntl", _FcntlProxy(self, "retention"))

    def record(self, module: str, op: str, outcome: str) -> None:
        self.events.append((module, op, outcome))

    def count(self, module: str, op: str, outcome: str) -> int:
        return sum(1 for e in self.events if e == (module, op, outcome))

    def clear(self) -> None:
        self.events.clear()


class OpSpy:
    """Records successful ``os`` mutations and sqlite connections while active."""

    KINDS = ("open", "link", "rename", "replace", "unlink", "rmdir", "mkdir", "utime")

    def __init__(self) -> None:
        self.ops: list[tuple[str, str, int]] = []
        self.uris: list[str] = []
        self._saved: dict[str, Any] = {}

    @staticmethod
    def _full(name: Any, fd: Any) -> str:
        """Resolve a name relative to a directory descriptor (Linux /proc), else keep it."""
        text = os.fsdecode(name)
        if fd is None or text.startswith("/"):
            return text
        try:
            return os.path.join(os.readlink(f"/proc/self/fd/{fd}"), text)
        except OSError:
            return text

    def _wrap(self, kind: str, original: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            result = original(*args, **kwargs)
            flags = args[1] if kind == "open" and len(args) > 1 else kwargs.get("flags", 0)
            if kind in {"link", "rename", "replace"}:
                self.ops.append((kind, self._full(args[0], kwargs.get("src_dir_fd")), flags))
                self.ops.append((kind, self._full(args[1], kwargs.get("dst_dir_fd")), flags))
            else:
                self.ops.append((kind, self._full(args[0], kwargs.get("dir_fd")), flags))
            return result

        return wrapper

    def __enter__(self) -> OpSpy:
        for kind in self.KINDS:
            self._saved[kind] = getattr(os, kind)
            setattr(os, kind, self._wrap(kind, self._saved[kind]))
        self._saved["connect"] = sqlite3.connect
        original = sqlite3.connect

        def connect(database: Any, *args: Any, **kwargs: Any) -> Any:
            self.uris.append(str(database))
            return original(database, *args, **kwargs)

        sqlite3.connect = connect  # type: ignore[assignment]
        return self

    def __exit__(self, *exc: Any) -> None:
        for kind in self.KINDS:
            setattr(os, kind, self._saved[kind])
        sqlite3.connect = self._saved["connect"]

    def mutations(self, exclude: Callable[[str], bool] = lambda _p: False) -> list[tuple[str, str]]:
        writes = []
        for kind, path, flags in self.ops:
            if exclude(path):
                continue
            if kind == "open":
                if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
                    writes.append((kind, path))
            elif kind != "open":
                writes.append((kind, path))
        return writes

    def under(self, kinds: tuple[str, ...], prefix: Path) -> list[tuple[str, str]]:
        return [(k, p) for k, p, _f in self.ops if k in kinds and p.startswith(str(prefix))]


def replace_by_copy(path: Path, mutate: Callable[[bytes], bytes]) -> None:
    """Break a hardlink and modify: copy, change, ``os.replace`` onto the path (never in place).

    Farm files share inodes with the live store, so an in-place edit would corrupt both names.
    """
    data = path.read_bytes()
    temp = path.with_name(path.name + ".tamper")
    temp.write_bytes(mutate(data))
    os.replace(temp, path)


def flip_byte(data: bytes) -> bytes:
    middle = len(data) // 2
    return data[:middle] + bytes([data[middle] ^ 0xFF]) + data[middle + 1 :]


@contextlib.contextmanager
def hold_flock(path: Path, operation: int) -> Iterator[int]:
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, operation)
        yield fd
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


# ------------------------------------------------------------------------------------ canary
def _mount_type(path: Path) -> str | None:
    try:
        lines = Path("/proc/self/mountinfo").read_text().splitlines()
    except OSError:
        return None
    best = ("", "")
    target = str(path.resolve())
    for line in lines:
        parts = line.split()
        mount, rest = parts[4], parts[parts.index("-") + 1 :]
        if (target == mount or target.startswith(mount.rstrip("/") + "/")) and len(mount) >= len(
            best[0]
        ):
            best = (mount, rest[0])
    return best[1] or None


def run_lock_canary(base: Path) -> dict[str, Any]:
    """The exact directory the flock and hardlink tests use must really behave like a local FS.

    EX|NB must fail against a held SH on a second descriptor and against another process; a
    hardlink must give nlink 2 with equal (st_dev, st_ino). A host shared-folder mount, where
    locks can be guest-local, is rejected: tests are meant to run on container-local storage.
    """
    work = Path(tempfile.mkdtemp(prefix="lock-canary-", dir=base))  # one per fixture instance
    lock = work / "lease.lock"
    lock.write_bytes(b"")
    held = os.open(lock, os.O_RDONLY)
    other = os.open(lock, os.O_RDONLY)
    try:
        fcntl.flock(held, fcntl.LOCK_SH | fcntl.LOCK_NB)
        fcntl.flock(other, fcntl.LOCK_SH | fcntl.LOCK_NB)
        fcntl.flock(other, fcntl.LOCK_UN)
        try:
            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            second_fd_blocks = False
        except BlockingIOError:
            second_fd_blocks = True
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                "import fcntl,os,sys\n"
                "fd=os.open(sys.argv[1],os.O_RDONLY)\n"
                "try:\n fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);print('granted')\n"
                "except BlockingIOError:\n print('blocked')\n",
                str(lock),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        process_blocks = child.stdout.strip() == "blocked"
    finally:
        os.close(held)
        os.close(other)
    source = work / "a.bin"
    source.write_bytes(b"x")
    linked = work / "b.bin"
    os.link(source, linked, follow_symlinks=False)
    one, two = source.stat(), linked.stat()
    fstype = _mount_type(work)
    repo_dev = ROOT.stat().st_dev
    return {
        "second_fd_blocks": second_fd_blocks,
        "process_blocks": process_blocks,
        "nlink": one.st_nlink,
        "same_inode": (one.st_dev, one.st_ino) == (two.st_dev, two.st_ino),
        "fstype": fstype,
        "device_differs_from_repo": one.st_dev != repo_dev,
    }


if pytest is not None:

    @pytest.fixture(scope="session", autouse=True)
    def lock_canary(tmp_path_factory: Any) -> dict[str, Any]:
        """Session canary on the exact base directory used; it fails (never skips)."""
        result = run_lock_canary(tmp_path_factory.getbasetemp())
        assert result["second_fd_blocks"], result
        assert result["process_blocks"], result
        assert result["nlink"] == 2 and result["same_inode"], result
        assert result["fstype"] not in SHARED_FOLDER_FILESYSTEMS, result
        # A bind mount of the repository has another device than container-local storage; where
        # the repository and the base directory share a device (CI runners), the filesystem must
        # be a known local type instead.
        assert result["device_differs_from_repo"] or result["fstype"] in LOCAL_FILESYSTEMS, result
        return result


# ------------------------------------------------------------------------ production driver
def _flag(argv: list[str], name: str, default: str | None = None) -> str:
    if name in argv:
        return argv[argv.index(name) + 1]
    if default is None:
        raise SystemExit(f"missing {name}")
    return default


def production_main(argv: list[str]) -> int:
    """Query a read-only `atlas-aci serve` N times (G-PROD P6) and print one digest per round.

    Runs inside the production image (stdlib plus the S38 raw line client). The server is the
    unchanged live server: it reads only the current generation.
    """
    import test_s38_support as s38

    repo, graphs = _flag(argv, "--repo"), _flag(argv, "--graph-root")
    rounds, interval = int(_flag(argv, "--rounds", "1")), float(_flag(argv, "--interval", "0"))
    name = _flag(argv, "--name", "f0")
    memex = ["--memex-root", _flag(argv, "--memex-root")] if "--memex-root" in argv else []
    client = s38.RawLineClient(
        ["atlas-aci", "serve", "--repo", repo, "--graph-root", graphs, *memex],
        env={k: v for k, v in os.environ.items() if not k.startswith("ATLAS_ACI_")},
        deadline_s=300,
    )
    failed = 0
    try:
        client.request("initialize", s38.initialize_params("2025-11-25"))
        client.notify("notifications/initialized")
        for n in range(rounds):
            raw = client.request(
                "tools/call", {"name": "search_symbol", "arguments": {"name": name}}
            )
            body = json.loads(raw)
            result = body.get("result", {})
            ok = "error" not in body and not result.get("isError") and name in json.dumps(result)
            failed += 0 if ok else 1
            digest = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
            sys.stdout.write(f"ROUND {n} ok={int(ok)} sha256={digest}\n")
            sys.stdout.flush()
            if interval:
                time.sleep(interval)
    finally:
        client.close()
    sys.stdout.write(f"DRIVER: {rounds - failed} ok, {failed} failed\n")
    return 1 if failed else 0


def copy_tree(source: Path, target: Path) -> None:
    shutil.copytree(source, target)
