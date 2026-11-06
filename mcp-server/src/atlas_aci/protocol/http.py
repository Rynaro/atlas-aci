"""Loopback Streamable HTTP transport for the read-only query engine (ACI-S39).

One module, the only importer of ``uvicorn``. It serves the same five MCP handlers the stdio
server registers, over POST ``/mcp`` with SSE responses, stateless (a fresh transport per
request), on a loopback address only. Nothing here changes what a tool returns: the handler
bodies call the shared ``build_tool_manifest`` / ``call_tool_result`` / ``read_resource``.

Closed policy, evaluated on the ASGI scope before the request body is read:
peer loopback, Host, Origin, path, method, then in-flight admission. Anything else (413, 406,
415, 400) is the SDK's own answer. A client disconnect cancels the in-flight request (the
per-request reader lease and any child process are released); that is a documented deviation.

The request path performs no Atlas logging and no I/O beyond the ASGI ``send``. Only
``run_http`` logs, twice: ``http_listening`` and ``http_stopped``.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.metadata
import ipaddress
import json
import signal
import socket
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, MutableMapping
from typing import TYPE_CHECKING, Any

import structlog
import uvicorn
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, Tool

from atlas_aci.config import Config
from atlas_aci.protocol.resources import RESOURCE_TEMPLATE, read_resource
from atlas_aci.protocol.revisions import VERIFIED_SDK, negotiate
from atlas_aci.query.leases import LeaseRegistry
from atlas_aci.server import build_server_state, build_tool_manifest, call_tool_result

if TYPE_CHECKING:
    from atlas_aci.codegraph import CodeGraph
    from atlas_aci.enforcement import Enforcement
    from atlas_aci.memex import Memex

log = structlog.get_logger()

ENDPOINT_PATH = "/mcp"
RETRY_AFTER_SECONDS = 1
SHUTDOWN_GRACE_S = 5
KEEP_ALIVE_S = 5
TICKET_SCOPE_KEY = "atlas_aci.inflight_ticket"
REJECTION_REASONS = (
    "peer_not_loopback",
    "host_not_allowed",
    "origin_not_allowed",
    "path_not_found",
    "method_not_allowed",
    "inflight_limit",
)
LOOPBACK_REASONS = (
    "empty_host",
    "wildcard_address",
    "scope_id",
    "unresolvable",
    "non_loopback_address",
    "mixed_resolution",
    "bound_non_loopback",
)
HOSTNAME_MAX_CHARS = 253
LOCALHOST = "localhost"

# reason -> (status, JSON-RPC code, fixed message). No header value is ever echoed.
_REJECTIONS: dict[str, tuple[int, int, str]] = {
    "peer_not_loopback": (403, -32600, "Peer address is not a loopback address."),
    "host_not_allowed": (421, -32600, "Host header is not allowed."),
    "origin_not_allowed": (403, -32600, "Origin header is not allowed."),
    "path_not_found": (404, -32600, "Not found."),
    "method_not_allowed": (405, -32600, "Method not allowed; use POST."),
    "inflight_limit": (503, -32000, "Too many requests in flight; retry later."),
}

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]

_getaddrinfo = socket.getaddrinfo


class LoopbackError(ValueError):
    """The host is not (only) a loopback address. ``str(exc)`` is fixed text naming the reason."""

    reason: str

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"host is not loopback-only (reason: {reason})")


class RefusedError(RuntimeError):
    """A fail-closed refusal with fixed text; the CLI maps it to a usage error (exit 2)."""


class RequestGone(RuntimeError):
    """The HTTP exchange ended before an Atlas handler started; no engine work was done."""


def _unwrap(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> Any:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def resolve_loopback(host: str) -> str:
    """The one loopback literal to bind for ``host``, or ``LoopbackError`` with a closed reason.

    Pure apart from ``_getaddrinfo`` (a module attribute tests patch). Only a literal is ever
    bound, never the name, so DNS cannot change what is listened on after this check.
    """
    if not isinstance(host, str):
        raise LoopbackError("unresolvable")
    if host == "":
        raise LoopbackError("empty_host")
    if len(host) > HOSTNAME_MAX_CHARS or host != host.strip() or any(c.isspace() for c in host):
        raise LoopbackError("unresolvable")
    if "%" in host:
        raise LoopbackError("scope_id")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        address = _unwrap(literal)
        if address.is_unspecified:
            raise LoopbackError("wildcard_address")
        if not address.is_loopback:
            raise LoopbackError("non_loopback_address")
        return str(address)
    try:
        results = _getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError, ValueError):
        raise LoopbackError("unresolvable") from None
    if not results:
        raise LoopbackError("unresolvable")
    loopback: list[Any] = []
    foreign = False
    for entry in results:
        sockaddr = entry[4]
        text = sockaddr[0]
        if "%" in str(text) or (len(sockaddr) > 3 and sockaddr[3]):
            raise LoopbackError("scope_id")
        try:
            address = _unwrap(ipaddress.ip_address(text))
        except ValueError:
            raise LoopbackError("unresolvable") from None
        if address.is_unspecified:
            raise LoopbackError("wildcard_address")
        if address.is_loopback:
            loopback.append(address)
        else:
            foreign = True
    if foreign:
        raise LoopbackError("mixed_resolution" if loopback else "non_loopback_address")
    for address in loopback:
        if address.version == 4:
            return str(address)
    return str(loopback[0])


class Ticket:
    """A refcounted in-flight slot: held by the HTTP exchange and by every Atlas handler for it."""

    __slots__ = ("_gate", "_holds", "released")

    def __init__(self, gate: InflightGate) -> None:
        self._gate = gate
        self._holds = 1
        self.released = False

    def hold(self) -> None:
        """Take one more hold; no engine work may start on a slot that is already gone."""
        if self.released:
            raise RequestGone("The request ended before the handler started.")
        self._holds += 1

    def release(self) -> None:
        """Drop one hold. At zero the slot is returned exactly once; later calls are no-ops."""
        if self.released:
            return
        self._holds -= 1
        if self._holds <= 0:
            self.released = True
            self._gate._free()


class InflightGate:
    """Bounded admission: reject (never queue) when ``limit`` slots are in use."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.active = 0
        self.peak = 0
        self.admitted = 0
        self.rejected = 0

    def try_admit(self) -> Ticket | None:
        # Synchronous on purpose: no await, so the check-and-take is atomic on the loop.
        if self.active >= self.limit:
            self.rejected += 1
            return None
        self.active += 1
        self.admitted += 1
        self.peak = max(self.peak, self.active)
        return Ticket(self)

    def _free(self) -> None:
        self.active -= 1

    def snapshot(self) -> dict[str, int]:
        return {
            "limit": self.limit,
            "active": self.active,
            "peak": self.peak,
            "admitted": self.admitted,
            "rejected": self.rejected,
        }


def _header_values(scope: Scope, name: bytes) -> list[str]:
    return [value.decode("latin-1") for key, value in scope.get("headers", ()) if key == name]


def _bracket(address: str) -> str:
    return f"[{address}]" if ":" in address else address


def _is_loopback_peer(client: Any) -> bool:
    try:
        return bool(_unwrap(ipaddress.ip_address(client[0])).is_loopback)
    except (TypeError, IndexError, KeyError, ValueError):
        return False


class AtlasHttpApp:
    """The ASGI3 application: a closed rejection wrapper in front of the SDK manager."""

    def __init__(
        self,
        config: Config,
        gate: InflightGate,
        server: Server,
        manager: StreamableHTTPSessionManager,
        state: tuple[Enforcement, Memex, CodeGraph],
        owns_state: bool,
        names: set[str],
    ) -> None:
        self.config = config
        self.gate = gate
        self.server = server
        self.manager = manager
        self.state = state
        self.owns_state = owns_state
        self._names = set(names)
        self._origins = frozenset(config.http_allowed_origins)
        self._state_closed = False

    def allow_host_name(self, name: str) -> None:
        """Register a Host name (the bound loopback literal) for both the wrapper and the SDK."""
        name = _bracket(name).lower()
        self._names.add(name)
        settings = self.manager.security_settings
        if settings is not None and f"{name}:*" not in settings.allowed_hosts:
            settings.allowed_hosts.append(f"{name}:*")

    async def _reject(self, send: Send, reason: str) -> None:
        status, code, message = _REJECTIONS[reason]
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "server-error",
                "error": {"code": code, "message": message, "data": {"reason": reason}},
            },
            separators=(",", ":"),
        ).encode("utf-8")
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"connection", b"close"),
        ]
        if reason == "method_not_allowed":
            headers.append((b"allow", b"POST"))
        if reason == "inflight_limit":
            headers.append((b"retry-after", str(RETRY_AFTER_SECONDS).encode("ascii")))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    def _host_allowed(self, scope: Scope) -> bool:
        hosts = _header_values(scope, b"host")
        server = scope.get("server")
        if len(hosts) != 1 or not server:
            return False
        host = hosts[0].lower()
        return any(host == f"{name}:{server[1]}" for name in self._names)

    def _origin_allowed(self, scope: Scope) -> bool:
        origins = _header_values(scope, b"origin")
        if not origins:
            return True
        return len(origins) == 1 and origins[0] in self._origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            raise RuntimeError("Only HTTP scopes are served.")
        if not _is_loopback_peer(scope.get("client")):
            await self._reject(send, "peer_not_loopback")
        elif not self._host_allowed(scope):
            await self._reject(send, "host_not_allowed")
        elif not self._origin_allowed(scope):
            await self._reject(send, "origin_not_allowed")
        elif scope["path"] != ENDPOINT_PATH:
            await self._reject(send, "path_not_found")
        elif scope["method"] != "POST":
            await self._reject(send, "method_not_allowed")
        else:
            ticket = self.gate.try_admit()
            if ticket is None:
                await self._reject(send, "inflight_limit")
                return
            try:
                scope[TICKET_SCOPE_KEY] = ticket
                await self.manager.handle_request(scope, receive, send)
            finally:
                ticket.release()

    @contextlib.asynccontextmanager
    async def running(self) -> AsyncIterator[None]:
        """Run the SDK manager; release the process state afterwards when this app owns it."""
        try:
            async with self.manager.run():
                yield
        except BaseExceptionGroup as group:
            # The SDK's task group wraps whatever the body raised; hand back the one real error
            # so callers (the CLI's OSError/ValueError mapping) see an ordinary exception.
            if len(group.exceptions) != 1:
                raise
            raise group.exceptions[0] from None
        finally:
            if self.owns_state:
                await self.aclose_state()

    async def aclose_state(self) -> None:
        """Mirror ``run_stdio``'s teardown. Idempotent."""
        if self._state_closed:
            return
        self._state_closed = True
        _enforcement, _memex, code_graph = self.state
        try:
            registry = getattr(code_graph, "_lease_registry", None)
            if registry is not None:
                await registry.aclose()
        finally:
            code_graph.close()


def build_app(
    config: Config, state: tuple[Enforcement, Memex, CodeGraph] | None = None
) -> AtlasHttpApp:
    """Wire the five stdio handlers onto a stateless Streamable HTTP manager."""
    owns_state = state is None
    enforcement, memex, code_graph = state if state is not None else build_server_state(config)
    try:
        gate = InflightGate(config.http_max_inflight)
        server: Server = Server("atlas-aci")

        def ticket() -> Ticket:
            try:
                request = server.request_context.request
            except LookupError:
                raise RuntimeError("No request context.") from None
            found = request.scope.get(TICKET_SCOPE_KEY) if request is not None else None
            if not isinstance(found, Ticket):
                raise RuntimeError("Request has no in-flight ticket.")
            return found

        @contextlib.contextmanager
        def admitted() -> Iterator[None]:
            # hold(); try: ...: nothing awaits in between, so a hold is always released.
            held = ticket()
            held.hold()
            try:
                yield
            finally:
                held.release()

        def session_version() -> str:
            request = server.request_context.request
            if request is None:
                raise RuntimeError("No HTTP request.")
            header = request.headers.get("mcp-protocol-version", types.DEFAULT_NEGOTIATED_VERSION)
            return negotiate(header).revision()

        @server.list_tools()
        async def _list_tools() -> list[Tool]:
            with admitted():
                return [Tool(**tool) for tool in build_tool_manifest(config, session_version())]

        @server.call_tool(validate_input=False)
        async def _call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
            with admitted():
                return await call_tool_result(
                    name,
                    arguments,
                    config,
                    enforcement,
                    memex,
                    code_graph,
                    protocol_version=session_version(),
                )

        @server.list_resources()
        async def _list_resources():  # type: ignore[no-untyped-def]
            with admitted():
                return []

        @server.list_resource_templates()
        async def _list_resource_templates():  # type: ignore[no-untyped-def]
            with admitted():
                return [
                    types.ResourceTemplate(
                        name="ATLAS leased source span",
                        uriTemplate=RESOURCE_TEMPLATE,
                        mimeType="application/octet-stream",
                    )
                ]

        @server.read_resource()
        async def _read_resource(uri):  # type: ignore[no-untyped-def]
            with admitted():
                registry = getattr(code_graph, "_lease_registry", None)
                if registry is None:
                    registry = code_graph._lease_registry = LeaseRegistry(config, code_graph)
                return await read_resource(registry, str(uri), enforcement)

        names = {LOCALHOST}
        host = config.http_host
        literal = host[1:-1] if host.startswith("[") and host.endswith("]") else host
        try:
            names.add(_bracket(str(_unwrap(ipaddress.ip_address(literal)))))
        except ValueError:
            names.add(host.lower())
        manager = StreamableHTTPSessionManager(
            server,
            stateless=True,
            json_response=False,
            event_store=None,
            security_settings=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=[f"{name}:*" for name in sorted(names)],
                allowed_origins=list(config.http_allowed_origins),
            ),
            max_request_body_size=config.http_max_body_bytes,
        )
        return AtlasHttpApp(
            config, gate, server, manager, (enforcement, memex, code_graph), owns_state, names
        )
    except BaseException:
        if owns_state:
            code_graph.close()
        raise


class AtlasUvicornServer(uvicorn.Server):
    """uvicorn's server with one change: signals drain and stop; they are never re-raised."""

    def __init__(self, config: uvicorn.Config, *, install_signals: bool = True) -> None:
        super().__init__(config)
        self._install_signals = install_signals

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        if not self._install_signals or threading.current_thread() is not threading.main_thread():
            yield
            return
        loop = asyncio.get_running_loop()
        signals = (signal.SIGINT, signal.SIGTERM)
        originals = {sig: signal.getsignal(sig) for sig in signals}

        def release() -> None:
            for sig in signals:
                with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
                    loop.remove_signal_handler(sig)
                if originals[sig] is not None:  # back to the disposition we found
                    with contextlib.suppress(ValueError, OSError):
                        signal.signal(sig, originals[sig])  # type: ignore[arg-type]

        def on_signal(sig: int) -> None:
            # handle_exit resolves to the sse_starlette wrapper: SSE streams drain too. The
            # handlers are removed at once, so a second signal takes the default disposition.
            self.handle_exit(sig, None)
            release()

        try:
            for sig in signals:
                loop.add_signal_handler(sig, on_signal, sig)
        except (NotImplementedError, RuntimeError, ValueError):
            release()
            yield
            return
        try:
            yield
        finally:
            release()


def uvicorn_config(app: AtlasHttpApp, host_ip: str, port: int) -> uvicorn.Config:
    """The only uvicorn configuration used. Refuses anything but a loopback literal."""
    try:
        address = ipaddress.ip_address(host_ip)
    except ValueError:
        raise LoopbackError("non_loopback_address") from None
    mapped = isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None
    # Only a canonical, scope-free, unmapped loopback literal: no interpreter-dependent forms.
    if mapped or "%" in host_ip or not address.is_loopback or str(address) != host_ip:
        raise LoopbackError("non_loopback_address")
    app.allow_host_name(host_ip)
    config = uvicorn.Config(
        app,  # type: ignore[arg-type]
        host=host_ip,
        port=port,
        interface="asgi3",
        lifespan="off",
        proxy_headers=False,
        server_header=False,
        log_config=None,
        access_log=False,
        ws="none",
        http="h11",
        loop="asyncio",
        workers=1,
        timeout_graceful_shutdown=SHUTDOWN_GRACE_S,
        timeout_keep_alive=KEEP_ALIVE_S,
    )
    if config.fd is not None or config.uds is not None:
        raise LoopbackError("non_loopback_address")
    return config


def _is_loopback_literal(text: str) -> bool:
    try:
        return bool(_unwrap(ipaddress.ip_address(text)).is_loopback)
    except ValueError:
        return False


def _listening_addresses(server: uvicorn.Server) -> list[tuple[str, int]]:
    return [
        (str(sock.getsockname()[0]), int(sock.getsockname()[1]))
        for listener in server.servers
        for sock in listener.sockets
    ]


async def _serve(server: uvicorn.Server) -> None:
    # uvicorn exits through SystemExit when it cannot bind; asyncio would re-raise that out of
    # the loop. Convert it so run_http fails with an ordinary exception and unwinds.
    try:
        await server.serve()
    except SystemExit:
        raise OSError("The HTTP listener could not start (is the port already in use?)") from None


async def _stop(server: uvicorn.Server, task: asyncio.Future[None]) -> None:
    server.should_exit = True
    if task.done():
        with contextlib.suppress(BaseException):
            task.result()
        return
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=SHUTDOWN_GRACE_S + 5)
    except Exception:
        task.cancel()
        with contextlib.suppress(BaseException):
            await task
    except asyncio.CancelledError:
        task.cancel()
        raise


async def run_http(
    config: Config,
    *,
    on_listening: Callable[[list[tuple[str, int]]], None] | None = None,
    install_signals: bool = True,
) -> None:
    """Serve until stopped. Raises RefusedError, LoopbackError or OSError before/while binding."""
    if importlib.metadata.version(VERIFIED_SDK[0]) != VERIFIED_SDK[1]:
        raise RefusedError("HTTP serving requires the verified mcp SDK version.")
    if config.test_execution_enabled:
        raise RefusedError("HTTP serving is not available while test execution is enabled.")
    host_ip = resolve_loopback(config.http_host)
    app = build_app(config)
    try:
        server = AtlasUvicornServer(
            uvicorn_config(app, host_ip, config.http_port), install_signals=install_signals
        )
        async with app.running():
            task: asyncio.Future[None] = asyncio.ensure_future(_serve(server))
            try:
                while not server.started:
                    if task.done():
                        await task
                        raise OSError("The HTTP listener stopped before it started.")
                    await asyncio.sleep(0.01)
                addresses = _listening_addresses(server)
                if not addresses or not all(_is_loopback_literal(a) for a, _ in addresses):
                    raise LoopbackError("bound_non_loopback")
                log.info(
                    "http_listening",
                    host=addresses[0][0],
                    port=addresses[0][1],
                    path=ENDPOINT_PATH,
                    max_inflight=config.http_max_inflight,
                    max_body_bytes=config.http_max_body_bytes,
                )
                if on_listening is not None:
                    on_listening(addresses)
                await task
            finally:
                await _stop(server, task)
    finally:
        await app.aclose_state()
        log.info(
            "http_stopped", **{k: app.gate.snapshot()[k] for k in ("admitted", "rejected", "peak")}
        )
