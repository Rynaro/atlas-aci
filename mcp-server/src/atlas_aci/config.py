"""Configuration for the ATLAS ACI server."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

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
