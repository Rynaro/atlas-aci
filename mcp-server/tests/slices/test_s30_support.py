"""ACI-S30 shared fixtures: real indexer builds, an S28 package snapshot and the explicit mapping.

Wheels, locks, package snapshots, repositories and graphs are generated in `tmp_path` from the S28
generator (loaded by path) and the JSON texts in `tests/fixtures/cross_repo/world.json`; no golden
overlay exists because snapshot ids depend on absolute paths.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import tree_sitter_language_pack as pack
from click.testing import CliRunner
from test_s19_support import build, init_git, write_tree

from atlas_aci.__main__ import cli
from atlas_aci.cross_repo import package_links as pl
from atlas_aci.cross_repo import revision_vector as rv
from atlas_aci.dependencies import python_wheel as pw
from atlas_aci.dependencies import uv_lock as ul
from atlas_aci.query.diff import Side

ROOT = Path(__file__).resolve().parents[3]
FX = ROOT / "mcp-server/tests/fixtures/cross_repo"
GENERATOR = ROOT / "mcp-server/tests/fixtures/python_dependencies/generate.py"
SRC = ROOT / "mcp-server/src/atlas_aci"
DOCS = ROOT / "docs/cross-repository-links.md"
PROVISION = (
    'python -c "from tree_sitter_language_pack import download, get_parser; '
    "download(['python']); get_parser('python')\""
)
BASE_TOOLS_SHA256 = "94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56"
BASE_TOOLS_BYTES = 1631472


def _load_generator():
    spec = importlib.util.spec_from_file_location("s30_generate", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["s30_generate"] = module  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(module)
    return module


GEN = _load_generator()
TEXTS = json.loads((FX / "world.json").read_text())
PACKAGE_MEMBERS: dict[str, str] = TEXTS["package"]
CONSUMER_FILES: dict[str, str] = TEXTS["consumer"]
PROVIDER_EXTRA: dict[str, str] = TEXTS["provider_extra"]
MAIN = CONSUMER_FILES["app/main.py"]
VERSION = "1.0.0"
NAME = "s30-shapes"
DIST = "s30_shapes-{}.dist-info"
SHA_EMPTY = "0" * 64


pytestmark = pytest.mark.usefixtures("grammar")


@pytest.fixture(scope="session")
def grammar():
    """Provision the python grammar OUTSIDE every measured window; fail (never skip) without it."""
    try:
        pack.get_parser("python")
    except Exception as exc:
        pytest.fail(
            "the python grammar cannot be provisioned ("
            + type(exc).__name__
            + "); run once with network access: "
            + PROVISION,
            pytrace=False,
        )
    assert "python" in pack.downloaded_languages()
    return pack.cache_dir()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def line_of(text: str) -> int:
    """The 1-based line of the one source line that equals `text` exactly."""
    lines = MAIN.splitlines()
    assert lines.count(text) == 1, text
    return lines.index(text) + 1


@dataclass(frozen=True)
class Package:
    """One in-test wheel, the lock that pins it and the S28 snapshot imported from it."""

    wheel: bytes
    filename: str
    lock: bytes
    snapshot: bytes
    version: str
    environment: dict[str, str] = field(default_factory=dict)


def wheel_for(members: dict[str, str], version: str = VERSION) -> tuple[bytes, str]:
    dist = DIST.format(version)
    entries = [GEN.Entry(path.encode(), text.encode()) for path, text in sorted(members.items())]
    entries.append(
        GEN.Entry(
            (dist + "/METADATA").encode(),
            ("Metadata-Version: 2.1\nName: s30-shapes\nVersion: " + version + "\n\n").encode(),
        )
    )
    entries.append(
        GEN.Entry(
            (dist + "/WHEEL").encode(),
            b"Wheel-Version: 1.0\nGenerator: s30-fixture\n"
            b"Root-Is-Purelib: true\nTag: py3-none-any\n",
        )
    )
    return GEN.build_zip(entries), "s30_shapes-" + version + "-py3-none-any.whl"


def lock_for(wheel: bytes, filename: str, version: str = VERSION, digest: str | None = None):
    line = GEN.wheel_line(filename, digest or sha256(wheel), len(wheel))
    block = GEN.package_block(NAME, version, wheels=(line,))
    return GEN.lock_document([block]).encode()


def make_package(
    members: dict[str, str] | None = None,
    version: str = VERSION,
    environment: dict[str, str] | None = None,
) -> Package:
    env = {} if environment is None else environment
    wheel, filename = wheel_for(PACKAGE_MEMBERS if members is None else members, version)
    lock = lock_for(wheel, filename, version)
    snapshot = pw.import_dependency(
        lock, NAME, env, [(filename, lambda: wheel)], ul.normalize_limits()
    )
    return Package(wheel, filename, lock, pw.canonical_bytes(snapshot), version, env)


def mapping_document(
    consumer_snapshot: str,
    provider_snapshot: str,
    package: Package,
    *,
    lock: bytes | None = None,
    coordinate: str | None = None,
    consumer_id: str = "app",
    provider_id: str = "lib",
    prefix: str = "src/",
    package_provider: str | None = None,
) -> dict[str, Any]:
    return {
        "format": rv.MAPPING_FORMAT,
        "repositories": [
            {"repository_id": consumer_id, "role": "consumer", "snapshot_id": consumer_snapshot},
            {"repository_id": provider_id, "role": "provider", "snapshot_id": provider_snapshot},
        ],
        "consumer": {
            "lock_sha256": sha256(package.lock if lock is None else lock),
            "environment": dict(package.environment),
        },
        "packages": [
            {
                "provider_repository_id": package_provider or provider_id,
                "coordinate": coordinate or "pypi:" + NAME + "@" + package.version,
                "package_snapshot_sha256": sha256(package.snapshot),
                "source_prefix": prefix,
            }
        ],
    }


def encode(document: Any, indent: int | None = None) -> bytes:
    return json.dumps(document, indent=indent).encode()


@dataclass
class World:
    """Two indexed repositories (and a sentinel), a package and the inputs to link them."""

    root: Path
    package: Package
    app: Path
    lib: Path
    other: Path | None
    graphs: Path
    inputs: Path
    snapshots: dict[str, str] = field(default_factory=dict)
    spies: list[Any] = field(default_factory=list)

    def side(self, name: str, graphs: Path | None = None) -> Side:
        repo = {"app": self.app, "lib": self.lib, "other": self.other}[name]
        assert repo is not None
        return Side(repo, (graphs or self.graphs) / name)

    @property
    def consumer(self) -> Side:
        return self.side("app")

    @property
    def provider(self) -> Side:
        return self.side("lib")

    def document(self, **overrides: Any) -> dict[str, Any]:
        return mapping_document(
            self.snapshots["app"], self.snapshots["lib"], self.package, **overrides
        )

    def mapping(self, **overrides: Any) -> bytes:
        return encode(self.document(**overrides))

    @property
    def lock(self) -> bytes:
        return self.package.lock

    def write_inputs(self, mapping: bytes | None = None) -> dict[str, Path]:
        paths = {
            "mapping": self.inputs / "mapping.json",
            "lock": self.inputs / "uv.lock",
            "package": self.inputs / "pkg.json",
        }
        paths["mapping"].write_bytes(self.mapping() if mapping is None else mapping)
        paths["lock"].write_bytes(self.package.lock)
        paths["package"].write_bytes(self.package.snapshot)
        return paths

    def reindex(self, name: str, graphs: Path | None = None) -> str:
        repo = {"app": self.app, "lib": self.lib, "other": self.other}[name]
        assert repo is not None
        stats = build(repo, (graphs or self.graphs) / name, ["python"])
        self.snapshots[name] = stats["snapshot_id"]
        return str(stats["snapshot_id"])

    def link(self, **kwargs: Any) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "mapping_raw": self.mapping(),
            "lock_raw": self.package.lock,
            "package_raw": self.package.snapshot,
            "consumer": self.consumer,
            "provider": self.provider,
        }
        arguments.update(kwargs)
        return pl.link(
            arguments["mapping_raw"],
            arguments["lock_raw"],
            arguments["package_raw"],
            arguments["consumer"],
            arguments["provider"],
            arguments.get("previous_raw"),
            arguments.get("limits"),
        )

    def link_bytes(self, **kwargs: Any) -> bytes:
        return pl.canonical_bytes(self.link(**kwargs))

    def impact(
        self,
        overlay: dict[str, Any] | bytes,
        seed: str,
        direction: str,
        mapping: bytes | None = None,
        budgets: pl.Budgets | None = None,
        limits: pl.LinkLimits | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        raw = overlay if isinstance(overlay, bytes) else pl.canonical_bytes(overlay)
        return pl.impact(
            raw,
            self.mapping() if mapping is None else mapping,
            kwargs.get("consumer", self.consumer),
            kwargs.get("provider", self.provider),
            seed,
            direction,
            budgets,
            limits,
        )

    def edit(self, name: str, path: str, text: str) -> str:
        """Rewrite one tracked file of a repository and re-index it; returns the new snapshot."""
        repo = {"app": self.app, "lib": self.lib, "other": self.other}[name]
        assert repo is not None
        (repo / path).write_text(text)
        return self.reindex(name)


def make_world(
    root: Path,
    *,
    package: Package | None = None,
    provider_edit: Callable[[dict[str, str]], None] | None = None,
    consumer_edit: Callable[[dict[str, str]], None] | None = None,
    with_other: bool = True,
) -> World:
    """Build the consumer, the provider and (optionally) a sentinel third repository."""
    package = make_package() if package is None else package
    root.mkdir(parents=True, exist_ok=True)
    provider_files = {"src/" + path: text for path, text in PACKAGE_MEMBERS.items()}
    provider_files.update(PROVIDER_EXTRA)
    consumer_files = dict(CONSUMER_FILES)
    if provider_edit is not None:
        provider_edit(provider_files)
    if consumer_edit is not None:
        consumer_edit(consumer_files)
    repos = {"app": consumer_files, "lib": provider_files}
    if with_other:
        repos["other"] = {"src/" + path: text for path, text in PACKAGE_MEMBERS.items()}
        repos["other"]["src/only_other.py"] = "def only_other():\n    pass\n"
    world = World(
        root=root,
        package=package,
        app=root / "app",
        lib=root / "lib",
        other=root / "other" if with_other else None,
        graphs=root / "graphs",
        inputs=root / "inputs",
    )
    world.inputs.mkdir()
    for name, files in repos.items():
        repo = root / name
        repo.mkdir()
        init_git(repo)
        write_tree(repo, {key: value for key, value in files.items() if value is not None})
        world.reindex(name)
    return world


# ---- inventories ----------------------------------------------------------------------------


def inventory(*roots: Path) -> dict[str, tuple[Any, ...]]:
    """Mode, size, mtime_ns and SHA-256 of every path (and directory) under the given trees."""
    found: dict[str, tuple[Any, ...]] = {}
    for root in roots:
        if not root.exists():
            found[str(root)] = ("<absent>",)
            continue
        for path in [root, *sorted(root.rglob("*"))]:
            info = os.lstat(path)
            digest = None
            if path.is_file() and not path.is_symlink():
                digest = sha256(path.read_bytes())
            found[str(path)] = (info.st_mode, info.st_size, info.st_mtime_ns, digest)
    return found


def world_inventory(world: World, *extra: Path) -> dict[str, tuple[Any, ...]]:
    roots = [world.app, world.lib, world.graphs, world.inputs, *extra]
    if world.other is not None:
        roots.append(world.other)
    return inventory(*roots)


# ---- reading an overlay -------------------------------------------------------------------------


def by_line(overlay: dict[str, Any]) -> dict[tuple[int, str], dict[str, Any]]:
    """The references of `app/main.py` keyed by (line, name)."""
    found = {}
    for item in overlay["references"]:
        if item["consumer"]["path"] == "app/main.py":
            key = (item["consumer"]["line"], item["name"])
            assert key not in found, key
            found[key] = item
    return found


def outcome(overlay: dict[str, Any], text: str, name: str) -> tuple[str, str | None]:
    item = by_line(overlay)[(line_of(text), name)]
    return item["resolution"], item["detail"]


def file_entity(path: str, language: str = "python") -> str:
    from atlas_aci.storage import evidence as ev

    return str(ev.file_entity(path, language)["id"])


# ---- the CLI ------------------------------------------------------------------------------------


def cli_run(*args: Any, **kwargs: Any):
    return CliRunner().invoke(cli, [str(item) for item in args], **kwargs)


def cli_link_args(world: World, paths: dict[str, Path], out: Path) -> list[Any]:
    return [
        "cross-repo-link",
        "--mapping", paths["mapping"],
        "--lock", paths["lock"],
        "--package-snapshot", paths["package"],
        "--consumer-repo", world.app,
        "--consumer-graph-root", world.graphs / "app",
        "--provider-repo", world.lib,
        "--provider-graph-root", world.graphs / "lib",
        "--output", out,
    ]  # fmt: skip


def cli_link(world: World, paths: dict[str, Path], out: Path, *extra: Any):
    return cli_run(*cli_link_args(world, paths, out), *extra)


def cli_impact_args(world: World, overlay: Path, mapping: Path, seed: str, direction: str):
    return [
        "cross-repo-impact",
        "--overlay", overlay,
        "--mapping", mapping,
        "--consumer-repo", world.app,
        "--consumer-graph-root", world.graphs / "app",
        "--provider-repo", world.lib,
        "--provider-graph-root", world.graphs / "lib",
        "--seed", seed,
        "--direction", direction,
    ]  # fmt: skip


def cli_impact(world: World, overlay: Path, mapping: Path, seed: str, direction: str, *extra: Any):
    return cli_run(*cli_impact_args(world, overlay, mapping, seed, direction), *extra)


def sentinel_output(tmp_path: Path, name: str = "out.json") -> tuple[Path, bytes]:
    """An existing `--output` holding sentinel bytes a rejection must leave byte-unchanged."""
    out = tmp_path / name
    sentinel = b"SENTINEL-" + name.encode() + b"\x00\xff"
    out.write_bytes(sentinel)
    return out, sentinel


def assert_rejected_cli(result, text: str, out: Path, sentinel: bytes) -> None:
    """Exit 1, the exact fixed text, empty stdout, the pre-existing output untouched, no temp."""
    assert result.exit_code == 1, (result.exit_code, result.stderr)
    assert result.stdout_bytes == b""
    assert result.stderr == "Error: " + text + "\n"
    assert out.read_bytes() == sentinel
    leftovers = [p.name for p in out.parent.iterdir() if p.name.startswith(".atlas-cross-repo")]
    assert leftovers == []


def test_world_texts_and_example_are_consistent():
    """The shared texts: the package members are the provider's sources, byte for byte."""
    assert "app/main.py" in CONSUMER_FILES and "s30_shapes/geometry.py" in PACKAGE_MEMBERS
    assert rv.parse_mapping((FX / "mapping-example.json").read_bytes(), 65536).coordinate == (
        "pypi:s30-shapes@1.0.0"
    )
    package = make_package()
    snapshot = json.loads(package.snapshot)
    sources = [m for m in snapshot["members"] if m["role"] == "python_source"]
    assert len(sources) == len(PACKAGE_MEMBERS) == 6
    for member in sources:
        assert member["sha256"] == sha256(PACKAGE_MEMBERS[member["path"]].encode())
    assert pl.LIMITS and pl.BUDGETS
