"""ACI-S22 shared fixtures: real builds, real coverage imports, real leases, one repo path.

Every graph root is external and every root is built at the SAME repository path, so the
repository identity is identical (`same_root`) across before, after and coverage sources.
Nothing here mocks the planner, the leases or the coverage importer.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from click.testing import CliRunner
from test_s19_support import build, inventory, same_path_pair  # noqa: F401
from test_s21_support import LABELS, PROJECT, import_run, run_id, workspace  # noqa: F401

from atlas_aci.__main__ import cli
from atlas_aci.codegraph import CodeGraph
from atlas_aci.query import impact, validation
from atlas_aci.query.diff import Side
from atlas_aci.storage import evidence as ev

SENTINEL = (
    "from pathlib import Path\n"
    "\n"
    "Path(__file__).with_name('SENTINEL').write_text('imported')\n"
    "\n"
    "\n"
    "def test_sentinel():\n"
    "    assert True\n"
)
CLAMP_V1 = ("        return low\n", "        return int(low)\n")
CLAMP_V2 = ("        return high\n", "        return int(high)\n")
TOTAL_V1 = ("    result = 0\n", "    result = 0 + 0\n")
NEVER_V1 = ('    return "unreached"\n', '    return "still"\n')
CALC = "app/calc.py"
# The three changed spans of the COV edit (same line count as the original).
EDITS = {1: (CLAMP_V1, TOTAL_V1, NEVER_V1), 2: (CLAMP_V1, CLAMP_V2, TOTAL_V1, NEVER_V1)}

PKG_V1 = {
    "pkg/__init__.py": "",
    "pkg/geometry.py": "def area(w, h):\n    return w * h\n",
    "pkg/shapes.py": (
        "from .geometry import area\n\n\ndef rect_area(w, h):\n    return area(w, h)\n"
    ),
    "pkg/test_shapes.py": (
        "from .shapes import rect_area\n\n\n"
        "def test_rect_area():\n    assert rect_area(1, 2) == 2\n"
    ),
}
PKG_V2 = {**PKG_V1, "pkg/geometry.py": "def area(w, h, scale=1):\n    return w * h * scale\n"}


def edited(original: bytes, level: int = 1) -> bytes:
    text = original.decode()
    for old, new in EDITS[level]:
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    assert text.count("\n") == original.decode().count("\n")
    return text.encode()


@dataclass
class World:
    """COV: the coverage fixture project, original bytes + run a (R0), then edited (R1)."""

    tmp: Path
    repo: Path
    original: bytes
    r0: Path
    r1: Path
    current: bytes

    def root(self, name: str) -> Path:
        return self.tmp / name

    @property
    def before(self) -> Side:
        return Side(self.repo, self.r0)

    @property
    def after(self) -> Side:
        return Side(self.repo, self.r1)

    def write(self, data: bytes) -> None:
        (self.repo / CALC).write_bytes(data)
        self.current = data

    def build_root(self, name: str, data: bytes, *, runs: tuple[str, ...] = ()) -> Path:
        """Build `data` into a fresh external root (optionally importing runs), then restore."""
        keep = self.current
        self.write(data)
        root = self.root(name)
        graph = CodeGraph(self.repo, graph_root=root)
        try:
            graph.build()
            for run in runs:
                import_run(graph, run)
        finally:
            graph.close()
            self.write(keep)
        return root


def cov_world(tmp_path: Path, *, level: int = 1) -> World:
    repo = workspace(tmp_path)
    (repo / "tests/test_sentinel.py").write_text(SENTINEL)
    original = (repo / CALC).read_bytes()
    r0 = tmp_path / "root-r0"
    graph = CodeGraph(repo, graph_root=r0)
    try:
        graph.build()
        import_run(graph, "a")
    finally:
        graph.close()
    (repo / CALC).write_bytes(edited(original, level))
    r1 = tmp_path / "root-r1"
    graph = CodeGraph(repo, graph_root=r1)
    try:
        graph.build()
    finally:
        graph.close()
    return World(tmp_path, repo, original, r0, r1, edited(original, level))


def plan(
    before: Side,
    after: Side,
    arguments: dict[str, Any] | None = None,
    *,
    coverage_roots: list[Path] | tuple[Path, ...] = (),
    settings: impact.Settings | None = None,
) -> dict[str, Any]:
    return validation.run(before, after, arguments, settings, list(coverage_roots))


def cli_args(before: Side, after: Side, roots=(), **options: Any) -> list[str]:
    args = [
        "validation-plan",
        "--before-repo",
        str(before.repo),
        "--after-repo",
        str(after.repo),
        "--before-graph-root",
        str(before.graph_root),
        "--after-graph-root",
        str(after.graph_root),
    ]
    for root in roots:
        args += ["--coverage-graph-root", str(root)]
    for name, value in options.items():
        args += ["--" + name.replace("_", "-"), str(value)]
    return args


def run_cli(*args: str):
    return CliRunner().invoke(cli, list(args), catch_exceptions=False)


def cli_bytes(before: Side, after: Side, roots=(), **options: Any) -> bytes:
    out = run_cli(*cli_args(before, after, roots, **options))
    assert out.exit_code == 0, out.output
    return out.stdout_bytes


def by_name(items: list[dict[str, Any]], name: str, **match: Any) -> dict[str, Any]:
    found = [
        i
        for i in items
        if (i.get("name") == name or i.get("test", {}).get("name") == name)
        and all(i.get(k) == v for k, v in match.items())
    ]
    assert len(found) == 1, (name, [i.get("name") for i in items])
    return found[0]


def span_of(result: dict[str, Any], name: str) -> dict[str, Any]:
    return by_name(result["spans"], name)


def recommendation(result: dict[str, Any], name: str) -> dict[str, Any]:
    return by_name(result["recommendations"], name)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def lease(stack: ExitStack, side: Side) -> Any:
    return impact.open_side(stack, side)


def with_plan_id(plan_record: dict[str, Any]) -> dict[str, Any]:
    from atlas_aci.query import validation_limits as vlim

    copy = json.loads(json.dumps(plan_record))
    copy["plan_id"] = vlim.plan_id(copy)
    return copy


def coverage_ids(root: Path, repo: Path) -> set[str]:
    """IDs of the coverage records (assertions/evidence/occurrences/producers/tests) of a root."""
    from atlas_aci.providers import coverage_py

    graph = CodeGraph(repo, read_only=True, graph_root=root)
    try:
        with graph.reader_lease() as held:
            return coverage_py.record_ids(list(ev.records(held.db)))
    finally:
        graph.close()


def init_git_repo(repo: Path) -> None:
    from test_s19_support import init_git

    init_git(repo)


def write_tree(repo: Path, files: dict[str, str | bytes | None]) -> None:
    from test_s19_support import write_tree as write

    write(repo, files)
