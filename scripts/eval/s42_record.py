"""ACI-S42 recorder: runs the shared-operation readiness evidence and writes immutable records.

Impure by design (processes, sockets, files, clocks). Top-level imports are the standard library
only; atlas_aci, mcp, httpx, jwt and cryptography are imported lazily inside the functions that
need them, so ``python -I scripts/eval/s42_record.py --help`` runs in any image. Every server is a
real ``python -m atlas_aci ...`` subprocess; nothing under test is mocked.

Usage::

    python [-I] scripts/eval/s42_record.py --manifest M --out DIR --work DIR [--image ID]
        [--phases environment,isolation,compat,capacity,interrupt] [--workers N]

Before any unit starts, one serial index run primes the grammar cache: a cold cache downloads
the grammar on first use, and parallel processes would race on that download.

Exit codes: 0 every unit complete; 1 at least one unit recorded failed (the others are still
written); 2 usage or invalid manifest (nothing written); 3 abort (lock canary failed, or
--out/--work not empty).

Records are written once, never overwritten: ``<kind>.<unit>.json.gz`` in canonical bytes. Ports,
pids, tokens, absolute paths, timings and counters live only under keys named ``observed`` or
``diagnostic``; the aggregator excludes those from the canonical digest.
"""

from __future__ import annotations

import argparse
import asyncio
import atexit
import base64
import contextlib
import fcntl
import hashlib
import importlib
import importlib.util
import json
import math
import os
import platform
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
GRAPH = "atlas.graph.v1"
ACCEPT = "application/json, text/event-stream"
SELECTED = "2025-11-25"
LISTENING = re.compile(r"\bport=(\d+)")
KEYVALUE = re.compile(r"(\w+)=(\d+)")
RESET = (ConnectionError, BrokenPipeError, OSError)
NONCE_PREFIX = "s42n"
WARM_TIMEOUT_S = 240.0  # a cold grammar download happens here, outside the recorder deadline


def _load_sibling(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


AGG = _load_sibling("s42_aggregate_for_recorder", "s42_aggregate.py")


class RecorderError(Exception):
    """A unit failed; ``code`` is one of the aggregator FAILURES."""

    def __init__(self, code: str, message: str = "") -> None:
        assert code in AGG.FAILURES, code
        self.code = code
        super().__init__(f"{code}: {message}")


# ----------------------------------------------------------------------------- child tracking
_CHILDREN: dict[int, subprocess.Popen[Any]] = {}
_CHILD_LOCK = threading.Lock()


def _track(proc: subprocess.Popen[Any]) -> None:
    with _CHILD_LOCK:
        _CHILDREN[proc.pid] = proc


def _untrack(proc: subprocess.Popen[Any]) -> None:
    with _CHILD_LOCK:
        _CHILDREN.pop(proc.pid, None)


def _proc_children() -> list[int]:
    """Direct children found through /proc (Linux); empty where /proc is unavailable."""
    found: list[int] = []
    me = os.getpid()
    try:
        names = os.listdir("/proc")
    except OSError:
        return found
    for name in names:
        if not name.isdigit():
            continue
        try:
            text = Path(f"/proc/{name}/stat").read_text()
            fields = text.rsplit(")", 1)[1].split()
            if int(fields[1]) == me:
                found.append(int(name))
        except (OSError, IndexError, ValueError):
            continue
    return found


def reap_children() -> None:
    """Kill every child the recorder started (tracked pids plus direct /proc children)."""
    with _CHILD_LOCK:
        pids = set(_CHILDREN)
    pids |= set(_proc_children())
    for pid in sorted(pids):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGKILL)
    for pid in sorted(pids):
        with contextlib.suppress(ChildProcessError, OSError):
            os.waitpid(pid, os.WNOHANG)


def _on_signal(signum: int, _frame: Any) -> None:
    reap_children()
    os._exit(128 + signum)


def child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """The environment of every child: no ATLAS_ACI_* except the explicit extras."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("ATLAS_ACI_")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONHASHSEED"] = "0"
    env.update(extra or {})
    return env


def atlas_argv(*args: str) -> list[str]:
    return [sys.executable, "-m", "atlas_aci", *args]


def spawn(
    argv: list[str],
    *,
    env: dict[str, str],
    stderr_path: Path | None = None,
    stdin: int | None = subprocess.DEVNULL,
    stdout: int | None = subprocess.DEVNULL,
    cwd: str | None = None,
) -> subprocess.Popen[bytes]:
    """Start a tracked child. stderr goes to a file object (no pump thread)."""
    stderr: Any = subprocess.DEVNULL
    handle = None
    if stderr_path is not None:
        handle = open(stderr_path, "wb", buffering=0)  # noqa: SIM115
        stderr = handle
    try:
        proc = subprocess.Popen(argv, stdin=stdin, stdout=stdout, stderr=stderr, env=env, cwd=cwd)
    finally:
        if handle is not None:
            handle.close()  # the child holds its own descriptor
    _track(proc)
    return proc


def run_cli(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    stdin_text: str = "",
    timeout: float = 90.0,
    cwd: str | None = None,
) -> tuple[int, str, str]:
    """One bounded ``python -m atlas_aci`` invocation: (returncode, stdout, stderr)."""
    proc = subprocess.Popen(
        atlas_argv(*args),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=child_env(env),
        cwd=cwd,
    )
    _track(proc)
    try:
        out, err = proc.communicate(stdin_text, timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise RecorderError("subprocess_error", f"timeout after {timeout}s: {args[:2]}") from None
    finally:
        _untrack(proc)
    return proc.returncode, out, err


def last_json(text: str) -> dict[str, Any]:
    lines = [line for line in text.splitlines() if line.startswith("{")]
    if not lines:
        raise RecorderError("subprocess_error", "no JSON line on stdout")
    value = json.loads(lines[-1])
    if not isinstance(value, dict):
        raise RecorderError("subprocess_error", "JSON line is not an object")
    return value


def is_killed(code: int) -> bool:
    return code in (-signal.SIGKILL, 137)


# ------------------------------------------------------------------------------- lock canary
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


SHARED_FOLDER_FILESYSTEMS = {"virtiofs", "9p", "nfs", "nfs4", "cifs", "fuse.osxfs", "fuse.grpcfuse"}


def run_lock_canary(base: Path) -> tuple[dict[str, bool], str | None]:
    """flock and hardlink behaviour on the exact directory used; fails closed (never skips)."""
    work = Path(tempfile.mkdtemp(prefix="lock-canary-", dir=base))
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
            second = False
        except BlockingIOError:
            second = True
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
        process = child.stdout.strip() == "blocked"
    finally:
        os.close(held)
        os.close(other)
    source = work / "a.bin"
    source.write_bytes(b"x")
    linked = work / "b.bin"
    os.link(source, linked, follow_symlinks=False)
    one, two = source.stat(), linked.stat()
    fstype = _mount_type(work)
    shutil.rmtree(work, ignore_errors=True)
    return (
        {
            "second_fd_blocks": second,
            "process_blocks": process,
            "hardlink_same_inode": one.st_nlink == 2
            and (one.st_dev, one.st_ino) == (two.st_dev, two.st_ino),
            "shared_folder_fs": fstype in SHARED_FOLDER_FILESYSTEMS,
        },
        fstype,
    )


# ------------------------------------------------------------------------------ keys and tokens
def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class Keys:
    """RSA-2048 signing key, a stranger key (same kid, different key) and the public JWKS."""

    KID = "kid-rsa"

    def __init__(self, issuer: str) -> None:
        from cryptography.hazmat.primitives.asymmetric import rsa

        self.issuer = issuer
        self.rsa = rsa.generate_private_key(65537, 2048)
        self.stranger = rsa.generate_private_key(65537, 2048)

    def jwks_bytes(self) -> bytes:
        from jwt.algorithms import RSAAlgorithm

        jwk = json.loads(RSAAlgorithm.to_jwk(self.rsa.public_key()))
        jwk["kid"] = self.KID
        jwk["use"] = "sig"
        return json.dumps({"keys": [jwk]}).encode("utf-8")

    def mint(self, subject: str, audience: str, *, which: str = "rsa", ttl: int = 3600) -> str:
        import jwt

        now = int(time.time())
        claims = {"iss": self.issuer, "aud": audience, "sub": subject, "iat": now, "exp": now + ttl}
        key = self.rsa if which == "rsa" else self.stranger
        return str(jwt.encode(claims, key, algorithm="RS256", headers={"kid": self.KID}))


def write_private(path: Path, data: bytes | str, mode: int = 0o600) -> None:
    """Atomic same-directory replace of a configuration file (owner-only)."""
    raw = data.encode("utf-8") if isinstance(data, str) else data
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(raw)
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def policy_document(issuer: str, grants: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "format": "atlas-aci-shared-policy.v1",
        "grants": [
            {
                "issuer": issuer,
                "subject": g["subject"],
                "repository": g["repository"],
                "tools": list(g["tools"]),
            }
            for g in grants
        ],
    }


# --------------------------------------------------------------------------- HTTP/1.1 client
class Resp:
    def __init__(self, status: int, reason: str, headers: list[tuple[str, str]], body: bytes):
        self.status, self.reason, self.headers, self.body = status, reason, headers, body

    def header(self, name: str) -> str | None:
        for key, value in self.headers:
            if key.lower() == name.lower():
                return value
        return None


def _headers_block(
    method: str, path: str, authority: str, headers: list[tuple[str, str]], length: int | None
) -> bytes:
    lines = [f"{method} {path} HTTP/1.1"]
    if "host" not in {k.lower() for k, _ in headers}:
        lines.append(f"Host: {authority}")
    lines += [f"{k}: {v}" for k, v in headers]
    lines.append("Connection: close")
    if length is not None:
        lines.append(f"Content-Length: {length}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")


async def _read_head(reader: asyncio.StreamReader) -> tuple[int, str, list[tuple[str, str]]]:
    raw = await reader.readuntil(b"\r\n\r\n")
    lines = raw.decode("latin-1").split("\r\n")
    _version, status, *reason = lines[0].split(" ", 2)
    headers = []
    for line in lines[1:]:
        if line:
            name, _, value = line.partition(":")
            headers.append((name.strip(), value.strip()))
    return int(status), (reason[0] if reason else ""), headers


async def _read_chunked(reader: asyncio.StreamReader) -> bytes:
    out = bytearray()
    while True:
        size_line = await reader.readline()
        if not size_line:
            break
        size = int(size_line.split(b";")[0].strip() or b"0", 16)
        if size == 0:
            await reader.readuntil(b"\r\n")
            break
        out += await reader.readexactly(size)
        await reader.readexactly(2)
    return bytes(out)


async def _read_response(reader: asyncio.StreamReader) -> Resp:
    status, reason, headers = await _read_head(reader)
    lowered = {k.lower(): v for k, v in headers}
    if "chunked" in lowered.get("transfer-encoding", "").lower():
        body = await _read_chunked(reader)
    elif "content-length" in lowered:
        body = await reader.readexactly(int(lowered["content-length"]))
    else:
        body = await reader.read()
    return Resp(status, reason, headers, body)


async def _close(writer: asyncio.StreamWriter) -> None:
    writer.close()
    with contextlib.suppress(*RESET, asyncio.CancelledError):
        await writer.wait_closed()


class Http:
    """One host:port; each call is one ``Connection: close`` exchange."""

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host, self.port, self.timeout = host, port, timeout

    @property
    def authority(self) -> str:
        return f"{self.host}:{self.port}"

    async def request(
        self,
        method: str,
        path: str,
        headers: list[tuple[str, str]],
        body: bytes | None = None,
        timeout: float | None = None,
    ) -> Resp:
        reader, writer = await asyncio.open_connection(self.host, self.port)
        length = len(body) if body is not None else None
        head = _headers_block(method, path, self.authority, headers, length)

        async def send() -> None:
            try:
                writer.write(head + (body or b""))
                await writer.drain()
            except RESET:
                pass

        sender = asyncio.ensure_future(send())
        try:
            return await asyncio.wait_for(_read_response(reader), timeout or self.timeout)
        finally:
            if not sender.done():
                sender.cancel()
            with contextlib.suppress(asyncio.CancelledError, *RESET):
                await sender
            await _close(writer)

    def hold(self, path: str, headers: list[tuple[str, str]], body: bytes) -> Hold:
        return Hold(self, path, headers, body)


class Hold:
    """A request whose headers are sent and whose body is withheld until ``release``."""

    def __init__(self, http: Http, path: str, headers: list[tuple[str, str]], body: bytes) -> None:
        self.http, self.path, self.headers, self.body = http, path, headers, body
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None

    async def open(self) -> None:
        self.reader, self.writer = await asyncio.open_connection(self.http.host, self.http.port)
        head = _headers_block("POST", self.path, self.http.authority, self.headers, len(self.body))
        self.writer.write(head)
        await self.writer.drain()

    async def release(self, timeout: float) -> Resp:
        assert self.reader is not None and self.writer is not None
        try:
            self.writer.write(self.body)
            await self.writer.drain()
        except RESET:
            pass
        try:
            return await asyncio.wait_for(_read_response(self.reader), timeout)
        finally:
            await _close(self.writer)

    async def close(self) -> None:
        if self.writer is not None:
            await _close(self.writer)


# ------------------------------------------------------------------- outcomes and canary scan
def _sse_messages(text: str) -> list[Any]:
    messages: list[Any] = []
    data: list[str] = []
    for line in [*text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), ""]:
        if line == "":
            if data:
                messages.append(json.loads("\n".join(data)))
            data = []
        elif line.startswith("data:"):
            value = line[5:]
            data.append(value[1:] if value.startswith(" ") else value)
    return messages


def parse_messages(resp: Resp) -> list[Any]:
    """The JSON-RPC messages of an SSE or JSON body; raises ValueError on a malformed body."""
    text = resp.body.decode("utf-8")
    ctype = (resp.header("content-type") or "").lower()
    if ctype.startswith("text/event-stream"):
        return _sse_messages(text)
    value = json.loads(text)
    return value if isinstance(value, list) else [value]


ROUTER_REASONS = {
    (401, "credentials_missing"): "http:401:credentials_missing",
    (401, "credentials_invalid"): "http:401:credentials_invalid",
    (403, "repository_forbidden"): "http:403:repository_forbidden",
    (404, "route_not_found"): "http:404:route_not_found",
    (503, "inflight_limit"): "http:503:inflight_limit",
    (503, "principal_inflight_limit"): "http:503:principal_inflight_limit",
}


def _error_code(value: Any) -> str:
    code = value.get("code") if isinstance(value, dict) else value
    return code if isinstance(code, str) and code in AGG.ERROR_CODES else "OTHER"


def _tool_payload(result: dict[str, Any]) -> dict[str, Any] | None:
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    content = result.get("content")
    if isinstance(content, list) and content and isinstance(content[0], dict):
        with contextlib.suppress(ValueError, TypeError):
            parsed = json.loads(content[0].get("text", ""))
            if isinstance(parsed, dict):
                return parsed
    return None


def classify(resp: Resp | None) -> tuple[str, dict[str, Any] | None]:
    """The closed outcome of one exchange and the decoded JSON-RPC response (or None).

    Order: (1) HTTP status and reason; (2) SSE/JSON parse; (3) an ``error`` member gives
    ``rpc_error``; (4) ``isError`` or a ``status: error`` payload gives ``tool_error``. ``ok`` is
    only a clean result. A malformed body gives ``transport_error``.
    """
    if resp is None:
        return "transport_error", None
    if resp.status != 200:
        reason = ""
        with contextlib.suppress(ValueError, TypeError, AttributeError, UnicodeDecodeError):
            body = json.loads(resp.body.decode("utf-8"))
            reason = str(body["error"]["data"]["reason"])
        return ROUTER_REASONS.get((resp.status, reason), "http:other"), None
    try:
        messages = parse_messages(resp)
    except (ValueError, UnicodeDecodeError):
        return "transport_error", None
    answers = [m for m in messages if isinstance(m, dict) and ("result" in m or "error" in m)]
    if len(answers) != 1:
        return "transport_error", None
    message = answers[0]
    if "error" in message:
        error = message["error"]
        data = error.get("data") if isinstance(error, dict) else None
        wire_reason = data.get("reason") if isinstance(data, dict) else None
        return f"rpc_error:{_error_code(wire_reason)}", message
    result = message.get("result")
    if not isinstance(result, dict):
        return "transport_error", message
    payload = _tool_payload(result)
    if result.get("isError") is True:
        error = payload.get("error") if payload else None
        return f"tool_error:{_error_code(error)}", message
    if payload is not None and payload.get("status") == "error":
        return f"tool_error:{_error_code(payload.get('error'))}", message
    return "ok", message


def scan_bytes(resp: Resp) -> bytes:
    """Raw response bytes plus every base64-decoded resource ``blob`` found in the messages."""
    parts = [resp.body]
    with contextlib.suppress(ValueError, UnicodeDecodeError):
        stack: list[Any] = list(parse_messages(resp))
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                for key, value in item.items():
                    if key == "blob" and isinstance(value, str):
                        with contextlib.suppress(ValueError):
                            parts.append(base64.b64decode(value, validate=False))
                    else:
                        stack.append(value)
            elif isinstance(item, list):
                stack.extend(item)
    return b"\n".join(parts)


def nonce_for(phase: str, case_id: str) -> str:
    return NONCE_PREFIX + hashlib.sha256(f"{phase}/{case_id}".encode()).hexdigest()[:16]


# ------------------------------------------------------------------------- servers
class Server:
    """A server subprocess: stderr in a file, port from the listening line, stop with wait4."""

    def __init__(
        self,
        label: str,
        argv: list[str],
        work: Path,
        *,
        marker: str,
        startup_s: float,
        env: dict[str, str] | None = None,
    ) -> None:
        work.mkdir(parents=True, exist_ok=True)
        self.label = label
        self.stderr_path = work / f"{label}.stderr"
        self.proc = spawn(argv, env=child_env(env), stderr_path=self.stderr_path)
        self.port: int | None = None
        self.exit_code: int | None = None
        self.maxrss: int | None = None
        deadline = time.monotonic() + startup_s
        while time.monotonic() < deadline:
            for line in self.stderr_text().splitlines():
                if marker in line and (match := LISTENING.search(line)):
                    self.port = int(match.group(1))
                    return
            if self.proc.poll() is not None:
                break
            time.sleep(0.05)
        tail = self.stderr_text()[-400:]
        self.stop(1.0)
        raise RecorderError("server_start_timeout", f"{label}: {tail}")

    def stderr_text(self) -> str:
        try:
            return self.stderr_path.read_bytes().decode("utf-8", errors="replace")
        except OSError:
            return ""

    def stop(self, wait_s: float = 10.0) -> int | None:
        """SIGTERM, then wait4 (SIGKILL after the deadline). Returns the exit code."""
        if self.exit_code is not None:
            return self.exit_code
        pid = self.proc.pid
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + wait_s
        while True:
            try:
                done, status, usage = os.wait4(pid, os.WNOHANG)
            except ChildProcessError:
                self.exit_code = self.proc.returncode if self.proc.returncode is not None else 0
                break
            if done == pid:
                self.exit_code = os.waitstatus_to_exitcode(status)
                self.maxrss = int(usage.ru_maxrss)
                break
            if time.monotonic() > deadline:
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGKILL)
                _done, status, usage = os.wait4(pid, 0)
                self.exit_code = os.waitstatus_to_exitcode(status)
                self.maxrss = int(usage.ru_maxrss)
                break
            time.sleep(0.05)
        self.proc.returncode = self.exit_code
        _untrack(self.proc)
        return self.exit_code

    def stopped_line(self) -> tuple[int, int, int] | None:
        for line in self.stderr_text().splitlines():
            if "shared_http_stopped" in line:
                pairs = dict(KEYVALUE.findall(line))
                if {"admitted", "rejected", "peak"} <= set(pairs):
                    return int(pairs["admitted"]), int(pairs["rejected"]), int(pairs["peak"])
        return None


# ---------------------------------------------------------------------- manifest and trees
def load_manifest(path: Path) -> tuple[dict[str, Any], str]:
    manifest = AGG.load_manifest(path)
    return manifest, AGG.manifest_sha(manifest)


def write_tree(root: Path, files: dict[str, str], *, clean: bool = False) -> None:
    root.mkdir(parents=True, exist_ok=True)
    if clean:
        for item in root.iterdir():
            if item.is_file():
                item.unlink()
            elif item.is_dir():
                shutil.rmtree(item)
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


def index_repo(repo: Path, graph_root: Path, cwd: str, timeout: float = 120.0) -> None:
    code, _out, err = run_cli(
        ["index", "--repo", str(repo), "--graph-root", str(graph_root), "--langs", "python"],
        timeout=timeout,
        cwd=cwd,
    )
    if code != 0:
        raise RecorderError("subprocess_error", f"index failed: {err[-300:]}")


def build_fixtures(manifest: dict[str, Any], base: Path) -> dict[str, Path]:
    """Repos a and b with their graphs; the graph of c indexes tree A1 at the path of a."""
    repos, graphs = base / "repos", base / "graphs"
    a, b = repos / "a", repos / "b"
    cwd = str(base)
    trees = manifest["trees"]
    write_tree(a, trees["A1"], clean=True)
    index_repo(a, graphs / "gc", cwd)
    write_tree(a, trees["A2"], clean=True)
    index_repo(a, graphs / "ga", cwd)
    write_tree(b, trees["B"], clean=True)
    index_repo(b, graphs / "gb", cwd)
    return {"a": a, "b": b, "ga": graphs / "ga", "gb": graphs / "gb", "gc": graphs / "gc"}


# ---------------------------------------------------------------------- shared deployment
class Deployment:
    """A shared-config document, its JWKS and policy files, and the server built from them."""

    def __init__(
        self,
        manifest: dict[str, Any],
        keys: Keys,
        root: Path,
        *,
        repos: list[tuple[str, Path, Path, str | None]],
        listen: dict[str, Any],
        limits: dict[str, Any],
        grants: list[dict[str, Any]],
    ) -> None:
        self.manifest, self.keys, self.root = manifest, keys, root
        self.issuer = manifest["issuer"]
        self.base = manifest["resource_base_url"]
        self.config_dir = root / "etc"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.config_dir, 0o700)
        self.policy_path = self.config_dir / "policy.json"
        self.jwks_path = self.config_dir / "jwks.json"
        self.config_path = self.config_dir / "shared.json"
        write_private(self.jwks_path, keys.jwks_bytes())
        self.set_grants(grants)
        entries = []
        for identifier, repo, graph_root, before in repos:
            entry: dict[str, Any] = {
                "id": identifier,
                "repo": str(repo),
                "graph_root": str(graph_root),
                "memex_root": str(root / "memex" / identifier),
            }
            if before is not None:
                entry["before_repo_id"] = before
            entries.append(entry)
        listen_document: dict[str, Any] = {"port": 0}
        listen_document.update(listen)
        self.document = {
            "format": "atlas-aci-shared.v1",
            "listen": listen_document,
            "identity": {
                "issuer": self.issuer,
                "jwks_file": str(self.jwks_path),
                "resource_base_url": self.base,
            },
            "policy": {"file": str(self.policy_path), "reload_interval_seconds": 0},
            "limits": dict(limits),
            "repositories": entries,
        }
        write_private(self.config_path, json.dumps(self.document))

    def set_grants(self, grants: list[dict[str, Any]]) -> str:
        document = policy_document(self.issuer, grants)
        write_private(self.policy_path, json.dumps(document))
        return hashlib.sha256(AGG.canonical(document)).hexdigest()

    def start(self, label: str, startup_s: float) -> Server:
        return Server(
            label,
            atlas_argv("serve-shared", "--shared-config", str(self.config_path)),
            self.root / "logs",
            marker="shared_http_listening",
            startup_s=startup_s,
        )

    def token(self, subject: str, repository: str, *, which: str = "rsa") -> str:
        return self.keys.mint(subject, f"{self.base}/{repository}", which=which)


def rpc_headers(version: str | None, token: str | None) -> list[tuple[str, str]]:
    headers = [("Content-Type", "application/json"), ("Accept", ACCEPT)]
    if version is not None:
        headers.append(("mcp-protocol-version", version))
    if token is not None:
        headers.append(("Authorization", f"Bearer {token}"))
    return headers


def rpc_message(method: str, params: Any = None, request_id: int = 1) -> bytes:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return json.dumps(message, separators=(",", ":")).encode("utf-8")


def tool_message(name: str, arguments: Any) -> bytes:
    return rpc_message("tools/call", {"name": name, "arguments": arguments})


# ------------------------------------------------------------------------------- isolation
class IsolationState:
    """Everything one isolation run shares between its cases."""

    def __init__(
        self, manifest: dict[str, Any], dep: Deployment, http: Http, repos: dict[str, Path]
    ):
        self.manifest, self.dep, self.http = manifest, dep, http
        self.repos = repos
        self.iso = manifest["isolation"]
        self.canaries = manifest["canaries"]
        self.saved: dict[str, dict[str, Any]] = {}
        self.t0 = time.monotonic()
        self.cases = {(c["phase"], c["id"]): c for c in self.iso["cases"]}

    def now_ms(self) -> int:
        return int((time.monotonic() - self.t0) * 1000)

    def subst(self, value: Any, phase: str, case_id: str) -> Any:
        table = {
            "$A_PATTERN": self.canaries["A"]["pattern"],
            "$B_PATTERN": self.canaries["B"]["pattern"],
            "$NONCE": nonce_for(phase, case_id),
            "$REPO_A": str(self.repos["a"]),
            "$REPO_B": str(self.repos["b"]),
        }
        if isinstance(value, str):
            for key, text in table.items():
                value = value.replace(key, text)
            return value
        if isinstance(value, list):
            return [self.subst(v, phase, case_id) for v in value]
        if isinstance(value, dict):
            return {k: self.subst(v, phase, case_id) for k, v in value.items()}
        return value

    def witnesses(self) -> dict[str, bytes]:
        return {d: self.canaries[d]["witness"].encode() for d in ("A", "B", "C")}

    def build(self, case: dict[str, Any], phase: str) -> tuple[str, list[tuple[str, str]], bytes]:
        """(path, headers, body) of the request a case sends."""
        request = case["request"]
        kind = request["kind"]
        if kind == "tool":
            arguments = self.subst(request["arguments"], phase, case["id"])
            body = tool_message(request["tool"], arguments)
        elif kind == "cursor_continue":
            saved = self.saved[request["uses"]]
            body = tool_message("graph_query", saved["continue"])
        else:
            saved = self.saved[request["uses"]]
            body = rpc_message("resources/read", {"uri": saved["uri"]})
        route = case["route"]
        token_kind = case["token"]
        subject = case["principal"]
        token: str | None
        if token_kind == "valid":
            token = self.dep.token(subject, route)
        elif token_kind == "missing":
            token = None
        elif token_kind == "stranger":
            token = self.dep.token(subject, route, which="stranger")
        else:  # audience:<repo>
            token = self.dep.token(subject, token_kind.split(":", 1)[1])
        return f"/mcp/{route}", rpc_headers(SELECTED, token), body

    def remember(self, case_id: str, message: dict[str, Any] | None) -> None:
        """Keep a page's continue arguments and first resource URI for later cases."""
        if not message or "result" not in message:
            return
        payload = _tool_payload(message["result"])
        if not payload or not isinstance(payload.get("data"), dict):
            return
        data = payload["data"]
        saved: dict[str, Any] = {}
        if data.get("next_cursor"):
            saved["continue"] = {
                "schema_version": GRAPH,
                "op": "continue",
                "cursor": data["next_cursor"],
                "query_digest": data["query_digest"],
            }
        resources = data.get("resources")
        first = resources[0] if isinstance(resources, list) and resources else None
        if isinstance(first, dict) and first.get("uri"):
            saved["uri"] = first["uri"]
        if saved:
            self.saved[case_id] = saved

    def row(
        self,
        case: dict[str, Any],
        phase: str,
        resp: Resp | None,
        body: bytes,
        t_start: int,
        t_end: int,
    ) -> dict[str, Any]:
        outcome, message = classify(resp)
        hits: list[str] = []
        witness_in_request = False
        scanned = scan_bytes(resp) if resp is not None else b""
        for domain, witness in self.witnesses().items():
            if witness in body:
                witness_in_request = True
            if witness in scanned and witness not in body:
                hits.append(domain)
        request = case["request"]
        return {
            "case_id": case["id"],
            "phase": phase,
            "principal": case["principal"],
            "route": case["route"],
            "request_kind": request["kind"],
            "tool": request.get("tool"),
            "class": case["class"],
            "expected": case["expected"],
            "outcome": outcome,
            "http_status": resp.status if resp is not None else 0,
            "canary_hits": sorted(hits),
            "server_seen": None,
            "witness_in_request": witness_in_request,
            "observed": {"t_start_ms": t_start, "t_end_ms": t_end},
            "_message": message,
            "_retry_after": resp.header("retry-after") if resp is not None else None,
        }

    async def run_case(self, case: dict[str, Any], phase: str | None = None) -> dict[str, Any]:
        phase = phase or case["phase"]
        path, headers, body = self.build(case, phase)
        started = self.now_ms()
        resp: Resp | None
        try:
            resp = await self.http.request("POST", path, headers, body)
        except (TimeoutError, *RESET):
            resp = None
        row = self.row(case, phase, resp, body, started, self.now_ms())
        self.remember(case["id"], row["_message"])
        return row


def _order_key(case_id: str) -> str:
    return hashlib.sha256(case_id.encode()).hexdigest()


def max_overlap(intervals: list[tuple[int, int]]) -> int:
    events = sorted([(s, 1) for s, _ in intervals] + [(e, -1) for _, e in intervals])
    best = current = 0
    for _time, delta in sorted(events, key=lambda e: (e[0], e[1])):
        current += delta
        best = max(best, current)
    return best


async def run_isolation_cases(st: IsolationState) -> dict[str, Any]:
    iso = st.iso
    cases = st.cases
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for cid in ("W1", "W2"):
        warm = await st.run_case(cases[("warm", cid)])
        if warm["outcome"] != "ok" or cid not in st.saved:
            raise RecorderError("subprocess_error", f"warm case {cid} gave {warm['outcome']}")
        rows[("warm", cid)] = warm
    holds: list[tuple[dict[str, Any], Hold, int]] = []
    for cid in ("H1", "H2"):
        case = cases[("hold", cid)]
        path, headers, body = st.build(case, "hold")
        hold = st.http.hold(path, headers, body)
        started = st.now_ms()
        await hold.open()
        holds.append((case, hold, started))
    await asyncio.sleep(iso["settle_ms"] / 1000)
    gate = asyncio.Semaphore(iso["concurrency"])
    main_cases = sorted(
        (c for (phase, _i), c in cases.items() if phase == "main"),
        key=lambda c: _order_key(c["id"]),
    )

    async def guarded(case: dict[str, Any]) -> dict[str, Any]:
        async with gate:
            return await st.run_case(case)

    for row in await asyncio.gather(*(guarded(c) for c in main_cases)):
        rows[("main", row["case_id"])] = row
    hold_rows = []
    for case, hold, started in holds:
        _path, _headers, body = st.build(case, "hold")
        resp: Resp | None
        try:
            resp = await hold.release(st.http.timeout)
        except (TimeoutError, *RESET):
            resp = None
        row = st.row(case, "hold", resp, body, started, st.now_ms())
        rows[("hold", case["id"])] = row
        hold_rows.append(row)
    main_rows = [r for (phase, _i), r in rows.items() if phase == "main"]
    spans = all(
        h["observed"]["t_start_ms"] <= min(r["observed"]["t_start_ms"] for r in main_rows)
        and h["observed"]["t_end_ms"] >= max(r["observed"]["t_end_ms"] for r in main_rows)
        for h in hold_rows
    )
    intervals = [(r["observed"]["t_start_ms"], r["observed"]["t_end_ms"]) for r in main_rows]
    intervals += [(h["observed"]["t_start_ms"], h["observed"]["t_end_ms"]) for h in hold_rows]
    overlap = max_overlap(intervals)

    # revocation
    main_grants = st.manifest["grants"]["main"]
    revoke = st.manifest["grants"]["revoke"]
    revoked = [
        g
        for g in main_grants
        if not any(
            g["subject"] == r["subject"] and g["repository"] == r["repository"] for r in revoke
        )
    ]
    policies = {"main": st.dep.set_grants(main_grants)}
    for cid in ("R1", "R2"):
        rows[("revocation", cid)] = await st.run_case(cases[("revocation", cid)])
    policies["revoked"] = st.dep.set_grants(revoked)
    for cid in ("R3", "R4"):
        rows[("revocation", cid)] = await st.run_case(cases[("revocation", cid)])
    st.dep.set_grants(main_grants)
    for cid in ("R5", "R6"):
        rows[("revocation", cid)] = await st.run_case(cases[("revocation", cid)])

    # teeth: an over-granted policy must make the ledger fire
    teeth_grants = main_grants + st.manifest["grants"]["teeth_add"]
    policies["teeth"] = st.dep.set_grants(teeth_grants)
    for cid in iso["teeth_rerun"]:
        rows[("teeth", cid)] = await st.run_case(cases[("main", cid)], phase="teeth")
    st.dep.set_grants(main_grants)
    return {"rows": rows, "holds_spanned": spans, "overlap": overlap, "policies": policies}


def _seen(stderr: str, nonce: str) -> bool:
    return any("tool_call" in line and nonce in line for line in stderr.splitlines())


def run_isolation(
    manifest: dict[str, Any],
    manifest_sha: str,
    work: Path,
    fixtures: dict[str, Path],
    keys: Keys,
) -> dict[str, dict[str, Any]]:
    """The three isolation records (main, revocation, teeth) from one serve-shared process."""
    deadlines = manifest["deadlines"]
    iso = manifest["isolation"]
    root = work / "isolation"
    graphs = root / "graphs"
    for name in ("ga", "gb", "gc"):
        shutil.copytree(fixtures[name], graphs / name)
    dep = Deployment(
        manifest,
        keys,
        root,
        repos=[
            ("a", fixtures["a"], graphs / "ga", "c"),
            ("b", fixtures["b"], graphs / "gb", None),
            ("c", fixtures["a"], graphs / "gc", None),
        ],
        listen=iso["listen"],
        limits=iso["limits"],
        grants=manifest["grants"]["main"],
    )
    server = dep.start("serve-shared", deadlines["startup_s"])
    try:
        assert server.port is not None
        http = Http("127.0.0.1", server.port, deadlines["request_s"])
        state = IsolationState(manifest, dep, http, fixtures)
        result = asyncio.run(run_isolation_cases(state))
    finally:
        server.stop(10.0)
    stopped = server.stopped_line()
    stderr = server.stderr_text()
    records: dict[str, dict[str, Any]] = {}
    for unit, phases in (
        ("main", ("warm", "hold", "main")),
        ("revocation", ("revocation",)),
        ("teeth", ("teeth",)),
    ):
        order = {(c["phase"], c["id"]): n for n, c in enumerate(iso["cases"])}
        picked = [(key, row) for key, row in result["rows"].items() if key[0] in phases]
        if unit == "teeth":
            picked.sort(key=lambda kv: iso["teeth_rerun"].index(kv[0][1]))
        else:
            picked.sort(key=lambda kv: order[kv[0]])
        rows = []
        for (phase, cid), row in picked:
            case = state.cases.get((phase, cid)) or state.cases[("main", cid)]
            clean = {k: v for k, v in row.items() if not k.startswith("_")}
            if case["nonce_carrier"]:
                clean["server_seen"] = _seen(stderr, nonce_for(phase, cid))
            rows.append(clean)
        policy = {"main": "main", "revocation": "revoked", "teeth": "teeth"}[unit]
        records[unit] = {
            "policy": policy,
            "policy_sha256": result["policies"][policy],
            "rows": rows,
            "holds_spanned": bool(result["holds_spanned"]) if unit == "main" else None,
            "server": {
                "stopped_line_parsed": stopped is not None,
                "peak_ge_3": bool(stopped and stopped[2] >= 3),
            },
        }
        if stopped is None:
            raise RecorderError("stopped_line_missing", "no shared_http_stopped line")
        records[unit]["observed"] = {
            "server": {"admitted": stopped[0], "rejected": stopped[1], "peak": stopped[2]},
            "max_client_overlap": result["overlap"],
        }
    return records


# ------------------------------------------------------------------------------ line client
class LineClient:
    """A JSON-RPC line client over a real stdio server subprocess (no SDK, no threads)."""

    def __init__(self, argv: list[str], work: Path, label: str, deadline_s: float = 60.0) -> None:
        self.stderr_path = work / f"{label}.stderr"
        self.proc = spawn(
            argv,
            env=child_env(),
            stderr_path=self.stderr_path,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )
        self.buffer = b""
        self.next_id = 1
        self.deadline = time.monotonic() + deadline_s

    def _send(self, message: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(message, separators=(",", ":")).encode() + b"\n")
        self.proc.stdin.flush()

    def _line(self, timeout: float) -> bytes:
        assert self.proc.stdout is not None
        fd = self.proc.stdout.fileno()
        end = min(time.monotonic() + timeout, self.deadline)
        while b"\n" not in self.buffer:
            wait = end - time.monotonic()
            if wait <= 0:
                raise RecorderError("request_deadline", "stdio server did not answer in time")
            ready, _w, _x = select.select([fd], [], [], wait)
            if ready:
                chunk = os.read(fd, 65536)
                if not chunk:
                    raise RecorderError("subprocess_error", "stdio server closed stdout")
                self.buffer += chunk
        line, self.buffer = self.buffer.split(b"\n", 1)
        return line

    def notify(self, method: str, params: Any = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)

    def request(self, method: str, params: Any = None, timeout: float = 30.0) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)
        while True:
            value = json.loads(self._line(timeout))
            if isinstance(value, dict) and value.get("id") == request_id:
                return value

    def initialize(self, revision: str) -> dict[str, Any]:
        answer = self.request(
            "initialize",
            {
                "protocolVersion": revision,
                "capabilities": {},
                "clientInfo": {"name": "atlas-s42-recorder", "version": "1"},
            },
        )
        self.notify("notifications/initialized")
        return answer

    def close(self) -> None:
        with contextlib.suppress(Exception):
            if self.proc.stdin is not None:
                self.proc.stdin.close()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            with contextlib.suppress(Exception):
                self.proc.wait(timeout=5)
        _untrack(self.proc)


# ------------------------------------------------------------------------------ interruption
class CaseDeadline(Exception):
    pass


class Ns:
    """One repository plus an external graph root for the interruption matrix."""

    def __init__(self, base: Path, graphs: str = "graphs", repo: str = "repo") -> None:
        base.mkdir(parents=True, exist_ok=True)
        self.base = base.resolve()
        self.repo = (self.base / repo).resolve()
        self.repo.mkdir(parents=True, exist_ok=True)
        self.graphs = (self.base / graphs).resolve()
        self.memex = (self.base / "memex").resolve()

    def with_graphs(self, graphs: str) -> Ns:
        return Ns(self.base, graphs=graphs, repo=self.repo.name)

    @property
    def atlas(self) -> Path:
        identity = hashlib.sha256(str(self.repo).encode("utf-8")).hexdigest()
        return (self.graphs / "repos" / identity).resolve()

    def args(self, *head: str, tail: tuple[str, ...] = ()) -> list[str]:
        return [*head, "--repo", str(self.repo), "--graph-root", str(self.graphs), *tail]


def fault_env(spec: dict[str, Any]) -> dict[str, str]:
    return {"ATLAS_ACI_JOB_FAULTS": json.dumps(spec, sort_keys=True, separators=(",", ":"))}


class Blocked:
    """A job process held at a seam: stdin is a held PIPE, the seam line is read from stdout."""

    def __init__(self, args: list[str], env: dict[str, str]) -> None:
        self.proc = subprocess.Popen(
            atlas_argv(*args),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=child_env(env),
        )
        _track(self.proc)
        self.buffer = b""

    def _read_line(self, timeout: float) -> bytes:
        assert self.proc.stdout is not None
        fd = self.proc.stdout.fileno()
        end = time.monotonic() + timeout
        while b"\n" not in self.buffer:
            wait = end - time.monotonic()
            if wait <= 0:
                raise RecorderError("subprocess_error", "seam line not seen in time")
            ready, _w, _x = select.select([fd], [], [], wait)
            if ready:
                chunk = os.read(fd, 65536)
                if not chunk:
                    raise RecorderError("subprocess_error", "job exited before the seam")
                self.buffer += chunk
        line, self.buffer = self.buffer.split(b"\n", 1)
        return line

    def wait_seam(self, seam: str, timeout: float = 60.0) -> None:
        end = time.monotonic() + timeout
        while True:
            line = self._read_line(max(0.1, end - time.monotonic()))
            if line.decode("utf-8", "replace").strip() == f"ATLAS_JOB_SEAM {seam}":
                return

    def release(self) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(b"\n")
        self.proc.stdin.flush()

    def finish(self, timeout: float = 90.0) -> tuple[int, str]:
        if self.proc.stdin is not None and not self.proc.stdin.closed:
            with contextlib.suppress(Exception):
                self.proc.stdin.close()
        self.proc.stdin = None
        assert self.proc.stdout is not None
        try:
            rest = self.proc.stdout.read()
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            raise RecorderError("subprocess_error", "blocked job did not finish") from None
        finally:
            _untrack(self.proc)
        text = (self.buffer + (rest or b"")).decode("utf-8", "replace")
        return self.proc.returncode, text


class InterruptRun:
    """The state of one interruption case; every step is bounded and checked against a deadline."""

    def __init__(self, manifest: dict[str, Any], case: dict[str, Any], work: Path) -> None:
        self.manifest, self.case = manifest, case
        self.limit = time.monotonic() + manifest["deadlines"]["interrupt_case_s"]
        self.ns = Ns(work / "interrupt" / case["id"])
        self.trees = manifest["trees"]
        self.names = {t: manifest["canaries"][t]["name"] for t in ("T1", "T2")}
        self.gids: dict[str, str | None] = {}
        self.durations: dict[str, float] = {}
        self.exit_codes: dict[str, int] = {}

    def check(self) -> float:
        left = self.limit - time.monotonic()
        if left <= 0:
            raise CaseDeadline
        return left

    def cli(
        self, label: str, args: list[str], *, env: dict[str, str] | None = None
    ) -> tuple[int, str, str]:
        timeout = min(90.0, self.check())
        started = time.monotonic()
        code, out, err = run_cli(args, env=env, timeout=timeout, cwd=str(self.ns.base))
        self.durations[label] = round(time.monotonic() - started, 2)
        self.exit_codes[label] = code
        return code, out, err

    def write(self, tree: str) -> None:
        write_tree(self.ns.repo, self.trees[tree], clean=True)

    def submit(self, label: str, ns: Ns, now: int, *, env: dict[str, str] | None = None):
        args = ns.args("jobs", "submit", tail=("--langs", "python", "--now", str(now)))
        return self.cli(label, args, env=env)

    def build(self, label: str, ns: Ns, now: int) -> str:
        code, out, err = self.submit(label, ns, now)
        if code != 0:
            raise RecorderError("subprocess_error", f"{label} exited {code}: {err[-200:]}")
        gid = last_json(out)["receipt"]["generation_id"]
        return str(gid)

    def status(self, label: str = "status") -> dict[str, Any]:
        code, out, err = self.cli(label, self.ns.args("jobs", "status"))
        if code != 0:
            raise RecorderError("subprocess_error", f"jobs status exited {code}: {err[-200:]}")
        return last_json(out)

    def job_doc(self, job: str) -> dict[str, Any]:
        data = json.loads((self.ns.atlas / "jobs" / job / "job.json").read_text())
        return data if isinstance(data, dict) else {}

    def block(self, label: str, now: int, seam: str) -> Blocked:
        args = self.ns.args("jobs", "submit", tail=("--langs", "python", "--now", str(now)))
        blocked = Blocked(args, fault_env({seam: {"action": "block"}}))
        blocked.wait_seam(seam, min(60.0, self.check()))
        return blocked

    def write_retention(self, keep_last: int) -> None:
        self.ns.atlas.mkdir(parents=True, exist_ok=True)
        (self.ns.atlas / "retention.json").write_text(
            json.dumps({"format": "atlas-aci-retention.v1", "keep_last": keep_last})
        )

    def server_state(self, ns: Ns, expected_name: str, other_name: str) -> dict[str, Any]:
        """Start a stdio server on the namespace, read its generation and look for the canaries."""
        work = ns.base / "validate"
        work.mkdir(parents=True, exist_ok=True)
        client = LineClient(
            atlas_argv(
                "serve",
                "--repo",
                str(ns.repo),
                "--graph-root",
                str(ns.graphs),
                "--memex-root",
                str(ns.memex),
            ),
            work,
            f"serve-{ns.graphs.name}",
            deadline_s=min(60.0, self.check()),
        )
        try:
            client.initialize(SELECTED)
            status = client.request(
                "tools/call",
                {"name": "graph_query", "arguments": {"schema_version": GRAPH, "op": "status"}},
            )
            payload = _tool_payload(status.get("result", {})) or {}
            gid = find_key(payload, "generation_id")

            def lookup(name: str) -> bool:
                answer = client.request(
                    "tools/call", {"name": "search_symbol", "arguments": {"name": name}}
                )
                result = answer.get("result", {})
                body = _tool_payload(result) or {}
                rows = find_symbol_rows(body)
                return any(isinstance(r, dict) and r.get("name") == name for r in rows)

            return {
                "generation_id": gid,
                "expected_found": lookup(expected_name),
                "other_found": lookup(other_name),
            }
        finally:
            client.close()

    def validate(
        self, ns: Ns, expected_gid: str, expected_tree: str, control_gid: str | None
    ) -> dict[str, Any]:
        other_tree = "T2" if expected_tree == "T1" else "T1"
        doc = self.status("validate-status")
        current = doc.get("current_generation_id")
        jobs = doc.get("jobs", {})
        server = self.server_state(ns, self.names[expected_tree], self.names[other_tree])
        code, _out, _err = self.cli(
            "validate-acquire", ns.args("jobs", "acquire", tail=("--generation", str(current)))
        )
        gid_known = server["generation_id"] is not None
        return {
            "status_current_matches": current == expected_gid,
            "no_invalid_jobs": all(jobs.get(k) == 0 for k in ("running", "interrupted", "failed")),
            "server_gid_matches": (server["generation_id"] == expected_gid) if gid_known else None,
            "server_expected_canary": bool(server["expected_found"]),
            "server_other_canary_absent": not server["other_found"],
            "acquire_ok": code == 0,
            "control_gid_matches": control_gid is not None and current == control_gid,
        }


def find_key(value: Any, key: str) -> Any:
    """First value under ``key`` anywhere in a JSON-like value (breadth first), else None."""
    queue = [value]
    while queue:
        item = queue.pop(0)
        if isinstance(item, dict):
            if key in item and isinstance(item[key], str):
                return item[key]
            queue.extend(item.values())
        elif isinstance(item, list):
            queue.extend(item)
    return None


def find_symbol_rows(value: Any) -> list[Any]:
    """The list of symbol rows of a search_symbol payload (any list of dicts that name a symbol)."""
    found: list[Any] = []
    queue = [value]
    while queue:
        item = queue.pop(0)
        if isinstance(item, dict):
            if "name" in item and ("path" in item or "file" in item or "line" in item):
                found.append(item)
            queue.extend(item.values())
        elif isinstance(item, list):
            queue.extend(item)
    return found


def _flip_byte(data: bytes) -> bytes:
    middle = len(data) // 2
    return data[:middle] + bytes([data[middle] ^ 0xFF]) + data[middle + 1 :]


def _replace_by_copy(path: Path) -> None:
    """Break a hardlink and modify: copy, change, ``os.replace`` onto the path (never in place)."""
    temp = path.with_name(path.name + ".tamper")
    temp.write_bytes(_flip_byte(path.read_bytes()))
    os.replace(temp, path)


def _file_identity(path: Path) -> tuple[int, str]:
    return path.stat().st_ino, hashlib.sha256(path.read_bytes()).hexdigest()


def _inject(run: InterruptRun, case: dict[str, Any]) -> dict[str, Any]:
    """Apply the injection; returns the facts and the namespace/gid bookkeeping to validate."""
    kind = case["injection"]["kind"]
    ns = run.ns
    run.write("T1")
    base_gid = run.build("base", ns, 100)
    twin1 = ns.with_graphs("twin1")
    control: dict[str, str | None] = {"T1": None, "T2": None}
    need = {"T1", "T2"} if kind == "teeth_wrong_expectation" else {case["expected_tree"]}
    if "T1" in need:
        control["T1"] = run.build("twin1", twin1, 100)
    run.write("T2")
    if "T2" in need:
        control["T2"] = run.build("twin2", ns.with_graphs("twin2"), 200)
    expected_tree = case["expected_tree"]
    facts: dict[str, Any] = {}
    validate_ns = ns
    validate_tree = expected_tree
    expected_gid: str
    live_unchanged: bool | None = None

    if kind in ("cancel_marker", "cancel_sigterm"):
        seam = case["injection"]["seam"]
        blocked = run.block("block", 300, seam)
        job = None
        if kind == "cancel_marker":
            job = run.status("status-running").get("running_jobs", [None])[0]
            code, out, _e = run.cli("cancel", ns.args("jobs", "cancel", tail=("--job", str(job))))
            if code != 0 or last_json(out).get("outcome") != "requested":
                raise RecorderError("subprocess_error", "cancel not requested")
        else:
            blocked.proc.send_signal(signal.SIGTERM)
            time.sleep(0.3)
        blocked.release()
        code, _out = blocked.finish(min(90.0, run.check()))
        status = run.status("status-after")
        job_id = job or _only_job_id(run)
        doc = run.job_doc(job_id)
        facts = {
            "exit": code,
            "state": doc.get("state"),
            "cancel": doc.get("cancel"),
            "published": status.get("current_generation_id") != base_gid,
        }
        expected_gid = base_gid
    elif kind == "kill":
        seam, at = case["injection"]["seam"], case["injection"].get("at")
        spec: dict[str, Any] = {"action": "kill"}
        if at is not None:
            spec["at"] = at
        code, _out, _err = run.submit("killed", ns, 300, env=fault_env({seam: spec}))
        interrupted = run.status("status-interrupted").get("interrupted_jobs", [])
        job = interrupted[0]["job_id"] if interrupted else None
        facts = {"killed": is_killed(code), "interrupted": job is not None}
        if job is None:
            raise RecorderError("subprocess_error", "no interrupted job after the kill")
        facts["interrupted_state"] = run.job_doc(job).get("state")
        code, out, err = run.cli(
            "resume", ns.args("jobs", "resume", tail=("--job", job, "--now", "400"))
        )
        if code != 0:
            raise RecorderError("subprocess_error", f"resume exited {code}: {err[-200:]}")
        envelope = last_json(out)
        receipt = envelope["receipt"]
        if seam == "checkpoint":
            facts["resumed_from"] = receipt.get("resumed_from")
        facts["outcome"] = receipt.get("outcome")
        facts["state"] = run.job_doc(job).get("state")
        expected_gid = str(receipt["generation_id"])
    elif kind == "gc_vs_lease":
        expected_gid = run.build("t2", ns, 300)
        run.write_retention(case_keep(run.manifest))
        holder = subprocess.Popen(
            atlas_argv(*ns.args("jobs", "acquire", tail=("--generation", str(base_gid), "--hold"))),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=child_env(),
        )
        _track(holder)
        try:
            assert holder.stdout is not None
            ready, _w, _x = select.select([holder.stdout], [], [], min(60.0, run.check()))
            held = json.loads(holder.stdout.readline()) if ready else {}
            code, out, _e = run.cli("gc1", ns.args("jobs", "gc", tail=("--now", "2000000000")))
            first = last_json(out) if code == 0 else {}
            leased = (
                any(
                    r.get("generation_id") == base_gid and r.get("reason") == "leased"
                    for r in first.get("retained", [])
                )
                and held.get("origin") == "retained"
            )
            holder.kill()
            holder.wait(timeout=10)
            killed = is_killed(holder.returncode)
        finally:
            with contextlib.suppress(Exception):
                holder.kill()
            _untrack(holder)
        code, out, _e = run.cli("gc2", ns.args("jobs", "gc", tail=("--now", "2000000000")))
        second = last_json(out) if code == 0 else {}
        retired = any(r.get("generation_id") == base_gid for r in second.get("retired", []))
        code, out, _e = run.cli(
            "acquire-expired", ns.args("jobs", "acquire", tail=("--generation", str(base_gid)))
        )
        facts = {
            "leased_retained": leased,
            "holder_killed": killed,
            "retired_after_release": retired,
            "acquire_exit": code,
            "acquire_code": last_json(out).get("code") if code != 0 else None,
        }
    elif kind == "gc_vs_build":
        blocked = run.block("block", 300, case["injection"]["seam"])
        code, out, _e = run.cli(
            "gc-busy", ns.args("jobs", "gc", tail=("--now", "5", "--wait-seconds", "0"))
        )
        gc_code = last_json(out).get("code") if code != 0 else None
        blocked.release()
        bcode, bout = blocked.finish(min(90.0, run.check()))
        if bcode != 0:
            raise RecorderError("subprocess_error", f"blocked build exited {bcode}")
        expected_gid = str(last_json(bout)["receipt"]["generation_id"])
        job = _only_job_id(run)
        facts = {"gc_exit": code, "gc_code": gc_code, "state": run.job_doc(job).get("state")}
    elif kind == "teeth_replace_by_copy":
        expected_gid = run.build("t2", ns, 300)
        copy = ns.with_graphs("copy")
        shutil.copytree(ns.graphs, copy.graphs)
        live_files = [
            ns.atlas / "graph.9.db",
            copy.atlas / "graph.9.db",
        ]
        before = [_file_identity(p) for p in live_files]
        farm = copy.atlas / "generations" / expected_gid / "graph.9.db"
        _replace_by_copy(farm)
        after = [_file_identity(p) for p in live_files]
        live_unchanged = before == after
        code, _o, _e = run.cli(
            "acquire-corrupt", copy.args("jobs", "acquire", tail=("--generation", expected_gid))
        )
        facts = {"corrupted_farm_acquire_refused": code != 0}
        validate_ns = copy
    elif kind == "teeth_wrong_expectation":
        current_gid = run.build("t2", ns, 300)
        _ = current_gid
        expected_gid = base_gid
        validate_tree = "T1"
        facts = {"validated_against": "T1"}
    else:
        raise RecorderError("subprocess_error", f"unknown injection {kind}")
    return {
        "facts": facts,
        "ns": validate_ns,
        "tree": validate_tree,
        "expected_gid": expected_gid,
        "control": control,
        "base_gid": base_gid,
        "live_unchanged": live_unchanged,
    }


def case_keep(manifest: dict[str, Any]) -> int:
    return int(manifest["interruption"]["retention"]["keep_last"])


def _only_job_id(run: InterruptRun) -> str:
    jobs_dir = run.ns.atlas / "jobs"
    names = sorted(p.name for p in jobs_dir.iterdir() if re.fullmatch(r"[0-9a-f]{64}", p.name))
    job_docs = []
    for name in names:
        doc = json.loads((jobs_dir / name / "job.json").read_text())
        job_docs.append((str(doc.get("created_at", "")), name, doc))
    # the job that was injected is the one that is not the (completed) base build
    latest = [n for _c, n, d in job_docs if d.get("state") != "completed" or len(job_docs) == 1]
    return latest[-1] if latest else names[-1]


def run_interrupt_case(
    manifest: dict[str, Any], manifest_sha: str, case: dict[str, Any], work: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    """One interruption case: (body, observed). Raises RecorderError / CaseDeadline on failure."""
    run = InterruptRun(manifest, case, work)
    state = _inject(run, case)
    expected_tree = state["tree"]
    control = state["control"].get(expected_tree)
    validation = run.validate(state["ns"], state["expected_gid"], expected_tree, control)
    parts = [validation[k] for k in AGG.VALIDATION]
    valid = all(
        v is True or (k == "server_gid_matches" and v is None)
        for k, v in zip(AGG.VALIDATION, parts, strict=True)
    )
    body = {
        "injection": case["injection"],
        "expected_tree": case["expected_tree"],
        "facts": state["facts"],
        "validation": validation,
        "valid": valid,
        "live_unchanged": state["live_unchanged"],
    }
    observed = {
        "gids": {
            "base": state["base_gid"],
            "expected": state["expected_gid"],
            "control": control,
        },
        "exit_codes": run.exit_codes,
        "durations_s": run.durations,
    }
    return body, observed


# ---------------------------------------------------------------------------------- compat
def tools_digest(tools: list[Any]) -> str:
    return hashlib.sha256(AGG.canonical(tools)).hexdigest()


def _expected(requested: str) -> dict[str, Any]:
    negotiate = importlib.import_module("atlas_aci.protocol.revisions").negotiate
    negotiation = negotiate(requested)
    return {"outcome": negotiation.outcome, "offered": negotiation.offered}


async def raw_http_cell(
    http: Http,
    path: str,
    token: str | None,
    requested: str,
    call: dict[str, Any],
    witness: bytes,
) -> dict[str, Any]:
    """Initialize at ``requested``, then send every later request with the negotiated header."""
    started = time.monotonic()
    init_body = rpc_message(
        "initialize",
        {
            "protocolVersion": requested,
            "capabilities": {},
            "clientInfo": {"name": "atlas-s42-recorder", "version": "1"},
        },
    )
    resp = await http.request("POST", path, rpc_headers(None, token), init_body)
    outcome, message = classify(resp)
    if outcome != "ok" or message is None:
        raise RecorderError("subprocess_error", f"initialize gave {outcome}")
    result = message["result"]
    negotiated = result["protocolVersion"]
    note = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode()
    await http.request("POST", path, rpc_headers(negotiated, token), note)
    listed = await http.request(
        "POST", path, rpc_headers(negotiated, token), rpc_message("tools/list")
    )
    outcome_list, listed_message = classify(listed)
    if outcome_list != "ok" or listed_message is None:
        raise RecorderError("subprocess_error", f"tools/list gave {outcome_list}")
    tools = listed_message["result"]["tools"]
    called = await http.request(
        "POST", path, rpc_headers(negotiated, token), tool_message(call["tool"], call["arguments"])
    )
    call_outcome, _m = classify(called)
    return {
        "negotiated": negotiated,
        "sent_revision": negotiated,
        "server_info": {
            "name": result.get("serverInfo", {}).get("name"),
            "version": result.get("serverInfo", {}).get("version"),
        },
        "tools": {"count": len(tools), "sha256": tools_digest(tools)},
        "call": {
            "tool": call["tool"],
            "outcome": call_outcome,
            "canary_hit": witness in scan_bytes(called),
        },
        "latency_ms": int((time.monotonic() - started) * 1000),
    }


def raw_stdio_cell(
    argv: list[str], work: Path, label: str, requested: str, call: dict[str, Any], witness: bytes
) -> dict[str, Any]:
    started = time.monotonic()
    client = LineClient(argv, work, label)
    try:
        init = client.initialize(requested)
        result = init["result"]
        negotiated = result["protocolVersion"]
        listed = client.request("tools/list")
        tools = listed["result"]["tools"]
        answer = client.request(
            "tools/call", {"name": call["tool"], "arguments": call["arguments"]}
        )
        text = json.dumps(answer, sort_keys=True).encode()
        outcome, _m = classify(
            Resp(200, "OK", [("content-type", "application/json")], json.dumps(answer).encode())
        )
    finally:
        client.close()
    return {
        "negotiated": negotiated,
        "sent_revision": negotiated,
        "server_info": {
            "name": result.get("serverInfo", {}).get("name"),
            "version": result.get("serverInfo", {}).get("version"),
        },
        "tools": {"count": len(tools), "sha256": tools_digest(tools)},
        "call": {"tool": call["tool"], "outcome": outcome, "canary_hit": witness in text},
        "latency_ms": int((time.monotonic() - started) * 1000),
    }


async def sdk_cell(
    transport: str,
    *,
    stdio_argv: list[str] | None,
    url: str | None,
    token: str | None,
    call: dict[str, Any],
    witness: bytes,
    errlog_path: Path,
    timeout: float,
) -> dict[str, Any]:
    """The mcp SDK client over stdio or Streamable HTTP; ``negotiated`` is read from the result."""
    from mcp import ClientSession, StdioServerParameters, types
    from mcp.client.stdio import stdio_client

    started = time.monotonic()

    async def exchange(session: Any) -> dict[str, Any]:
        init = await session.initialize()
        listed = await session.list_tools()
        tools = [t.model_dump(by_alias=True, exclude_none=True) for t in listed.tools]
        called = await session.call_tool(call["tool"], call["arguments"])
        dumped = called.model_dump(by_alias=True, exclude_none=True)
        text = json.dumps(dumped, sort_keys=True).encode()
        outcome, _m = classify(
            Resp(
                200,
                "OK",
                [("content-type", "application/json")],
                json.dumps({"jsonrpc": "2.0", "id": 1, "result": dumped}).encode(),
            )
        )
        return {
            "negotiated": str(init.protocolVersion),
            "sent_revision": str(init.protocolVersion),
            "server_info": {"name": init.serverInfo.name, "version": init.serverInfo.version},
            "tools": {"count": len(tools), "sha256": tools_digest(tools)},
            "call": {"tool": call["tool"], "outcome": outcome, "canary_hit": witness in text},
            "sdk_default": types.LATEST_PROTOCOL_VERSION,
        }

    async def go() -> dict[str, Any]:
        if transport == "stdio":
            assert stdio_argv is not None
            params = StdioServerParameters(
                command=stdio_argv[0], args=stdio_argv[1:], env=child_env()
            )
            with open(errlog_path, "w") as errlog:
                async with stdio_client(params, errlog=errlog) as (read, write):
                    async with ClientSession(read, write) as session:
                        return await exchange(session)
        import httpx
        from mcp.client.streamable_http import streamable_http_client

        assert url is not None
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        async with (
            httpx.AsyncClient(headers=headers, timeout=timeout) as client,
            streamable_http_client(url, http_client=client) as streams,
        ):
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                return await exchange(session)

    try:
        out = await asyncio.wait_for(go(), timeout)
    except ImportError as exc:
        raise RecorderError("sdk_unavailable", str(exc)) from None
    except TimeoutError:
        raise RecorderError("request_deadline", f"sdk {transport} exceeded {timeout}s") from None
    out["latency_ms"] = int((time.monotonic() - started) * 1000)
    return out


def run_compat(
    manifest: dict[str, Any],
    manifest_sha: str,
    work: Path,
    fixtures: dict[str, Path],
    keys: Keys,
    emit: Any,
) -> None:
    """Every compatibility cell; ``emit(unit, body, observed)`` or ``emit_failed`` per cell."""
    deadlines = manifest["deadlines"]
    cells = manifest["compatibility"]["cells"]
    root = work / "compat"
    graphs = root / "graphs"
    shutil.copytree(fixtures["gb"], graphs / "gb")
    b_pattern = manifest["canaries"]["B"]["pattern"]
    witness = manifest["canaries"]["B"]["witness"].encode()
    call = {"tool": "search_symbol", "arguments": {"name": f"{b_pattern}_fn"}}
    dep = Deployment(
        manifest,
        keys,
        root,
        repos=[("b", fixtures["b"], graphs / "gb", None)],
        listen={"max_inflight": 8, "max_inflight_per_principal": 4},
        limits={"max_calls_per_minute": 0, "max_calls_per_principal_per_minute": 0},
        grants=[{"subject": "p1", "repository": "b", "tools": ["search_symbol", "graph_query"]}],
    )
    token = dep.token("p1", "b")
    plain_graphs = str(graphs / "gb")
    serve = ["serve", "--repo", str(fixtures["b"]), "--graph-root", plain_graphs]

    def record(cell: dict[str, Any], measured: dict[str, Any]) -> None:
        latency = measured.pop("latency_ms", None)
        measured.pop("sdk_default", None)
        body = {
            "client": cell["client"],
            "transport": cell["transport"],
            "requested": cell["requested"],
            "sent_revision": measured["sent_revision"],
            "expected": _expected(
                cell["requested"] if cell["requested"] != "sdk_default" else measured_default()
            ),
            "negotiated": measured["negotiated"],
            "server_info": measured["server_info"],
            "mcp_version": importlib_metadata_version("mcp"),
            "tools": measured["tools"],
            "call": measured["call"],
        }
        emit(cell["id"], body, {"latency_ms": latency})

    def measured_default() -> str:
        from mcp import types

        return str(types.LATEST_PROTOCOL_VERSION)

    def fail(cell: dict[str, Any], exc: BaseException) -> None:
        code = exc.code if isinstance(exc, RecorderError) else "unexpected_exception"
        emit(cell["id"], None, {"error": f"{type(exc).__name__}: {exc}"[:300]}, failure=code)

    shared = dep.start("serve-shared", deadlines["startup_s"])
    try:
        assert shared.port is not None
        http = Http("127.0.0.1", shared.port, deadlines["request_s"])
        for cell in cells:
            if cell["transport"] != "shared-http":
                continue
            try:
                if cell["client"] == "raw-jsonrpc":
                    measured = asyncio.run(
                        raw_http_cell(http, "/mcp/b", token, cell["requested"], call, witness)
                    )
                else:
                    measured = asyncio.run(
                        sdk_cell(
                            "shared-http",
                            stdio_argv=None,
                            url=f"http://127.0.0.1:{shared.port}/mcp/b",
                            token=token,
                            call=call,
                            witness=witness,
                            errlog_path=root / "sdk-shared.err",
                            timeout=deadlines["request_s"],
                        )
                    )
                record(cell, measured)
            except Exception as exc:
                fail(cell, exc)
    finally:
        shared.stop(10.0)
    for cell in cells:
        if cell["transport"] == "shared-http":
            continue
        try:
            if cell["transport"] == "stdio":
                argv = atlas_argv(*serve, "--memex-root", str(root / "memex-stdio"))
                if cell["client"] == "raw-jsonrpc":
                    measured = raw_stdio_cell(
                        argv, root, "stdio-raw", cell["requested"], call, witness
                    )
                else:
                    measured = asyncio.run(
                        sdk_cell(
                            "stdio",
                            stdio_argv=argv,
                            url=None,
                            token=None,
                            call=call,
                            witness=witness,
                            errlog_path=root / "stdio-sdk.err",
                            timeout=deadlines["request_s"],
                        )
                    )
            else:  # plain http (S39)
                server = Server(
                    f"serve-http-{cell['client']}",
                    atlas_argv(
                        *serve,
                        "--memex-root",
                        str(root / f"memex-http-{cell['client']}"),
                        "--transport",
                        "http",
                        "--port",
                        "0",
                    ),
                    root / "logs",
                    marker="http_listening",
                    startup_s=deadlines["startup_s"],
                )
                try:
                    assert server.port is not None
                    if cell["client"] == "raw-jsonrpc":
                        measured = asyncio.run(
                            raw_http_cell(
                                Http("127.0.0.1", server.port, deadlines["request_s"]),
                                "/mcp",
                                None,
                                cell["requested"],
                                call,
                                witness,
                            )
                        )
                    else:
                        measured = asyncio.run(
                            sdk_cell(
                                "http",
                                stdio_argv=None,
                                url=f"http://127.0.0.1:{server.port}/mcp",
                                token=None,
                                call=call,
                                witness=witness,
                                errlog_path=root / "http-sdk.err",
                                timeout=deadlines["request_s"],
                            )
                        )
                finally:
                    server.stop(10.0)
            record(cell, measured)
        except Exception as exc:
            fail(cell, exc)


def importlib_metadata_version(name: str) -> str:
    import importlib.metadata

    return importlib.metadata.version(name)


# -------------------------------------------------------------------------------- capacity
class ProcSampler(threading.Thread):
    """Samples VmRSS/VmHWM and the fd count of a pid every 200 ms (Linux /proc)."""

    def __init__(self, pid: int) -> None:
        super().__init__(daemon=True)
        self.pid = pid
        self.stop_flag = threading.Event()
        self.samples = 0
        self.rss_kib = 0
        self.hwm_kib = 0
        self.fds = 0
        self.status_ok = False
        self.fd_ok = False

    def run(self) -> None:
        while not self.stop_flag.is_set():
            try:
                for line in Path(f"/proc/{self.pid}/status").read_text().splitlines():
                    if line.startswith("VmRSS:"):
                        self.rss_kib = max(self.rss_kib, int(line.split()[1]))
                    elif line.startswith("VmHWM:"):
                        self.hwm_kib = max(self.hwm_kib, int(line.split()[1]))
                self.status_ok = True
                self.samples += 1
            except (OSError, ValueError, IndexError):
                pass
            try:
                self.fds = max(self.fds, len(os.listdir(f"/proc/{self.pid}/fd")))
                self.fd_ok = True
            except OSError:
                pass
            self.stop_flag.wait(0.2)


def percentile(values: list[int], fraction: float) -> int:
    ordered = sorted(values)
    if not ordered:
        return 0
    return ordered[min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))]


async def capacity_workload(
    manifest: dict[str, Any], dep: Deployment, http: Http
) -> dict[str, Any]:
    cap = manifest["capacity"]
    work = cap["workload"]
    mix = work["mix"]
    limit_attempts = int(work["max_attempts"])
    inflight_by_principal: dict[str, int] = {}
    peak_by_principal: dict[str, int] = {}
    latencies: list[int] = []
    attempts_total = 0
    rejections = {"inflight_limit": 0, "principal_inflight_limit": 0}
    untyped_5xx = 0
    missing_retry_after = 0
    completed = 0
    denied = 0
    foreign_hits = 0
    witnesses = {
        "a": manifest["canaries"]["A"]["witness"].encode(),
        "b": manifest["canaries"]["B"]["witness"].encode(),
    }

    async def one_call(principal: str, entry: list[Any]) -> None:
        nonlocal attempts_total, untyped_5xx, missing_retry_after, completed, denied, foreign_hits
        repo, tool, arguments = entry
        body = tool_message(tool, arguments)
        token = dep.token(principal, repo)
        for _attempt in range(limit_attempts):
            attempts_total += 1
            inflight_by_principal[principal] = inflight_by_principal.get(principal, 0) + 1
            peak_by_principal[principal] = max(
                peak_by_principal.get(principal, 0), inflight_by_principal[principal]
            )
            started = time.monotonic()
            try:
                resp: Resp | None = await http.request(
                    "POST", f"/mcp/{repo}", rpc_headers(SELECTED, token), body
                )
            except (TimeoutError, *RESET):
                resp = None
            finally:
                inflight_by_principal[principal] -= 1
            outcome, _m = classify(resp)
            if outcome in ("http:503:inflight_limit", "http:503:principal_inflight_limit"):
                rejections[outcome.rsplit(":", 1)[1]] += 1
                assert resp is not None
                retry = resp.header("retry-after")
                if retry is None:
                    missing_retry_after += 1
                await asyncio.sleep(float(retry) if retry and retry.isdigit() else 1.0)
                continue
            if resp is not None and resp.status >= 500:
                untyped_5xx += 1
            if outcome == "ok":
                completed += 1
                latencies.append(int((time.monotonic() - started) * 1000))
                other = witnesses["b" if repo == "a" else "a"]
                if resp is not None and other in scan_bytes(resp):
                    foreign_hits += 1
            else:
                denied += 1
            return

    async def client(index: int, principal: str) -> None:
        for k in range(work["calls_per_client"]):
            await one_call(principal, mix[(index + k) % len(mix)])

    started = time.monotonic()
    await asyncio.gather(*(client(i, p) for i, p in enumerate(work["client_principals"])))
    wall = round(time.monotonic() - started, 2)
    total = work["clients"] * work["calls_per_client"]
    cap_per = cap["listen"]["max_inflight_per_principal"]
    return {
        "latencies": latencies,
        "wall_s": wall,
        "attempts": attempts_total,
        "rejections": rejections,
        "invariants": {
            "all_calls_completed": completed == total,
            "client_principal_concurrency_within_cap": all(
                v <= cap_per for v in peak_by_principal.values()
            ),
            "no_untyped_5xx": untyped_5xx == 0,
            "typed_503_has_retry_after": missing_retry_after == 0,
            "unauthorized_zero": denied == 0 and foreign_hits == 0,
        },
    }


async def capacity_saturation(
    manifest: dict[str, Any], dep: Deployment, http: Http
) -> dict[str, Any]:
    sat = manifest["capacity"]["saturation"]
    canary = manifest["canaries"]["A"]["pattern"]
    body = tool_message("search_symbol", {"name": f"{canary}_hub"})
    polls = 0

    def headers(principal: str) -> list[tuple[str, str]]:
        return rpc_headers(SELECTED, dep.token(principal, "a"))

    async def call(principal: str) -> tuple[str, Resp | None]:
        try:
            resp: Resp | None = await http.request("POST", "/mcp/a", headers(principal), body)
        except (TimeoutError, *RESET):
            resp = None
        return classify(resp)[0], resp

    result: dict[str, Any] = {}
    # S1: the per-principal cap
    holders = [http.hold("/mcp/a", headers(sat["principal"]), body) for _ in range(sat["holds"])]
    for hold in holders:
        await hold.open()
    await asyncio.sleep(sat["settle_ms"] / 1000)
    third: tuple[str, Resp | None] = ("transport_error", None)
    for _ in range(sat["max_polls"]):
        polls += 1
        third = await call(sat["principal"])
        if third[0] != "ok":
            break
        await asyncio.sleep(sat["poll_ms"] / 1000)
    result["third_rejected_typed"] = (
        third[0] == "http:503:principal_inflight_limit"
        and third[1] is not None
        and third[1].header("retry-after") == "1"
    )
    other, _r = await call(sat["other"])
    result["other_principal_admitted"] = other == "ok"
    for hold in holders:
        await hold.close()
    recovered = False
    for _ in range(sat["max_polls"]):
        polls += 1
        state, _r = await call(sat["principal"])
        if state == "ok":
            recovered = True
            break
        await asyncio.sleep(sat["poll_ms"] / 1000)
    result["recovered_after_disconnect"] = recovered
    # S2: the shared gate (p1 holds 2, p2 holds 2, p3 is refused by the gate)
    holders = [http.hold("/mcp/a", headers(sat["principal"]), body) for _ in range(sat["holds"])]
    holders += [http.hold("/mcp/a", headers(sat["other"]), body) for _ in range(sat["holds"])]
    for hold in holders:
        await hold.open()
    await asyncio.sleep(sat["settle_ms"] / 1000)
    gate: tuple[str, Resp | None] = ("transport_error", None)
    for _ in range(sat["max_polls"]):
        polls += 1
        gate = await call(sat["gate_principal"])
        if gate[0] != "ok":
            break
        await asyncio.sleep(sat["poll_ms"] / 1000)
    result["gate_503_typed"] = (
        gate[0] == "http:503:inflight_limit"
        and gate[1] is not None
        and gate[1].header("retry-after") is not None
    )
    for hold in holders:
        await hold.close()
    result["polls"] = polls
    return result


def run_capacity(
    manifest: dict[str, Any],
    manifest_sha: str,
    work: Path,
    fixtures: dict[str, Path],
    keys: Keys,
) -> tuple[dict[str, Any], dict[str, Any]]:
    deadlines = manifest["deadlines"]
    cap = manifest["capacity"]
    root = work / "capacity"
    graphs = root / "graphs"
    for name in ("ga", "gb"):
        shutil.copytree(fixtures[name], graphs / name)
    dep = Deployment(
        manifest,
        keys,
        root,
        repos=[
            ("a", fixtures["a"], graphs / "ga", None),
            ("b", fixtures["b"], graphs / "gb", None),
        ],
        listen=cap["listen"],
        limits=cap["limits"],
        grants=cap["workload"]["grants"],
    )
    server = dep.start("serve-shared", deadlines["startup_s"])
    sampler = ProcSampler(server.proc.pid)
    sampler.start()
    try:
        assert server.port is not None
        http = Http("127.0.0.1", server.port, deadlines["request_s"])
        load = asyncio.run(capacity_workload(manifest, dep, http))
        saturation = asyncio.run(capacity_saturation(manifest, dep, http))
    finally:
        sampler.stop_flag.set()
        sampler.join(timeout=5)
        server.stop(10.0)
    stopped = server.stopped_line()
    if stopped is None:
        raise RecorderError("stopped_line_missing", "no shared_http_stopped line")
    document = json.loads(dep.config_path.read_text())
    listen_keys = ("max_inflight", "max_inflight_per_principal", "max_body_bytes")
    written = {
        "listen": {k: document["listen"].get(k) for k in listen_keys},
        "limits": document["limits"],
    }
    invariants = dict(load["invariants"])
    invariants["server_peak_within_limit"] = stopped[2] <= cap["listen"]["max_inflight"]
    invariants["limits_match_config_document"] = written == {
        "listen": cap["listen"],
        "limits": cap["limits"],
    }
    body = {
        "limits": written,
        "workload": cap["workload"],
        "invariants": {k: invariants[k] for k in AGG.INVARIANTS},
        "saturation": {
            "third_rejected_typed": saturation["third_rejected_typed"],
            "other_principal_admitted": saturation["other_principal_admitted"],
            "recovered_after_disconnect": saturation["recovered_after_disconnect"],
            "gate_503_typed": saturation["gate_503_typed"],
            "stopped_line_rejected_ge_1": stopped[1] >= 1,
        },
    }
    lat = load["latencies"]
    unit = "bytes" if sys.platform == "darwin" else "kib"
    observed = {
        "latency_ms": {
            "n": len(lat),
            "p50": percentile(lat, 0.5),
            "p95": percentile(lat, 0.95),
            "max": max(lat) if lat else 0,
        },
        "wall_s": load["wall_s"],
        "attempts": load["attempts"],
        "rejections": load["rejections"],
        "server": {
            "admitted": stopped[0],
            "rejected": stopped[1],
            "peak": stopped[2],
            "source": "stopped_line",
        },
        "memory": {
            "vm_rss_kib": sampler.rss_kib,
            "vm_hwm_kib": sampler.hwm_kib,
            "samples": sampler.samples,
            "source": "proc_status" if sampler.status_ok else "unavailable",
        },
        "fds": {"max": sampler.fds, "source": "proc_fd" if sampler.fd_ok else "unavailable"},
        "rusage": {
            "ru_maxrss": server.maxrss if server.maxrss is not None else 0,
            "unit": unit,
            "source": "wait4" if server.maxrss is not None else "unavailable",
        },
        "saturation_polls": saturation["polls"],
    }
    return body, observed


# ------------------------------------------------------------------------------------ main
def write_record(
    out: Path,
    manifest_sha: str,
    kind: str,
    unit: str,
    *,
    body: dict[str, Any] | None,
    observed: dict[str, Any] | None = None,
    failure: str | None = None,
    diagnostic: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "format": AGG.FORMAT_RAW,
        "kind": kind,
        "unit": unit,
        "manifest_sha256": manifest_sha,
        "status": "failed" if failure else "complete",
        "failure": {"code": failure} if failure else None,
    }
    if body is not None and not failure:
        record["body"] = body
    if observed:
        record["observed"] = observed
    if diagnostic:
        record["diagnostic"] = diagnostic
    out.mkdir(parents=True, exist_ok=True)
    with open(out / f"{kind}.{unit}.json.gz", "xb") as handle:
        handle.write(AGG.gzip_canonical(record))
    return record


class Collector:
    """Thread-safe unit results; every unit is written once."""

    def __init__(self, out: Path, manifest_sha: str) -> None:
        self.out, self.sha = out, manifest_sha
        self.lock = threading.Lock()
        self.records: dict[tuple[str, str], dict[str, Any]] = {}

    def emit(
        self,
        kind: str,
        unit: str,
        body: dict[str, Any] | None,
        observed: dict[str, Any] | None = None,
        *,
        failure: str | None = None,
        diagnostic: dict[str, Any] | None = None,
    ) -> None:
        with self.lock:
            if (kind, unit) in self.records:
                return
            self.records[(kind, unit)] = write_record(
                self.out,
                self.sha,
                kind,
                unit,
                body=body,
                observed=observed,
                failure=failure,
                diagnostic=diagnostic,
            )

    def has(self, kind: str, unit: str) -> bool:
        with self.lock:
            return (kind, unit) in self.records


def failure_code(exc: BaseException) -> str:
    if isinstance(exc, RecorderError):
        return exc.code
    if isinstance(exc, CaseDeadline):
        return "case_deadline"
    return "unexpected_exception"


def phase_environment(
    args: argparse.Namespace, manifest: dict[str, Any], work: Path, collector: Collector
) -> bool:
    canary, fstype = run_lock_canary(work)
    body = {
        "mcp_version": importlib_metadata_version("mcp"),
        "atlas_aci_version": importlib_metadata_version("atlas-aci"),
        "lock_canary": canary,
    }
    observed = {
        "python": platform.python_version(),
        "platform": f"{platform.system()}-{platform.machine()}",
        "runner": "container_image" if args.image else "host_process",
        "image": args.image,
        "fstype": fstype,
        "cpu_count": os.cpu_count(),
    }
    collector.emit("environment", "main", body, observed)
    return bool(
        canary["second_fd_blocks"]
        and canary["process_blocks"]
        and canary["hardlink_same_inode"]
        and not canary["shared_folder_fs"]
    )


def warm_grammars(work: Path) -> None:
    """One serial index run primes the tree-sitter grammar cache before any parallel process runs.

    A cold cache downloads the grammar on first use; four processes doing that at once race on
    the cache directory. A failure here is not recorded: every unit that needs the grammar then
    fails on its own deadline with its own diagnostic.
    """
    repo = work / "warm" / "repo"
    write_tree(repo, {"warm.py": "def warm():\n    return 1\n"})
    with contextlib.suppress(RecorderError):
        index_repo(repo, work / "warm" / "graphs", str(work), timeout=WARM_TIMEOUT_S)


def run_independent_phases(
    phases: set[str],
    manifest: dict[str, Any],
    work: Path,
    collector: Collector,
    keys: Keys,
) -> None:
    """Isolation, compat and capacity, one after another (each owns its server)."""
    sha = collector.sha
    try:
        fixtures = build_fixtures(manifest, work / "fixtures")
    except Exception as exc:
        for kind, unit in AGG.expected_units(manifest):
            if kind in phases and not collector.has(kind, unit):
                collector.emit(
                    kind,
                    unit,
                    None,
                    failure=failure_code(exc),
                    diagnostic={"error": f"{type(exc).__name__}: {exc}"[-400:]},
                )
        return
    if "isolation" in phases:
        try:
            for unit, rec in run_isolation(manifest, sha, work, fixtures, keys).items():
                observed = rec.pop("observed", None)
                collector.emit("isolation", unit, rec, observed)
        except Exception as exc:
            for unit in ("main", "revocation", "teeth"):
                collector.emit(
                    "isolation",
                    unit,
                    None,
                    failure=failure_code(exc),
                    diagnostic={"error": f"{type(exc).__name__}: {exc}"[-400:]},
                )
    if "compat" in phases:

        def emit_cell(
            unit: str,
            body: dict[str, Any] | None,
            observed: dict[str, Any],
            failure: str | None = None,
        ) -> None:
            collector.emit(
                "compat",
                unit,
                body,
                observed if not failure else None,
                failure=failure,
                diagnostic=observed if failure else None,
            )

        try:
            run_compat(manifest, sha, work, fixtures, keys, emit_cell)
        except Exception as exc:
            for cell in manifest["compatibility"]["cells"]:
                if not collector.has("compat", cell["id"]):
                    collector.emit(
                        "compat",
                        cell["id"],
                        None,
                        failure=failure_code(exc),
                        diagnostic={"error": f"{type(exc).__name__}: {exc}"[-400:]},
                    )
    if "capacity" in phases:
        try:
            body, observed = run_capacity(manifest, sha, work, fixtures, keys)
            collector.emit("capacity", "main", body, observed)
        except Exception as exc:
            collector.emit(
                "capacity",
                "main",
                None,
                failure=failure_code(exc),
                diagnostic={"error": f"{type(exc).__name__}: {exc}"[-400:]},
            )


def run_interrupt_unit(
    manifest: dict[str, Any], sha: str, case: dict[str, Any], work: Path, collector: Collector
) -> None:
    try:
        body, observed = run_interrupt_case(manifest, sha, case, work)
        collector.emit("interrupt", case["id"], body, observed)
    except Exception as exc:
        collector.emit(
            "interrupt",
            case["id"],
            None,
            failure=failure_code(exc),
            diagnostic={"error": f"{type(exc).__name__}: {exc}"[-400:]},
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="s42_record")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--image", default=None)
    parser.add_argument("--phases", default="environment,isolation,compat,capacity,interrupt")
    parser.add_argument("--workers", type=int, default=None)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2
    phases = {p for p in args.phases.split(",") if p}
    if not phases <= set(AGG.RECORD_KINDS):
        sys.stderr.write("s42_record: unknown phase\n")
        return 2
    try:
        manifest, sha = load_manifest(Path(args.manifest))
    except AGG.ReadinessInputError as exc:
        sys.stderr.write(f"s42_record: invalid manifest: {exc}\n")
        return 2
    workers = args.workers if args.workers is not None else int(manifest["interruption"]["workers"])
    if not 1 <= workers <= 4:
        sys.stderr.write("s42_record: --workers must be 1..4\n")
        return 2
    out, work = Path(args.out), Path(args.work)
    for label, path in (("--out", out), ("--work", work)):
        if path.exists() and any(path.iterdir()):
            sys.stderr.write(f"s42_record: {label} is not empty\n")
            return 3
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    atexit.register(reap_children)
    collector = Collector(out, sha)
    if "environment" in phases and not phase_environment(args, manifest, work, collector):
        sys.stderr.write("s42_record: lock canary failed\n")
        return 3
    if phases - {"environment"}:
        warm_grammars(work)
    started = time.monotonic()  # the deadline covers the units, not a cold grammar download
    total = float(manifest["deadlines"]["recorder_total_s"])
    pool = ThreadPoolExecutor(max_workers=workers)
    futures = []
    interrupt_cases = manifest["interruption"]["cases"]
    if "interrupt" in phases:
        futures = [
            pool.submit(run_interrupt_unit, manifest, sha, case, work, collector)
            for case in interrupt_cases
        ]
    independent: threading.Thread | None = None
    keys = None
    wanted = phases & {"isolation", "compat", "capacity"}
    if wanted:
        keys = Keys(manifest["issuer"])
        independent = threading.Thread(
            target=lambda: run_independent_phases(wanted, manifest, work, collector, keys),
            daemon=True,
        )
        independent.start()
    deadline = started + total
    if independent is not None:
        independent.join(max(0.0, deadline - time.monotonic()))
    for future in futures:
        with contextlib.suppress(Exception):
            future.result(timeout=max(0.0, deadline - time.monotonic()))
    expired = time.monotonic() > deadline
    if expired or (independent is not None and independent.is_alive()):
        reap_children()
    wanted_units: list[tuple[str, str]] = []
    for kind, unit in AGG.expected_units(manifest):
        if kind in phases:
            wanted_units.append((kind, unit))
    for kind, unit in wanted_units:
        if not collector.has(kind, unit):
            collector.emit(
                kind, unit, None, failure="case_deadline", diagnostic={"error": "recorder deadline"}
            )
    pool.shutdown(wait=False, cancel_futures=True)
    failed = [k for k, r in collector.records.items() if r["status"] != "complete"]
    for kind, unit in failed:
        code = collector.records[(kind, unit)]["failure"]["code"]
        sys.stderr.write(f"s42_record: unit {kind}.{unit} failed ({code})\n")
    reap_children()
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
