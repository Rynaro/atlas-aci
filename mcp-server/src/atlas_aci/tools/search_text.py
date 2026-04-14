"""search_text — ripgrep-backed regex search with mechanical match cap."""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path
from typing import Any

from atlas_aci.config import Config
from atlas_aci.enforcement import Enforcement, ToolError

PREVIEW_MAX_CHARS = 120


async def search_text(
    args: dict[str, Any],
    config: Config,
    enforcement: Enforcement,
) -> dict[str, Any]:
    start_t = time.monotonic()
    pattern = args["pattern"]
    scope = args["scope"]
    regex = bool(args.get("regex", True))
    requested_limit = int(args.get("limit", config.max_matches_per_search))
    limit = enforcement.cap_matches(requested_limit)

    if not shutil.which("rg"):
        raise ToolError(
            "INDEX_UNAVAILABLE",
            "ripgrep (rg) is not installed on PATH.",
            "different_tool",
        )

    # Build the rg command. --json gives us structured output; we'll parse line-by-line.
    cmd = [
        "rg",
        "--json",
        "--max-count",
        str(limit + 1),  # +1 so we can detect overflow
        "--smart-case",
    ]
    if not regex:
        cmd.append("--fixed-strings")

    cmd.append(pattern)

    # Resolve scope relative to repo
    scope_path = Path(scope) if Path(scope).is_absolute() else config.repo / scope
    enforcement.assert_path_in_repo(scope_path)

    # rg accepts either a directory or a glob; if scope contains globbing chars,
    # let the shell-side rg handle it via -g
    if any(c in scope for c in "*?[]"):
        cmd.extend(["-g", scope, str(config.repo)])
    else:
        cmd.append(str(scope_path))

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(config.repo),
    )
    try:
        stdout_bytes, _stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=15.0)
    except TimeoutError as e:
        proc.kill()
        raise ToolError(
            "TIMEOUT",
            "Search exceeded 15s. Use a narrower scope or a more specific pattern.",
            "narrower_scope",
        ) from e

    matches: list[dict[str, Any]] = []
    for line in stdout_bytes.splitlines():
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "match":
            continue
        data = event["data"]
        path_text = data["path"].get("text", "")
        if not path_text:
            continue
        try:
            rel_path = str(Path(path_text).relative_to(config.repo))
        except ValueError:
            rel_path = path_text

        line_no = data["line_number"]
        # rg can give us multiple submatches per line; we want one row per match
        text = data["lines"].get("text", "").rstrip("\n")
        for sub in data.get("submatches", []):
            preview = text[:PREVIEW_MAX_CHARS]
            matches.append(
                {
                    "path": rel_path,
                    "line": line_no,
                    "col": sub["start"] + 1,
                    "preview": preview,
                }
            )
            if len(matches) > limit:
                break
        if len(matches) > limit:
            break

    overflow = len(matches) > limit
    matches = matches[:limit]

    result: dict[str, Any] = {"matches": matches}
    if overflow:
        result["overflow"] = True
        result["message"] = (
            f"Search hit the cap of {limit} matches. Narrow scope or use search_symbol."
        )

    bytes_out = sum(len(m["preview"]) + len(m["path"]) for m in matches)
    enforcement.record(
        tool="search_text",
        args=args,
        bytes_out=bytes_out,
        duration_ms=int((time.monotonic() - start_t) * 1000),
        overflow=overflow,
    )
    return result
