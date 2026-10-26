"""Bounded raw-byte resource URI parsing and actual SDK result/error framing."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

from mcp import McpError, types
from mcp.server.lowlevel.helper_types import ReadResourceContents

from atlas_aci.enforcement import ToolError
from atlas_aci.storage import evidence as ev

MAX_URI_LENGTH = 4096
RESOURCE_TEMPLATE = "atlas-source://{resource_id}{?token,offset,max_bytes}"
REASONS = {
    "INVALID_REFERENCE": -32602,
    "SCOPE_MISMATCH": -32602,
    "INVALID_BUDGET": -32602,
    "FORBIDDEN": -32602,
    "RATE_LIMITED": -32602,
    "EXPIRED_REFERENCE": -32002,
    "SOURCE_UNAVAILABLE": -32002,
    "SOURCE_CORRUPT": -32603,
    "INTERNAL": -32603,
}


def error_data(reason):
    reason = reason if reason in REASONS else "INTERNAL"
    return types.ErrorData(
        code=REASONS[reason], message="Resource request rejected.", data={"reason": reason}
    )


def wire_bytes(result):
    return result.model_dump_json(by_alias=True, exclude_none=True).encode()


FIXED_RESOURCE_ERROR_CAP = max(len(wire_bytes(error_data(reason))) for reason in REASONS)


def parse_uri(uri):
    try:
        if not isinstance(uri, str) or not 0 < len(uri) <= MAX_URI_LENGTH:
            raise ValueError("URI length")
        parts = urlsplit(uri)
        if (
            parts.scheme != "atlas-source"
            or not ev.hash_value(parts.netloc)
            or parts.path
            or parts.fragment
        ):
            raise ValueError("URI shape")
        pairs = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
        if len(pairs) != 3 or {p[0] for p in pairs} != {"token", "offset", "max_bytes"}:
            raise ValueError("query fields")
        fields = dict(pairs)
        offset = int(fields["offset"])
        budget = int(fields["max_bytes"])
        if (
            offset < 0
            or budget < 0
            or str(offset) != fields["offset"]
            or str(budget) != fields["max_bytes"]
        ):
            raise ValueError("query integers")
        canonical = (
            "atlas-source://"
            + parts.netloc
            + "?"
            + urlencode({"token": fields["token"], "offset": offset, "max_bytes": budget})
        )
        if canonical != uri:
            raise ValueError("canonical URI")
        return parts.netloc, fields["token"], offset, budget
    except (ValueError, TypeError, UnicodeError):
        raise ToolError("INVALID_REFERENCE", "Invalid resource URI.") from None


def next_uri(identifier, token, offset, budget):
    return (
        "atlas-source://"
        + identifier
        + "?"
        + urlencode({"token": token, "offset": offset, "max_bytes": budget})
    )


def result_for(uri, descriptor, token, offset, count, blob):
    total = descriptor["end_byte"] - descriptor["start_byte"]
    end = offset + count
    complete = end == total
    metadata = {
        "schema_version": "atlas.source.v1",
        "resource_id": descriptor["id"],
        "generation_id": descriptor["generation_id"],
        "snapshot_id": descriptor["snapshot_id"],
        "source_sha256": descriptor["source_sha256"],
        "span_start_byte": descriptor["start_byte"],
        "span_end_byte": descriptor["end_byte"],
        "returned_offset": offset,
        "returned_end": end,
        "complete": complete,
        "next_uri": None if complete else next_uri(descriptor["id"], token, end, parse_uri(uri)[3]),
    }
    return types.ReadResourceResult(
        contents=[
            types.BlobResourceContents(
                uri=uri, mimeType="application/octet-stream", blob=blob, _meta=metadata
            )
        ]
    )


async def read_resource(registry, uri, enforcement):
    """Return the SDK decorator's advertised helper type after exact result admission."""
    try:
        enforcement.assert_rate_limit()
        identifier, token, offset, budget = parse_uri(uri)
        if not 0 < budget <= registry.config.max_response_bytes:
            raise ToolError("INVALID_BUDGET", "Invalid resource result budget.")
        async with registry._lock:
            lease, signed_identifier = registry.authenticate(token, "resource")
            if identifier != signed_identifier or identifier not in lease.resources:
                raise ToolError("INVALID_REFERENCE", "Resource was not issued by this lease.")
            descriptor = json.loads(lease.resources[identifier])
            if descriptor["id"] != identifier or registry.config.should_skip(
                Path(descriptor["path"])
            ):
                raise ToolError("FORBIDDEN", "Source read policy rejects this resource.")
            total = descriptor["end_byte"] - descriptor["start_byte"]
            if offset > total:
                raise ToolError("INVALID_REFERENCE", "Resource offset is outside its span.")
            source = lease.reader._source_lease
            if source is None:
                raise ToolError("SOURCE_UNAVAILABLE", "Source bytes are unavailable.")
            upper = min(total - offset, registry.config.max_bytes_per_call, budget * 3 // 4)

            def fits(count):
                dummy = "A" * (4 * ((count + 2) // 3))
                return (
                    len(wire_bytes(result_for(uri, descriptor, token, offset, count, dummy)))
                    <= budget
                )

            if fits(upper):
                count = upper
            else:
                low, high = 0, upper - 1
                while low < high:
                    middle = (low + high + 1) // 2
                    if fits(middle):
                        low = middle
                    else:
                        high = middle - 1
                count = low
            if (total > offset and count == 0) or not fits(count):
                raise ToolError(
                    "INVALID_BUDGET", "Resource budget cannot contain metadata and progress."
                )
            try:
                data = source.read_span(
                    descriptor["path"],
                    descriptor["source_sha256"],
                    descriptor["start_byte"],
                    descriptor["end_byte"],
                    offset,
                    count,
                )
            except (ValueError, OSError, RuntimeError):
                raise ToolError(
                    "SOURCE_CORRUPT", "Authenticated source bytes are unavailable."
                ) from None
            result = result_for(
                uri, descriptor, token, offset, len(data), base64.b64encode(data).decode()
            )
            if len(data) != count or len(wire_bytes(result)) > budget:
                raise ToolError("INTERNAL", "Resource framing invariant failed.")
            item = result.contents[0]
            return [ReadResourceContents(data, "application/octet-stream", meta=item.meta)]
    except ToolError as exc:
        raise McpError(error_data("RATE_LIMITED" if exc.code == "TIMEOUT" else exc.code)) from None
    except (ValueError, TypeError, KeyError, OverflowError):
        raise McpError(error_data("INVALID_REFERENCE")) from None
