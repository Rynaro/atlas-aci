"""CLI entry point: `atlas-aci serve | index | export | import | import-scip | ...`."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import click
import structlog

from atlas_aci.codegraph import DEFAULT_LANGS, CodeGraph
from atlas_aci.config import Config

# AC-REL-2's documented bound (README.md "Why read-only" section /
# GITHUB_FILE_HARD_LIMIT_BYTES): GitHub rejects any single committed file
# >= 100 MiB outright (`git push` fails with "this exceeds GitHub's file
# size limit"), and separately warns starting at 50 MiB (still committable,
# but a visible signal something is getting large). Both are GitHub's own
# documented, well-known thresholds, not invented here — the export CLI
# reuses them so a user hits this warning BEFORE `git push` rejects a
# 120 MB blob silently-until-that-moment, exactly the "silent
# incompleteness" this release exists to eliminate. Measured for real on
# the larger pinned reference repo (Spree): 88,742,743 bytes, 84.63% of
# the hard limit — comfortably over the warn threshold, a real data point
# for why this warning exists, not a hypothetical.
_GITHUB_FILE_WARN_BYTES = 50 * 1024 * 1024
_GITHUB_FILE_HARD_LIMIT_BYTES = 100 * 1024 * 1024


def _open_graph(repo: Path, graph_root: Path | None, **options) -> CodeGraph:
    try:
        return CodeGraph(repo=repo, graph_root=graph_root, **options)
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc


@click.group()
@click.option(
    "--log-level", default="info", type=click.Choice(["debug", "info", "warning", "error"])
)
@click.pass_context
def cli(ctx: click.Context, log_level: str) -> None:
    """ATLAS bounded ACI — reference MCP server."""
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(__import__("logging"), log_level.upper())
        ),
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty()),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    )
    ctx.ensure_object(dict)
    ctx.obj["log_level"] = log_level


@cli.command()
@click.option(
    "--graph-root",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="External graph storage root (default: <repo>/.atlas; independent of memex)",
)
@click.option(
    "--repo", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path)
)
@click.option("--memex-root", default=None, type=click.Path(path_type=Path))
@click.option("--max-bytes-per-call", default=8 * 1024, type=int)
@click.option(
    "--before-graph-root",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help=(
        "Graph root of the BEFORE snapshot for `graph_query op=impact` (operator-only; "
        "never a request argument). The before repository defaults to --repo."
    ),
)
@click.option(
    "--before-repo",
    default=None,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Repository of the before snapshot (default: --repo); alone = its default namespace",
)
@click.option("--impact-max-diff-entities", default=None, type=click.IntRange(min=0))
@click.option("--impact-max-diff-occurrences", default=None, type=click.IntRange(min=0))
@click.option("--impact-max-changed-files", default=None, type=click.IntRange(min=0))
def serve(
    repo: Path,
    memex_root: Path | None,
    max_bytes_per_call: int,
    graph_root: Path | None = None,
    before_graph_root: Path | None = None,
    before_repo: Path | None = None,
    impact_max_diff_entities: int | None = None,
    impact_max_diff_occurrences: int | None = None,
    impact_max_changed_files: int | None = None,
) -> None:
    """Start the MCP server over stdio."""
    from atlas_aci.query.impact import validate_configuration
    from atlas_aci.server import run_stdio

    diff_options: dict[str, Any] = {
        name: value
        for name, value in (
            ("impact_max_diff_entities", impact_max_diff_entities),
            ("impact_max_diff_occurrences", impact_max_diff_occurrences),
            ("impact_max_changed_files", impact_max_changed_files),
        )
        if value is not None
    }
    config = Config(
        repo=repo.resolve(),
        memex_root=(memex_root or repo / ".atlas" / "memex").resolve(),
        max_bytes_per_call=max_bytes_per_call,
        graph_root=graph_root,
        before_repo=before_repo,
        before_graph_root=before_graph_root,
        **diff_options,
    )
    try:
        validate_configuration(config)
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    try:
        asyncio.run(run_stdio(config))
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc


@cli.command()
@click.option(
    "--graph-root",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="External graph storage root (default: <repo>/.atlas; independent of memex)",
)
@click.option(
    "--repo", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path)
)
@click.option(
    "--langs",
    default=",".join(DEFAULT_LANGS),
    show_default=True,
    help="Comma-separated language list",
)
@click.option(
    "--since",
    default=None,
    help=(
        "Enable incremental re-index: skip files unchanged since the last pass "
        "(pass any marker, e.g. HEAD)"
    ),
)
def index(repo: Path, langs: str, since: str | None, graph_root: Path | None = None) -> None:
    """Build or update the code-graph index for a repository."""
    log = structlog.get_logger()
    repo = repo.resolve()
    lang_list = [s.strip() for s in langs.split(",") if s.strip()]

    log.info("index_start", repo=str(repo), langs=lang_list, since=since)
    graph = _open_graph(repo, graph_root, langs=lang_list)
    stats = graph.build(since=since)
    log.info("index_done", **stats)


@cli.command()
@click.option(
    "--graph-root",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="External graph storage root (default: <repo>/.atlas; independent of memex)",
)
@click.option(
    "--repo", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path)
)
def status(repo: Path, graph_root: Path | None = None) -> None:
    """Print canonical structural capabilities and last-observed index completeness."""
    graph = _open_graph(repo.resolve(), graph_root, read_only=True)
    try:
        click.echo(json.dumps(graph.status(), sort_keys=True, separators=(",", ":")))
    finally:
        graph.close()


@cli.command()
@click.option(
    "--graph-root",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="External graph storage root (default: <repo>/.atlas; independent of memex)",
)
@click.option(
    "--repo", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path)
)
@click.argument("out_path", type=click.Path(path_type=Path))
def export(repo: Path, out_path: Path, graph_root: Path | None = None) -> None:
    """Export the code-graph index as canonical, deterministic JSONL (A5).

    The output is a portable, committable artifact (D6): `atlas-aci import
    <out_path> --repo <another-checkout>` reproduces a valid current-epoch
    DB without re-parsing a single source file. `OUT_PATH` may point
    anywhere on disk, including outside `--repo` — this is a CLI-only,
    operator-invoked command, never an MCP tool a served agent can call
    (see README.md's "Why read-only" section for the threat-model
    reasoning), so the enforcement layer's in-repo path restriction
    (`assert_path_in_repo`) does not apply here.

    Reads the selected current-epoch graph read-only; only OUT_PATH is written.

    Warns (never fails — the export itself always succeeds; a size
    warning is advisory, not a reason to withhold a graph the user asked
    for) when the output approaches or exceeds GitHub's own committable-file
    thresholds (50 MiB soft warning, 100 MiB hard rejection at `git push`
    time) — see README.md's AC-REL-2 section for what to do beyond that
    bound (compress it out-of-band, or don't commit it and re-index
    instead; D6/AC-A5-1 freeze the canonical JSONL format itself, so
    changing the export's shape is not an option here).
    """
    log = structlog.get_logger()
    repo = repo.resolve()
    out_path = out_path.resolve()

    graph = _open_graph(repo, graph_root, read_only=True)
    if not graph.epoch_ok():
        raise click.ClickException(
            f"no current-epoch index found under {graph.atlas_dir} -- run "
            f"`atlas-aci index --repo {repo}` with the same graph-root selection first."
        )

    log.info("export_start", repo=str(repo), out_path=str(out_path))
    stats = graph.export_jsonl(out_path)
    log.info("export_done", out_path=str(out_path), **stats)

    bytes_written = stats["bytes_written"]
    if bytes_written >= _GITHUB_FILE_HARD_LIMIT_BYTES:
        log.warning(
            "export_exceeds_github_hard_limit",
            bytes_written=bytes_written,
            hard_limit_bytes=_GITHUB_FILE_HARD_LIMIT_BYTES,
            message=(
                f"This export is {bytes_written} bytes -- at or over GitHub's hard "
                f"{_GITHUB_FILE_HARD_LIMIT_BYTES}-byte (100 MiB) per-file limit. "
                "`git push` WILL reject this file outright. See README.md's AC-REL-2 "
                "section: compress it out-of-band, or don't commit it and re-index "
                "instead."
            ),
        )
    elif bytes_written >= _GITHUB_FILE_WARN_BYTES:
        log.warning(
            "export_approaching_github_limit",
            bytes_written=bytes_written,
            warn_threshold_bytes=_GITHUB_FILE_WARN_BYTES,
            hard_limit_bytes=_GITHUB_FILE_HARD_LIMIT_BYTES,
            message=(
                f"This export is {bytes_written} bytes -- over GitHub's "
                f"{_GITHUB_FILE_WARN_BYTES}-byte (50 MiB) soft-warning threshold. "
                "Still committable today, but a repository that grows further could "
                "cross the 100 MiB hard limit with no further warning until `git push` "
                "rejects it. See README.md's AC-REL-2 section."
            ),
        )


@cli.command(name="import")
@click.option(
    "--graph-root",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="External graph storage root (default: <repo>/.atlas; independent of memex)",
)
@click.option(
    "--repo", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path)
)
@click.argument("in_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def import_(repo: Path, in_path: Path, graph_root: Path | None = None) -> None:
    """Import a canonical JSONL export, reproducing a current-epoch DB (A5).

    The cold-start workflow this exists for: commit a JSONL export
    alongside your repo (or fetch one from CI/a shared cache), then run
    `atlas-aci import <in_path> --repo <repo>` on a fresh checkout instead
    of a full `atlas-aci index` — skips re-parsing every source file.

    `import` WRITES to the selected graph directory (default `<repo>/.atlas`,
    index-path only, DIR-2) and is
    NEVER exposed as an MCP tool — see README.md's "Why read-only" section
    for the threat-model reasoning: the agent is the untrusted party,
    `serve` is read-only by construction, and `import` replaces the
    entire index from bytes an agent could otherwise have supplied or
    influenced. This is an operator-invoked, human-in-the-loop command,
    exactly like `atlas-aci index` itself.
    """
    log = structlog.get_logger()
    repo = repo.resolve()
    in_path = in_path.resolve()

    log.info("import_start", repo=str(repo), in_path=str(in_path))
    graph = _open_graph(repo, graph_root)
    try:
        stats = graph.import_jsonl(in_path)
    except ValueError as e:
        raise click.ClickException(str(e)) from e
    log.info("import_done", in_path=str(in_path), **stats)


@cli.command(name="scip-manifest")
@click.option(
    "--repo",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="The producer's working tree: the directory the SCIP indexer ran over",
)
@click.option(
    "--position-encoding",
    required=True,
    type=click.Choice(["utf-8", "utf-16", "utf-32"]),
    help=(
        "How the producer counts Occurrence columns. No default: it must be declared and is "
        "checked against what the producer version is known to emit"
    ),
)
@click.option("--output", required=True, type=click.Path(dir_okay=False, path_type=Path))
@click.argument(
    "index_path", metavar="INDEX", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
def scip_manifest(repo: Path, position_encoding: str, output: Path, index_path: Path) -> None:
    """Write the producer-time `atlas-scip-manifest.v1` sidecar for a SCIP index.

    Run it right after the producer, in the same working tree: it records the
    producer name/version, the declared position encoding, the exact index
    digest and the SHA-256 of every indexed document as it is on disk now.
    Reads only; writes only OUTPUT. Never an MCP tool.
    """
    from atlas_aci.providers import scip

    log = structlog.get_logger()
    try:
        raw = scip.build_manifest(repo.resolve(), index_path.resolve(), position_encoding)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(raw)
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    log.info("scip_manifest_written", output=str(output), bytes=len(raw))


@cli.command(name="import-scip")
@click.option(
    "--graph-root",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="External graph storage root (default: <repo>/.atlas; independent of memex)",
)
@click.option(
    "--repo", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path)
)
@click.option(
    "--manifest",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="The `atlas-scip-manifest.v1` sidecar written by `atlas-aci scip-manifest`",
)
@click.argument(
    "index_path", metavar="INDEX", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
def import_scip(
    repo: Path, manifest: Path, index_path: Path, graph_root: Path | None = None
) -> None:
    """Import an official scip-typescript index onto the current published graph.

    Validates everything (decode bounds, producer, manifest, source identity,
    positions) before taking the writer lock; on any rejection the published
    graph is untouched. On success publishes a derived `scip_import` generation
    atomically. WRITES to the selected graph directory only; never an MCP tool.
    """
    log = structlog.get_logger()
    repo = repo.resolve()
    log.info("import_scip_start", repo=str(repo), index=str(index_path))
    graph = _open_graph(repo, graph_root)
    try:
        stats = graph.import_scip(index_path.resolve(), manifest.resolve())
    except (ValueError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        graph.close()
    click.echo(json.dumps(stats, sort_keys=True, separators=(",", ":")))
    log.info("import_scip_done", **stats)


@cli.command(name="coverage-probe")
def coverage_probe() -> None:
    """Print the environment probe for the TEST interpreter (it prints nothing else).

    Atlas never spawns your interpreter. Pipe the probe into the environment that produced
    the coverage report: ``atlas-aci coverage-probe | <venv>/bin/python -I - > env.json``.
    """
    click.echo(
        (Path(__file__).parent / "providers" / "coverage_probe.py").read_text("utf-8"), nl=False
    )


@cli.command(name="coverage-manifest")
@click.option(
    "--repo",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="The producer's working tree (the pytest rootdir the coverage run measured)",
)
@click.option(
    "--environment",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="The probe output captured in the test environment (`atlas-aci coverage-probe`)",
)
@click.option("--output", required=True, type=click.Path(dir_okay=False, path_type=Path))
@click.argument(
    "report_path",
    metavar="COVERAGE_JSON",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
def coverage_manifest(repo: Path, environment: Path, output: Path, report_path: Path) -> None:
    """Write the producer-time `atlas-coverage-manifest.v1` sidecar for a coverage report.

    Records the exact report digest, the probe's environment attestation and the SHA-256 of
    every measured file and test file as it is on disk now. Reads only; writes only OUTPUT,
    which may not lie inside the repository. Never an MCP tool.
    """
    from atlas_aci.providers import coverage_py

    log = structlog.get_logger()
    try:
        resolved = output.resolve()
        if resolved == repo.resolve() or repo.resolve() in resolved.parents:
            raise click.ClickException("--output must not be inside --repo")
        raw = coverage_py.build_manifest(
            repo.resolve(), report_path.resolve(), environment.resolve()
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(raw)
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    log.info("coverage_manifest_written", output=str(output), bytes=len(raw))


@cli.command(name="import-coverage")
@click.option(
    "--graph-root",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="External graph storage root (default: <repo>/.atlas; independent of memex)",
)
@click.option(
    "--repo", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path)
)
@click.option(
    "--manifest",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="The `atlas-coverage-manifest.v1` sidecar written by `atlas-aci coverage-manifest`",
)
@click.argument(
    "report_path",
    metavar="COVERAGE_JSON",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
def import_coverage(
    repo: Path, manifest: Path, report_path: Path, graph_root: Path | None = None
) -> None:
    """Import one per-test coverage.py run onto the current published graph.

    Validates everything (strict report, sidecar, producer, source and test-file identity,
    line mapping, bounds) before taking the writer lock; on any rejection the published
    graph is untouched. On success publishes a derived `coverage_import` generation
    atomically. Records OBSERVED EXECUTION only: never a verdict about behavior. WRITES to
    the selected graph directory only; never an MCP tool.
    """
    log = structlog.get_logger()
    repo = repo.resolve()
    log.info("import_coverage_start", repo=str(repo), report=str(report_path))
    graph = _open_graph(repo, graph_root)
    try:
        stats = graph.import_coverage(report_path.resolve(), manifest.resolve())
    except (ValueError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        graph.close()
    click.echo(json.dumps(stats, sort_keys=True, separators=(",", ":")))
    log.info("import_coverage_done", **stats)


def _graph_root_option(name: str, help_text: str):
    return click.option(
        name, default=None, type=click.Path(file_okay=False, path_type=Path), help=help_text
    )


@cli.command()
@click.option(
    "--repo",
    default=None,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Repository for both sides (overridden per side by --before-repo/--after-repo)",
)
@click.option(
    "--before-repo",
    default=None,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Repository of the before snapshot",
)
@click.option(
    "--after-repo",
    default=None,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Repository of the after snapshot",
)
@_graph_root_option(
    "--graph-root", "External graph root for both sides (default: <repo>/.atlas per side)"
)
@_graph_root_option("--before-graph-root", "Graph root of the before snapshot")
@_graph_root_option("--after-graph-root", "Graph root of the after snapshot")
def diff(
    repo: Path | None,
    before_repo: Path | None,
    after_repo: Path | None,
    graph_root: Path | None,
    before_graph_root: Path | None,
    after_graph_root: Path | None,
) -> None:
    """Compare two indexed snapshots at symbol and contract boundaries.

    Each side is a complete, already-built graph namespace. Prints the canonical
    `atlas-symbol-diff.v1` change set (JSON) on stdout. READ-ONLY: nothing is
    written under either graph root or repository, and nothing is printed on
    rejection (exit 1) or invalid usage (exit 2).
    """
    from atlas_aci.query import diff as symbol_diff

    first = before_repo or repo
    second = after_repo or repo
    if first is None or second is None:
        raise click.UsageError("Give --repo, or both --before-repo and --after-repo.")
    try:
        change_set = symbol_diff.compare(
            symbol_diff.Side(first.resolve(), before_graph_root or graph_root),
            symbol_diff.Side(second.resolve(), after_graph_root or graph_root),
        )
    except symbol_diff.DiffUsageError as exc:
        raise click.UsageError(str(exc)) from exc
    except symbol_diff.DiffRejected as exc:
        raise click.ClickException(f"Snapshot diff rejected: {exc.reason}") from exc
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    stream = click.get_binary_stream("stdout")
    stream.write(symbol_diff.canonical_bytes(change_set))
    stream.flush()


def _csv(value: str | None) -> list[str] | None:
    return None if value is None else [v.strip() for v in value.split(",") if v.strip()]


@cli.command()
@click.option(
    "--repo",
    default=None,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Repository for both sides (overridden per side by --before-repo/--after-repo)",
)
@click.option(
    "--before-repo",
    default=None,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Repository of the before snapshot",
)
@click.option(
    "--after-repo",
    default=None,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Repository of the after snapshot",
)
@_graph_root_option(
    "--graph-root", "External graph root for both sides (default: <repo>/.atlas per side)"
)
@_graph_root_option("--before-graph-root", "Graph root of the before snapshot")
@_graph_root_option("--after-graph-root", "Graph root of the after snapshot")
@click.option("--relations", default=None, help="Comma-separated propagation relations")
@click.option("--confidence", default=None, help="Comma-separated: EXTRACTED,INFERRED")
@click.option("--max-bytes", default=None, type=click.IntRange(min=0))
@click.option("--max-seeds", default=None, type=int)
@click.option("--max-depth", default=None, type=int)
@click.option("--max-visited-nodes", default=None, type=int)
@click.option("--max-examined-edges", default=None, type=int)
@click.option("--max-boundary-probes", default=None, type=int)
@click.option("--max-vm-operations", default=None, type=int)
@click.option("--deadline-ms", default=None, type=int)
@click.option("--max-diff-entities", default=None, type=click.IntRange(min=0))
@click.option("--max-diff-occurrences", default=None, type=click.IntRange(min=0))
@click.option("--max-changed-files", default=None, type=click.IntRange(min=0))
def impact(
    repo: Path | None,
    before_repo: Path | None,
    after_repo: Path | None,
    graph_root: Path | None,
    before_graph_root: Path | None,
    after_graph_root: Path | None,
    relations: str | None,
    confidence: str | None,
    max_bytes: int | None,
    max_seeds: int | None,
    max_depth: int | None,
    max_visited_nodes: int | None,
    max_examined_edges: int | None,
    max_boundary_probes: int | None,
    max_vm_operations: int | None,
    deadline_ms: int | None,
    max_diff_entities: int | None,
    max_diff_occurrences: int | None,
    max_changed_files: int | None,
) -> None:
    """Explain POTENTIAL change impact of two indexed snapshots by typed reverse paths.

    Same library function as `graph_query op=impact`; same canonical envelope. Static
    reachability only: never a breakage claim. READ-ONLY: nothing is written under
    either graph root or repository; nothing is printed on rejection (exit 1) or
    invalid usage (exit 2).
    """
    from atlas_aci.query import diff as symbol_diff
    from atlas_aci.query import impact as change_impact
    from atlas_aci.query.impact_limits import DEFAULT_DIFF_LIMITS

    first = before_repo or repo
    second = after_repo or repo
    if first is None or second is None:
        raise click.UsageError("Give --repo, or both --before-repo and --after-repo.")
    chosen = {
        "relations": _csv(relations),
        "confidence": _csv(confidence),
        "max_bytes": max_bytes,
        "max_seeds": max_seeds,
        "max_depth": max_depth,
        "max_visited_nodes": max_visited_nodes,
        "max_examined_edges": max_examined_edges,
        "max_boundary_probes": max_boundary_probes,
        "max_vm_operations": max_vm_operations,
        "deadline_ms": deadline_ms,
    }
    arguments = {k: v for k, v in chosen.items() if v is not None}
    limits = {
        "max_entities": max_diff_entities,
        "max_occurrences": max_diff_occurrences,
        "max_changed_files": max_changed_files,
    }
    settings = change_impact.Settings(
        diff_limits={**DEFAULT_DIFF_LIMITS, **{k: v for k, v in limits.items() if v is not None}}
    )
    try:
        envelope = change_impact.run(
            symbol_diff.Side(first.resolve(), before_graph_root or graph_root),
            symbol_diff.Side(second.resolve(), after_graph_root or graph_root),
            arguments,
            settings,
        )
    except symbol_diff.DiffUsageError as exc:
        raise click.UsageError(str(exc)) from exc
    except change_impact.ToolError as exc:
        if exc.code in {"INVALID_INPUT", "INVALID_BUDGET"}:
            raise click.UsageError(f"{exc.code}: {exc.message}") from exc
        reason = getattr(exc, "reason", None)
        raise click.ClickException(
            f"Impact rejected: {exc.code}" + (f": {reason}" if reason else "")
        ) from exc
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    stream = click.get_binary_stream("stdout")
    stream.write(change_impact.canonical_bytes(envelope))
    stream.flush()


@cli.command("validation-plan")
@click.option(
    "--repo",
    default=None,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Repository for both sides (overridden per side by --before-repo/--after-repo)",
)
@click.option(
    "--before-repo",
    default=None,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Repository of the before snapshot",
)
@click.option(
    "--after-repo",
    default=None,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Repository of the after snapshot",
)
@_graph_root_option(
    "--graph-root", "External graph root for both sides (default: <repo>/.atlas per side)"
)
@_graph_root_option("--before-graph-root", "Graph root of the before snapshot")
@_graph_root_option("--after-graph-root", "Graph root of the after snapshot")
@click.option(
    "--coverage-graph-root",
    "coverage_graph_roots",
    multiple=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Extra coverage source: a graph root built for the same repository",
)
@click.option("--relations", default=None, help="Comma-separated propagation relations")
@click.option("--confidence", default=None, help="Comma-separated: EXTRACTED,INFERRED")
@click.option("--max-seeds", default=None, type=int)
@click.option("--max-depth", default=None, type=int)
@click.option("--max-visited-nodes", default=None, type=int)
@click.option("--max-examined-edges", default=None, type=int)
@click.option("--max-vm-operations", default=None, type=int)
@click.option("--deadline-ms", default=None, type=int)
@click.option("--max-diff-entities", default=None, type=click.IntRange(min=0))
@click.option("--max-diff-occurrences", default=None, type=click.IntRange(min=0))
@click.option("--max-changed-files", default=None, type=click.IntRange(min=0))
@click.option("--max-spans", default=None, type=int)
@click.option("--max-recommendations", default=None, type=int)
@click.option("--max-gaps", default=None, type=int)
@click.option("--max-evidence-per-recommendation", default=None, type=int)
@click.option("--max-matched-lines", default=None, type=int)
@click.option("--max-observation-rows", default=None, type=int)
@click.option("--max-reference-probes", default=None, type=int)
@click.option("--max-reference-rows", default=None, type=int)
@click.option("--max-coverage-sources", default=None, type=int)
def validation_plan(
    repo: Path | None,
    before_repo: Path | None,
    after_repo: Path | None,
    graph_root: Path | None,
    before_graph_root: Path | None,
    after_graph_root: Path | None,
    coverage_graph_roots: tuple[Path, ...],
    relations: str | None,
    confidence: str | None,
    max_seeds: int | None,
    max_depth: int | None,
    max_visited_nodes: int | None,
    max_examined_edges: int | None,
    max_vm_operations: int | None,
    deadline_ms: int | None,
    max_diff_entities: int | None,
    max_diff_occurrences: int | None,
    max_changed_files: int | None,
    max_spans: int | None,
    max_recommendations: int | None,
    max_gaps: int | None,
    max_evidence_per_recommendation: int | None,
    max_matched_lines: int | None,
    max_observation_rows: int | None,
    max_reference_probes: int | None,
    max_reference_rows: int | None,
    max_coverage_sources: int | None,
) -> None:
    """Plan which tests to run for a change: ranked, evidence-backed, ADVISORY only.

    Composes `impact` with imported per-test coverage (`import-coverage`): observed
    execution matches a changed span only through a byte-identical file snapshot of the
    before or after side; stale coverage is reported and never used. Uncovered spans are
    explicit gaps. Nothing is executed, imported or collected; READ-ONLY. Nothing is
    printed on rejection (exit 1) or invalid usage (exit 2).
    """
    from atlas_aci.enforcement import ToolError
    from atlas_aci.query import diff as symbol_diff
    from atlas_aci.query import validation
    from atlas_aci.query.impact import Settings
    from atlas_aci.query.impact_limits import DEFAULT_DIFF_LIMITS

    first = before_repo or repo
    second = after_repo or repo
    if first is None or second is None:
        raise click.UsageError("Give --repo, or both --before-repo and --after-repo.")
    chosen = {
        "relations": _csv(relations),
        "confidence": _csv(confidence),
        "max_seeds": max_seeds,
        "max_depth": max_depth,
        "max_visited_nodes": max_visited_nodes,
        "max_examined_edges": max_examined_edges,
        "max_vm_operations": max_vm_operations,
        "deadline_ms": deadline_ms,
        "max_spans": max_spans,
        "max_recommendations": max_recommendations,
        "max_gaps": max_gaps,
        "max_evidence_per_recommendation": max_evidence_per_recommendation,
        "max_matched_lines": max_matched_lines,
        "max_observation_rows": max_observation_rows,
        "max_reference_probes": max_reference_probes,
        "max_reference_rows": max_reference_rows,
        "max_coverage_sources": max_coverage_sources,
    }
    arguments = {k: v for k, v in chosen.items() if v is not None}
    limits = {
        "max_entities": max_diff_entities,
        "max_occurrences": max_diff_occurrences,
        "max_changed_files": max_changed_files,
    }
    settings = Settings(
        diff_limits={**DEFAULT_DIFF_LIMITS, **{k: v for k, v in limits.items() if v is not None}}
    )
    try:
        plan = validation.run(
            symbol_diff.Side(first.resolve(), before_graph_root or graph_root),
            symbol_diff.Side(second.resolve(), after_graph_root or graph_root),
            arguments,
            settings,
            [root.resolve() for root in coverage_graph_roots],
        )
    except symbol_diff.DiffUsageError as exc:
        # Fixed text: the underlying message may spell a path.
        raise click.UsageError("A snapshot side cannot be opened as a graph namespace.") from exc
    except ToolError as exc:
        if exc.code in {"INVALID_INPUT", "INVALID_BUDGET"}:
            raise click.UsageError(f"{exc.code}: {exc.message}") from exc
        reason = getattr(exc, "reason", None)
        raise click.ClickException(
            f"Validation plan rejected: {exc.code}" + (f": {reason}" if reason else "")
        ) from exc
    except (ValueError, OSError) as exc:
        # Never `str(exc)`: a message may carry a repository or graph-root path.
        raise click.ClickException("Validation plan failed: " + type(exc).__name__) from exc
    stream = click.get_binary_stream("stdout")
    stream.write(validation.canonical_bytes(plan))
    stream.flush()


@cli.command("check-architecture")
@click.option(
    "--repo",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Repository whose indexed snapshot is checked",
)
@click.option(
    "--graph-root",
    default=None,
    type=click.Path(path_type=Path),
    help="External graph root (default: <repo>/.atlas)",
)
@click.option(
    "--rules",
    "rules_path",
    required=True,
    type=click.Path(path_type=Path),
    help="atlas-architecture-rules.v1 JSON document",
)
@click.option("--max-depth", default=None, type=int, help="Hops per source search (1..32)")
@click.option("--max-visited-nodes", default=None, type=int, help="Files per search (1..1000)")
@click.option("--max-examined-edges", default=None, type=int, help="Import rows per search")
@click.option(
    "--max-total-examined-edges", default=None, type=int, help="Import rows per run (0..2000000)"
)
@click.option("--max-sources-per-rule", default=None, type=int, help="Sources per rule (1..4096)")
@click.option(
    "--max-counterexamples-per-rule", default=None, type=int, help="Emitted witnesses (1..64)"
)
@click.option("--max-boundaries-per-rule", default=None, type=int, help="Emitted boundaries")
def check_architecture(
    repo: Path,
    graph_root: Path | None,
    rules_path: Path,
    max_depth: int | None,
    max_visited_nodes: int | None,
    max_examined_edges: int | None,
    max_total_examined_edges: int | None,
    max_sources_per_rule: int | None,
    max_counterexamples_per_rule: int | None,
    max_boundaries_per_rule: int | None,
) -> None:
    """Check declarative forbidden-import rules against one indexed snapshot.

    Prints the canonical `atlas-architecture-report.v1` (JSON) on stdout. Exit 0 means the
    check ran, not that the repository complies: read each rule's `outcome` and `reasons`.
    READ-ONLY: nothing is written under the repository or graph root; nothing is printed on
    rejection (exit 1) or invalid usage (exit 2).
    """
    import sqlite3

    from atlas_aci.architecture import checker, rules
    from atlas_aci.query import diff as symbol_diff

    chosen = {
        "max_depth": max_depth,
        "max_visited_nodes": max_visited_nodes,
        "max_examined_edges": max_examined_edges,
        "max_total_examined_edges": max_total_examined_edges,
        "max_sources_per_rule": max_sources_per_rule,
        "max_counterexamples_per_rule": max_counterexamples_per_rule,
        "max_boundaries_per_rule": max_boundaries_per_rule,
    }
    try:
        limits = checker.normalize_limits(chosen)
    except checker.ArchitectureUsageError as exc:
        raise click.UsageError("Invalid architecture check limit: " + exc.limit) from None
    try:
        with rules_path.open("rb") as handle:
            raw = handle.read(rules.MAX_DOCUMENT_BYTES + 1)
    except OSError:
        # Fixed text: the underlying message spells the path.
        raise click.ClickException("Architecture rules file cannot be read.") from None
    try:
        report = checker.run(symbol_diff.Side(repo.resolve(), graph_root), raw, limits)
    except rules.InvalidRule as exc:
        where = "" if exc.rule_index is None else f" (rule {exc.rule_index})"
        raise click.ClickException(
            f"Architecture rules rejected: invalid_rule: {exc.reason}{where}"
        ) from None
    except checker.ArchitectureRejected as exc:
        raise click.ClickException(f"Architecture check rejected: {exc.reason}") from None
    except symbol_diff.DiffUsageError:
        raise click.UsageError("The repository cannot be opened as a graph namespace.") from None
    except (OSError, ValueError, sqlite3.Error) as exc:
        # Never `str(exc)`: a message may carry a repository or graph-root path.
        raise click.ClickException("Architecture check failed: " + type(exc).__name__) from None
    stream = click.get_binary_stream("stdout")
    stream.write(checker.canonical_bytes(report))
    stream.flush()


_COUNTERFACTUAL_LIMITS = (
    ("max_patch_bytes", "Patch size in bytes (1..262144)"),
    ("max_files", "Files in the patch (1..32)"),
    ("max_hunks", "Hunks in the patch (1..1024)"),
    ("max_line_bytes", "Bytes per patch line (1..65536)"),
    ("max_result_file_bytes", "Bytes per patched file (1..8388608)"),
    ("max_base_files", "Observed files in the base (1..200000)"),
    ("max_base_bytes", "Distinct source bytes in the base (1..2147483648)"),
    ("max_listed_failures", "Failed files listed in the report (0..1024)"),
    ("max_diff_entities", "S19 entity admission bound (0..10^9)"),
    ("max_diff_occurrences", "S19 occurrence admission bound (0..10^9)"),
    ("max_changed_files", "S19 changed-file admission bound (0..10^9)"),
    ("max_depth", "Rules: hops per source search (1..32)"),
    ("max_visited_nodes", "Rules: files per search (1..1000)"),
    ("max_examined_edges", "Rules: import rows per search"),
    ("max_total_examined_edges", "Rules: import rows per run (0..2000000)"),
    ("max_sources_per_rule", "Rules: sources per rule (1..4096)"),
    ("max_counterexamples_per_rule", "Rules: emitted witnesses (1..64)"),
    ("max_boundaries_per_rule", "Rules: emitted boundaries"),
)


def _counterfactual_limit_options(command):
    for name, help_text in reversed(_COUNTERFACTUAL_LIMITS):
        command = click.option(
            "--" + name.replace("_", "-"), name, default=None, type=int, help=help_text
        )(command)
    return command


@cli.command()
@click.option(
    "--repo",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Repository whose indexed snapshot is the base",
)
@click.option(
    "--graph-root",
    default=None,
    type=click.Path(path_type=Path),
    help="External graph root of the base (default: <repo>/.atlas)",
)
@click.option(
    "--base-snapshot",
    "base_snapshot",
    required=True,
    help="Snapshot id the patch is declared against (must be the active snapshot)",
)
@click.option(
    "--patch",
    "patch_path",
    required=True,
    type=click.Path(dir_okay=False, path_type=Path),
    help="Unified diff (git or plain headers); read once, never applied to the checkout",
)
@click.option(
    "--rules",
    "rules_path",
    default=None,
    type=click.Path(dir_okay=False, path_type=Path),
    help="Optional atlas-architecture-rules.v1 document evaluated before and after",
)
@click.option(
    "--scratch-root",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="Existing directory for the temporary staged namespace (default: the system temp dir)",
)
@click.option("--timing", is_flag=True, help="Print the elapsed milliseconds to stderr")
@_counterfactual_limit_options
def counterfactual(
    repo: Path,
    graph_root: Path | None,
    base_snapshot: str,
    patch_path: Path,
    rules_path: Path | None,
    scratch_root: Path | None,
    timing: bool,
    **limits: int | None,
) -> None:
    """Analyze a proposed patch in an isolated hypothetical snapshot.

    Prints the canonical `atlas-counterfactual-report.v1` (JSON) on stdout. The report is
    HYPOTHETICAL: the patch is applied to the base snapshot's source bytes, indexed into a
    temporary namespace outside the repository, compared with the base, and discarded. The
    checkout, git state and active graph are never modified. Exit 0 means the analysis ran:
    read each `analyses` status. Nothing is printed on rejection (exit 1) or invalid usage
    (exit 2).
    """
    import re
    import sqlite3

    from atlas_aci.architecture import rules
    from atlas_aci.counterfactual import analyze, patch_input
    from atlas_aci.query import diff as symbol_diff

    started = None
    if timing:
        import time

        started = time.perf_counter_ns()
    if re.fullmatch(r"[0-9a-f]{64}", base_snapshot) is None:
        raise click.UsageError("--base-snapshot must be a 64-character lowercase hex snapshot id.")
    try:
        effective = analyze.normalize_limits(limits, with_rules=rules_path is not None)
    except analyze.CounterfactualUsageError as exc:
        raise click.UsageError("Invalid counterfactual limit: " + str(exc.name)) from None
    try:
        with patch_path.open("rb") as handle:
            raw_patch = handle.read(int((effective["patch"] or {})["max_patch_bytes"]) + 1)
    except OSError:
        # Fixed text: the underlying message spells the path.
        raise click.ClickException("Counterfactual patch file cannot be read.") from None
    raw_rules = None
    if rules_path is not None:
        try:
            with rules_path.open("rb") as handle:
                raw_rules = handle.read(rules.MAX_DOCUMENT_BYTES + 1)
        except OSError:
            raise click.ClickException("Architecture rules file cannot be read.") from None
    try:
        report = analyze.run(
            symbol_diff.Side(repo.resolve(), graph_root),
            base_snapshot,
            raw_patch,
            rule_bytes=raw_rules,
            limits=limits,
            scratch_root=scratch_root,
        )
    except patch_input.PatchRejected as exc:
        raise click.ClickException(f"Counterfactual patch rejected: {exc.reason}") from None
    except rules.InvalidRule as exc:
        where = "" if exc.rule_index is None else f" (rule {exc.rule_index})"
        raise click.ClickException(
            f"Architecture rules rejected: invalid_rule: {exc.reason}{where}"
        ) from None
    except analyze.CounterfactualUsageError as exc:
        usage = {
            "limit": "Invalid counterfactual limit: " + str(exc.name),
            "base_snapshot": "--base-snapshot must be a 64-character lowercase hex snapshot id.",
            "scratch_root": (
                "The scratch root must be an existing directory outside the repository "
                "and its graph root."
            ),
            "namespace": "The repository cannot be opened as a graph namespace.",
        }
        raise click.UsageError(usage[exc.kind]) from None
    except analyze.CounterfactualRejected as exc:
        raise click.ClickException(f"Counterfactual analysis rejected: {exc.reason}") from None
    except analyze.CounterfactualFailed as exc:
        detail = "" if exc.detail is None else f": {exc.detail}"
        raise click.ClickException(
            f"Counterfactual analysis failed: {exc.reason}{detail}"
        ) from None
    except (OSError, ValueError, sqlite3.Error) as exc:
        # Never `str(exc)`: a message may carry a repository, graph-root or patch path.
        raise click.ClickException(
            "Counterfactual analysis failed: " + type(exc).__name__
        ) from None
    stream = click.get_binary_stream("stdout")
    stream.write(analyze.canonical_bytes(report))
    stream.flush()
    if started is not None:
        import time

        elapsed = (time.perf_counter_ns() - started) // 1_000_000
        click.echo(f"Counterfactual timing: elapsed_ms={elapsed}", err=True)


_OPENAPI_LIMITS = (
    ("max_document_bytes", "Artifact size in bytes (1..33554432)"),
    ("max_operations", "Path keys and operations, each (1..20000)"),
    ("max_parameters_per_operation", "Parameter entries per operation (1..1024)"),
    ("max_ref_depth", "Hops in one reference chain (1..64)"),
    ("max_ref_expansions", "Pointer resolutions per import (0..1000000)"),
    ("max_diagnostics", "Emitted diagnostics (0..100000)"),
)


def _openapi_limit_options(command):
    for name, help_text in reversed(_OPENAPI_LIMITS):
        command = click.option(
            "--" + name.replace("_", "-"), name, default=None, type=int, help=help_text
        )(command)
    return command


def _read_openapi(path: Path, limit: int, side: str) -> bytes:
    try:
        with path.open("rb") as handle:
            return handle.read(limit + 1)
    except OSError:
        # Fixed text: the underlying message spells the path.
        raise click.ClickException("OpenAPI document cannot be read" + side + ".") from None


@cli.command("openapi-snapshot")
@click.argument("document", type=click.Path(path_type=Path))
@_openapi_limit_options
def openapi_snapshot(document: Path, **chosen: int | None) -> None:
    """Import one OpenAPI 3.1 JSON document into a contract snapshot.

    Prints the canonical `atlas-contract-snapshot.v1` (JSON) on stdout. The snapshot is a
    contract snapshot, not a source snapshot: its id binds the artifact bytes, the profile and
    the limits. Exit 0 means the import ran; read `status` and `diagnostics` for incomplete
    regions. READ-ONLY: nothing is written; nothing is printed on rejection (exit 1) or invalid
    usage (exit 2).
    """
    from atlas_aci.importers import openapi31

    try:
        options = openapi31.normalize_options(chosen)
    except openapi31.OpenAPIUsageError as exc:
        raise click.UsageError("Invalid OpenAPI limit: " + exc.limit) from None
    raw = _read_openapi(document, options.max_document_bytes, "")
    try:
        snapshot = openapi31.import_document(raw, options)
        openapi31.validate_snapshot(snapshot)
    except openapi31.OpenAPIRejected as exc:
        raise click.ClickException("OpenAPI document rejected: " + exc.reason) from None
    except (OSError, ValueError) as exc:
        # Never `str(exc)`: a message may carry document text or a path.
        raise click.ClickException("OpenAPI import failed: " + type(exc).__name__) from None
    stream = click.get_binary_stream("stdout")
    stream.write(openapi31.canonical_bytes(snapshot))
    stream.flush()


@cli.command("openapi-diff")
@click.option("--before", "before_path", required=True, type=click.Path(path_type=Path))
@click.option("--after", "after_path", required=True, type=click.Path(path_type=Path))
@click.option("--max-findings", "max_findings", default=None, type=int, help="Emitted findings")
@_openapi_limit_options
def openapi_diff(
    before_path: Path, after_path: Path, max_findings: int | None, **chosen: int | None
) -> None:
    """Explain contract changes between two OpenAPI 3.1 JSON documents.

    Imports both documents with the same limits, then prints the canonical
    `atlas-contract-diff.v1` (JSON) on stdout. The change set reuses the shape of the S19 change
    set; S19 is not invoked. Exit 0 means the comparison ran, not that nothing changed: an empty
    finding list is not evidence that the API is unaffected. READ-ONLY: nothing is written;
    nothing is printed on rejection (exit 1) or invalid usage (exit 2).
    """
    from atlas_aci.contracts import openapi_diff as contract_diff
    from atlas_aci.importers import openapi31

    try:
        options = openapi31.normalize_options(chosen)
        diff_options = contract_diff.normalize_diff_options({"max_findings": max_findings})
    except openapi31.OpenAPIUsageError as exc:
        raise click.UsageError("Invalid OpenAPI limit: " + exc.limit) from None
    snapshots = []
    for label, path in (("before", before_path), ("after", after_path)):
        raw = _read_openapi(path, options.max_document_bytes, ": " + label)
        try:
            snapshots.append(openapi31.import_document(raw, options))
        except openapi31.OpenAPIRejected as exc:
            raise click.ClickException(
                "OpenAPI document rejected: " + exc.reason + " (" + label + ")"
            ) from None
        except (OSError, ValueError) as exc:
            raise click.ClickException("OpenAPI import failed: " + type(exc).__name__) from None
    try:
        change_set = contract_diff.diff(snapshots[0], snapshots[1], diff_options)
        contract_diff.validate_diff(change_set)
    except contract_diff.ContractDiffRejected as exc:
        raise click.ClickException("OpenAPI diff rejected: " + exc.reason) from None
    except (OSError, ValueError) as exc:
        raise click.ClickException("OpenAPI import failed: " + type(exc).__name__) from None
    stream = click.get_binary_stream("stdout")
    stream.write(contract_diff.canonical_bytes(change_set))
    stream.flush()


@cli.command()
@click.option(
    "--repo", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path)
)
def tools(repo: Path) -> None:
    """Print the tool manifest as JSON (for quick inspection)."""
    import json

    from atlas_aci.server import build_tool_manifest

    config = Config(repo=repo.resolve(), memex_root=repo / ".atlas" / "memex")
    manifest = build_tool_manifest(config)
    click.echo(json.dumps(manifest, indent=2))


@cli.command("evidence-report")
@click.argument("envelope", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--html",
    "html_out",
    required=True,
    type=click.Path(dir_okay=False, path_type=Path),
    help="Write the self-contained HTML report here.",
)
@click.option(
    "--json",
    "json_out",
    required=True,
    type=click.Path(dir_okay=False, path_type=Path),
    help="Write the canonical JSON report here.",
)
def evidence_report(envelope: Path, html_out: Path, json_out: Path) -> None:
    """Export one recorded `graph_query op=context` envelope as offline HTML and canonical JSON.

    ENVELOPE is a file holding one successful, default-bounds context response. Live lease
    URIs and continuation cursors are omitted from both outputs. Both outputs are rendered in
    memory first: a rejection (exit 1) writes nothing and leaves existing files untouched.
    Nothing is printed to stdout, and no server, repository or network is contacted.
    """
    import os

    from atlas_aci.reports import evidence

    def same(first: Path, second: Path) -> bool:
        if first.resolve() == second.resolve():
            return True
        try:
            return first.exists() and second.exists() and first.samefile(second)
        except OSError:
            return False

    if same(html_out, json_out) or same(html_out, envelope) or same(json_out, envelope):
        raise click.UsageError("ENVELOPE, --html and --json must be three different files.")
    try:
        with envelope.open("rb") as handle:
            raw = handle.read(evidence.MAX_INPUT_FILE_BYTES + 1)
    except OSError as exc:
        raise click.ClickException("Evidence report rejected: invalid_input") from exc
    try:
        report = evidence.export(raw)
    except evidence.ReportRejected as exc:
        raise click.ClickException(f"Evidence report rejected: {exc}") from None
    except Exception as exc:
        # Never `str(exc)`: a message may carry input text or a path.
        raise click.ClickException("Evidence report failed: " + type(exc).__name__) from None
    staged: list[tuple[Path, Path]] = []
    try:
        for target, payload in ((json_out, report.json), (html_out, report.html)):
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
            with temp.open("xb") as sink:
                staged.append((temp, target))
                sink.write(payload)
        for temp, target in staged:
            temp.replace(target)
    except OSError as exc:
        for temp, _ in staged:
            temp.unlink(missing_ok=True)
        raise click.ClickException("Evidence report failed: " + type(exc).__name__) from None


if __name__ == "__main__":
    cli()
