"""One event-loop owned registry of immutable pages and actual graph/source leases."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from contextlib import ExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Any

from atlas_aci.enforcement import ToolError
from atlas_aci.storage import evidence as ev

MAX_TOKEN_BYTES = 2048
POLICY = (
    "max_graph_leases",
    "graph_lease_seconds",
    "max_lease_state_bytes",
    "max_total_lease_state_bytes",
    "max_retained_pack_bytes",
    "max_lease_pages",
    "max_lease_resources",
    "lease_setup_ms",
)


def canonical_bytes(value):
    return ev.canonical(value).encode()


def digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def b64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def decode(value):
    raw = base64.b64decode(value + "=" * ((-len(value)) % 4), altchars=b"-_", validate=True)
    if b64(raw) != value:
        raise ValueError("noncanonical token")
    return raw


@dataclass
class Reservation:
    identifier: str
    scope: str
    owner: ExitStack
    consumed: bool = False


@dataclass
class OwnedLease:
    identifier: str
    scope: str
    query: str
    reader: Any
    owner: ExitStack
    expires: float
    pages: tuple[bytes, ...]
    resources: dict
    state_bytes: int
    pack_bytes: int

    def close(self):
        self.owner.close()


class LeaseRegistry:
    def __init__(self, config, factory):
        self.config = config
        self.factory = factory
        self.instance = secrets.token_hex(16)
        self._secret = secrets.token_bytes(32)
        self._leases: dict[str, OwnedLease] = {}
        self._pending = 0
        self._reservations: dict[str, Reservation] = {}
        self._lock = asyncio.Lock()
        self._timer = None
        self._closed = False

    def scope(self):
        if any(
            type(getattr(self.config, name)) is not int or getattr(self.config, name) <= 0
            for name in POLICY
        ):
            raise ToolError("SCOPE_MISMATCH", "Invalid current retention policy.")
        return digest(
            {
                "repository": str(self.config.repo.resolve()),
                "graph_namespace": str(self.factory.atlas_dir.resolve()),
                "graph_root": str(self.config.graph_root) if self.config.graph_root else None,
                "skip": list(self.config.skip_patterns),
                "read_only": True,
                "max_response_bytes": self.config.max_response_bytes,
                "max_bytes_per_call": self.config.max_bytes_per_call,
                "max_bound_field_elements": self.config.max_bound_field_elements,
                "rate": self.config.max_calls_per_minute,
                "policy": {name: getattr(self.config, name) for name in POLICY},
            }
        )

    def usage(self):
        return {
            "pending": self._pending,
            "active": len(self._leases),
            "state_bytes": sum(v.state_bytes for v in self._leases.values()),
            "referenced_pack_bytes": sum(v.pack_bytes for v in self._leases.values()),
            "sqlite_handles": len(self._leases),
            "pack_fds": sum(v.reader._source_lease is not None for v in self._leases.values()),
        }

    def _expire(self):
        for identifier, lease in list(self._leases.items()):
            if time.monotonic() >= lease.expires:
                del self._leases[identifier]
                lease.close()

    def _ensure_timer(self):
        if self._timer is None or self._timer.done():
            self._timer = asyncio.create_task(self._idle_expiry())

    async def _idle_expiry(self):
        try:
            while not self._closed:
                async with self._lock:
                    self._expire()
                    delay = min(
                        (v.expires - time.monotonic() for v in self._leases.values()),
                        default=self.config.graph_lease_seconds,
                    )
                await asyncio.sleep(max(0, delay))
        except asyncio.CancelledError:
            pass

    @asynccontextmanager
    async def pending(self):
        async with self._lock:
            if self._closed:
                raise ToolError("EXPIRED_REFERENCE", "Reference service is closed.")
            self._expire()
            if self._pending + len(self._leases) >= self.config.max_graph_leases:
                raise ToolError("LEASE_CAPACITY", "No retained lease capacity.")
            reservation = Reservation(secrets.token_hex(16), self.scope(), ExitStack())
            self._reservations[reservation.identifier] = reservation
            self._pending += 1
            self._ensure_timer()
        try:
            yield reservation
        finally:
            async with self._lock:
                if self._reservations.pop(reservation.identifier, None) is not None:
                    self._pending -= 1
                reservation.owner.close()

    def token(self, lease, kind, index):
        payload = {
            "v": 1,
            "kind": kind,
            "instance": self.instance,
            "lease": lease.identifier,
            "repository": digest(str(self.config.repo.resolve())),
            "generation": lease.reader.generation_id,
            "query": lease.query,
            "authority": lease.scope,
            "index": index,
        }
        body = b64(canonical_bytes(payload))
        token = body + "." + b64(hmac.digest(self._secret, body.encode(), "sha256"))
        if len(token) > MAX_TOKEN_BYTES:
            raise ToolError("LEASE_CAPACITY", "Reference metadata exceeds capacity.")
        return token

    def authenticate(self, token, kind):
        try:
            if not isinstance(token, str) or len(token) > MAX_TOKEN_BYTES or not token:
                raise ValueError("token size")
            body, mac = token.split(".")
            if not hmac.compare_digest(
                decode(mac), hmac.digest(self._secret, body.encode(), "sha256")
            ):
                raise ValueError("token MAC")
            raw = decode(body)

            def pairs(items):
                result = {}
                for key, value in items:
                    if key in result:
                        raise ValueError("duplicate key")
                    result[key] = value
                return result

            payload = json.loads(raw, object_pairs_hook=pairs)
            if (
                not isinstance(payload, dict)
                or set(payload)
                != {
                    "v",
                    "kind",
                    "instance",
                    "lease",
                    "repository",
                    "generation",
                    "query",
                    "authority",
                    "index",
                }
                or canonical_bytes(payload) != raw
                or type(payload["v"]) is not int
                or payload["v"] != 1
                or payload["kind"] != kind
                or payload["instance"] != self.instance
            ):
                raise ValueError("token shape")
            if not all(
                ev.hash_value(payload[k])
                for k in ("repository", "generation", "query", "authority")
            ):
                raise ValueError("token fields")
            if (
                not isinstance(payload["lease"], str)
                or re.fullmatch("[0-9a-f]{32}", payload["lease"]) is None
                or (
                    kind == "cursor"
                    and (
                        type(payload["index"]) is not int
                        or not 0 <= payload["index"] < self.config.max_lease_pages
                    )
                )
                or (kind == "resource" and not ev.hash_value(payload["index"]))
            ):
                raise ValueError("token index/lease")
        except (ValueError, TypeError, UnicodeError, KeyError):
            raise ToolError("INVALID_REFERENCE", "Invalid reference.") from None
        self._expire()
        lease = self._leases.get(payload["lease"])
        if lease is None:
            raise ToolError("EXPIRED_REFERENCE", "Reference expired or retired.")
        if (
            lease.scope != self.scope()
            or payload["authority"] != lease.scope
            or payload["repository"] != digest(str(self.config.repo.resolve()))
            or payload["query"] != lease.query
            or payload["generation"] != lease.reader.generation_id
        ):
            raise ToolError("SCOPE_MISMATCH", "Reference scope does not match.")
        return lease, payload["index"]

    async def install(
        self, stack, reader, query, pages, resources, *, reservation, check_setup=None
    ):
        """Adopt only complete immutable pages; caller still owns failures/cancellation."""
        async with self._lock:
            self._expire()
            if (
                self._closed
                or self._reservations.get(reservation.identifier) is not reservation
                or reservation.consumed
                or reservation.owner is not stack
            ):
                raise ToolError("LEASE_CAPACITY", "No pending ownership reservation.")
            if reservation.scope != self.scope():
                raise ToolError("SCOPE_MISMATCH", "Operator changed during setup.")
            if reader.repo != self.config.repo.resolve() or self.factory.repo != reader.repo:
                raise ToolError("SCOPE_MISMATCH", "Reader repository does not match operator.")
            source = reader._source_lease
            lease = OwnedLease(
                secrets.token_hex(16),
                self.scope(),
                query,
                reader,
                stack,
                time.monotonic() + self.config.graph_lease_seconds,
                (),
                resources,
                0,
                0,
            )
            built = pages(lease) if callable(pages) else pages
            built = tuple(bytes(page) for page in built)
            # Lifetime begins at completed issuance, not during page construction.
            lease.expires = time.monotonic() + self.config.graph_lease_seconds
            if any(canonical_bytes(json.loads(page)) != page for page in built):
                raise ValueError("Pages must be canonical immutable JSON")
            # Copy request-owned descriptors, retaining canonical bytes rather than
            # aliases to caller dictionaries. Every issued token is in saved state.
            frozen = {key: canonical_bytes(value) for key, value in resources.items()}
            metadata = (
                {
                    "manifest": source.manifest,
                    "chunks": source.chunk_catalog,
                    "current": source.current,
                    "bindings": sorted((p, h, v) for (p, h), v in source.bindings.items()),
                }
                if source
                else {}
            )
            state_bytes = (
                sum(len(p) for p in built)
                + len(canonical_bytes(resources))
                + len(canonical_bytes(metadata))
            )
            pack_bytes = source.manifest["pack"]["size"] if source else 0
            # Account the lease's own canonical identity/authority/lifetime and
            # retained reader metadata, not just pages and source dictionaries.
            lease_metadata = {
                "identifier": lease.identifier,
                "instance": self.instance,
                "scope": lease.scope,
                "query": lease.query,
                "generation_id": reader.generation_id,
                "snapshot_id": reader.snapshot_id,
                "expires": lease.expires,
                "referenced_pack_bytes": pack_bytes,
                "reader": {
                    "repository": str(reader.repo),
                    "namespace": str(self.factory.atlas_dir),
                    "schema_epoch": reader._lease_schema_epoch,
                    "languages": sorted(reader.langs),
                    "query_limit": reader.query_limit,
                },
            }
            base_bytes = state_bytes + len(canonical_bytes(lease_metadata))
            state_bytes = base_bytes
            while True:
                charged = base_bytes + len(canonical_bytes({"retained_state_bytes": state_bytes}))
                if charged == state_bytes:
                    break
                state_bytes = charged
            if check_setup is not None:
                check_setup()
            usage = self.usage()
            if (
                not built
                or len(built) > self.config.max_lease_pages
                or len(frozen) > self.config.max_lease_resources
                or state_bytes > self.config.max_lease_state_bytes
                or usage["state_bytes"] + state_bytes > self.config.max_total_lease_state_bytes
                or usage["referenced_pack_bytes"] + pack_bytes > self.config.max_retained_pack_bytes
            ):
                raise ToolError("LEASE_CAPACITY", "Retained result exceeds capacity.")
            lease.pages = built
            lease.resources = frozen
            lease.state_bytes = state_bytes
            lease.pack_bytes = pack_bytes
            if check_setup is not None:
                check_setup()  # Final real deadline boundary, immediately before ownership commit.
            lease.owner = stack.pop_all()
            reservation.consumed = True
            del self._reservations[reservation.identifier]
            self._pending -= 1
            self._leases[lease.identifier] = lease
            # Wake from an empty/longer prior schedule to honor this absolute expiry.
            if self._timer is not None:
                self._timer.cancel()
            self._timer = None
            self._ensure_timer()
            return lease

    async def page(self, token, query_digest):
        async with self._lock:
            lease, index = self.authenticate(token, "cursor")
            if query_digest != lease.query:
                raise ToolError("SCOPE_MISMATCH", "Reference query does not match.")
            if type(index) is not int or not 0 <= index < len(lease.pages):
                raise ToolError("INVALID_REFERENCE", "Invalid page reference.")
            return lease.pages[index]

    def close(self):
        """Synchronous same-event-loop shutdown also supports the factory close API."""
        self._closed = True
        for reservation in self._reservations.values():
            reservation.owner.close()
        self._reservations.clear()
        self._pending = 0
        for lease in self._leases.values():
            lease.close()
        self._leases.clear()
        if self._timer is not None:
            self._timer.cancel()

    async def aclose(self):
        async with self._lock:
            self.close()
        if self._timer is not None:
            # gather converts only the OWNED timer's cancellation to a result.
            # Cancellation of this caller still propagates through the await.
            results = await asyncio.gather(self._timer, return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException) and not isinstance(
                    result, asyncio.CancelledError
                ):
                    raise result
