"""Shared multi-principal serving over loopback Streamable HTTP (ACI-S40).

One loopback listener serves several operator-declared repositories on ``/mcp/<repository_id>``.
Every repository has its own derived ``Config``, server state and S39 ``build_app`` instance; the
S39 transport (``protocol/http.py``) and the engines are used unchanged. Authorization is added in
three independent layers:

1. ``SharedRouter`` (an outer ASGI application) decides peer, Host, Origin, route, method, bearer
   credentials, policy and repository authorization from the ASGI scope, before any request body
   is read and before any repository, graph or source access;
2. ``install_guards`` wraps every MCP request handler of each repository's server so that a
   request is classified and decided again, and the authorization context is set only inside the
   handler;
3. ``SharedEnforcement`` and ``ScopedLeaseRegistry`` (``authz.py``) re-check the principal at the
   first call of every dispatch and bind every lease, cursor and resource to the principal's
   authorization scope.

The request path performs no Atlas logging. ``run_shared`` logs ``shared_http_listening`` and
``shared_http_stopped`` only; the policy store logs one warning when the policy turns invalid.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import importlib.metadata
import json
import re
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import structlog
from mcp import McpError, types
from mcp.types import ErrorData

from atlas_aci.codegraph import CodeGraph
from atlas_aci.config import Config
from atlas_aci.enforcement import Enforcement
from atlas_aci.memex import Memex
from atlas_aci.protocol import http
from atlas_aci.protocol.graph import RESERVED
from atlas_aci.protocol.http import (
    AtlasHttpApp,
    AtlasUvicornServer,
    InflightGate,
    LoopbackError,
    RefusedError,
    resolve_loopback,
    uvicorn_config,
)
from atlas_aci.protocol.resources import error_data
from atlas_aci.protocol.results import STRUCTURED_VERSIONS, fixed_result
from atlas_aci.protocol.revisions import VERIFIED_SDK, negotiate
from atlas_aci.query.authn import (
    AuthnError,
    JwksError,
    JwtVerifier,
    RuntimeVersionError,
    check_runtime_versions,
    load_jwks,
    metadata_body,
    resource_url,
    www_authenticate,
)
from atlas_aci.query.authz import (
    AUTHZ_CONTEXT,
    AccessRequest,
    AuthzContext,
    Credential,
    FilePolicySource,
    FileRejected,
    PolicyError,
    PolicySource,
    PolicyStore,
    PrincipalInflight,
    PrincipalLimiter,
    Registration,
    ScopedLeaseRegistry,
    SharedEnforcement,
    classify,
    decide,
    grant_for,
    parse_policy,
    purge_all,
    read_hygienic,
    strict_json,
)

log = structlog.get_logger()

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]

SHARED_FORMAT = "atlas-aci-shared.v1"
SHARED_CONFIG_MAX_BYTES = 1024 * 1024
MAX_REPOSITORIES = 64
PRINCIPAL_SCOPE_KEY = "atlas_aci.shared_principal"
LABEL = r"[a-z0-9][a-z0-9._-]{0,63}"
REPOSITORY_LABEL = re.compile(LABEL, re.ASCII)
ROUTE = re.compile(rf"/mcp/({LABEL})", re.ASCII)
METADATA_ROUTE = re.compile(rf"/\.well-known/oauth-protected-resource/mcp/({LABEL})", re.ASCII)
ISSUER = re.compile(r"https://[^\s#?]+", re.ASCII)
RESOURCE_BASE = re.compile(r"https://[A-Za-z0-9.-]+(:[0-9]{1,5})?/mcp", re.ASCII)
SCOPE_TOKEN = re.compile(r"[\x21\x23-\x5b\x5d-\x7e]{1,128}")
MIB = 1024 * 1024


def _config_default(name: str) -> int:
    value = Config.__dataclass_fields__[name].default
    if not isinstance(value, int):
        raise RuntimeError("Config default is not an integer.")
    return value


DEFAULT_TOTAL_LEASE_STATE = _config_default("max_total_lease_state_bytes")
DEFAULT_LEASE_STATE = _config_default("max_lease_state_bytes")
SHARED_CONFIG_REASONS = (
    "unreadable",
    "too_large",
    "unsafe_file",
    "not_json",
    "unknown_field",
    "missing_field",
    "wrong_type",
    "out_of_range",
    "bad_format",
    "duplicate_id",
    "not_absolute",
    "not_directory",
    "memex_inside_repository",
    "duplicate_namespace",
    "duplicate_memex",
    "repository_nested",
    "graph_root_inside_repository",
    "root_nested",
    "before_unknown",
    "before_self",
    "capacity_exceeded",
    "jwks_invalid",
    "policy_invalid",
    "test_execution_enabled",
)
SHARED_REJECTION_REASONS = (
    "route_not_found",
    "metadata_method_not_allowed",
    "credentials_missing",
    "credentials_invalid",
    "repository_forbidden",
    "principal_inflight_limit",
)
_REJECTIONS: dict[str, tuple[int, int, str]] = {
    "route_not_found": (404, -32600, "Not found."),
    "metadata_method_not_allowed": (405, -32600, "Method not allowed; use GET."),
    "credentials_missing": (401, -32600, "Authentication required."),
    "credentials_invalid": (401, -32600, "Invalid credentials."),
    "repository_forbidden": (403, -32600, "Access to this repository is not allowed."),
    "principal_inflight_limit": (
        503,
        -32000,
        "Too many requests in flight for this principal; retry later.",
    ),
}
GUARD_ATTRIBUTE = "__atlas_guard__"
HANDLER_TYPES = (
    types.PingRequest,
    types.ListToolsRequest,
    types.CallToolRequest,
    types.ListResourcesRequest,
    types.ListResourceTemplatesRequest,
    types.ReadResourceRequest,
)
GUARD_KINDS: dict[type, str] = {
    types.ListToolsRequest: "tools/list",
    types.CallToolRequest: "tools/call",
    types.ListResourcesRequest: "resources/list",
    types.ListResourceTemplatesRequest: "resources/templates/list",
    types.ReadResourceRequest: "resources/read",
}
UNAUTHORIZED_TEXT = "Request not authorized."


# ----------------------------------------------------------------------------- shared config
class SharedConfigError(ValueError):
    """An invalid shared configuration. ``str`` is fixed text; values are never echoed."""

    def __init__(self, reason: str, field: str = "") -> None:
        self.reason = reason
        self.field = field
        super().__init__(f"invalid shared configuration ({reason} at {field or '/'})")


class DeploymentError(RuntimeError):
    """A registered repository is not servable. ``str`` is fixed text without paths."""

    def __init__(self, repository_id: str) -> None:
        self.repository_id = repository_id
        super().__init__(f"repository {repository_id}: graph index is unavailable or misconfigured")


@dataclass(frozen=True)
class Listen:
    host: str
    port: int
    max_inflight: int
    max_body_bytes: int
    allowed_origins: tuple[str, ...]
    max_inflight_per_principal: int


@dataclass(frozen=True)
class Identity:
    issuer: str
    jwks_file: Path
    resource_base_url: str
    leeway_seconds: int
    max_token_bytes: int
    max_token_lifetime_seconds: int
    scopes_supported: tuple[str, ...]


@dataclass(frozen=True)
class PolicyRef:
    file: Path
    reload_interval_seconds: int


@dataclass(frozen=True)
class Limits:
    max_calls_per_minute: int
    max_calls_per_principal_per_minute: int
    max_leases_per_principal: int
    max_graph_leases: int
    max_total_lease_state_bytes: int
    max_records: int


@dataclass(frozen=True)
class Entry:
    repository_id: str
    repo: Path
    graph_root: Path
    memex_root: Path
    before_id: str | None


@dataclass(frozen=True)
class Deployment:
    listen: Listen
    identity: Identity
    policy: PolicyRef
    limits: Limits
    entries: tuple[Entry, ...]
    jwks_raw: bytes
    configs: Mapping[str, Config]
    registrations: Mapping[str, Registration]
    host_ip: str


def _closed(
    value: Any, required: tuple[str, ...], optional: tuple[str, ...], pointer: str
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SharedConfigError("wrong_type", pointer)
    for key in value:
        if key not in required and key not in optional:
            raise SharedConfigError("unknown_field", f"{pointer}/{key}")
    for key in required:
        if key not in value:
            raise SharedConfigError("missing_field", f"{pointer}/{key}")
    return value


def _integer(
    section: dict[str, Any], name: str, default: int, low: int, high: int, where: str
) -> int:
    value = section.get(name, default)
    if type(value) is not int:
        raise SharedConfigError("wrong_type", f"{where}/{name}")
    if not low <= value <= high:
        raise SharedConfigError("out_of_range", f"{where}/{name}")
    return value


def _text(section: dict[str, Any], name: str, where: str) -> str:
    value = section[name]
    if not isinstance(value, str):
        raise SharedConfigError("wrong_type", f"{where}/{name}")
    return value


def _absolute(text: str, where: str) -> Path:
    if not text or "\x00" in text or not Path(text).is_absolute():
        raise SharedConfigError("not_absolute", where)
    return Path(text)


def _within(root: Path, path: Path) -> bool:
    return path == root or root in path.parents


def namespace_of(repo: Path, graph_root: Path) -> Path:
    """The graph namespace ``CodeGraph`` derives for (repo, graph_root)."""
    identity = hashlib.sha256(str(repo).encode("utf-8")).hexdigest()
    return (graph_root / "repos" / identity).resolve()


def load_shared_config(path: Path, *, environ: Mapping[str, str]) -> Deployment:
    """Load and validate a shared-config document; errors carry a reason and a field."""
    if environ.get("ATLAS_ACI_ENABLE_TEST_EXECUTION") == "1":
        raise SharedConfigError("test_execution_enabled")
    try:
        raw = read_hygienic(Path(path), SHARED_CONFIG_MAX_BYTES)
    except FileRejected as exc:
        raise SharedConfigError(exc.reason) from None
    try:
        document = strict_json(raw)
    except ValueError:
        raise SharedConfigError("not_json") from None
    top = _closed(
        document,
        ("format", "identity", "policy", "repositories"),
        ("listen", "limits"),
        "",
    )
    if top["format"] != SHARED_FORMAT:
        raise SharedConfigError("bad_format", "/format")

    listen_raw = _closed(
        top.get("listen", {}),
        (),
        (
            "host",
            "port",
            "max_inflight",
            "max_body_bytes",
            "allowed_origins",
            "max_inflight_per_principal",
        ),
        "/listen",
    )
    host = listen_raw.get("host", "127.0.0.1")
    if not isinstance(host, str):
        raise SharedConfigError("wrong_type", "/listen/host")
    port = _integer(listen_raw, "port", 8765, 0, 65535, "/listen")
    if 1 <= port <= 1023:
        raise SharedConfigError("out_of_range", "/listen/port")  # clients must send Host:port
    max_inflight = _integer(listen_raw, "max_inflight", 8, 1, 256, "/listen")
    max_body = _integer(listen_raw, "max_body_bytes", MIB, 1024, 16 * MIB, "/listen")
    per_principal = _integer(
        listen_raw,
        "max_inflight_per_principal",
        max(1, max_inflight // 2),
        1,
        max_inflight,
        "/listen",
    )
    origins_raw = listen_raw.get("allowed_origins", [])
    if not isinstance(origins_raw, list) or not all(isinstance(o, str) for o in origins_raw):
        raise SharedConfigError("wrong_type", "/listen/allowed_origins")
    origins = tuple(origins_raw)

    identity_raw = _closed(
        top["identity"],
        ("issuer", "jwks_file", "resource_base_url"),
        ("leeway_seconds", "max_token_bytes", "max_token_lifetime_seconds", "scopes_supported"),
        "/identity",
    )
    issuer = _text(identity_raw, "issuer", "/identity")
    if len(issuer) > 2048 or ISSUER.fullmatch(issuer) is None:
        raise SharedConfigError("bad_format", "/identity/issuer")
    base = _text(identity_raw, "resource_base_url", "/identity")
    if RESOURCE_BASE.fullmatch(base) is None:
        raise SharedConfigError("bad_format", "/identity/resource_base_url")
    jwks_file = _absolute(_text(identity_raw, "jwks_file", "/identity"), "/identity/jwks_file")
    leeway = _integer(identity_raw, "leeway_seconds", 30, 0, 60, "/identity")
    max_token = _integer(identity_raw, "max_token_bytes", 8192, 1024, 16384, "/identity")
    lifetime = _integer(identity_raw, "max_token_lifetime_seconds", 86400, 60, 604800, "/identity")
    scopes_raw = identity_raw.get("scopes_supported", [])
    if not isinstance(scopes_raw, list) or len(scopes_raw) > 32:
        raise SharedConfigError("wrong_type", "/identity/scopes_supported")
    for item in scopes_raw:
        if not isinstance(item, str) or SCOPE_TOKEN.fullmatch(item) is None:
            raise SharedConfigError("bad_format", "/identity/scopes_supported")

    policy_raw = _closed(top["policy"], ("file",), ("reload_interval_seconds",), "/policy")
    policy_file = _absolute(_text(policy_raw, "file", "/policy"), "/policy/file")
    reload_interval = _integer(policy_raw, "reload_interval_seconds", 2, 0, 60, "/policy")

    limits_raw = _closed(
        top.get("limits", {}),
        (),
        (
            "max_calls_per_minute",
            "max_calls_per_principal_per_minute",
            "max_leases_per_principal",
            "max_graph_leases",
            "max_total_lease_state_bytes",
            "max_records",
        ),
        "/limits",
    )
    max_graph_leases = _integer(limits_raw, "max_graph_leases", 4, 1, 64, "/limits")
    limits = Limits(
        max_calls_per_minute=_integer(
            limits_raw, "max_calls_per_minute", 200, 0, 100000, "/limits"
        ),
        max_calls_per_principal_per_minute=_integer(
            limits_raw, "max_calls_per_principal_per_minute", 120, 0, 100000, "/limits"
        ),
        max_leases_per_principal=_integer(
            limits_raw, "max_leases_per_principal", 1, 1, 64, "/limits"
        ),
        max_graph_leases=max_graph_leases,
        max_total_lease_state_bytes=_integer(
            limits_raw,
            "max_total_lease_state_bytes",
            1024 * MIB,
            16 * MIB,
            16 * 1024 * MIB,
            "/limits",
        ),
        max_records=_integer(limits_raw, "max_records", 1024, 0, 65536, "/limits"),
    )

    repos_raw = top["repositories"]
    if not isinstance(repos_raw, list):
        raise SharedConfigError("wrong_type", "/repositories")
    if not 1 <= len(repos_raw) <= MAX_REPOSITORIES:
        raise SharedConfigError("out_of_range", "/repositories")
    entries: list[Entry] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(repos_raw):
        where = f"/repositories/{index}"
        row = _closed(item, ("id", "repo", "graph_root", "memex_root"), ("before_repo_id",), where)
        identifier = _text(row, "id", where)
        if REPOSITORY_LABEL.fullmatch(identifier) is None:
            raise SharedConfigError("bad_format", f"{where}/id")
        if identifier in seen_ids:
            raise SharedConfigError("duplicate_id", f"{where}/id")
        seen_ids.add(identifier)
        repo = _absolute(_text(row, "repo", where), f"{where}/repo")
        graph_root = _absolute(_text(row, "graph_root", where), f"{where}/graph_root")
        memex_root = _absolute(_text(row, "memex_root", where), f"{where}/memex_root")
        before = row.get("before_repo_id")
        if "before_repo_id" in row and not isinstance(before, str):
            raise SharedConfigError("wrong_type", f"{where}/before_repo_id")
        try:
            resolved_repo = repo.resolve()
            is_dir = resolved_repo.is_dir()
        except (OSError, RuntimeError):
            raise SharedConfigError("not_directory", f"{where}/repo") from None
        if not is_dir:
            raise SharedConfigError("not_directory", f"{where}/repo")
        entries.append(
            Entry(
                identifier,
                resolved_repo,
                graph_root.resolve(),
                memex_root.resolve(),
                before,
            )
        )
    by_id = {e.repository_id: e for e in entries}
    for index, entry in enumerate(entries):
        if entry.before_id is not None:
            if entry.before_id == entry.repository_id:
                raise SharedConfigError("before_self", f"/repositories/{index}/before_repo_id")
            if entry.before_id not in by_id:
                raise SharedConfigError("before_unknown", f"/repositories/{index}/before_repo_id")
    _check_overlaps(entries, by_id)

    if 2 * limits.max_leases_per_principal > limits.max_graph_leases:
        raise SharedConfigError("capacity_exceeded", "/limits/max_leases_per_principal")
    share = min(DEFAULT_TOTAL_LEASE_STATE, limits.max_total_lease_state_bytes // len(entries))
    if share < DEFAULT_LEASE_STATE:
        raise SharedConfigError("capacity_exceeded", "/limits/max_total_lease_state_bytes")

    try:
        host_ip = resolve_loopback(host)
    except LoopbackError:
        raise
    registrations: dict[str, Registration] = {}
    configs: dict[str, Config] = {}
    for entry in sorted(entries, key=lambda e: e.repository_id):
        registrations[entry.repository_id] = Registration(
            entry.repository_id,
            str(entry.repo),
            str(namespace_of(entry.repo, entry.graph_root)),
            entry.before_id,
        )
        before_entry = by_id[entry.before_id] if entry.before_id is not None else None
        try:
            configs[entry.repository_id] = Config(
                repo=entry.repo,
                memex_root=entry.memex_root,
                graph_root=entry.graph_root,
                before_repo=before_entry.repo if before_entry else None,
                before_graph_root=before_entry.graph_root if before_entry else None,
                max_calls_per_minute=limits.max_calls_per_minute,
                test_execution_enabled=False,
                max_graph_leases=limits.max_graph_leases,
                max_total_lease_state_bytes=share,
                http_host=host_ip,
                http_port=port,
                http_max_inflight=max_inflight,
                http_max_body_bytes=max_body,
                http_allowed_origins=origins,
            )
        except ValueError:
            raise SharedConfigError("out_of_range", "/listen") from None

    try:
        jwks_raw = read_hygienic(jwks_file, 64 * 1024)
        load_jwks(jwks_raw)
    except (FileRejected, JwksError):
        raise SharedConfigError("jwks_invalid", "/identity/jwks_file") from None
    try:
        parse_policy(
            read_hygienic(policy_file, 1024 * 1024), issuer=issuer, registrations=registrations
        )
    except (FileRejected, PolicyError):
        raise SharedConfigError("policy_invalid", "/policy/file") from None

    return Deployment(
        listen=Listen(host, port, max_inflight, max_body, origins, per_principal),
        identity=Identity(issuer, jwks_file, base, leeway, max_token, lifetime, tuple(scopes_raw)),
        policy=PolicyRef(policy_file, reload_interval),
        limits=limits,
        entries=tuple(entries),
        jwks_raw=jwks_raw,
        configs=configs,
        registrations=registrations,
        host_ip=host_ip,
    )


def _check_overlaps(entries: list[Entry], by_id: dict[str, Entry]) -> None:
    """Reject repositories and storage roots that could expose one another's files.

    A principal granted any id of a path can read that path's live tree, so two ids may share a
    path only as an explicit ``before_repo_id`` pair with distinct graph roots.
    """
    for i, one in enumerate(entries):
        for j in range(i + 1, len(entries)):
            two = entries[j]
            if one.repo == two.repo:
                linked = one.before_id == two.repository_id or two.before_id == one.repository_id
                if linked and one.graph_root != two.graph_root:
                    continue
                if one.graph_root == two.graph_root:
                    raise SharedConfigError("duplicate_namespace", f"/repositories/{j}/graph_root")
                raise SharedConfigError("repository_nested", f"/repositories/{j}/repo")
            if _within(one.repo, two.repo) or _within(two.repo, one.repo):
                raise SharedConfigError("repository_nested", f"/repositories/{j}/repo")
    for i, entry in enumerate(entries):
        for other in entries:
            if _within(other.repo, entry.graph_root):
                raise SharedConfigError(
                    "graph_root_inside_repository", f"/repositories/{i}/graph_root"
                )
            if _within(other.repo, entry.memex_root):
                raise SharedConfigError("memex_inside_repository", f"/repositories/{i}/memex_root")
            for root, name in ((entry.graph_root, "graph_root"), (entry.memex_root, "memex_root")):
                if _within(root, other.repo) and root != other.repo:
                    # a served repository inside a storage root: the root would contain it
                    raise SharedConfigError("root_nested", f"/repositories/{i}/{name}")
    memex_seen: set[Path] = set()
    for i, entry in enumerate(entries):
        if entry.memex_root in memex_seen:
            raise SharedConfigError("duplicate_memex", f"/repositories/{i}/memex_root")
        memex_seen.add(entry.memex_root)
    for i, entry in enumerate(entries):
        for j, other in enumerate(entries):
            if i == j:
                continue
            for mine, theirs, name in (
                (entry.memex_root, other.memex_root, "memex_root"),
                (entry.memex_root, other.graph_root, "memex_root"),
                (entry.graph_root, other.memex_root, "graph_root"),
            ):
                if _within(theirs, mine) or _within(mine, theirs):
                    raise SharedConfigError("root_nested", f"/repositories/{i}/{name}")
            if entry.graph_root != other.graph_root and (
                _within(other.graph_root, entry.graph_root)
                or _within(entry.graph_root, other.graph_root)
            ):
                raise SharedConfigError("root_nested", f"/repositories/{i}/graph_root")
        if _within(entry.graph_root, entry.memex_root) or _within(
            entry.memex_root, entry.graph_root
        ):
            raise SharedConfigError("root_nested", f"/repositories/{i}/memex_root")


def validate_deployment(deployment: Deployment) -> None:
    """Startup check per repository (a read-only CodeGraph per side); fixed text on failure."""
    from atlas_aci.query.impact import validate_configuration

    for identifier in sorted(deployment.configs):
        try:
            validate_configuration(deployment.configs[identifier])
        except (ValueError, OSError):
            raise DeploymentError(identifier) from None


# ----------------------------------------------------------------------------- guards
class Guard:
    """Per-repository guard state: denial counters (never on the wire)."""

    def __init__(self, repository_id: str) -> None:
        self.repository_id = repository_id
        self.denied: Counter[str] = Counter()
        self.allowed = 0


def _protocol_version(request: Any) -> str:
    header = request.headers.get("mcp-protocol-version", types.DEFAULT_NEGOTIATED_VERSION)
    return str(negotiate(header).revision())


def _denial(kind: str, req: Any, request: Any) -> types.ServerResult:
    """The wire answer for a denied request: fixed bytes, no reason, no echoed value."""
    if kind == "tools/call":
        name = req.params.name
        arguments = req.params.arguments or {}
        mode = (
            (
                "structured"
                if isinstance(arguments, dict) and any(key in arguments for key in RESERVED)
                else "legacy"
            )
            if name == "graph_query"
            else None
        )
        version = (
            _protocol_version(request) if request is not None else types.DEFAULT_NEGOTIATED_VERSION
        )
        structured = name == "graph_query" and version in STRUCTURED_VERSIONS
        op = arguments.get("op") if isinstance(arguments, dict) else None
        operation = (
            str(op)
            if isinstance(op, str)
            and op
            in {
                "status",
                "neighborhood",
                "path",
                "page",
                "continue",
                "retrieve",
                "context",
                "impact",
            }
            else "status"
        )
        return types.ServerResult(
            fixed_result(
                "FORBIDDEN", mode=mode or "legacy", structured=structured, operation=operation
            )
        )
    if kind == "resources/read":
        raise McpError(error_data("FORBIDDEN"))
    raise McpError(ErrorData(code=-32600, message=UNAUTHORIZED_TEXT))


def install_guards(app: AtlasHttpApp, repository_id: str, store: PolicyStore) -> Guard:
    """Wrap every non-Ping MCP handler of ``app.server``. Idempotent.

    Refuses (``RefusedError``) unless the verified SDK is installed and the server has exactly the
    six expected request handlers and no notification handler.
    """
    if importlib.metadata.version(VERIFIED_SDK[0]) != VERIFIED_SDK[1]:
        raise RefusedError("Shared serving requires the verified mcp SDK version.")
    server = app.server
    handlers = server.request_handlers
    if set(handlers) != set(HANDLER_TYPES) or server.notification_handlers != {}:
        raise RefusedError("Shared serving requires the verified mcp SDK handler set.")
    guard = Guard(repository_id)
    config = app.config
    route_repo = str(config.repo)

    def build(original: Callable[..., Awaitable[Any]], kind: str) -> Callable[..., Awaitable[Any]]:
        async def guarded(req: Any) -> Any:
            request: Any = None
            context: AuthzContext | None = None
            reason = "principal_missing"
            internal = req is None
            try:
                try:
                    request = server.request_context.request
                except LookupError:
                    request = None
                if internal:
                    # The SDK's own tool-cache refresh inside a guarded call (req is None).
                    ambient = AUTHZ_CONTEXT.get()
                    if ambient is not None and ambient.repository_id == repository_id:
                        context = ambient
                    else:
                        reason = "principal_missing"
                else:
                    credential = (
                        request.scope.get(PRINCIPAL_SCOPE_KEY) if request is not None else None
                    )
                    if isinstance(credential, Credential):
                        name = req.params.name if kind == "tools/call" else None
                        arguments = req.params.arguments if kind == "tools/call" else None
                        policy = store.current()
                        decision = decide(
                            credential,
                            policy,
                            repository_id,
                            classify(kind, name, arguments, route_repo),
                        )
                        if decision.allowed:
                            grant = grant_for(policy, credential.principal, repository_id)
                            if grant is not None and decision.scope_identity is not None:
                                context = AuthzContext(
                                    credential,
                                    repository_id,
                                    grant.tools,
                                    policy.epoch,
                                    decision.scope_identity,
                                )
                        else:
                            reason = decision.reason
            except Exception:
                context = None
                reason = "principal_missing"
            if context is None:
                guard.denied[reason] += 1
                if internal:
                    raise RuntimeError("Shared request is not authorized.")
                try:
                    return _denial(kind, req, request)
                except McpError:
                    raise
                except Exception:
                    raise McpError(ErrorData(code=-32600, message=UNAUTHORIZED_TEXT)) from None
            guard.allowed += 1
            if internal:
                return await original(req)
            token = AUTHZ_CONTEXT.set(context)
            try:
                return await original(req)
            finally:
                AUTHZ_CONTEXT.reset(token)

        setattr(guarded, GUARD_ATTRIBUTE, kind)
        return guarded

    for handler_type, kind in GUARD_KINDS.items():
        original = handlers[handler_type]
        if getattr(original, GUARD_ATTRIBUTE, None) is None:
            handlers[handler_type] = build(original, kind)
    return guard


# ----------------------------------------------------------------------------- router
def _authorization(scope: Scope) -> list[str]:
    return [v.decode("latin-1") for k, v in scope.get("headers", ()) if k == b"authorization"]


class SharedRouter:
    """The outer ASGI application: a closed, body-free admission policy in front of S39 apps."""

    def __init__(
        self,
        *,
        deployment: Deployment,
        apps: dict[str, AtlasHttpApp],
        registries: dict[str, ScopedLeaseRegistry],
        guards: dict[str, Guard],
        store: PolicyStore,
        verifier: JwtVerifier,
        gate: InflightGate,
        limiter: PrincipalLimiter,
        inflight: PrincipalInflight,
    ) -> None:
        self.deployment = deployment
        self.apps = apps
        self.registries = registries
        self.guards = guards
        self.store = store
        self.verifier = verifier
        self.gate = gate
        self.limiter = limiter
        self.inflight = inflight
        self.rejected: Counter[str] = Counter()
        self._first = apps[sorted(apps)[0]]

    def allow_host_name(self, name: str) -> None:
        for app in self.apps.values():
            app.allow_host_name(name)

    @contextlib.asynccontextmanager
    async def running(self) -> AsyncIterator[None]:
        try:
            async with contextlib.AsyncExitStack() as stack:
                for identifier in sorted(self.apps):
                    await stack.enter_async_context(self.apps[identifier].running())
                yield
        finally:
            await self.aclose_state()

    async def aclose_state(self) -> None:
        for identifier in sorted(self.apps):
            await self.apps[identifier].aclose_state()

    async def _respond(
        self, send: Send, reason: str, headers: list[tuple[bytes, bytes]] | None = None
    ) -> None:
        self.rejected[reason] += 1
        status, code, message = _REJECTIONS[reason]
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "server-error",
                "error": {"code": code, "message": message, "data": {"reason": reason}},
            },
            separators=(",", ":"),
        ).encode("utf-8")
        await self._send(send, status, "application/json", body, headers)

    async def _send(
        self,
        send: Send,
        status: int,
        content_type: str,
        body: bytes,
        extra: list[tuple[bytes, bytes]] | None = None,
    ) -> None:
        headers = [
            (b"content-type", content_type.encode("ascii")),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"connection", b"close"),
            *(extra or []),
        ]
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            raise RuntimeError("Only HTTP scopes are served.")
        first = self._first
        if not http._is_loopback_peer(scope.get("client")):
            await first._reject(send, "peer_not_loopback")
            return
        if not first._host_allowed(scope):
            await first._reject(send, "host_not_allowed")
            return
        if not first._origin_allowed(scope):
            await first._reject(send, "origin_not_allowed")
            return
        path = scope.get("path")
        exact = (
            isinstance(path, str)
            and scope.get("raw_path") == path.encode("utf-8", "surrogatepass")
            and scope.get("root_path", "") == ""
        )
        metadata = METADATA_ROUTE.fullmatch(path) if exact and isinstance(path, str) else None
        route = ROUTE.fullmatch(path) if exact and isinstance(path, str) else None
        if metadata is not None:
            if scope.get("method") != "GET":
                await self._respond(send, "metadata_method_not_allowed", [(b"allow", b"GET")])
                return
            identity = self.deployment.identity
            body = metadata_body(
                identity.resource_base_url,
                metadata.group(1),
                identity.issuer,
                list(identity.scopes_supported),
            )
            await self._send(send, 200, "application/json", body)
            return
        if route is None:
            await self._respond(send, "route_not_found")
            return
        repository_id = route.group(1)
        if scope.get("method") != "POST":
            await first._reject(send, "method_not_allowed")
            return
        base = self.deployment.identity.resource_base_url
        # Stage 6: credentials. Nothing below reads the request body.
        try:
            verified = self.verifier.verify(
                _authorization(scope), audience=resource_url(base, repository_id)
            )
        except AuthnError as exc:
            missing = exc.reason == "header_missing"
            await self._respond(
                send,
                "credentials_missing" if missing else "credentials_invalid",
                [(b"www-authenticate", www_authenticate(base, repository_id, invalid=not missing))],
            )
            return
        except Exception:
            await self._respond(
                send,
                "credentials_invalid",
                [(b"www-authenticate", www_authenticate(base, repository_id, invalid=True))],
            )
            return
        credential = verified.credential
        # Stages 7 and 8: policy and repository authorization; any failure is a fixed 403.
        try:
            if self.store.refresh():
                snapshot = self.store.current()
                for registry in self.registries.values():
                    registry.advance_epoch(snapshot.epoch)  # synchronous fence
                await purge_all(self.registries.values(), snapshot)
            decision = decide(
                credential, self.store.current(), repository_id, AccessRequest("route")
            )
            allowed = decision.allowed and repository_id in self.apps
        except Exception:
            allowed = False
        if not allowed:
            await self._respond(send, "repository_forbidden")
            return
        principal = credential.principal
        if not self.inflight.try_acquire(principal):
            await self._respond(
                send,
                "principal_inflight_limit",
                [(b"retry-after", str(http.RETRY_AFTER_SECONDS).encode())],
            )
            return
        try:
            child: Scope = dict(scope)
            child["path"] = http.ENDPOINT_PATH
            child["raw_path"] = http.ENDPOINT_PATH.encode("ascii")
            child["headers"] = [
                (k, v) for k, v in scope.get("headers", ()) if k != b"authorization"
            ]
            child[PRINCIPAL_SCOPE_KEY] = credential
            await self.apps[repository_id](child, receive, send)
        finally:
            self.inflight.release(principal)


# ----------------------------------------------------------------------------- build and run
def _close_built(built: list[tuple[CodeGraph, ScopedLeaseRegistry]]) -> None:
    for code_graph, registry in built:
        with contextlib.suppress(Exception):
            registry.close()
        with contextlib.suppress(Exception):
            code_graph.close()


def build_shared(
    deployment: Deployment,
    *,
    policy_source: PolicySource | None = None,
    verifier: JwtVerifier | None = None,
) -> SharedRouter:
    """Build one S39 application per repository behind one router. Closes everything on failure."""
    for config in deployment.configs.values():
        if config.test_execution_enabled:
            raise RefusedError("Shared serving is not available while test execution is enabled.")
    source = (
        policy_source if policy_source is not None else FilePolicySource(deployment.policy.file)
    )
    store = PolicyStore(
        source,
        deployment.registrations,
        issuer=deployment.identity.issuer,
        interval_s=deployment.policy.reload_interval_seconds,
    )
    identity = deployment.identity
    jwt_verifier = verifier or JwtVerifier(
        issuer=identity.issuer,
        jwks=deployment.jwks_raw,
        leeway_seconds=identity.leeway_seconds,
        max_token_bytes=identity.max_token_bytes,
        max_lifetime_seconds=identity.max_token_lifetime_seconds,
    )
    limits = deployment.limits
    limiter = PrincipalLimiter(limits.max_calls_per_principal_per_minute)
    gate = InflightGate(deployment.listen.max_inflight)
    inflight = PrincipalInflight(deployment.listen.max_inflight_per_principal)
    apps: dict[str, AtlasHttpApp] = {}
    registries: dict[str, ScopedLeaseRegistry] = {}
    guards: dict[str, Guard] = {}
    built: list[tuple[CodeGraph, ScopedLeaseRegistry]] = []
    try:
        for identifier in sorted(deployment.configs):
            config = deployment.configs[identifier]
            enforcement = SharedEnforcement(
                config, repository_id=identifier, limiter=limiter, max_records=limits.max_records
            )
            memex = Memex(config.memex_root)
            # Identical to server.build_server_state (parity is tested).
            code_graph = CodeGraph(
                repo=config.repo,
                read_only=True,
                query_limit=config.max_bound_field_elements + 1,
                graph_root=config.graph_root,
            )
            registry = ScopedLeaseRegistry(
                config,
                code_graph,
                repository_id=identifier,
                max_leases_per_principal=limits.max_leases_per_principal,
                epoch=store.current().epoch,
            )
            code_graph._lease_registry = registry
            built.append((code_graph, registry))
            app = http.build_app(
                config,
                cast("tuple[Enforcement, Memex, CodeGraph]", (enforcement, memex, code_graph)),
            )
            guards[identifier] = install_guards(app, identifier, store)
            app.gate = gate
            apps[identifier] = app
            registries[identifier] = registry
        router = SharedRouter(
            deployment=deployment,
            apps=apps,
            registries=registries,
            guards=guards,
            store=store,
            verifier=jwt_verifier,
            gate=gate,
            limiter=limiter,
            inflight=inflight,
        )
        verify_wiring(router)
        return router
    except BaseException:
        _close_built(built)
        raise


def verify_wiring(router: SharedRouter) -> None:
    """Tripwire: every repository is wired exactly as the authorization model assumes."""
    for identifier, app in router.apps.items():
        enforcement, _memex, code_graph = app.state
        registry = getattr(code_graph, "_lease_registry", None)
        if (
            type(registry) is not ScopedLeaseRegistry
            or registry is not router.registries[identifier]
        ):
            raise RefusedError("Shared serving requires the principal-scoped lease registry.")
        if type(enforcement) is not SharedEnforcement:
            raise RefusedError("Shared serving requires the shared enforcement.")
        if app.gate is not router.gate:
            raise RefusedError("Shared serving requires one shared in-flight gate.")
        handlers = app.server.request_handlers
        for handler_type, handler in handlers.items():
            if handler_type is types.PingRequest:
                from mcp.server.lowlevel.server import _ping_handler

                if handler is not _ping_handler:
                    raise RefusedError("Shared serving requires the SDK ping handler.")
            elif getattr(handler, GUARD_ATTRIBUTE, None) != GUARD_KINDS[handler_type]:
                raise RefusedError("Shared serving requires every request handler to be guarded.")


async def run_shared(
    deployment: Deployment,
    *,
    on_listening: Callable[[list[tuple[str, int]]], None] | None = None,
    install_signals: bool = True,
) -> None:
    """Serve until stopped. Raises RefusedError, LoopbackError or OSError before/while binding."""
    if importlib.metadata.version(VERIFIED_SDK[0]) != VERIFIED_SDK[1]:
        raise RefusedError("Shared serving requires the verified mcp SDK version.")
    try:
        check_runtime_versions()
    except RuntimeVersionError as exc:
        raise RefusedError(str(exc)) from None
    if any(config.test_execution_enabled for config in deployment.configs.values()):
        raise RefusedError("Shared serving is not available while test execution is enabled.")
    host_ip = resolve_loopback(deployment.listen.host)
    router = build_shared(deployment)
    try:
        server = AtlasUvicornServer(
            uvicorn_config(cast("AtlasHttpApp", router), host_ip, deployment.listen.port),
            install_signals=install_signals,
        )
        async with router.running():
            task: asyncio.Future[None] = asyncio.ensure_future(http._serve(server))
            try:
                while not server.started:
                    if task.done():
                        await task
                        raise OSError("The HTTP listener stopped before it started.")
                    await asyncio.sleep(0.01)
                addresses = http._listening_addresses(server)
                if not addresses or not all(http._is_loopback_literal(a) for a, _ in addresses):
                    raise LoopbackError("bound_non_loopback")
                log.info(
                    "shared_http_listening",
                    host=addresses[0][0],
                    port=addresses[0][1],
                    repositories=len(router.apps),
                    policy_epoch=router.store.current().epoch,
                )
                if on_listening is not None:
                    on_listening(addresses)
                await task
            finally:
                await http._stop(server, task)
    finally:
        await router.aclose_state()
        snapshot = router.gate.snapshot()
        log.info(
            "shared_http_stopped",
            admitted=snapshot["admitted"],
            rejected=snapshot["rejected"],
            peak=snapshot["peak"],
        )
