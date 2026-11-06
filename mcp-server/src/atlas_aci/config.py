"""Configuration for the ATLAS ACI server."""

from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import structlog

log = structlog.get_logger()

# ACI-S39 HTTP transport bounds (see Config._validate_http).
HTTP_HOST_MAX_CHARS: int = 253
HTTP_MAX_BUFFERED_BYTES: int = 256 * 1024 * 1024
HTTP_ORIGIN_PATTERN = re.compile(r"https?://([a-z0-9.-]+|\[[0-9a-f:]+\])(:[1-9][0-9]{0,4})?")

# The default for Config.max_bound_field_elements below. Named at module
# level (not just a dataclass default) so codegraph.py can derive its own
# internal SQL fetch-limit default from the *same* number — see
# CodeGraph.__init__'s `query_limit` — instead of an independent magic
# constant that could silently drift out of sync with it (F-1: this is
# exactly the class of bug a second hardcoded 200 caused).
DEFAULT_MAX_BOUND_FIELD_ELEMENTS: int = 200

# The default for Config.max_response_bytes; named so the offline `impact` CLI and the
# server share one number (an impact envelope must be identical on both surfaces).
DEFAULT_MAX_RESPONSE_BYTES: int = 1024 * 1024

# Hardcoded skip list — overridable in instance config.
DEFAULT_SKIP_PATTERNS: tuple[str, ...] = (
    "node_modules",
    "vendor/bundle",
    "vendor/cache",
    "tmp",
    "log",
    ".git",
    "dist",
    "build",
    "public/assets",
    "public/packs",
    "public/packs-test",
    "coverage",
    ".bundle",
    "__pycache__",
    ".venv",
    ".atlas",  # our own index dir
    "storage",
    # Static-site generator build output / caches — generated, not source.
    "_site",  # Jekyll build output
    ".jekyll-cache",
    ".sass-cache",
)


def path_is_within(repo: Path, p: Path) -> bool:
    """The core path-containment check (path-traversal guard): resolves
    symlinks and normalizes any `..`/`.` components, then checks the
    result stays under `repo`. Factored out of `Config.is_in_repo` so
    there is exactly ONE implementation of "is this path contained" —
    shared by the MCP tool-facing guard (`Config.is_in_repo` /
    `enforcement.assert_path_in_repo`) and `CodeGraph.import_jsonl`'s
    imported-path validation (AC-A5-2 / checker MAJOR-1, the nineteenth
    defect: `content_hash` proves byte integrity, never path semantics —
    an imported `path`/`target_path`/candidate `path` needs the SAME
    containment guarantee `README.md` already promises for tool calls,
    reused rather than reimplemented a second time to silently drift).
    """
    try:
        resolved = (repo / p).resolve() if not p.is_absolute() else p.resolve()
        resolved.relative_to(repo)
    except (ValueError, OSError):
        return False
    return True


@dataclass
class Config:
    """Server-wide configuration. One instance per server process."""

    repo: Path
    memex_root: Path

    # Bounds — these are the mechanical guarantees the agent sees.
    max_lines_per_view: int = 100
    max_entries_per_list: int = 200
    max_matches_per_search: int = 50
    max_bytes_per_call: int = 8 * 1024
    test_dry_run_timeout_s: int = 30
    # Snapshot explicit operator consent once per server. Request arguments and
    # later environment changes cannot grant execution to this Config instance.
    test_execution_enabled: bool = field(
        default_factory=lambda: os.environ.get("ATLAS_ACI_ENABLE_TEST_EXECUTION") == "1"
    )

    # The D2 central-bounds-chokepoint backstop (server.py `_call_tool`):
    # every tool/verb's declared `_bounded_field` is truncated to this many
    # elements before the universal byte ceiling is checked. Distinct from
    # the tool-specific caps above — this is the floor that makes "a tool
    # forgot to cap its list field" (search_symbol, graph_query) structurally
    # impossible, regardless of what the tool itself does.
    max_bound_field_elements: int = DEFAULT_MAX_BOUND_FIELD_ELEMENTS

    # The absolute serialized-byte ceiling (AC-H-6) — deliberately a
    # *separate*, larger number than `max_bytes_per_call` above. Several
    # tools already legitimately combine more than one `max_bytes_per_call`
    # chunk in a single response (e.g. test_dry_run's stdout *and* stderr are
    # each independently capped at `max_bytes_per_call`, so a normal
    # both-near-cap response is ~2x that). Reusing `max_bytes_per_call` here
    # would hard-fail those *normal* responses instead of reserving hard-fail
    # for a genuinely degenerate one, as AC-H-6 requires. This is the true
    # backstop: comfortably above any well-behaved tool's worst case, so it
    # only fires when element-capping couldn't rescue the response.
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES

    # Process-owned graph references; no environment/CLI precedence or durable service.
    max_graph_leases: int = 4
    graph_lease_seconds: int = 300
    max_lease_state_bytes: int = 16 * 1024 * 1024
    max_total_lease_state_bytes: int = 64 * 1024 * 1024
    max_retained_pack_bytes: int = 512 * 1024 * 1024
    max_lease_pages: int = 256
    max_lease_resources: int = 4096
    lease_setup_ms: int = 2000

    # Explicit trusted local tokenizer object; never loaded from requests/environment.
    context_tokenizer: object | None = None

    # Rate limiting (set to 0 to disable)
    max_calls_per_minute: int = 200

    skip_patterns: tuple[str, ...] = field(default_factory=lambda: DEFAULT_SKIP_PATTERNS)

    # Explicit graph storage override; independent of the writable memex.
    graph_root: Path | None = None

    # ACI-S20 `graph_query op=impact`: the BEFORE snapshot is operator configuration,
    # never a request argument (enforcement has no path rule for graph_query, and a
    # request-supplied path would expose arbitrary repositories). Both None = impact is
    # unconfigured. `before_repo` alone selects that repository's default namespace.
    before_repo: Path | None = None
    before_graph_root: Path | None = None
    # Operator bounds on the unmetered change-set phase (entities / occurrences per side,
    # distinct files parsed). Measured defaults: see atlas/docs/change-impact.md.
    impact_max_diff_entities: int = 50_000
    impact_max_diff_occurrences: int = 250_000
    impact_max_changed_files: int = 200

    # ACI-S39 loopback Streamable HTTP transport (`serve --transport http`). Unused by stdio.
    # Validated here without DNS; hostnames are resolved (loopback-only) by protocol/http.py.
    http_host: str = "127.0.0.1"
    http_port: int = 8765
    http_max_inflight: int = 8
    http_max_body_bytes: int = 1024 * 1024
    http_allowed_origins: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.context_tokenizer is not None:
            from atlas_aci.query.context_tokens import declaration

            declaration(self.context_tokenizer)
        for name in (
            "max_graph_leases",
            "graph_lease_seconds",
            "max_lease_state_bytes",
            "max_total_lease_state_bytes",
            "max_retained_pack_bytes",
            "max_lease_pages",
            "max_lease_resources",
            "lease_setup_ms",
        ):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError("Retention policy must be finite positive integers: " + name)
        for name in (
            "impact_max_diff_entities",
            "impact_max_diff_occurrences",
            "impact_max_changed_files",
        ):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ValueError("Impact diff limits must be finite nonnegative integers: " + name)
        self._validate_http()
        self.graph_root = self.graph_root.resolve() if self.graph_root is not None else None
        self.before_repo = self.before_repo.resolve() if self.before_repo is not None else None
        self.before_graph_root = (
            self.before_graph_root.resolve() if self.before_graph_root is not None else None
        )
        self.repo = self.repo.resolve()
        self.memex_root = self.memex_root.resolve()
        # Best-effort only (AC-H-16 finding): `serve`'s default --memex-root
        # is `<repo>/.atlas/memex` — INSIDE the repo. Under a real
        # `--read-only`/`:ro` mount without a separately-mounted writable
        # memex volume (the documented deployment passes one explicitly via
        # the Dockerfile's baked `--memex-root /memex`; an operator who
        # forgets that flag does not), an unconditional mkdir crashes the
        # entire server at startup — discovered by the AC-H-16 Docker smoke
        # test, not by the mode=ro chmod simulation, which never exercises
        # this code path. `Memex.__init__` is the actual owner of creating
        # this directory (and is equally best-effort); Config no longer
        # assumes write access on the agent's behalf. A missing/unwritable
        # memex_root degrades gracefully: `memex_read` returns NOT_FOUND for
        # any ref (nothing was ever written there anyway — no ATLAS tool
        # currently emits a memex ref), while the other six tools are
        # entirely unaffected.
        try:
            self.memex_root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log.warning(
                "memex_root_not_writable",
                memex_root=str(self.memex_root),
                error=str(e),
            )

    def _validate_http(self) -> None:
        """Bounds for the HTTP transport fields (fixed messages naming the field; no DNS)."""
        host = self.http_host
        if type(host) is not str or not 1 <= len(host) <= HTTP_HOST_MAX_CHARS:
            raise ValueError("http_host must be a string of 1 to 253 characters")
        if "%" in host or any(ch.isspace() for ch in host):
            raise ValueError("http_host must not contain whitespace or a scope id")
        literal = host[1:-1] if host.startswith("[") and host.endswith("]") else host
        try:
            address = ipaddress.ip_address(literal)
        except ValueError:
            pass  # a hostname: resolved by protocol/http.py (loopback-only), never here
        else:
            if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
                address = address.ipv4_mapped
            if not address.is_loopback:
                raise ValueError("http_host must be a loopback address or a hostname")
        port = self.http_port
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("http_port must be an integer from 0 to 65535")
        if 1 <= port <= 1023:
            raise ValueError("http_port must be 0 or at least 1024 (clients must send Host:port)")
        inflight = self.http_max_inflight
        if type(inflight) is not int or not 1 <= inflight <= 256:
            raise ValueError("http_max_inflight must be an integer from 1 to 256")
        size = self.http_max_body_bytes
        if type(size) is not int or not 1024 <= size <= 16 * 1024 * 1024:
            raise ValueError("http_max_body_bytes must be an integer from 1024 to 16777216")
        if inflight * size > HTTP_MAX_BUFFERED_BYTES:
            raise ValueError(
                "http_max_inflight times http_max_body_bytes must not exceed 268435456 bytes"
            )
        origins = self.http_allowed_origins
        if isinstance(origins, list):
            origins = self.http_allowed_origins = tuple(origins)
        if type(origins) is not tuple or len(origins) > 32 or len(set(origins)) != len(origins):
            raise ValueError("http_allowed_origins must be at most 32 unique origins")
        for origin in origins:
            match = HTTP_ORIGIN_PATTERN.fullmatch(origin) if type(origin) is str else None
            if match is None or (match.group(2) and int(match.group(2)[1:]) > 65535):
                raise ValueError("http_allowed_origins entries must be exact scheme://host[:port]")

    def is_in_repo(self, p: Path) -> bool:
        """Path-traversal guard. Resolves symlinks; rejects anything outside the repo."""
        return path_is_within(self.repo, p)

    def should_skip(self, p: Path) -> bool:
        """Hardcoded ignore: should we exclude this path from listings/searches?"""
        rel = p.relative_to(self.repo) if p.is_absolute() else p
        parts = rel.parts
        return any(
            any(part == pat or part.startswith(pat + "/") for pat in self.skip_patterns)
            for part in parts
        )
