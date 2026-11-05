"""ACI-S27 shared helpers: a real indexed repository, manifests, artifacts and the CLI."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from click.testing import CliRunner
from test_s19_support import build, init_git, write_tree

from atlas_aci.__main__ import cli
from atlas_aci.query.diff import Side

ROOT = Path(__file__).resolve().parents[3]
FX = ROOT / "mcp-server/tests/fixtures/otel"
DOCS = ROOT / "docs/importers/opentelemetry.md"
SRC = ROOT / "mcp-server/src/atlas_aci"

SERVICE = (
    "class Service:\n"
    "    def handle(self, x):\n"
    "        return helper(x)\n"
    "\n"
    "\n"
    "def helper(x):\n"
    "    return x\n"
)
FILES = {
    "app/__init__.py": "",
    "app/service.py": SERVICE,
    "app/broken.py": "def (:\n",
    "web/page.js": "function f() { return 1 }\n",
}

T1 = "0af7651916cd43dd8448eb211c80319c"
T2 = "5b8aa5a2d2c872e8321cf37308d69df2"


def sid(number: int) -> str:
    """The fixture span id of span `s<number>`."""
    return f"a0000000000000{number:02x}"


# The exact linkage of every fixture span when the build matches (trace-basic.json).
EXPECTED = {
    (T1, 1): ("linked_symbol", "handle"),
    (T1, 2): ("linked_symbol", "helper"),
    (T1, 3): ("linked_file", None),
    (T1, 4): ("unlinked_missing_source_location", None),
    (T1, 5): ("unlinked_missing_source_location", None),
    (T1, 7): ("unlinked_file_not_in_snapshot", None),
    (T1, 8): ("unlinked_file_not_indexed", None),
    (T1, 9): ("unlinked_line_out_of_range", None),
    (T1, 11): ("unlinked_malformed_source_location", None),
    (T1, 12): ("linked_symbol", "helper"),
    (T2, 6): ("unlinked_path_outside_root", None),
    (T2, 10): ("linked_file", None),
    (T2, 13): ("linked_symbol", "handle"),
}
EDGES = {(1, 2), (2, 3), (1, 4), (1, 5), (1, 7), (1, 8), (1, 9), (1, 11), (1, 12)}


@dataclass
class World:
    repo: Path
    root: Path
    snapshot_id: str

    @property
    def side(self) -> Side:
        return Side(self.repo, self.root)


def make_world(base: Path, files=None, name="w", langs=("python",)) -> World:
    repo = base / name
    repo.mkdir()
    init_git(repo)
    write_tree(repo, FILES if files is None else files)
    root = base / (name + "-graph")
    stats = build(repo, root, list(langs))
    return World(repo, root, stats["snapshot_id"])


def raw_of(value) -> bytes:
    return value if isinstance(value, bytes) else json.dumps(value, sort_keys=True).encode()


def example() -> dict:
    return json.loads((FX / "manifest-example.json").read_bytes())


def manifest_for(artifact: bytes, snapshot_id: str | None = None, **patch) -> dict:
    """The example manifest re-bound to `artifact` (and to a snapshot id when given)."""
    document = example()
    document["artifact"] = {"sha256": hashlib.sha256(artifact).hexdigest(), "bytes": len(artifact)}
    if snapshot_id is not None:
        document["build"]["snapshot_id"] = snapshot_id
    document.update(patch)
    return document


def basic() -> bytes:
    return (FX / "trace-basic.json").read_bytes()


def sentinels() -> bytes:
    return (FX / "trace-sentinels.json").read_bytes()


def write_inputs(
    base: Path, artifact: bytes, manifest: dict | bytes, stem="in"
) -> tuple[Path, Path]:
    base.mkdir(parents=True, exist_ok=True)
    artifact_path = base / (stem + "-artifact.json")
    manifest_path = base / (stem + "-manifest.json")
    artifact_path.write_bytes(artifact)
    manifest_path.write_bytes(raw_of(manifest))
    return manifest_path, artifact_path


def run_cli(world: World, manifest: Path, artifact: Path, output: Path, *extra, graph=True):
    args = ["import-otel", "--repo", str(world.repo)]
    if graph:
        args += ["--graph-root", str(world.root)]
    args += ["--manifest", str(manifest), "--output", str(output), *extra, str(artifact)]
    return CliRunner().invoke(cli, args)


def observation(overlay: dict, trace: str, number: int) -> dict:
    found = [
        o for o in overlay["observations"] if o["trace_id"] == trace and o["span_id"] == sid(number)
    ]
    assert len(found) == 1
    return found[0]


def key_walk(node, path=()):
    """Every (key-path, key) of a JSON document, iteratively."""
    pending = [(node, path)]
    while pending:
        item, where = pending.pop()
        if isinstance(item, dict):
            for key, value in item.items():
                yield where, key
                pending.append((value, (*where, key)))
        elif isinstance(item, list):
            pending.extend((value, where) for value in item)


def span_json(number, *, trace=T1, parent=None, path=None, line=None, extra=None) -> dict:
    document = {
        "traceId": trace,
        "spanId": sid(number),
        "startTimeUnixNano": "1700000000000000000",
        "endTimeUnixNano": "1700000000000000001",
    }
    if parent is not None:
        document["parentSpanId"] = sid(parent)
    attributes = []
    if path is not None:
        attributes.append({"key": "code.file.path", "value": {"stringValue": path}})
    if line is not None:
        attributes.append({"key": "code.line.number", "value": {"intValue": str(line)}})
    if attributes:
        document["attributes"] = attributes
    document.update(extra or {})
    return document


def otlp(*spans) -> bytes:
    document = {"resourceSpans": [{"scopeSpans": [{"spans": list(spans)}]}]}
    return json.dumps(document).encode()


def run_import(side, snapshot_id, artifact: bytes, roots=None, **patch) -> dict:
    """The library pipeline over `artifact`, with a manifest bound to `snapshot_id`."""
    from atlas_aci.overlays import runtime

    manifest = manifest_for(artifact, snapshot_id, **patch)
    if roots is not None:
        manifest["source_roots"] = roots
    return runtime.import_otel(side, raw_of(manifest), artifact)


def classes(overlay: dict) -> dict:
    """span number -> classification, for spans of the default trace."""
    return {
        int(o["span_id"][-2:], 16): o["linkage"]["classification"]
        for o in overlay["observations"]
        if o["trace_id"] == T1
    }
