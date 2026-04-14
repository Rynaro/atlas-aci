"""test_dry_run — sandboxed test execution.

WARNING: This tool runs code. The MVP impl is intentionally restrictive
(timeout, output cap, repo-relative path requirement). For production:

- Run inside a DevContainer or Firecracker microVM.
- Mount the repo read-only inside the sandbox.
- Block egress at the network namespace level.
- Run as a dedicated low-privilege user.

The current implementation does NONE of that. It is fine for local dev
against your own repos. Do not expose it to untrusted models without
hardening.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from atlas_aci.config import Config
from atlas_aci.enforcement import Enforcement, ToolError


async def test_dry_run(
    args: dict[str, Any],
    config: Config,
    enforcement: Enforcement,
) -> dict[str, Any]:
    start_t = time.monotonic()
    path_str = args["path"]
    case = args.get("case")

    p = Path(path_str)
    abs_p = p if p.is_absolute() else config.repo / p
    enforcement.assert_path_in_repo(abs_p)

    if not abs_p.exists() or not abs_p.is_file():
        raise ToolError("NOT_FOUND", f"Test file {path_str!r} not found.", "none")

    # Detect framework by extension/content. Extend as needed.
    if abs_p.suffix == ".rb":
        # Rails / Minitest
        cmd = ["bundle", "exec", "rails", "test", str(abs_p)]
        if case:
            cmd.extend(["-n", f"/{case}/"])
    elif abs_p.suffix == ".py":
        # pytest
        cmd = ["pytest", str(abs_p)]
        if case:
            cmd.extend(["-k", case])
    elif abs_p.suffix in (".ts", ".tsx", ".js"):
        # Jest as default; configurable later
        cmd = ["npx", "jest", str(abs_p)]
        if case:
            cmd.extend(["-t", case])
    else:
        raise ToolError(
            "FORBIDDEN",
            f"Unsupported test file extension {abs_p.suffix!r}.",
            "different_tool",
        )

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(config.repo),
    )

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=config.test_dry_run_timeout_s,
        )
    except TimeoutError as e:
        proc.kill()
        await proc.wait()
        raise ToolError(
            "TIMEOUT",
            f"test_dry_run exceeded {config.test_dry_run_timeout_s}s.",
            "narrower_scope",
        ) from e

    # Cap output to byte budget
    cap = config.max_bytes_per_call
    stdout_text = stdout_bytes[:cap].decode("utf-8", errors="replace")
    stderr_text = stderr_bytes[:cap].decode("utf-8", errors="replace")
    truncated = len(stdout_bytes) > cap or len(stderr_bytes) > cap

    result = {
        "exit_code": proc.returncode,
        "stdout": stdout_text,
        "stderr": stderr_text,
        "truncated": truncated,
    }

    enforcement.record(
        tool="test_dry_run",
        args=args,
        bytes_out=len(stdout_text) + len(stderr_text),
        duration_ms=int((time.monotonic() - start_t) * 1000),
        overflow=truncated,
    )
    return result
