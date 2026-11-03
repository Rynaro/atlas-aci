"""CLI entry point: `atlas-aci serve | index | export | import | import-scip | diff | ...`."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

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
def serve(
    repo: Path, memex_root: Path | None, max_bytes_per_call: int, graph_root: Path | None = None
) -> None:
    """Start the MCP server over stdio."""
    from atlas_aci.server import run_stdio

    config = Config(
        repo=repo.resolve(),
        memex_root=(memex_root or repo / ".atlas" / "memex").resolve(),
        max_bytes_per_call=max_bytes_per_call,
        graph_root=graph_root,
    )
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


if __name__ == "__main__":
    cli()
