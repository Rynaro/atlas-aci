"""Configuration for the ATLAS ACI server."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# The default for Config.max_bound_field_elements below. Named at module
# level (not just a dataclass default) so codegraph.py can derive its own
# internal SQL fetch-limit default from the *same* number — see
# CodeGraph.__init__'s `query_limit` — instead of an independent magic
# constant that could silently drift out of sync with it (F-1: this is
# exactly the class of bug a second hardcoded 200 caused).
DEFAULT_MAX_BOUND_FIELD_ELEMENTS: int = 200

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
    max_response_bytes: int = 1024 * 1024

    # Rate limiting (set to 0 to disable)
    max_calls_per_minute: int = 200

    skip_patterns: tuple[str, ...] = field(default_factory=lambda: DEFAULT_SKIP_PATTERNS)

    def __post_init__(self) -> None:
        self.repo = self.repo.resolve()
        self.memex_root = self.memex_root.resolve()
        self.memex_root.mkdir(parents=True, exist_ok=True)

    def is_in_repo(self, p: Path) -> bool:
        """Path-traversal guard. Resolves symlinks; rejects anything outside the repo."""
        try:
            resolved = (self.repo / p).resolve() if not p.is_absolute() else p.resolve()
            resolved.relative_to(self.repo)
        except (ValueError, OSError):
            return False
        return True

    def should_skip(self, p: Path) -> bool:
        """Hardcoded ignore: should we exclude this path from listings/searches?"""
        rel = p.relative_to(self.repo) if p.is_absolute() else p
        parts = rel.parts
        return any(
            any(part == pat or part.startswith(pat + "/") for pat in self.skip_patterns)
            for part in parts
        )
