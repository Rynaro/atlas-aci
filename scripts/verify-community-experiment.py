#!/usr/bin/env python3
"""ACI-S36 verifier and library of record for the community experiment.

Standard library only. It holds every frozen constant, the closed
vocabularies, strict loading, the shared projection, the exact metrics, the
usefulness proxy and the decision rule. The runner imports the very same
functions, so the runner and this verifier cannot drift; correctness rests on
independent oracles in the tests.

The verifier recomputes every number from the retained partitions and the
frozen task evidence. It never reads thresholds, seeds, budgets or bounds
from the artifacts it audits. Leiden partitions are validated and scored, not
regenerated; the shipped LPA is re-run by file path.

Usage:
  python scripts/verify-community-experiment.py --results DIR [--manifest M]
      [--manifest-sha256 HEX] [--repo-root R] [--max-artifact-bytes N]
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import sys
from collections import Counter
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from fractions import Fraction
from pathlib import Path
from typing import Any, NoReturn

sys.dont_write_bytecode = True

# ----------------------------------------------------------------- vocabularies

RUNNER_REJECTIONS = (
    "manifest_unreadable",
    "manifest_invalid",
    "manifest_digest_mismatch",
    "manifest_constants_mismatch",
    "provider_pin_invalid",
    "input_unreadable",
    "input_too_large",
    "input_hash_mismatch",
    "task_anchor_mismatch",
    "graph_invalid",
    "graph_too_large",
    "provider_required_missing",
    "provider_import_failed",
    "provider_version_mismatch",
    "provider_changed_during_run",
    "measurement_failed",
    "run_timeout",
    "partition_invalid",
    "self_check_failed",
    "output_write_failed",
)
PARTITION_REJECTIONS = (
    "partition_invalid_type",
    "partition_node_unknown",
    "partition_node_duplicated",
    "partition_node_omitted",
    "partition_noncanonical",
    "partition_node_table_mismatch",
)
VERIFIER_REJECTIONS = (
    *RUNNER_REJECTIONS[:11],
    "artifact_unreadable",
    "artifact_too_large",
    "artifact_invalid_json",
    "artifact_schema_invalid",
    "canonical_digest_mismatch",
    "partitions_digest_mismatch",
    "manifest_reference_mismatch",
    "projection_mismatch",
    "run_set_mismatch",
    *PARTITION_REJECTIONS,
    "lpa_rerun_mismatch",
    "metric_mismatch",
    "usefulness_mismatch",
    "decision_mismatch",
    "skipped_state_inconsistent",
    "provider_record_mismatch",
    "telemetry_invalid",
)
RUNNER_USAGE_NAMES = (
    "command",
    "manifest",
    "manifest_sha256",
    "out",
    "out_exists",
    "out_inside_mcp_server",
    "repo_root",
    "arguments",
)
VERIFIER_USAGE_NAMES = (
    "results",
    "manifest",
    "manifest_sha256",
    "repo_root",
    "max_artifact_bytes",
    "arguments",
)
STATUSES = ("complete", "skipped_missing_provider")
OUTCOMES = (
    "retain_lpa",
    "leiden_candidate_for_review",
    "inconclusive",
    "skipped_missing_provider",
)
REASONS = (
    "provider_missing",
    "no_task_evidence",
    "task_graph_partitions_identical",
    "usefulness_regression",
    "no_usefulness_gain",
    "stability_below_floor",
    "modularity_below_lpa",
)
SEED_TIERS = ("exact_name", "lexical_file", "none")
PROVIDER_KINDS = ("atlas_lpa", "real", "fixture")
LIMITATIONS = (
    "task_graph_is_small_and_may_not_discriminate",
    "usefulness_is_an_offline_node_level_proxy_not_agent_success",
    "graph_only_repositories_have_no_gold_labels",
    "leiden_results_are_pinned_to_provider_versions_and_platform",
    "fixture_provider_results_are_not_leiden_measurements",
    "timing_and_memory_are_telemetry_not_canonical",
    "modularity_alone_never_selects_an_algorithm",
    "node_budgets_not_byte_budgets",
    "production_default_remains_label_propagation",
    "deployment_selection_requires_a_separately_reviewed_decision",
    "seeding_restates_the_s17_tiers_offline_and_does_not_call_graph_query_retrieve",
    "lexical_seeding_is_a_case_sensitive_whole_file_substring",
)

# --------------------------------------------------------------------- formats

MANIFEST_FORMAT = "atlas-community-experiment-manifest.v1"
GRAPH_FORMAT = "atlas-community-graph.v1"
PARTITIONS_FORMAT = "atlas-community-partitions.v1"
RESULT_FORMAT = "atlas-community-experiment-result.v1"
TELEMETRY_FORMAT = "atlas-community-telemetry.v1"
SUMMARY_FORMAT = "atlas-community-run-summary.v1"
VERIFICATION_FORMAT = "atlas-community-verification.v1"
PROJECTION_RULE = "atlas-community-projection.v1"
DECISION_RULE = "atlas-community-decision.v1"
USEFULNESS_PROXY = "offline_community_expansion_proxy.v1"
EXPERIMENT_ID = "leiden-vs-lpa.v1"
FIXTURE_MARK = "+atlas.fixture"

# ------------------------------------------------------- frozen external facts
# Never read from, or derived from, an artifact under audit.

FROZEN_SEEDS = tuple(range(10))
FROZEN_BUDGETS = (2, 4, 8)
FROZEN_PARTITION_TYPE = "ModularityVertexPartition"
FROZEN_N_ITERATIONS = -1
FROZEN_LPA_MAX_ITERATIONS = 100
FROZEN_LPA_MODULE_PATH = "mcp-server/src/atlas_aci/label_propagation.py"
FROZEN_TASK_INPUT_SHA256 = "236c0def27a416dd93a411a4e40d2666510320c4d9f9cfff08f29a1145cc8fe6"
FROZEN_USEFUL_SPANS_PATH = "mcp-server/tests/fixtures/s18/useful-spans.v1.json"
FROZEN_USEFUL_SPANS_SHA256 = "07553bd3d51be01a1d522fc59c240bf41ecf312a051c413c609585c60999b507"
FROZEN_S23_MANIFEST_PATH = "atlas/evals/v3/manifest.v1.json"
FROZEN_BUNDLE_PATH = ".spectra/changes/aci-s17/probe/probe-graphs.json.gz"
FROZEN_BUNDLE_SHA256 = "c5bfe09753acccb2652e1ea730079e3269ed47b2ed58ee6d3ea642fc08ff1ffe"
FROZEN_BUNDLE_KEYS = ("solidus", "spree")
FROZEN_STABILITY_FLOOR = Fraction(9, 10)
FROZEN_MIN_RECALL_GAIN_BUDGETS = 1
FROZEN_MODULARITY_MARGIN = Fraction(0, 1)
FROZEN_PROJECTION = {
    "rule": PROJECTION_RULE,
    "directed": False,
    "weighted": False,
    "node_order": "path_line_name_ascending",
    "self_loops": "dropped",
    "multi_edges": "collapsed",
    "isolated_nodes": "retained",
}
FROZEN_BOUNDS = {
    "max_input_bytes": 16777216,
    "max_nodes": 200000,
    "max_edges": 1000000,
    "run_timeout_seconds": 600,
}
QUALITY_FIELDS = (
    "modularity",
    "coverage",
    "community_count",
    "singleton_count",
    "size_max",
    "size_median",
    "size_p90",
    "disconnected_communities",
    "disconnected_share",
)
STABILITY_FIELDS = ("pairwise_ari", "identical_pairs", "lpa_vs_leiden_ari")
FROZEN_METRICS = {
    "arithmetic": "exact_fraction_ratio6_half_even",
    "quality": list(QUALITY_FIELDS),
    "stability": list(STABILITY_FIELDS),
    "usefulness": {"proxy": USEFULNESS_PROXY, "budgets_nodes": list(FROZEN_BUDGETS)},
}
FROZEN_DECISION = {
    "rule": DECISION_RULE,
    "stability_min_mean_pairwise_ari": {"num": 9, "den": 10},
    "min_budgets_with_recall_gain": 1,
    "modularity_margin": {"num": 0, "den": 1},
    "outcomes": list(OUTCOMES),
}
FROZEN_TELEMETRY = {
    "canonical": False,
    "time": "perf_counter_ns_partitioner_call",
    "memory": "ru_maxrss_fresh_subprocess_per_run",
}
# The sha256 of the committed production manifest bytes (frozen at Day-1 qualification).
PRODUCTION_MANIFEST_SHA256 = "3239ec59d9fce113a115ff4b27c42bfbf06715060e1b2cb7e7de8e2e435a2beb"
PRODUCTION_MANIFEST_PATH = "mcp-server/tests/fixtures/community_experiment/manifest.v1.json"

DEFAULT_MAX_ARTIFACT_BYTES = 67108864
MIN_ARTIFACT_BYTES = 1
MAX_ARTIFACT_BYTES = 268435456
MAX_MANIFEST_BYTES = 1048576
HEX_DIGITS = "0123456789abcdef"


class Rejection(Exception):
    """A typed, closed-vocabulary rejection; never carries free text."""

    def __init__(self, reason: str, detail: str | None = None) -> None:
        detail = detail or None
        super().__init__(reason if detail is None else f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


# ------------------------------------------------------------------ primitives


def canonical(value: Any) -> bytes:
    text = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return (text + "\n").encode("utf-8")


def sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def digest_of(value: Any) -> str:
    return sha256_hex(canonical(value))


def is_sha(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in HEX_DIGITS for c in value)


def ratio_of(num: int, den: int) -> dict[str, Any]:
    """A reduced ratio with a 6-place ROUND_HALF_EVEN decimal string, or null at den 0."""
    if den == 0:
        return {"num": 0, "den": 0, "value": None}
    reduced = Fraction(num, den)
    with localcontext() as context:
        context.prec = 60
        quant = Decimal(reduced.numerator) / Decimal(reduced.denominator)
        value = quant.quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN)
    return {"num": reduced.numerator, "den": reduced.denominator, "value": format(value, "f")}


def ratio_frac(value: Fraction) -> dict[str, Any]:
    return ratio_of(value.numerator, value.denominator)


def frac_of(ratio: dict[str, Any]) -> Fraction | None:
    if ratio["den"] == 0:
        return None
    return Fraction(ratio["num"], ratio["den"])


def load_strict(raw: bytes, *, allow_floats: bool = False) -> Any:
    """Strict JSON: UTF-8 without BOM, no duplicate keys, no NaN/Infinity, no floats."""
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ValueError("bom")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        seen: dict[str, Any] = {}
        for key, value in items:
            if key in seen:
                raise ValueError("duplicate key")
            seen[key] = value
        return seen

    def constant(name: str) -> Any:
        raise ValueError("non-finite number")

    def number(text: str) -> Any:
        raise ValueError("float")

    kwargs: dict[str, Any] = {"object_pairs_hook": pairs, "parse_constant": constant}
    if not allow_floats:
        kwargs["parse_float"] = number
    try:
        return json.loads(raw.decode("utf-8"), **kwargs)
    except (RecursionError, MemoryError) as exc:
        raise ValueError("too deep") from exc


def read_bounded(path: Path, limit: int, unreadable: str, too_large: str, detail: str) -> bytes:
    """Read at most `limit` bytes of a regular file; one more byte means too large."""
    try:
        if not path.is_file():
            raise Rejection(unreadable, detail)
        with path.open("rb") as handle:
            data = handle.read(limit + 1)
    except OSError as exc:
        raise Rejection(unreadable, detail) from exc
    if len(data) > limit:
        raise Rejection(too_large, detail)
    return data


def read_gzip_bounded(
    path: Path, limit: int, unreadable: str, too_large: str, detail: str
) -> tuple[bytes, bytes]:
    """Return (compressed, decompressed) bytes, both bounded by `limit`."""
    compressed = read_bounded(path, limit, unreadable, too_large, detail)
    try:
        with gzip.open(path, "rb") as handle:
            plain = handle.read(limit + 1)
    except (OSError, EOFError, ValueError) as exc:
        raise Rejection(unreadable, detail) from exc
    if len(plain) > limit:
        raise Rejection(too_large, detail)
    return compressed, plain


def load_module(path: Path, name: str) -> Any:
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ImportError("cannot load module")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def is_int(value: Any) -> bool:
    return type(value) is int


def check_shape(value: Any, spec: Any) -> bool:
    """Closed-shape check without recursion. A spec is a type, a closed dict, a one
    element list (any length), ("null", spec) for a nullable value, or "any"."""
    stack = [(value, spec)]
    while stack:
        item, rule = stack.pop()
        if rule == "any":
            continue
        if rule is int or rule is str or rule is bool:
            if type(item) is not rule:
                return False
        elif isinstance(rule, dict):
            if not isinstance(item, dict) or set(item) != set(rule):
                return False
            stack.extend((item[key], sub) for key, sub in rule.items())
        elif isinstance(rule, list):
            if not isinstance(item, list):
                return False
            stack.extend((element, rule[0]) for element in item)
        elif isinstance(rule, tuple):
            if item is not None:
                stack.append((item, rule[1]))
        else:
            return False
    return True


def plain_relative(path: Any) -> bool:
    if not isinstance(path, str) or not path or not path.isascii() or "\\" in path:
        return False
    parts = Path(path).parts
    return not Path(path).is_absolute() and ".." not in parts and "" not in parts


# ------------------------------------------------------------------- manifest

RATIO_SPEC = {"num": int, "den": int}
MANIFEST_SPEC: dict[str, Any] = {
    "format": str,
    "slice": str,
    "experiment_id": str,
    "projection": {
        "rule": str,
        "directed": bool,
        "weighted": bool,
        "node_order": str,
        "self_loops": str,
        "multi_edges": str,
        "isolated_nodes": str,
    },
    "graphs": ["any"],
    "tasks": {
        "graph_id": str,
        "s23_manifest_path": str,
        "task_input_sha256": str,
        "items": [{"corpus": str, "id": str, "terms": [str], "text": str}],
        "useful_spans": {"path": str, "sha256": str},
    },
    "algorithms": {
        "lpa": {"module_path": str, "module_sha256": str, "max_iterations": int, "seeded": bool},
        "leiden": {
            "provider": {"kind": str, "distributions": {"igraph": str, "leidenalg": str}},
            "partition_type": str,
            "n_iterations": int,
            "seeds": [int],
        },
    },
    "metrics": {
        "arithmetic": str,
        "quality": [str],
        "stability": [str],
        "usefulness": {"proxy": str, "budgets_nodes": [int]},
    },
    "decision": {
        "rule": str,
        "stability_min_mean_pairwise_ari": RATIO_SPEC,
        "min_budgets_with_recall_gain": int,
        "modularity_margin": RATIO_SPEC,
        "outcomes": [str],
    },
    "bounds": {
        "max_input_bytes": int,
        "max_nodes": int,
        "max_edges": int,
        "run_timeout_seconds": int,
    },
    "telemetry": {"canonical": bool, "time": str, "memory": str},
}
GRAPH_KEYS = {"id", "role", "kind", "path", "sha256"}


def validate_manifest_shape(manifest: Any) -> None:
    if not check_shape(manifest, MANIFEST_SPEC):
        raise Rejection("manifest_invalid")
    if manifest["format"] != MANIFEST_FORMAT or manifest["slice"] != "ACI-S36":
        raise Rejection("manifest_invalid")
    seen: set[str] = set()
    for entry in manifest["graphs"]:
        if not isinstance(entry, dict) or set(entry) not in (GRAPH_KEYS, GRAPH_KEYS | {"key"}):
            raise Rejection("manifest_invalid")
        kinds = {"graph_file": GRAPH_KEYS, "probe_bundle": GRAPH_KEYS | {"key"}}
        if entry["kind"] not in kinds or set(entry) != kinds[entry["kind"]]:
            raise Rejection("manifest_invalid")
        text_keys = ("id", "role", "kind", "path", "sha256", "key")
        if any(not isinstance(entry[k], str) for k in text_keys if k in entry):
            raise Rejection("manifest_invalid")
        if entry["role"] not in ("task_graph", "graph_only") or not plain_relative(entry["path"]):
            raise Rejection("manifest_invalid")
        if not is_sha(entry["sha256"]) or not entry["id"] or entry["id"] in seen:
            raise Rejection("manifest_invalid")
        seen.add(entry["id"])
    if not manifest["graphs"]:
        raise Rejection("manifest_invalid")
    paths = [
        manifest["tasks"]["s23_manifest_path"],
        manifest["tasks"]["useful_spans"]["path"],
        manifest["algorithms"]["lpa"]["module_path"],
    ]
    if not all(plain_relative(p) for p in paths):
        raise Rejection("manifest_invalid")
    if not is_sha(manifest["algorithms"]["lpa"]["module_sha256"]):
        raise Rejection("manifest_invalid")
    task_graphs = [g for g in manifest["graphs"] if g["role"] == "task_graph"]
    if len(task_graphs) != 1 or task_graphs[0]["id"] != manifest["tasks"]["graph_id"]:
        raise Rejection("manifest_invalid")


def check_manifest_constants(manifest: dict[str, Any]) -> None:
    """Every frozen value must match; the manifest can never lower a bar."""
    tasks = manifest["tasks"]
    lpa = manifest["algorithms"]["lpa"]
    leiden = manifest["algorithms"]["leiden"]
    frozen_ok = (
        manifest["experiment_id"] == EXPERIMENT_ID
        and manifest["projection"] == FROZEN_PROJECTION
        and manifest["metrics"] == FROZEN_METRICS
        and manifest["decision"] == FROZEN_DECISION
        and manifest["bounds"] == FROZEN_BOUNDS
        and manifest["telemetry"] == FROZEN_TELEMETRY
        and tuple(leiden["seeds"]) == FROZEN_SEEDS
        and leiden["partition_type"] == FROZEN_PARTITION_TYPE
        and leiden["n_iterations"] == FROZEN_N_ITERATIONS
        and lpa["max_iterations"] == FROZEN_LPA_MAX_ITERATIONS
        and lpa["seeded"] is False
        and lpa["module_path"] == FROZEN_LPA_MODULE_PATH
        and tasks["task_input_sha256"] == FROZEN_TASK_INPUT_SHA256
        and tasks["s23_manifest_path"] == FROZEN_S23_MANIFEST_PATH
        and tasks["useful_spans"]["path"] == FROZEN_USEFUL_SPANS_PATH
        and tasks["useful_spans"]["sha256"] == FROZEN_USEFUL_SPANS_SHA256
        and digest_of(tasks["items"]) == FROZEN_TASK_INPUT_SHA256
    )
    if not frozen_ok:
        raise Rejection("manifest_constants_mismatch")
    for entry in manifest["graphs"]:
        if entry["kind"] == "probe_bundle":
            bundle_ok = (
                entry["path"] == FROZEN_BUNDLE_PATH
                and entry["sha256"] == FROZEN_BUNDLE_SHA256
                and entry["key"] in FROZEN_BUNDLE_KEYS
                and entry["role"] == "graph_only"
            )
            if not bundle_ok:
                raise Rejection("manifest_constants_mismatch")


def check_provider_pin(manifest: dict[str, Any]) -> None:
    provider = manifest["algorithms"]["leiden"]["provider"]
    versions = list(provider["distributions"].values())
    if provider["kind"] not in ("real", "fixture") or not all(v and v.isascii() for v in versions):
        raise Rejection("provider_pin_invalid")
    marked = [FIXTURE_MARK in v for v in versions]
    if provider["kind"] == "fixture" and not all(marked):
        raise Rejection("provider_pin_invalid")
    if provider["kind"] == "real" and (any(marked) or any("+atlas" in v for v in versions)):
        raise Rejection("provider_pin_invalid")


def load_manifest(path: Path, expected_sha: str) -> tuple[dict[str, Any], str]:
    raw = read_bounded(path, MAX_MANIFEST_BYTES, "manifest_unreadable", "manifest_invalid", "")
    try:
        manifest = load_strict(raw)
    except ValueError as exc:
        raise Rejection("manifest_invalid") from exc
    validate_manifest_shape(manifest)
    if digest_of(manifest) != expected_sha:
        raise Rejection("manifest_digest_mismatch")
    check_manifest_constants(manifest)
    check_provider_pin(manifest)
    return manifest, expected_sha


# ------------------------------------------------------------------ projection


def project(
    nodes: Any,
    edges: Any,
    *,
    max_nodes: int = FROZEN_BOUNDS["max_nodes"],
    max_edges: int = FROZEN_BOUNDS["max_edges"],
) -> tuple[list[list[Any]], list[list[int]]]:
    """atlas-community-projection.v1: undirected, unweighted, self-loops dropped,
    multi-edges collapsed, nodes sorted by (path, line, name), isolated nodes kept."""
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise Rejection("graph_invalid")
    if len(nodes) > max_nodes:
        raise Rejection("graph_too_large")
    seen: set[tuple[Any, ...]] = set()
    for node in nodes:
        ok = isinstance(node, list) and len(node) == 3
        ok = ok and type(node[0]) is str and is_int(node[1]) and type(node[2]) is str
        if not ok or node[1] < 1:
            raise Rejection("graph_invalid")
        if tuple(node) in seen:
            raise Rejection("graph_invalid")
        seen.add(tuple(node))
    n = len(nodes)
    for edge in edges:
        if not (isinstance(edge, list) and len(edge) == 2):
            raise Rejection("graph_invalid")
        if not (is_int(edge[0]) and is_int(edge[1])):
            raise Rejection("graph_invalid")
        if not (0 <= edge[0] < n and 0 <= edge[1] < n):
            raise Rejection("graph_invalid")
    order = sorted(range(n), key=lambda i: tuple(nodes[i]))
    remap = {old: new for new, old in enumerate(order)}
    pairs = {(min(remap[u], remap[v]), max(remap[u], remap[v])) for u, v in edges if u != v}
    if not pairs:
        raise Rejection("graph_invalid")
    if len(pairs) > max_edges:
        raise Rejection("graph_too_large")
    return [list(nodes[i]) for i in order], [list(pair) for pair in sorted(pairs)]


def nodes_digest(nodes: list[list[Any]]) -> str:
    return digest_of(nodes)


def projection_digest(nodes: list[list[Any]], edges: list[list[int]]) -> str:
    return digest_of({"edges": edges, "nodes": nodes})


def degrees(n: int, edges: list[list[int]]) -> list[int]:
    degree = [0] * n
    for u, v in edges:
        degree[u] += 1
        degree[v] += 1
    return degree


# ------------------------------------------------------------------ partitions


def validate_partition(communities: Any, n: int) -> str | None:
    """Return the first failing reason in D5 precedence, or None for a canonical cover."""
    if not isinstance(communities, list):
        return "partition_invalid_type"
    for members in communities:
        if not isinstance(members, list) or not all(is_int(v) for v in members):
            return "partition_invalid_type"
    flat = [v for members in communities for v in members]
    if any(v < 0 or v >= n for v in flat):
        return "partition_node_unknown"
    if len(set(flat)) != len(flat):
        return "partition_node_duplicated"
    if len(flat) != n:
        return "partition_node_omitted"
    if any(not members for members in communities):
        return "partition_noncanonical"
    for members in communities:
        if any(members[i] >= members[i + 1] for i in range(len(members) - 1)):
            return "partition_noncanonical"
    firsts = [members[0] for members in communities]
    if any(firsts[i] >= firsts[i + 1] for i in range(len(firsts) - 1)):
        return "partition_noncanonical"
    return None


def canonicalize(membership: list[int]) -> list[list[int]]:
    """Group a membership list into sorted communities ordered by smallest member."""
    groups: dict[int, list[int]] = {}
    for node, label in enumerate(membership):
        groups.setdefault(label, []).append(node)
    return sorted(groups.values(), key=lambda members: members[0])


def membership_of(communities: list[list[int]], n: int) -> list[int]:
    labels = [0] * n
    for index, members in enumerate(communities):
        for v in members:
            labels[v] = index
    return labels


def rerun_lpa(module: Any, n: int, edges: list[list[int]]) -> list[list[int]]:
    adjacency: dict[int, set[int]] = {i: set() for i in range(n)}
    for u, v in edges:
        adjacency[u].add(v)
        adjacency[v].add(u)
    community_of = module.label_propagation(adjacency, FROZEN_LPA_MAX_ITERATIONS)
    return canonicalize([community_of[i] for i in range(n)])


# --------------------------------------------------------------------- metrics


def pairs_of(count: int) -> int:
    return count * (count - 1) // 2


def quality(n: int, edges: list[list[int]], communities: list[list[int]]) -> dict[str, Any]:
    """Exact quality of one partition of the projected graph (m >= 1)."""
    m = len(edges)
    labels = membership_of(communities, n)
    k = len(communities)
    degree = degrees(n, edges)
    internal = [0] * k
    degree_sum = [0] * k
    for node in range(n):
        degree_sum[labels[node]] += degree[node]
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for u, v in edges:
        if labels[u] == labels[v]:
            internal[labels[u]] += 1
            parent[find(u)] = find(v)
    sum_internal = sum(internal)
    sum_square = sum(d * d for d in degree_sum)
    modularity = Fraction(4 * m * sum_internal - sum_square, 4 * m * m)
    sizes = sorted(len(members) for members in communities)
    disconnected = sum(1 for members in communities if len({find(v) for v in members}) > 1)
    return {
        "modularity": ratio_frac(modularity),
        "coverage": ratio_of(sum_internal, m),
        "community_count": k,
        "singleton_count": sum(1 for s in sizes if s == 1),
        "size_max": sizes[-1],
        "size_median": sizes[(k + 1) // 2 - 1],
        "size_p90": sizes[(9 * k + 9) // 10 - 1],
        "disconnected_communities": disconnected,
        "disconnected_share": ratio_of(disconnected, k),
    }


def ari(first: list[list[int]], second: list[list[int]], n: int) -> Fraction:
    """Exact adjusted Rand index by pair counting."""
    if first == second:
        return Fraction(1)
    left = membership_of(first, n)
    right = membership_of(second, n)
    cells = Counter(zip(left, right, strict=True))
    index = sum(pairs_of(c) for c in cells.values())
    sum_a = sum(pairs_of(len(members)) for members in first)
    sum_b = sum(pairs_of(len(members)) for members in second)
    total = pairs_of(n)
    expected = Fraction(sum_a * sum_b, total)
    maximum = Fraction(sum_a + sum_b, 2)
    if maximum == expected:
        raise AssertionError("distinct partitions with a degenerate adjusted Rand index")
    return (index - expected) / (maximum - expected)


def stability(lpa: list[list[int]], leiden: list[list[list[int]]], n: int) -> dict[str, Any]:
    """Seed stability of the Leiden partitions (in seed order) and agreement with LPA."""
    pairwise: list[Fraction] = []
    identical = 0
    for a in range(len(leiden)):
        for b in range(a + 1, len(leiden)):
            pairwise.append(ari(leiden[a], leiden[b], n))
            identical += 1 if leiden[a] == leiden[b] else 0
    distinct = {tuple(tuple(members) for members in part) for part in leiden}
    return {
        "pairwise_ari": [ratio_frac(v) for v in pairwise],
        "min_pairwise_ari": ratio_frac(min(pairwise)),
        "mean_pairwise_ari": ratio_frac(sum(pairwise, Fraction(0)) / len(pairwise)),
        "identical_pairs": identical,
        "identical_pair_share": ratio_of(identical, len(pairwise)),
        "distinct_leiden_partitions": len(distinct),
        "lpa_vs_leiden_ari": [ratio_frac(ari(lpa, part, n)) for part in leiden],
        "lpa": "deterministic_no_seed",
    }


# ------------------------------------------------------------------ usefulness


def line_offsets(source: bytes) -> list[int]:
    offsets = [0]
    for index, byte in enumerate(source):
        if byte == 10:
            offsets.append(index + 1)
    return offsets


def task_evidence(
    nodes: list[list[Any]],
    items: list[dict[str, Any]],
    useful: dict[str, Any],
) -> list[dict[str, Any]]:
    """Seeds and gold per task over the projected task graph (D8)."""
    sources = {path: text.encode("utf-8") for path, text in useful["source_files"].items()}
    offsets = {path: line_offsets(raw) for path, raw in sources.items()}
    spans_by_task = {task["id"]: task["useful_spans"] for task in useful["tasks"]}
    rows: list[dict[str, Any]] = []
    for item in items:
        spans = spans_by_task[item["id"]]
        gold = []
        for index, (path, line, _name) in enumerate(nodes):
            start = offsets[path][line - 1]
            for span in spans:
                same = span["path"] == path and span["source_sha256"] == sha256_hex(sources[path])
                if same and span["start_byte"] <= start < span["end_byte"]:
                    gold.append(index)
                    break
        seeds = [i for i, node in enumerate(nodes) if node[2] in item["terms"]]
        tier = "exact_name"
        if not seeds:
            needle = item["text"].encode("utf-8")
            seeds = [i for i, node in enumerate(nodes) if needle in sources[node[0]]]
            tier = "lexical_file" if seeds else "none"
        rows.append({"task_id": item["id"], "seed_tier": tier, "seeds": seeds, "gold": gold})
    return rows


def expansion_order(seeds: list[int], communities: list[list[int]]) -> list[int]:
    """Seeds first, then each seed's community members ascending, unseen ones only."""
    owner = {v: index for index, members in enumerate(communities) for v in members}
    order = list(seeds)
    seen = set(seeds)
    for seed in seeds:
        for member in communities[owner[seed]]:
            if member not in seen:
                seen.add(member)
                order.append(member)
    return order


def usefulness(
    manifest_sha: str,
    graph_id: str,
    evidence: list[dict[str, Any]],
    runs: list[tuple[str, list[list[int]]]],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    totals: list[dict[str, Any]] = []
    for run_id, communities in runs:
        orders = {row["task_id"]: expansion_order(row["seeds"], communities) for row in evidence}
        for budget in FROZEN_BUDGETS:
            hits = selected_total = gold_total = 0
            for row in evidence:
                selected = orders[row["task_id"]][:budget]
                found = len(set(selected) & set(row["gold"]))
                rows.append(
                    {
                        "manifest_sha256": manifest_sha,
                        "run_id": run_id,
                        "budget": budget,
                        "task_id": row["task_id"],
                        "selected": selected,
                        "hits": found,
                        "selected_count": len(selected),
                        "gold_count": len(row["gold"]),
                    }
                )
                hits += found
                selected_total += len(selected)
                gold_total += len(row["gold"])
            totals.append(
                {
                    "manifest_sha256": manifest_sha,
                    "run_id": run_id,
                    "budget": budget,
                    "hits": hits,
                    "selected_count": selected_total,
                    "gold_count": gold_total,
                    "recall": ratio_of(hits, gold_total),
                    "precision": ratio_of(hits, selected_total),
                }
            )
    return {
        "proxy": USEFULNESS_PROXY,
        "manifest_sha256": manifest_sha,
        "graph_id": graph_id,
        "task_input_sha256": FROZEN_TASK_INPUT_SHA256,
        "useful_spans_sha256": FROZEN_USEFUL_SPANS_SHA256,
        "budgets": list(FROZEN_BUDGETS),
        "tasks": evidence,
        "rows": rows,
        "totals": totals,
    }


# -------------------------------------------------------------------- decision

Score = tuple[Fraction | None, Fraction | None]


def at_least(value: Fraction | None, base: Fraction | None) -> bool:
    if value is None or base is None:
        return value is None and base is None
    return value >= base


def decide(
    provider_available: bool,
    gold_total: int,
    seeded_tasks: int,
    leiden_differs: list[bool],
    lpa_scores: dict[int, Score],
    leiden_scores: dict[int, dict[int, Score]],
    mean_ari: dict[str, Fraction],
    lpa_q: dict[str, Fraction],
    leiden_q: dict[str, list[Fraction]],
) -> dict[str, Any]:
    """atlas-community-decision.v1 over plain values and the FROZEN thresholds only."""
    if not provider_available:
        conditions: dict[str, Any] = {
            "provider_available": False,
            "task_evidence_present": None,
            "task_graph_partitions_differ": None,
            "usefulness_no_worse_all_budgets": None,
            "recall_gain_budgets": None,
            "stability_floor_met": None,
            "modularity_not_below_lpa": None,
        }
        return {
            "outcome": "skipped_missing_provider",
            "reasons": ["provider_missing"],
            "conditions": conditions,
        }
    evidence = gold_total > 0 and seeded_tasks > 0
    differ = any(leiden_differs)
    no_worse = bool(leiden_scores) and all(
        at_least(scores[budget][0], lpa_scores[budget][0])
        and at_least(scores[budget][1], lpa_scores[budget][1])
        for scores in leiden_scores.values()
        for budget in lpa_scores
    )
    gains = 0
    for budget, (base_recall, _base_precision) in lpa_scores.items():
        recalls = [scores[budget][0] for scores in leiden_scores.values()]
        if (
            base_recall is not None
            and recalls
            and all(r is not None and r > base_recall for r in recalls)
        ):
            gains += 1
    stable = bool(mean_ari) and all(v >= FROZEN_STABILITY_FLOOR for v in mean_ari.values())
    modular = bool(lpa_q) and all(
        min(leiden_q[graph]) >= base + FROZEN_MODULARITY_MARGIN for graph, base in lpa_q.items()
    )
    conditions = {
        "provider_available": True,
        "task_evidence_present": evidence,
        "task_graph_partitions_differ": differ,
        "usefulness_no_worse_all_budgets": no_worse,
        "recall_gain_budgets": gains,
        "stability_floor_met": stable,
        "modularity_not_below_lpa": modular,
    }
    if not evidence:
        return {
            "outcome": "inconclusive",
            "reasons": ["no_task_evidence"],
            "conditions": conditions,
        }
    if not differ:
        return {
            "outcome": "inconclusive",
            "reasons": ["task_graph_partitions_identical"],
            "conditions": conditions,
        }
    reasons = []
    if not no_worse:
        reasons.append("usefulness_regression")
    if gains < FROZEN_MIN_RECALL_GAIN_BUDGETS:
        reasons.append("no_usefulness_gain")
    if not stable:
        reasons.append("stability_below_floor")
    if not modular:
        reasons.append("modularity_below_lpa")
    outcome = "retain_lpa" if reasons else "leiden_candidate_for_review"
    return {"outcome": outcome, "reasons": reasons, "conditions": conditions}


# ----------------------------------------------------------------------- inputs


def input_path(repo_root: Path, relative: str) -> Path:
    return repo_root / Path(relative)


def load_graph_input(
    entry: dict[str, Any], repo_root: Path, bounds: dict[str, int]
) -> dict[str, Any]:
    path = input_path(repo_root, entry["path"])
    limit = bounds["max_input_bytes"]
    detail = entry["id"]
    if entry["kind"] == "probe_bundle":
        compressed, plain = read_gzip_bounded(
            path, limit, "input_unreadable", "input_too_large", detail
        )
        raw = compressed
    else:
        raw = read_bounded(path, limit, "input_unreadable", "input_too_large", detail)
        plain = raw
    if sha256_hex(raw) != entry["sha256"]:
        raise Rejection("input_hash_mismatch", detail)
    try:
        data = load_strict(plain, allow_floats=True)
    except ValueError as exc:
        raise Rejection("graph_invalid", detail) from exc
    if entry["kind"] == "probe_bundle":
        data = data.get(entry["key"]) if isinstance(data, dict) else None
        if not isinstance(data, dict) or "nodes" not in data or "edges" not in data:
            raise Rejection("graph_invalid", detail)
    else:
        shaped = isinstance(data, dict) and data.get("format") == GRAPH_FORMAT
        if not shaped or data.get("graph_id") != entry["id"]:
            raise Rejection("graph_invalid", detail)
        if "nodes" not in data or "edges" not in data:
            raise Rejection("graph_invalid", detail)
    try:
        nodes, edges = project(
            data["nodes"],
            data["edges"],
            max_nodes=bounds["max_nodes"],
            max_edges=bounds["max_edges"],
        )
    except Rejection as exc:
        raise Rejection(exc.reason, detail) from exc
    degree = degrees(len(nodes), edges)
    return {
        "id": entry["id"],
        "role": entry["role"],
        "kind": entry["kind"],
        "path": entry["path"],
        "sha256": entry["sha256"],
        "nodes": nodes,
        "edges": edges,
        "n": len(nodes),
        "m": len(edges),
        "isolated_nodes": sum(1 for d in degree if d == 0),
        "nodes_sha256": nodes_digest(nodes),
        "projection_sha256": projection_digest(nodes, edges),
    }


def load_task_inputs(
    manifest: dict[str, Any], repo_root: Path, bounds: dict[str, int]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The S18 useful-spans file and the S23 manifest, anchored to their frozen digests."""
    tasks = manifest["tasks"]
    limit = bounds["max_input_bytes"]
    raw23 = read_bounded(
        input_path(repo_root, tasks["s23_manifest_path"]),
        limit,
        "input_unreadable",
        "input_too_large",
        "s23_manifest",
    )
    try:
        s23 = load_strict(raw23, allow_floats=True)
    except ValueError as exc:
        raise Rejection("task_anchor_mismatch", "s23_manifest") from exc
    anchored = isinstance(s23, dict) and isinstance(s23.get("tasks"), list)
    if not anchored or digest_of(s23["tasks"]) != tasks["task_input_sha256"]:
        raise Rejection("task_anchor_mismatch", "s23_manifest")
    if s23.get("task_input_sha256") != tasks["task_input_sha256"] or s23["tasks"] != tasks["items"]:
        raise Rejection("task_anchor_mismatch", "s23_manifest")
    spans_raw = read_bounded(
        input_path(repo_root, tasks["useful_spans"]["path"]),
        limit,
        "input_unreadable",
        "input_too_large",
        "useful_spans",
    )
    if sha256_hex(spans_raw) != tasks["useful_spans"]["sha256"]:
        raise Rejection("input_hash_mismatch", "useful_spans")
    try:
        useful = load_strict(spans_raw, allow_floats=True)
    except ValueError as exc:
        raise Rejection("task_anchor_mismatch", "useful_spans") from exc
    files_ok = isinstance(useful, dict) and isinstance(useful.get("source_files"), dict)
    files_ok = files_ok and isinstance(useful.get("source_hashes"), dict)
    files_ok = files_ok and isinstance(useful.get("tasks"), list)
    if not files_ok:
        raise Rejection("task_anchor_mismatch", "useful_spans")
    for path, text in useful["source_files"].items():
        if not isinstance(text, str) or useful["source_hashes"].get(path) != sha256_hex(
            text.encode("utf-8")
        ):
            raise Rejection("task_anchor_mismatch", "useful_spans")
    by_id = {t.get("id"): t for t in useful["tasks"] if isinstance(t, dict)}
    for item in tasks["items"]:
        task = by_id.get(item["id"])
        if task is None or task.get("text") != item["text"] or "useful_spans" not in task:
            raise Rejection("task_anchor_mismatch", item["id"])
    records = [
        {
            "id": "useful_spans",
            "kind": "useful_spans",
            "path": tasks["useful_spans"]["path"],
            "sha256": sha256_hex(spans_raw),
        },
        {
            "id": "s23_manifest",
            "kind": "s23_manifest",
            "path": tasks["s23_manifest_path"],
            "sha256": sha256_hex(raw23),
        },
    ]
    return useful, records


def check_task_anchors(graph: dict[str, Any], useful: dict[str, Any]) -> None:
    sources = {path: text.encode("utf-8") for path, text in useful["source_files"].items()}
    counts = {path: len(line_offsets(raw)) for path, raw in sources.items()}
    for path, line, _name in graph["nodes"]:
        if path not in counts or line > counts[path]:
            raise Rejection("task_anchor_mismatch", graph["id"])
    for task in useful["tasks"]:
        for span in task["useful_spans"]:
            if span["path"] not in sources or span["source_sha256"] != sha256_hex(
                sources[span["path"]]
            ):
                raise Rejection("task_anchor_mismatch", task["id"])


def load_lpa_module(
    manifest: dict[str, Any], repo_root: Path, bounds: dict[str, int]
) -> tuple[Any, dict[str, Any]]:
    lpa = manifest["algorithms"]["lpa"]
    path = input_path(repo_root, lpa["module_path"])
    raw = read_bounded(
        path, bounds["max_input_bytes"], "input_unreadable", "input_too_large", "lpa_module"
    )
    if sha256_hex(raw) != lpa["module_sha256"]:
        raise Rejection("input_hash_mismatch", "lpa_module")
    try:
        module = load_module(path, "atlas_s36_label_propagation")
    except Exception as exc:
        raise Rejection("input_unreadable", "lpa_module") from exc
    record = {
        "id": "lpa_module",
        "kind": "lpa_module",
        "path": lpa["module_path"],
        "sha256": lpa["module_sha256"],
    }
    return module, record


def load_inputs(
    manifest: dict[str, Any], repo_root: Path, bounds: dict[str, int] | None = None
) -> dict[str, Any]:
    """Read, hash and project every frozen input. Bounds default to the frozen ones."""
    limits = dict(FROZEN_BOUNDS if bounds is None else bounds)
    graphs = [load_graph_input(entry, repo_root, limits) for entry in manifest["graphs"]]
    useful, task_records = load_task_inputs(manifest, repo_root, limits)
    task_graph = next(g for g in graphs if g["role"] == "task_graph")
    check_task_anchors(task_graph, useful)
    module, lpa_record = load_lpa_module(manifest, repo_root, limits)
    records = [
        {"id": g["id"], "kind": g["kind"], "path": g["path"], "sha256": g["sha256"]} for g in graphs
    ]
    records.extend([*task_records, lpa_record])
    return {
        "graphs": graphs,
        "task_graph": task_graph,
        "useful": useful,
        "evidence": task_evidence(task_graph["nodes"], manifest["tasks"]["items"], useful),
        "lpa": module,
        "records": sorted(records, key=lambda r: r["id"]),
    }


# --------------------------------------------------------------------- results


def run_ids(available: bool) -> list[str]:
    return ["lpa", *([f"leiden-seed-{s}" for s in FROZEN_SEEDS] if available else [])]


def graph_rows(
    manifest_sha: str, graphs: list[dict[str, Any]], docs: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Per-graph rows with quality and stability, recomputed from retained partitions."""
    rows = []
    for graph, doc in zip(graphs, docs, strict=True):
        runs = []
        for run in doc["runs"]:
            runs.append(
                {
                    "run_id": run["run_id"],
                    "manifest_sha256": manifest_sha,
                    "algorithm": run["algorithm"],
                    "seed": run["seed"],
                    "input_projection_sha256": run["input_projection_sha256"],
                    "quality": quality(graph["n"], graph["edges"], run["communities"]),
                }
            )
        leiden = [r["communities"] for r in doc["runs"] if r["algorithm"] == "leiden"]
        lpa = doc["runs"][0]["communities"]
        rows.append(
            {
                "graph_id": graph["id"],
                "role": graph["role"],
                "manifest_sha256": manifest_sha,
                "n": graph["n"],
                "m": graph["m"],
                "isolated_nodes": graph["isolated_nodes"],
                "nodes_sha256": graph["nodes_sha256"],
                "projection_sha256": graph["projection_sha256"],
                "runs": runs,
                "stability": stability(lpa, leiden, graph["n"]) if leiden else None,
            }
        )
    return rows


def decision_of(
    manifest_sha: str,
    available: bool,
    rows: list[dict[str, Any]],
    use: dict[str, Any],
    task_graph_id: str,
    docs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Run decide() over the primitives extracted from recomputed rows."""
    lpa_scores: dict[int, Score] = {}
    leiden_scores: dict[int, dict[int, Score]] = {}
    for total in use["totals"]:
        score = (frac_of(total["recall"]), frac_of(total["precision"]))
        if total["run_id"] == "lpa":
            lpa_scores[total["budget"]] = score
        else:
            seed = int(total["run_id"].rsplit("-", 1)[1])
            leiden_scores.setdefault(seed, {})[total["budget"]] = score
    mean_ari: dict[str, Fraction] = {}
    lpa_q: dict[str, Fraction] = {}
    leiden_q: dict[str, list[Fraction]] = {}
    for row in rows:
        lpa_q[row["graph_id"]] = frac_of(row["runs"][0]["quality"]["modularity"]) or Fraction(0)
        leiden_q[row["graph_id"]] = [
            frac_of(r["quality"]["modularity"]) or Fraction(0) for r in row["runs"][1:]
        ]
        if row["stability"] is not None:
            mean_ari[row["graph_id"]] = frac_of(row["stability"]["mean_pairwise_ari"]) or Fraction(
                0
            )
    task_doc = next(d for d in docs if d["graph_id"] == task_graph_id)
    lpa_part = task_doc["runs"][0]["communities"]
    differs = [r["communities"] != lpa_part for r in task_doc["runs"][1:]]
    gold_total = sum(len(t["gold"]) for t in use["tasks"])
    seeded = sum(1 for t in use["tasks"] if t["seeds"])
    outcome = decide(
        available, gold_total, seeded, differs, lpa_scores, leiden_scores, mean_ari, lpa_q, leiden_q
    )
    return {
        "manifest_sha256": manifest_sha,
        "rule": DECISION_RULE,
        "outcome": outcome["outcome"],
        "reasons": outcome["reasons"],
        "conditions": outcome["conditions"],
    }


def provider_record(available: bool, kind: str | None, versions: Any, missing: list[str]) -> Any:
    return {
        "available": available,
        "kind": kind,
        "missing": missing,
        "versions": versions,
    }


def build_result(
    manifest: dict[str, Any],
    manifest_sha: str,
    inputs: dict[str, Any],
    docs: list[dict[str, Any]],
    partitions_sha: str,
    provider: dict[str, Any],
) -> dict[str, Any]:
    """The canonical result (without canonical_sha256) from retained partitions."""
    available = provider["available"]
    rows = graph_rows(manifest_sha, inputs["graphs"], docs)
    task_graph = inputs["task_graph"]
    task_doc = next(d for d in docs if d["graph_id"] == task_graph["id"])
    use = usefulness(
        manifest_sha,
        task_graph["id"],
        inputs["evidence"],
        [(run["run_id"], run["communities"]) for run in task_doc["runs"]],
    )
    return {
        "format": RESULT_FORMAT,
        "manifest_sha256": manifest_sha,
        "experiment_id": manifest["experiment_id"],
        "status": "complete" if available else "skipped_missing_provider",
        "provider": provider,
        "inputs": inputs["records"],
        "partitions_sha256": partitions_sha,
        "graphs": rows,
        "usefulness": use,
        "decision": decision_of(manifest_sha, available, rows, use, task_graph["id"], docs),
        "limitations": list(LIMITATIONS),
    }


def seal(result: dict[str, Any]) -> dict[str, Any]:
    body = {k: v for k, v in result.items() if k != "canonical_sha256"}
    return {**body, "canonical_sha256": digest_of(body)}


def build_partition_doc(
    manifest_sha: str,
    inputs: dict[str, Any],
    runs_by_graph: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    graphs = []
    for graph in inputs["graphs"]:
        graphs.append(
            {
                "graph_id": graph["id"],
                "n": graph["n"],
                "nodes_sha256": graph["nodes_sha256"],
                "projection_sha256": graph["projection_sha256"],
                "runs": [
                    {**run, "manifest_sha256": manifest_sha} for run in runs_by_graph[graph["id"]]
                ],
            }
        )
    return {"format": PARTITIONS_FORMAT, "manifest_sha256": manifest_sha, "graphs": graphs}


# ---------------------------------------------------------------------- verify

RESULT_SPEC: dict[str, Any] = {
    "format": str,
    "manifest_sha256": str,
    "experiment_id": str,
    "status": str,
    "provider": {
        "available": bool,
        "kind": ("null", str),
        "missing": [str],
        "versions": ("null", {"igraph": str, "leidenalg": str}),
    },
    "inputs": [{"id": str, "kind": str, "path": str, "sha256": str}],
    "partitions_sha256": str,
    "graphs": ["any"],
    "usefulness": "any",
    "decision": "any",
    "limitations": [str],
    "canonical_sha256": str,
}
RUN_SPEC: dict[str, Any] = {
    "run_id": str,
    "manifest_sha256": str,
    "algorithm": str,
    "seed": ("null", int),
    "provider_kind": str,
    "input_projection_sha256": str,
    "communities": "any",
}
DOC_GRAPH_SPEC: dict[str, Any] = {
    "graph_id": str,
    "n": int,
    "nodes_sha256": str,
    "projection_sha256": str,
    "runs": [RUN_SPEC],
}
PARTITIONS_SPEC: dict[str, Any] = {
    "format": str,
    "manifest_sha256": str,
    "graphs": [DOC_GRAPH_SPEC],
}
TELEMETRY_SPEC: dict[str, Any] = {
    "format": str,
    "canonical": bool,
    "manifest_sha256": str,
    "result_canonical_sha256": str,
    "platform": {
        "system": str,
        "machine": str,
        "python_version": str,
        "python_implementation": str,
    },
    "runs": [
        {
            "graph_id": str,
            "run_id": str,
            "elapsed_ns": int,
            "peak_rss_bytes_before_call": int,
            "peak_rss_bytes_after_call": int,
            "rss_source": str,
        }
    ],
}
RSS_SOURCES = ("ru_maxrss_bytes", "ru_maxrss_kib", "unavailable")


def read_artifact(path: Path, limit: int, name: str) -> bytes:
    return read_bounded(path, limit, "artifact_unreadable", "artifact_too_large", name)


def load_artifact(raw: bytes, name: str) -> Any:
    try:
        return load_strict(raw)
    except ValueError as exc:
        raise Rejection("artifact_invalid_json", name) from exc


def verify_result_document(
    raw: bytes, manifest_sha: str, manifest: dict[str, Any]
) -> dict[str, Any]:
    result = load_artifact(raw, "result")
    if not check_shape(result, RESULT_SPEC) or result["format"] != RESULT_FORMAT:
        raise Rejection("artifact_schema_invalid", "result")
    try:
        body = {k: v for k, v in result.items() if k != "canonical_sha256"}
        sound = digest_of(body) == result["canonical_sha256"] and canonical(result) == raw
    except ValueError:
        sound = False
    if not sound:
        raise Rejection("canonical_digest_mismatch")
    if result["status"] not in STATUSES or result["limitations"] != list(LIMITATIONS):
        raise Rejection("artifact_schema_invalid", "result")
    if result["manifest_sha256"] != manifest_sha:
        raise Rejection("manifest_reference_mismatch", "result")
    if result["experiment_id"] != manifest["experiment_id"]:
        raise Rejection("manifest_reference_mismatch", "result")
    for graph in result["graphs"]:
        if not isinstance(graph, dict) or graph.get("manifest_sha256") != manifest_sha:
            raise Rejection("manifest_reference_mismatch", "result")
        for run in graph.get("runs", []):
            if not isinstance(run, dict) or run.get("manifest_sha256") != manifest_sha:
                raise Rejection("manifest_reference_mismatch", "result")
    use = result["usefulness"]
    decision = result["decision"]
    if not isinstance(use, dict) or not isinstance(decision, dict):
        raise Rejection("artifact_schema_invalid", "result")
    stamped = [decision, use]
    for key in ("rows", "totals"):
        if not isinstance(use.get(key), list):
            raise Rejection("artifact_schema_invalid", "result")
        stamped.extend(use[key])
    if any(not isinstance(r, dict) or r.get("manifest_sha256") != manifest_sha for r in stamped):
        raise Rejection("manifest_reference_mismatch", "result")
    return result


def verify_partitions(
    path: Path,
    limit: int,
    result: dict[str, Any],
    manifest_sha: str,
    inputs: dict[str, Any],
    available: bool,
) -> dict[str, Any]:
    _compressed, plain = read_gzip_bounded(
        path, limit, "artifact_unreadable", "artifact_too_large", "partitions"
    )
    if sha256_hex(plain) != result["partitions_sha256"]:
        raise Rejection("partitions_digest_mismatch")
    doc = load_artifact(plain, "partitions")
    if not check_shape(doc, PARTITIONS_SPEC) or doc["format"] != PARTITIONS_FORMAT:
        raise Rejection("artifact_schema_invalid", "partitions")
    if doc["manifest_sha256"] != manifest_sha:
        raise Rejection("manifest_reference_mismatch", "partitions")
    graphs = inputs["graphs"]
    if [g["graph_id"] for g in doc["graphs"]] != [g["id"] for g in graphs]:
        raise Rejection("run_set_mismatch", "graphs")
    wanted = run_ids(available)
    for graph, entry in zip(graphs, doc["graphs"], strict=True):
        if entry["nodes_sha256"] != graph["nodes_sha256"]:
            raise Rejection("partition_node_table_mismatch", graph["id"])
        if entry["n"] != graph["n"] or entry["projection_sha256"] != graph["projection_sha256"]:
            raise Rejection("projection_mismatch", graph["id"])
        if [r["run_id"] for r in entry["runs"]] != wanted:
            raise Rejection("run_set_mismatch", graph["id"])
        for run in entry["runs"]:
            expected_seed = None if run["run_id"] == "lpa" else int(run["run_id"].rsplit("-", 1)[1])
            expected_algorithm = "lpa" if expected_seed is None else "leiden"
            if run["seed"] != expected_seed or run["algorithm"] != expected_algorithm:
                raise Rejection("run_set_mismatch", run["run_id"])
            if run["manifest_sha256"] != manifest_sha:
                raise Rejection("manifest_reference_mismatch", run["run_id"])
            if run["input_projection_sha256"] != graph["projection_sha256"]:
                raise Rejection("projection_mismatch", run["run_id"])
            reason = validate_partition(run["communities"], graph["n"])
            if reason is not None:
                raise Rejection(reason, run["run_id"])
    return doc


def verify_skipped_state(
    result: dict[str, Any], manifest: dict[str, Any], doc: dict[str, Any]
) -> None:
    provider = result["provider"]
    available = provider["available"]
    leiden_runs = [r for g in doc["graphs"] for r in g["runs"] if r["algorithm"] == "leiden"]
    consistent = (result["status"] == "complete") == available
    consistent = consistent and (result["decision"]["outcome"] == "skipped_missing_provider") == (
        not available
    )
    consistent = consistent and bool(leiden_runs) == available
    consistent = consistent and (available == (result["graphs"][0]["stability"] is not None))
    if not consistent:
        raise Rejection("skipped_state_inconsistent")
    pin = manifest["algorithms"]["leiden"]["provider"]
    if available:
        good = provider["kind"] == pin["kind"] and provider["versions"] == pin["distributions"]
        good = good and provider["missing"] == []
        kinds = {r["provider_kind"] for r in leiden_runs}
        good = good and kinds == {pin["kind"]}
    else:
        missing = provider["missing"]
        good = provider["kind"] is None and provider["versions"] is None
        good = good and bool(missing) and missing == sorted(set(missing))
        good = good and set(missing) <= {"igraph", "leidenalg"}
    if not good:
        raise Rejection("provider_record_mismatch")


def verify_telemetry(
    path: Path, limit: int, result: dict[str, Any], manifest_sha: str, doc: dict[str, Any]
) -> None:
    raw = read_artifact(path, limit, "telemetry")
    try:
        tel = load_strict(raw)
    except ValueError as exc:
        raise Rejection("telemetry_invalid") from exc
    if not check_shape(tel, TELEMETRY_SPEC) or tel["format"] != TELEMETRY_FORMAT:
        raise Rejection("telemetry_invalid")
    if tel["canonical"] is not False or tel["manifest_sha256"] != manifest_sha:
        raise Rejection("telemetry_invalid")
    if tel["result_canonical_sha256"] != result["canonical_sha256"]:
        raise Rejection("telemetry_invalid")
    expected = [(g["graph_id"], r["run_id"]) for g in doc["graphs"] for r in g["runs"]]
    got = [(r["graph_id"], r["run_id"]) for r in tel["runs"]]
    if got != expected:
        raise Rejection("telemetry_invalid")
    for run in tel["runs"]:
        counters = (
            run["elapsed_ns"],
            run["peak_rss_bytes_before_call"],
            run["peak_rss_bytes_after_call"],
        )
        if any(v < 0 for v in counters) or run["rss_source"] not in RSS_SOURCES:
            raise Rejection("telemetry_invalid")


def verify(
    results_dir: Path,
    manifest_path: Path,
    expected_sha: str,
    repo_root: Path,
    *,
    max_artifact_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
    bounds: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Verify one results directory; return the pass summary or raise Rejection.

    `bounds` lets internal callers lower the input bounds; the CLI never exposes it."""
    manifest, manifest_sha = load_manifest(manifest_path, expected_sha)
    inputs = load_inputs(manifest, repo_root, bounds)
    checks = ["manifest", "inputs"]
    raw = read_artifact(results_dir / "result.json", max_artifact_bytes, "result")
    result = verify_result_document(raw, manifest_sha, manifest)
    if result["inputs"] != inputs["records"]:
        raise Rejection("input_hash_mismatch", "inputs")
    checks.append("result")
    available = result["provider"]["available"]
    doc = verify_partitions(
        results_dir / "partitions.json.gz",
        max_artifact_bytes,
        result,
        manifest_sha,
        inputs,
        available,
    )
    checks.append("partitions")
    for graph, entry in zip(inputs["graphs"], doc["graphs"], strict=True):
        if rerun_lpa(inputs["lpa"], graph["n"], graph["edges"]) != entry["runs"][0]["communities"]:
            raise Rejection("lpa_rerun_mismatch", graph["id"])
    checks.append("lpa_rerun")
    rows = graph_rows(manifest_sha, inputs["graphs"], doc["graphs"])
    if result["graphs"] != rows:
        raise Rejection("metric_mismatch")
    checks.extend(["quality", "stability"])
    task_doc = next(d for d in doc["graphs"] if d["graph_id"] == inputs["task_graph"]["id"])
    use = usefulness(
        manifest_sha,
        inputs["task_graph"]["id"],
        inputs["evidence"],
        [(r["run_id"], r["communities"]) for r in task_doc["runs"]],
    )
    if result["usefulness"] != use:
        raise Rejection("usefulness_mismatch")
    checks.append("usefulness")
    decision = decision_of(
        manifest_sha, available, rows, use, inputs["task_graph"]["id"], doc["graphs"]
    )
    if result["decision"] != decision:
        raise Rejection("decision_mismatch")
    checks.append("decision")
    verify_skipped_state(result, manifest, doc)
    checks.append("skipped_state")
    verify_telemetry(results_dir / "telemetry.json", max_artifact_bytes, result, manifest_sha, doc)
    checks.append("telemetry")
    return {
        "format": VERIFICATION_FORMAT,
        "status": "pass",
        "manifest_sha256": manifest_sha,
        "production_manifest": manifest_sha == PRODUCTION_MANIFEST_SHA256,
        "outcome": result["decision"]["outcome"],
        "canonical_sha256": result["canonical_sha256"],
        "partitions_sha256": result["partitions_sha256"],
        "checks": checks,
    }


# ------------------------------------------------------------------------- CLI


class NamedParser(argparse.ArgumentParser):
    """Usage failures print `Usage error: <name>` and exit 2, never argparse prose."""

    names: tuple[str, ...] = ("arguments",)

    def error(self, message: str) -> NoReturn:
        name = "arguments"
        if message.startswith("argument "):
            name = message[len("argument ") :].split(":")[0].strip().lstrip("-").replace("-", "_")
        elif "required" in message:
            name = message.rsplit(":", 1)[-1].split(",")[0].strip().lstrip("-").replace("-", "_")
        if name not in self.names:
            name = "arguments"
        print(f"Usage error: {name}", file=sys.stderr)
        raise SystemExit(2)


def usage_error(name: str) -> NoReturn:
    print(f"Usage error: {name}", file=sys.stderr)
    raise SystemExit(2)


def build_parser() -> NamedParser:
    parser = NamedParser(prog="verify-community-experiment", allow_abbrev=False)
    parser.names = VERIFIER_USAGE_NAMES
    parser.add_argument("--results")
    parser.add_argument("--manifest")
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--repo-root")
    parser.add_argument("--max-artifact-bytes")
    return parser


def main(argv: list[str] | None = None, *, overrides: dict[str, int] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.results is None:
        usage_error("results")
    repo_root = (
        Path(args.repo_root) if args.repo_root is not None else Path(__file__).resolve().parents[1]
    )
    if not repo_root.is_dir():
        usage_error("repo_root")
    bounds = {**FROZEN_BOUNDS, **(overrides or {})}
    manifest_path = (
        Path(args.manifest) if args.manifest is not None else repo_root / PRODUCTION_MANIFEST_PATH
    )
    sha = PRODUCTION_MANIFEST_SHA256 if args.manifest_sha256 is None else args.manifest_sha256
    if args.manifest_sha256 is not None and not is_sha(sha):
        usage_error("manifest_sha256")
    limit = DEFAULT_MAX_ARTIFACT_BYTES
    if args.max_artifact_bytes is not None:
        text = args.max_artifact_bytes
        if not (text.isascii() and text.isdigit()):
            usage_error("max_artifact_bytes")
        limit = int(text)
        if not MIN_ARTIFACT_BYTES <= limit <= MAX_ARTIFACT_BYTES:
            usage_error("max_artifact_bytes")
    try:
        summary = verify(
            Path(args.results),
            manifest_path,
            sha,
            repo_root,
            max_artifact_bytes=limit,
            bounds=bounds,
        )
    except Rejection as exc:
        print(f"Community verification rejected: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(canonical(summary).decode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
