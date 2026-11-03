"""ACI-S20 shared fixtures: real indexer builds, real MCP dispatch, frozen labels.

The fixture is a package with relative imports (condition 1): absolute imports are
`unsupported_absolute_import` and top-level relative imports without a package are
`relative_import_no_package` in the Python import provider. Labels are frozen
constants pinned by sha256 (`labels.json` in the S20 evidence directory was captured
before any propagation code existed).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from test_s19_support import (  # noqa: F401
    build,
    git,
    init_git,
    inventory,
    same_path_pair,
    wipe,
    write_tree,
)

from atlas_aci.config import Config
from atlas_aci.server import build_server_state, call_tool_result

GEOMETRY_V1 = (
    "def area(w, h):\n    return w * h\n\n\n"
    "def perimeter(w, h):\n    return 2 * (w + h)\n\n\n"
    "def helper():\n    return 1\n\n\n"
    "def legacy():\n    return 0\n\n\n"
    "def doubled(w, h):\n    return area(w, h) * 2\n"
)
GEOMETRY_V2 = (
    "def area(w, h, scale=1):\n    return w * h * scale\n\n\n"
    "def perimeter(w, h):\n    return 2 * (w + h)\n\n\n"
    "def helper():\n    return 2\n\n\n"
    "def doubled(w, h):\n    return area(w, h) * 2\n"
)
V1 = {
    "pkg/__init__.py": "",
    "pkg/geometry.py": GEOMETRY_V1,
    "pkg/shapes.py": (
        "from .geometry import area\n\n\n"
        "class Rect:\n    def size(self):\n        return area(1, 2)\n"
    ),
    "pkg/report.py": "def summary(r):\n    return r.size()\n",
    "pkg/cli.py": "from .geometry import legacy\n\n\ndef main():\n    return legacy()\n",
    "pkg/util.py": (
        "from .geometry import perimeter\n\n\ndef unrelated():\n    return perimeter(1, 2)\n"
    ),
}
V2 = {**V1, "pkg/geometry.py": GEOMETRY_V2}
FIXTURE_SHA256 = "cb0bd5f322da222e7339b17726f7d44cb6a9f73e7899c3dc07c182790cfd1ce1"

# Frozen labels (entity key = "<path>::<owner chain>.<name>").
LABEL_SEEDS = {
    "pkg/geometry.py::area": "signature",
    "pkg/geometry.py::helper": "body",
    "pkg/geometry.py::legacy": "removed",
}
LABEL_AFFECTED = {
    "pkg/geometry.py::doubled",
    "pkg/shapes.py::Rect.size",
    "pkg/cli.py::main",
    "pkg/report.py::summary",
}
LABEL_UNAFFECTED = {"pkg/geometry.py::perimeter", "pkg/util.py::unrelated"}

# Ruby has no scoped resolver: duplicate same-named methods yield an AMBIGUOUS edge.
RUBY_V1 = {
    "app/a.rb": "class Hub\n  def dup_name(a)\n  end\nend\n",
    "app/b.rb": "class Hub\n  def dup_name(a)\n  end\nend\n",
    "app/caller.rb": "class Hub\n  def call\n    dup_name()\n  end\nend\n",
}
RUBY_V2 = {**RUBY_V1, "app/a.rb": "class Hub\n  def dup_name(a, b)\n  end\nend\n"}

# No unresolved or ambiguous reference anywhere: the coverage negative control.
CLEAN_V1 = {"m.py": "def f(a):\n    return a\n\n\ndef g():\n    return f(1)\n"}
CLEAN_V2 = {"m.py": "def f(a, b):\n    return a\n\n\ndef g():\n    return f(1)\n"}


def fixture_digest() -> str:
    text = json.dumps({"v1": V1, "v2": V2}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def key(entity: dict[str, Any]) -> str:
    owners = ".".join(o["name"] for o in entity.get("owners", []) if o.get("name"))
    return f"{entity['path']}::{owners + '.' if owners else ''}{entity['name']}"


def chain_files(length: int, fan_a: int = 0, fan_b: int = 0) -> tuple[dict, dict]:
    """c0 <- c1 <- ... <- c{length}; plus two changed seeds `s_a`/`s_b` with fan-out callers."""
    lines1, lines2 = ["def c0(x):\n    return x\n"], ["def c0(x, y):\n    return x\n"]
    rest = "".join(f"\n\ndef c{i}():\n    return c{i - 1}(1)\n" for i in range(1, length + 1))
    seeds1, seeds2 = "", ""
    for tag, fan in (("a", fan_a), ("b", fan_b)):
        if not fan:
            continue
        seeds1 += f"\n\ndef s_{tag}(x):\n    return x\n"
        seeds2 += f"\n\ndef s_{tag}(x, y):\n    return x\n"
        for i in range(fan):
            seeds1 += f"\n\ndef t_{tag}{i}():\n    return s_{tag}(1)\n"
            seeds2 += f"\n\ndef t_{tag}{i}():\n    return s_{tag}(1)\n"
    return (
        {"chain.py": lines1[0] + rest + seeds1},
        {"chain.py": lines2[0] + rest + seeds2},
    )


class Served:
    """A real MCP server state over the AFTER side, with an operator-set BEFORE side."""

    def __init__(self, tmp_path: Path, repo: Path, before, after, **config: Any):
        self.repo, self.before, self.after = repo, before, after
        options = {"max_calls_per_minute": 0, **config}
        if "before_graph_root" not in options and not options.pop("unconfigured", False):
            options["before_graph_root"] = before.graph_root
        self.config = Config(
            repo=repo,
            memex_root=tmp_path / "memex",
            graph_root=after.graph_root,
            **options,
        )
        self.enforcement, self.memex, self.graph = build_server_state(self.config)

    def call(self, arguments: dict[str, Any], *, version: str = "2025-11-25"):
        arguments = {"schema_version": "atlas.graph.v1", "op": "impact", **arguments}
        return asyncio.run(
            call_tool_result(
                "graph_query",
                arguments,
                self.config,
                self.enforcement,
                self.memex,
                self.graph,
                protocol_version=version,
            )
        )

    def impact(self, **arguments: Any) -> dict[str, Any]:
        result = self.call(arguments)
        assert not result.isError, result.content[0].text
        assert result.structuredContent is not None
        assert json.loads(result.content[0].text) == result.structuredContent
        return result.structuredContent

    def close(self) -> None:
        self.graph.close()


def served_pair(tmp_path, first, second, **config):
    repo, before, after = same_path_pair(tmp_path, first, second)
    return Served(tmp_path, repo, before, after, **config)


def by_name(items, name, **match):
    found = [
        i for i in items if i["entity"]["name"] == name and all(i[k] == v for k, v in match.items())
    ]
    assert len(found) == 1, (name, [i["entity"]["name"] for i in items])
    return found[0]


def seed_named(data, name):
    found = [s for s in data["seeds"] if s["name"] == name]
    assert len(found) == 1, (name, data["seeds"])
    return found[0]


def lease(stack, side):
    """A real pinned reader lease on one snapshot namespace (what impact itself uses)."""
    from atlas_aci.query import diff as symbol_diff

    return symbol_diff._lease(stack, symbol_diff._open(stack, side))


def exists(reader, table: str, identifier: str) -> bool:
    row = reader.db.execute(f"SELECT 1 FROM {table} WHERE id=?", (identifier,)).fetchone()
    return row is not None
