"""Atlas-owned MCP protocol-revision table and the pure negotiation model.

The SDK (mcp 1.27.0) decides what the wire says: a supported revision is echoed, anything else
is answered with its latest revision. This module only names that behaviour, so the shaping of
tool manifests and tool results never depends on an unverified revision.

Pure by construction: no I/O, no clock, no randomness, no logging, and no import of
``atlas_aci.protocol.graph`` or ``atlas_aci.protocol.results`` (import order cannot change a
published schema). Nothing here runs a request-path side effect.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from mcp import types
from mcp.shared import version as sdk_version
from pydantic import ValidationError


@dataclass(frozen=True)
class Revision:
    id: str
    family: str
    structured_results: bool
    status: str


REVISIONS = (
    Revision("2024-11-05", "legacy", False, "retained_legacy"),
    Revision("2025-03-26", "legacy", False, "retained_legacy"),
    Revision("2025-06-18", "modern", True, "verified_modern"),
    Revision("2025-11-25", "modern", True, "selected"),
)
REVISION_IDS = tuple(revision.id for revision in REVISIONS)
SELECTED = "2025-11-25"
LEGACY = ("2024-11-05", "2025-03-26")
MODERN = ("2025-06-18", "2025-11-25")
CONSERVATIVE = "2024-11-05"
VERIFIED_SDK = ("mcp", "1.27.0")
STRUCTURED_VERSIONS = frozenset(
    revision.id for revision in REVISIONS if revision.structured_results
)
FAMILIES = ("legacy", "modern")
STATUSES = ("retained_legacy", "verified_modern", "selected")

OUTCOMES = ("supported", "counter_offer", "malformed")
REASONS = (
    "listed_revision",
    "sdk_only_revision",
    "unknown_revision",
    "integer_revision",
    "missing_revision",
    "invalid_revision_type",
    "invalid_initialize_params",
)
WIRE = ("initialize_result", "invalid_params_error")
REQUESTED_TYPES = ("string", "integer", "missing", "other")
RECORD_FORMAT = "atlas-protocol-negotiation.v1"
REQUESTED_LIMIT = 64
RECORD_KEYS = (
    "format",
    "outcome",
    "reason",
    "requested",
    "requested_len",
    "requested_truncated",
    "requested_type",
    "offered",
    "shaping_revision",
    "verified",
    "family",
    "structured_results",
    "wire",
    "atlas_revisions",
    "selected",
    "sdk_supported",
)
_BY_ID = {revision.id: revision for revision in REVISIONS}


@dataclass(frozen=True)
class Negotiation:
    outcome: str
    reason: str
    requested: str | int | None
    requested_type: str
    offered: str | None
    shaping_revision: str | None
    verified: bool
    family: str | None
    structured_results: bool
    wire: str

    def revision(self) -> str:
        """The revision that shapes manifests and results for this session."""
        if self.shaping_revision is None:
            raise ValueError("A malformed initialize request has no shaping revision.")
        return self.shaping_revision

    def record(self) -> dict[str, Any]:
        """The closed-key atlas-protocol-negotiation.v1 diagnostic (client input is bounded)."""
        requested = self.requested
        length: int | None = None
        truncated = False
        if isinstance(requested, str):
            length = len(requested)
            truncated = length > REQUESTED_LIMIT
            requested = requested[:REQUESTED_LIMIT]
        return {
            "format": RECORD_FORMAT,
            "outcome": self.outcome,
            "reason": self.reason,
            "requested": requested,
            "requested_len": length,
            "requested_truncated": truncated,
            "requested_type": self.requested_type,
            "offered": self.offered,
            "shaping_revision": self.shaping_revision,
            "verified": self.verified,
            "family": self.family,
            "structured_results": self.structured_results,
            "wire": self.wire,
            "atlas_revisions": list(REVISION_IDS),
            "selected": SELECTED,
            "sdk_supported": list(sdk_version.SUPPORTED_PROTOCOL_VERSIONS),
        }

    def record_bytes(self) -> bytes:
        return json.dumps(
            self.record(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")


def _requested_type(requested: object) -> str:
    if isinstance(requested, str):
        return "string"
    if isinstance(requested, int) and not isinstance(requested, bool):
        return "integer"
    return "other"


def _shaping(revision: str) -> tuple[str, bool, str, bool]:
    """(shaping revision, verified, family, structured results) for a revision id.

    An id outside the Atlas table is shaped as the conservative legacy revision, unverified, so
    an unlisted revision can never enable structured results.
    """
    row = _BY_ID.get(revision)
    if row is None:
        row = _BY_ID[CONSERVATIVE]
        return row.id, False, row.family, row.structured_results
    return row.id, True, row.family, row.structured_results


def negotiate(
    requested: str | int,
    *,
    sdk_supported: Sequence[str] | None = None,
    sdk_latest: str | None = None,
) -> Negotiation:
    """Describe what the SDK answers for a well-formed initialize and how Atlas shapes results.

    ``None`` reads the SDK list and latest revision at call time: the same objects the SDK
    session uses to answer ``initialize``.
    """
    supported = list(
        sdk_version.SUPPORTED_PROTOCOL_VERSIONS if sdk_supported is None else sdk_supported
    )
    latest = types.LATEST_PROTOCOL_VERSION if sdk_latest is None else sdk_latest
    kind = _requested_type(requested)
    value = (
        requested if isinstance(requested, (str, int)) and not isinstance(requested, bool) else None
    )
    if isinstance(requested, str) and requested in supported:
        shaping, verified, family, structured = _shaping(requested)
        return Negotiation(
            "supported",
            "listed_revision" if verified else "sdk_only_revision",
            value,
            kind,
            requested,
            shaping,
            verified,
            family,
            structured,
            "initialize_result",
        )
    shaping, verified, family, structured = _shaping(latest)
    return Negotiation(
        "counter_offer",
        "unknown_revision" if isinstance(requested, str) else "integer_revision",
        value,
        kind,
        latest,
        shaping,
        verified,
        family,
        structured,
        "initialize_result",
    )


def classify_initialize(params: object) -> Negotiation:
    """Classify raw initialize params exactly as the SDK validates them (no I/O, no raising)."""
    present = isinstance(params, dict) and "protocolVersion" in params
    try:
        validated = types.InitializeRequestParams.model_validate(params)
    except ValidationError as exc:
        requested: str | int | None = None
        if not present:
            reason = "missing_revision" if isinstance(params, dict) else "invalid_initialize_params"
        elif any(error["loc"][:1] == ("protocolVersion",) for error in exc.errors()):
            reason = "invalid_revision_type"
        else:
            reason = "invalid_initialize_params"
            value = params["protocolVersion"]  # type: ignore[index]
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                requested = value
        return Negotiation(
            "malformed",
            reason,
            requested,
            "missing" if not present else _requested_type(requested),
            None,
            None,
            False,
            None,
            False,
            "invalid_params_error",
        )
    return negotiate(validated.protocolVersion)


def shaping_revision(requested: str | int) -> str:
    return negotiate(requested).revision()


def negotiated_version(requested: str) -> str:
    """Compatibility name kept for the published import path (``protocol.results``)."""
    return shaping_revision(requested)
