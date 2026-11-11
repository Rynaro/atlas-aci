"""CLI entry point: `atlas-aci serve | index | export | import | import-scip | ...`."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import click
import structlog

from atlas_aci.codegraph import DEFAULT_LANGS, CodeGraph
from atlas_aci.config import Config
from atlas_aci.frameworks import REGISTRY as FRAMEWORK_ADAPTERS
from atlas_aci.frameworks import normalize as normalize_frameworks
from atlas_aci.frameworks import rails_models

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


def _read_table_map(path: Path) -> bytes:
    """Bounded read of the operator table map (only the CLI reads it; the adapter takes bytes).

    Opened without blocking, then required to be a regular file, then read at most one byte
    past the limit. The messages are fixed text: the path and the document are never echoed.
    """
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        raise click.UsageError("Cannot read the Rails table map") from None
    try:
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise click.UsageError("The Rails table map is not a regular file")
            return stream.read(rails_models.MAX_MAP_BYTES + 1)
    except OSError:
        raise click.UsageError("Cannot read the Rails table map") from None


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
@click.option(
    "--graph-query-deadline",
    "graph_query_deadline",
    default=None,
    type=click.FloatRange(0, 3600),
    help=(
        "Server-side deadline in seconds for one graph_query/search_symbol call; 0 disables "
        "it (default 20). A call past it fails with GRAPH_DEADLINE_EXCEEDED."
    ),
)
@click.option(
    "--transport",
    default="stdio",
    show_default=True,
    type=click.Choice(["stdio", "http"]),
    help="stdio, or a loopback-only Streamable HTTP endpoint at /mcp (see hosts/http-transport.md)",
)
@click.option(
    "--host",
    default=None,
    help="HTTP only: loopback address or a name resolving only to loopback (default 127.0.0.1)",
)
@click.option(
    "--port",
    default=None,
    type=click.IntRange(0, 65535),
    help="HTTP only: port (default 8765; 0 picks a free port, reported by http_listening)",
)
@click.option(
    "--max-inflight",
    default=None,
    type=click.IntRange(1, 256),
    help="HTTP only: requests in flight before 503 (default 8)",
)
@click.option(
    "--max-body-bytes",
    default=None,
    type=click.IntRange(1024, 16 * 1024 * 1024),
    help="HTTP only: request body cap in bytes before 413 (default 1048576)",
)
@click.option(
    "--allowed-origins",
    default=None,
    help="HTTP only: comma-separated exact origins allowed to call (default: none)",
)
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
    graph_query_deadline: float | None = None,
    transport: str = "stdio",
    host: str | None = None,
    port: int | None = None,
    max_inflight: int | None = None,
    max_body_bytes: int | None = None,
    allowed_origins: str | None = None,
) -> None:
    """Start the MCP server over stdio (default) or loopback Streamable HTTP."""
    from atlas_aci.query.impact import validate_configuration

    http_options: dict[str, Any] = {
        name: value
        for name, value in (
            ("http_host", host),
            ("http_port", port),
            ("http_max_inflight", max_inflight),
            ("http_max_body_bytes", max_body_bytes),
            (
                "http_allowed_origins",
                tuple(o.strip() for o in allowed_origins.split(","))
                if allowed_origins is not None
                else None,
            ),
        )
        if value is not None
    }
    if transport != "http" and http_options:
        raise click.UsageError(
            "--host, --port, --max-inflight, --max-body-bytes and --allowed-origins "
            "require --transport http"
        )
    if transport == "http":
        _serve_http(
            repo=repo,
            memex_root=memex_root,
            max_bytes_per_call=max_bytes_per_call,
            graph_root=graph_root,
            before_graph_root=before_graph_root,
            before_repo=before_repo,
            impact_max_diff_entities=impact_max_diff_entities,
            impact_max_diff_occurrences=impact_max_diff_occurrences,
            impact_max_changed_files=impact_max_changed_files,
            http_options=http_options,
            graph_query_deadline=graph_query_deadline,
        )
        return

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
    if graph_query_deadline is not None:
        diff_options["graph_query_deadline_s"] = graph_query_deadline
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
    from atlas_aci.graph_dispatch import start_graph_warmup

    start_graph_warmup(config)
    try:
        asyncio.run(run_stdio(config))
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc


def _serve_http(
    *,
    repo: Path,
    memex_root: Path | None,
    max_bytes_per_call: int,
    graph_root: Path | None,
    before_graph_root: Path | None,
    before_repo: Path | None,
    impact_max_diff_entities: int | None,
    impact_max_diff_occurrences: int | None,
    impact_max_changed_files: int | None,
    http_options: dict[str, Any],
    graph_query_deadline: float | None = None,
) -> None:
    """`serve --transport http`: the HTTP module is imported here, never at CLI import."""
    from atlas_aci.protocol.http import LoopbackError, RefusedError, resolve_loopback, run_http
    from atlas_aci.query.impact import validate_configuration

    def loopback_usage(exc: LoopbackError) -> click.UsageError:
        return click.UsageError(
            f"--host must resolve only to loopback addresses (reason: {exc.reason}); "
            "a non-loopback bind needs S40 shared mode, which is not available"
        )

    if "http_host" in http_options:
        try:
            http_options["http_host"] = resolve_loopback(http_options["http_host"])
        except LoopbackError as exc:
            raise loopback_usage(exc) from None
    diff_options: dict[str, Any] = {
        name: value
        for name, value in (
            ("impact_max_diff_entities", impact_max_diff_entities),
            ("impact_max_diff_occurrences", impact_max_diff_occurrences),
            ("impact_max_changed_files", impact_max_changed_files),
        )
        if value is not None
    }
    if graph_query_deadline is not None:
        diff_options["graph_query_deadline_s"] = graph_query_deadline
    try:
        config = Config(
            repo=repo.resolve(),
            memex_root=(memex_root or repo / ".atlas" / "memex").resolve(),
            max_bytes_per_call=max_bytes_per_call,
            graph_root=graph_root,
            before_repo=before_repo,
            before_graph_root=before_graph_root,
            **diff_options,
            **http_options,
        )
    except ValueError as exc:
        if str(exc).startswith("http_"):
            raise click.UsageError(str(exc)) from None
        raise
    if config.test_execution_enabled:
        raise click.UsageError(
            "HTTP serving is not available while test execution is enabled "
            "(ATLAS_ACI_ENABLE_TEST_EXECUTION=1): any local process could reach it"
        )
    try:
        validate_configuration(config)
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    from atlas_aci.graph_dispatch import start_graph_warmup

    start_graph_warmup(config)
    try:
        asyncio.run(run_http(config))
    except LoopbackError as exc:
        raise loopback_usage(exc) from None
    except RefusedError as exc:
        raise click.UsageError(str(exc)) from None
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc


@cli.command("serve-shared")
@click.option(
    "--shared-config",
    "shared_config",
    required=True,
    type=click.Path(dir_okay=False, path_type=Path),
    help="Shared-deployment document (see docs/operations/shared-http-deployment.md)",
)
def serve_shared(shared_config: Path) -> None:
    """Serve registered repositories to authenticated principals over loopback HTTP."""
    from atlas_aci.protocol.http import LoopbackError, RefusedError
    from atlas_aci.query.shared_http import (
        DeploymentError,
        SharedConfigError,
        load_shared_config,
        run_shared,
        validate_deployment,
    )

    def loopback_usage(exc: LoopbackError) -> click.UsageError:
        return click.UsageError(
            "--shared-config: listen.host must resolve only to loopback addresses "
            f"(reason: {exc.reason})"
        )

    try:
        deployment = load_shared_config(shared_config, environ=os.environ)
    except SharedConfigError as exc:
        if exc.reason == "test_execution_enabled":
            raise click.UsageError(
                "shared serving is not available while test execution is enabled "
                "(ATLAS_ACI_ENABLE_TEST_EXECUTION=1)"
            ) from None
        raise click.UsageError(f"--shared-config: {exc}") from None
    except LoopbackError as exc:
        raise loopback_usage(exc) from None
    try:
        validate_deployment(deployment)
    except DeploymentError as exc:
        raise click.ClickException(str(exc)) from None
    from atlas_aci.graph_dispatch import start_graph_warmup

    for repository_config in deployment.configs.values():
        start_graph_warmup(repository_config)
    try:
        asyncio.run(run_shared(deployment))
    except LoopbackError as exc:
        raise loopback_usage(exc) from None
    except RefusedError as exc:
        raise click.UsageError(str(exc)) from None
    except OSError as exc:
        raise click.ClickException(str(exc)) from exc
    except ValueError:
        raise click.ClickException("The shared server could not start.") from None


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
@click.option(
    "--frameworks",
    default="",
    help="Comma-separated opt-in framework adapters; closed set: "
    + ", ".join(sorted(FRAMEWORK_ADAPTERS)),
)
@click.option(
    "--rails-table-map",
    default=None,
    type=click.Path(path_type=Path),
    help="Operator model-to-table map (JSON, atlas-rails-table-map.v1); needs the "
    "rails_models framework adapter",
)
def index(
    repo: Path,
    langs: str,
    since: str | None,
    frameworks: str = "",
    graph_root: Path | None = None,
    rails_table_map: Path | None = None,
) -> None:
    """Build or update the code-graph index for a repository."""
    log = structlog.get_logger()
    repo = repo.resolve()
    lang_list = [s.strip() for s in langs.split(",") if s.strip()]
    # Validated here, not in an option callback: click processes options in command-line
    # order, so only the command body sees both --langs and --frameworks. Nothing is opened
    # (and no graph directory is created) when the selection is rejected.
    try:
        adapters = normalize_frameworks(frameworks, lang_list or DEFAULT_LANGS)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc

    table_map: bytes | None = None
    if rails_table_map is not None:
        if "rails_models" not in adapters:
            raise click.UsageError("--rails-table-map requires --frameworks rails_models")
        table_map = _read_table_map(rails_table_map)
        try:
            rails_models.parse_table_map(table_map)
        except ValueError as exc:
            raise click.UsageError(str(exc)) from None

    log.info("index_start", repo=str(repo), langs=lang_list, since=since, frameworks=adapters)
    graph = _open_graph(
        repo, graph_root, langs=lang_list, frameworks=adapters, rails_table_map=table_map
    )
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


_OTEL_LIMITS = (
    ("max_artifact_bytes", "Artifact size in bytes (1..268435456)"),
    ("max_spans", "Spans per artifact (1..1000000)"),
    ("max_attributes_per_span", "Attributes per span (1..4096)"),
)


def _otel_limit_options(command):
    for name, help_text in reversed(_OTEL_LIMITS):
        command = click.option(
            "--" + name.replace("_", "-"), name, default=None, type=int, help=help_text
        )(command)
    return command


def _read_otel_input(path: Path, limit: int, message: str) -> bytes:
    """Bounded read of one operator input: opened without blocking, regular files only.

    A FIFO or a directory is refused instead of waiting on or enumerating it. The messages are
    fixed text: the path is never echoed.
    """
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        raise click.ClickException(message) from None
    try:
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise click.ClickException(message)
            return stream.read(limit + 1)
    except OSError:
        raise click.ClickException(message) from None


@cli.command("import-otel")
@click.option(
    "--repo", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path)
)
@click.option(
    "--graph-root",
    default=None,
    type=click.Path(path_type=Path),
    help="External graph storage root (default: <repo>/.atlas); read-only here",
)
@click.option(
    "--manifest",
    "manifest_path",
    required=True,
    type=click.Path(path_type=Path),
    help="Provenance manifest (JSON, atlas-otel-provenance.v1)",
)
@click.option(
    "--output",
    "output_path",
    required=True,
    type=click.Path(path_type=Path),
    help="Where the runtime overlay is written; outside --repo and the graph root",
)
@_otel_limit_options
@click.argument("artifact", type=click.Path(path_type=Path))
def import_otel(
    repo: Path,
    graph_root: Path | None,
    manifest_path: Path,
    output_path: Path,
    artifact: Path,
    **chosen: int | None,
) -> None:
    """Import one local OTLP JSON trace artifact into a separate runtime overlay.

    The overlay is bound to the ACTIVE snapshot of --repo (read-only lease); it does not write
    the graph, and the only file written is --output. Span nesting is recorded as
    `observed_parent_of` (observed nesting, not a call) and every result carries the coverage
    `observed_sample`. Prints the canonical `atlas-otel-import-summary.v1` on stdout; nothing is
    printed on rejection (exit 1) or invalid usage (exit 2).
    """
    import sqlite3

    from atlas_aci.importers import otel_json as otel
    from atlas_aci.overlays import runtime
    from atlas_aci.query.diff import DiffUsageError, Side

    try:
        limits = otel.normalize_limits(chosen)
    except otel.OtelUsageError as exc:
        raise click.UsageError("Invalid OpenTelemetry import limit: " + exc.limit) from None
    repo = repo.resolve()
    protected = [repo, repo / ".atlas"] + ([graph_root] if graph_root is not None else [])
    try:
        target = runtime.resolve_output(output_path, protected, [manifest_path, artifact])
    except runtime.OverlayUsageError as exc:
        if exc.reason == "output_equals_input":
            raise click.UsageError(
                "--output must differ from the manifest and the artifact."
            ) from None
        raise click.UsageError("--output must be outside --repo and the graph root.") from None
    manifest_raw = _read_otel_input(
        manifest_path, otel.MAX_MANIFEST_BYTES, "OpenTelemetry manifest cannot be read."
    )
    try:
        otel.parse_manifest(manifest_raw)
    except otel.OtelRejected as exc:
        raise click.ClickException("OpenTelemetry import rejected: " + exc.reason) from None
    artifact_raw = _read_otel_input(
        artifact, limits.max_artifact_bytes, "OpenTelemetry artifact cannot be read."
    )
    try:
        overlay = runtime.import_otel(Side(repo, graph_root), manifest_raw, artifact_raw, limits)
        data = runtime.canonical_bytes(overlay)
        summary = runtime.canonical_bytes(runtime.summary_document(overlay))
    except (otel.OtelRejected, runtime.OverlayRejected) as exc:
        raise click.ClickException("OpenTelemetry import rejected: " + exc.reason) from None
    except DiffUsageError:
        raise click.UsageError("The repository cannot be opened as a graph namespace.") from None
    except (OSError, ValueError, sqlite3.Error) as exc:
        # Never `str(exc)`: a message may carry a path or input text.
        raise click.ClickException("OpenTelemetry import failed: " + type(exc).__name__) from None
    try:
        runtime.write_atomic(target, data)
    except OSError:
        raise click.ClickException("Runtime overlay cannot be written.") from None
    stream = click.get_binary_stream("stdout")
    stream.write(summary)
    stream.flush()


_DEPENDENCY_LIMITS = (
    ("max_lock_bytes", "uv.lock size in bytes (1..268435456)"),
    ("max_wheel_bytes", "Wheel size in bytes (1..268435456)"),
    ("max_members", "Archive members (1..100000)"),
    ("max_total_uncompressed_bytes", "Total uncompressed bytes (1..1073741824)"),
    ("max_member_bytes", "Uncompressed bytes per member (1..268435456)"),
    ("max_records", "Declaration, import, export and diagnostic records (1..1000000)"),
    ("max_compression_ratio", "Uncompressed/compressed ratio per member (1..10000)"),
)


def _dependency_limit_options(command):
    for name, help_text in reversed(_DEPENDENCY_LIMITS):
        command = click.option(
            "--" + name.replace("_", "-"), name, default=None, type=int, help=help_text
        )(command)
    return command


def _read_dependency_input(path: Path, limit: int, message: str) -> bytes:
    """Bounded read of one operator input: opened without blocking, regular files only.

    A FIFO or a directory is refused instead of waiting on or enumerating it. The messages are
    fixed text: the path is never echoed.
    """
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        raise click.ClickException(message) from None
    try:
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise click.ClickException(message)
            return stream.read(limit + 1)
    except OSError:
        raise click.ClickException(message) from None


def _dependency_identity(path: Path | str) -> tuple[int, int] | None:
    try:
        info = os.stat(path)
    except OSError:
        return None
    return (info.st_dev, info.st_ino)


def _dependency_output(output: Path, inputs: list[Path]) -> Path:
    """The write target: its parent resolved once; it must differ from every input file."""
    parent = Path(os.path.realpath(output.parent))
    target = parent / output.name
    own = _dependency_identity(target)
    named = {os.path.realpath(item) for item in inputs}
    if str(target) in named or (
        own is not None and any(own == _dependency_identity(item) for item in inputs)
    ):
        raise click.UsageError("--output must differ from the lock and every wheel.")
    return target


def _write_dependency_atomic(target: Path, data: bytes) -> None:
    """Write beside `target` (same directory) and `os.replace` it; no partial output remains."""
    import tempfile
    from contextlib import suppress

    descriptor, name = tempfile.mkstemp(prefix=".atlas-python-dependency-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, target)
    except BaseException:
        with suppress(OSError):
            os.unlink(name)
        raise


@cli.command("import-python-dependency")
@click.option(
    "--lock",
    "lock_path",
    required=True,
    type=click.Path(path_type=Path),
    help="The uv.lock that pins the dependency (profile atlas-uv-lock-profile.v1)",
)
@click.option("--package", "package", required=True, help="Package name (PEP 503 normalised)")
@click.option(
    "--env",
    "env_items",
    multiple=True,
    help="Target environment KEY=VALUE (repeatable); needed only when the lock is forked",
)
@click.option(
    "--wheel",
    "wheel_paths",
    multiple=True,
    type=click.Path(path_type=Path),
    help="A local wheel (repeatable); only the one named like a locked pure wheel is opened",
)
@click.option(
    "--output",
    "output_path",
    required=True,
    type=click.Path(path_type=Path),
    help="Where the package snapshot is written (the only file written)",
)
@_dependency_limit_options
def import_python_dependency(
    lock_path: Path,
    package: str,
    env_items: tuple[str, ...],
    wheel_paths: tuple[Path, ...],
    output_path: Path,
    **chosen: int | None,
) -> None:
    """Index one uv-locked pure-Python dependency from a supplied local wheel.

    The wheel is selected by the lock for the declared environment, matched by its locked
    filename, checked against the locked sha256 and then read as an archive in memory: nothing
    is installed, downloaded, imported or executed. The result is a standalone, declaration-only
    `atlas-python-package-snapshot.v1` written to --output; the code graph is not touched.
    Prints the canonical `atlas-python-dependency-import-summary.v1` on stdout; nothing is
    printed on rejection (exit 1) or invalid usage (exit 2).
    """
    from atlas_aci.dependencies import python_wheel as wheel
    from atlas_aci.dependencies import uv_lock

    try:
        limits = uv_lock.normalize_limits(chosen)
        environment = uv_lock.parse_environment(env_items)
        uv_lock.parse_package_argument(package)
    except uv_lock.DependencyUsageError as exc:
        if exc.what == "env":
            raise click.UsageError("Invalid --env value.") from None
        if exc.what == "package":
            raise click.UsageError("Invalid --package name.") from None
        raise click.UsageError("Invalid Python dependency limit: " + exc.what) from None
    target = _dependency_output(output_path, [lock_path, *wheel_paths])
    lock_raw = _read_dependency_input(
        lock_path, limits.max_lock_bytes, "uv lock file cannot be read."
    )

    def reader(path: Path) -> Callable[[], bytes]:
        def read() -> bytes:
            return _read_dependency_input(
                path, limits.max_wheel_bytes, "Wheel file cannot be read."
            )

        return read

    supplied = [(path.name, reader(path)) for path in wheel_paths]
    try:
        snapshot = wheel.import_dependency(lock_raw, package, environment, supplied, limits)
        data = wheel.canonical_bytes(snapshot)
        summary = wheel.canonical_bytes(wheel.summary_document(snapshot, len(supplied) - 1))
    except uv_lock.DependencyRejected as exc:
        text = "Python dependency rejected: " + exc.reason
        if exc.coordinate is not None:
            text += " " + exc.coordinate["coordinate"]
        raise click.ClickException(text) from None
    except click.ClickException:
        raise
    except Exception as exc:
        # Never `str(exc)`: a message may carry a path or input text.
        raise click.ClickException(
            "Python dependency import failed: " + type(exc).__name__
        ) from None
    try:
        _write_dependency_atomic(target, data)
    except OSError:
        raise click.ClickException("Python dependency snapshot cannot be written.") from None
    stream = click.get_binary_stream("stdout")
    stream.write(summary)
    stream.flush()


_CROSS_REPO_LIMITS = (
    ("max_mapping_bytes", "Mapping size in bytes (1..1048576)"),
    ("max_lock_bytes", "uv.lock size in bytes (1..268435456)"),
    ("max_package_snapshot_bytes", "Package snapshot size in bytes (1..268435456)"),
    ("max_overlay_bytes", "Overlay size in bytes (1..268435456)"),
    ("max_references", "Candidate import references (1..1000000)"),
)
_CROSS_REPO_IMPACT_LIMITS = (
    ("max_results", "Impact results (1..100000)"),
    ("max_links_examined", "Records examined (1..1000000)"),
    ("max_boundaries", "Impact boundaries (1..100000)"),
    ("max_overlay_bytes", "Overlay size in bytes (1..268435456)"),
    ("max_mapping_bytes", "Mapping size in bytes (1..1048576)"),
)


def _cross_repo_options(table):
    def decorate(command):
        for name, help_text in reversed(table):
            command = click.option(
                "--" + name.replace("_", "-"), name, default=None, type=int, help=help_text
            )(command)
        return command

    return decorate


def _cross_repo_sides(command):
    for role in ("provider", "consumer"):
        command = _graph_root_option(
            f"--{role}-graph-root", f"External graph root of the {role} (default: <repo>/.atlas)"
        )(command)
        command = click.option(
            f"--{role}-repo",
            f"{role}_repo",
            required=True,
            type=click.Path(exists=True, file_okay=False, path_type=Path),
            help=f"Repository of the {role}",
        )(command)
    return command


def _read_cross_repo_input(path: Path, limit: int, message: str) -> bytes:
    """Bounded read of one operator input: opened without blocking, regular files only.

    A FIFO or a directory is refused instead of waiting on or enumerating it. The messages are
    fixed text: the path is never echoed.
    """
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        raise click.ClickException(message) from None
    try:
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise click.ClickException(message)
            return stream.read(limit + 1)
    except OSError:
        raise click.ClickException(message) from None


def _write_cross_repo_atomic(target: Path, data: bytes) -> None:
    """Write beside `target` (same directory) and `os.replace` it; no partial output remains."""
    import tempfile
    from contextlib import suppress

    descriptor, name = tempfile.mkstemp(prefix=".atlas-cross-repo-overlay-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, target)
    except BaseException:
        with suppress(OSError):
            os.unlink(name)
        raise


def _cross_repo_usage(what: str) -> click.UsageError:
    """The fixed usage text of one `CrossRepoUsageError` (never the exception's own text)."""
    texts = {
        "graph_namespace": "A repository cannot be opened as a graph namespace.",
        "repository_overlap": "The repositories and graph roots must not overlap.",
        "seed": "Invalid --seed value.",
        "direction": "Invalid --direction value.",
    }
    return click.UsageError(texts.get(what, "Invalid cross-repository limit: " + what))


@cli.command("cross-repo-link")
@click.option(
    "--mapping",
    "mapping_path",
    required=True,
    type=click.Path(path_type=Path),
    help="Mapping (JSON, atlas-cross-repo-mapping.v1): two repositories, one package",
)
@click.option(
    "--lock",
    "lock_path",
    required=True,
    type=click.Path(path_type=Path),
    help="The consumer's uv.lock; its sha256 must equal the mapping's lock_sha256",
)
@click.option(
    "--package-snapshot",
    "package_path",
    required=True,
    type=click.Path(path_type=Path),
    help="The S28 package snapshot (import-python-dependency output)",
)
@click.option(
    "--previous",
    "previous_path",
    default=None,
    type=click.Path(path_type=Path),
    help="The overlay being refreshed; the result records what it invalidated",
)
@click.option(
    "--output",
    "output_path",
    required=True,
    type=click.Path(path_type=Path),
    help="Where the overlay is written; outside both repositories and both graph roots",
)
@_cross_repo_sides
@_cross_repo_options(_CROSS_REPO_LIMITS)
def cross_repo_link(
    mapping_path: Path,
    lock_path: Path,
    package_path: Path,
    previous_path: Path | None,
    output_path: Path,
    consumer_repo: Path,
    consumer_graph_root: Path | None,
    provider_repo: Path,
    provider_graph_root: Path | None,
    **chosen: int | None,
) -> None:
    """Link a consumer's imports of one locked package to the provider's declarations.

    Both snapshots are the ACTIVE ones of their repositories (read-only leases) and must be the
    ones the mapping names; the lock, the package snapshot and the provider's indexed sources
    must agree byte for byte. A link joins an import reference to a declaration; it is not a
    call. The overlay is standalone: the graphs are not written and the only file written is
    --output. Prints the canonical `atlas-cross-repo-link-summary.v1` on stdout; nothing is
    printed on rejection (exit 1) or invalid usage (exit 2).
    """
    from atlas_aci.cross_repo import package_links as links
    from atlas_aci.cross_repo import revision_vector as vector
    from atlas_aci.overlays import runtime
    from atlas_aci.query.diff import Side

    try:
        limits = links.normalize_limits(chosen)
    except vector.CrossRepoUsageError as exc:
        raise _cross_repo_usage(exc.what) from None
    first, second = consumer_repo.resolve(), provider_repo.resolve()
    consumer = Side(first, consumer_graph_root)
    provider = Side(second, provider_graph_root)
    try:
        vector.check_roots(consumer, provider)
    except vector.CrossRepoUsageError as exc:
        raise _cross_repo_usage(exc.what) from None
    protected = [first, first / ".atlas", second, second / ".atlas"]
    protected += [root for root in (consumer_graph_root, provider_graph_root) if root is not None]
    inputs = [mapping_path, lock_path, package_path]
    inputs += [] if previous_path is None else [previous_path]
    try:
        target = runtime.resolve_output(output_path, protected, inputs)
    except runtime.OverlayUsageError as exc:
        if exc.reason == "output_equals_input":
            raise click.UsageError("--output must differ from every input file.") from None
        raise click.UsageError(
            "--output must be outside both repositories and both graph roots."
        ) from None
    mapping_raw = _read_cross_repo_input(
        mapping_path, limits.max_mapping_bytes, "Cross-repository mapping cannot be read."
    )
    try:
        # The mapping is judged before any other input is read.
        vector.parse_mapping(mapping_raw, limits.max_mapping_bytes)
        vector.check_distinct(consumer, provider)
    except vector.CrossRepoRejected as exc:
        raise click.ClickException("Cross-repository link rejected: " + exc.reason) from None
    except vector.CrossRepoUsageError as exc:
        raise _cross_repo_usage(exc.what) from None
    lock_raw = _read_cross_repo_input(
        lock_path, limits.max_lock_bytes, "uv lock file cannot be read."
    )
    package_raw = _read_cross_repo_input(
        package_path, limits.max_package_snapshot_bytes, "Package snapshot cannot be read."
    )
    previous_raw = (
        None
        if previous_path is None
        else _read_cross_repo_input(
            previous_path, limits.max_overlay_bytes, "Previous overlay cannot be read."
        )
    )
    try:
        overlay = links.link(
            mapping_raw, lock_raw, package_raw, consumer, provider, previous_raw, limits
        )
        data = links.canonical_bytes(overlay)
        summary = links.canonical_bytes(links.summary_document(overlay))
    except vector.CrossRepoRejected as exc:
        raise click.ClickException("Cross-repository link rejected: " + exc.reason) from None
    except vector.CrossRepoUsageError as exc:
        raise _cross_repo_usage(exc.what) from None
    except Exception as exc:
        # Never `str(exc)`: a message may carry a path or input text.
        raise click.ClickException("Cross-repository link failed: " + type(exc).__name__) from None
    try:
        _write_cross_repo_atomic(target, data)
    except OSError:
        raise click.ClickException("Cross-repository overlay cannot be written.") from None
    stream = click.get_binary_stream("stdout")
    stream.write(summary)
    stream.flush()


@cli.command("cross-repo-impact")
@click.option(
    "--overlay",
    "overlay_path",
    required=True,
    type=click.Path(path_type=Path),
    help="The overlay written by cross-repo-link",
)
@click.option(
    "--mapping",
    "mapping_path",
    required=True,
    type=click.Path(path_type=Path),
    help="The mapping the overlay was built from",
)
@click.option("--seed", required=True, help="Entity id (64 hex) to start from")
@click.option(
    "--direction",
    required=True,
    type=click.Choice(["from_provider", "to_provider"]),
    help="from_provider: consumers of a provider entity; to_provider: providers of a consumer file",
)
@_cross_repo_sides
@_cross_repo_options(_CROSS_REPO_IMPACT_LIMITS)
def cross_repo_impact(
    overlay_path: Path,
    mapping_path: Path,
    seed: str,
    direction: str,
    consumer_repo: Path,
    consumer_graph_root: Path | None,
    provider_repo: Path,
    provider_graph_root: Path | None,
    **chosen: int | None,
) -> None:
    """Depth-one potential impact across the mapped revision vector; writes nothing.

    Seeds are file entities for module-level imports: a consumer function entity returns nothing.
    A provider snapshot that is not the mapped, active one yields a `missing_provider_snapshot`
    boundary. Prints the canonical `atlas-cross-repo-impact.v1` on stdout; nothing is printed on
    rejection (exit 1) or invalid usage (exit 2).
    """
    from atlas_aci.cross_repo import package_links as links
    from atlas_aci.cross_repo import revision_vector as vector
    from atlas_aci.query.diff import Side
    from atlas_aci.storage import evidence as evidence

    try:
        limits = links.normalize_limits(
            {name: value for name, value in chosen.items() if name in links.LIMITS}
        )
        budgets = links.normalize_budgets(
            {name: value for name, value in chosen.items() if name in links.BUDGETS}
        )
    except vector.CrossRepoUsageError as exc:
        raise _cross_repo_usage(exc.what) from None
    if not evidence.hash_value(seed):
        raise _cross_repo_usage("seed")
    consumer = Side(consumer_repo.resolve(), consumer_graph_root)
    provider = Side(provider_repo.resolve(), provider_graph_root)
    try:
        vector.check_roots(consumer, provider)
    except vector.CrossRepoUsageError as exc:
        raise _cross_repo_usage(exc.what) from None
    overlay_raw = _read_cross_repo_input(
        overlay_path, limits.max_overlay_bytes, "Cross-repository overlay cannot be read."
    )
    mapping_raw = _read_cross_repo_input(
        mapping_path, limits.max_mapping_bytes, "Cross-repository mapping cannot be read."
    )
    try:
        document = links.impact(
            overlay_raw, mapping_raw, consumer, provider, seed, direction, budgets, limits
        )
        data = links.canonical_bytes(document)
    except vector.CrossRepoRejected as exc:
        raise click.ClickException("Cross-repository impact rejected: " + exc.reason) from None
    except vector.CrossRepoUsageError as exc:
        raise _cross_repo_usage(exc.what) from None
    except Exception as exc:
        # Never `str(exc)`: a message may carry a path or input text.
        raise click.ClickException(
            "Cross-repository impact failed: " + type(exc).__name__
        ) from None
    stream = click.get_binary_stream("stdout")
    stream.write(data)
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


def _write_history_atomic(target: Path, data: bytes) -> None:
    """Write beside `target` (same directory) and `os.replace` it; no partial output remains."""
    import tempfile
    from contextlib import suppress

    descriptor, name = tempfile.mkstemp(prefix=".atlas-history-overlay-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, target)
    except BaseException:
        with suppress(OSError):
            os.unlink(name)
        raise


# (name, minimum, maximum, default); the library owns the same table and a test pins the two.
_HISTORY_LIMITS = (
    ("max_commits", 1, 20000, 1000),
    ("max_pairs", 1, 1000000, 100000),
    ("max_pair_observations", 1, 10000000, 1000000),
    ("max_overlay_bytes", 1, 268435456, 67108864),
    ("max_tree_bytes", 1, 268435456, 67108864),
    ("git_timeout", 1, 600, 30),
    ("git_deadline", 1, 86400, 600),
)


def _history_limit_options(command):
    for name, low, high, default in reversed(_HISTORY_LIMITS):
        command = click.option(
            "--" + name.replace("_", "-"),
            name,
            default=None,
            type=int,
            help=f"Limit {low}..{high} (default {default})",
        )(command)
    return command


@cli.command("cochange")
@click.option(
    "--repo",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="The git repository (its top-level directory)",
)
@click.option("--repository-id", "repository_id", required=True, help="Operator label")
@click.option("--start", required=True, help="Window start commit, excluded (40 hex)")
@click.option("--end", required=True, help="Window end commit, included (40 hex)")
@click.option(
    "--source-commit",
    "source_commit",
    required=True,
    help="The commit the indexed snapshot was taken from (40 hex)",
)
@click.option("--snapshot-id", "snapshot_id", required=True, help="The active snapshot (64 hex)")
@_graph_root_option("--graph-root", "External graph root (default: <repo>/.atlas)")
@click.option(
    "--output",
    "output_path",
    required=True,
    type=click.Path(dir_okay=False, path_type=Path),
    help="Where the overlay is written; outside the repository and the graph root",
)
@click.option("--bulk-threshold", "bulk_threshold", default=None, type=int)
@click.option("--diff-output-cap", "diff_output_cap", default=None, type=int)
@click.option("--generated-prefix", "generated_prefix", multiple=True, help="Adds to the defaults")
@click.option("--generated-name", "generated_name", multiple=True, help="Adds to the defaults")
@click.option("--generated-suffix", "generated_suffix", multiple=True, help="Adds to the defaults")
@click.option(
    "--no-generated-defaults",
    "no_generated_defaults",
    is_flag=True,
    help="Start every generated-path list empty",
)
@_history_limit_options
def cochange(output_path: Path, **chosen: Any) -> None:
    """Compute file-pair co-change correlations of one commit window (start..end).

    A signal says two paths changed in the same eligible commit; it is a correlation, not a
    dependency and not causality. The ACTIVE snapshot named by --snapshot-id is leased read-only
    and bound to --source-commit by git blob ids; --end must be that commit or an ancestor.
    The repository and the graph are never written; the only file written is --output.
    Prints the canonical `atlas-history-summary.v1` on stdout; nothing is printed on rejection
    (exit 1) or invalid usage (exit 2). Incomplete history is declared, with records withheld.
    """
    from atlas_aci.history import cochange as history
    from atlas_aci.overlays import runtime
    from atlas_aci.query.diff import DiffUsageError

    raw = {
        key: (list(value) if isinstance(value, tuple) else value)
        for key, value in chosen.items()
        if value is not None
    }
    try:
        options = history.normalize_options(raw)
    except history.HistoryUsageError as exc:
        raise click.UsageError("Invalid co-change option: " + exc.what) from None
    protected = [options.repo, options.repo / ".atlas"]
    if options.graph_root is not None:
        protected.append(options.graph_root)
    try:
        target = runtime.resolve_output(output_path, protected, [])
    except runtime.OverlayUsageError:
        raise click.UsageError("--output must be outside --repo and the graph root.") from None
    try:
        overlay = history.cochange(options)
        data = history.canonical_bytes(overlay)
        summary = history.canonical_bytes(history.summary_document(overlay))
    except history.HistoryRejected as exc:
        raise click.ClickException("History co-change rejected: " + exc.reason) from None
    except history.HistoryUsageError as exc:
        raise click.UsageError("Invalid co-change option: " + exc.what) from None
    except DiffUsageError:
        raise click.UsageError("The repository cannot be opened as a graph namespace.") from None
    except Exception as exc:
        # Never `str(exc)`: a message may carry a path.
        raise click.ClickException("History co-change failed: " + type(exc).__name__) from None
    try:
        _write_history_atomic(target, data)
    except OSError:
        raise click.ClickException("History overlay cannot be written.") from None
    stream = click.get_binary_stream("stdout")
    stream.write(summary)
    stream.flush()


def _write_rationale_atomic(target: Path, data: bytes) -> None:
    """Write beside `target` (same directory) and `os.replace` it; no partial output remains."""
    import tempfile
    from contextlib import suppress

    descriptor, name = tempfile.mkstemp(prefix=".atlas-rationale-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, target)
    except BaseException:
        with suppress(OSError):
            os.unlink(name)
        raise


# (name, minimum, maximum, default); the library owns the same table and a test pins the two.
_RATIONALE_LIMITS = (
    ("max_mapping_bytes", 1, 16777216, 1048576),
    ("max_mappings", 1, 10000, 1000),
    ("max_output_bytes", 1, 67108864, 16777216),
)


def _rationale_limit_options(command):
    for name, low, high, default in reversed(_RATIONALE_LIMITS):
        command = click.option(
            "--" + name.replace("_", "-"),
            name,
            default=None,
            type=int,
            help=f"Limit {low}..{high} (default {default})",
        )(command)
    return command


def _rationale_usage(what: str) -> click.UsageError:
    """The fixed usage text of one `RationaleUsageError` (never the exception's own text)."""
    texts = {
        "repository": "Give --repo, or both --before-repo and --after-repo.",
        "output_location": "--output must be outside the repositories and graph roots.",
        "output_equals_input": "--output must differ from the mapping.",
        "graph_namespace": "The repository cannot be opened as a graph namespace.",
        "same_namespace": "Both sides resolve to the same graph namespace.",
    }
    return click.UsageError(texts.get(what, "Invalid rationale option: " + what))


def _rationale_execute(
    command: str,
    mapping_path: Path,
    output_path: Path,
    sides: list[tuple[Path | None, Path | None]],
    chosen: dict[str, int | None],
) -> None:
    from atlas_aci.overlays import runtime
    from atlas_aci.query.diff import Side
    from atlas_aci.rationale import adr_mapping as mapping
    from atlas_aci.rationale import drift

    mapped = command == "rationale-map"
    label = "mapping" if mapped else "drift"
    try:
        limits = mapping.normalize_limits(chosen)
        if any(repo is None for repo, _ in sides):
            raise mapping.RationaleUsageError("repository")
    except mapping.RationaleUsageError as exc:
        raise _rationale_usage(exc.what) from None
    checked = [(Path(str(repo)).resolve(), root) for repo, root in sides]
    protected = [item for repo, _ in checked for item in (repo, repo / ".atlas")]
    protected += [root for _, root in checked if root is not None]
    try:
        target = runtime.resolve_output(output_path, protected, [mapping_path])
    except runtime.OverlayUsageError as exc:
        what = "output_equals_input" if exc.reason == "output_equals_input" else "output_location"
        raise _rationale_usage(what) from None
    raw = _read_cross_repo_input(
        mapping_path, limits.max_mapping_bytes, "Rationale mapping cannot be read."
    )
    try:
        if mapped:
            document = mapping.map_rationale(raw, Side(*checked[0]), limits)
        else:
            document = drift.rationale_drift(raw, Side(*checked[0]), Side(*checked[1]), limits)
        data = mapping.canonical_bytes(document)
        summary_data = mapping.summary_document(document)
        mapping.validate_summary(summary_data)
        summary = mapping.canonical_bytes(summary_data)
    except mapping.RationaleRejected as exc:
        raise click.ClickException(f"Rationale {label} rejected: " + exc.reason) from None
    except mapping.RationaleUsageError as exc:
        raise _rationale_usage(exc.what) from None
    except Exception as exc:
        # Never `str(exc)`: a message may carry a path or input text.
        raise click.ClickException(f"Rationale {label} failed: " + type(exc).__name__) from None
    try:
        _write_rationale_atomic(target, data)
    except OSError:
        raise click.ClickException("Rationale output cannot be written.") from None
    stream = click.get_binary_stream("stdout")
    stream.write(summary)
    stream.flush()


_RATIONALE_MAPPING_OPTION = click.option(
    "--mapping",
    "mapping_path",
    required=True,
    type=click.Path(path_type=Path),
    help="Mapping (JSON, atlas-rationale-mapping.v1): ADR spans to exact source endpoints",
)
_RATIONALE_OUTPUT_OPTION = click.option(
    "--output",
    "output_path",
    required=True,
    type=click.Path(dir_okay=False, path_type=Path),
    help="Where the document is written; outside the repositories and graph roots",
)


@cli.command("rationale-map")
@_RATIONALE_MAPPING_OPTION
@click.option(
    "--repo",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="The repository whose active snapshot the mapping names",
)
@_graph_root_option("--graph-root", "External graph root (default: <repo>/.atlas)")
@_RATIONALE_OUTPUT_OPTION
@_rationale_limit_options
def rationale_map(
    mapping_path: Path,
    repo: Path,
    graph_root: Path | None,
    output_path: Path,
    **chosen: int | None,
) -> None:
    """Resolve explicit ADR-span-to-source mappings on the mapped snapshot.

    The mapping names, by hand, a span of a decision document (an ADR) and an exact source
    endpoint. Document bytes come from the snapshot, never from the checkout. A mapping that
    does not resolve is reported as unresolved and is never rebound. The graph, the repository
    and the documents are not written; the only file written is --output. Prints the canonical
    `atlas-rationale-summary.v1` on stdout; nothing is printed on rejection (exit 1) or invalid
    usage (exit 2).
    """
    _rationale_execute(
        "rationale-map", mapping_path, output_path, [(repo, graph_root)], dict(chosen)
    )


@cli.command("rationale-drift")
@_RATIONALE_MAPPING_OPTION
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
    help="Repository of the before (mapped) snapshot",
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
@_graph_root_option("--before-graph-root", "Graph root of the before (mapped) snapshot")
@_graph_root_option("--after-graph-root", "Graph root of the after snapshot")
@_RATIONALE_OUTPUT_OPTION
@_rationale_limit_options
def rationale_drift(
    mapping_path: Path,
    repo: Path | None,
    before_repo: Path | None,
    after_repo: Path | None,
    graph_root: Path | None,
    before_graph_root: Path | None,
    after_graph_root: Path | None,
    output_path: Path,
    **chosen: int | None,
) -> None:
    """Flag mapped source that changed between two snapshots while its ADR did not.

    Compares the mapped (before) snapshot with the after snapshot through the snapshot diff.
    A source change with an identical decision document is reported as rationale_review_needed:
    an observation, not a verdict. Uncomparable targets are unknown_target_history and missing
    endpoints stay unresolved. The graphs, the repositories and the documents are not written;
    the only file written is --output. Prints the canonical `atlas-rationale-summary.v1` on
    stdout; nothing is printed on rejection (exit 1) or invalid usage (exit 2).
    """
    _rationale_execute(
        "rationale-drift",
        mapping_path,
        output_path,
        [
            (before_repo or repo, before_graph_root or graph_root),
            (after_repo or repo, after_graph_root or graph_root),
        ],
        dict(chosen),
    )


def _write_feedback_atomic(target: Path, data: bytes) -> None:
    """Write beside `target` (same directory) and `os.replace` it; no partial output remains."""
    import tempfile
    from contextlib import suppress

    descriptor, name = tempfile.mkstemp(prefix=".atlas-feedback-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, target)
    except BaseException:
        with suppress(OSError):
            os.unlink(name)
        raise


# (name, minimum, maximum, default); the library owns the same table and a test pins the two.
_FEEDBACK_LIMITS = (
    ("max_envelope_bytes", 1, 16777216, 1048576),
    ("max_record_bytes", 1, 67108864, 16777216),
    ("max_artifact_bytes", 1, 67108864, 8388608),
    ("max_store_bytes", 1, 268435456, 67108864),
    ("max_claims", 1, 10000, 1000),
    ("max_testcases", 1, 1000000, 100000),
    ("max_events", 1, 100000, 10000),
    ("max_request_bytes", 1, 1048576, 65536),
    ("max_output_bytes", 1, 268435456, 67108864),
)


def _feedback_limit_options(names: tuple[str, ...]):
    table = {row[0]: row for row in _FEEDBACK_LIMITS}

    def decorate(command):
        for name in reversed(names):
            _, low, high, default = table[name]
            command = click.option(
                "--" + name.replace("_", "-"),
                name,
                default=None,
                type=int,
                help=f"Limit {low}..{high} (default {default})",
            )(command)
        return command

    return decorate


def _feedback_usage(what: str) -> click.UsageError:
    """The fixed usage text of one `FeedbackUsageError` (never the exception's own text)."""
    texts = {
        "output_location": "--output must be outside the repository and graph root.",
        "output_equals_input": "--output must differ from every input.",
        "graph_namespace": "The repository cannot be opened as a graph namespace.",
        "store_without_overlay": "--store requires --overlay.",
        "overlay_without_store": "--overlay requires --store.",
        "unsupported_policy": "Unsupported ranking policy.",
    }
    return click.UsageError(texts.get(what, "Invalid feedback option: " + what))


def _read_feedback_input(path: Path | None, limit: int, role: str) -> bytes | None:
    if path is None:
        return None
    return _read_cross_repo_input(path, limit, "Feedback input cannot be read: " + role)


@cli.command("feedback-import")
@click.option(
    "--repo",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="The repository whose active snapshot the feedback names",
)
@_graph_root_option("--graph-root", "External graph root (default: <repo>/.atlas)")
@click.option(
    "--envelope",
    "envelope_path",
    required=True,
    type=click.Path(path_type=Path),
    help="Feedback envelope (JSON, atlas-feedback-envelope.v1)",
)
@click.option(
    "--retrieval-record",
    "record_path",
    required=True,
    type=click.Path(path_type=Path),
    help="The saved context result (structuredContent) the envelope names",
)
@click.option(
    "--artifact",
    "artifact_path",
    default=None,
    type=click.Path(path_type=Path),
    help="The pytest JUnit XML artifact; required when the envelope has claims",
)
@click.option(
    "--store",
    "store_path",
    default=None,
    type=click.Path(path_type=Path),
    help="A previous atlas-feedback-events.v1 store to merge into",
)
@click.option(
    "--output",
    "output_path",
    required=True,
    type=click.Path(dir_okay=False, path_type=Path),
    help="Where the store is written; outside the repository and graph root",
)
@_feedback_limit_options(
    (
        "max_envelope_bytes",
        "max_record_bytes",
        "max_artifact_bytes",
        "max_store_bytes",
        "max_claims",
        "max_testcases",
        "max_events",
        "max_output_bytes",
    )
)
def feedback_import(
    repo: Path,
    graph_root: Path | None,
    envelope_path: Path,
    record_path: Path,
    artifact_path: Path | None,
    store_path: Path | None,
    output_path: Path,
    **chosen: int | None,
) -> None:
    """Record test outcomes a JUnit artifact bears out as verified feedback events.

    Each claimed outcome is checked against the artifact (digest, size, run identity), the saved
    retrieval record and the leased snapshot; a claim that is not borne out rejects the whole
    import (exit 1, empty stdout, nothing written). Annotations are stored as unverified and are
    never scored. The graph and the repository are not written; the only file written is
    --output. Prints the canonical `atlas-feedback-import-summary.v1` on stdout.
    """
    from atlas_aci.feedback import outcomes
    from atlas_aci.overlays import runtime
    from atlas_aci.query.diff import Side

    try:
        limits = outcomes.normalize_limits(chosen)
    except outcomes.FeedbackUsageError as exc:
        raise _feedback_usage(exc.what) from None
    resolved = Path(str(repo)).resolve()
    protected = [resolved, resolved / ".atlas"]
    if graph_root is not None:
        protected.append(graph_root)
    inputs = [p for p in (envelope_path, record_path, artifact_path, store_path) if p is not None]
    try:
        target = runtime.resolve_output(output_path, protected, inputs)
    except runtime.OverlayUsageError as exc:
        what = "output_equals_input" if exc.reason == "output_equals_input" else "output_location"
        raise _feedback_usage(what) from None
    envelope_raw = _read_feedback_input(envelope_path, limits.max_envelope_bytes, "envelope")
    record_raw = _read_feedback_input(record_path, limits.max_record_bytes, "retrieval-record")
    artifact_raw = _read_feedback_input(artifact_path, limits.max_artifact_bytes, "artifact")
    store_raw = _read_feedback_input(store_path, limits.max_store_bytes, "store")
    assert envelope_raw is not None and record_raw is not None
    try:
        store, summary_data = outcomes.import_feedback(
            envelope_raw, record_raw, artifact_raw, store_raw, Side(resolved, graph_root), limits
        )
        data = outcomes.canonical_bytes(store)
        summary = outcomes.canonical_bytes(summary_data)
    except outcomes.FeedbackRejected as exc:
        raise click.ClickException("Feedback import rejected: " + str(exc)) from None
    except outcomes.FeedbackUsageError as exc:
        raise _feedback_usage(exc.what) from None
    except Exception as exc:
        # Never `str(exc)`: a message may carry a path or input text.
        raise click.ClickException("Feedback import failed: " + type(exc).__name__) from None
    try:
        _write_feedback_atomic(target, data)
    except OSError:
        raise click.ClickException("Feedback output cannot be written.") from None
    stream = click.get_binary_stream("stdout")
    stream.write(summary)
    stream.flush()


@cli.command("feedback-rank")
@click.option(
    "--repo",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="The repository whose active snapshot is queried",
)
@_graph_root_option("--graph-root", "External graph root (default: <repo>/.atlas)")
@click.option(
    "--request",
    "request_path",
    required=True,
    type=click.Path(path_type=Path),
    help="A graph_query context request (JSON): schema_version, op, text and optional fields",
)
@click.option(
    "--overlay",
    "overlay",
    default=None,
    help="Enable the overlay with this policy name (atlas-feedback-ranking-policy.v1)",
)
@click.option(
    "--store",
    "store_path",
    default=None,
    type=click.Path(path_type=Path),
    help="The atlas-feedback-events.v1 store the overlay reads; only with --overlay",
)
@_feedback_limit_options(("max_request_bytes", "max_store_bytes", "max_events", "max_output_bytes"))
def feedback_rank(
    repo: Path,
    graph_root: Path | None,
    request_path: Path,
    overlay: str | None,
    store_path: Path | None,
    **chosen: int | None,
) -> None:
    """Print the baseline context ordering, optionally adjusted by active verified feedback.

    Without --overlay the ordering is the unchanged baseline ranker's and the store is never
    read. With --overlay atlas-feedback-ranking-policy.v1 and --store, each adjustment lists
    the ids of the events that support it, and stale events are listed as excluded. Nothing is
    written; the canonical `atlas-feedback-ranking.v1` is printed on stdout, and nothing is
    printed on rejection (exit 1) or invalid usage (exit 2).
    """
    from atlas_aci.feedback import outcomes, ranking
    from atlas_aci.query.diff import Side

    try:
        limits = outcomes.normalize_limits(chosen)
        if store_path is not None and overlay is None:
            raise outcomes.FeedbackUsageError("store_without_overlay")
        if overlay is not None and store_path is None:
            raise outcomes.FeedbackUsageError("overlay_without_store")
        if overlay is not None and overlay != ranking.POLICY_NAME:
            raise outcomes.FeedbackUsageError("unsupported_policy")
    except outcomes.FeedbackUsageError as exc:
        raise _feedback_usage(exc.what) from None
    request_raw = _read_feedback_input(request_path, limits.max_request_bytes, "request")
    store_raw = _read_feedback_input(store_path, limits.max_store_bytes, "store")
    assert request_raw is not None
    try:
        document = ranking.rank_feedback(
            request_raw,
            Side(Path(str(repo)).resolve(), graph_root),
            store_raw,
            overlay is not None,
            limits,
        )
        data = outcomes.canonical_bytes(document)
    except outcomes.FeedbackRejected as exc:
        raise click.ClickException("Feedback ranking rejected: " + str(exc)) from None
    except outcomes.FeedbackUsageError as exc:
        raise _feedback_usage(exc.what) from None
    except Exception as exc:
        raise click.ClickException("Feedback ranking failed: " + type(exc).__name__) from None
    stream = click.get_binary_stream("stdout")
    stream.write(data)
    stream.flush()


# ---------------------------------------------------------------------- jobs (ACI-S41)
def _jobs_options(function: Callable[..., Any]) -> Callable[..., Any]:
    function = click.option(
        "--graph-root",
        default=None,
        type=click.Path(file_okay=False, path_type=Path),
        help="External graph storage root (default: <repo>/.atlas)",
    )(function)
    return click.option(
        "--repo", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path)
    )(function)


def _jobs_now(function: Callable[..., Any]) -> Callable[..., Any]:
    return click.option(
        "--now",
        default=None,
        type=click.IntRange(min=0),
        help="Unix seconds used for capture times and retention age (default: the clock)",
    )(function)


def _jobs_namespace(repo: Path, graph_root: Path | None) -> Any:
    from atlas_aci.storage import generations as generation_store

    try:
        return generation_store.namespace(repo.resolve(), graph_root)
    except (ValueError, OSError):
        raise click.UsageError("Invalid repository or graph root") from None


def _jobs_id(value: str, label: str, noun: str) -> str:
    import re

    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise click.UsageError(f"{label}: must be a 64-character lowercase hex {noun} id")
    return value


def _jobs_emit(value: dict[str, Any]) -> None:
    click.echo(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


def _jobs_fail(exc: BaseException, *, generation: str | None = None) -> None:
    from atlas_aci.indexing import jobs as job_api

    error = job_api.error_for(exc)
    if generation is not None and error.generation_id is None:
        error.generation_id = generation
    _jobs_emit(error.envelope())
    click.echo(f"jobs: {error.code}", err=True)
    sys.exit(job_api.exit_code(error.code))


def _jobs_guard(call: Callable[[], Any], *, generation: str | None = None) -> Any:
    """Run ``call``; map expected failures to the typed error envelope and the documented exit."""
    from atlas_aci.enforcement import ToolError
    from atlas_aci.indexing import job_runner
    from atlas_aci.indexing import jobs as job_api
    from atlas_aci.storage import generations as generation_store

    try:
        return call()
    except job_runner.FaultSpecError as exc:
        raise click.UsageError(str(exc)) from None
    except generation_store.PolicyError as exc:
        raise click.UsageError("retention.json: " + str(exc)) from None
    except (job_api.JobError, generation_store.StoreError, ToolError, OSError) as exc:
        _jobs_fail(exc, generation=generation)
    return None


@cli.group("jobs")
def jobs_group() -> None:
    """Operator index jobs, retained generations, reader leases and garbage collection."""


@jobs_group.command("submit")
@_jobs_options
@click.option(
    "--langs", default=",".join(DEFAULT_LANGS), show_default=True, help="Comma-separated languages"
)
@click.option(
    "--frameworks",
    default="",
    help="Comma-separated opt-in framework adapters; closed set: "
    + ", ".join(sorted(FRAMEWORK_ADAPTERS)),
)
@click.option(
    "--rails-table-map",
    default=None,
    type=click.Path(path_type=Path),
    help="Operator model-to-table map (JSON); needs the rails_models framework adapter",
)
@_jobs_now
def jobs_submit(
    repo: Path,
    graph_root: Path | None,
    langs: str,
    frameworks: str,
    rails_table_map: Path | None,
    now: int | None,
) -> None:
    """Run (or reuse) an index job; prints the canonical result envelope."""
    import time

    lang_list = [s.strip() for s in langs.split(",") if s.strip()]
    try:
        adapters = normalize_frameworks(frameworks, lang_list or DEFAULT_LANGS)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc
    table_map: bytes | None = None
    if rails_table_map is not None:
        if "rails_models" not in adapters:
            raise click.UsageError("--rails-table-map requires --frameworks rails_models")
        table_map = _read_table_map(rails_table_map)
        try:
            rails_models.parse_table_map(table_map)
        except ValueError as exc:
            raise click.UsageError(str(exc)) from None
    from atlas_aci.indexing import job_runner
    from atlas_aci.indexing import jobs as job_api

    ns = _jobs_namespace(repo, graph_root)
    spec = job_api.JobSpec(ns, tuple(sorted(set(lang_list))), adapters, table_map)
    stamp = int(time.time()) if now is None else now
    result = _jobs_guard(lambda: job_runner.run_from_cli("submit", ns, now=stamp, spec=spec))
    _jobs_emit(result)


@jobs_group.command("resume")
@_jobs_options
@click.option("--job", "job", required=True, help="64-character lowercase hex job id")
@_jobs_now
def jobs_resume(repo: Path, graph_root: Path | None, job: str, now: int | None) -> None:
    """Continue an interrupted job from its last valid checkpoint."""
    import time

    _jobs_id(job, "--job", "job")
    from atlas_aci.indexing import job_runner

    ns = _jobs_namespace(repo, graph_root)
    stamp = int(time.time()) if now is None else now
    result = _jobs_guard(lambda: job_runner.run_from_cli("resume", ns, now=stamp, job=job))
    _jobs_emit(result)


@jobs_group.command("cancel")
@_jobs_options
@click.option("--job", "job", required=True, help="64-character lowercase hex job id")
def jobs_cancel(repo: Path, graph_root: Path | None, job: str) -> None:
    """Request cancellation (honoured only before the commit); prints the outcome."""
    _jobs_id(job, "--job", "job")
    from atlas_aci.indexing import jobs as job_api

    ns = _jobs_namespace(repo, graph_root)
    _jobs_emit(_jobs_guard(lambda: job_api.cancel(ns, job)))


@jobs_group.command("status")
@_jobs_options
def jobs_status(repo: Path, graph_root: Path | None) -> None:
    """Print canonical job, lease and retention metrics (read-only)."""
    from atlas_aci.indexing import jobs as job_api

    ns = _jobs_namespace(repo, graph_root)
    _jobs_emit(_jobs_guard(lambda: job_api.status(ns)))


@jobs_group.command("gc")
@_jobs_options
@_jobs_now
@click.option(
    "--wait-seconds",
    default=30.0,
    show_default=True,
    type=click.FloatRange(min=0),
    help="How long to wait for the writer lock held by a running build before WRITER_BUSY",
)
def jobs_gc(repo: Path, graph_root: Path | None, now: int | None, wait_seconds: float) -> None:
    """Retire unleased generations by policy; prints the garbage-collection receipt."""
    import time

    from atlas_aci.storage import generations as generation_store
    from atlas_aci.storage import retention

    ns = _jobs_namespace(repo, graph_root)
    stamp = int(time.time()) if now is None else now

    def collect() -> dict[str, Any]:
        policy = generation_store.load_policy(ns)
        return retention.gc(ns, policy=policy, now=stamp, wait_seconds=wait_seconds)

    _jobs_emit(_jobs_guard(collect))


@jobs_group.command("acquire")
@_jobs_options
@click.option("--generation", "generation", required=True, help="64-character hex generation id")
@click.option("--hold", is_flag=True, help="Keep the lease until stdin closes or SIGTERM")
def jobs_acquire(repo: Path, graph_root: Path | None, generation: str, hold: bool) -> None:
    """Take a reader lease on a retained generation; prints it (read-only on disk)."""
    _jobs_id(generation, "--generation", "generation")
    from atlas_aci.indexing import job_runner
    from atlas_aci.storage import leases

    ns = _jobs_namespace(repo, graph_root)
    lease = _jobs_guard(lambda: leases.acquire(ns, generation), generation=generation)
    try:
        _jobs_emit(
            {
                "format": "atlas-aci-lease.v1",
                "generation_id": lease.generation_id,
                "snapshot_id": lease.snapshot_id,
                "origin": lease.origin,
            }
        )
        if hold:
            sys.stdout.flush()
            job_runner.hold_lease()
    finally:
        lease.close()


if __name__ == "__main__":
    cli()
