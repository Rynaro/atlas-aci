"""Repository authorization for shared multi-principal serving (ACI-S40).

The policy model and loader, the pure ``decide`` function, the scope identity, the policy store
with its epoch, the principal-scoped lease registry, the per-principal limiter and the shared
enforcement subclass. Nothing here opens a repository, a graph or a socket: the only I/O is the
throttled read of the operator policy file through one hygienic file descriptor.

Authorization is a pure function of (credential, immutable policy snapshot, repository id,
classified request). The policy file is the source of truth; every change of its content digest
advances the epoch, and the epoch is part of every scope identity, so no cursor or resource issued
under an older policy can be resumed.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import re
import stat
import time
from collections import deque
from collections.abc import AsyncIterator, Callable, Iterable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Protocol

import structlog

from atlas_aci.config import Config
from atlas_aci.enforcement import READ_ONLY_TOOLS, Enforcement, ToolError
from atlas_aci.protocol.results import FIXED_MESSAGES
from atlas_aci.query.leases import LeaseRegistry, digest

log = structlog.get_logger()

NEVER_SHARED = frozenset({"test_dry_run", "memex_read"})
SHAREABLE_TOOLS = READ_ONLY_TOOLS - NEVER_SHARED
DENIAL_REASONS = (
    "policy_unavailable",
    "repository_unknown",
    "repository_not_granted",
    "scope_not_presented",
    "tool_disabled",
    "tool_unknown",
    "tool_not_granted",
    "argument_rejected",
    "repository_argument_mismatch",
    "cross_repository_not_granted",
    "principal_missing",
)
KINDS = (
    "route",
    "tools/list",
    "tools/call",
    "resources/list",
    "resources/templates/list",
    "resources/read",
)
STRUCTURED_OPS = frozenset(
    {"status", "neighborhood", "path", "page", "continue", "retrieve", "context", "impact"}
)
POLICY_FORMAT = "atlas-aci-shared-policy.v1"
POLICY_MAX_BYTES = 1024 * 1024
POLICY_MAX_GRANTS = 10000
POLICY_REASONS = (
    "unreadable",
    "too_large",
    "unsafe_file",
    "not_json",
    "unknown_field",
    "missing_field",
    "wrong_type",
    "bad_format",
    "issuer_unknown",
    "repository_unknown",
    "tool_not_shareable",
    "duplicate_grant",
    "too_many_grants",
)
SUBJECT_MAX_CHARS = 255
SCOPE_TOKEN = re.compile(r"[\x21\x23-\x5b\x5d-\x7e]{1,128}")
REPOSITORY_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
INVALID_DIGEST = "invalid"
DECISION_FORMAT = "atlas-aci-authz-decision.v1"
SCOPE_FORMAT = "atlas-aci-authz-scope.v1"
FORBIDDEN_TEXT = FIXED_MESSAGES["FORBIDDEN"]


# ----------------------------------------------------------------------------- strict JSON / files
class FileRejected(Exception):
    """A configuration file failed the hygiene checks; ``reason`` is a closed code."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def strict_json(raw: bytes) -> Any:
    """JSON with no duplicate keys, no NaN/Infinity/overflowing floats; ``ValueError`` otherwise."""

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def constant(_name: str) -> Any:
        raise ValueError("non-finite number")

    def number(text: str) -> float:
        value = float(text)
        if not math.isfinite(value):
            raise ValueError("non-finite number")
        return value

    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=constant,
            parse_float=number,
        )
    except RecursionError:
        raise ValueError("nesting too deep") from None


def read_hygienic(path: Path, limit: int) -> bytes:
    """Read a regular, owner-writable-only file through one descriptor (no symlink, no FIFO).

    The descriptor is opened with ``O_NOFOLLOW``, ``fstat``-ed and read to its end; the bytes
    that are returned are exactly the bytes that were checked. Group- or world-writable files and
    parent directories are refused.
    """
    flags = os.O_RDONLY
    for name in ("O_NOFOLLOW", "O_CLOEXEC", "O_NONBLOCK"):
        flags |= getattr(os, name, 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        raise FileRejected("unreadable") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise FileRejected("unreadable")
        if info.st_mode & 0o022:
            raise FileRejected("unsafe_file")
        try:
            parent = os.stat(path.parent)
        except OSError:
            raise FileRejected("unreadable") from None
        if parent.st_mode & 0o022:
            raise FileRejected("unsafe_file")
        if info.st_size > limit:
            raise FileRejected("too_large")
        chunks: list[bytes] = []
        total = 0
        while True:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                raise FileRejected("unreadable") from None
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise FileRejected("too_large")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def valid_subject(value: object) -> bool:
    """The one subject predicate, used for the policy and for the token ``sub`` claim."""
    return (
        isinstance(value, str)
        and 1 <= len(value) <= SUBJECT_MAX_CHARS
        and value.isprintable()
        and value == value.strip()
    )


# ----------------------------------------------------------------------------- model
@dataclass(frozen=True)
class Principal:
    issuer: str
    subject: str


@dataclass(frozen=True)
class Credential:
    principal: Principal
    scopes: frozenset[str]


@dataclass(frozen=True)
class Grant:
    repository_id: str
    tools: frozenset[str]
    required_scope: str | None
    digest: str


@dataclass(frozen=True)
class Registration:
    repository_id: str
    repo: str
    namespace: str
    before_id: str | None


@dataclass(frozen=True)
class PolicySnapshot:
    """An immutable view of the policy and the registry; ``epoch`` is assigned by the store."""

    valid: bool
    digest: str
    epoch: int
    registry_digest: str
    registrations: Mapping[str, Registration]
    grants: Mapping[tuple[Principal, str], Grant]


@dataclass(frozen=True)
class AccessRequest:
    kind: str
    tool: str | None = None
    op: str | None = None
    repository_argument: str = "absent"  # "absent" | "route" | "other"
    argument: str = "ok"  # "ok" | "rejected" (a value that could be parsed as an option)


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    principal: Principal
    repository_id: str
    request: AccessRequest
    policy_digest: str
    registry_digest: str
    policy_epoch: int
    grant_digest: str | None
    scope_identity: str | None

    def digest(self) -> str:
        return digest({"format": DECISION_FORMAT, **asdict(self)})


@dataclass(frozen=True)
class PathDecision:
    authorized: tuple[str, ...]
    denied_at: str | None
    reason: str


@dataclass(frozen=True)
class AuthzContext:
    credential: Credential
    repository_id: str
    tools: frozenset[str]
    policy_epoch: int
    scope_identity: str


# Set only inside the handler guard (the request task) and reset in `finally`. None everywhere
# else: in the router, in tasks spawned outside a guard and in any code that runs unguarded.
AUTHZ_CONTEXT: ContextVar[AuthzContext | None] = ContextVar("atlas_aci_authz_context", default=None)


def registry_digest(registrations: Iterable[Registration]) -> str:
    return digest(
        [
            {"id": r.repository_id, "repo": r.repo, "namespace": r.namespace, "before": r.before_id}
            for r in sorted(registrations, key=lambda r: r.repository_id)
        ]
    )


def scope_identity(
    principal: Principal, repository_id: str, grant_digest: str, policy_epoch: int
) -> str:
    return digest(
        {
            "format": SCOPE_FORMAT,
            "principal": {"issuer": principal.issuer, "subject": principal.subject},
            "repository_id": repository_id,
            "grant_digest": grant_digest,
            "policy_epoch": policy_epoch,
        }
    )


# ----------------------------------------------------------------------------- policy loading
class PolicyError(ValueError):
    """An invalid policy document. ``str`` is fixed text; values and content are never echoed."""

    def __init__(self, reason: str, field: str = "") -> None:
        self.reason = reason
        self.field = field
        super().__init__(f"invalid shared policy ({reason} at {field or '/'})")


def _closed(
    value: Any, required: Sequence[str], optional: Sequence[str], pointer: str
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PolicyError("wrong_type", pointer)
    for key in value:
        if key not in required and key not in optional:
            raise PolicyError("unknown_field", f"{pointer}/{key}")
    for key in required:
        if key not in value:
            raise PolicyError("missing_field", f"{pointer}/{key}")
    return value


def parse_policy(
    raw: bytes, *, issuer: str, registrations: Mapping[str, Registration]
) -> PolicySnapshot:
    """Validate a policy document; the result has ``epoch == 0`` (the store assigns the epoch)."""
    if len(raw) > POLICY_MAX_BYTES:
        raise PolicyError("too_large")
    try:
        document = strict_json(raw)
    except ValueError:
        raise PolicyError("not_json") from None
    top = _closed(document, ("format", "grants"), (), "")
    if top["format"] != POLICY_FORMAT:
        raise PolicyError("bad_format", "/format")
    grants_raw = top["grants"]
    if not isinstance(grants_raw, list):
        raise PolicyError("wrong_type", "/grants")
    if len(grants_raw) > POLICY_MAX_GRANTS:
        raise PolicyError("too_many_grants", "/grants")
    grants: dict[tuple[Principal, str], Grant] = {}
    for index, entry in enumerate(grants_raw):
        where = f"/grants/{index}"
        item = _closed(
            entry,
            ("issuer", "subject", "repository", "tools"),
            ("required_scope",),
            where,
        )
        for name in ("issuer", "subject", "repository"):
            if not isinstance(item[name], str):
                raise PolicyError("wrong_type", f"{where}/{name}")
        if item["issuer"] != issuer:
            raise PolicyError("issuer_unknown", f"{where}/issuer")
        if not valid_subject(item["subject"]):
            raise PolicyError("bad_format", f"{where}/subject")
        repository = item["repository"]
        if repository not in registrations:
            raise PolicyError("repository_unknown", f"{where}/repository")
        tools = item["tools"]
        if not isinstance(tools, list) or not tools:
            raise PolicyError("wrong_type", f"{where}/tools")
        for tool in tools:
            if not isinstance(tool, str):
                raise PolicyError("wrong_type", f"{where}/tools")
        if len(set(tools)) != len(tools):
            raise PolicyError("duplicate_grant", f"{where}/tools")
        for tool in tools:
            if tool not in SHAREABLE_TOOLS:
                raise PolicyError("tool_not_shareable", f"{where}/tools")
        scope = item.get("required_scope")
        if "required_scope" in item and (
            not isinstance(scope, str) or SCOPE_TOKEN.fullmatch(scope) is None
        ):
            raise PolicyError("bad_format", f"{where}/required_scope")
        principal = Principal(item["issuer"], item["subject"])
        if (principal, repository) in grants:
            raise PolicyError("duplicate_grant", where)
        canonical = {
            "issuer": principal.issuer,
            "subject": principal.subject,
            "repository": repository,
            "tools": sorted(tools),
            "required_scope": scope,
        }
        grants[(principal, repository)] = Grant(
            repository, frozenset(tools), scope, digest(canonical)
        )
    ordered = sorted(grants.items(), key=lambda kv: (kv[0][0].issuer, kv[0][0].subject, kv[0][1]))
    policy_digest = digest(
        {"format": POLICY_FORMAT, "grants": [{"grant": g.digest} for _, g in ordered]}
    )
    return PolicySnapshot(
        True,
        policy_digest,
        0,
        registry_digest(registrations.values()),
        dict(registrations),
        grants,
    )


def deny_all(registrations: Mapping[str, Registration], epoch: int = 0) -> PolicySnapshot:
    return PolicySnapshot(
        False, INVALID_DIGEST, epoch, registry_digest(registrations.values()), {}, {}
    )


class PolicySource(Protocol):
    def read(self) -> bytes: ...


class FilePolicySource:
    """The operator policy file, read through one hygienic descriptor per refresh."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> bytes:
        try:
            return read_hygienic(self.path, POLICY_MAX_BYTES)
        except FileRejected as exc:
            raise PolicyError(exc.reason) from None


class MemoryPolicySource:
    """A policy held in memory (tests); ``set(None)`` makes the source unreadable."""

    def __init__(self, document: Any | None = None) -> None:
        self._raw: bytes | None = None
        self.reads = 0
        self.set(document)

    def set(self, document: Any | None) -> None:
        if document is None:
            self._raw = None
        elif isinstance(document, bytes):
            self._raw = document
        else:
            self._raw = json.dumps(document).encode("utf-8")

    def read(self) -> bytes:
        self.reads += 1
        if self._raw is None:
            raise PolicyError("unreadable")
        return self._raw


class PolicyStore:
    """The current policy snapshot and its epoch.

    ``current()`` does no I/O. ``refresh()`` re-reads the source at most once per interval, always
    through the content: the epoch advances if and only if the effective digest changes, never on
    a stat or a timestamp. An unreadable or invalid source means deny-all (digest ``"invalid"``).
    """

    def __init__(
        self,
        source: PolicySource,
        registrations: Mapping[str, Registration],
        *,
        issuer: str,
        interval_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._source = source
        self._registrations = dict(registrations)
        self._issuer = issuer
        self._interval = interval_s
        self._clock = clock
        self._next = 0.0
        self.reloads = 0
        self.last_error: PolicyError | None = None
        self._snapshot = deny_all(self._registrations, 0)
        self._load(initial=True)
        self._next = clock() + interval_s

    def current(self) -> PolicySnapshot:
        return self._snapshot

    def _load(self, *, initial: bool = False) -> bool:
        self.reloads += 1
        error: PolicyError | None = None
        try:
            parsed = parse_policy(
                self._source.read(), issuer=self._issuer, registrations=self._registrations
            )
        except PolicyError as exc:
            error = exc
            parsed = deny_all(self._registrations, 0)
        except Exception:
            error = PolicyError("unreadable")
            parsed = deny_all(self._registrations, 0)
        previous = self._snapshot
        self.last_error = error
        if initial:
            self._snapshot = replace(parsed, epoch=1)
            return False
        if parsed.digest == previous.digest:
            return False
        self._snapshot = replace(parsed, epoch=previous.epoch + 1)
        if error is not None:
            log.warning("shared_policy_invalid", reason=error.reason)
        return True

    def refresh(self) -> bool:
        """Re-read when the interval has elapsed. True iff the epoch advanced. Synchronous."""
        now = self._clock()
        if now < self._next:
            return False
        self._next = now + self._interval
        return self._load()


# ----------------------------------------------------------------------------- decisions
def classify(kind: str, name: str | None, arguments: object, route_repo: str) -> AccessRequest:
    """Pure and total: never raises. Unknown shapes are classified conservatively."""
    if kind != "tools/call":
        # An unknown kind is kept as given: `decide` denies anything it does not list.
        return AccessRequest(kind if isinstance(kind, str) else "unknown")
    tool = name if isinstance(name, str) else ""
    args: Mapping[str, Any] = arguments if isinstance(arguments, dict) else {}
    op: str | None = None
    repository_argument = "absent"
    argument = "ok"
    if tool == "graph_query":
        candidate = args.get("op")
        op = candidate if isinstance(candidate, str) and candidate in STRUCTURED_OPS else None
        if op in {"retrieve", "context"}:
            values = [args["repository"]] if "repository" in args else []
            nested = args.get("query")
            if isinstance(nested, dict) and "repository" in nested:
                values.append(nested["repository"])
            if values:
                repository_argument = "route" if all(v == route_repo for v in values) else "other"
    elif tool == "search_text":
        pattern = args.get("pattern")
        if not isinstance(pattern, str) or pattern.startswith("-"):
            argument = "rejected"
    return AccessRequest("tools/call", tool, op, repository_argument, argument)


def _decision(
    allowed: bool,
    reason: str,
    credential: Credential,
    policy: PolicySnapshot,
    repository_id: str,
    request: AccessRequest,
    grant: Grant | None = None,
) -> Decision:
    identity = (
        scope_identity(credential.principal, repository_id, grant.digest, policy.epoch)
        if allowed and grant is not None
        else None
    )
    return Decision(
        allowed,
        reason,
        credential.principal,
        repository_id,
        request,
        policy.digest,
        policy.registry_digest,
        policy.epoch,
        grant.digest if allowed and grant is not None else None,
        identity,
    )


def decide(
    credential: Credential, policy: PolicySnapshot, repository_id: str, request: AccessRequest
) -> Decision:
    """The authorization decision. The first matching rule wins; see the plan's D3 for the order."""

    def deny(reason: str) -> Decision:
        return _decision(False, reason, credential, policy, repository_id, request)

    if not policy.valid:
        return deny("policy_unavailable")
    registration = policy.registrations.get(repository_id)
    if registration is None:
        return deny("repository_unknown")
    grant = policy.grants.get((credential.principal, repository_id))
    if grant is None:
        return deny("repository_not_granted")
    if grant.required_scope is not None and grant.required_scope not in credential.scopes:
        return deny("scope_not_presented")

    def allow() -> Decision:
        return _decision(True, "allowed", credential, policy, repository_id, request, grant)

    if request.kind in {"route", "tools/list", "resources/list", "resources/templates/list"}:
        return allow()
    if request.kind == "resources/read":
        return allow() if "graph_query" in grant.tools else deny("tool_not_granted")
    if request.kind != "tools/call":
        return deny("tool_unknown")
    tool = request.tool or ""
    if tool in NEVER_SHARED:
        return deny("tool_disabled")
    if tool not in READ_ONLY_TOOLS:
        return deny("tool_unknown")
    if tool not in grant.tools:
        return deny("tool_not_granted")
    if request.argument != "ok":
        return deny("argument_rejected")
    if request.repository_argument == "other":
        return deny("repository_argument_mismatch")
    if request.op == "impact" and registration.before_id is not None:
        path = authorize_path(credential, policy, [repository_id, registration.before_id])
        if path.denied_at is not None:
            return deny("cross_repository_not_granted")
    return allow()


def authorize_path(
    credential: Credential, policy: PolicySnapshot, repository_ids: Sequence[str]
) -> PathDecision:
    """Every hop must be readable through ``graph_query``; the first denial stops the walk."""
    authorized: list[str] = []
    hop = AccessRequest("tools/call", "graph_query", "neighborhood")
    for repository_id in repository_ids:
        decision = decide(credential, policy, repository_id, hop)
        if not decision.allowed:
            return PathDecision(tuple(authorized), repository_id, decision.reason)
        authorized.append(repository_id)
    return PathDecision(tuple(authorized), None, "allowed")


def grant_for(policy: PolicySnapshot, principal: Principal, repository_id: str) -> Grant | None:
    return policy.grants.get((principal, repository_id))


# ----------------------------------------------------------------------------- limits
class PrincipalLimiter:
    """A sliding-window call limiter per principal, shared by every repository."""

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.per_minute = per_minute
        self._clock = clock
        self._calls: dict[Principal, deque[float]] = {}

    def check(self, principal: Principal) -> None:
        if self.per_minute <= 0:
            return
        now = self._clock()
        calls = self._calls.setdefault(principal, deque())
        while calls and calls[0] < now - 60.0:
            calls.popleft()
        if len(calls) >= self.per_minute:
            raise ToolError("TIMEOUT", "Rate limit exceeded for this principal.")
        calls.append(now)


class PrincipalInflight:
    """A synchronous per-principal in-flight counter (increment and decrement never await)."""

    def __init__(self, cap: int) -> None:
        self.cap = cap
        self._active: dict[Principal, int] = {}
        self.rejected = 0

    def try_acquire(self, principal: Principal) -> bool:
        active = self._active.get(principal, 0)
        if active >= self.cap:
            self.rejected += 1
            return False
        self._active[principal] = active + 1
        return True

    def release(self, principal: Principal) -> None:
        active = self._active.get(principal, 0) - 1
        if active > 0:
            self._active[principal] = active
        else:
            self._active.pop(principal, None)

    def active(self, principal: Principal) -> int:
        return self._active.get(principal, 0)


# ----------------------------------------------------------------------------- lease registry
def _scope_mismatch() -> ToolError:
    return ToolError("SCOPE_MISMATCH", "Reference scope does not match.")


class ScopedLeaseRegistry(LeaseRegistry):
    """The S13 registry with every reservation, token authority, install and authentication bound
    to the calling principal's authorization scope.

    ``scope()`` fails closed when no authorization context is set for this repository, so a code
    path that bypasses the guard cannot create or resume anything. The retained-evidence store is
    ``_leases``; there is no result cache.
    """

    def __init__(
        self,
        config: Config,
        factory: Any,
        *,
        repository_id: str,
        max_leases_per_principal: int,
        epoch: int = 1,
    ) -> None:
        super().__init__(config, factory)
        self.repository_id = repository_id
        self.quota = max_leases_per_principal
        self._epoch = epoch
        self._scope_owner: dict[str, tuple[Principal, int]] = {}
        self._pending_by: dict[Principal, int] = {}

    def _context(self) -> AuthzContext:
        ctx = AUTHZ_CONTEXT.get()
        if ctx is None or ctx.repository_id != self.repository_id:
            raise _scope_mismatch()
        return ctx

    def scope(self) -> str:
        ctx = self._context()
        if ctx.policy_epoch != self._epoch:
            raise _scope_mismatch()
        result = digest({"base": super().scope(), "authz": ctx.scope_identity})
        self._scope_owner[result] = (ctx.credential.principal, ctx.policy_epoch)
        return result

    def held(self, principal: Principal) -> int:
        return sum(
            1
            for lease in self._leases.values()
            if (owner := self._scope_owner.get(lease.scope)) is not None and owner[0] == principal
        )

    @contextlib.asynccontextmanager
    async def pending(self) -> AsyncIterator[Any]:
        ctx = self._context()
        if ctx.policy_epoch != self._epoch:
            raise _scope_mismatch()
        principal = ctx.credential.principal
        # Synchronous up to the increment: the check and the claim are atomic on the loop.
        self._expire()
        if self.held(principal) + self._pending_by.get(principal, 0) >= self.quota:
            raise ToolError("LEASE_CAPACITY", "No retained lease capacity.")
        self._pending_by[principal] = self._pending_by.get(principal, 0) + 1
        try:
            async with super().pending() as reservation:
                yield reservation
        finally:
            remaining = self._pending_by.get(principal, 0) - 1
            if remaining > 0:
                self._pending_by[principal] = remaining
            else:
                self._pending_by.pop(principal, None)

    def advance_epoch(self, epoch: int) -> None:
        """Synchronous fence: in-flight work decided under an older epoch can no longer commit."""
        self._epoch = epoch

    async def purge_revoked(self, policy: PolicySnapshot) -> int:
        """Close every retained lease the policy no longer authorizes; return how many."""
        async with self._lock:
            self.advance_epoch(policy.epoch)
            closed = 0
            for identifier, lease in list(self._leases.items()):
                owner = self._scope_owner.get(lease.scope)
                stale = (
                    owner is None
                    or owner[1] != policy.epoch
                    or not policy.valid
                    or (owner[0], self.repository_id) not in policy.grants
                )
                if stale:
                    del self._leases[identifier]
                    lease.close()
                    closed += 1
            self._scope_owner = {
                scope: owner
                for scope, owner in self._scope_owner.items()
                if owner[1] == policy.epoch
            }
            return closed


# ----------------------------------------------------------------------------- enforcement
class SharedEnforcement(Enforcement):
    """Enforcement for one repository of a shared deployment: the first calls of every dispatch
    and resource read re-check the principal that the guard authorized."""

    def __init__(
        self,
        config: Config,
        *,
        repository_id: str,
        limiter: PrincipalLimiter,
        max_records: int,
    ) -> None:
        super().__init__(config)
        self.repository_id = repository_id
        self.limiter = limiter
        self.max_records = max_records

    def _context(self) -> AuthzContext:
        ctx = AUTHZ_CONTEXT.get()
        if ctx is None or ctx.repository_id != self.repository_id or not ctx.tools:
            raise ToolError("FORBIDDEN", FORBIDDEN_TEXT)
        return ctx

    def assert_read_only(self, tool_name: str) -> None:
        ctx = self._context()
        if (
            tool_name in NEVER_SHARED
            or tool_name not in SHAREABLE_TOOLS
            or tool_name not in ctx.tools
        ):
            raise ToolError("FORBIDDEN", FORBIDDEN_TEXT)
        super().assert_read_only(tool_name)

    def assert_rate_limit(self) -> None:
        ctx = self._context()
        self.limiter.check(ctx.credential.principal)
        super().assert_rate_limit()

    def record(self, **kwargs: Any) -> None:
        super().record(**kwargs)
        if self.max_records >= 0 and len(self.records) > self.max_records:
            del self.records[: len(self.records) - self.max_records]


async def purge_all(registries: Iterable[ScopedLeaseRegistry], policy: PolicySnapshot) -> int:
    results = await asyncio.gather(*(r.purge_revoked(policy) for r in registries))
    return sum(results)
