"""ACI-S38 fixture hosts, the documented conversation, the normaliser and the capture drivers.

Stdlib only at module level: the same file runs in a base worktree, in the dev image and inside
the production image (``production_main``), where no test dependency is installed. ``atlas_aci`` is
imported lazily inside the functions that need it. The file defines no pytest test function.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BASE_COMMIT = "2db6dd1a3cba6589bf4efc9ad83620e39bacb289"
TRANSCRIPT_FORMAT = "atlas-s38-transcript.v1"
FIXTURE_NAME = "s38-mini.v1"
FIXTURE_SOURCE = b"def target():\n return 1\ndef caller():\n target()\n"
FIXTURE_MTIME = 1_700_000_000
LIVE_SOURCE = b"def A():\n " + b"B(); " * 20 + b"\ndef B():\n pass\n"
RECORDED_CAPABILITIES = {
    "experimental": {},
    "resources": {"listChanged": False, "subscribe": False},
    "tools": {"listChanged": False},
}
KNOWN_DEVIATIONS: frozenset[tuple[str, int, str, str, str]] = frozenset()
GRAPH = "atlas.graph.v1"
LEGACY_REVISIONS = ("2024-11-05", "2025-03-26")
MODERN_REVISIONS = ("2025-06-18", "2025-11-25")
ALL_REVISIONS = LEGACY_REVISIONS + MODERN_REVISIONS
SELECTED = "2025-11-25"
STDERR_CAP = 1024 * 1024
RESPONSE_TIMEOUT_S = 30
CLOSE_TIMEOUT_S = 10
MISSING = object()

SERVER = """import asyncio,sys,structlog
from pathlib import Path
from atlas_aci.config import Config
from atlas_aci.server import run_stdio
structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
asyncio.run(run_stdio(Config(repo=Path(sys.argv[1]),memex_root=Path(sys.argv[2]))))
"""
# D9 only: the dependency's data (its supported list) is extended; Atlas code is unchanged.
SERVER_APPENDED = (
    "import mcp.shared.version as _v\n_v.SUPPORTED_PROTOCOL_VERSIONS.append('2026-06-30')\n"
    + SERVER
)

# The documented conversation (D6). The hosts document renders this table.
CONVERSATION: tuple[tuple[int, str, Any], ...] = (
    (1, "initialize", None),
    (2, "notifications/initialized", None),
    (3, "ping", None),
    (4, "tools/list", None),
    (5, "resources/list", None),
    (6, "resources/templates/list", None),
    (7, "graph_query", {"query": "definitions_of:target"}),
    (8, "graph_query", {"query": "callers_of:target"}),
    (9, "search_symbol", {"name": "target"}),
    (10, "view_file", {"path": "a.py", "start_line": 1, "end_line": 4}),
    (11, "graph_query", {"schema_version": "atlas.graph.v1", "op": "status"}),
    (
        12,
        "graph_query",
        {
            "schema_version": "atlas.graph.v1",
            "op": "neighborhood",
            "seed": {"name": "target"},
            "max_depth": 1,
            "deadline_ms": 2000,
        },
    ),
    (
        13,
        "graph_query",
        {
            "schema_version": "atlas.graph.v1",
            "op": "path",
            "seed": {"name": "caller"},
            "target": {"name": "target"},
            "deadline_ms": 2000,
        },
    ),
    (14, "graph_query", {"query": "not-a-query"}),
    (15, "graph_query", {"query": False}),
    (16, "write_file", {"path": "a.py", "content": "x"}),
    (17, "logging/setLevel", {"level": "info"}),
)
TOOL_STEPS = tuple(row[0] for row in CONVERSATION if row[0] >= 7 and row[0] <= 16)
# Recorded at Day-1 (c): steps 7-13 succeed, 14-16 are rejected as tool errors.
EXPECTED_IS_ERROR = {7: False, 8: False, 9: False, 10: False, 11: False, 12: False, 13: False}
EXPECTED_IS_ERROR.update({14: True, 15: True, 16: True})
TOOL_NAMES = {
    "view_file",
    "list_dir",
    "search_text",
    "search_symbol",
    "graph_query",
    "test_dry_run",
    "memex_read",
}

CASES: dict[str, tuple[str, Any]] = {
    "legacy.2024-11-05": ("lifecycle.v1", "2024-11-05"),
    "legacy.2025-03-26": ("lifecycle.v1", "2025-03-26"),
    "modern.2025-06-18": ("lifecycle.v1", "2025-06-18"),
    "modern.2025-11-25": ("lifecycle.v1", "2025-11-25"),
    "unsupported.2099-01-01": ("negotiation.v1", "2099-01-01"),
    "unsupported.integer": ("negotiation.v1", 20241105),
    "malformed.missing-version": ("negotiation.v1", MISSING),
    "malformed.pre-initialize": ("negotiation.v1", None),
}
LIFECYCLE_CASES = tuple(name for name, (conv, _) in CASES.items() if conv == "lifecycle.v1")

NORMALISATION_RULES = (
    ("N1", "response id becomes the ordinal of first appearance among server frames"),
    ("N2", "fixture repo, memex and graph-root paths become <repo>, <memex>, <graphs>"),
    ("N3", "content text that parses as JSON becomes {$json, sha256 of the post-N4/N5 text}"),
    ("N4", "64-hex runs in string values become <h64:N> by first appearance"),
    ("N5", "values under lease_id, cursor and next_cursor become <lease:N> and <cursor:N>"),
    ("N6", "serverInfo.version is kept verbatim"),
    ("N7", "the tools/list result becomes a digest object"),
    ("N8", "server notifications are collected separately; stderr is excluded"),
    ("N9", "canonical JSON plus one LF"),
)


def canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(data: bytes | str) -> str:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha256(raw).hexdigest()


def render_conversation_table() -> list[str]:
    """The hosts document must contain exactly these lines (checked by test_s38_isolation)."""
    lines = ["| step | method | arguments |", "| --- | --- | --- |"]
    for step, name, arguments in CONVERSATION:
        shown = (
            "revision-specific"
            if step == 1
            else ("none" if arguments is None else canon(arguments))
        )
        method = "tools/call " + name if step in TOOL_STEPS else name
        lines.append(
            f"| {step} | {method} | `{shown}` |"
            if shown != "none"
            else f"| {step} | {method} | none |"
        )
    return lines


def initialize_params(requested: Any) -> dict[str, Any]:
    params: dict[str, Any] = {
        "capabilities": {},
        "clientInfo": {"name": "atlas-s38-fixture-host", "version": "1"},
    }
    if requested is not MISSING:
        params["protocolVersion"] = requested
    return params


# ----------------------------------------------------------------------------- raw line client
class ProtocolError(AssertionError):
    pass


class RawLineClient:
    """An independent JSON-RPC line client over a real server subprocess (no SDK)."""

    def __init__(
        self,
        argv: list[str],
        env: dict[str, str] | None = None,
        deadline_s: float = 120,
        cwd: str | None = None,
    ) -> None:
        self.argv = argv
        self.deadline = time.monotonic() + deadline_s
        self.proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=cwd,
        )
        self.frames: list[bytes] = []
        self.notifications: list[dict[str, Any]] = []
        self.stderr = bytearray()
        self.stderr_discarded = 0
        self.close_record: dict[str, Any] | None = None
        self.close_timeout: float = CLOSE_TIMEOUT_S
        self._next_id = 1
        self._lines: queue.Queue[bytes | None] = queue.Queue()
        self._threads = [
            threading.Thread(target=self._read_stdout, daemon=True),
            threading.Thread(target=self._read_stderr, daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def _read_stdout(self) -> None:
        assert self.proc.stdout is not None
        try:
            while line := self.proc.stdout.readline():
                self._lines.put(line)
        finally:
            self._lines.put(None)

    def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        while chunk := self.proc.stderr.read1(65536):  # type: ignore[attr-defined]
            room = STDERR_CAP - len(self.stderr)
            if room > 0:
                self.stderr.extend(chunk[:room])
            self.stderr_discarded += max(0, len(chunk) - max(room, 0))

    def send_raw(self, data: bytes) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def _send(self, message: dict[str, Any]) -> None:
        self.send_raw(json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n")

    def notify(self, method: str, params: Any = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)

    def next_frame(self, timeout: float = RESPONSE_TIMEOUT_S) -> bytes | None:
        """The next stdout line; raises on a non-JSON line or an expired deadline."""
        limit = min(timeout, max(0.001, self.deadline - time.monotonic()))
        try:
            line = self._lines.get(timeout=limit)
        except queue.Empty:
            raise ProtocolError("timed out waiting for a server frame") from None
        if line is None:
            return None
        self.frames.append(line)
        try:
            json.loads(line)
        except ValueError:
            raise ProtocolError(f"non-JSON stdout line: {line[:80]!r}") from None
        return line

    def request(
        self, method: str, params: Any = MISSING, timeout: float = RESPONSE_TIMEOUT_S
    ) -> bytes:
        """Send one request and return the raw response line for its id."""
        request_id = self._next_id
        self._next_id += 1
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not MISSING and params is not None:
            message["params"] = params
        self._send(message)
        return self.await_response(request_id, timeout)

    def await_response(self, request_id: int, timeout: float = RESPONSE_TIMEOUT_S) -> bytes:
        end = time.monotonic() + timeout
        while True:
            line = self.next_frame(max(0.001, end - time.monotonic()))
            if line is None:
                raise ProtocolError("server closed stdout before responding")
            body = json.loads(line)
            if (
                isinstance(body, dict)
                and body.get("id") == request_id
                and ("result" in body or "error" in body)
            ):
                return line
            if isinstance(body, dict) and "method" in body:
                self.notifications.append(body)
                continue
            raise ProtocolError(f"unexpected frame: {line[:80]!r}")

    def close(self) -> dict[str, Any]:
        if self.close_record is not None:
            return self.close_record
        killed = False
        try:
            if self.proc.stdin is not None:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            code = self.proc.wait(timeout=self.close_timeout)
        except subprocess.TimeoutExpired:
            killed = True
            self.proc.kill()
            code = self.proc.wait(timeout=CLOSE_TIMEOUT_S)
        for thread in self._threads:
            thread.join(timeout=CLOSE_TIMEOUT_S)
        for stream in (self.proc.stdout, self.proc.stderr):
            if stream is not None:
                stream.close()
        self.close_record = {"exit_code": code, "killed": killed}
        return self.close_record

    def stderr_text(self) -> str:
        return bytes(self.stderr).decode("utf-8", errors="replace")


def child_env(seed: int | None = 0, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("ATLAS_ACI_")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if seed is not None:
        env["PYTHONHASHSEED"] = str(seed)
    env.update(extra or {})
    return env


# ----------------------------------------------------------------------------- fixture repo
@dataclass(frozen=True)
class Paths:
    repo: Path
    memex: Path
    graphs: Path
    api: bool = False  # Day-1 (c2) only: the in-process Config path instead of the CLI path

    def serve_argv(self, python: str = sys.executable) -> list[str]:
        if self.api:
            return [python, "-c", SERVER, str(self.repo), str(self.memex)]
        return [
            python,
            "-m",
            "atlas_aci",
            "serve",
            "--repo",
            str(self.repo),
            "--graph-root",
            str(self.graphs),
            "--memex-root",
            str(self.memex),
        ]


def build_fixture(
    root: Path,
    python: str = sys.executable,
    env: dict[str, str] | None = None,
    api: bool = False,
    source: bytes = FIXTURE_SOURCE,
) -> Paths:
    """Write the s38-mini.v1 repo and index it through the production CLI path."""
    repo, memex, graphs = root / "repo", root / "memex", root / "graphs"
    if api:
        graphs = repo / ".atlas"
    for path in (repo, memex, graphs):
        path.mkdir(parents=True, exist_ok=True)
    (repo / "a.py").write_bytes(source)
    os.utime(repo / "a.py", (FIXTURE_MTIME, FIXTURE_MTIME))  # list_dir reports the mtime
    if api:
        from atlas_aci.codegraph import CodeGraph

        graph = CodeGraph(repo)
        graph.build()
        graph.close()
        return Paths(repo, memex, graphs, api=True)
    done = subprocess.run(
        [python, "-m", "atlas_aci", "index", "--repo", str(repo), "--graph-root", str(graphs)],
        capture_output=True,
        env=env or child_env(None),
        timeout=120,
    )
    if done.returncode != 0:
        raise ProtocolError(f"index failed: {done.stderr[-400:]!r}")
    return Paths(repo, memex, graphs)


def fresh_memex(paths: Paths, tag: str) -> Paths:
    """A per-process memex directory (servers run sequentially over one graph root)."""
    memex = paths.memex.parent / f"memex-{tag}"
    memex.mkdir(parents=True, exist_ok=True)
    return Paths(paths.repo, memex, paths.graphs, paths.api)


# ----------------------------------------------------------------------------- normaliser
def _escaped_forms(text: str) -> list[str]:
    once = json.dumps(text)[1:-1]
    twice = json.dumps(once)[1:-1]
    return [text, once, twice]


def n2_substitutions(paths: Paths) -> list[tuple[re.Pattern[str], str]]:
    pairs: list[tuple[str, str]] = []
    for token, path in (
        ("<repo>", paths.repo),
        ("<memex>", paths.memex),
        ("<graphs>", paths.graphs),
    ):
        forms = {str(path)}
        with contextlib.suppress(OSError):
            forms.add(str(path.resolve()))
        for form in forms:
            for variant in _escaped_forms(form):
                pairs.append((variant, token))
    pairs = sorted(set(pairs), key=lambda row: (-len(row[0]), row[0]))
    return [(re.compile(re.escape(needle) + r"(?![A-Za-z0-9_])"), token) for needle, token in pairs]


def apply_n2(text: str, subs: list[tuple[re.Pattern[str], str]]) -> str:
    for pattern, token in subs:
        text = pattern.sub(token, text)
    return text


H64 = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")
N5_KEYS = {"lease_id": "lease", "cursor": "cursor", "next_cursor": "cursor"}
N5_TEXT = re.compile(r'("(lease_id|cursor|next_cursor)"\s*:\s*")((?:[^"\\]|\\.)*)(")')


class Normaliser:
    """N1, N3, N4, N5, N7 over one transcript. N2 is applied to the raw frame text first."""

    def __init__(self) -> None:
        self.hashes: dict[str, int] = {}
        self.tokens: dict[str, dict[str, int]] = {"lease": {}, "cursor": {}}

    def n4(self, text: str) -> str:
        def repl(match: re.Match[str]) -> str:
            value = match.group(0)
            return f"<h64:{self.hashes.setdefault(value, len(self.hashes) + 1)}>"

        return H64.sub(repl, text)

    def _token(self, kind: str, value: str) -> str:
        table = self.tokens[kind]
        return f"<{kind}:{table.setdefault(value, len(table) + 1)}>"

    def n5_text(self, text: str) -> str:
        def repl(match: re.Match[str]) -> str:
            token = self._token(N5_KEYS[match.group(2)], match.group(3))
            return match.group(1) + token + match.group(4)

        return N5_TEXT.sub(repl, text)

    def value(self, node: Any, key: str | None = None) -> Any:
        if isinstance(node, dict):
            return {k: self.value(node[k], k) for k in sorted(node)}
        if isinstance(node, list):
            return [self.value(item) for item in node]
        if isinstance(node, str):
            if key in N5_KEYS:
                return self._token(N5_KEYS[key], self.n4(node))
            return self.n4(node)
        return node

    def content(self, content: Any) -> Any:
        out = []
        for item in content:
            if (
                isinstance(item, dict)
                and item.get("type") == "text"
                and isinstance(item.get("text"), str)
            ):
                text = self.n5_text(self.n4(item["text"]))
                try:
                    parsed = json.loads(text)
                except ValueError:
                    out.append(
                        {
                            **{k: self.value(v, k) for k, v in item.items() if k != "text"},
                            "text": text,
                        }
                    )
                else:
                    out.append(
                        {
                            **{k: self.value(v, k) for k, v in item.items() if k != "text"},
                            "text": {"$json": parsed, "sha256": sha256_hex(text)},
                        }
                    )
            else:
                out.append(self.value(item))
        return out

    def result(self, method: str, result: Any) -> Any:
        if method == "tools/list" and isinstance(result, dict) and "tools" in result:
            return tools_digest(result)
        if isinstance(result, dict) and isinstance(result.get("content"), list):
            rest = {k: v for k, v in result.items() if k != "content"}
            out = self.value(rest)
            out["content"] = self.content(result["content"])
            return {k: out[k] for k in sorted(out)}
        return self.value(result)


def tools_digest(result: dict[str, Any]) -> dict[str, Any]:
    tools = result["tools"]
    text = canon(tools)
    digest = {
        "names": sorted(t["name"] for t in tools),
        "sha256": sha256_hex(text),
        "bytes": len(text.encode("utf-8")),
        "output_schema": sorted(t["name"] for t in tools if "outputSchema" in t),
        "meta": sorted(t["name"] for t in tools if "_meta" in t),
        "annotations": sorted(t["name"] for t in tools if "annotations" in t),
    }
    rest = {k: v for k, v in result.items() if k != "tools"}
    return {"tools_digest": digest, **({"other": rest} if rest else {})}


@dataclass
class Exchange:
    step: int
    name: str
    method: str
    kind: str
    request: Any
    raw: bytes | None


@dataclass
class CaseRun:
    case: str
    conversation: str
    requested: Any
    exchanges: list[Exchange] = field(default_factory=list)
    frames: list[bytes] = field(default_factory=list)
    notifications: list[dict[str, Any]] = field(default_factory=list)
    close: dict[str, Any] = field(default_factory=dict)
    stderr: str = ""
    pid: int = 0


def _requested_value(requested: Any) -> Any:
    return None if requested is MISSING else requested


def normalise_run(run: CaseRun, paths: Paths, sdk_version: str) -> dict[str, Any]:
    """The atlas-s38-transcript.v1 object for one run (N1-N8)."""
    subs = n2_substitutions(paths)
    norm = Normaliser()
    ordinals: dict[Any, int] = {}
    for frame in run.frames:
        body = json.loads(frame)
        if isinstance(body, dict) and "id" in body:
            ordinals.setdefault(body["id"], len(ordinals) + 1)
    exchanges = []
    for ex in run.exchanges:
        response: Any = None
        if ex.raw is not None:
            body = json.loads(apply_n2(ex.raw.decode("utf-8"), subs))
            response = {"id": ordinals.get(body.get("id"), body.get("id"))}
            if "result" in body:
                response["result"] = norm.result(ex.method, body["result"])
            else:
                response["error"] = norm.value(body["error"])
        exchanges.append(
            {
                "step": ex.step,
                "name": ex.name,
                "method": ex.method,
                "kind": ex.kind,
                "request": ex.request,
                "response": response,
            }
        )
    return {
        "format": TRANSCRIPT_FORMAT,
        "case": run.case,
        "conversation": run.conversation,
        "revision_requested": _requested_value(run.requested),
        "host": "raw-line",
        "sdk": {"mcp": sdk_version},
        "fixture": FIXTURE_NAME,
        "exchanges": exchanges,
        "notifications": [
            norm.value(json.loads(apply_n2(canon(n), subs))) for n in run.notifications
        ],
        "exit": {"code": run.close.get("exit_code"), "closed_by": "client"},
    }


def transcript_bytes(transcript: dict[str, Any]) -> bytes:
    return (canon(transcript) + "\n").encode("utf-8")


def frame_digests(frames: list[bytes], paths: Paths) -> list[dict[str, Any]]:
    """Per raw frame sha256 and bytes with only N2 applied (the line terminator is excluded)."""
    subs = n2_substitutions(paths)
    out = []
    for frame in frames:
        data = apply_n2(frame.decode("utf-8"), subs).rstrip("\n").encode("utf-8")
        out.append({"sha256": sha256_hex(data), "bytes": len(data)})
    return out


def sdk_version() -> str:
    import importlib.metadata

    return importlib.metadata.version("mcp")


# ----------------------------------------------------------------------------- case drivers
def steps_for(case: str) -> list[tuple[int, str, str, str, Any]]:
    """(step, name, method, kind, params) for one case; initialize params are case-specific."""
    conversation, requested = CASES[case]
    if conversation == "lifecycle.v1":
        rows = []
        for step, name, arguments in CONVERSATION:
            if step == 1:
                rows.append(
                    (1, "initialize", "initialize", "request", initialize_params(requested))
                )
            elif name.startswith("notifications/"):
                rows.append((step, name, name, "notification", None))
            elif step in TOOL_STEPS:
                rows.append(
                    (step, name, "tools/call", "request", {"name": name, "arguments": arguments})
                )
            else:
                rows.append((step, name, name, "request", arguments))
        return rows
    if case == "malformed.pre-initialize":
        return [
            (1, "tools/list", "tools/list", "request", None),
            (2, "ping", "ping", "request", None),
        ]
    rows = [(1, "initialize", "initialize", "request", initialize_params(requested))]
    if requested is not MISSING:
        rows.append(
            (2, "notifications/initialized", "notifications/initialized", "notification", None)
        )
    rows.append((3, "ping", "ping", "request", None))
    rows.append((4, "tools/list", "tools/list", "request", None))
    return rows


def run_steps(client: RawLineClient, steps: list[tuple[int, str, str, str, Any]]) -> list[Exchange]:
    exchanges = []
    for step, name, method, kind, params in steps:
        if kind == "notification":
            client.notify(method, params)
            exchanges.append(Exchange(step, name, method, kind, params, None))
        else:
            raw = client.request(method, params if params is not None else MISSING)
            exchanges.append(Exchange(step, name, method, kind, params, raw))
    return exchanges


def run_case(
    case: str, argv: list[str], env: dict[str, str] | None = None, deadline_s: float = 150
) -> CaseRun:
    conversation, requested = CASES[case]
    client = RawLineClient(argv, env=env, deadline_s=deadline_s)
    run = CaseRun(case, conversation, requested, pid=client.proc.pid)
    try:
        run.exchanges = run_steps(client, steps_for(case))
    finally:
        run.close = client.close()
        run.frames = list(client.frames)
        run.notifications = list(client.notifications)
        run.stderr = client.stderr_text()
    return run


AC004_CASES: dict[str, tuple[str, list[tuple[str, Any]]]] = {
    "U1": (
        "2099-01-01",
        [("initialize", "2099-01-01"), ("notifications/initialized", None), ("tools/list", None)],
    ),
    "U2": (
        "2024-10-07",
        [("initialize", "2024-10-07"), ("notifications/initialized", None), ("ping", None)],
    ),
    "U3": ("", [("initialize", ""), ("notifications/initialized", None), ("ping", None)]),
    "U4": (
        "20241105",
        [("initialize", 20241105), ("notifications/initialized", None), ("tools/list", None)],
    ),
    "M1": ("missing", [("initialize", MISSING)]),
    "M2a": ("null", [("initialize", None)]),
    "M2b": ("list", [("initialize", ["2025-11-25"])]),
    "M3": ("no-capabilities", [("initialize", "$no-capabilities")]),
    "P1": ("pre-init-tools-list", [("tools/list", MISSING)]),
    "P2": ("pre-init-ping", [("ping", MISSING)]),
    "R1": (
        "recovery",
        [
            ("initialize", MISSING),
            ("initialize", "2024-11-05"),
            ("notifications/initialized", None),
            ("ping", None),
            ("tools/list", None),
        ],
    ),
}


def ac004_params(value: Any) -> Any:
    info = {"name": "atlas-s38-fixture-host", "version": "1"}
    if isinstance(value, str) and value == "$no-capabilities":
        return {"protocolVersion": SELECTED, "clientInfo": info}
    if value is MISSING:
        return {"capabilities": {}, "clientInfo": info}
    return {"protocolVersion": value, "capabilities": {}, "clientInfo": info}


def summarise_response(body: dict[str, Any]) -> dict[str, Any]:
    """A response body with a tools/list result reduced to its digest."""
    result = body.get("result")
    if isinstance(result, dict) and "tools" in result:
        return {**body, "result": tools_digest(result)}
    return body


def run_ac004(case: str, argv: list[str], env: dict[str, str] | None = None) -> dict[str, Any]:
    """Raw frames of one AC-004 table case. Returns {frames, responses, stderr, close}."""
    _label, steps = AC004_CASES[case]
    client = RawLineClient(argv, env=env, deadline_s=150)
    responses: list[Any] = []
    try:
        for method, value in steps:
            if method == "notifications/initialized":
                client.notify(method)
                continue
            params: Any = ac004_params(value) if method == "initialize" else MISSING
            responses.append(json.loads(client.request(method, params)))
    finally:
        close = client.close()
    return {
        "frames": list(client.frames),
        "responses": responses,
        "stderr": client.stderr_text(),
        "close": close,
    }


def normalise_dump(
    norm: Normaliser, subs: list[tuple[re.Pattern[str], str]], method: str, dump: Any
) -> Any:
    """Normalise a client-side model dump the way a raw server frame result is normalised."""
    return norm.result(method, json.loads(apply_n2(canon(dump), subs)))


def negotiate_as_client(
    client: RawLineClient, supported: set[str], requested: Any
) -> dict[str, Any]:
    """A fixture client with a closed revision set; it disconnects on an unusable counter-offer."""
    body = json.loads(client.request("initialize", initialize_params(requested)))
    offered = body["result"]["protocolVersion"]
    if offered not in supported:
        return {
            "client_outcome": "disconnect",
            "reason": "offered_revision_not_supported_by_client",
            "requested": requested,
            "offered": offered,
        }
    client.notify("notifications/initialized")
    ping = json.loads(client.request("ping"))
    tools = json.loads(client.request("tools/list"))
    return {
        "client_outcome": "proceed",
        "requested": requested,
        "offered": offered,
        "completed": ["initialize", "ping", "tools/list"],
        "ping": ping["result"],
        "tools": len(tools["result"]["tools"]),
    }


def run_live(paths: Paths, revision: str, source: bytes = LIVE_SOURCE) -> dict[str, Any]:
    """page -> resources/read -> continue on a repo large enough to page (raw host)."""
    client = RawLineClient(paths.serve_argv(), env=child_env(0), deadline_s=120)
    exchanges: list[Exchange] = []
    out: dict[str, Any] = {}
    try:
        steps: list[tuple[int, str, str, str, Any]] = [
            (1, "initialize", "initialize", "request", initialize_params(revision)),
            (2, "notifications/initialized", "notifications/initialized", "notification", None),
        ]
        exchanges += run_steps(client, steps)
        page_args = {
            "schema_version": GRAPH,
            "op": "page",
            "query": {
                "schema_version": GRAPH,
                "op": "neighborhood",
                "seed": {"name": "A"},
                "max_depth": 1,
                "deadline_ms": 2000,
            },
            "page_max_bytes": 18000,
        }
        raw = client.request("tools/call", {"name": "graph_query", "arguments": page_args})
        exchanges.append(Exchange(3, "graph_query", "tools/call", "request", page_args, raw))
        page = json.loads(raw)["result"]
        payload = json.loads(page["content"][0]["text"])
        data = payload["data"]
        descriptor = data["resources"][0]
        raw = client.request("resources/read", {"uri": descriptor["uri"]})
        exchanges.append(Exchange(4, "resources/read", "resources/read", "request", None, raw))
        resource = json.loads(raw)["result"]["contents"][0]
        cont_args = {
            "schema_version": GRAPH,
            "op": "continue",
            "cursor": data["next_cursor"],
            "query_digest": data["query_digest"],
        }
        raw = client.request("tools/call", {"name": "graph_query", "arguments": cont_args})
        exchanges.append(Exchange(5, "graph_query", "tools/call", "request", cont_args, raw))
        out = {
            "page": page,
            "payload": payload,
            "descriptor": descriptor,
            "resource": resource,
            "continue": json.loads(raw)["result"],
            "source": source,
        }
    finally:
        out["close"] = client.close()
        out["stderr"] = client.stderr_text()
        out["exchanges"] = exchanges
    return out


class Lab:
    """One module-shared set of fixture repos and cached runs (servers run sequentially)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.paths = build_fixture(root / "mini")
        self.live_paths = build_fixture(root / "live", source=LIVE_SOURCE)
        self.api_paths: Paths | None = None
        self._runs: dict[tuple[str, int], tuple[CaseRun, Paths]] = {}
        self._live: dict[str, dict[str, Any]] = {}
        self._counter = 0

    def fresh(self, base: Paths | None = None) -> Paths:
        self._counter += 1
        return fresh_memex(base or self.paths, f"p{self._counter}")

    def api(self) -> Paths:
        if self.api_paths is None:
            self.api_paths = build_fixture(self.root / "api", api=True)
        return self.api_paths

    def lifecycle(self, case: str, seed: int = 0) -> tuple[CaseRun, Paths]:
        key = (case, seed)
        if key not in self._runs:
            paths = self.fresh()
            self._runs[key] = (run_case(case, paths.serve_argv(), child_env(seed)), paths)
        return self._runs[key]

    def transcript(self, case: str, seed: int = 0) -> bytes:
        run, paths = self.lifecycle(case, seed)
        return transcript_bytes(normalise_run(run, paths, sdk_version()))

    def live(self, revision: str) -> dict[str, Any]:
        if revision not in self._live:
            self._live[revision] = run_live(self.fresh(self.live_paths), revision)
        return self._live[revision]


_LAB: list[Lab] = []


def get_lab(root: Path) -> Lab:
    """The process-wide lab (the first caller's root wins; later callers share its caches)."""
    if not _LAB:
        _LAB.append(Lab(root))
    return _LAB[0]


# ----------------------------------------------------------------------------- official schema
RESULT_DEFINITIONS = {
    "initialize": "InitializeResult",
    "ping": "EmptyResult",
    "tools/list": "ListToolsResult",
    "tools/call": "CallToolResult",
    "resources/list": "ListResourcesResult",
    "resources/templates/list": "ListResourceTemplatesResult",
    "resources/read": "ReadResourceResult",
}
ENVELOPES = {
    "2024-11-05": ("JSONRPCResponse", "JSONRPCError"),
    "2025-03-26": ("JSONRPCResponse", "JSONRPCError"),
    "2025-06-18": ("JSONRPCResponse", "JSONRPCError"),
    "2025-11-25": ("JSONRPCResultResponse", "JSONRPCErrorResponse"),
}


class OfficialSchemas:
    """The official MCP schema.json files, registered as urn:mcp-schema:<revision>."""

    def __init__(self, schema_dir: Path) -> None:
        from jsonschema.validators import validator_for
        from referencing import Registry, Resource

        self.schemas: dict[str, dict[str, Any]] = {}
        self.classes: dict[str, Any] = {}
        self.dialects: dict[str, str] = {}
        self.defs_key: dict[str, str] = {}
        self.registry = Registry()
        for revision in ALL_REVISIONS:
            path = schema_dir / f"{revision}.schema.json"
            if not path.is_file():
                continue
            schema = json.loads(path.read_bytes())
            cls = validator_for(schema)
            cls.check_schema(schema)
            self.schemas[revision] = schema
            self.classes[revision] = cls
            self.dialects[revision] = schema["$schema"]
            self.defs_key[revision] = "$defs" if "$defs" in schema else "definitions"
            self.registry = self.registry.with_resource(
                f"urn:mcp-schema:{revision}", Resource.from_contents(schema)
            )

    def errors(self, revision: str, definition: str, instance: Any) -> list[tuple[str, str]]:
        ref = f"urn:mcp-schema:{revision}#/{self.defs_key[revision]}/{definition}"
        validator = self.classes[revision]({"$ref": ref}, registry=self.registry)
        out = []
        for error in validator.iter_errors(instance):
            pointer = "/" + "/".join(
                "*" if isinstance(p, int) else str(p) for p in error.absolute_path
            )
            out.append((pointer, str(error.validator)))
        return sorted(set(out))


def validate_run(
    schemas: OfficialSchemas, revision: str, case: str, exchanges: list[Exchange]
) -> tuple[set[tuple[str, int, str, str, str]], set[str]]:
    """Failures as (case, step, definition, pointer, keyword) and the definitions covered."""
    failures: set[tuple[str, int, str, str, str]] = set()
    covered: set[str] = set()
    response_def, error_def = ENVELOPES[revision]
    for ex in exchanges:
        if ex.raw is None:
            continue
        body = json.loads(ex.raw)
        envelope = error_def if "error" in body else response_def
        checks = [(envelope, body)]
        definition = RESULT_DEFINITIONS.get(ex.method)
        if "result" in body and definition:
            checks.append((definition, body["result"]))
        for name, instance in checks:
            covered.add(name)
            for pointer, keyword in schemas.errors(revision, name, instance):
                failures.add((case, ex.step, name, pointer, keyword))
    return failures, covered


# ----------------------------------------------------------------------------- capture driver
def _identity(label: str) -> dict[str, Any]:
    import atlas_aci

    root = Path(atlas_aci.__file__).resolve().parent
    return {
        "label": label,
        "atlas_aci_file": str(root / "__init__.py"),
        "server_py_sha256": sha256_hex((root / "server.py").read_bytes()),
        "results_py_sha256": sha256_hex((root / "protocol" / "results.py").read_bytes()),
        "python": sys.version.split()[0],
        "mcp": sdk_version(),
    }


def _inprocess_dump(paths: Paths) -> dict[str, Any]:
    import asyncio

    from atlas_aci.config import Config
    from atlas_aci.server import build_server_state, build_tool_manifest, call_tool_result

    config = Config(
        repo=paths.repo, memex_root=paths.memex, graph_root=None if paths.api else paths.graphs
    )
    manifests = {}
    for version in (*ALL_REVISIONS, "2099-01-01", ""):
        text = canon(build_tool_manifest(config, version))
        manifests[version] = {"sha256": sha256_hex(text), "bytes": len(text.encode("utf-8"))}
    default = canon(build_tool_manifest(config))
    manifests["<default>"] = {"sha256": sha256_hex(default), "bytes": len(default.encode("utf-8"))}
    cli = subprocess.run(
        [sys.executable, "-m", "atlas_aci", "tools", "--repo", str(paths.repo)],
        capture_output=True,
        env=child_env(None),
        timeout=120,
    )
    calls = {
        "view_file": {"path": "a.py", "start_line": 1, "end_line": 4},
        "list_dir": {"path": "."},
        "search_text": {"pattern": "target", "scope": "a.py"},
        "search_symbol": {"name": "target"},
        "graph_query": {"query": "definitions_of:target"},
        "test_dry_run": {"path": "a.py"},
        "memex_read": {"ref": "memex://excerpt/" + "0" * 64},
    }

    async def results() -> dict[str, Any]:
        enforcement, memex, code_graph = build_server_state(config)
        out: dict[str, Any] = {}
        try:
            for version in ("2024-11-05", "2025-11-25"):
                for name, arguments in calls.items():
                    result = await call_tool_result(
                        name,
                        arguments,
                        config,
                        enforcement,
                        memex,
                        code_graph,
                        protocol_version=version,
                    )
                    data = result.model_dump_json(by_alias=True, exclude_none=True).encode("utf-8")
                    out[f"{version}:{name}"] = {
                        "sha256": sha256_hex(data),
                        "bytes": len(data),
                        "isError": bool(result.isError),
                    }
        finally:
            code_graph.close()
        return out

    return {
        "manifests": manifests,
        "cli_tools": {
            "exit": cli.returncode,
            "sha256": sha256_hex(cli.stdout),
            "bytes": len(cli.stdout),
        },
        "call_tool_result": asyncio.run(results()),
    }


def capture(
    out: Path, fixture: Path, label: str, seed: int = 0, api: bool = False
) -> dict[str, Any]:
    """Run every golden case and every AC-004 case against the importable atlas_aci tree."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "transcripts").mkdir(exist_ok=True)
    env = child_env(seed)
    paths = build_fixture(fixture, env=env, api=api)
    identity = _identity(label)
    version = sdk_version()
    frames: dict[str, Any] = {}
    hashes: dict[str, str] = {}
    for case in CASES:
        run = run_case(case, paths.serve_argv(), env)
        transcript = normalise_run(run, paths, version)
        data = transcript_bytes(transcript)
        (out / "transcripts" / f"{case}.v1.json").write_bytes(data)
        hashes[case] = sha256_hex(data)
        frames[f"case:{case}"] = frame_digests(run.frames, paths)
        if run.stderr.count("Traceback"):
            raise ProtocolError(f"{case}: Traceback on stderr")
    observed: dict[str, Any] = {}
    for case in AC004_CASES:
        result = run_ac004(case, paths.serve_argv(), env)
        frames[f"ac004:{case}"] = frame_digests(result["frames"], paths)
        observed[case] = {
            "responses": [summarise_response(body) for body in result["responses"]],
            "close": result["close"],
            "traceback": "Traceback" in result["stderr"],
        }
    dump = _inprocess_dump(paths)
    (out / "frames.json").write_text(
        json.dumps({"identity": identity, "frames": frames}, indent=1, sort_keys=True) + "\n"
    )
    (out / "inprocess.json").write_text(
        json.dumps({"identity": identity, **dump}, indent=1, sort_keys=True) + "\n"
    )
    (out / "ac004.json").write_text(json.dumps(observed, indent=1, sort_keys=True) + "\n")
    (out / "case-sha256.json").write_text(json.dumps(hashes, indent=1, sort_keys=True) + "\n")
    return {"identity": identity, "cases": hashes}


# ----------------------------------------------------------------------------- production driver
def _flag(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


def production_main(argv: list[str]) -> int:
    """Replay one case against `-- <server argv>` and compare with a committed golden."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", required=True)
    parser.add_argument("--golden", required=True)
    parser.add_argument("--golden-case", default=None)
    parser.add_argument("--hashseed", default="0")
    head, _, server = (argv[: argv.index("--")], "--", argv[argv.index("--") + 1 :])
    args = parser.parse_args(head)
    paths = Paths(
        Path(_flag(server, "--repo")),
        Path(_flag(server, "--memex-root")),
        Path(_flag(server, "--graph-root")),
    )
    env = child_env(int(args.hashseed))
    run = run_case(args.case, server, env)
    transcript = normalise_run(run, paths, sdk_version())
    data = transcript_bytes(transcript)
    golden = Path(args.golden) / "transcripts" / f"{args.golden_case or args.case}.v1.json"
    expected = golden.read_bytes()
    first = run.exchanges[0].raw
    init_frame = apply_n2(first.decode("utf-8"), n2_substitutions(paths)).strip() if first else ""
    match = bool(data) and bool(expected) and data == expected
    print(
        json.dumps(
            {
                "case": args.case,
                "golden": golden.name,
                "match": match,
                "sha256": sha256_hex(data),
                "bytes": len(data),
                "exit": run.close,
                "traceback": "Traceback" in run.stderr,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    # The raw first server frame (initialize result or error), N2 applied, for wire greps.
    print(init_frame, flush=True)
    return 0 if match and not run.close.get("killed") else 1


def manifest_entries(root: Path) -> list[dict[str, Any]]:
    """sha256 and size of every fixture file except the manifest itself (sorted by path)."""
    rows = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "MANIFEST.v1.json":
            data = path.read_bytes()
            rows.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": sha256_hex(data),
                    "bytes": len(data),
                }
            )
    return rows


def write_manifest(root: Path, captured_with: list[str]) -> dict[str, Any]:
    manifest = {
        "format": "atlas-s38-fixtures.v1",
        "base_commit": BASE_COMMIT,
        "captured_with": captured_with,
        "sdk": {"mcp": sdk_version()},
        "files": manifest_entries(root),
    }
    (root / "MANIFEST.v1.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    return manifest


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    cap = sub.add_parser("capture")
    cap.add_argument("--out", required=True)
    cap.add_argument("--fixture", required=True)
    cap.add_argument("--label", required=True)
    cap.add_argument("--seed", type=int, default=0)
    cap.add_argument("--api", action="store_true")
    man = sub.add_parser("manifest")
    man.add_argument("--dir", required=True)
    man.add_argument("--captured-with", nargs="+", required=True)
    args = parser.parse_args(argv)
    if args.command == "manifest":
        manifest = write_manifest(Path(args.dir), args.captured_with)
        print(json.dumps({"files": len(manifest["files"])}))
        return 0
    if args.command == "capture":
        print(
            json.dumps(
                capture(Path(args.out), Path(args.fixture), args.label, args.seed, args.api),
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
