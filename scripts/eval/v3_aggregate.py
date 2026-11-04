"""ACI-S23: pure, deterministic aggregation of immutable v3 evaluation records.

Reads the frozen manifest and the gzipped raw records and writes the release report. This module
is stdlib-only and performs no clock, environment, process, network or platform access (an AST
test enforces the import list); the same records always produce the same `canonical` section.

Formats: `atlas-eval-manifest.v1`, `atlas-eval-raw.v1`, `atlas-release-report.v1`.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import re
import sys
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from pathlib import Path
from typing import Any

MANIFEST_FORMAT = "atlas-eval-manifest.v1"
RAW_FORMAT = "atlas-eval-raw.v1"
REPORT_FORMAT = "atlas-release-report.v1"
OBSERVATION_PREFIX = "atlas-eval-observation.v1:"
LEGACY_POLICY = "atlas-legacy-symbol-read.v1"
SEARCH_READ_POLICY = "atlas-search-read.v1"
CONTEXT_POLICY = "atlas-context.v1"
ROOT = Path(__file__).resolve().parents[2]

MANIFEST_KEYS = frozenset(
    {
        "format",
        "slice",
        "scope",
        "corpus",
        "tasks",
        "task_input_sha256",
        "budgets",
        "measure",
        "variants",
        "arms",
        "not_applicable",
        "policies",
        "comparisons",
        "cost",
        "engineering_outcomes",
        "p0",
        "host_matrix",
        "evidence_anchors",
        "known_failures",
        "inputs",
    }
)

LIMITATIONS = (
    "no_live_agent_evaluation_no_operator_budget",
    "four_labelled_tasks_742_bytes_no_generalisation",
    "no_thresholds_or_significance_claimed",
    "policies_are_deterministic_transcripts_not_agents",
    "byte_quarter_is_not_model_tokens",
    "result_bytes_client_measured_repo_root_normalized",
    "protocol_version_negotiated_per_server_not_controlled",
    "v0_limited_to_legacy_tools",
    "offline_validation_comparison_is_fixture_proxy",
    "grammar_cache_warm_in_cold_builds",
    "durations_diagnostic_only",
    "retrieve_impact_diff_ops_not_span_evaluated",
    "search_symbol_is_exact_name_match_so_phrase_terms_find_no_definitions",
    "v0_index_uses_a_prefetched_declared_grammar_cache",
    "search_text_match_order_is_ripgrep_thread_order_so_B_recordings_can_vary_between_runs",
)

CODES = frozenset(
    {
        "manifest_mismatch",
        "hash_mismatch",
        "duplicate_input",
        "unpaired_inputs",
        "incomplete_matrix",
        "missing_cost_category",
        "live_without_budget",
        "content_mismatch",
        "unknown_shape",
        "missing_input",
    }
)


class EvalInputError(Exception):
    """A typed, fail-closed input error; `code` is one of CODES."""

    def __init__(self, code: str, message: str = "") -> None:
        assert code in CODES, code
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message


# ---------------------------------------------------------------- formats (D1)


def canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
        + "\n"
    ).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def gzip_canonical(value: Any) -> bytes:
    return gzip.compress(canonical(value), compresslevel=9, mtime=0)


def decode_gzip_canonical(raw: bytes) -> Any:
    plain = gzip.decompress(raw)
    value = json.loads(plain)
    if canonical(value) != plain:
        raise EvalInputError("hash_mismatch", "raw record is not canonical JSON")
    return value


def ratio(num: int, den: int) -> dict[str, Any]:
    """`value` is a 6-place ROUND_HALF_EVEN decimal string, or null when the denominator is 0."""
    if den == 0:
        return {"num": num, "den": den, "value": None}
    with localcontext() as context:
        context.prec = 60
        value = (Decimal(num) / Decimal(den)).quantize(
            Decimal("0.000001"), rounding=ROUND_HALF_EVEN
        )
    return {"num": num, "den": den, "value": format(value, "f")}


# ---------------------------------------------------------------- manifest (D2)


def tree_sha256(directory: Path) -> str:
    rows = []
    for path in sorted(directory.rglob("*")):
        parts = path.relative_to(directory).parts
        if not path.is_file() or "__pycache__" in parts or ".pytest_cache" in parts:
            continue
        if path.suffix == ".pyc":
            continue
        rows.append([path.relative_to(directory).as_posix(), sha256_bytes(path.read_bytes())])
    return digest(rows)


def manifest_digest(manifest: dict[str, Any]) -> str:
    return digest(manifest)


def validate_manifest(manifest: Any) -> None:
    if not isinstance(manifest, dict) or manifest.get("format") != MANIFEST_FORMAT:
        raise EvalInputError("manifest_mismatch", "not an atlas-eval-manifest.v1")
    if set(manifest) != MANIFEST_KEYS:
        raise EvalInputError("unknown_shape", f"manifest keys {sorted(set(manifest))}")
    tasks = manifest["tasks"]
    if not tasks or manifest["task_input_sha256"] != digest(tasks):
        raise EvalInputError("manifest_mismatch", "task_input_sha256 is not sha256(tasks)")
    if not manifest["variants"] or any(
        v["task_input_sha256"] != manifest["task_input_sha256"] for v in manifest["variants"]
    ):
        raise EvalInputError("unpaired_inputs", "a variant cites a different task input digest")
    budgets = manifest["budgets"]
    tokens, limits = budgets["tokens"], budgets["max_bytes"]
    if (
        not tokens
        or len(tokens) != len(limits)
        or any(m != 4 * t for t, m in zip(tokens, limits, strict=True))
    ):
        raise EvalInputError("manifest_mismatch", "budgets violate byte_quarter.v1")
    variants = {v["id"] for v in manifest["variants"]}
    arm_ids = [a["id"] for a in manifest["arms"]]
    if not arm_ids or len(set(arm_ids)) != len(arm_ids):
        raise EvalInputError("manifest_mismatch", "duplicate or empty arms")
    if any(a["variant"] not in variants for a in manifest["arms"]):
        raise EvalInputError("manifest_mismatch", "arm cites an unknown variant")
    ids = [n["id"] for n in manifest["p0"]["node_ids"]]
    if not ids or len(set(ids)) != len(ids) or manifest["p0"]["count"] != len(ids):
        raise EvalInputError("manifest_mismatch", "p0 id count mismatch")
    if manifest["engineering_outcomes"]["live_budget"] is not None:
        raise EvalInputError("live_without_budget", "live budget declared but never spent")


def load_manifest(path: Path) -> tuple[dict[str, Any], str]:
    manifest = json.loads(path.read_bytes())
    validate_manifest(manifest)
    return manifest, manifest_digest(manifest)


def verify_corpus(manifest: dict[str, Any], root: Path) -> None:
    for corpus in manifest["corpus"]:
        target = root / corpus["path"]
        if "tree_sha256" in corpus:
            actual = tree_sha256(target) if target.is_dir() else ""
            expected = corpus["tree_sha256"]
        else:
            actual = sha256_bytes(target.read_bytes()) if target.is_file() else ""
            expected = corpus["sha256"]
        if actual != expected:
            raise EvalInputError("hash_mismatch", f"corpus {corpus['id']} differs from manifest")


def load_fixture(manifest: dict[str, Any], root: Path) -> dict[str, Any]:
    entry = next(c for c in manifest["corpus"] if c["role"] == "labelled_retrieval")
    raw = (root / entry["path"]).read_bytes()
    if sha256_bytes(raw) != entry["sha256"]:
        raise EvalInputError("hash_mismatch", "labelled corpus differs from manifest")
    fixture: dict[str, Any] = json.loads(raw)
    return fixture


# ---------------------------------------------------------------- policies (D4)


def parse_result(result_text: str) -> tuple[bool, Any]:
    """(is_error, body) of a re-serialised CallToolResult; body is None when not JSON."""
    try:
        result = json.loads(result_text)
        is_error = bool(result.get("isError"))
        body = json.loads(result["content"][0]["text"])
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        return True, None
    if isinstance(body, dict) and body.get("error") and "status" not in body:
        return True, body
    return is_error, body


def search_calls(policy: str, terms: list[str]) -> list[dict[str, Any]]:
    if policy == LEGACY_POLICY:
        return [{"tool": "search_symbol", "arguments": {"name": t}} for t in terms]
    if policy == SEARCH_READ_POLICY:
        return [
            {"tool": "search_text", "arguments": {"pattern": t, "scope": ".", "regex": False}}
            for t in terms
        ]
    raise EvalInputError("unknown_shape", f"unknown policy {policy}")


def plan_reads(
    policy: str, search_results: list[str], policies: dict[str, Any]
) -> list[dict[str, Any]]:
    """The view_file calls that follow a task's search results (pure, order-deterministic)."""
    picked: list[tuple[Any, ...]] = []
    seen: set[tuple[Any, ...]] = set()
    for index, text in enumerate(search_results):
        is_error, body = parse_result(text)
        if is_error or not isinstance(body, dict):
            continue
        rows: list[tuple[Any, ...]] = []
        if policy == LEGACY_POLICY:
            for d in body.get("definitions", []):
                rows.append((index, d["path"], d["line_start"], d["line_end"]))
        else:
            for m in body.get("matches", []):
                rows.append((index, m["path"], m["line"], m["line"]))
        for row in sorted(rows):
            key = row[1:]
            if key not in seen:
                seen.add(key)
                picked.append(row)
    picked.sort(key=lambda r: (r[0], r[1], r[2], r[3]))
    config = policies["legacy" if policy == LEGACY_POLICY else "search_read"]
    window = int(config.get("window_lines", 0))
    reads = []
    for _, path, first, last in picked[: int(config["max_reads"])]:
        if policy == LEGACY_POLICY:
            reads.append({"path": path, "start_line": first, "end_line": last})
        else:
            reads.append(
                {"path": path, "start_line": max(1, first - window), "end_line": first + window}
            )
    return [{"tool": "view_file", "arguments": a} for a in reads]


def context_arguments(
    task: dict[str, Any], budget_tokens: int, source_policy: str, policies: dict[str, Any]
) -> dict[str, Any]:
    config = policies["context"]
    return {
        "schema_version": config["schema_version"],
        "op": "context",
        "text": task["text"],
        "source_policy": source_policy,
        "max_bytes": 4 * budget_tokens,
        "max_candidates": config["max_candidates"],
        "max_inline_bytes": config["max_inline_bytes"],
    }


def observation_key(
    arm: str,
    task_id: str,
    task_input_sha256: str,
    budget_tokens: int | None,
    step: int,
    tool: str,
    arguments: dict[str, Any],
) -> str:
    return sha256_bytes(
        (
            OBSERVATION_PREFIX
            + canonical(
                {
                    "arm": arm,
                    "task_id": task_id,
                    "task_input_sha256": task_input_sha256,
                    "budget_tokens": budget_tokens,
                    "step": step,
                    "tool": tool,
                    "arguments": arguments,
                }
            ).decode()
        ).encode()
    )


# ---------------------------------------------------------------- normalisation (D3)


def check_repo_root(repo_root: str) -> None:
    if (
        not repo_root.startswith("/")
        or '"' in repo_root
        or "\\" in repo_root
        or any(ord(c) < 0x20 for c in repo_root)
    ):
        raise EvalInputError("unknown_shape", "repo_root contains a JSON-escaped character")


def root_aliases(repo_root: str) -> list[str]:
    roots = {repo_root}
    if repo_root.startswith("/private/"):
        roots.add(repo_root[len("/private") :])
    elif repo_root.startswith(("/var/", "/tmp/")):
        roots.add("/private" + repo_root)
    return sorted(roots, key=len, reverse=True)


def normalise(text: str, repo_root: str) -> tuple[str, int]:
    count = 0
    for alias in root_aliases(repo_root):
        count += text.count(alias)
        text = text.replace(alias, "/repo")
    return text, count


def repo_relative(path: str, repo_root: str) -> str:
    for alias in [*root_aliases(repo_root), "/repo"]:
        if path.startswith(alias + "/"):
            return path[len(alias) + 1 :]
    return path


# ---------------------------------------------------------------- span conversion (D5)

_LINE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+")


def line_table(text: str) -> list[tuple[int, int]]:
    rows, offset = [], 0
    for piece in _LINE.findall(text):
        size = len(piece.encode())
        rows.append((offset, offset + size))
        offset += size
    return rows


class SpanContext:
    def __init__(self, fixture: dict[str, Any], repo_root: str) -> None:
        self.fixture = fixture
        self.repo_root = repo_root
        self.tables = {p: line_table(t) for p, t in fixture["source_files"].items()}

    def source(self, path: str, start: int, end: int) -> dict[str, Any]:
        sha = self.fixture["source_hashes"][path]
        return {
            "state": "available",
            "path": path,
            "source_sha256": sha,
            "start_byte": start,
            "end_byte": end,
        }

    def rel(self, path: Any) -> str:
        if not isinstance(path, str):
            raise EvalInputError("unknown_shape", "path is not text")
        rel = repo_relative(path, self.repo_root)
        if rel not in self.tables:
            raise EvalInputError("unknown_shape", f"path {rel!r} is not in the frozen corpus")
        return rel

    def line_span(self, path: str, first: Any, last: Any) -> dict[str, Any]:
        table = self.tables[path]
        if not (
            isinstance(first, int) and isinstance(last, int) and 1 <= first <= last <= len(table)
        ):
            raise EvalInputError("unknown_shape", "line outside the frozen file")
        return self.source(path, table[first - 1][0], table[last - 1][1])


def spans_for(
    tool: str, arguments: dict[str, Any], result_text: str, ctx: SpanContext
) -> tuple[list[dict[str, Any]], int | None]:
    """Source dicts for `span_metrics.metrics` and the bundle count (context only)."""
    is_error, body = parse_result(result_text)
    if tool == "graph_query":
        module = _span_metrics()
        try:
            _, sources, count = module.body_and_sources(result_text, False)
        except (KeyError, ValueError, IndexError, TypeError) as exc:
            raise EvalInputError("unknown_shape", f"context result: {exc}") from exc
        return list(sources), int(count)
    if is_error:
        return [], None
    if not isinstance(body, dict):
        raise EvalInputError("unknown_shape", f"{tool} body is not an object")
    out: list[dict[str, Any]] = []
    try:
        if tool == "view_file":
            path = ctx.rel(body["path"])
            first, lines = body["start_line"], body["lines"]
            if (
                not isinstance(lines, list)
                or not lines
                or body["end_line"] != first + len(lines) - 1
            ):
                raise EvalInputError("unknown_shape", "view_file line window is inconsistent")
            span = ctx.line_span(path, first, body["end_line"])
            raw = ctx.fixture["source_files"][path].encode()
            text = "".join(lines)
            if text.encode() != raw[span["start_byte"] : span["end_byte"]]:
                raise EvalInputError("content_mismatch", f"{path}:{first} differs from the corpus")
            span["inline"] = {"encoding": "utf8", "data": text}
            out.append(span)
        elif tool == "search_text":
            for m in body["matches"]:
                path = ctx.rel(m["path"])
                out.append(ctx.line_span(path, m["line"], m["line"]))
        elif tool == "search_symbol":
            for d in body["definitions"]:
                path = ctx.rel(d["path"])
                out.append(ctx.line_span(path, d["line_start"], d["line_end"]))
            for r in body["references"]:
                path = ctx.rel(r["path"])
                out.append(ctx.line_span(path, r["line"], r["line"]))
        else:
            raise EvalInputError("unknown_shape", f"unsupported tool {tool}")
    except (KeyError, TypeError) as exc:
        raise EvalInputError("unknown_shape", f"{tool} result shape: {exc}") from exc
    return out, None


_SPAN_MODULE: Any = None


def _span_metrics() -> Any:
    global _SPAN_MODULE
    if _SPAN_MODULE is None:
        path = ROOT / "atlas/evals/span_metrics.py"
        spec = importlib.util.spec_from_file_location("atlas_eval_span_metrics", path)
        if spec is None or spec.loader is None:
            raise EvalInputError("unknown_shape", "span_metrics.py cannot be loaded")
        module = importlib.util.module_from_spec(spec)
        sys.modules["atlas_eval_span_metrics"] = module
        spec.loader.exec_module(module)
        _SPAN_MODULE = module
    return _SPAN_MODULE


# ---------------------------------------------------------------- aggregation (D5/D9)

INT_KEYS = (
    "calls",
    "calls_recorded",
    "errors",
    "result_bytes",
    "result_bytes_repo_normalized",
    "byte_quarter_tokens",
    "useful_label_bytes",
    "useful_reference_bytes",
    "useful_inline_bytes",
    "unique_reference_bytes",
    "unique_inline_bytes",
    "redundant_reference_bytes",
    "redundant_inline_bytes",
    "path_replacements",
)
DIFF_KEYS = (
    "calls",
    "errors",
    "result_bytes_repo_normalized",
    "byte_quarter_tokens",
    "useful_inline_bytes",
    "useful_reference_bytes",
    "unique_inline_bytes",
    "unique_reference_bytes",
    "redundant_inline_bytes",
    "redundant_reference_bytes",
)


def _finish(row: dict[str, Any]) -> dict[str, Any]:
    row["inline_recall"] = ratio(row["useful_inline_bytes"], row["useful_label_bytes"])
    row["reference_recall"] = ratio(row["useful_reference_bytes"], row["useful_label_bytes"])
    row["inline_precision"] = ratio(row["useful_inline_bytes"], row["unique_inline_bytes"])
    return row


def useful_spans(fixture: dict[str, Any], task_id: str) -> list[dict[str, Any]]:
    for task in fixture["tasks"]:
        if task["id"] == task_id:
            spans: list[dict[str, Any]] = task["useful_spans"]
            return spans
    raise EvalInputError("unpaired_inputs", f"task {task_id} is not in the labelled corpus")


def _task_row(
    ctx: SpanContext, task: dict[str, Any], observations: list[dict[str, Any]], fixture: Any
) -> dict[str, Any]:
    """Metrics for one task over its admitted observations (already prefix-filtered)."""
    sources: list[dict[str, Any]] = []
    bundles: int | None = None
    errors = 0
    raw_bytes = norm_bytes = replacements = 0
    for obs in observations:
        text, count = normalise(obs["result_text"], ctx.repo_root)
        raw_bytes += len(obs["result_text"].encode())
        norm_bytes += len(text.encode())
        replacements += count
        spans, bundle_count = spans_for(obs["tool"], obs["arguments"], obs["result_text"], ctx)
        if parse_result(obs["result_text"])[0]:
            errors += 1
        sources.extend(spans)
        if bundle_count is not None:
            bundles = (bundles or 0) + bundle_count
    try:
        computed = _span_metrics().metrics(sources, useful_spans(fixture, task["id"]), fixture)
    except ValueError as exc:
        raise EvalInputError("content_mismatch", str(exc)) from exc
    row: dict[str, Any] = {
        "task_id": task["id"],
        "calls": len(observations),
        "errors": errors,
        "result_bytes": raw_bytes,
        "result_bytes_repo_normalized": norm_bytes,
        "byte_quarter_tokens": (norm_bytes + 3) // 4,
        "bundles": bundles,
        "path_replacements": replacements,
        "selected_files": computed.pop("selected_files"),
    }
    row.update(computed)
    return row


def _admit(
    arm: dict[str, Any], observations: list[dict[str, Any]], max_bytes: int, repo_root: str
) -> list[dict[str, Any]]:
    admitted: list[dict[str, Any]] = []
    total = 0
    for obs in sorted(observations, key=lambda o: o["step"]):
        size = len(normalise(obs["result_text"], repo_root)[0].encode())
        if total + size > max_bytes:
            break
        total += size
        admitted.append(obs)
    return admitted


def _validate_retrieval(
    manifest: dict[str, Any], arm: dict[str, Any], record: dict[str, Any]
) -> dict[str, dict[Any, list[dict[str, Any]]]]:
    """Check a complete retrieval record; returns observations by task then budget."""
    tasks = {t["id"]: t for t in manifest["tasks"]}
    budgets = list(manifest["budgets"]["tokens"])
    if record.get("task_input_sha256") != manifest["task_input_sha256"]:
        raise EvalInputError("unpaired_inputs", f"{arm['id']} task input digest differs")
    if record.get("arm") != arm["id"] or record.get("variant") != arm["variant"]:
        raise EvalInputError("manifest_mismatch", f"{arm['id']} record names another arm")
    check_repo_root(record["repo_root"])
    by_task: dict[str, dict[Any, list[dict[str, Any]]]] = {}
    seen_keys: set[str] = set()
    for obs in record["observations"]:
        if obs["task_id"] not in tasks:
            raise EvalInputError("unpaired_inputs", f"{arm['id']} has an extra task")
        expected = observation_key(
            arm["id"],
            obs["task_id"],
            record["task_input_sha256"],
            obs["budget_tokens"],
            obs["step"],
            obs["tool"],
            obs["arguments"],
        )
        if obs["observation_key"] != expected or obs["observation_key"] in seen_keys:
            raise EvalInputError("hash_mismatch", f"{arm['id']} observation key mismatch")
        seen_keys.add(obs["observation_key"])
        if sha256_bytes(obs["result_text"].encode()) != obs["result_sha256"]:
            raise EvalInputError("hash_mismatch", f"{arm['id']} result_sha256 mismatch")
        by_task.setdefault(obs["task_id"], {}).setdefault(obs["budget_tokens"], []).append(obs)
    if arm["kind"] == "context":
        have = {b for per in by_task.values() for b in per}
        if not set(budgets) <= have:
            raise EvalInputError("incomplete_matrix", f"{arm['id']} lacks a budget")
        if have - set(budgets):
            raise EvalInputError("unpaired_inputs", f"{arm['id']} has an extra budget")
        for budget in budgets:
            if {t for t, per in by_task.items() if budget in per} != set(tasks):
                raise EvalInputError("unpaired_inputs", f"{arm['id']}@{budget} task set differs")
        for task_id, per in by_task.items():
            for budget, obs_list in per.items():
                expected_args = context_arguments(
                    tasks[task_id], budget, arm["source_policy"], manifest["policies"]
                )
                if (
                    len(obs_list) != 1
                    or obs_list[0]["step"] != 0
                    or obs_list[0]["tool"] != "graph_query"
                    or obs_list[0]["arguments"] != expected_args
                ):
                    raise EvalInputError("unpaired_inputs", f"{arm['id']}/{task_id} call differs")
    else:
        if set(by_task) != set(tasks):
            raise EvalInputError("unpaired_inputs", f"{arm['id']} task set differs")
        for task_id, per in by_task.items():
            if set(per) != {None}:
                raise EvalInputError("unpaired_inputs", f"{arm['id']} legacy budget not null")
            ordered = sorted(per[None], key=lambda o: o["step"])
            if [o["step"] for o in ordered] != list(range(len(ordered))):
                raise EvalInputError("unpaired_inputs", f"{arm['id']}/{task_id} steps differ")
            searches = search_calls(arm["policy"], tasks[task_id]["terms"])
            head = ordered[: len(searches)]
            if [{"tool": o["tool"], "arguments": o["arguments"]} for o in head] != searches:
                raise EvalInputError("unpaired_inputs", f"{arm['id']}/{task_id} searches differ")
            reads = plan_reads(
                arm["policy"], [o["result_text"] for o in head], manifest["policies"]
            )
            tail = [{"tool": o["tool"], "arguments": o["arguments"]} for o in ordered[len(head) :]]
            if tail != reads:
                raise EvalInputError("unpaired_inputs", f"{arm['id']}/{task_id} reads differ")
    return by_task


def _retrieval(
    manifest: dict[str, Any],
    records: dict[tuple[str, str], dict[str, Any]],
    fixture: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    tasks = {t["id"]: t for t in manifest["tasks"]}
    budgets = manifest["budgets"]
    matrix: list[dict[str, Any]] = []
    per_task: list[dict[str, Any]] = []
    pairing_arms: list[dict[str, Any]] = []
    commits: set[str] = set()
    by_cell: dict[tuple[str, int], dict[str, Any]] = {}
    for arm in manifest["arms"]:
        record = records.get(("retrieval", arm["id"]))
        if record is None:
            raise EvalInputError("incomplete_matrix", f"no retrieval record for {arm['id']}")
        if record["status"] == "not_run":
            pairing_arms.append({"arm": arm["id"], "tasks": [], "status": "not_run"})
            for token, limit in zip(budgets["tokens"], budgets["max_bytes"], strict=True):
                matrix.append(
                    {
                        "arm": arm["id"],
                        "budget_tokens": token,
                        "max_bytes": limit,
                        "status": "not_run",
                        "reason": record.get("not_run_reason", "not_run"),
                        "metrics": None,
                    }
                )
            continue
        by_task = _validate_retrieval(manifest, arm, record)
        commits.add(record["corpus_commit"])
        pairing_arms.append({"arm": arm["id"], "tasks": sorted(tasks), "status": "complete"})
        ctx = SpanContext(fixture, record["repo_root"])
        for token, limit in zip(budgets["tokens"], budgets["max_bytes"], strict=True):
            rows = []
            for task in manifest["tasks"]:
                if arm["kind"] == "context":
                    obs_list = by_task[task["id"]][token]
                else:
                    obs_list = by_task[task["id"]][None]
                admitted = _admit(arm, obs_list, limit, record["repo_root"])
                row = _task_row(ctx, task, admitted, fixture)
                row["calls_recorded"] = len(obs_list)
                row["arm"], row["budget_tokens"] = arm["id"], token
                rows.append(row)
                per_task.append(row)
            total: dict[str, Any] = {k: sum(r[k] for r in rows) for k in INT_KEYS}
            total["bundles"] = (
                sum(r["bundles"] or 0 for r in rows) if arm["kind"] == "context" else None
            )
            cell = {
                "arm": arm["id"],
                "budget_tokens": token,
                "max_bytes": limit,
                "status": "measured",
                "metrics": _finish(total),
            }
            matrix.append(cell)
            by_cell[(arm["id"], token)] = cell
    if len(commits) > 1:
        raise EvalInputError("unpaired_inputs", "corpus_commit differs between arms")
    for item in manifest["not_applicable"]:
        for token, limit in zip(budgets["tokens"], budgets["max_bytes"], strict=True):
            matrix.append(
                {
                    "arm": item["arm"],
                    "budget_tokens": token,
                    "max_bytes": limit,
                    "status": "not_applicable",
                    "reason": item["reason"],
                    "metrics": None,
                }
            )
    differences = []
    for left, right in manifest["comparisons"]:
        for token in budgets["tokens"]:
            a, b = by_cell.get((left, token)), by_cell.get((right, token))
            entry: dict[str, Any] = {"left": left, "right": right, "budget_tokens": token}
            if a is None or b is None:
                entry["status"] = "not_run"
                entry["differences"] = None
            else:
                entry["status"] = "measured"
                entry["differences"] = {k: a["metrics"][k] - b["metrics"][k] for k in DIFF_KEYS}
            differences.append(entry)
    section = {
        "budget": {
            "unit": budgets["unit"],
            "tokens": list(budgets["tokens"]),
            "max_bytes": list(budgets["max_bytes"]),
            "measure": budgets["measure"],
            "prefix_rule": budgets["prefix_rule"],
        },
        "span_matrix": matrix,
        "per_task": per_task,
        "paired_differences": differences,
    }
    pairing = {
        "task_input_sha256": manifest["task_input_sha256"],
        "corpus_commit": sorted(commits)[0] if commits else None,
        "arms": pairing_arms,
        "paired": bool(commits) and len(commits) == 1,
    }
    return section, pairing


def _cost(
    manifest: dict[str, Any],
    records: dict[tuple[str, str], dict[str, Any]],
    retrieval: dict[str, Any],
) -> dict[str, Any]:
    spec = manifest["cost"]
    out: dict[str, Any] = {}
    for category in spec["categories"]:
        if category.startswith("index."):
            per_corpus: dict[str, Any] = {}
            for corpus in spec["corpora"]:
                variants: dict[str, Any] = {}
                record = records.get(("cost", f"{corpus}.V1"))
                if record is None:
                    raise EvalInputError("missing_cost_category", f"{corpus} has no V1 cost")
                build = next((b for b in record["builds"] if b["category"] == category), None)
                if build is None:
                    raise EvalInputError("missing_cost_category", f"{corpus} lacks {category}")
                if set(spec["counters"]) - set(build["counters"]):
                    raise EvalInputError("missing_cost_category", f"{corpus} lacks counters")
                variants["V1"] = {
                    "status": "measured",
                    "counters": {k: build["counters"][k] for k in spec["counters"]},
                }
                v0 = records.get(("cost", f"{corpus}.V0"))
                if corpus not in spec["v0_corpora"]:
                    variants["V0"] = {
                        "status": "unmeasured",
                        "reason": "v0_index_cost_recorded_on_s18_only",
                    }
                elif category != "index.cold_full":
                    variants["V0"] = {
                        "status": "not_applicable",
                        "reason": "pre_v3_counters_not_exposed",
                    }
                elif v0 is None or v0["status"] != "complete":
                    variants["V0"] = {
                        "status": "unmeasured",
                        "reason": "pre_v3_counters_not_exposed",
                    }
                else:
                    v0_build = next((b for b in v0["builds"] if b["category"] == category), None)
                    if v0_build is None:
                        raise EvalInputError(
                            "missing_cost_category", f"V0 {corpus} lacks {category}"
                        )
                    variants["V0"] = {
                        "status": "measured",
                        "counters": {k: v0_build["counters"][k] for k in spec["v0_counters"]},
                    }
                per_corpus[corpus] = variants
            out[category] = per_corpus
        elif category == "query.retrieval":
            out[category] = [
                {
                    "arm": cell["arm"],
                    "budget_tokens": cell["budget_tokens"],
                    "calls": cell["metrics"]["calls"],
                    "result_bytes": cell["metrics"]["result_bytes"],
                    "result_bytes_repo_normalized": cell["metrics"]["result_bytes_repo_normalized"],
                    "byte_quarter_tokens": cell["metrics"]["byte_quarter_tokens"],
                }
                for cell in retrieval["span_matrix"]
                if cell["status"] == "measured"
            ]
        elif category == "query.model":
            out[category] = {"status": "unmeasured", "reason": "no_operator_budget"}
        else:
            raise EvalInputError("missing_cost_category", f"undeclared category {category}")
    return out


def _live(manifest: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    names = set(manifest["engineering_outcomes"]["live"])

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in names and item is not None:
                    raise EvalInputError("live_without_budget", f"live value {key} in a record")
                if key != "result_text":
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for record in records:
        if record.get("kind") == "live":
            raise EvalInputError("live_without_budget", "a live record exists without a budget")
        walk({k: v for k, v in record.items() if k != "diagnostic"})
    return {
        name: {"status": "unmeasured", "reason": "no_operator_budget", "value": None}
        for name in manifest["engineering_outcomes"]["live"]
    }


def _integrity(manifest: dict[str, Any], record: dict[str, Any] | None) -> dict[str, Any]:
    if record is None:
        raise EvalInputError("missing_input", "no p0 record")
    declared = {n["id"]: n["category"] for n in manifest["p0"]["node_ids"]}
    results = {r["node_id"]: r for r in record["results"]}
    passed = sum(
        1
        for node, row in results.items()
        if node in declared and row["outcome"] == "passed" and row["testcases"] >= 1
    )
    ok = (
        record["status"] == "complete" and set(results) == set(declared) and passed == len(declared)
    )
    return {
        "status": "pass" if ok else "fail",
        "total": len(declared),
        "passed": passed,
        "results": [
            {
                "node_id": node,
                "category": declared[node],
                "outcome": results[node]["outcome"] if node in results else "missing",
                "testcases": results[node]["testcases"] if node in results else 0,
            }
            for node in sorted(declared)
        ],
    }


def aggregate(
    manifest: dict[str, Any], raw_records: list[dict[str, Any]], fixture: dict[str, Any]
) -> dict[str, Any]:
    """The release report for validated inputs; raises EvalInputError on anything unpaired."""
    validate_manifest(manifest)
    manifest_sha = manifest_digest(manifest)
    records: dict[tuple[str, str], dict[str, Any]] = {}
    inputs = []
    diagnostics: dict[str, Any] = {}
    for record in raw_records:
        if record.get("format") != RAW_FORMAT or record.get("status") not in {
            "complete",
            "not_run",
        }:
            raise EvalInputError("unknown_shape", "not an atlas-eval-raw.v1 record")
        if record.get("manifest_sha256") != manifest_sha:
            raise EvalInputError("manifest_mismatch", "record made under another manifest")
        kind = record["kind"]
        if kind == "probe":
            key = record["variant"]
        elif kind == "retrieval":
            key = record["arm"]
        elif kind == "cost":
            key = f"{record['corpus']}.{record['variant']}"
        elif kind in {"p0"}:
            key = "all"
        elif kind == "offline":
            key = record["name"]
        elif kind == "live":
            raise EvalInputError("live_without_budget", "a live record exists without a budget")
        else:
            raise EvalInputError("unknown_shape", f"unknown record kind {kind}")
        if (kind, key) in records:
            raise EvalInputError("duplicate_input", f"{kind}.{key}")
        records[(kind, key)] = record
        body = {k: v for k, v in record.items() if k != "diagnostic"}
        inputs.append({"name": f"{kind}.{key}", "sha256": digest(body)})
        diagnostics[f"{kind}.{key}"] = record.get("diagnostic", {})
    live = _live(manifest, raw_records)
    retrieval, pairing = _retrieval(manifest, records, fixture)
    cost = _cost(manifest, records, retrieval)
    integrity = _integrity(manifest, records.get(("p0", "all")))
    probes = [
        {
            "variant": r["variant"],
            "status": r["status"],
            "protocol_version": r.get("protocol_version"),
            "server_info": r.get("server_info"),
            "tools": r.get("tools"),
            "tools_list_sha256": r.get("tools_list_sha256"),
            "image": r.get("image"),
        }
        for (kind, _), r in sorted(records.items())
        if kind == "probe"
    ]
    offline: dict[str, Any] = {}
    for item in manifest["engineering_outcomes"]["offline"]:
        proxy = records.get(("offline", item))
        if proxy is None or proxy["status"] != "complete":
            offline[item] = {
                "label": "offline_fixture_proxy",
                "status": "available_not_collected",
                "reason": (proxy or {}).get("not_run_reason", "not_recorded"),
            }
        else:
            offline[item] = {
                "label": "offline_fixture_proxy",
                "status": "measured",
                "result": proxy["result"],
            }
    measured = [c for c in retrieval["span_matrix"] if c["status"] == "measured"]
    expected_cells = len(manifest["arms"]) * len(manifest["budgets"]["tokens"])
    gates = {
        "paired_inputs": "pass" if pairing["paired"] else "fail",
        "release_integrity": integrity["status"],
        "span_matrix": "pass" if len(measured) == expected_cells else "fail",
        "cost": "pass",
        "live_agent_evaluation": "incomplete",
    }
    section = {
        "manifest_sha256": manifest_sha,
        "task_input_sha256": manifest["task_input_sha256"],
        "inputs": sorted(inputs, key=lambda i: i["name"]),
        "pairing": pairing,
        "probes": probes,
        "retrieval": retrieval,
        "cost": cost,
        "engineering_outcomes": {"live": live, "offline": offline},
        "release_integrity": integrity,
        "host_matrix": manifest["host_matrix"],
        "known_failures": manifest["known_failures"],
        "limitations": list(LIMITATIONS),
        "gates": gates,
    }
    producers = sorted(
        {digest(d["producer"]) for d in diagnostics.values() if d.get("producer") is not None}
    )
    return {
        "format": REPORT_FORMAT,
        "canonical": section,
        "canonical_sha256": digest(section),
        "diagnostic": {
            "durations_seconds": {
                k: d["durations_seconds"]
                for k, d in sorted(diagnostics.items())
                if d.get("durations_seconds")
            },
            "run_ids": sorted({r for d in diagnostics.values() for r in d.get("run_ids", [])}),
            "producers": producers,
            "platforms": sorted({d["platform"] for d in diagnostics.values() if d.get("platform")}),
        },
    }


# ---------------------------------------------------------------- markdown (canonical only)


def _cell(value: Any) -> str:
    if isinstance(value, dict) and "num" in value:
        return "n/a" if value["value"] is None else str(value["value"])
    return "" if value is None else str(value)


def render_md(report: dict[str, Any]) -> str:
    c = report["canonical"]
    lines = [
        "# ATLAS ACI v3 evidence: release report",
        "",
        f"- manifest_sha256: `{c['manifest_sha256']}`",
        f"- task_input_sha256: `{c['task_input_sha256']}`",
        f"- canonical_sha256: `{report['canonical_sha256']}`",
        f"- corpus_commit (all arms): `{c['pairing']['corpus_commit']}`",
        "",
        "Rendered from the canonical section only; replay with `v3_aggregate.py`.",
        "",
        "## Gates",
        "",
    ]
    lines += [f"- {name}: {value}" for name, value in sorted(c["gates"].items())]
    lines += ["", "## Useful-span matrix (per arm and budget; micro-sum over tasks)", ""]
    lines += [
        "| arm | budget (byte_quarter tokens) | max_bytes | status | calls | errors | "
        "result_bytes (normalised) | useful_inline | useful_reference | unique_inline | "
        "inline_recall | reference_recall | inline_precision |",
        "| --- | ---: | ---: | --- |" + " ---: |" * 9,
    ]
    for row in c["retrieval"]["span_matrix"]:
        m = row["metrics"]
        if m is None:
            lines.append(
                f"| {row['arm']} | {row['budget_tokens']} | {row['max_bytes']} | "
                f"{row['status']} ({row.get('reason', '')}) |  |  |  |  |  |  |  |  |  |"
            )
        else:
            lines.append(
                f"| {row['arm']} | {row['budget_tokens']} | {row['max_bytes']} | measured | "
                f"{m['calls']} | {m['errors']} | {m['result_bytes_repo_normalized']} | "
                f"{m['useful_inline_bytes']} | {m['useful_reference_bytes']} | "
                f"{m['unique_inline_bytes']} | {_cell(m['inline_recall'])} | "
                f"{_cell(m['reference_recall'])} | {_cell(m['inline_precision'])} |"
            )
    lines += ["", "## Paired differences (integer, left minus right)", ""]
    lines += ["| left | right | budget | " + " | ".join(DIFF_KEYS) + " |"]
    lines += ["| --- | --- | ---: |" + " ---: |" * len(DIFF_KEYS)]
    for d in c["retrieval"]["paired_differences"]:
        values = d["differences"]
        cells = ["" if values is None else str(values[k]) for k in DIFF_KEYS]
        lines.append(
            f"| {d['left']} | {d['right']} | {d['budget_tokens']} | " + " | ".join(cells) + " |"
        )
    lines += ["", "## Per-task rows", ""]
    lines += [
        "| arm | budget | task | calls | errors | result_bytes (normalised) | useful_inline | "
        "useful_reference | useful_label |",
        "| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in c["retrieval"]["per_task"]:
        lines.append(
            f"| {r['arm']} | {r['budget_tokens']} | {r['task_id']} | {r['calls']} | "
            f"{r['errors']} | "
            f"{r['result_bytes_repo_normalized']} | {r['useful_inline_bytes']} | "
            f"{r['useful_reference_bytes']} | {r['useful_label_bytes']} |"
        )
    lines += ["", "## Cost", "", "### Indexing counters (build() return values, no durations)", ""]
    lines += [
        "| category | corpus | variant | status | files_indexed | files_skipped | symbols | "
        "parser_invocations | disk_hits | misses | predecessor_reused |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for category, per_corpus in c["cost"].items():
        if not category.startswith("index."):
            continue
        for corpus, variants in per_corpus.items():
            for variant, cell in variants.items():
                k = cell.get("counters") or {}
                cache = k.get("extraction_cache") or {}
                lines.append(
                    f"| {category} | {corpus} | {variant} | {cell['status']}"
                    f"{' (' + cell['reason'] + ')' if 'reason' in cell else ''} | "
                    f"{k.get('files_indexed', '')} | {k.get('files_skipped', '')} | "
                    f"{k.get('symbols', '')} | {cache.get('parser_invocations', '')} | "
                    f"{cache.get('disk_hits', '')} | {cache.get('misses', '')} | "
                    f"{cache.get('predecessor_reused', '')} |"
                )
    lines += [
        "",
        "### query.retrieval",
        "",
        "| arm | budget | calls | result_bytes (normalised) | byte_quarter_tokens |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for q in c["cost"]["query.retrieval"]:
        lines.append(
            f"| {q['arm']} | {q['budget_tokens']} | {q['calls']} | "
            f"{q['result_bytes_repo_normalized']} | {q['byte_quarter_tokens']} |"
        )
    model = c["cost"]["query.model"]
    lines += [
        "",
        f"### query.model: {model['status']} ({model['reason']})",
        "",
    ]
    lines += ["## Engineering outcomes", "", "### Live (agent) outcomes", ""]
    for name, item in sorted(c["engineering_outcomes"]["live"].items()):
        lines.append(f"- {name}: {item['status']} ({item['reason']})")
    lines += ["", "### Offline (label: offline_fixture_proxy)", ""]
    for name, item in sorted(c["engineering_outcomes"]["offline"].items()):
        lines.append(f"- {name}: {item['status']}, label {item['label']}")
    integrity = c["release_integrity"]
    lines += [
        "",
        "## Release integrity (P0 regression cases)",
        "",
        f"status: {integrity['status']} ({integrity['passed']}/{integrity['total']} ids passed)",
        "",
        "| node id | category | outcome | testcases |",
        "| --- | --- | --- | ---: |",
    ]
    for r in integrity["results"]:
        lines.append(f"| {r['node_id']} | {r['category']} | {r['outcome']} | {r['testcases']} |")
    lines += ["", "## Probes", ""]
    for p in c["probes"]:
        lines.append(
            f"- {p['variant']}: protocol {p['protocol_version']}, server {p['server_info']}"
        )
    lines += [
        "",
        "## Host and SDK matrix",
        "",
        "| dimension | value | status | evidence |",
        "| --- | --- | --- | --- |",
    ]
    for h in c["host_matrix"]:
        lines.append(f"| {h['dimension']} | {h['value']} | {h['status']} | {h['evidence']} |")
    lines += ["", "## Known failures", ""]
    for k in c["known_failures"]:
        lines.append(f"- {k['id']} ({k['kind']}): {k['evidence']}")
    lines += ["", "## Limitations", ""]
    lines += [f"- {item}" for item in c["limitations"]]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- IO / CLI


def read_raw_dir(raw: Path) -> list[dict[str, Any]]:
    records = []
    for path in sorted(raw.glob("*.json.gz")):
        records.append(decode_gzip_canonical(path.read_bytes()))
    if not records:
        raise EvalInputError("missing_input", f"no raw records under {raw}")
    return records


def write_raw(directory: Path, record: dict[str, Any]) -> Path:
    kind = record["kind"]
    key = {
        "probe": record.get("variant"),
        "retrieval": record.get("arm"),
        "cost": f"{record.get('corpus')}.{record.get('variant')}",
        "p0": "all",
        "offline": record.get("name"),
    }[kind]
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{kind}.{key}.json.gz"
    target.write_bytes(gzip_canonical(record))
    return target


def write_report(out: Path, report: dict[str, Any]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "release-report.json").write_text(
        json.dumps(report, sort_keys=True, indent=1, ensure_ascii=False, allow_nan=False) + "\n"
    )
    (out / "release-report.md").write_text(render_md(report))


def run(manifest_path: Path, raw: Path, root: Path) -> dict[str, Any]:
    manifest, _ = load_manifest(manifest_path)
    verify_corpus(manifest, root)
    return aggregate(manifest, read_raw_dir(raw), load_fixture(manifest, root))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        report = run(args.manifest, args.raw, args.root)
    except EvalInputError as exc:
        print(f"v3_aggregate: {exc}", file=sys.stderr)
        return 2
    write_report(args.out, report)
    print(report["canonical_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
