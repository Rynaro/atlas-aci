"""list_dir — directory listing with bound and skip-list."""

from __future__ import annotations

import fnmatch
import time
from pathlib import Path
from typing import Any

from atlas_aci.config import Config
from atlas_aci.enforcement import Enforcement, ToolError


async def list_dir(
    args: dict[str, Any],
    config: Config,
    enforcement: Enforcement,
) -> dict[str, Any]:
    start_t = time.monotonic()
    path_str = args["path"]
    glob_pat = args.get("glob")

    p = Path(path_str)
    abs_p = p if p.is_absolute() else config.repo / p
    enforcement.assert_path_in_repo(abs_p)

    if not abs_p.exists():
        raise ToolError("NOT_FOUND", f"Directory {path_str!r} does not exist.", "none")
    if not abs_p.is_dir():
        raise ToolError("NOT_FOUND", f"Path {path_str!r} is not a directory.", "different_tool")

    cap = enforcement.cap_entries(None)

    entries: list[dict[str, Any]] = []
    overflow = False
    for child in sorted(abs_p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
        if config.should_skip(child):
            continue
        if glob_pat and not fnmatch.fnmatch(child.name, glob_pat):
            continue
        if len(entries) >= cap:
            overflow = True
            break

        try:
            stat = child.stat()
            kind = "dir" if child.is_dir() else ("symlink" if child.is_symlink() else "file")
            entries.append(
                {
                    "name": child.name,
                    "kind": kind,
                    "size": stat.st_size if kind == "file" else None,
                    "mtime": int(stat.st_mtime),
                }
            )
        except OSError:
            continue

    result: dict[str, Any] = {"path": str(p), "entries": entries}
    if overflow:
        result["overflow"] = True
        result["message"] = f"Listing capped at {cap} entries. Use a glob to narrow."

    enforcement.record(
        tool="list_dir",
        args=args,
        bytes_out=sum(len(e["name"]) for e in entries),
        duration_ms=int((time.monotonic() - start_t) * 1000),
        overflow=overflow,
    )
    return result
