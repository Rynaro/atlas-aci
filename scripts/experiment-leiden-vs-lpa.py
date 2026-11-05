#!/usr/bin/env python3
"""ACI-S36 runner: measure an optional Leiden provider against the shipped LPA.

The comparison is read-only with respect to the product: it never changes the
production community selection, never edits a dependency file and never
installs anything. The optional provider (`leidenalg` plus `igraph`) is found
at call time; when it is absent the run is recorded as
`skipped_missing_provider` and the shipped LPA rows are still measured.

Every measured run (LPA included) executes in a fresh child process fed the
same canonical projected graph, so peak memory is per run and a provider crash
cannot corrupt the parent. The child reports the digest of the very objects it
handed to the partitioner. The parent canonicalises and validates each result,
assembles the artifacts with the verifier's own functions, self-verifies and
only then publishes the output directory.

Usage:
  python scripts/experiment-leiden-vs-lpa.py run --manifest M --manifest-sha256 HEX \\
      --out DIR [--repo-root R] [--require-provider]

`measure-one` is an internal subcommand used by `run`; it is not an operator
interface.
"""

from __future__ import annotations

import gzip
import importlib
import importlib.metadata
import importlib.util
import os
import platform
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

SCRIPT_DIR = Path(__file__).resolve().parent
OVERRIDE_KEYS = (
    "max_input_bytes",
    "max_nodes",
    "max_edges",
    "run_timeout_seconds",
    "max_output_bytes",
    "make_temp",
    "self_check",
)
MAX_CHILD_OUTPUT_BYTES = 67108864
DIST_NAMES = ("igraph", "leidenalg")


def _load_verifier() -> Any:
    path = SCRIPT_DIR / "verify-community-experiment.py"
    spec = importlib.util.spec_from_file_location("atlas_s36_verifier", path)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load the verifier")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V: Any = _load_verifier()


# --------------------------------------------------------------- provider


def detect_provider(manifest: dict[str, Any], require: bool) -> dict[str, Any]:
    """Find the provider at call time; never install, never trust a stale answer."""
    missing = []
    for name in DIST_NAMES:
        try:
            found = importlib.util.find_spec(name)
        except (ImportError, ValueError):
            found = None
        if found is None:
            missing.append(name)
    if missing:
        if require:
            raise V.Rejection("provider_required_missing")
        return {"available": False, "kind": None, "missing": sorted(missing), "versions": None}
    versions: dict[str, str] = {}
    for name in DIST_NAMES:
        try:
            importlib.import_module(name)
            versions[name] = importlib.metadata.version(name)
        except Exception as exc:
            raise V.Rejection("provider_import_failed") from exc
    pin = manifest["algorithms"]["leiden"]["provider"]["distributions"]
    if versions != pin:
        raise V.Rejection("provider_version_mismatch")
    kind = "fixture" if all(V.FIXTURE_MARK in v for v in versions.values()) else "real"
    return {"available": True, "kind": kind, "missing": [], "versions": versions}


# ----------------------------------------------------------- child process


def _rss_bytes() -> tuple[int, str]:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return peak, "ru_maxrss_bytes"
    if sys.platform.startswith("linux"):
        return peak * 1024, "ru_maxrss_kib"
    return 0, "unavailable"


def measure_one(projection: Path, algorithm: str, seed: int | None, repo_root: Path) -> int:
    """Internal: one partitioner call on the canonical projection file."""
    document = V.load_strict(projection.read_bytes())
    nodes, edges = document["nodes"], document["edges"]
    n = len(nodes)
    versions: dict[str, str] | None = None
    if algorithm == "lpa":
        module = V.load_module(repo_root / V.FROZEN_LPA_MODULE_PATH, "atlas_s36_child_lpa")
        adjacency: dict[int, set[int]] = {i: set() for i in range(n)}
        for u, v in edges:
            adjacency[u].add(v)
            adjacency[v].add(u)
        passed = sorted((u, v) for u in adjacency for v in adjacency[u] if u < v)
        if len(adjacency) != len(nodes):
            raise RuntimeError("adjacency does not cover the node table")
        digest = V.projection_digest(nodes, [list(pair) for pair in passed])
        before, source = _rss_bytes()
        started = time.perf_counter_ns()
        labels = module.label_propagation(adjacency, V.FROZEN_LPA_MAX_ITERATIONS)
        elapsed = time.perf_counter_ns() - started
        after, source = _rss_bytes()
        membership = [labels[i] for i in range(n)]
    else:
        igraph = importlib.import_module("igraph")
        leidenalg = importlib.import_module("leidenalg")
        tuples = [tuple(edge) for edge in edges]
        graph = igraph.Graph(n=n, edges=tuples, directed=False)
        if graph.vcount() != n or graph.ecount() != len(tuples):
            raise RuntimeError("the provider graph differs from the projection")
        digest = V.projection_digest(nodes, [list(pair) for pair in tuples])
        versions = {"igraph": igraph.__version__, "leidenalg": leidenalg.__version__}
        before, source = _rss_bytes()
        started = time.perf_counter_ns()
        found = leidenalg.find_partition(
            graph,
            leidenalg.ModularityVertexPartition,
            n_iterations=V.FROZEN_N_ITERATIONS,
            seed=seed,
        )
        elapsed = time.perf_counter_ns() - started
        after, source = _rss_bytes()
        membership = [int(label) for label in found.membership]
    report = {
        "membership": membership,
        "input_projection_sha256": digest,
        "versions": versions,
        "elapsed_ns": elapsed,
        "rss_before": before,
        "rss_after": after,
        "rss_source": source,
    }
    sys.stdout.write(V.canonical(report).decode("utf-8"))
    return 0


def _interpreter_flags() -> list[str]:
    flags = []
    if sys.flags.isolated:
        flags.append("-I")
    elif sys.flags.ignore_environment:
        flags.append("-E")
    if sys.flags.no_site:
        flags.append("-S")
    return flags


def _measure_run(
    projection: Path,
    run_id: str,
    algorithm: str,
    seed: int | None,
    repo_root: Path,
    n: int,
    versions: dict[str, str] | None,
    *,
    timeout_seconds: int = V.FROZEN_BOUNDS["run_timeout_seconds"],
    max_output_bytes: int = MAX_CHILD_OUTPUT_BYTES,
) -> dict[str, Any]:
    """Run one partitioner in a fresh child and return its validated report."""
    argv = [sys.executable, *_interpreter_flags(), str(Path(__file__).resolve())]
    argv += ["measure-one", "--projection", str(projection), "--algorithm", algorithm]
    argv += ["--seed", "none" if seed is None else str(seed), "--repo-root", str(repo_root)]
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        done = subprocess.run(argv, capture_output=True, timeout=timeout_seconds, env=env)
    except subprocess.TimeoutExpired as exc:
        raise V.Rejection("run_timeout", run_id) from exc
    except OSError as exc:
        raise V.Rejection("measurement_failed", run_id) from exc
    if done.returncode != 0 or len(done.stdout) > max_output_bytes:
        raise V.Rejection("measurement_failed", run_id)
    try:
        report = V.load_strict(done.stdout)
    except ValueError as exc:
        raise V.Rejection("measurement_failed", run_id) from exc
    keys = {"membership", "input_projection_sha256", "versions", "elapsed_ns"}
    keys |= {"rss_before", "rss_after", "rss_source"}
    if not isinstance(report, dict) or set(report) != keys:
        raise V.Rejection("measurement_failed", run_id)
    if algorithm == "leiden" and report["versions"] != versions:
        raise V.Rejection("provider_changed_during_run", run_id)
    membership = report["membership"]
    listed = isinstance(membership, list) and all(type(v) is int for v in membership)
    if not listed or len(membership) != n:
        raise V.Rejection("partition_invalid", run_id)
    communities = V.canonicalize(membership)
    if V.validate_partition(communities, n) is not None:
        raise V.Rejection("partition_invalid", run_id)
    counters = [report["elapsed_ns"], report["rss_before"], report["rss_after"]]
    if any(type(c) is not int or c < 0 for c in counters):
        raise V.Rejection("measurement_failed", run_id)
    if report["rss_source"] not in V.RSS_SOURCES:
        raise V.Rejection("measurement_failed", run_id)
    report["communities"] = communities
    return report


# ------------------------------------------------------------------ pipeline


def _write_flat(directory: Path, files: dict[str, bytes]) -> None:
    for name, data in files.items():
        (directory / name).write_bytes(data)


def _remove_flat(directory: Path) -> None:
    """Remove a directory this process created; it only ever holds plain files."""
    try:
        for entry in directory.iterdir():
            entry.unlink()
        directory.rmdir()
    except OSError:
        pass


def run_experiment(
    manifest_path: Path,
    expected_sha: str,
    out: Path,
    repo_root: Path,
    require_provider: bool,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Measure, assemble, self-verify and publish; return the run summary."""
    knobs = dict(overrides or {})
    bounds = dict(V.FROZEN_BOUNDS)
    for key in ("max_input_bytes", "max_nodes", "max_edges"):
        if key in knobs:
            bounds[key] = knobs[key]
    timeout = knobs.get("run_timeout_seconds", V.FROZEN_BOUNDS["run_timeout_seconds"])
    manifest, manifest_sha = V.load_manifest(manifest_path, expected_sha)
    inputs = V.load_inputs(manifest, repo_root, bounds)
    provider = detect_provider(manifest, require_provider)
    projection_dir = Path(tempfile.mkdtemp(prefix="s36-projection-"))
    staging: Path | None = None
    try:
        make_temp = knobs.get("make_temp", tempfile.mkdtemp)
        try:
            staging = Path(make_temp(dir=str(out.parent), prefix=".s36-run-"))
        except OSError as exc:
            raise V.Rejection("output_write_failed") from exc
        runs_by_graph: dict[str, list[dict[str, Any]]] = {}
        telemetry_runs: list[dict[str, Any]] = []
        for index, graph in enumerate(inputs["graphs"]):
            file = projection_dir / f"graph-{index}.json"
            body = {"edges": graph["edges"], "nodes": graph["nodes"]}
            file.write_bytes(V.canonical(body))
            runs: list[dict[str, Any]] = []
            for run_id in V.run_ids(provider["available"]):
                seed = None if run_id == "lpa" else int(run_id.rsplit("-", 1)[1])
                algorithm = "lpa" if seed is None else "leiden"
                report = _measure_run(
                    file,
                    run_id,
                    algorithm,
                    seed,
                    repo_root,
                    graph["n"],
                    provider["versions"],
                    timeout_seconds=timeout,
                    max_output_bytes=knobs.get("max_output_bytes", MAX_CHILD_OUTPUT_BYTES),
                )
                kind = "atlas_lpa" if seed is None else provider["kind"]
                runs.append(
                    {
                        "run_id": run_id,
                        "algorithm": algorithm,
                        "seed": seed,
                        "provider_kind": kind,
                        "input_projection_sha256": report["input_projection_sha256"],
                        "communities": report["communities"],
                    }
                )
                telemetry_runs.append(
                    {
                        "graph_id": graph["id"],
                        "run_id": run_id,
                        "elapsed_ns": report["elapsed_ns"],
                        "peak_rss_bytes_before_call": report["rss_before"],
                        "peak_rss_bytes_after_call": report["rss_after"],
                        "rss_source": report["rss_source"],
                    }
                )
            runs_by_graph[graph["id"]] = runs
        doc = V.build_partition_doc(manifest_sha, inputs, runs_by_graph)
        plain = V.canonical(doc)
        result = V.seal(
            V.build_result(
                manifest, manifest_sha, inputs, doc["graphs"], V.sha256_hex(plain), provider
            )
        )
        telemetry = {
            "format": V.TELEMETRY_FORMAT,
            "canonical": False,
            "manifest_sha256": manifest_sha,
            "result_canonical_sha256": result["canonical_sha256"],
            "platform": {
                "system": platform.system(),
                "machine": platform.machine(),
                "python_version": platform.python_version(),
                "python_implementation": platform.python_implementation(),
            },
            "runs": telemetry_runs,
        }
        files = {
            "partitions.json.gz": gzip.compress(plain, compresslevel=9, mtime=0),
            "result.json": V.canonical(result),
            "telemetry.json": V.canonical(telemetry),
        }
        try:
            _write_flat(staging, files)
        except OSError as exc:
            raise V.Rejection("output_write_failed") from exc
        check = knobs.get("self_check", V.verify)
        try:
            check(staging, manifest_path, manifest_sha, repo_root)
        except V.Rejection as exc:
            raise V.Rejection("self_check_failed") from exc
        try:
            staging.rename(out)
        except OSError as exc:
            raise V.Rejection("output_write_failed") from exc
        staging = None
        return {
            "format": V.SUMMARY_FORMAT,
            "status": result["status"],
            "outcome": result["decision"]["outcome"],
            "manifest_sha256": manifest_sha,
            "canonical_sha256": result["canonical_sha256"],
            "partitions_sha256": result["partitions_sha256"],
        }
    finally:
        _remove_flat(projection_dir)
        if staging is not None:
            _remove_flat(staging)


# ------------------------------------------------------------------------ CLI


def build_parser() -> Any:
    parser = V.NamedParser(prog="experiment-leiden-vs-lpa", allow_abbrev=False)
    parser.names = V.RUNNER_USAGE_NAMES
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", allow_abbrev=False)
    run.names = V.RUNNER_USAGE_NAMES
    for flag in ("--manifest", "--manifest-sha256", "--out", "--repo-root"):
        run.add_argument(flag)
    run.add_argument("--require-provider", action="store_true")
    child = commands.add_parser("measure-one", allow_abbrev=False)
    child.names = V.RUNNER_USAGE_NAMES
    for flag in ("--projection", "--algorithm", "--seed", "--repo-root"):
        child.add_argument(flag, required=True)
    return parser


def _run_command(args: Any, overrides: dict[str, Any] | None) -> int:
    for name in ("manifest", "manifest_sha256", "out"):
        if getattr(args, name) is None:
            V.usage_error(name)
    if not V.is_sha(args.manifest_sha256):
        V.usage_error("manifest_sha256")
    repo_root = Path(args.repo_root) if args.repo_root is not None else SCRIPT_DIR.parent
    if not repo_root.is_dir():
        V.usage_error("repo_root")
    out = Path(args.out)
    target = out.resolve()
    mcp = (repo_root / "mcp-server").resolve()
    if target == mcp or mcp in target.parents:
        V.usage_error("out_inside_mcp_server")
    if out.exists() or out.is_symlink():
        V.usage_error("out_exists")
    if not out.parent.is_dir():
        V.usage_error("out")
    if overrides and not set(overrides) <= set(OVERRIDE_KEYS):
        raise ValueError("unknown override")
    try:
        summary = run_experiment(
            Path(args.manifest),
            args.manifest_sha256,
            out,
            repo_root,
            args.require_provider,
            overrides,
        )
    except V.Rejection as exc:
        print(f"Community experiment rejected: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(V.canonical(summary).decode("utf-8"))
    return 0


def main(argv: list[str] | None = None, *, overrides: dict[str, Any] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "measure-one":
        if args.algorithm not in ("lpa", "leiden"):
            V.usage_error("arguments")
        seed = None if args.seed == "none" else int(args.seed)
        return measure_one(Path(args.projection), args.algorithm, seed, Path(args.repo_root))
    return _run_command(args, overrides)


if __name__ == "__main__":
    sys.exit(main())
