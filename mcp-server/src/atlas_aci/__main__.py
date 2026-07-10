"""CLI entry point: `atlas-aci serve | index | export | import | tools`."""

from __future__ import annotations

import asyncio
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
    "--repo", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path)
)
@click.option("--memex-root", default=None, type=click.Path(path_type=Path))
@click.option("--max-bytes-per-call", default=8 * 1024, type=int)
def serve(repo: Path, memex_root: Path | None, max_bytes_per_call: int) -> None:
    """Start the MCP server over stdio."""
    from atlas_aci.server import run_stdio

    config = Config(
        repo=repo.resolve(),
        memex_root=(memex_root or repo / ".atlas" / "memex").resolve(),
        max_bytes_per_call=max_bytes_per_call,
    )
    asyncio.run(run_stdio(config))


@cli.command()
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
def index(repo: Path, langs: str, since: str | None) -> None:
    """Build or update the code-graph index for a repository."""
    log = structlog.get_logger()
    repo = repo.resolve()
    lang_list = [s.strip() for s in langs.split(",") if s.strip()]

    log.info("index_start", repo=str(repo), langs=lang_list, since=since)
    graph = CodeGraph(repo=repo, langs=lang_list)
    stats = graph.build(since=since)
    log.info("index_done", **stats)


@cli.command()
@click.option(
    "--repo", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path)
)
@click.argument("out_path", type=click.Path(path_type=Path))
def export(repo: Path, out_path: Path) -> None:
    """Export the code-graph index as canonical, deterministic JSONL (A5).

    The output is a portable, committable artifact (D6): `atlas-aci import
    <out_path> --repo <another-checkout>` reproduces a valid current-epoch
    DB without re-parsing a single source file. `OUT_PATH` may point
    anywhere on disk, including outside `--repo` — this is a CLI-only,
    operator-invoked command, never an MCP tool a served agent can call
    (see README.md's "Why read-only" section for the threat-model
    reasoning), so the enforcement layer's in-repo path restriction
    (`assert_path_in_repo`) does not apply here.

    Reads the current-epoch DB read-only; never writes to `.atlas`.

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

    graph = CodeGraph(repo=repo, read_only=True)
    if not graph.epoch_ok():
        raise click.ClickException(
            f"no current-epoch index found under {repo}/.atlas -- run "
            f"`atlas-aci index --repo {repo}` first."
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
    "--repo", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path)
)
@click.argument("in_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def import_(repo: Path, in_path: Path) -> None:
    """Import a canonical JSONL export, reproducing a current-epoch DB (A5).

    The cold-start workflow this exists for: commit a JSONL export
    alongside your repo (or fetch one from CI/a shared cache), then run
    `atlas-aci import <in_path> --repo <repo>` on a fresh checkout instead
    of a full `atlas-aci index` — skips re-parsing every source file.

    `import` WRITES to `<repo>/.atlas` (index-path only, DIR-2) and is
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
    graph = CodeGraph(repo=repo)
    try:
        stats = graph.import_jsonl(in_path)
    except ValueError as e:
        raise click.ClickException(str(e)) from e
    log.info("import_done", in_path=str(in_path), **stats)


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
