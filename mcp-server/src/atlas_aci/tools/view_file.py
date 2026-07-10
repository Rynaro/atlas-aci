"""view_file — windowed file read with mechanical bounds."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from atlas_aci.config import Config
from atlas_aci.enforcement import Enforcement, ToolError

# Heuristic: files we treat as opaque binaries.
BINARY_EXTENSIONS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".pdf",
        ".zip",
        ".tar",
        ".gz",
        ".bin",
        ".so",
        ".dylib",
        ".dll",
        ".exe",
        ".pyc",
        ".class",
        ".jar",
    }
)


async def view_file(
    args: dict[str, Any],
    config: Config,
    enforcement: Enforcement,
) -> dict[str, Any]:
    start_t = time.monotonic()
    path_str = args["path"]
    start_line = int(args["start_line"])
    end_line = int(args["end_line"])

    if start_line < 1:
        raise ToolError("FORBIDDEN", "start_line must be >= 1.", "none")
    if end_line < start_line:
        raise ToolError("FORBIDDEN", "end_line must be >= start_line.", "none")

    p = Path(path_str)
    abs_p = p if p.is_absolute() else config.repo / p
    enforcement.assert_path_in_repo(abs_p)

    if not abs_p.exists():
        raise ToolError("NOT_FOUND", f"File {path_str!r} does not exist.", "none")
    if not abs_p.is_file():
        raise ToolError("NOT_FOUND", f"Path {path_str!r} is not a file.", "different_tool")

    if abs_p.suffix.lower() in BINARY_EXTENSIONS:
        size = abs_p.stat().st_size
        enforcement.record(
            tool="view_file",
            args=args,
            bytes_out=0,
            duration_ms=int((time.monotonic() - start_t) * 1000),
            error="BINARY_CONTENT",
        )
        return {
            "error": "BINARY_CONTENT",
            "message": f"File appears binary; size={size} bytes.",
            "retry_hint": "different_tool",
        }

    actual_start, actual_end, _ = enforcement.cap_lines(start_line, end_line)

    try:
        with abs_p.open("r", encoding="utf-8") as f:
            all_lines = f.readlines()
    except UnicodeDecodeError:
        enforcement.record(
            tool="view_file",
            args=args,
            bytes_out=0,
            duration_ms=int((time.monotonic() - start_t) * 1000),
            error="BINARY_CONTENT",
        )
        return {
            "error": "BINARY_CONTENT",
            "message": "File contains non-UTF-8 bytes; treated as binary.",
            "retry_hint": "different_tool",
        }

    if actual_start > len(all_lines):
        raise ToolError(
            "NOT_FOUND",
            f"start_line {actual_start} exceeds file length {len(all_lines)}.",
            "narrower_scope",
        )

    selected = all_lines[actual_start - 1 : actual_end]
    body = "".join(selected)
    body_bytes = body.encode("utf-8")

    if enforcement.cap_bytes(body_bytes):
        enforcement.record(
            tool="view_file",
            args=args,
            bytes_out=len(body_bytes),
            duration_ms=int((time.monotonic() - start_t) * 1000),
            error="OVERFLOW",
        )
        return {
            "error": "OVERFLOW",
            "message": f"Line content exceeds {config.max_bytes_per_call}B; "
            f"file has unusually long lines.",
            "retry_hint": "different_tool",
        }

    total_lines = len(all_lines)

    # The invariant (NEW-1 residual, checker second pass, round 2):
    # `overflow` means "content the caller asked for was withheld" —
    # nothing less, nothing more. It is NOT "did cap_lines structurally
    # clamp the requested range" (that was the previous, still-wrong proxy:
    # asking for lines 1-5000 of a 10-line file clamps to 1-100 by
    # max_lines_per_view alone, but the file only has 10 lines, so nothing
    # was actually withheld — that must not report overflow, and must not
    # emit a next_cursor past EOF either).
    #
    # `effective_end` is the true upper bound of what the caller could
    # possibly receive: the smaller of what they asked for and what the
    # file actually has.
    effective_end = min(end_line, total_lines)
    # Overflow iff the max_lines_per_view cap cut the window short of that
    # true bound — i.e. lines that exist AND were asked for were withheld.
    overflow = actual_end < effective_end
    # next_cursor iff there is more file beyond what was returned — an
    # entirely independent fact from `overflow` (row 4 of the boundary
    # test: a fully-satisfied, un-clamped window can still have more file
    # after it). Never past EOF: only set when real content remains.
    more_file_remains = actual_end < total_lines

    result: dict[str, Any] = {
        "path": str(p),
        "start_line": actual_start,
        "end_line": actual_start + len(selected) - 1,
        "lines": selected,
    }
    if overflow:
        # `apply_central_bounds` promotes this literal key to the unified
        # `truncated` contract (F-6); it deliberately does NOT promote bare
        # `next_cursor` presence (NEW-1).
        result["overflow"] = True
    if more_file_remains:
        result["next_cursor"] = actual_end + 1
        result["total_lines"] = total_lines

    enforcement.record(
        tool="view_file",
        args=args,
        bytes_out=len(body_bytes),
        duration_ms=int((time.monotonic() - start_t) * 1000),
        overflow=overflow,
    )
    return result
