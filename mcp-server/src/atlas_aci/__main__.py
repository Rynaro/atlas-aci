"""CLI entry point: `atlas-aci serve | index | export | import | tools`."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import click
import structlog

from atlas_aci.codegraph import DEFAULT_LANGS, CodeGraph
from atlas_aci.config import Config


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
