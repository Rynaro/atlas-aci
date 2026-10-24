"""One MCP stdio host and a verified, resumable transport-task ledger.

No model is invoked. A transport success never claims coding-task success.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "atlas/transport-ledger.v1"
dispatch_count = 0


def canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_json(path: Path) -> Any:
    return json.loads(path.read_bytes())


def git(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.PIPE)


def index_identity(path: Path) -> str:
    """Hash consumed index facts, excluding cache mtimes and diagnostic dates."""
    tables: dict[str, Any] = {}
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
        for name, sql in connection.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ):
            quoted = '"' + name.replace('"', '""') + '"'
            columns = [
                row[1]
                for row in connection.execute(f"PRAGMA table_info({quoted})")
                if not (name == "files" and row[1] in {"mtime_ns", "indexed_at"})
            ]
            selected = ",".join('"' + column.replace('"', '""') + '"' for column in columns)
            statement = f"SELECT {selected} FROM {quoted}"
            if name == "manifest":
                # Operational correlation token, never a logical graph input.
                # Keep coverage_snapshot and every other manifest/fact row.
                statement += " WHERE key <> 'diagnostic_publication_token'"
            rows = list(connection.execute(statement))
            tables[name] = {"schema": sql, "columns": columns, "rows": sorted(rows, key=canonical)}
    return digest(tables)


def repository_identity(repo: Path, revision: str) -> dict[str, Any]:
    actual = git(repo, "rev-parse", "HEAD").decode().strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", revision) or actual != revision:
        raise ValueError("Manifest repository.revision must pin the current full Git commit")
    names = (
        git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
        .decode()
        .split("\0")
    )
    files: dict[str, Any] = {}
    for name in sorted(set(names) - {""}):
        if name == ".atlas" or name.startswith(".atlas/"):
            continue  # generated index is fingerprinted separately below
        path = repo / name
        if path.is_symlink():
            files[name] = {"symlink": os.readlink(path)}
        elif path.is_file():
            files[name] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        else:
            files[name] = {"missing": True}
    # Index changes affect search responses even when source/revision do not.
    indexes = {
        p.relative_to(repo).as_posix(): index_identity(p)
        for p in sorted((repo / ".atlas").glob("graph*.db"))
    }
    return {
        "revision": actual,
        "content_sha256": digest(files),
        "index_sha256": digest(indexes),
    }


def validate_manifest(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(manifest, dict):
        raise ValueError("Manifest must be a JSON object")
    if manifest.get("schema") != "atlas/task-manifest.v1":
        raise ValueError("Expected atlas/task-manifest.v1")
    if manifest.get("model") is not None:
        raise ValueError("This transport adapter does not run a model; model must be null")
    tasks = manifest.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("Manifest requires a nonempty ordered tasks array")
    seen = set()
    for task in tasks:
        if (
            not isinstance(task, dict)
            or not isinstance(task.get("id"), str)
            or not re.fullmatch(r"[A-Za-z0-9_-]+", task["id"])
        ):
            raise ValueError("Task id must be a safe nonempty filename")
        if task["id"] in seen:
            raise ValueError("Duplicate task id")
        seen.add(task["id"])
        if not isinstance(task.get("tool"), str) or not isinstance(task.get("arguments"), dict):
            raise ValueError("Each task requires tool and arguments")
        if "expect_text" in task and not isinstance(task["expect_text"], str):
            raise ValueError("expect_text must be text")
    return tasks


def producer_identity() -> dict[str, Any]:
    paths = [
        ROOT / "scripts/run-canaries.py",
        Path(__file__),
        *sorted((ROOT / "mcp-server/src/atlas_aci").rglob("*.py")),
    ]
    return {
        "source_sha256": digest(
            {
                p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in paths
            }
        ),
        "python": sys.version,
        "packages": {
            name: importlib.metadata.version(name)
            for name in ("mcp", "tree-sitter", "tree-sitter-language-pack", "pydantic")
        },
    }


def has_timeout(error: BaseException) -> bool:
    if isinstance(error, TimeoutError):
        return True
    return isinstance(error, BaseExceptionGroup) and any(has_timeout(e) for e in error.exceptions)


async def dispatch(
    task: dict[str, Any], repo: Path, server: list[str], deadline: float
) -> dict[str, Any]:
    # Imports stay lazy: verified replay does not even construct a host client.
    import anyio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    global dispatch_count
    dispatch_count += 1
    params = StdioServerParameters(
        command=server[0],
        args=server[1:],
        cwd=str(repo),
        env={"PYTHONPATH": str(ROOT / "mcp-server/src")},
    )
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        with anyio.fail_after(deadline):
            await session.initialize()
            response = await session.call_tool(task["tool"], task["arguments"])
            return response.model_dump(mode="json", by_alias=True, exclude_none=True)


def response_outcome(response: dict[str, Any], task: dict[str, Any]) -> str:
    if response.get("isError"):
        return "tool_error"
    for block in response.get("content", []):
        if block.get("type") == "text":
            try:
                body = json.loads(block["text"])
            except (ValueError, KeyError):
                continue
            if isinstance(body, dict) and body.get("error"):
                return "application_error"
    if task.get("expect_text") and task["expect_text"] not in json.dumps(response):
        return "expectation_failure"
    return "success"


def evidence(directory: Path, relative: str, value: Any) -> dict[str, str]:
    atomic_json(directory / relative, value)
    return {
        "path": relative,
        "sha256": hashlib.sha256((directory / relative).read_bytes()).hexdigest(),
    }


def verify_ref(directory: Path, ref: Any) -> bool:
    if not isinstance(ref, dict) or not isinstance(ref.get("path"), str):
        return False
    path = directory / ref["path"]
    if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
        return False
    return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == ref.get("sha256")


def verified_task(directory: Path, task: dict[str, Any], run_id: str) -> dict[str, Any] | None:
    try:
        receipt = read_json(directory / "tasks" / f"{task['id']}.json")
        if not isinstance(receipt, dict):
            return None
        if (
            receipt.get("schema") != SCHEMA
            or receipt.get("run_id") != run_id
            or receipt.get("task_id") != digest(task)
            or receipt.get("id") != task["id"]
            or receipt.get("status") != "COMPLETE"
        ):
            return None
        if receipt.get("outcome") not in {
            "success",
            "timeout",
            "transport_error",
            "tool_error",
            "application_error",
            "expectation_failure",
        }:
            return None
        if not verify_ref(directory, receipt.get("request")) or not verify_ref(
            directory, receipt.get("response")
        ):
            return None
        if read_json(directory / receipt["request"]["path"]) != {
            "method": "tools/call",
            "params": {"name": task["tool"], "arguments": task["arguments"]},
        }:
            return None
        duration = receipt.get("duration_seconds")
        if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration < 0:
            return None
        response = read_json(directory / receipt["response"]["path"])
        if not isinstance(response, dict):
            return None
        if "client_error" in response:
            expected = response["client_error"].get("kind")
            if expected not in {"timeout", "transport_error"}:
                return None
        else:
            if not isinstance(response.get("content"), list) or not isinstance(
                response.get("isError"), bool
            ):
                return None
            expected = response_outcome(response, task)
        return receipt if receipt["outcome"] == expected else None
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def completed_result(inputs: dict[str, Any], receipts: list[Any]) -> dict[str, Any]:
    run_id = digest(inputs)
    tasks = inputs["tasks"]
    passed = sum(r is not None and r["outcome"] == "success" for r in receipts)
    return {
        "schema": SCHEMA,
        "run_id": run_id,
        "inputs": inputs,
        "status": "COMPLETE",
        "evaluation_kind": "transport_replay",
        "task_receipts": [digest(r) for r in receipts],
        "summary": {
            "tasks": len(tasks),
            "passed": passed,
            "failed": len(tasks) - passed,
        },
        "metrics": {
            "model_tokens": None,
            "model_cost": None,
            "coding_task_success": None,
            "unavailable_reason": "No live model evaluation or usage telemetry was collected.",
        },
    }


def run_manifest(
    repo: Path,
    manifest: dict[str, Any],
    output: Path,
    *,
    server: list[str] | None = None,
    deadline: float = 10.0,
    on_task: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Execute missing tasks, or return verified existing results without dispatch."""
    global dispatch_count
    dispatch_count = 0
    if not math.isfinite(deadline) or deadline <= 0:
        raise ValueError("Deadline must be a positive finite number of seconds")
    repo, output = repo.resolve(), output.resolve()
    if output.is_relative_to(repo):
        raise ValueError("Ledger directory must be outside the indexed repository")
    tasks = validate_manifest(manifest)
    command = server or [
        sys.executable,
        "-m",
        "atlas_aci",
        "serve",
        "--repo",
        str(repo),
    ]
    if not command or not all(isinstance(arg, str) and arg for arg in command):
        raise ValueError("Server command must be a nonempty argument vector")
    inputs = {
        "schema": SCHEMA,
        "manifest_sha256": digest(manifest),
        "repository": repository_identity(repo, manifest.get("repository", {}).get("revision", "")),
        "producer": producer_identity(),
        "adapter": {
            "host": "stdio",
            "server": [
                arg.replace(str(repo), "<repo>").replace(sys.executable, "<python>")
                for arg in command
            ],
            "command_files": {
                str(i): hashlib.sha256(Path(arg).read_bytes()).hexdigest()
                for i, arg in enumerate(command)
                if Path(arg).is_file()
            },
            "deadline_seconds": deadline,
        },
        "model": None,
        "tasks": [{"task_id": digest(t), "task": t} for t in tasks],
    }
    run_id = digest(inputs)
    directory = output / run_id
    receipts = [verified_task(directory, t, run_id) for t in tasks]
    try:
        previous = read_json(directory / "run.json")
    except (OSError, ValueError):
        previous = None
    if (
        isinstance(previous, dict)
        and previous.get("schema") == SCHEMA
        and previous.get("run_id") == run_id
        and previous.get("inputs") == inputs
        and previous.get("status") == "COMPLETE"
        and all(receipts)
        and previous == completed_result(inputs, receipts)
    ):
        return previous
    atomic_json(
        directory / "run.json",
        {"schema": SCHEMA, "run_id": run_id, "inputs": inputs, "status": "IN_PROGRESS"},
    )
    for i, task in enumerate(tasks):
        if receipts[i] is not None:
            continue
        request = {
            "method": "tools/call",
            "params": {"name": task["tool"], "arguments": task["arguments"]},
        }
        request_ref = evidence(directory, f"payloads/{task['id']}.request.json", request)
        started = time.monotonic()
        try:
            response = asyncio.run(dispatch(task, repo, command, deadline))
            outcome = response_outcome(response, task)
        except Exception as exc:
            outcome = "timeout" if has_timeout(exc) else "transport_error"
            response = {"client_error": {"kind": outcome, "message": str(exc)}}
        response_ref = evidence(directory, f"payloads/{task['id']}.response.json", response)
        receipt = {
            "schema": SCHEMA,
            "id": task["id"],
            "task_id": digest(task),
            "run_id": run_id,
            "status": "COMPLETE",
            "outcome": outcome,
            "duration_seconds": time.monotonic() - started,
            "request": request_ref,
            "response": response_ref,
        }
        atomic_json(directory / "tasks" / f"{task['id']}.json", receipt)
        receipts[i] = receipt
        if on_task is not None:
            on_task(receipt)
    result = completed_result(inputs, receipts)
    atomic_json(directory / "run.json", result)
    return result


def run_cli(args: Any) -> int:
    """The stdio mode consumes CANARIES_DIR/tasks.json and writes OUTPUT ledger."""
    try:
        result = run_manifest(
            args.repo,
            read_json(args.canaries_dir / "tasks.json"),
            args.output.parent / f"{args.output.stem}.ledger",
            deadline=args.deadline,
        )
        atomic_json(args.output, result)
        failed = result["summary"]["failed"] > 0
        if args.ci_gate and args.baseline:
            baseline = read_json(args.baseline)
            old = baseline["inputs"]
            if (
                old["manifest_sha256"] != result["inputs"]["manifest_sha256"]
                or old["repository"] != result["inputs"]["repository"]
            ):
                raise ValueError("Baseline task/repository inputs do not match")
            failed |= result["summary"]["passed"] < baseline["summary"]["passed"]
        print(json.dumps(result["summary"], sort_keys=True))
        return 1 if failed else 0
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(f"Canary input error: {exc}", file=sys.stderr)
        return 2
