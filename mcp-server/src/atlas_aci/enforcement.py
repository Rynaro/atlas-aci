"""Enforcement layer: bounds, read-only guard, rate limiting, telemetry.

This module exists for one reason: the security and reliability properties of
ATLAS are mechanical, not advisory. Every tool call funnels through here.

Anything an agent could do to misbehave — mutating files, exfiltrating data,
exhausting tokens via huge stdout floods — is rejected here, before reaching
the underlying tool implementation.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from atlas_aci.config import Config

log = structlog.get_logger()

# The complete read-only tool set. Anything not in here is FORBIDDEN.
READ_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        "view_file",
        "list_dir",
        "search_text",
        "search_symbol",
        "graph_query",
        "test_dry_run",
        "memex_read",
    }
)


class ToolError(Exception):
    """Structured tool error. Maps to MCP error responses with retry hints."""

    def __init__(self, code: str, message: str, retry_hint: str = "none"):
        self.code = code
        self.message = message
        self.retry_hint = retry_hint
        super().__init__(f"{code}: {message}")

    def to_dict(self) -> dict[str, str]:
        return {
            "error": self.code,
            "message": self.message,
            "retry_hint": self.retry_hint,
        }


@dataclass
class ToolCallRecord:
    """Telemetry record for one tool invocation. Wire this to your observability sink."""

    tool: str
    args: dict[str, Any]
    bytes_out: int
    duration_ms: int
    overflow: bool
    error: str | None


class Enforcement:
    """Per-server-process enforcement state.

    Owns: rate limiter, telemetry sink, the read-only invariant.
    """

    def __init__(self, config: Config):
        self.config = config
        self._call_times: deque[float] = deque(maxlen=config.max_calls_per_minute or 1)
        self.records: list[ToolCallRecord] = []  # in-memory; replace with sink in prod

    # ---- The two hard checks ----

    def assert_read_only(self, tool_name: str) -> None:
        if tool_name not in READ_ONLY_TOOLS:
            raise ToolError(
                "FORBIDDEN",
                f"Tool {tool_name!r} is not in the ATLAS read-only set. "
                f"This server does not expose write tools.",
                retry_hint="none",
            )

    def assert_path_in_repo(self, p: Path) -> None:
        if not self.config.is_in_repo(p):
            raise ToolError(
                "FORBIDDEN",
                f"Path {str(p)!r} resolves outside the repo root. Path-traversal is rejected.",
                retry_hint="none",
            )

    # ---- Rate limiting (token-bucket equivalent over a sliding window) ----

    def assert_rate_limit(self) -> None:
        if self.config.max_calls_per_minute <= 0:
            return
        now = time.monotonic()
        cutoff = now - 60.0
        # Trim old entries
        while self._call_times and self._call_times[0] < cutoff:
            self._call_times.popleft()
        if len(self._call_times) >= self.config.max_calls_per_minute:
            raise ToolError(
                "TIMEOUT",
                f"Rate limit: max {self.config.max_calls_per_minute} calls/min exceeded.",
                retry_hint="none",
            )
        self._call_times.append(now)

    # ---- Bound enforcement helpers (called by tool impls) ----

    def cap_lines(self, requested_start: int, requested_end: int) -> tuple[int, int, bool]:
        """Returns (start, end, overflow). Caller pages with end+1 if overflow."""
        max_lines = self.config.max_lines_per_view
        if requested_end - requested_start + 1 > max_lines:
            return requested_start, requested_start + max_lines - 1, True
        return requested_start, requested_end, False

    def cap_matches(self, requested_limit: int) -> int:
        return min(
            requested_limit or self.config.max_matches_per_search,
            self.config.max_matches_per_search,
        )

    def cap_entries(self, requested_limit: int | None) -> int:
        return min(
            requested_limit or self.config.max_entries_per_list, self.config.max_entries_per_list
        )

    def cap_bytes(self, body: bytes) -> bool:
        """Returns True if body is over the byte cap; caller decides what to do."""
        return len(body) > self.config.max_bytes_per_call

    def cap_list_field(self, items: list[Any]) -> tuple[list[Any], bool]:
        """The D2 central-bounds-chokepoint element cap (server.py `_call_tool`).

        Truncates a single declared list-valued field on a whole-element
        boundary — never raw byte-slicing. This is the universal backstop
        applied to *every* registered tool/verb via the `_bounded_field`
        convention, independent of whatever tool-specific cap (if any) the
        tool itself already applied. Returns (possibly-truncated list,
        overflowed).
        """
        cap = self.config.max_bound_field_elements
        if len(items) > cap:
            return items[:cap], True
        return items, False

    # ---- Telemetry ----

    def record(
        self,
        *,
        tool: str,
        args: dict[str, Any],
        bytes_out: int,
        duration_ms: int,
        overflow: bool = False,
        error: str | None = None,
    ) -> None:
        rec = ToolCallRecord(
            tool=tool,
            args=args,
            bytes_out=bytes_out,
            duration_ms=duration_ms,
            overflow=overflow,
            error=error,
        )
        self.records.append(rec)
        log.info(
            "tool_call",
            tool=tool,
            args={k: v for k, v in args.items() if k != "content"},  # don't log raw content
            bytes_out=bytes_out,
            ms=duration_ms,
            overflow=overflow,
            error=error,
        )

    def telemetry_summary(self) -> dict[str, Any]:
        """For the harness to query at mission boundaries."""
        total_calls = len(self.records)
        total_bytes = sum(r.bytes_out for r in self.records)
        per_tool: dict[str, int] = {}
        overflows = 0
        errors = 0
        for r in self.records:
            per_tool[r.tool] = per_tool.get(r.tool, 0) + 1
            overflows += int(r.overflow)
            errors += int(bool(r.error))
        return {
            "total_calls": total_calls,
            "total_bytes_out": total_bytes,
            "per_tool": per_tool,
            "overflows": overflows,
            "errors": errors,
        }
