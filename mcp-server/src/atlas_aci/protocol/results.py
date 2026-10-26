"""SDK result validation and the actual serialized tool-result byte ceiling."""

from __future__ import annotations

import json
from typing import Any

from mcp import types
from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS

from atlas_aci.protocol.graph import (
    LEGACY_VALIDATOR,
    STRUCTURED_VALIDATOR,
    envelope,
    validate_navigation,
    validate_page,
)

STRUCTURED_VERSIONS = frozenset({"2025-06-18", "2025-11-25"})
FIXED_MESSAGES = {
    "RESPONSE_TOO_LARGE": "Result exceeds cap.",
    "INVALID_BUDGET": "Configured budget cannot represent a bounded error.",
    "INVALID_RESULT": "Generated tool result failed schema validation.",
    "INTERNAL": "Tool execution failed unexpectedly.",
    "INVALID_INPUT": "Tool arguments do not match the published schema.",
    "FORBIDDEN": "The requested tool is not available in this server.",
}


def negotiated_version(requested: str) -> str:
    """Mirror the public locked SDK initialization rule, not client identity."""
    return requested if requested in SUPPORTED_PROTOCOL_VERSIONS else types.LATEST_PROTOCOL_VERSION


def result_bytes(result: types.CallToolResult) -> bytes:
    """Same model settings used by the SDK stdio writer; no RPC ID/framing."""
    return result.model_dump_json(by_alias=True, exclude_none=True).encode("utf-8")


def sdk_result(payload: dict[str, Any], *, error: bool, structured: bool) -> types.CallToolResult:
    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text",
                text=json.dumps(
                    payload,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                ),
            )
        ],
        structuredContent=payload if structured else None,
        isError=error,
    )


def failure_payload(
    code: str, message: str, retry_hint: str, *, mode: str, operation: str = "status"
) -> dict[str, Any]:
    if mode == "structured":
        return envelope(
            None,
            error={"code": code, "message": message, "retry_hint": retry_hint},
            operation=operation,
        )
    return {"error": code, "message": message, "retry_hint": retry_hint}


def fixed_result(
    code: str, *, mode: str, structured: bool, operation: str = "status"
) -> types.CallToolResult:
    payload = failure_payload(code, FIXED_MESSAGES[code], "none", mode=mode, operation=operation)
    return sdk_result(payload, error=True, structured=structured)


# This fixed exception cap is measured from emitted INVALID_BUDGET variants,
# rather than silently raising the operator's configured tool-result ceiling.
FIXED_ERROR_ENVELOPE_CAP = max(
    len(
        result_bytes(
            fixed_result("INVALID_BUDGET", mode=mode, structured=structured, operation=operation)
        )
    )
    for operation in ("status", "neighborhood", "path", "page", "continue")
    for mode in ("legacy", "structured")
    for structured in (False, True)
)


def bounded_result(
    payload: dict[str, Any],
    *,
    error: bool,
    mode: str | None,
    structured: bool,
    budget: int,
    operation: str = "status",
    minimum_budget: int = 0,
) -> types.CallToolResult:
    intended = mode or "legacy"
    fallback = fixed_result(
        "RESPONSE_TOO_LARGE", mode=intended, structured=structured, operation=operation
    )
    minimum = max(minimum_budget, len(result_bytes(fallback)))
    if type(budget) is not int or budget < minimum:
        result = fixed_result(
            "INVALID_BUDGET", mode=intended, structured=structured, operation=operation
        )
        assert len(result_bytes(result)) <= FIXED_ERROR_ENVELOPE_CAP
        return result
    try:
        if not isinstance(payload, dict):
            raise ValueError("Tool result must be an object")
        if mode is not None:
            validator = STRUCTURED_VALIDATOR if mode == "structured" else LEGACY_VALIDATOR
            if not validator.is_valid(payload):
                raise ValueError("Invalid mode-specific result")
            if mode == "structured" and payload["operation"] != operation:
                raise ValueError("Result operation differs from request")
            if mode == "structured" and error != (payload["status"] == "error"):
                raise ValueError("Structured status and MCP error flag disagree")
            if mode == "structured" and operation in {"neighborhood", "path"}:
                validate_navigation(payload, operation, payload["bounds"]["element_limit"])
            elif mode == "structured" and operation in {"page", "continue"}:
                validate_page(payload, operation, payload["bounds"]["element_limit"])
        result = sdk_result(payload, error=error, structured=structured)
        return result if len(result_bytes(result)) <= budget else fallback
    except (TypeError, ValueError, OverflowError, RecursionError):
        result = fixed_result(
            "INVALID_RESULT", mode=intended, structured=structured, operation=operation
        )
        return result if len(result_bytes(result)) <= budget else fallback
