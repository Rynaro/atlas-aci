"""ACI-S39 support: a raw asyncio HTTP client, an SSE parser, the in-loop serving harness,
subprocess helpers and the production driver.

The top level imports only the standard library, so the production driver can run in the
production image (no pytest, `python -I`). atlas, uvicorn, httpx and test_s38_support are
imported lazily. This module defines no test functions.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LOOPBACK = "127.0.0.1"
ACCEPT = "application/json, text/event-stream"
JSON_TYPE = "application/json"
LEGACY_DEFAULT = "2025-03-26"
REVISIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
SELECTED = "2025-11-25"
POLL_S = 5.0
RESPONSE_S = 30.0
EMPTY_SHA = hashlib.sha256(b"").hexdigest()
RESET = (ConnectionError, BrokenPipeError, OSError)
LISTENING = re.compile(r"\bport=(\d+)")
GRAPH = "atlas.graph.v1"
NEIGHBORHOOD_A = {
    "schema_version": GRAPH,
    "op": "neighborhood",
    "seed": {"name": "A"},
    "max_visited_nodes": 1,
    "deadline_ms": 2000,
}


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# ----------------------------------------------------------------------------- SSE and responses
@dataclass
class SseEvent:
    event: str | None = None
    id: str | None = None
    data: str = ""
    retry: str | None = None


def parse_sse(text: str) -> list[SseEvent]:
    """Server-sent events: CRLF, CR or LF line ends; `:` comments ignored; event/id/data/retry."""
    events: list[SseEvent] = []
    current: dict[str, Any] = {}
    data_lines: list[str] = []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for line in lines:
        if line == "":
            if data_lines or current:
                events.append(
                    SseEvent(
                        event=current.get("event"),
                        id=current.get("id"),
                        data="\n".join(data_lines),
                        retry=current.get("retry"),
                    )
                )
            current, data_lines = {}, []
            continue
        if line.startswith(":"):
            continue
        name, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if name == "data":
            data_lines.append(value)
        elif name in {"event", "id", "retry"}:
            current[name] = value
    if data_lines or current:
        events.append(
            SseEvent(
                event=current.get("event"),
                id=current.get("id"),
                data="\n".join(data_lines),
                retry=current.get("retry"),
            )
        )
    return events


@dataclass
class HttpResponse:
    status: int
    reason: str
    headers: list[tuple[str, str]]
    body: bytes

    def header(self, name: str) -> str | None:
        values = [v for k, v in self.headers if k.lower() == name.lower()]
        return values[0] if values else None

    def header_names(self) -> set[str]:
        return {k.lower() for k, _ in self.headers}

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.body)

    def events(self) -> list[SseEvent]:
        return parse_sse(self.text)

    def sse_data(self) -> list[str]:
        return [e.data for e in self.events() if e.data]

    def messages(self) -> list[dict[str, Any]]:
        """JSON-RPC messages carried by an SSE body, or by a JSON body."""
        ctype = (self.header("content-type") or "").lower()
        if ctype.startswith("text/event-stream"):
            return [json.loads(d) for d in self.sse_data()]
        return [self.json()]

    def rpc(self, request_id: int | str | None = None) -> dict[str, Any]:
        """The one JSON-RPC response (result or error), optionally by id."""
        found = [
            m
            for m in self.messages()
            if "result" in m or "error" in m
            if request_id is None or m.get("id") == request_id
        ]
        assert len(found) == 1, f"expected one JSON-RPC response, got {len(found)}"
        return found[0]


def _headers_block(
    method: str,
    path: str,
    host: str,
    headers: list[tuple[str, str]],
    content_length: int | None,
    chunked: bool,
) -> bytes:
    lines = [f"{method} {path} HTTP/1.1"]
    names = {k.lower() for k, _ in headers}
    if "host" not in names:
        lines.append(f"Host: {host}")
    lines += [f"{k}: {v}" for k, v in headers]
    lines.append("Connection: close")
    if chunked:
        lines.append("Transfer-Encoding: chunked")
    elif content_length is not None:
        lines.append(f"Content-Length: {content_length}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")


async def _read_head(reader: asyncio.StreamReader) -> tuple[int, str, list[tuple[str, str]]]:
    raw = await reader.readuntil(b"\r\n\r\n")
    lines = raw.decode("latin-1").split("\r\n")
    _version, status, *reason = lines[0].split(" ", 2)
    headers = []
    for line in lines[1:]:
        if not line:
            continue
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


async def _read_response(reader: asyncio.StreamReader) -> HttpResponse:
    status, reason, headers = await _read_head(reader)
    lowered = {k.lower(): v for k, v in headers}
    if "chunked" in lowered.get("transfer-encoding", "").lower():
        body = await _read_chunked(reader)
    elif "content-length" in lowered:
        body = await reader.readexactly(int(lowered["content-length"]))
    else:
        body = await reader.read()
    return HttpResponse(status, reason, headers, body)


async def close_writer(writer: asyncio.StreamWriter) -> None:
    writer.close()
    with contextlib.suppress(*RESET, asyncio.CancelledError):
        await writer.wait_closed()


class Stream:
    """An open response (headers read, body streaming) for disconnect tests."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        status: int,
        headers: list[tuple[str, str]],
    ) -> None:
        self.reader, self.writer, self.status, self.headers = reader, writer, status, headers
        self.closed = False

    def header(self, name: str) -> str | None:
        values = [v for k, v in self.headers if k.lower() == name.lower()]
        return values[0] if values else None

    async def disconnect(self) -> None:
        if not self.closed:
            self.closed = True
            await close_writer(self.writer)


class RawHttp:
    """HTTP/1.1 over asyncio streams. `Connection: close`; chunked decoding; no SDK."""

    def __init__(self, host: str, port: int, *, timeout: float = RESPONSE_S) -> None:
        self.host, self.port, self.timeout = host, port, timeout

    @property
    def authority(self) -> str:
        return f"{self.host}:{self.port}"

    def _rpc_headers(self, version: str | None, extra: list[tuple[str, str]] | None):
        headers = [("Content-Type", JSON_TYPE), ("Accept", ACCEPT)]
        if version is not None:
            headers.append(("mcp-protocol-version", version))
        return headers + list(extra or [])

    async def request(
        self,
        method: str,
        path: str,
        *,
        headers: list[tuple[str, str]] | None = None,
        body: bytes | None = None,
        chunks: list[bytes] | None = None,
        declared_length: int | None = None,
        withhold_body: bool = False,
        tolerate_reset: bool = False,
        timeout: float | None = None,
    ) -> HttpResponse:
        """One exchange. The response is read concurrently with the body write.

        `withhold_body` sends the headers with a Content-Length and never the body: a response
        that arrives proves the server answered before it read any body. `tolerate_reset`
        accepts a reset or broken pipe on the write side only (the server may close early).
        """
        reader, writer = await asyncio.open_connection(self.host, self.port)
        if chunks is not None:
            head = _headers_block(method, path, self.authority, headers or [], None, True)
        elif body is not None or withhold_body:
            length = declared_length if declared_length is not None else len(body or b"")
            head = _headers_block(method, path, self.authority, headers or [], length, False)
        else:
            head = _headers_block(method, path, self.authority, headers or [], None, False)

        async def send() -> None:
            try:
                writer.write(head)
                await writer.drain()
                if withhold_body:
                    return
                if chunks is not None:
                    for chunk in chunks:
                        writer.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                        await writer.drain()
                    writer.write(b"0\r\n\r\n")
                    await writer.drain()
                elif body:
                    for start in range(0, len(body), 16384):
                        writer.write(body[start : start + 16384])
                        await writer.drain()
            except RESET:
                if not tolerate_reset:
                    raise

        sender = asyncio.ensure_future(send())
        try:
            return await asyncio.wait_for(_read_response(reader), timeout or self.timeout)
        finally:
            if not sender.done():
                sender.cancel()
            with contextlib.suppress(asyncio.CancelledError, *RESET):
                await sender
            await close_writer(writer)

    async def post(
        self,
        message: Any,
        *,
        version: str | None = None,
        headers: list[tuple[str, str]] | None = None,
        raw: bytes | None = None,
        **kwargs: Any,
    ) -> HttpResponse:
        body = raw if raw is not None else json.dumps(message).encode("utf-8")
        return await self.request(
            "POST",
            "/mcp",
            headers=self._rpc_headers(version, headers),
            body=body,
            **kwargs,
        )

    async def rpc(
        self,
        method: str,
        params: Any = None,
        *,
        request_id: int = 1,
        version: str | None = None,
        **kwargs: Any,
    ) -> HttpResponse:
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        return await self.post(message, version=version, **kwargs)

    async def notify(
        self, method: str, params: Any = None, *, version: str | None = None
    ) -> HttpResponse:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        return await self.post(message, version=version)

    async def tool(
        self,
        name: str,
        arguments: Any,
        *,
        request_id: int = 1,
        version: str | None = SELECTED,
        **kwargs: Any,
    ) -> HttpResponse:
        return await self.rpc(
            "tools/call",
            {"name": name, "arguments": arguments},
            request_id=request_id,
            version=version,
            **kwargs,
        )

    async def open_stream(
        self,
        message: Any,
        *,
        version: str | None = SELECTED,
        headers: list[tuple[str, str]] | None = None,
    ) -> Stream:
        """POST in full, then return once the response headers have arrived."""
        reader, writer = await asyncio.open_connection(self.host, self.port)
        body = json.dumps(message).encode("utf-8")
        head = _headers_block(
            "POST", "/mcp", self.authority, self._rpc_headers(version, headers), len(body), False
        )
        writer.write(head + body)
        await writer.drain()
        status, _reason, response_headers = await asyncio.wait_for(_read_head(reader), self.timeout)
        return Stream(reader, writer, status, response_headers)

    async def initialize(self, version: str = SELECTED) -> dict[str, Any]:
        params = {
            "protocolVersion": version,
            "capabilities": {},
            "clientInfo": {"name": "atlas-s39-fixture-host", "version": "1"},
        }
        response = await self.rpc("initialize", params, request_id=1)
        assert response.status == 200, response.text
        return response.rpc(1)


def tool_message(
    name: str, arguments: Any, request_id: int = 1, method: str = "tools/call"
) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": {"name": name, "arguments": arguments},
    }


# ----------------------------------------------------------------------------- polling
async def poll(
    predicate: Callable[[], Any], timeout: float = POLL_S, interval: float = 0.02
) -> bool:
    """Bounded polling: True as soon as `predicate()` is truthy, else False after `timeout`."""
    end = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= end:
            return False
        await asyncio.sleep(interval)


# ----------------------------------------------------------------------------- in-loop serving
class TraversalHook:
    """The H2 gate: replaces `traversal.asyncio` by a namespace holding only `sleep`."""

    def __init__(self) -> None:
        self.real_sleep = asyncio.sleep
        self.release = asyncio.Event()
        self.inside = 0
        self.max_inside = 0
        self.entered = 0
        self.cancelled = 0
        self.passthrough = False
        self._seen: set[asyncio.Task[Any]] = set()

    async def sleep(self, delay: float) -> None:
        task = asyncio.current_task()
        if self.passthrough or task is None or task in self._seen:
            await self.real_sleep(delay)
            return
        self._seen.add(task)
        self.entered += 1
        self.inside += 1
        self.max_inside = max(self.max_inside, self.inside)
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        finally:
            self.inside -= 1
        await self.real_sleep(delay)

    def install(self, monkeypatch: Any) -> TraversalHook:
        from types import SimpleNamespace

        from atlas_aci.query import traversal

        monkeypatch.setattr(traversal, "asyncio", SimpleNamespace(sleep=self.sleep))
        return self


@dataclass
class LeaseObserver:
    """The H3 observer: a class-level wrapper of `CodeGraph.reader_lease` (S12 precedent)."""

    readers: list[Any] = field(default_factory=list)
    conns: list[Any] = field(default_factory=list)
    sources: list[Any] = field(default_factory=list)
    open_leases: int = 0
    max_open_leases: int = 0
    total: int = 0

    def install(self, monkeypatch: Any) -> LeaseObserver:
        from atlas_aci.codegraph import CodeGraph

        original = CodeGraph.reader_lease
        observer = self

        @contextlib.contextmanager
        def wrapper(code_graph: Any, *args: Any, **kwargs: Any):
            counted = False
            try:
                with original(code_graph, *args, **kwargs) as reader:
                    observer.readers.append(reader)
                    observer.conns.append(reader.db)
                    observer.sources.append(reader._source_lease)
                    observer.total += 1
                    observer.open_leases += 1
                    observer.max_open_leases = max(observer.max_open_leases, observer.open_leases)
                    counted = True
                    yield reader
            finally:
                if counted:
                    observer.open_leases -= 1

        monkeypatch.setattr(CodeGraph, "reader_lease", wrapper)
        return self


@contextlib.asynccontextmanager
async def serving(app: Any) -> AsyncIterator[tuple[str, int]]:
    """Run uvicorn in the test loop on 127.0.0.1:0 with a real bind; stop via `should_exit`."""
    import sse_starlette.sse as sse

    from atlas_aci.protocol import http

    assert sse.AppStatus.should_exit is False, "stale process-global SSE shutdown flag"
    server = http.AtlasUvicornServer(http.uvicorn_config(app, LOOPBACK, 0), install_signals=False)
    problems: list[str] = []
    async with app.running():
        task = asyncio.ensure_future(http._serve(server))
        try:
            started = await poll(lambda: server.started or task.done(), 10.0, 0.01)
            if task.done():
                task.result()
            assert started and server.started, "uvicorn did not start"
            port = server.servers[0].sockets[0].getsockname()[1]
            yield LOOPBACK, port
        finally:
            idle = await poll(lambda: app.gate.active == 0, POLL_S)
            if not idle:
                problems.append(f"in-flight slots leaked: {app.gate.snapshot()}")
            server.should_exit = True
            try:
                await asyncio.wait_for(asyncio.shield(task), 15)
            except BaseException:
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
                raise
            sse.AppStatus.should_exit = False
    assert not problems, problems


def make_config(paths: Any, **overrides: Any) -> Any:
    from atlas_aci.config import Config

    values: dict[str, Any] = {
        "repo": paths.repo,
        "memex_root": paths.memex,
        "graph_root": paths.graphs,
        "max_calls_per_minute": 0,
    }
    values.update(overrides)
    return Config(**values)


def make_app(paths: Any, **overrides: Any) -> Any:
    from atlas_aci.protocol.http import build_app

    return build_app(make_config(paths, **overrides))


# ----------------------------------------------------------------------------- subprocess servers
def http_argv(paths: Any, *extra: str, python: str | None = None) -> list[str]:
    base = [
        python or sys.executable,
        "-m",
        "atlas_aci",
        "serve",
        "--repo",
        str(paths.repo),
        "--graph-root",
        str(paths.graphs),
        "--memex-root",
        str(paths.memex),
        "--transport",
        "http",
        "--port",
        "0",
    ]
    return base + list(extra)


class HttpChild:
    """A `serve --transport http` subprocess; stderr is captured and the port parsed from it."""

    def __init__(
        self,
        argv: list[str],
        env: dict[str, str] | None = None,
        *,
        startup_s: float = 60.0,
    ) -> None:
        self.argv = argv
        self.proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        self.stderr = bytearray()
        self.stdout = bytearray()
        self.port: int | None = None
        self._ready = threading.Event()
        self._threads = [
            threading.Thread(target=self._pump, args=(self.proc.stderr, self.stderr, True)),
            threading.Thread(target=self._pump, args=(self.proc.stdout, self.stdout, False)),
        ]
        for thread in self._threads:
            thread.daemon = True
            thread.start()
        deadline = time.monotonic() + startup_s
        while time.monotonic() < deadline and not self._ready.is_set():
            if self.proc.poll() is not None:
                break
            self._ready.wait(0.05)
        if self.port is None:
            code = self.proc.poll()
            self.kill()
            raise AssertionError(
                f"no http_listening line (exit={code}): {self.stderr_text()[-600:]}"
            )

    def _pump(self, stream: Any, sink: bytearray, parse: bool) -> None:
        while chunk := stream.read1(65536):
            if len(sink) < 1024 * 1024:
                sink.extend(chunk)
            if parse and self.port is None:
                for line in bytes(sink).decode("utf-8", errors="replace").splitlines():
                    if "http_listening" in line and (match := LISTENING.search(line)):
                        self.port = int(match.group(1))
                        self._ready.set()
                        break

    def stderr_text(self) -> str:
        return bytes(self.stderr).decode("utf-8", errors="replace")

    def client(self) -> RawHttp:
        assert self.port is not None
        return RawHttp(LOOPBACK, self.port)

    def wait(self, timeout: float) -> int | None:
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    def kill(self) -> None:
        with contextlib.suppress(ProcessLookupError):
            self.proc.kill()
        with contextlib.suppress(Exception):
            self.proc.wait(timeout=10)
        for thread in self._threads:
            thread.join(timeout=5)
        for stream in (self.proc.stdout, self.proc.stderr):
            if stream is not None:
                with contextlib.suppress(Exception):
                    stream.close()

    def close(self) -> int | None:
        """SIGTERM, wait up to 10 s, then kill. Returns the exit code (None if killed)."""
        code: int | None = None
        if self.proc.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                self.proc.send_signal(signal.SIGTERM)
            code = self.wait(10)
        else:
            code = self.proc.returncode
        self.kill()
        return code


def run_cli(
    argv: list[str], *, env: dict[str, str] | None = None, timeout: float = 60
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(argv, capture_output=True, env=env, timeout=timeout)


def connection_refused(port: int, host: str = LOOPBACK) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(3)
        try:
            probe.connect((host, port))
        except ConnectionRefusedError:
            return True
        except OSError:
            return False
        return False


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def listen_rows(port: int, family: str) -> list[str]:
    """`/proc/net/tcp` (family 'tcp') or tcp6 LISTEN rows (state 0A) for a local port."""
    rows = []
    for line in Path(f"/proc/net/{family}").read_text().splitlines()[1:]:
        fields = line.split()
        local, state = fields[1], fields[3]
        if state == "0A" and int(local.rsplit(":", 1)[1], 16) == port:
            rows.append(local)
    return rows


def expected_loopback_row(port: int) -> str:
    return f"0100007F:{port:04X}"


# ----------------------------------------------------------------------------- fake binaries
def write_fake(directory: Path, name: str, body: str) -> Path:
    path = directory / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return path


FAKE_HANG = 'echo $$ > "$ATLAS_FAKE_PIDFILE"\nexec sleep 60\n'


# ----------------------------------------------------------------------------- shared fixtures
def build_labs(root: Path) -> Any:
    """Two indexed repositories: `mini` (the S38 conversation) and `live` (large enough to page)."""
    from types import SimpleNamespace

    import test_s38_support as s38

    return SimpleNamespace(
        mini=s38.build_fixture(root / "mini"),
        live=s38.build_fixture(root / "live", source=s38.LIVE_SOURCE),
    )


def asgi_scope(
    *,
    method: str = "POST",
    path: str = "/mcp",
    headers: list[tuple[str, str]] | None = None,
    client: tuple[str, int] | None = ("127.0.0.1", 40000),
    server: tuple[str, int] | None = (LOOPBACK, 5555),
) -> dict[str, Any]:
    """A bare ASGI http scope for direct wrapper calls (no network)."""
    scope: dict[str, Any] = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [(k.lower().encode(), v.encode("latin-1")) for k, v in (headers or [])],
    }
    if client is not None:
        scope["client"] = client
    if server is not None:
        scope["server"] = server
    return scope


async def asgi_call(app: Any, scope: dict[str, Any]) -> tuple[int, dict[str, str], bytes, int]:
    """Call the wrapper directly. Returns (status, headers, body, receive_calls)."""
    sent: list[dict[str, Any]] = []
    receives = 0

    async def receive() -> dict[str, Any]:
        nonlocal receives
        receives += 1
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(scope, receive, send)
    start = sent[0]
    headers = {k.decode(): v.decode() for k, v in start["headers"]}
    return start["status"], headers, b"".join(m.get("body", b"") for m in sent[1:]), receives


async def finish_stream(stream: Stream, timeout: float = RESPONSE_S) -> HttpResponse:
    """Read the rest of an open response body (chunked or until EOF)."""
    chunked = "chunked" in (stream.header("transfer-encoding") or "").lower()
    reader = stream.reader
    body = await asyncio.wait_for(_read_chunked(reader) if chunked else reader.read(), timeout)
    return HttpResponse(stream.status, "", stream.headers, body)


# ----------------------------------------------------------------------------- production driver
class _Report:
    """One PASS/FAIL line per check; a check whose prerequisite failed FAILs too."""

    def __init__(self) -> None:
        self.ok: dict[str, bool] = {}
        self.failed = 0
        self.passed = 0

    def record(self, name: str, ok: bool, detail: str) -> None:
        self.ok[name] = ok
        self.passed += ok
        self.failed += not ok
        print(("PASS " if ok else "FAIL ") + f"{name} {detail}", flush=True)

    async def run(self, name: str, requires: list[str], check: Any) -> None:
        missing = [r for r in requires if not self.ok.get(r)]
        if missing:
            self.record(name, False, f"prerequisite failed: {','.join(missing)}")
            return
        try:
            detail = await check()
        except BaseException as exc:
            self.record(name, False, f"{type(exc).__name__}: {str(exc)[:300]}")
            return
        self.record(name, True, detail)


def _flag(argv: list[str], name: str, default: str | None = None) -> str:
    if name in argv:
        return argv[argv.index(name) + 1]
    if default is None:
        raise SystemExit(f"missing {name}")
    return default


def _tool_view(body: dict[str, Any]) -> dict[str, Any]:
    result = body["result"]
    return {
        "text": result["content"][0]["text"],
        "isError": result["isError"],
        "structuredContent": result.get("structuredContent"),
    }


async def _e1(s38: Any, repo: Path, graphs: Path, memex: Path, client: RawHttp) -> str:
    """stdio versus HTTP: steps 1-16 (and the -32601 of step 17) at two revisions."""
    total = 0
    for revision, case in (
        (SELECTED, "modern.2025-11-25"),
        ("2024-11-05", "legacy.2024-11-05"),
    ):
        steps = s38.steps_for(case)
        paths = s38.Paths(repo, memex / f"stdio-{revision}", graphs)
        paths.memex.mkdir(parents=True, exist_ok=True)
        stdio_client = s38.RawLineClient(paths.serve_argv(), env=s38.child_env(0), deadline_s=120)
        try:
            exchanges = s38.run_steps(stdio_client, steps)
        finally:
            stdio_client.close()
        stdio = {x.step: json.loads(x.raw) for x in exchanges if x.raw is not None}
        over_http: dict[int, dict[str, Any]] = {}
        for step, _name, method, kind, params in steps:
            if kind == "notification":
                note = await client.notify(method, params, version=revision)
                assert note.status == 202, f"notification {step}: {note.status}"
                continue
            version = None if method == "initialize" else revision
            response = await client.rpc(method, params, request_id=step, version=version)
            assert response.status == 200, f"step {step}: {response.status}"
            over_http[step] = response.rpc(step)
        assert set(stdio) == set(over_http) and len(stdio) == 16, "step sets differ"
        for key in ("protocolVersion", "serverInfo", "capabilities"):
            assert over_http[1]["result"][key] == stdio[1]["result"][key], key
        assert over_http[1]["result"]["protocolVersion"] == revision
        digest = s38.tools_digest(over_http[4]["result"])["tools_digest"]
        assert digest == s38.tools_digest(stdio[4]["result"])["tools_digest"]
        assert digest["sha256"] != EMPTY_SHA and len(digest["names"]) == 7
        for step in (3, 5, 6):
            assert over_http[step]["result"] == stdio[step]["result"], step
        total += 4
        for step in s38.TOOL_STEPS:
            mine, theirs = _tool_view(over_http[step]), _tool_view(stdio[step])
            assert theirs["text"] and mine == theirs, f"tool step {step} differs"
            assert mine["isError"] == s38.EXPECTED_IS_ERROR[step], step
            total += 1
        assert over_http[17]["error"] == stdio[17]["error"]
        assert stdio[17]["error"]["code"] == -32601
        total += 1
    assert total >= 20
    return f"{total} comparisons equal at 2 revisions"


async def _rejection(
    client: RawHttp, headers: list[tuple[str, str]], status: int, reason: str
) -> HttpResponse:
    response = await client.request(
        "POST",
        "/mcp",
        headers=[*headers, *client._rpc_headers(SELECTED, None)],
        declared_length=64,
        withhold_body=True,
    )
    assert response.status == status, f"status {response.status}"
    body = response.json()
    assert body["error"]["data"] == {"reason": reason}, body
    return response


async def _e3(client: RawHttp, port: int) -> str:
    """Two slow-body POSTs hold both slots; a third is refused; finishing frees them."""
    message = json.dumps(tool_message("graph_query", {"schema_version": GRAPH, "op": "status"}))
    body = message.encode()
    half = len(body) // 2
    assert half > 0 and body[half:]
    held = []
    for _ in range(2):
        reader, writer = await asyncio.open_connection(LOOPBACK, port)
        head = _headers_block(
            "POST", "/mcp", client.authority, client._rpc_headers(SELECTED, None), len(body), False
        )
        writer.write(head + body[:half])
        await writer.drain()
        held.append((reader, writer))
    await asyncio.sleep(0.8)
    third = await client.post(json.loads(message), version=SELECTED)
    assert third.status == 503 and third.header("retry-after") == "1", third.status
    assert third.json()["error"]["data"] == {"reason": "inflight_limit"}
    done = []
    for reader, writer in held:
        writer.write(body[half:])
        await writer.drain()
        response = await asyncio.wait_for(_read_response(reader), 30)
        assert response.status == 200, response.status
        assert response.rpc(1)["result"]["isError"] is False
        done.append(response.status)
        await close_writer(writer)
    fourth = await client.post(json.loads(message), version=SELECTED)
    assert fourth.status == 200 and fourth.rpc(1)["result"]["isError"] is False
    return f"third=503 Retry-After 1; finished={done}; fourth=200"


async def _production(repo: Path, graphs: Path, memex: Path, port: int, report: _Report) -> None:
    import test_s38_support as s38

    memex.mkdir(parents=True, exist_ok=True)
    http_memex = memex / "http"
    http_memex.mkdir(parents=True, exist_ok=True)
    argv = [
        sys.executable,
        "-m",
        "atlas_aci",
        "serve",
        "--repo",
        str(repo),
        "--graph-root",
        str(graphs),
        "--memex-root",
        str(http_memex),
        "--transport",
        "http",
        "--host",
        LOOPBACK,
        "--port",
        str(port),
        "--max-inflight",
        "2",
        "--max-body-bytes",
        "65536",
    ]
    state: dict[str, Any] = {}

    async def start() -> str:
        state["child"] = HttpChild(argv, env=s38.child_env(0))
        child = state["child"]
        assert child.port == port, f"listening on {child.port}, wanted {port}"
        client = child.client()
        ping = await client.rpc("ping", version=SELECTED)
        assert ping.status == 200 and ping.rpc(1)["result"] == {}
        state["client"] = client
        return f"http_listening on 127.0.0.1:{port}; ping 200"

    await report.run("E0 http child serves a ping", [], start)
    if not report.ok.get("E0 http child serves a ping"):
        child = state.get("child")
        if child is not None:
            child.kill()
        return
    child = state["child"]
    client = state["client"]
    ready = ["E0 http child serves a ping"]

    async def e1() -> str:
        return await _e1(s38, repo, graphs, memex, client)

    await report.run("E1 stdio and http answers are equal", ready, e1)

    async def e2() -> str:
        await _rejection(client, [("Origin", "http://evil.example")], 403, "origin_not_allowed")
        ok = await client.tool("graph_query", {"schema_version": GRAPH, "op": "status"})
        assert ok.status == 200 and ok.rpc(1)["result"]["isError"] is False
        return "evil Origin 403 before the body; a valid call 200"

    await report.run("E2 invalid Origin refused before the body", ready, e2)
    await report.run("E3 in-flight bound", ready, lambda: _e3(client, port))

    async def e4() -> str:
        big = tool_message("view_file", {"path": "a.py", "pad": "x" * 70000})
        declared = await client.post(big, version=SELECTED, tolerate_reset=True)
        assert declared.status == 413, declared.status
        chunked = await client.request(
            "POST",
            "/mcp",
            headers=client._rpc_headers(SELECTED, None),
            chunks=[b"x" * 40000, b"y" * 40000],
            tolerate_reset=True,
        )
        assert chunked.status == 413, chunked.status
        return "declared 65537+ and chunked overrun both 413"

    await report.run("E4 body cap", ready, e4)

    async def e5() -> str:
        for method in ("GET", "DELETE"):
            response = await client.request(method, "/mcp")
            assert response.status == 405 and response.header("allow") == "POST", method
        missing = await client.request("GET", "/x")
        assert missing.status == 404
        return "GET/DELETE /mcp 405 Allow POST; GET /x 404"

    await report.run("E5 method and path", ready, e5)

    async def e6() -> str:
        await _rejection(client, [("Host", f"attacker.example:{port}")], 421, "host_not_allowed")
        return "foreign Host 421"

    await report.run("E6 host", ready, e6)

    async def e8() -> str:
        done = subprocess.run(
            [
                sys.executable,
                "-m",
                "atlas_aci",
                "serve",
                "--repo",
                str(repo),
                "--transport",
                "http",
                "--host",
                "0.0.0.0",
            ],
            capture_output=True,
            env=s38.child_env(None),
            timeout=60,
        )
        assert done.returncode == 2 and done.stdout == b"", done.returncode
        assert b"Usage:" in done.stderr and b"wildcard_address" in done.stderr
        return "in-container --host 0.0.0.0 exit 2"

    await report.run("E8 non-loopback host refused", [], e8)

    async def e9() -> str:
        tcp = listen_rows(port, "tcp")
        tcp6 = listen_rows(port, "tcp6")
        assert tcp == [expected_loopback_row(port)], tcp
        assert tcp6 == [], tcp6
        return f"/proc/net/tcp LISTEN {tcp}; tcp6 {tcp6}"

    await report.run("E9 listening socket is loopback only", ready, e9)

    async def e7() -> str:
        started = time.monotonic()
        child.proc.send_signal(signal.SIGTERM)
        code = child.wait(10)
        elapsed = time.monotonic() - started
        child.kill()
        err = child.stderr_text()
        assert code == 0, f"exit {code}"
        assert "http_stopped" in err and "Traceback" not in err
        assert connection_refused(port)
        return f"SIGTERM exit 0 in {elapsed:.1f}s; http_stopped; connection refused"

    await report.run("E7 SIGTERM stops cleanly", ready, e7)
    child.kill()


def production_main(argv: list[str]) -> int:
    """Run E0-E9 against `atlas-aci serve --transport http` inside the production image."""
    repo = Path(_flag(argv, "--repo"))
    graphs = Path(_flag(argv, "--graph-root"))
    memex = Path(_flag(argv, "--memex-root"))
    port = int(_flag(argv, "--port", "18765"))
    report = _Report()
    asyncio.run(_production(repo, graphs, memex, port, report))
    print(f"DRIVER: {report.passed} passed, {report.failed} failed", flush=True)
    return 0 if report.failed == 0 and report.passed >= 9 else 1
