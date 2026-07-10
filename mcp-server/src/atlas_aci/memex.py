"""Memex — content-addressable store for byte-exact excerpts.

The MVP backend is a hashed directory tree. Production deployments may swap
in sqlite-vec for semantic search; same public interface.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from atlas_aci.enforcement import ToolError

log = structlog.get_logger()

REF_PREFIX = "memex://excerpt/"


@dataclass
class MemexEntry:
    ref: str
    content: bytes
    metadata: dict[str, Any]


class Memex:
    """Hashed-directory Memex. Idempotent writes, byte-exact reads."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        # Best-effort (AC-H-16 finding, P0): a `serve` deployment whose
        # --memex-root has landed inside a read-only mount (the default is
        # `<repo>/.atlas/memex` — see config.py) must not crash the entire
        # server at construction time just because this directory can't be
        # created. `memex_read` degrades to NOT_FOUND for any ref if the
        # directory never exists; no ATLAS tool currently emits a memex ref,
        # so nothing is lost in the read-only-serve path this fixes.
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log.warning("memex_root_not_writable", memex_root=str(self.root), error=str(e))

    def _path_for(self, h: str) -> Path:
        # Two-level fanout to keep any one directory small
        return self.root / h[:2] / h[2:4] / h

    def write(self, content: bytes, metadata: dict[str, Any] | None = None) -> str:
        h = hashlib.sha256(content).hexdigest()
        p = self._path_for(h)
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_bytes(content)
        if metadata:
            meta_path = p.with_suffix(".meta.json")
            existing: dict[str, Any] = {}
            if meta_path.exists():
                existing = json.loads(meta_path.read_text())
            existing.update(metadata)
            meta_path.write_text(json.dumps(existing, indent=2))
        return f"{REF_PREFIX}{h}"

    def read(self, ref: str) -> bytes:
        if not ref.startswith(REF_PREFIX):
            raise ToolError(
                "NOT_FOUND",
                f"Invalid memex ref {ref!r}; expected {REF_PREFIX}<sha256>.",
                retry_hint="none",
            )
        h = ref[len(REF_PREFIX) :]
        if not all(c in "0123456789abcdef" for c in h) or len(h) != 64:
            raise ToolError("NOT_FOUND", f"Malformed sha256 in ref {ref!r}.", "none")
        p = self._path_for(h)
        if not p.exists():
            raise ToolError(
                "NOT_FOUND",
                f"Excerpt {ref!r} not present in this Memex.",
                retry_hint="none",
            )
        return p.read_bytes()

    def get_metadata(self, ref: str) -> dict[str, Any]:
        h = ref[len(REF_PREFIX) :]
        meta_path = self._path_for(h).with_suffix(".meta.json")
        if not meta_path.exists():
            return {}
        return json.loads(meta_path.read_text())
