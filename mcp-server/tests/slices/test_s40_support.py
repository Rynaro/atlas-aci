"""ACI-S40 support: test-time keys and tokens, a shared-deployment builder, an HTTP client with
per-route bearer tokens, class-level repository observers and the production driver.

The top level imports only the standard library, so the production driver can run in the
production image (no pytest, `python -I`). atlas, jwt, cryptography and the S38/S39 support modules
are imported lazily. This module defines no test functions. Key pairs are generated here, at test
time; no key material is committed.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import os
import signal
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ISSUER = "https://idp.example.test"
BASE = "https://atlas.example.test/mcp"
LOOPBACK = "127.0.0.1"
SELECTED = "2025-11-25"
GRAPH = "atlas.graph.v1"
ALL_TOOLS = ["graph_query", "list_dir", "search_symbol", "search_text", "view_file"]
FIXTURE_TOOLS = ["list_dir", "search_symbol", "search_text", "view_file"]
RESPONSE_S = 30.0

PAGE_QUERY = {
    "schema_version": GRAPH,
    "op": "neighborhood",
    "seed": {"name": "A"},
    "max_depth": 1,
    "deadline_ms": 2000,
}
PAGE_ARGS = {"schema_version": GRAPH, "op": "page", "query": PAGE_QUERY, "page_max_bytes": 18000}
IMPACT_ARGS = {"schema_version": GRAPH, "op": "impact"}
STATUS_ARGS = {"schema_version": GRAPH, "op": "status"}
RETRIEVE_B = {"schema_version": GRAPH, "op": "retrieve", "text": "B"}
V1 = {"m.py": "def A():\n    return B()\n\n\ndef B(x):\n    return x\n"}
V2 = {"m.py": "def A():\n    return B(1)\n\n\ndef B(x, y):\n    return x\n"}


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def grant(
    subject: str,
    repository: str,
    tools: list[str] | None = None,
    scope: str | None = None,
    issuer: str = ISSUER,
) -> dict[str, Any]:
    document: dict[str, Any] = {
        "issuer": issuer,
        "subject": subject,
        "repository": repository,
        "tools": list(tools if tools is not None else ALL_TOOLS),
    }
    if scope is not None:
        document["required_scope"] = scope
    return document


def policy_document(grants: list[dict[str, Any]]) -> dict[str, Any]:
    return {"format": "atlas-aci-shared-policy.v1", "grants": grants}


def write_private(path: Path, data: bytes | str, mode: int = 0o600) -> None:
    """Atomic same-directory replace of a configuration file (owner-writable only)."""
    raw = data.encode("utf-8") if isinstance(data, str) else data
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(raw)
    os.chmod(temporary, mode)
    os.replace(temporary, path)


# ----------------------------------------------------------------------------- keys and tokens
class Keys:
    """Generated-at-test-time RSA-2048, EC P-256 and Ed25519 key pairs and the JWKS for them."""

    def __init__(self) -> None:
        from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

        self.rsa = rsa.generate_private_key(65537, 2048)
        self.ec = ec.generate_private_key(ec.SECP256R1())
        self.ed = ed25519.Ed25519PrivateKey.generate()
        self.stranger = rsa.generate_private_key(65537, 2048)
        self.kids = {"rsa": "kid-rsa", "ec": "kid-ec", "ed": "kid-ed"}

    def public_jwk(self, which: str) -> dict[str, Any]:
        from jwt.algorithms import ECAlgorithm, OKPAlgorithm, RSAAlgorithm

        key = {"rsa": self.rsa, "ec": self.ec, "ed": self.ed, "stranger": self.stranger}[which]
        algorithm = {"rsa": RSAAlgorithm, "stranger": RSAAlgorithm, "ec": ECAlgorithm}.get(
            which, OKPAlgorithm
        )
        document = json.loads(algorithm.to_jwk(key.public_key()))
        document["kid"] = self.kids.get(which, "kid-" + which)
        document["use"] = "sig"
        return document

    def jwks(self, which: tuple[str, ...] = ("rsa", "ec", "ed")) -> dict[str, Any]:
        return {"keys": [self.public_jwk(name) for name in which]}

    def jwks_bytes(self, which: tuple[str, ...] = ("rsa", "ec", "ed")) -> bytes:
        return json.dumps(self.jwks(which)).encode("utf-8")

    def claims(
        self, subject: str, audience: Any, *, ttl: int = 3600, **extra: Any
    ) -> dict[str, Any]:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": ISSUER,
            "aud": audience,
            "sub": subject,
            "iat": now,
            "exp": now + ttl,
        }
        claims.update(extra)
        return {k: v for k, v in claims.items() if v is not MISSING}

    def mint(
        self,
        subject: str,
        audience: Any,
        *,
        alg: str = "RS256",
        which: str | None = None,
        kid: str | None = None,
        header: dict[str, Any] | None = None,
        ttl: int = 3600,
        **extra: Any,
    ) -> str:
        import jwt

        which = which or {"RS256": "rsa", "PS256": "rsa", "ES256": "ec", "EdDSA": "ed"}[alg]
        key = {"rsa": self.rsa, "ec": self.ec, "ed": self.ed, "stranger": self.stranger}[which]
        headers = {"kid": kid or self.kids.get(which, "kid-rsa"), **(header or {})}
        claims = self.claims(subject, audience, ttl=ttl, **extra)
        return str(jwt.encode(claims, key, algorithm=alg, headers=headers))

    def forge(
        self,
        header: dict[str, Any],
        payload_text: str,
        *,
        signature: bytes | None = None,
        sign_with: str | None = "rsa",
    ) -> str:
        """A token assembled by hand: any header, any payload text, any (or no) signature."""
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        head = b64(json.dumps(header, separators=(",", ":")).encode())
        body = b64(payload_text.encode("utf-8"))
        signing_input = f"{head}.{body}".encode("ascii")
        if signature is None:
            assert sign_with is not None
            key = {"rsa": self.rsa, "stranger": self.stranger}[sign_with]
            signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        return f"{head}.{body}.{b64(signature)}"

    def hs256_with_public_key(self, subject: str, audience: str) -> str:
        """HS256 keyed with the RSA public key's PEM (algorithm confusion)."""
        from cryptography.hazmat.primitives import serialization

        pem = self.rsa.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        head = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": self.kids["rsa"]}).encode())
        body = b64(json.dumps(self.claims(subject, audience)).encode())
        mac = hmac.new(pem, f"{head}.{body}".encode("ascii"), hashlib.sha256).digest()
        return f"{head}.{body}.{b64(mac)}"


MISSING: Any = object()
_KEYS: list[Keys] = []


def shared_keys() -> Keys:
    """One key set per process (generation is the slow part)."""
    if not _KEYS:
        _KEYS.append(Keys())
    return _KEYS[0]


# ----------------------------------------------------------------------------- fixtures
@dataclass
class Labs:
    """Indexed fixture repositories, built once per module. Tests never mutate them."""

    a: Any  # LIVE source: pages
    b: Any  # the S38 mini repository
    c: Any  # a third repository (adversarial files are planted here, then removed)

    @classmethod
    def build(cls, root: Path) -> Labs:
        import test_s38_support as s38

        return cls(
            a=s38.build_fixture(root / "a", source=s38.LIVE_SOURCE),
            b=s38.build_fixture(root / "b"),
            c=s38.build_fixture(root / "c"),
        )


_LABS: list[Labs] = []


def get_labs(root: Path) -> Labs:
    if not _LABS:
        _LABS.append(Labs.build(root))
    return _LABS[0]


class World:
    """One shared deployment on disk (config, policy, JWKS) plus the router built from it."""

    def __init__(
        self,
        root: Path,
        labs: Labs,
        *,
        repos: dict[str, Any] | None = None,
        grants: list[dict[str, Any]] | None = None,
        befores: dict[str, str] | None = None,
        limits: dict[str, Any] | None = None,
        listen: dict[str, Any] | None = None,
        identity: dict[str, Any] | None = None,
        interval: int = 0,
        keys: Keys | None = None,
    ) -> None:
        self.root = root
        self.labs = labs
        self.keys = keys or shared_keys()
        self.repos = repos if repos is not None else {"a": labs.a, "b": labs.b}
        self.befores = befores or {}
        self.config_dir = root / "etc"
        self.policy_path = self.config_dir / "policy.json"
        self.jwks_path = self.config_dir / "jwks.json"
        self.config_path = self.config_dir / "shared.json"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.config_dir, 0o700)
        self.document: dict[str, Any] = {
            "format": "atlas-aci-shared.v1",
            "listen": {"port": 0, "max_inflight": 8, **(listen or {})},
            "identity": {
                "issuer": ISSUER,
                "jwks_file": str(self.jwks_path),
                "resource_base_url": BASE,
                **(identity or {}),
            },
            "policy": {"file": str(self.policy_path), "reload_interval_seconds": interval},
            "limits": {"max_calls_per_minute": 0, **(limits or {})},
            "repositories": [
                self._entry(identifier, paths) for identifier, paths in self.repos.items()
            ],
        }
        write_private(self.jwks_path, self.keys.jwks_bytes())
        self.write_policy(grants or [])
        self.write_config()
        self.deployment: Any = None
        self.router: Any = None
        self.observer: Observer | None = None

    def _entry(self, identifier: str, paths: Any) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "id": identifier,
            "repo": str(paths.repo),
            "graph_root": str(paths.graphs),
            "memex_root": str(self.root / "memex" / identifier),
        }
        if identifier in self.befores:
            entry["before_repo_id"] = self.befores[identifier]
        return entry

    def write_policy(self, grants: list[dict[str, Any]] | bytes) -> None:
        raw = grants if isinstance(grants, bytes) else json.dumps(policy_document(grants))
        write_private(self.policy_path, raw)

    def write_config(self) -> None:
        write_private(self.config_path, json.dumps(self.document))

    def load(self, environ: dict[str, str] | None = None) -> Any:
        from atlas_aci.query.shared_http import load_shared_config

        self.deployment = load_shared_config(self.config_path, environ=environ or {})
        return self.deployment

    def build(self, **kwargs: Any) -> Any:
        from atlas_aci.query.shared_http import build_shared

        if self.deployment is None:
            self.load()
        self.router = build_shared(self.deployment, **kwargs)
        return self.router

    @contextlib.asynccontextmanager
    async def serve(self, **kwargs: Any) -> Any:
        import test_s39_support as s39

        if self.router is None:
            self.build(**kwargs)
        async with s39.serving(self.router) as (host, port):
            yield SharedHttp(host, port, self)

    def registry(self, identifier: str) -> Any:
        return self.router.registries[identifier]


def jwt_text(token: str | bytes) -> str:
    return token.decode() if isinstance(token, bytes) else token


# ----------------------------------------------------------------------------- HTTP client
class SharedHttp:
    """The S39 raw client with per-route bearer tokens. Every method takes a repository id."""

    def __init__(self, host: str, port: int, world: World) -> None:
        import test_s39_support as s39

        self.raw = s39.RawHttp(host, port)
        self.host, self.port = host, port
        self.world = world
        self._ids: dict[tuple[str, str], int] = {}

    def token(self, subject: str, repository: str, **kwargs: Any) -> str:
        return self.world.keys.mint(subject, f"{BASE}/{repository}", **kwargs)

    def headers(self, token: str | None, version: str | None = SELECTED, extra: Any = None) -> Any:
        headers = self.raw._rpc_headers(version, list(extra or []))
        if token is not None:
            headers.append(("Authorization", f"Bearer {token}"))
        return headers

    async def post(
        self,
        repository: str,
        subject: str | None,
        message: Any,
        *,
        token: str | None = None,
        authorization: list[str] | None = None,
        version: str | None = SELECTED,
        path: str | None = None,
        extra: Any = None,
        **kwargs: Any,
    ) -> Any:
        headers = self.headers(None, version, extra)
        if authorization is not None:
            headers += [("Authorization", value) for value in authorization]
        else:
            if token is None and subject is not None:
                token = self.token(subject, repository)
            if token is not None:
                headers.append(("Authorization", f"Bearer {token}"))
        body = json.dumps(message).encode("utf-8")
        return await self.raw.request(
            "POST", path or f"/mcp/{repository}", headers=headers, body=body, **kwargs
        )

    async def rpc(
        self, repository: str, subject: str | None, method: str, params: Any = None, **kwargs: Any
    ) -> Any:
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
        if params is not None:
            message["params"] = params
        return await self.post(repository, subject, message, **kwargs)

    async def tool(
        self, repository: str, subject: str | None, name: str, arguments: Any, **kwargs: Any
    ) -> Any:
        return await self.rpc(
            repository, subject, "tools/call", {"name": name, "arguments": arguments}, **kwargs
        )

    async def graph(self, repository: str, subject: str | None, arguments: Any, **kw: Any) -> Any:
        return await self.tool(repository, subject, "graph_query", arguments, **kw)

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        return await self.raw.request(method, path, **kwargs)


async def router_call(
    world: World,
    repository: str,
    token: str | None,
    *,
    method: str = "POST",
    path: str | None = None,
    authorization: list[str] | None = None,
    extra: list[tuple[str, str]] | None = None,
    client: tuple[str, int] | None = ("127.0.0.1", 40000),
    host: str = "127.0.0.1:5555",
) -> tuple[int, dict[str, str], bytes, int]:
    """Call the router directly (no network): (status, headers, body, receive calls)."""
    import test_s39_support as s39

    world.router.allow_host_name(LOOPBACK)
    headers = [
        ("Host", host),
        ("Content-Type", "application/json"),
        ("Accept", s39.ACCEPT),
        ("mcp-protocol-version", SELECTED),
    ]
    if authorization is not None:
        headers += [("Authorization", value) for value in authorization]
    elif token is not None:
        headers.append(("Authorization", f"Bearer {token}"))
    headers += list(extra or [])
    scope = s39.asgi_scope(
        method=method, path=path or f"/mcp/{repository}", headers=headers, client=client
    )
    scope["raw_path"] = scope["path"].encode()
    return await s39.asgi_call(world.router, scope)


def tool_payload(response: Any) -> dict[str, Any]:
    """The decoded tool-result text of a 200 tools/call response."""
    assert response.status == 200, (response.status, response.text[:300])
    result = response.rpc(1)["result"]
    parsed = json.loads(result["content"][0]["text"])
    assert isinstance(parsed, dict)
    parsed["_isError"] = result["isError"]
    return parsed


def error_code(payload: dict[str, Any]) -> Any:
    """The error code of a structured or legacy tool failure payload."""
    error = payload.get("error")
    return error.get("code") if isinstance(error, dict) else error


def page_cursor(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload["data"]
    return {
        "schema_version": GRAPH,
        "op": "continue",
        "cursor": data["next_cursor"],
        "query_digest": data["query_digest"],
    }


# ----------------------------------------------------------------------------- observers
@dataclass
class Observer:
    """Class-level counting wrappers (the S39 H3 precedent). Spies count; they never change
    behaviour. Every zero asserted from this observer is paired with a positive control."""

    events: list[tuple[str, str]] = field(default_factory=list)
    scopes: list[Any] = field(default_factory=list)
    watched: list[Path] = field(default_factory=list)
    started: bool = False

    def count(self, kind: str, repo: Any = None) -> int:
        return sum(1 for k, r in self.events if k == kind and (repo is None or r == str(repo)))

    def total(self) -> int:
        return len(self.events)

    def mark(self) -> int:
        return len(self.events)

    def since(self, mark: int, kind: str | None = None, repo: Any = None) -> int:
        return sum(
            1
            for k, r in self.events[mark:]
            if (kind is None or k == kind) and (repo is None or r == str(repo))
        )

    def _note(self, kind: str, repo: Any) -> None:
        self.events.append((kind, str(repo)))

    def install(self, monkeypatch: Any, *, watch: list[Path] | None = None) -> Observer:
        import atlas_aci.query.context as context_module
        import atlas_aci.query.impact as impact_module
        import atlas_aci.query.paging as paging_module
        import atlas_aci.server as server_module
        import atlas_aci.tools.graph_query as graph_module
        from atlas_aci.codegraph import CodeGraph
        from atlas_aci.memex import Memex
        from atlas_aci.query.authz import ScopedLeaseRegistry
        from atlas_aci.storage.snapshots import SourceLease

        observer = self
        self.watched = list(watch or [])

        original_lease = CodeGraph.reader_lease

        @contextlib.contextmanager
        def reader_lease(code_graph: Any, *args: Any, **kwargs: Any) -> Any:
            with original_lease(code_graph, *args, **kwargs) as reader:
                observer._note("lease", code_graph.repo)
                observer._note("lease_ns", code_graph.atlas_dir)
                yield reader

        monkeypatch.setattr(CodeGraph, "reader_lease", reader_lease)

        original_init = CodeGraph.__init__

        def init(code_graph: Any, repo: Any, *args: Any, **kwargs: Any) -> None:
            original_init(code_graph, repo, *args, **kwargs)
            observer._note("graph_init", code_graph.repo)
            observer._note("graph_init_ns", code_graph.atlas_dir)

        monkeypatch.setattr(CodeGraph, "__init__", init)

        original_source = CodeGraph.source_lease

        @contextlib.contextmanager
        def source_lease(code_graph: Any, snapshot_id: Any) -> Any:
            observer._note("source_lease", code_graph.repo)
            with original_source(code_graph, snapshot_id) as lease:
                yield lease

        monkeypatch.setattr(CodeGraph, "source_lease", source_lease)

        original_span = SourceLease.read_span

        def read_span(lease: Any, *args: Any, **kwargs: Any) -> Any:
            observer._note("read_span", "")
            return original_span(lease, *args, **kwargs)

        monkeypatch.setattr(SourceLease, "read_span", read_span)

        original_memex = Memex.read

        def memex_read(memex: Any, *args: Any, **kwargs: Any) -> Any:
            observer._note("memex_read", getattr(memex, "root", ""))
            return original_memex(memex, *args, **kwargs)

        monkeypatch.setattr(Memex, "read", memex_read)

        for name in ("view_file", "list_dir", "search_text", "search_symbol", "graph_query"):
            self._wrap_tool(monkeypatch, server_module, name)
        self._wrap_tool(monkeypatch, server_module, "test_dry_run")
        for module, attr in (
            (graph_module, "retrieve"),
            (graph_module, "navigate"),
            (context_module, "retrieve"),
            (context_module, "navigate"),
            (paging_module, "navigate"),
            (impact_module, "navigate"),
        ):
            self._wrap_plain(monkeypatch, module, attr)

        original_scope = ScopedLeaseRegistry.scope

        def scope(registry: Any) -> Any:
            from atlas_aci.query.authz import AUTHZ_CONTEXT

            ctx = AUTHZ_CONTEXT.get()
            observer.scopes.append((registry.repository_id, None if ctx is None else ctx))
            return original_scope(registry)

        monkeypatch.setattr(ScopedLeaseRegistry, "scope", scope)

        original_authenticate = ScopedLeaseRegistry.authenticate

        def authenticate(registry: Any, *args: Any, **kwargs: Any) -> Any:
            observer._note("authenticate", registry.repository_id)
            return original_authenticate(registry, *args, **kwargs)

        monkeypatch.setattr(ScopedLeaseRegistry, "authenticate", authenticate)

        for attr in ("read_bytes", "iterdir", "stat"):
            self._wrap_path(monkeypatch, attr)
        self.started = True
        return self

    def _wrap_tool(self, monkeypatch: Any, module: Any, name: str) -> None:
        original = getattr(module, name)
        observer = self

        async def wrapper(arguments: Any, config: Any, *args: Any, **kwargs: Any) -> Any:
            observer._note("tool:" + name, config.repo)
            return await original(arguments, config, *args, **kwargs)

        monkeypatch.setattr(module, name, wrapper)

    def _wrap_plain(self, monkeypatch: Any, module: Any, name: str) -> None:
        original = getattr(module, name)
        observer = self

        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            observer._note(name, "")
            return await original(*args, **kwargs)

        monkeypatch.setattr(module, name, wrapper)

    def _wrap_path(self, monkeypatch: Any, attr: str) -> None:
        original = getattr(Path, attr)
        observer = self

        def wrapper(path: Path, *args: Any, **kwargs: Any) -> Any:
            if observer.watched:
                text = str(path)
                for root in observer.watched:
                    if text == str(root) or text.startswith(str(root) + os.sep):
                        observer._note("path:" + attr, root)
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, attr, wrapper)

    def tool_counts(self, repo: Any = None) -> Counter[str]:
        result: Counter[str] = Counter()
        for kind, where in self.events:
            if kind.startswith("tool:") and (repo is None or where == str(repo)):
                result[kind] += 1
        return result


# ----------------------------------------------------------------------------- fake binaries
def write_fake(directory: Path, name: str, body: str) -> Path:
    path = directory / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return path


def fake_logger(directory: Path, name: str) -> tuple[Path, Path]:
    """A fake executable that appends its argv (one argument per line) to a log, then exits 0."""
    log = directory / f"{name}.log"
    script = (
        f'printf "ARGV-BEGIN\\n" >> "{log}"\n'
        f'for a in "$@"; do printf "%s\\n" "$a" >> "{log}"; done\n'
        f'printf "ARGV-END\\n" >> "{log}"\n'
    )
    return write_fake(directory, name, script), log


def fake_runs(log: Path) -> list[list[str]]:
    if not log.exists():
        return []
    runs: list[list[str]] = []
    current: list[str] = []
    for line in log.read_text().splitlines():
        if line == "ARGV-BEGIN":
            current = []
        elif line == "ARGV-END":
            runs.append(current)
        else:
            current.append(line)
    return runs


# ----------------------------------------------------------------------------- production driver
class _Report:
    """One PASS/FAIL line per check; a check whose prerequisite failed FAILs too."""

    def __init__(self) -> None:
        self.ok: dict[str, bool] = {}
        self.detail: dict[str, str] = {}
        self.failed = 0
        self.passed = 0

    def record(self, name: str, ok: bool, detail: str) -> None:
        self.ok[name] = ok
        self.detail[name] = detail
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


class _Child:
    """A `serve-shared` subprocess; stderr is captured and the port parsed from it."""

    def __init__(self, argv: list[str], env: dict[str, str] | None = None) -> None:
        import test_s39_support as s39

        self.s39 = s39
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
        import threading

        self._ready = threading.Event()
        self._threads = [
            threading.Thread(target=self._pump, args=(self.proc.stderr, self.stderr, True)),
            threading.Thread(target=self._pump, args=(self.proc.stdout, self.stdout, False)),
        ]
        for thread in self._threads:
            thread.daemon = True
            thread.start()
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not self._ready.is_set():
            if self.proc.poll() is not None:
                break
            self._ready.wait(0.05)
        if self.port is None:
            code = self.proc.poll()
            self.kill()
            raise AssertionError(
                f"no shared_http_listening line (exit={code}): {self.stderr_text()[-600:]}"
            )

    def _pump(self, stream: Any, sink: bytearray, parse: bool) -> None:
        while chunk := stream.read1(65536):
            if len(sink) < 1024 * 1024:
                sink.extend(chunk)
            if parse and self.port is None:
                for line in bytes(sink).decode("utf-8", errors="replace").splitlines():
                    if "shared_http_listening" in line and (m := self.s39.LISTENING.search(line)):
                        self.port = int(m.group(1))
                        self._ready.set()
                        break

    def stderr_text(self) -> str:
        return bytes(self.stderr).decode("utf-8", errors="replace")

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
        code: int | None = None
        if self.proc.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                self.proc.send_signal(signal.SIGTERM)
            code = self.wait(10)
        else:
            code = self.proc.returncode
        self.kill()
        return code


def serve_shared_argv(config: Path, python: str | None = None) -> list[str]:
    return [
        python or sys.executable,
        "-m",
        "atlas_aci",
        "serve-shared",
        "--shared-config",
        str(config),
    ]


def child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("ATLAS_ACI_")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(extra or {})
    return env


class _Site:
    """What the production driver needs: keys, files and a client factory."""

    def __init__(self, work: Path, repo_a: Path, graphs_a: Path, repo_b: Path, graphs_b: Path):
        self.work = work
        self.keys = Keys()
        self.repos = {"a": (repo_a, graphs_a), "b": (repo_b, graphs_b)}
        self.config_dir = work / "etc"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.config_dir, 0o700)
        self.policy = self.config_dir / "policy.json"
        self.jwks = self.config_dir / "jwks.json"
        self.config = self.config_dir / "shared.json"
        write_private(self.jwks, self.keys.jwks_bytes())
        document = {
            "format": "atlas-aci-shared.v1",
            "listen": {"port": 0, "max_inflight": 4},
            "identity": {
                "issuer": ISSUER,
                "jwks_file": str(self.jwks),
                "resource_base_url": BASE,
                "scopes_supported": ["atlas.read"],
            },
            "policy": {"file": str(self.policy), "reload_interval_seconds": 0},
            "limits": {"max_calls_per_minute": 0},
            "repositories": [
                {
                    "id": identifier,
                    "repo": str(paths[0]),
                    "graph_root": str(paths[1]),
                    "memex_root": str(work / "memex" / identifier),
                }
                for identifier, paths in self.repos.items()
            ],
        }
        write_private(self.config, json.dumps(document))
        self.set_policy(
            [
                grant("p1", "a", ["graph_query", "view_file"]),
                grant("p1", "b"),
                grant("p2", "b"),
            ]
        )

    def set_policy(self, grants: list[dict[str, Any]]) -> None:
        write_private(self.policy, json.dumps(policy_document(grants)))


async def _production(
    work: Path, repo_a: Path, graphs_a: Path, repo_b: Path, graphs_b: Path, negative: bool = False
) -> int:
    import test_s39_support as s39

    report = _Report()
    site = _Site(work, repo_a, graphs_a, repo_b, graphs_b)
    state: dict[str, Any] = {}

    async def start() -> str:
        child = _Child(serve_shared_argv(site.config), env=child_env())
        state["child"] = child
        assert child.port is not None
        state["http"] = SharedClientLite(child.port, site.keys)
        return f"shared_http_listening on 127.0.0.1:{child.port}"

    await report.run("E0 serve-shared starts", [], start)
    if not report.ok["E0 serve-shared starts"]:
        print(f"DRIVER: {report.passed} passed, {report.failed} failed", flush=True)
        return 1
    child = state["child"]
    http: SharedClientLite = state["http"]
    port = child.port
    ready = ["E0 serve-shared starts"]

    async def e1() -> str:
        missing = await http.raw.request(
            "POST",
            "/mcp/a",
            headers=http.raw._rpc_headers(SELECTED, None),
            declared_length=64,
            withhold_body=True,
        )
        assert missing.status == 401, missing.status
        challenge = missing.header("www-authenticate") or ""
        assert "resource_metadata=" in challenge and "error=" not in challenge, challenge
        ok = await http.call("a", "p1", "tools/list")
        assert ok.status == 200 and len(ok.rpc(1)["result"]["tools"]) == 7
        return "no token 401 + resource_metadata before the body; valid token 200"

    await report.run("E1 missing token 401, valid token 200", ready, e1)

    async def e2() -> str:
        keys = site.keys
        bad = {
            "expired": keys.mint("p1", f"{BASE}/a", ttl=-60),
            "alg_none": keys.forge(
                {"alg": "none", "typ": "JWT", "kid": "kid-rsa"},
                json.dumps(keys.claims("p1", f"{BASE}/a")),
                signature=b"",
            ),
            "wrong_aud": keys.mint("p1", f"{BASE}/b"),
        }
        for name, token in bad.items():
            response = await http.raw.request(
                "POST",
                "/mcp/a",
                headers=[
                    *http.raw._rpc_headers(SELECTED, None),
                    ("Authorization", f"Bearer {token}"),
                ],
                declared_length=64,
                withhold_body=True,
            )
            assert response.status == 401, (name, response.status)
            assert 'error="invalid_token"' in (response.header("www-authenticate") or ""), name
        positive = await http.call("a", "p1", "tools/list")
        assert positive.status == 200
        return "expired, alg=none and wrong-audience tokens 401; a valid token 200"

    await report.run("E2 invalid tokens 401", [*ready, "E1 missing token 401, valid token 200"], e2)

    async def e3() -> str:
        forbidden = await http.raw.request(
            "POST",
            "/mcp/b",
            headers=[
                *http.raw._rpc_headers(SELECTED, None),
                ("Authorization", f"Bearer {site.keys.mint('p3', f'{BASE}/b')}"),
            ],
            declared_length=64,
            withhold_body=True,
        )
        unknown = await http.raw.request(
            "POST",
            "/mcp/nope",
            headers=[
                *http.raw._rpc_headers(SELECTED, None),
                ("Authorization", f"Bearer {site.keys.mint('p3', f'{BASE}/nope')}"),
            ],
            declared_length=64,
            withhold_body=True,
        )
        wanted = 200 if negative else 403
        assert forbidden.status == unknown.status == wanted, (forbidden.status, unknown.status)
        assert forbidden.body == unknown.body
        allowed = await http.call("b", "p1", "tools/list")
        assert allowed.status == 200
        return "unauthorized and unknown repository 403 with identical bodies; granted 200"

    await report.run("E3 403 without enumeration", ready, e3)

    async def e4() -> str:
        page = tool_payload(
            await http.call("a", "p1", "tools/call", tool=("graph_query", PAGE_ARGS))
        )
        assert page["status"] == "ok" and page["data"]["next_cursor"]
        cont = page_cursor(page)
        other = tool_payload(await http.call("a", "p2x", "tools/call", tool=("graph_query", cont)))
        assert error_code(other) == "SCOPE_MISMATCH", other
        own = tool_payload(await http.call("a", "p1", "tools/call", tool=("graph_query", cont)))
        assert own["status"] == "ok"
        state["cursor"] = cont
        return "p1 page; another principal's continue SCOPE_MISMATCH; p1's continue ok"

    # p2x is granted `a` below so the cross-principal attempt reaches the registry.
    site.set_policy(
        [
            grant("p1", "a", ["graph_query", "view_file"]),
            grant("p1", "b"),
            grant("p2", "b"),
            grant("p2x", "a", ["graph_query"]),
        ]
    )
    await report.run(
        "E4 cursor bound to the principal", [*ready, "E1 missing token 401, valid token 200"], e4
    )

    async def e5() -> str:
        site.set_policy([grant("p1", "b"), grant("p2", "b"), grant("p2x", "a", ["graph_query"])])
        cont = state["cursor"]
        stale = await http.call("a", "p1", "tools/call", tool=("graph_query", cont), withhold=True)
        assert stale.status == 403, stale.status
        fresh = await http.call(
            "a", "p1", "tools/call", tool=("graph_query", PAGE_ARGS), withhold=True
        )
        assert fresh.status == 403, fresh.status
        still = await http.call("a", "p2x", "tools/call", tool=("graph_query", STATUS_ARGS))
        assert tool_payload(still)["status"] == "ok"
        return "policy swap: p1's continue and page 403; another granted principal still 200"

    await report.run(
        "E5 revocation after a policy swap", [*ready, "E4 cursor bound to the principal"], e5
    )

    async def e6() -> str:
        site.set_policy([grant("p1", "a", ALL_TOOLS), grant("p1", "b"), grant("p2", "b")])
        for name, arguments in (
            ("test_dry_run", {"path": "a.py"}),
            ("memex_read", {"ref": "0" * 64}),
        ):
            payload = tool_payload(await http.call("a", "p1", "tools/call", tool=(name, arguments)))
            assert payload["_isError"] is True and payload["error"] == "FORBIDDEN", payload
        control = tool_payload(
            await http.call("a", "p1", "tools/call", tool=("graph_query", STATUS_ARGS))
        )
        assert control["status"] == "ok"
        return "test_dry_run and memex_read isError FORBIDDEN for a fully granted principal"

    await report.run("E6 legacy tools denied", ready, e6)

    async def e7() -> str:
        document = json.loads(site.config.read_text())
        done = subprocess.run(
            serve_shared_argv(site.config),
            capture_output=True,
            env=child_env({"ATLAS_ACI_ENABLE_TEST_EXECUTION": "1"}),
            timeout=60,
        )
        assert done.returncode == 2 and done.stdout == b"", (done.returncode, done.stdout)
        assert b"test execution is enabled" in done.stderr, done.stderr[-300:]
        assert document["format"] == "atlas-aci-shared.v1"
        return "ATLAS_ACI_ENABLE_TEST_EXECUTION=1 serve-shared exit 2, empty stdout"

    await report.run("E7 test execution refuses serving", [], e7)

    async def e8() -> str:
        tcp = s39.listen_rows(port, "tcp")
        tcp6 = s39.listen_rows(port, "tcp6")
        assert tcp == [s39.expected_loopback_row(port)], tcp
        assert tcp6 == [], tcp6
        wild = json.loads(site.config.read_text())
        wild["listen"]["host"] = "0.0.0.0"
        wild_path = site.config_dir / "wild.json"
        write_private(wild_path, json.dumps(wild))
        done = subprocess.run(
            serve_shared_argv(wild_path), capture_output=True, env=child_env(), timeout=60
        )
        assert done.returncode == 2 and done.stdout == b"", (done.returncode, done.stdout)
        assert b"wildcard_address" in done.stderr, done.stderr[-300:]
        return f"/proc/net/tcp LISTEN {tcp}; tcp6 {tcp6}; listen.host 0.0.0.0 exit 2"

    await report.run("E8 loopback only", ready, e8)

    async def e9() -> str:
        response = await http.raw.request("GET", "/.well-known/oauth-protected-resource/mcp/a")
        assert response.status == 200, response.status
        document = response.json()
        assert document["resource"] == f"{BASE}/a", document
        assert document["authorization_servers"], document
        return "PRM 200 with resource == the route URL"

    await report.run("E9 protected-resource metadata", ready, e9)

    async def e10() -> str:
        started = time.monotonic()
        child.proc.send_signal(signal.SIGTERM)
        code = child.wait(10)
        elapsed = time.monotonic() - started
        child.kill()
        text = child.stderr_text()
        assert code == 0, f"exit {code}"
        assert "shared_http_stopped" in text and "Traceback" not in text, text[-400:]
        assert s39.connection_refused(port)
        return f"SIGTERM exit 0 in {elapsed:.1f}s; shared_http_stopped; connection refused"

    await report.run("E10 SIGTERM stops cleanly", ready, e10)
    child.kill()
    print(f"DRIVER: {report.passed} passed, {report.failed} failed", flush=True)
    return 0 if report.failed == 0 and report.passed >= 11 else 1


class SharedClientLite:
    """The production driver's client: per-route tokens over the S39 raw client."""

    def __init__(self, port: int, keys: Keys) -> None:
        import test_s39_support as s39

        self.raw = s39.RawHttp(LOOPBACK, port)
        self.keys = keys

    async def call(
        self,
        repository: str,
        subject: str,
        method: str,
        *,
        tool: tuple[str, Any] | None = None,
        withhold: bool = False,
    ) -> Any:
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
        if tool is not None:
            message["params"] = {"name": tool[0], "arguments": tool[1]}
        token = self.keys.mint(subject, f"{BASE}/{repository}")
        headers = [*self.raw._rpc_headers(SELECTED, None), ("Authorization", f"Bearer {token}")]
        if withhold:
            return await self.raw.request(
                "POST",
                f"/mcp/{repository}",
                headers=headers,
                declared_length=256,
                withhold_body=True,
            )
        return await self.raw.request(
            "POST", f"/mcp/{repository}", headers=headers, body=json.dumps(message).encode()
        )


def production_main(argv: list[str]) -> int:
    """Run E0-E10 against `atlas-aci serve-shared` inside the production image.

    Keys, the JWKS, the policy and the shared-config are generated under `--work`.
    """
    work = Path(_flag(argv, "--work"))
    paths = [
        Path(_flag(argv, flag))
        for flag in ("--repo-a", "--graph-root-a", "--repo-b", "--graph-root-b")
    ]
    return asyncio.run(_production(work, *paths, negative="--negative" in argv))
