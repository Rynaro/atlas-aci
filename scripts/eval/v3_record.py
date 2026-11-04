"""ACI-S23 recorder: runs real servers and writes immutable raw records for v3_aggregate.

Impure by design (docker, subprocess, clock, MCP SDK sessions); it is not part of CI. Units:

- probe:     initialize + tools/list per variant (negotiated protocol, serverInfo, image identity);
- retrieval: one transcript per arm under the deterministic policies (no model);
- cost:      CodeGraph.build() counters (cold, warm, incremental no-op, refresh after an edit);
- p0:        the declared binding/snapshot regression ids, run by pytest in a subprocess;
- offline:   the S22 validation-plan comparison (a labelled fixture proxy).

Any `timeout`/`transport_error`, an empty or incomplete V0 index, or a failed P0 id aborts the
run: a transport failure must never look like zero retrieval yield.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
ANSI = re.compile(r"\x1b\[[0-9;]*m")
COUNTERS = (
    "files_indexed",
    "files_skipped",
    "files_removed",
    "symbols",
    "refs",
    "edges",
    "rationale",
    "extraction_state",
    "files_failed",
    "unsupported_skipped",
    "binding_resolution",
    "schema_epoch",
    "extraction_cache",
)
GIT_ENV = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "Atlas Eval",
    "GIT_AUTHOR_EMAIL": "eval@example.invalid",
    "GIT_COMMITTER_NAME": "Atlas Eval",
    "GIT_COMMITTER_EMAIL": "eval@example.invalid",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
}


class RecorderAbort(Exception):
    """The release run must stop: continuing would record misleading evidence."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


def _load(name: str, path: Path) -> Any:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RecorderAbort("cannot_load", str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


agg = _load("v3_aggregate", Path(__file__).resolve().parent / "v3_aggregate.py")


def runner() -> Any:
    return _load("stdio_runner", ROOT / "atlas/evals/stdio_runner.py")


# ---------------------------------------------------------------- deterministic corpora


def det_repo(dest: Path, files: dict[str, bytes]) -> str:
    """Write files and make one fully deterministic commit; equal inputs give equal commits."""
    dest.mkdir(parents=True)
    for rel, data in files.items():
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    env = {"PATH": os.environ.get("PATH", ""), "HOME": "/nonexistent", **GIT_ENV}
    config = [
        "-c",
        "commit.gpgsign=false",
        "-c",
        "core.autocrlf=false",
        "-c",
        "core.fileMode=false",
    ]
    for command in (
        ["init", "-q", "--template=", "--initial-branch=main"],
        ["add", "-A"],
        [*config, "commit", "-q", "-m", "atlas eval corpus"],
    ):
        subprocess.run(["git", *command], cwd=dest, env=env, check=True, capture_output=True)
    done = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=dest, env=env, check=True, capture_output=True, text=True
    )
    return done.stdout.strip()


def corpus_files(manifest: dict[str, Any], root: Path, corpus_id: str) -> dict[str, bytes]:
    import base64
    import gzip

    entry = next(c for c in manifest["corpus"] if c["id"] == corpus_id)
    target = root / entry["path"]
    if entry["role"] == "labelled_retrieval":
        fixture = json.loads(target.read_bytes())
        return {p: t.encode() for p, t in fixture["source_files"].items()}
    if "materialize" in entry:  # complete-source fixture: base64 files, verified per file
        data = json.loads(gzip.decompress(target.read_bytes()))
        provenance = json.loads((root / entry["provenance_path"]).read_bytes())
        expected = {f["path"]: f["sha256"] for f in provenance["files"]}
        files = {f["path"]: base64.b64decode(f["base64"]) for f in data["files"]}
        actual = {p: agg.sha256_bytes(b) for p, b in files.items()}
        if actual != expected or len(files) != provenance["file_count"]:
            raise RecorderAbort("corpus_mismatch", corpus_id)
        return files
    files = {}
    for path in sorted(target.rglob("*")):
        parts = path.relative_to(target).parts
        if (
            path.is_file()
            and "__pycache__" not in parts
            and ".pytest_cache" not in parts
            and path.suffix != ".pyc"
        ):
            files[path.relative_to(target).as_posix()] = path.read_bytes()
    return files


# ---------------------------------------------------------------- docker (D7)


def classify_docker(docker: str, image_ref: str) -> tuple[str, str]:
    """`ok`, `image_absent` or `docker_unavailable`, by exit code and stderr. Never pulls."""
    try:
        done = subprocess.run(
            [docker, "image", "inspect", image_ref], capture_output=True, text=True, timeout=120
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return "docker_unavailable", f"{type(exc).__name__}: {exc}"
    if done.returncode == 0:
        return "ok", ""
    error = done.stderr.lower()
    if "no such image" in error or "no such object" in error:
        return "image_absent", done.stderr.strip()[:300]
    return "docker_unavailable", done.stderr.strip()[:300]


def image_identity(docker: str, image_ref: str, digest: str) -> dict[str, Any]:
    done = subprocess.run(
        [docker, "image", "inspect", image_ref], capture_output=True, text=True, check=True
    )
    info = json.loads(done.stdout)[0]
    digests = sorted(info.get("RepoDigests") or [])
    if not any(d.endswith("@" + digest) for d in digests):
        raise RecorderAbort("image_digest_mismatch", f"{digests} lacks {digest}")
    return {
        "id": info.get("Id"),
        "repo_digests": digests,
        "architecture": info.get("Architecture"),
        "os": info.get("Os"),
    }


def parse_index_done(stderr: str) -> dict[str, Any] | None:
    """The last `index_done` structlog event of a v2 `atlas-aci index`, as typed counters."""
    for line in reversed(stderr.splitlines()):
        line = ANSI.sub("", line)
        if "index_done" not in line:
            continue
        fields: dict[str, Any] = {}
        for key, value in re.findall(r"(\w+)=(\{[^}]*\}|\S+)", line.split("index_done", 1)[1]):
            if value.lstrip("-").isdigit():
                fields[key] = int(value)
            elif value.startswith("{"):
                fields[key] = ast.literal_eval(value)
            else:
                fields[key] = value
        return fields
    return None


def v0_index_guard(done: dict[str, Any] | None, corpus_files_count: int) -> dict[str, Any]:
    """V0 retrieval is admissible only after a real index: never a silent-empty V0."""
    if done is None:
        raise RecorderAbort("v0_index_event_missing", "no index_done event on stderr")
    if done.get("files_indexed") != corpus_files_count or not done.get("symbols", 0) > 0:
        raise RecorderAbort(
            "v0_index_empty",
            f"files_indexed={done.get('files_indexed')} symbols={done.get('symbols')} "
            f"(corpus has {corpus_files_count} files)",
        )
    return done


def fill(template: list[str], **values: str) -> list[str]:
    return [part.format(**values) for part in template]


def verify_cache(cache: Path, declared: Path) -> str:
    """The V0 grammar cache is a declared input: its tree hash must equal the committed one."""
    expected = json.loads(declared.read_bytes())["tree_sha256"]
    actual = agg.tree_sha256(cache)
    if actual != expected:
        raise RecorderAbort("grammar_cache_mismatch", f"{actual} != {expected}")
    return actual


def run_v0_index(
    variant: dict[str, Any], repo: Path, cache: Path, docker: str, count: int
) -> dict[str, Any]:
    server = variant["server"]
    argv = fill(
        server["index_argv"],
        docker=docker,
        cache=str(cache),
        repo=str(repo),
        image=f"{server['image']}@{server['digest']}",
    )
    done = subprocess.run(argv, capture_output=True, text=True, timeout=600)
    if done.returncode != 0:
        raise RecorderAbort("v0_index_failed", done.stderr[-300:])
    return v0_index_guard(parse_index_done(done.stderr), count)


def v0_serve_argv(variant: dict[str, Any], repo: Path, cache: Path, docker: str) -> list[str]:
    server = variant["server"]
    return fill(
        server["serve_argv"],
        docker=docker,
        cache=str(cache),
        repo=str(repo),
        image=f"{server['image']}@{server['digest']}",
    )


# ---------------------------------------------------------------- records


def record(manifest_sha: str, kind: str, **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "format": agg.RAW_FORMAT,
        "kind": kind,
        "manifest_sha256": manifest_sha,
        "status": "complete",
    }
    base.update(fields)
    return base


def diagnostic(durations: dict[str, float], run_ids: list[str], argv: list[str]) -> dict[str, Any]:
    return {
        "durations_seconds": {k: round(v, 3) for k, v in durations.items()},
        "run_ids": sorted(run_ids),
        "producer": runner().producer_identity(),
        "platform": platform.platform(),
        "recorder_argv": argv,
    }


def ledger_rows(output: Path, result: dict[str, Any], tasks: list[dict[str, Any]]) -> list[Any]:
    rows = []
    for task in tasks:
        directory = output / result["run_id"]
        receipt = json.loads((directory / "tasks" / f"{task['id']}.json").read_bytes())
        response = json.loads((directory / receipt["response"]["path"]).read_bytes())
        if receipt["outcome"] in {"timeout", "transport_error"}:
            raise RecorderAbort(receipt["outcome"], f"{task['id']}: {str(response)[:300]}")
        rows.append((receipt, response))
    return rows


def run_calls(
    calls: list[dict[str, Any]],
    repo: Path,
    commit: str,
    server: list[str],
    deadline: float,
    output: Path,
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], str]:
    """Execute calls (one SDK session each) through the stdio runner; abort on transport failure."""
    task_manifest: dict[str, Any] = {
        "schema": "atlas/task-manifest.v1",
        "repository": {"revision": commit},
        "model": None,
        "tasks": [{"id": c["id"], "tool": c["tool"], "arguments": c["arguments"]} for c in calls],
    }
    result = runner().run_manifest(repo, task_manifest, output, server=server, deadline=deadline)
    return ledger_rows(output, result, task_manifest["tasks"]), result["run_id"]


def result_text(response: dict[str, Any]) -> str:
    """Deterministic re-serialisation of the ledger CallToolResult (compact, sorted)."""
    return json.dumps(response, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def observation(
    arm: str,
    task: dict[str, Any],
    task_input: str,
    budget: int | None,
    step: int,
    call: dict[str, Any],
    row: tuple[dict[str, Any], dict[str, Any]],
) -> dict[str, Any]:
    receipt, response = row
    text = result_text(response)
    return {
        "observation_key": agg.observation_key(
            arm, task["id"], task_input, budget, step, call["tool"], call["arguments"]
        ),
        "task_id": task["id"],
        "budget_tokens": budget,
        "step": step,
        "tool": call["tool"],
        "arguments": call["arguments"],
        "outcome": receipt["outcome"],
        "result_text": text,
        "result_sha256": agg.sha256_bytes(text.encode()),
    }


def record_retrieval(
    manifest: dict[str, Any],
    manifest_sha: str,
    arm: dict[str, Any],
    *,
    repo: Path,
    commit: str,
    server: list[str],
    deadline: float,
    work: Path,
    argv: list[str],
) -> dict[str, Any]:
    agg.check_repo_root(str(repo))
    task_input = manifest["task_input_sha256"]
    observations: list[dict[str, Any]] = []
    durations: dict[str, float] = {}
    run_ids: list[str] = []
    policies = manifest["policies"]
    if arm["kind"] == "context":
        calls, owners = [], []
        for token in manifest["budgets"]["tokens"]:
            for task in manifest["tasks"]:
                arguments = agg.context_arguments(task, token, arm["source_policy"], policies)
                calls.append(
                    {
                        "id": f"{task['id']}-b{token}",
                        "tool": "graph_query",
                        "arguments": arguments,
                    }
                )
                owners.append((task, token))
        rows, run_id = run_calls(calls, repo, commit, server, deadline, work / "ledger")
        run_ids.append(run_id)
        for call, (task, token), row in zip(calls, owners, rows, strict=True):
            observations.append(observation(arm["id"], task, task_input, token, 0, call, row))
            durations[call["id"]] = row[0]["duration_seconds"]
    else:
        first, owners2 = [], []
        for task in manifest["tasks"]:
            for index, call in enumerate(agg.search_calls(arm["policy"], task["terms"])):
                first.append({"id": f"{task['id']}-s{index}", **call})
                owners2.append((task, index, call))
        rows, run_id = run_calls(first, repo, commit, server, deadline, work / "ledger-1")
        run_ids.append(run_id)
        texts: dict[str, list[str]] = {}
        for call, (task, index, plain), row in zip(first, owners2, rows, strict=True):
            observations.append(observation(arm["id"], task, task_input, None, index, plain, row))
            texts.setdefault(task["id"], []).append(result_text(row[1]))
            durations[call["id"]] = row[0]["duration_seconds"]
        second, owners3 = [], []
        for task in manifest["tasks"]:
            reads = agg.plan_reads(arm["policy"], texts[task["id"]], policies)
            for offset, call in enumerate(reads):
                step = len(task["terms"]) + offset
                second.append({"id": f"{task['id']}-r{offset}", **call})
                owners3.append((task, step, call))
        if second:
            rows, run_id = run_calls(second, repo, commit, server, deadline, work / "ledger-2")
            run_ids.append(run_id)
            for call, (task, step, plain), row in zip(second, owners3, rows, strict=True):
                observations.append(
                    observation(arm["id"], task, task_input, None, step, plain, row)
                )
                durations[call["id"]] = row[0]["duration_seconds"]
    return record(
        manifest_sha,
        "retrieval",
        arm=arm["id"],
        variant=arm["variant"],
        task_input_sha256=task_input,
        corpus_commit=commit,
        repo_root=str(repo),
        observations=observations,
        diagnostic=diagnostic(durations, run_ids, argv),
    )


# ---------------------------------------------------------------- probe


async def _probe(server: list[str], cwd: Path, deadline: float) -> dict[str, Any]:
    import anyio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=server[0],
        args=server[1:],
        cwd=str(cwd),
        env={"PYTHONPATH": str(ROOT / "mcp-server/src")},
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        with anyio.fail_after(deadline):
            init = await session.initialize()
            listed = await session.list_tools()
    tools = [t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in listed.tools]
    return {
        "protocol_version": init.protocolVersion,
        "server_info": init.serverInfo.model_dump(mode="json", exclude_none=True),
        "tools": [t["name"] for t in tools],
        "tools_list_sha256": agg.digest(tools),
    }


def record_probe(
    manifest_sha: str,
    variant: str,
    server: list[str],
    cwd: Path,
    deadline: float,
    image: dict[str, Any] | None,
    argv: list[str],
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        fields = asyncio.run(_probe(server, cwd, deadline))
    except Exception as exc:  # a failed probe is a transport failure, never silent
        raise RecorderAbort("transport_error", f"probe {variant}: {exc}") from exc
    return record(
        manifest_sha,
        "probe",
        variant=variant,
        image=image,
        diagnostic=diagnostic({"probe": time.monotonic() - started}, [], argv),
        **fields,
    )


# ---------------------------------------------------------------- cost (D6)


def measure_index_costs(repo: Path, edit: tuple[str, bytes] | None = None) -> dict[str, Any]:
    """Real CodeGraph.build() counters: cold, warm (new writer), incremental no-op, refresh."""
    CodeGraph = importlib.import_module("atlas_aci.codegraph").CodeGraph

    steps: list[tuple[str, str | None]] = [
        ("index.cold_full", None),
        ("index.warm_full", None),
        ("index.incremental_noop", "incremental"),
    ]
    builds: list[dict[str, Any]] = []
    snapshots: list[str] = []
    durations: dict[str, float] = {}

    def one(category: str, since: str | None) -> None:
        graph = CodeGraph(repo)
        started = time.monotonic()
        try:
            stats = graph.build(since=since)
        finally:
            graph.close()
        durations[category] = time.monotonic() - started
        builds.append({"category": category, "counters": {k: stats[k] for k in COUNTERS}})
        snapshots.append(stats["snapshot_id"])

    for category, since in steps:
        one(category, since)
    if edit is not None:
        with (repo / edit[0]).open("ab") as handle:
            handle.write(edit[1])
    one("index.refresh_after_edit", "incremental")
    return {"builds": builds, "snapshot_ids": snapshots, "durations": durations}


# ---------------------------------------------------------------- P0 (D8)


def run_p0(ids: list[str], cwd: Path, timeout: float = 900.0) -> dict[str, Any]:
    """Run the declared ids with the same interpreter; map junit testcases back to ids."""
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("PYTEST_", "COVERAGE_"))
        and not re.fullmatch(r"ATLAS_ACI_.*_DOCKER_IMAGE", k)
    }
    with tempfile.TemporaryDirectory(prefix="atlas-p0-") as temporary:
        junit = Path(temporary) / "junit.xml"
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--basetemp",
            str(Path(temporary) / "basetemp"),
            "--junitxml",
            str(junit),
            *ids,
        ]
        started = time.monotonic()
        try:
            done = subprocess.run(
                command, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout
            )
            code, tail = done.returncode, done.stdout[-400:]
        except subprocess.TimeoutExpired:
            code, tail = -1, "timeout"
        seconds = time.monotonic() - started
        cases = ET.parse(junit).getroot().iter("testcase") if junit.exists() else iter(())
        found: dict[str, list[str]] = {i: [] for i in ids}
        for case in cases:
            classname, name = case.get("classname", ""), re.sub(r"\[.*$", "", case.get("name", ""))
            outcome = "passed"
            for child, label in (("skipped", "skipped"), ("error", "error"), ("failure", "failed")):
                if case.find(child) is not None:
                    outcome = label
            for node in ids:
                path, _, rest = node.partition("::")
                function = rest.rsplit("::", 1)[-1]
                module = path[: -len(".py")].replace("/", ".")
                if function == name and (classname == module or classname.startswith(module + ".")):
                    found[node].append(outcome)
    results = []
    for node in ids:
        outcomes = found[node]
        if not outcomes:
            outcome = "missing"
        else:
            outcome = next((o for o in ("failed", "error", "skipped") if o in outcomes), "passed")
        results.append({"node_id": node, "testcases": len(outcomes), "outcome": outcome})
    return {
        "results": results,
        "returncode": code,
        "seconds": seconds,
        "tail": tail,
        "python": sys.version.split()[0],
        "pytest": _pytest_version(),
    }


def _pytest_version() -> str:
    import pytest

    return str(pytest.__version__)


# ---------------------------------------------------------------- release run


def _v1_server(repo: Path) -> list[str]:
    return [sys.executable, "-m", "atlas_aci", "serve", "--repo", str(repo)]


def v0_not_run(
    manifest: dict[str, Any],
    manifest_sha: str,
    corpus: str,
    units: set[str],
    reason: str,
    detail: str,
) -> list[dict[str, Any]]:
    """`not_run` V0 records (recorder contract and tests only; a release run must include V0)."""
    stub = {"status": "not_run", "not_run_reason": reason, "diagnostic": {"detail": detail}}
    out = []
    if "probe" in units:
        out.append(record(manifest_sha, "probe", variant="V0", **stub))
    if "retrieval" in units:
        out.append(
            record(
                manifest_sha,
                "retrieval",
                arm="V0.legacy",
                variant="V0",
                task_input_sha256=manifest["task_input_sha256"],
                **stub,
            )
        )
    if "cost" in units:
        out.append(record(manifest_sha, "cost", corpus=corpus, variant="V0", **stub))
    return out


def release(args: argparse.Namespace) -> int:
    # The V0 container writes into its repo copy as another uid; git must not refuse it.
    os.environ.update(
        {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "safe.directory", "GIT_CONFIG_VALUE_0": "*"}
    )
    root: Path = args.root
    manifest, manifest_sha = agg.load_manifest(args.manifest)
    agg.verify_corpus(manifest, root)
    out: Path = args.out
    work = Path(tempfile.mkdtemp(prefix="v3-record-", dir=args.work))
    units = set(args.units.split(","))
    argv = sys.argv[:]
    variants = {v["id"]: v for v in manifest["variants"]}
    v0 = variants["V0"]
    docker: str = args.docker
    image_ref = f"{v0['server']['image']}@{v0['server']['digest']}"
    status, detail = classify_docker(docker, image_ref)
    s18 = next(c["id"] for c in manifest["corpus"] if c["role"] == "labelled_retrieval")
    files = corpus_files(manifest, root, s18)
    repos = {name: work / name for name in ("v0", "v1", "b")}
    commits = {name: det_repo(path, files) for name, path in repos.items()}
    if len(set(commits.values())) != 1:
        raise RecorderAbort("corpus_commit_differs", str(commits))
    commit = commits["v0"]
    written: list[Path] = []
    v0_missing = False

    def put(rec: dict[str, Any]) -> None:
        written.append(agg.write_raw(out, rec))

    arms = {a["id"]: a for a in manifest["arms"]}
    v0_done: dict[str, Any] | None = None
    cache = Path(args.v0_cache) if args.v0_cache else None
    if status != "ok" or cache is None:
        reason = status if status != "ok" else "docker_unavailable"
        v0_missing = True
        for item in v0_not_run(manifest, manifest_sha, s18, units, reason, detail):
            put(item)
    else:
        declared = root / manifest["inputs"]["v0_grammar_cache"]["path"]
        before = verify_cache(cache, declared)
        v0_done = run_v0_index(v0, repos["v0"], cache, docker, len(files))
        server0 = v0_serve_argv(v0, repos["v0"], cache, docker)
        if "probe" in units:
            identity = image_identity(docker, image_ref, v0["server"]["digest"])
            put(
                record_probe(
                    manifest_sha, "V0", server0, repos["v0"], args.deadline_v0, identity, argv
                )
            )
        if "retrieval" in units:
            put(
                record_retrieval(
                    manifest,
                    manifest_sha,
                    arms["V0.legacy"],
                    repo=repos["v0"],
                    commit=commit,
                    server=server0,
                    deadline=args.deadline_v0,
                    work=work / "v0-ledger",
                    argv=argv,
                )
            )
        if "cost" in units:
            counters = {k: v0_done[k] for k in manifest["cost"]["v0_counters"]}
            put(
                record(
                    manifest_sha,
                    "cost",
                    corpus=s18,
                    variant="V0",
                    builds=[{"category": "index.cold_full", "counters": counters}],
                    diagnostic=diagnostic({}, [], argv),
                )
            )
        if verify_cache(cache, declared) != before:
            raise RecorderAbort("grammar_cache_mutated")
    # HEAD arms (V1 and B) run from this checkout.
    CodeGraph = importlib.import_module("atlas_aci.codegraph").CodeGraph

    graph = CodeGraph(repos["v1"])
    graph.build()
    graph.close()
    if "probe" in units:
        put(
            record_probe(
                manifest_sha, "V1", _v1_server(repos["v1"]), repos["v1"], args.deadline, None, argv
            )
        )
    if "retrieval" in units:
        for arm_id, repo_name in (
            ("V1.legacy", "v1"),
            ("V1.context.references", "v1"),
            ("V1.context.inline", "v1"),
            ("B.search_read", "b"),
        ):
            repo = repos[repo_name]
            put(
                record_retrieval(
                    manifest,
                    manifest_sha,
                    arms[arm_id],
                    repo=repo,
                    commit=commit,
                    server=_v1_server(repo),
                    deadline=args.deadline,
                    work=work / f"ledger-{arm_id}",
                    argv=argv,
                )
            )
    if "cost" in units:
        for corpus in manifest["cost"]["corpora"]:
            repo = work / f"cost-{corpus}"
            det_repo(repo, corpus_files(manifest, root, corpus))
            edit = manifest["cost"]["refresh_edit"][corpus]
            measured = measure_index_costs(repo, (edit["path"], edit["append_bytes"].encode()))
            put(
                record(
                    manifest_sha,
                    "cost",
                    corpus=corpus,
                    variant="V1",
                    builds=measured["builds"],
                    diagnostic={
                        **diagnostic(measured["durations"], [], argv),
                        "snapshot_ids": measured["snapshot_ids"],
                    },
                )
            )
    exit_code = 0
    if "p0" in units:
        ids = [n["id"] for n in manifest["p0"]["node_ids"]]
        p0 = run_p0(ids, ROOT / "mcp-server")
        put(
            record(
                manifest_sha,
                "p0",
                python=p0["python"],
                pytest=p0["pytest"],
                results=p0["results"],
                diagnostic=diagnostic({"p0": p0["seconds"]}, [], argv),
            )
        )
        if any(r["outcome"] != "passed" for r in p0["results"]):
            print("v3_record: a P0 regression case did not pass", file=sys.stderr)
            exit_code = 3
        if p0["seconds"] > 300:
            print(
                f"v3_record: P0 ran {p0['seconds']:.0f}s (> 300s escalation bound)", file=sys.stderr
            )
            exit_code = 3
    if "offline" in units:
        comparison = _load(
            "validation_plan_comparison", ROOT / "atlas/evals/validation_plan_comparison.py"
        )
        with tempfile.TemporaryDirectory(prefix="atlas-offline-") as temporary:
            result = comparison.comparison(Path(temporary))
        put(
            record(
                manifest_sha,
                "offline",
                name="validation_plan_comparison",
                result=result,
                diagnostic=diagnostic({}, [], argv),
            )
        )
    shutil.rmtree(work, ignore_errors=True)
    if v0_missing and not args.allow_not_run:
        print("v3_record: V0 was not run; a release run must include V0", file=sys.stderr)
        return 4
    for path in written:
        print(path)
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--work", type=Path, default=Path(tempfile.gettempdir()))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--v0-cache", type=Path, default=None)
    parser.add_argument("--units", default="probe,retrieval,cost,p0,offline")
    parser.add_argument("--deadline", type=float, default=60.0)
    parser.add_argument("--deadline-v0", type=float, default=90.0)
    parser.add_argument("--allow-not-run", action="store_true", help="tests only")
    args = parser.parse_args(argv)
    try:
        return release(args)
    except (RecorderAbort, agg.EvalInputError) as exc:
        print(f"v3_record: ABORT {exc}", file=sys.stderr)
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
