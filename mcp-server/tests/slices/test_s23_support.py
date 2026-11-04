"""ACI-S23 shared helpers: load the eval scripts by importlib spec and build fixture records.

Fixture records are built from the real S18 buffers: every span a fake result delivers is a real
slice of a frozen file (function ranges come from `ast`, match lines from a substring scan), and
cost records come from real `CodeGraph.build()` runs on a tiny tmp repo. No behaviour under test
is mocked.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parents[3]
MANIFEST = ROOT / "atlas/evals/v3/manifest.v1.json"
EVIDENCE = ROOT / "docs/evidence/v3"
RAW = EVIDENCE / "raw"
FIXTURE_PATH = ROOT / "mcp-server/tests/fixtures/s18/useful-spans.v1.json"


def load_script(name: str, path: Path) -> Any:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


agg = load_script("v3_aggregate", ROOT / "scripts/eval/v3_aggregate.py")
rec = load_script("v3_record", ROOT / "scripts/eval/v3_record.py")


def manifest() -> tuple[dict[str, Any], str]:
    return agg.load_manifest(MANIFEST)


def fixture() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FIXTURE_PATH.read_bytes())
    return data


def committed_records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = agg.read_raw_dir(RAW)
    return records


def committed_report() -> dict[str, Any]:
    report: dict[str, Any] = json.loads((EVIDENCE / "release-report.json").read_bytes())
    return report


def call_result(body: Any, is_error: bool = False) -> str:
    """A re-serialised CallToolResult exactly as the recorder stores it."""
    text = json.dumps(body, indent=2)
    return json.dumps(
        {"content": [{"type": "text", "text": text}], "isError": is_error},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def function_ranges() -> dict[str, list[tuple[str, int, int]]]:
    """name -> [(path, line_start, line_end)] from the frozen sources, via ast."""
    found: dict[str, list[tuple[str, int, int]]] = {}
    for path, text in sorted(fixture()["source_files"].items()):
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.end_lineno is not None:
                found.setdefault(node.name, []).append((path, node.lineno, node.end_lineno))
    return found


def lines_of(path: str) -> list[str]:
    return fixture()["source_files"][path].splitlines(keepends=True)


def view_body(path: str, first: int, last: int, *, display: str | None = None) -> dict[str, Any]:
    lines = lines_of(path)[first - 1 : last]
    return {
        "path": display or path,
        "start_line": first,
        "end_line": first + len(lines) - 1,
        "lines": lines,
    }


def observation(
    manifest_: dict[str, Any],
    arm: str,
    task_id: str,
    budget: int | None,
    step: int,
    tool: str,
    arguments: dict[str, Any],
    text: str,
) -> dict[str, Any]:
    key = agg.observation_key(
        arm, task_id, manifest_["task_input_sha256"], budget, step, tool, arguments
    )
    return {
        "observation_key": key,
        "task_id": task_id,
        "budget_tokens": budget,
        "step": step,
        "tool": tool,
        "arguments": arguments,
        "outcome": "tool_error" if json.loads(text)["isError"] else "success",
        "result_text": text,
        "result_sha256": hashlib.sha256(text.encode()).hexdigest(),
    }


def retrieval_record(
    manifest_: dict[str, Any],
    sha: str,
    arm: dict[str, Any],
    observations: list[dict[str, Any]],
    repo_root: str = "/repo",
) -> dict[str, Any]:
    return {
        "format": agg.RAW_FORMAT,
        "kind": "retrieval",
        "manifest_sha256": sha,
        "status": "complete",
        "arm": arm["id"],
        "variant": arm["variant"],
        "task_input_sha256": manifest_["task_input_sha256"],
        "corpus_commit": "c" * 40,
        "repo_root": repo_root,
        "observations": observations,
        "diagnostic": {"durations_seconds": {"x": 1.0}, "run_ids": ["r"], "platform": "test"},
    }


def legacy_observations(
    manifest_: dict[str, Any],
    arm: dict[str, Any],
    *,
    pad_definitions: dict[str, int] | None = None,
    display_root: str = "",
) -> list[dict[str, Any]]:
    """A policy-conformant legacy or search-read transcript over the real S18 buffers.

    `pad_definitions[term]` repeats the term's first definition row to enlarge the search result
    (the duplicates are dropped by the policy, so the planned reads do not change).
    """
    ranges = function_ranges()
    out: list[dict[str, Any]] = []
    for task in manifest_["tasks"]:
        searches = agg.search_calls(arm["policy"], task["terms"])
        texts: list[str] = []
        for step, call in enumerate(searches):
            term = task["terms"][step]
            if arm["policy"] == agg.LEGACY_POLICY:
                rows = [
                    {
                        "id": i,
                        "name": term,
                        "kind": "function",
                        "path": path,
                        "line_start": a,
                        "line_end": b,
                        "lang": "python",
                    }
                    for i, (path, a, b) in enumerate(ranges.get(term, []))
                ]
                if rows and pad_definitions and term in pad_definitions:
                    rows += [dict(rows[0], id=100 + n) for n in range(pad_definitions[term])]
                body: dict[str, Any] = {"definitions": rows, "references": []}
            else:
                matches = []
                for path, text in sorted(fixture()["source_files"].items()):
                    for number, line in enumerate(text.splitlines(), 1):
                        if term in line:
                            matches.append(
                                {
                                    "path": path,
                                    "line": number,
                                    "col": line.index(term) + 1,
                                    "preview": line[:120],
                                }
                            )
                body = {"matches": matches[:50]}
            text_ = call_result(body)
            texts.append(text_)
            out.append(
                observation(
                    manifest_,
                    arm["id"],
                    task["id"],
                    None,
                    step,
                    call["tool"],
                    call["arguments"],
                    text_,
                )
            )
        for offset, call in enumerate(agg.plan_reads(arm["policy"], texts, manifest_["policies"])):
            args = call["arguments"]
            body = view_body(args["path"], args["start_line"], args["end_line"])
            if display_root:
                body["path"] = f"{display_root}/{args['path']}"
            out.append(
                observation(
                    manifest_,
                    arm["id"],
                    task["id"],
                    None,
                    len(task["terms"]) + offset,
                    "view_file",
                    args,
                    call_result(body),
                )
            )
    return out


def context_observations(manifest_: dict[str, Any], arm: dict[str, Any]) -> list[dict[str, Any]]:
    """One context call per (task, budget); the smallest budget returns a fixed-envelope error."""
    fx = fixture()
    labelled = {t["id"]: t["useful_spans"] for t in fx["tasks"]}
    smallest = min(manifest_["budgets"]["tokens"])
    out = []
    for token in manifest_["budgets"]["tokens"]:
        for task in manifest_["tasks"]:
            args = agg.context_arguments(task, token, arm["source_policy"], manifest_["policies"])
            if token == smallest:
                body: dict[str, Any] = {
                    "status": "error",
                    "error": {"code": "INVALID_BUDGET", "message": "too small"},
                }
                text = call_result(body, is_error=True)
            else:
                sources = []
                for span in labelled[task["id"]]:
                    source = {
                        "state": "available",
                        "path": span["path"],
                        "source_sha256": span["source_sha256"],
                        "start_byte": span["start_byte"],
                        "end_byte": span["end_byte"],
                    }
                    if arm["source_policy"] == "inline_if_complete":
                        raw = fx["source_files"][span["path"]].encode()
                        data = raw[span["start_byte"] : span["end_byte"]].decode()
                        source["inline"] = {"encoding": "utf8", "data": data}
                    sources.append(source)
                body = {"status": "ok", "data": {"bundles": [{"sources": sources}]}}
                text = call_result(body)
            out.append(
                observation(
                    manifest_,
                    arm["id"],
                    task["id"],
                    token,
                    0,
                    "graph_query",
                    args,
                    text,
                )
            )
    return out


def real_cost_builds(root: Path) -> list[dict[str, Any]]:
    """Real CodeGraph.build() counters on a 2-file repo (cold 2, warm 0, noop 0, refresh 1)."""
    repo = root / "cost-repo"
    repo.mkdir()
    (repo / "a.py").write_text("def alpha():\n    return 1\n")
    (repo / "b.py").write_text("def beta():\n    return alpha()\n")
    measured: dict[str, Any] = rec.measure_index_costs(repo, ("a.py", b"\n# edit\n"))
    return list(measured["builds"])


def make_records(
    manifest_: dict[str, Any], sha: str, cost_builds: list[dict[str, Any]]
) -> dict[tuple[str, str], dict[str, Any]]:
    """A complete, consistent record set keyed by (kind, key); mutate then pass to aggregate."""
    arms = {a["id"]: a for a in manifest_["arms"]}
    records: dict[tuple[str, str], dict[str, Any]] = {}

    def add(kind: str, key: str, record: dict[str, Any]) -> None:
        records[(kind, key)] = record

    for arm_id, arm in arms.items():
        if arm["kind"] == "context":
            observations = context_observations(manifest_, arm)
        else:
            observations = legacy_observations(manifest_, arm)
        root = "/repo" if arm["variant"] == "V0" else "/tmp/atlas-v1-repo"
        add("retrieval", arm_id, retrieval_record(manifest_, sha, arm, observations, root))
    spec = manifest_["cost"]
    for corpus in spec["corpora"]:
        add(
            "cost",
            f"{corpus}.V1",
            {
                "format": agg.RAW_FORMAT,
                "kind": "cost",
                "manifest_sha256": sha,
                "status": "complete",
                "corpus": corpus,
                "variant": "V1",
                "builds": cost_builds,
                "diagnostic": {"durations_seconds": {"index.cold_full": 0.5}},
            },
        )
    v0_counters = {k: 1 for k in spec["v0_counters"]}
    v0_counters["unsupported_skipped"] = {}
    add(
        "cost",
        f"{spec['v0_corpora'][0]}.V0",
        {
            "format": agg.RAW_FORMAT,
            "kind": "cost",
            "manifest_sha256": sha,
            "status": "complete",
            "corpus": spec["v0_corpora"][0],
            "variant": "V0",
            "builds": [{"category": "index.cold_full", "counters": v0_counters}],
            "diagnostic": {},
        },
    )
    add(
        "p0",
        "all",
        {
            "format": agg.RAW_FORMAT,
            "kind": "p0",
            "manifest_sha256": sha,
            "status": "complete",
            "python": "3.12.0",
            "pytest": "9.0",
            "results": [
                {"node_id": n["id"], "testcases": 1, "outcome": "passed"}
                for n in manifest_["p0"]["node_ids"]
            ],
            "diagnostic": {"durations_seconds": {"p0": 3.0}},
        },
    )
    add(
        "offline",
        "validation_plan_comparison",
        {
            "format": agg.RAW_FORMAT,
            "kind": "offline",
            "manifest_sha256": sha,
            "status": "complete",
            "name": "validation_plan_comparison",
            "result": {"format": "atlas-validation-plan-comparison.v1"},
            "diagnostic": {},
        },
    )
    return records


def fixture_report(
    manifest_: dict[str, Any], sha: str, records: dict[tuple[str, str], dict[str, Any]]
) -> dict[str, Any]:
    report: dict[str, Any] = agg.aggregate(manifest_, list(records.values()), fixture())
    return report
