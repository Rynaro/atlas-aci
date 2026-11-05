"""ACI-S34 shared fixtures: real indexer builds, the ADR world and the operator mapping.

Repositories and graph roots are generated in `tmp_path` from the JSON texts in
`tests/fixtures/adr_mapping/world.json`; no golden output document exists because snapshot ids
embed absolute paths. The mapping literals (entity ids, digests) are Day-1 observations.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import tree_sitter_language_pack as pack
from click.testing import CliRunner
from test_s19_support import build, same_path_pair

from atlas_aci.__main__ import cli
from atlas_aci.overlays import runtime
from atlas_aci.query.diff import Side

ROOT = Path(__file__).resolve().parents[3]
FX = ROOT / "mcp-server/tests/fixtures/adr_mapping"
SRC = ROOT / "mcp-server/src/atlas_aci"
DOCS = ROOT / "docs/rationale-drift.md"
BASE_TOOLS_SHA256 = "94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56"
BASE_TOOLS_BYTES = 1631472
GRAMMARS = ("python", "markdown", "yaml")
PROVISION = (
    'python -c "from tree_sitter_language_pack import download, get_parser; '
    "names = ['python', 'markdown', 'yaml']; download(names); "
    '[get_parser(n) for n in names]"'
)
WORLD_TEXTS = json.loads((FX / "world.json").read_text(encoding="ascii"))
EXPECTED = json.loads((FX / "expected.json").read_text(encoding="ascii"))
ADR = "docs/adr/0001-pricing.md"


def provision_grammars() -> None:
    """Fail (never skip) when a grammar the world needs is not provisioned."""
    for name in GRAMMARS:
        try:
            pack.get_parser(name)
        except Exception as exc:
            pytest.fail(
                f"the {name} grammar cannot be provisioned ({type(exc).__name__}); "
                f"run once with network access: {PROVISION}",
                pytrace=False,
            )


@dataclass
class World:
    """One built world: the checkout holds the AFTER state, the roots hold both snapshots."""

    tmp: Path
    repo: Path
    before: Side
    after: Side
    config: Side
    nongit: Side
    snapshot_id: str

    @property
    def roots(self) -> list[Path]:
        return [
            self.repo,
            *(side.graph_root for side in (self.before, self.after, self.config, self.nongit)),
        ]

    def raw(self, name: str = "mapping.json", snapshot_id: str | None = None) -> bytes:
        text = (FX / name).read_text(encoding="ascii")
        return text.replace("@before@", snapshot_id or self.snapshot_id).encode("ascii")

    def document(self, name: str = "mapping.json") -> dict[str, Any]:
        return json.loads(self.raw(name))

    def mixed(self) -> bytes:
        """Every resolvable and every import-unresolved mapping in one document."""
        first, second = self.document(), self.document("mapping-unresolved.json")
        return encode({**first, "mappings": [*first["mappings"], *second["mappings"]]})

    def mapping_file(self, name: str, raw: bytes | None = None) -> Path:
        path = self.tmp / "inputs" / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(self.raw() if raw is None else raw)
        return path

    def outputs(self, name: str = "out") -> Path:
        path = self.tmp / name
        path.mkdir(exist_ok=True)
        return path


def encode(document: Any) -> bytes:
    return json.dumps(document, sort_keys=True, indent=1).encode("ascii") + b"\n"


def snapshot_of(side: Side) -> str:
    with ExitStack() as stack:
        return str(runtime.open_active(stack, side).snapshot_id)


def generation_of(side: Side) -> str:
    with ExitStack() as stack:
        return str(runtime.open_active(stack, side).generation_id)


def build_world(tmp: Path, *, extra_roots: bool = True) -> World:
    """Index the world's before and after states; the checkout is left at the after state."""
    provision_grammars()
    tmp.mkdir(parents=True, exist_ok=True)
    repo, before, after = same_path_pair(tmp, WORLD_TEXTS["before"], WORLD_TEXTS["after"])
    config = nongit = after
    if extra_roots:
        build(repo, tmp / "root-config", ["python", "markdown"])
        config = Side(repo, tmp / "root-config")
        aside = repo / ".git-aside"
        (repo / ".git").rename(aside)
        try:
            build(repo, tmp / "root-nongit")
        finally:
            aside.rename(repo / ".git")
        nongit = Side(repo, tmp / "root-nongit")
    return World(tmp, repo, before, after, config, nongit, snapshot_of(before))


def reindex(world: World, edits: dict[str, str], root: str, langs: list[str] | None = None) -> None:
    """Edit the checkout and index it again into `root` (a held lease must not notice)."""
    for name, text in edits.items():
        (world.repo / name).write_text(text)
    build(world.repo, world.tmp / root, langs)


# ---- inventories -------------------------------------------------------------------------------


def stat_inventory(*roots: Path) -> dict[str, tuple[Any, ...]]:
    """(kind, sha256, size, mode, mtime_ns) of every file, link and directory under the roots."""
    result: dict[str, tuple[Any, ...]] = {}
    for root in roots:
        if not root.exists():
            result[str(root)] = ("absent",)
            continue
        for current, directories, files in os.walk(root):
            for name in [*directories, *files]:
                path = Path(current) / name
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode):
                    entry: tuple[Any, ...] = ("link", os.readlink(path))
                elif stat.S_ISDIR(info.st_mode):
                    entry = ("dir", None, 0, info.st_mode, info.st_mtime_ns)
                else:
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                    entry = ("file", digest, info.st_size, info.st_mode, info.st_mtime_ns)
                result[str(path)] = entry
            info = Path(current).lstat()
            result[current + "/"] = ("dir", None, 0, info.st_mode, info.st_mtime_ns)
    return result


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---- running ------------------------------------------------------------------------------------


def cli_run(*args: Any, **kwargs: Any):
    return CliRunner().invoke(cli, [str(item) for item in args], **kwargs)


def map_args(world: World, mapping: Path, out: Path, *extra: Any) -> list[str]:
    return [
        "rationale-map",
        "--mapping",
        str(mapping),
        "--repo",
        str(world.repo),
        "--graph-root",
        str(world.before.graph_root),
        "--output",
        str(out),
        *[str(item) for item in extra],
    ]


def drift_args(
    world: World, mapping: Path, out: Path, after: Side | None = None, *extra: Any
) -> list[str]:
    after = after or world.after
    return [
        "rationale-drift",
        "--mapping",
        str(mapping),
        "--repo",
        str(world.repo),
        "--before-graph-root",
        str(world.before.graph_root),
        "--after-graph-root",
        str(after.graph_root),
        "--output",
        str(out),
        *[str(item) for item in extra],
    ]


def sentinel_output(tmp: Path, name: str = "out.json") -> tuple[Path, bytes]:
    """An existing `--output` holding sentinel bytes a rejection must leave byte-unchanged."""
    out = tmp / name
    sentinel = b"SENTINEL-" + name.encode() + b"\x00\xff"
    out.write_bytes(sentinel)
    return out, sentinel


def temp_leftovers(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir() if p.name.startswith(".atlas-rationale-"))


def assert_rejected_cli(result, text: str, out: Path, sentinel: bytes) -> None:
    """Exit 1, the exact fixed text, empty stdout, the pre-existing output untouched, no temp."""
    assert result.exit_code == 1, (result.exit_code, result.stderr)
    assert result.stdout_bytes == b""
    assert result.stderr == "Error: " + text + "\n"
    assert out.read_bytes() == sentinel
    assert temp_leftovers(out.parent) == []


def by_mapping(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["mapping_id"]: item for item in document["results"]}
