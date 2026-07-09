"""Tests for server.py's dispatch table + the D2 central bounds chokepoint.

These are the tests that prove "Tools can only narrow bounds, never widen
them" (README §Mechanical bounds) is actually true for every tool and every
graph_query verb — not just the ones that remembered to call a `cap_*`
helper themselves. AC-H-7 (search_symbol) and AC-H-8 (graph_query) are the
two named regressions this file exists to close; AC-H-15's registry test is
built so a no-op cap (an empty/missing `_bounded_field`) fails it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from atlas_aci.codegraph import PRODUCED_KINDS, CodeGraph
from atlas_aci.config import Config
from atlas_aci.enforcement import Enforcement, ToolError
from atlas_aci.memex import Memex
from atlas_aci.server import (
    GRAPH_QUERY_VERB_BOUNDED_FIELDS,
    TOOL_BOUNDED_FIELDS,
    apply_central_bounds,
    bounded_fields_for,
    build_tool_manifest,
    dispatch_tool_call,
)


@pytest.fixture
def config(tmp_path: Path) -> Config:
    repo = tmp_path / "repo"
    repo.mkdir()
    return Config(
        repo=repo,
        memex_root=tmp_path / "memex",
        max_bound_field_elements=3,
    )


@pytest.fixture
def enforcement(config: Config) -> Enforcement:
    return Enforcement(config)


@pytest.fixture
def memex(config: Config) -> Memex:
    return Memex(config.memex_root)


def _rb(repo: Path, rel: str, content: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


# ---- AC-H-15 — registry completeness (a no-op cap must fail this) ----

_LIST_BEARING_TOOLS = {
    "view_file": ("lines",),
    "list_dir": ("entries",),
    "search_text": ("matches",),
    "search_symbol": ("definitions", "references"),
}
_LIST_BEARING_VERBS = {
    "callers_of": ("edges",),
    "definitions_of": ("definitions", "references"),
    "subclasses_of": ("edges",),
}


def test_every_list_returning_tool_registers_a_bounded_field() -> None:
    for tool, expected in _LIST_BEARING_TOOLS.items():
        fields = TOOL_BOUNDED_FIELDS.get(tool, ())
        assert fields, f"{tool} exposes list field(s) {expected} but has no _bounded_field"
        assert set(expected) <= set(fields)

    for verb, expected in _LIST_BEARING_VERBS.items():
        fields = GRAPH_QUERY_VERB_BOUNDED_FIELDS.get(verb, ())
        assert fields, f"graph_query:{verb} exposes {expected} but has no _bounded_field"
        assert set(expected) <= set(fields)

    # test_dry_run/memex_read have no list-valued field — deliberately absent,
    # not forgotten. Pin the distinction so it can't drift silently.
    assert bounded_fields_for("test_dry_run", {}) == ()
    assert bounded_fields_for("memex_read", {}) == ()


def test_bounded_field_registry_matches_tool_manifest(config: Config) -> None:
    """A brand-new tool added to build_tool_manifest without also registering
    a _bounded_field (or an explicit no-list-field acknowledgment) fails
    this — the "future tool can't forget" property D2 promises."""
    manifest_names = {t["name"] for t in build_tool_manifest(config)}
    known = set(TOOL_BOUNDED_FIELDS) | {"graph_query", "test_dry_run", "memex_read"}
    assert manifest_names <= known, (
        f"tool(s) {manifest_names - known} added without a _bounded_field "
        f"registration or explicit no-list-field acknowledgment (D2)"
    )


# ---- AC-H-3 — element cap before byte ceiling ----


def test_central_bounds_applies_element_cap_then_byte_ceiling(enforcement: Enforcement) -> None:
    # Each item ~220KB; 6 of them (~1.3MB) would blow the absolute byte
    # ceiling (1 MiB) if it ran first, but 3 of them (~660KB, post
    # element-cap) comfortably fit. If the byte ceiling ran BEFORE the
    # element cap this would hard-fail instead of truncating — proving the
    # two run in the documented order.
    item = {"path": "a" * 220_000, "line": 1}
    result = {"edges": [dict(item) for _ in range(6)]}
    out = apply_central_bounds("graph_query", {"query": "callers_of:foo"}, result, enforcement)
    assert len(out["edges"]) == 3
    assert out["truncated"] is True


# ---- AC-H-4 — every tool/verb truncates+flags an over-cap fixture ----


def test_every_tool_and_verb_truncates_and_flags_over_cap(enforcement: Enforcement) -> None:
    tool_fixtures = {
        "view_file": {"lines": ["x\n"] * 6},
        "list_dir": {"entries": [{"name": f"f{i}"} for i in range(6)]},
        "search_text": {"matches": [{"path": "a", "line": i} for i in range(6)]},
        "search_symbol": {
            "definitions": [{"name": "foo"}] * 6,
            "references": [{"name": "foo"}] * 6,
        },
    }
    for tool, result in tool_fixtures.items():
        out = apply_central_bounds(tool, {}, dict(result), enforcement)
        assert out["truncated"] is True, f"{tool} did not truncate an over-cap fixture"
        assert out["more_available"] is True
        assert out["retry_hint"] == "narrower_scope"
        for field in TOOL_BOUNDED_FIELDS[tool]:
            assert len(out[field]) <= enforcement.config.max_bound_field_elements

    verb_fixtures = {
        "callers_of": {"edges": [{"path": "a", "line": i} for i in range(6)]},
        "definitions_of": {
            "definitions": [{"name": "foo"}] * 6,
            "references": [{"name": "foo"}] * 6,
        },
        "subclasses_of": {"edges": [{"name": f"Sub{i}"} for i in range(6)]},
    }
    for verb, result in verb_fixtures.items():
        out = apply_central_bounds(
            "graph_query", {"query": f"{verb}:Foo"}, dict(result), enforcement
        )
        assert out["truncated"] is True, f"graph_query:{verb} did not truncate an over-cap fixture"
        for field in GRAPH_QUERY_VERB_BOUNDED_FIELDS[verb]:
            assert len(out[field]) <= enforcement.config.max_bound_field_elements


# ---- AC-H-5 — the overflow contract ----


def test_overflow_truncate_and_flag_contract(enforcement: Enforcement) -> None:
    result = {"entries": [{"name": f"f{i}"} for i in range(10)]}
    out = apply_central_bounds("list_dir", {}, result, enforcement)
    assert out["truncated"] is True
    assert out["returned_count"] == 3
    assert out["more_available"] is True
    assert out["retry_hint"] == "narrower_scope"


# ---- AC-H-6 — absolute byte ceiling hard-fails ----


def test_absolute_byte_ceiling_hard_fails(enforcement: Enforcement) -> None:
    # A single element far larger than the absolute byte ceiling — element
    # truncation cannot rescue it (1 item is already under the element cap).
    huge = "x" * (enforcement.config.max_response_bytes * 2)
    result = {"lines": [huge]}
    with pytest.raises(ToolError) as exc:
        apply_central_bounds("view_file", {}, result, enforcement)
    assert exc.value.code == "RESPONSE_TOO_LARGE"


def test_absolute_byte_ceiling_does_not_false_positive_on_combined_per_tool_caps(
    enforcement: Enforcement,
) -> None:
    """Regression guard: test_dry_run independently caps stdout AND stderr at
    max_bytes_per_call, so a normal both-near-cap response legitimately runs
    to ~2x that. The absolute ceiling (max_response_bytes) must be a genuine
    backstop above this, not equal to max_bytes_per_call — reusing the
    latter here previously hard-failed a perfectly normal response."""
    cap = enforcement.config.max_bytes_per_call
    result = {
        "exit_code": 1,
        "stdout": "x" * cap,
        "stderr": "y" * cap,
        "truncated": True,
    }
    out = apply_central_bounds("test_dry_run", {}, dict(result), enforcement)
    assert out["stdout"] == "x" * cap
    assert out["stderr"] == "y" * cap


# ---- AC-H-7 / AC-H-8 — the two named regressions, end to end ----


async def test_search_symbol_is_bounded(
    config: Config, enforcement: Enforcement, memex: Memex
) -> None:
    for i in range(6):
        _rb(config.repo, f"app/m{i}.rb", f"class M{i}\n  def call\n  end\nend\n")
    code_graph = CodeGraph(repo=config.repo)
    code_graph.build()

    result = await dispatch_tool_call(
        "search_symbol", {"name": "call"}, config, enforcement, memex, code_graph
    )
    assert len(result["definitions"]) <= config.max_bound_field_elements
    assert result["truncated"] is True


async def test_graph_query_is_bounded(
    config: Config, enforcement: Enforcement, memex: Memex
) -> None:
    for i in range(6):
        _rb(
            config.repo,
            f"app/m{i}.rb",
            f"class M{i}\n  def call\n    record_vote(1)\n  end\nend\n",
        )
    code_graph = CodeGraph(repo=config.repo)
    code_graph.build()

    result = await dispatch_tool_call(
        "graph_query",
        {"query": "callers_of:record_vote"},
        config,
        enforcement,
        memex,
        code_graph,
    )
    assert len(result["edges"]) <= config.max_bound_field_elements
    assert result["truncated"] is True


async def test_search_symbol_still_works_under_cap(
    config: Config, enforcement: Enforcement, memex: Memex
) -> None:
    """Regression guard: the central cap must not touch a response that's
    already within bounds — no truncated flag, no data loss."""
    _rb(config.repo, "app/a.rb", "class Tallier\n  def call\n  end\nend\n")
    code_graph = CodeGraph(repo=config.repo)
    code_graph.build()

    result = await dispatch_tool_call(
        "search_symbol", {"name": "Tallier"}, config, enforcement, memex, code_graph
    )
    assert len(result["definitions"]) == 1
    assert "truncated" not in result


# ---- AC-DOC-6 — kind enum honesty ----


def test_search_symbol_kind_enum_superset_of_produced_kinds(config: Config) -> None:
    manifest = build_tool_manifest(config)
    search_symbol_tool = next(t for t in manifest if t["name"] == "search_symbol")
    enum = set(search_symbol_tool["inputSchema"]["properties"]["kind"]["enum"])
    assert set(PRODUCED_KINDS) <= enum
