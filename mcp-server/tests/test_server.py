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
from typing import Any

import pytest

from atlas_aci.codegraph import PRODUCED_KINDS, CodeGraph
from atlas_aci.config import DEFAULT_MAX_BOUND_FIELD_ELEMENTS, Config
from atlas_aci.enforcement import Enforcement, ToolError
from atlas_aci.memex import Memex
from atlas_aci.server import (
    GRAPH_QUERY_VERB_BOUNDED_FIELDS,
    TOOL_BOUNDED_FIELDS,
    apply_central_bounds,
    bounded_fields_for,
    build_server_state,
    build_tool_manifest,
    dispatch_tool_call,
)
from atlas_aci.tools.list_dir import list_dir
from atlas_aci.tools.search_text import search_text


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


# ---- NEW-2 — guard the query_limit <-> config-cap wiring (F-1 relapse) ----


def test_run_stdio_wires_query_limit_to_config_cap_plus_one(tmp_path: Path) -> None:
    """CodeGraph.__init__'s own default for query_limit is
    DEFAULT_MAX_BOUND_FIELD_ELEMENTS + 1 — a *module* constant — not
    Config.max_bound_field_elements + 1. `build_server_state` (what
    `run_stdio` calls) only agrees with the configured cap because it
    passes query_limit explicitly; nothing previously asserted that
    wiring survives a refactor. A default-constructed CodeGraph paired
    with a custom-cap Config would silently reopen F-1 at a different cap
    value — identical failure mode, one layer up. Uses a cap deliberately
    far from the module default so a regression to CodeGraph's bare
    default (which would silently disagree) fails this test rather than
    passing by coincidence."""
    repo = tmp_path / "repo"
    repo.mkdir()
    custom_cap = 57
    assert custom_cap != DEFAULT_MAX_BOUND_FIELD_ELEMENTS, "fixture must differ from the default"
    config = Config(repo=repo, memex_root=tmp_path / "memex", max_bound_field_elements=custom_cap)

    _enforcement, _memex, code_graph = build_server_state(config)

    assert code_graph.query_limit == custom_cap + 1
    assert code_graph.query_limit != DEFAULT_MAX_BOUND_FIELD_ELEMENTS + 1
    assert code_graph.read_only is True


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


def test_bounded_field_registry_covers_every_known_query_verb() -> None:
    """F-3: a brand-new graph_query verb is checked here mechanically,
    regardless of what it's named. The checker demonstrated that
    harden-gate.yml's content-marker heuristic (grepping for literal
    strings like "label_propagation") is evadable by simply choosing
    different identifiers. This test instead discovers the DSL's verb
    vocabulary from `codegraph.KNOWN_QUERY_VERBS` — the same constant
    `CodeGraph.query` dispatches against — so a verb cannot be reachable at
    all without appearing here, and therefore cannot dodge this check by
    naming alone. (Every verb today returns a list-valued field; a future
    purely-scalar verb would need the same kind of explicit
    no-list-field acknowledgment `test_dry_run`/`memex_read` get above.)"""
    from atlas_aci.codegraph import KNOWN_QUERY_VERBS

    missing = KNOWN_QUERY_VERBS - set(GRAPH_QUERY_VERB_BOUNDED_FIELDS)
    assert not missing, (
        f"graph_query verb(s) {missing} are dispatchable but have no "
        f"_bounded_field registration (D2/F-3)"
    )


def test_undispatched_known_verb_raises_rather_than_impersonating_a_stub(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NEW-3 (checker, second pass): CodeGraph.query()'s final branch used
    to be an unconditional `return` (no `if verb == "subclasses_of"` guard),
    so any future member of KNOWN_QUERY_VERBS added without a matching
    dispatch branch would silently fall through and return
    subclasses_of's empty-with-warning shape — impersonating a DIFFERENT
    verb's stub rather than failing loudly. A1 is expected to add verbs;
    this simulates exactly that (a verb declared reachable but never wired
    up) and asserts it now raises instead of returning a wrong-shaped
    success response."""
    import atlas_aci.codegraph as codegraph_module

    repo = tmp_path / "repo"
    repo.mkdir()
    graph = codegraph_module.CodeGraph(repo=repo)
    graph.build()

    monkeypatch.setattr(
        codegraph_module,
        "KNOWN_QUERY_VERBS",
        codegraph_module.KNOWN_QUERY_VERBS | {"widgets_of"},
    )
    with pytest.raises(NotImplementedError):
        graph.query("widgets_of:Anything")


# ---- R-1 — nested sub-field escape (candidates[] inside edges[]) ----
#
# The `_bounded_field` registry (AC-H-15) protects every top-level list
# field. A1 introduces a sub-field NESTED one level inside an already-capped
# field's own elements: an AMBIGUOUS graph_query edge's `candidates[]`
# (AC-A1-5, "the full ordered candidates[] attached, never dropped") is
# itself unbounded. These tests compute the expected post-cap shape
# independently of apply_central_bounds's own arithmetic (a fresh slice in
# the test), per the boundary-testing lesson the P0 pass learned four times.


def test_nested_bounded_field_registry_covers_every_verb_with_a_sub_list() -> None:
    from atlas_aci.server import GRAPH_QUERY_VERB_NESTED_BOUNDED_FIELDS

    assert GRAPH_QUERY_VERB_NESTED_BOUNDED_FIELDS["callers_of"] == {"edges": "candidates"}
    assert GRAPH_QUERY_VERB_NESTED_BOUNDED_FIELDS["subclasses_of"] == {"edges": "candidates"}


def test_candidates_subfield_is_capped_and_flagged_when_over_cap(enforcement: Enforcement) -> None:
    cap = enforcement.config.max_bound_field_elements  # 3, from the `config` fixture
    over_cap_candidates = [{"path": "a.rb", "line": i, "name": "Foo"} for i in range(cap + 3)]
    result = {
        "edges": [
            {
                "relation": "call",
                "confidence": "AMBIGUOUS",
                "source": {"path": "b.rb", "line": 1, "name": "call", "kind": "method"},
                "target": None,
                "candidates": over_cap_candidates,
            }
        ]
    }
    out = apply_central_bounds("graph_query", {"query": "callers_of:foo"}, result, enforcement)

    assert len(out["edges"]) == 1, "the top-level edges list itself was under cap"
    assert out["edges"][0]["candidates"] == over_cap_candidates[:cap], (
        "candidates[] must be capped on a whole-element boundary, computed independently here"
    )
    assert out["truncated"] is True
    assert "edges.candidates" in out["truncated_fields"]
    assert out["returned_count"]["edges.candidates"] == cap
    assert out["more_available"] is True
    assert out["retry_hint"] == "narrower_scope"


def test_candidates_subfield_exactly_at_cap_is_not_falsely_flagged(
    enforcement: Enforcement,
) -> None:
    cap = enforcement.config.max_bound_field_elements
    exact_candidates = [{"path": "a.rb", "line": i, "name": "Foo"} for i in range(cap)]
    result = {
        "edges": [
            {
                "relation": "call",
                "confidence": "AMBIGUOUS",
                "source": {"path": "b.rb", "line": 1, "name": "call", "kind": "method"},
                "target": None,
                "candidates": exact_candidates,
            }
        ]
    }
    out = apply_central_bounds("graph_query", {"query": "subclasses_of:Foo"}, result, enforcement)

    assert out["edges"][0]["candidates"] == exact_candidates
    assert "truncated" not in out, "an exact-fit candidates[] must not be flagged as truncated"


def test_candidates_subfield_under_cap_across_multiple_edges_not_flagged(
    enforcement: Enforcement,
) -> None:
    """Every edge's candidates[] individually under cap: no truncation, even
    though the tool returns several AMBIGUOUS edges at once."""
    cap = enforcement.config.max_bound_field_elements
    result = {
        "edges": [
            {
                "relation": "call",
                "confidence": "AMBIGUOUS",
                "source": {"path": "b.rb", "line": i, "name": "call", "kind": "method"},
                "target": None,
                "candidates": [{"path": "a.rb", "line": 1, "name": "Foo"}],
            }
            for i in range(cap - 1)
        ]
    }
    out = apply_central_bounds("graph_query", {"query": "callers_of:foo"}, result, enforcement)
    assert "truncated" not in out


def test_extracted_edge_with_null_candidates_is_unaffected(enforcement: Enforcement) -> None:
    """An EXTRACTED/INFERRED edge carries `candidates: None`, never a list
    (AC-A1-2/AC-A1-5 mutual exclusivity) — the nested cap must not choke on
    that shape."""
    result = {
        "edges": [
            {
                "relation": "call",
                "confidence": "EXTRACTED",
                "source": {"path": "b.rb", "line": 1, "name": "call", "kind": "method"},
                "target": {"path": "a.rb", "line": 2, "name": "Foo"},
                "candidates": None,
            }
        ]
    }
    out = apply_central_bounds("graph_query", {"query": "callers_of:foo"}, result, enforcement)
    assert "truncated" not in out
    assert out["edges"][0]["candidates"] is None


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
    assert out["truncated_fields"] == ["entries"]
    assert out["returned_count"] == {"entries": 3}
    assert out["more_available"] is True
    assert out["retry_hint"] == "narrower_scope"


def test_returned_count_is_per_field_not_summed(enforcement: Enforcement) -> None:
    """F-7: a multi-field tool's returned_count must be attributable to a
    specific field — a summed total conflates e.g. search_symbol's
    `definitions` (over cap) with `references` (under cap), making it
    impossible to tell which one actually lost data."""
    result = {
        "definitions": [{"name": "foo"}] * 6,  # over cap (3)
        "references": [{"name": "foo"}] * 2,  # under cap
    }
    out = apply_central_bounds("search_symbol", {}, result, enforcement)
    assert out["truncated"] is True
    assert out["truncated_fields"] == ["definitions"]
    assert out["returned_count"] == {"definitions": 3, "references": 2}


# ---- F-6 — unify tool-level overflow vocabularies onto `truncated` ----
#
# list_dir/search_text signal their own cap via a literal `overflow: true`
# key; view_file signals pagination via `next_cursor`. Each fixture below
# stays UNDER the central element cap (3) on purpose, so it's specifically
# the tool-level-signal-promotion path being tested here, not central
# cap_list_field's own (already-covered) detection.


def test_list_dir_own_overflow_signal_promotes_to_truncated(enforcement: Enforcement) -> None:
    result = {
        "path": ".",
        "entries": [{"name": "a"}, {"name": "b"}],  # 2 items, under cap (3)
        "overflow": True,
        "message": "Listing capped at 2 entries. Use a glob to narrow.",
    }
    out = apply_central_bounds("list_dir", {}, result, enforcement)
    assert out["truncated"] is True
    assert out["truncated_fields"] == ["entries"]
    assert out["returned_count"] == {"entries": 2}
    assert out["more_available"] is True
    assert out["retry_hint"] == "narrower_scope"
    # The tool's own vocabulary survives untouched alongside the unified one.
    assert out["overflow"] is True
    assert out["message"] == "Listing capped at 2 entries. Use a glob to narrow."


def test_search_text_own_overflow_signal_promotes_to_truncated(enforcement: Enforcement) -> None:
    result = {
        "matches": [{"path": "a.rb", "line": 1}],  # 1 item, under cap (3)
        "overflow": True,
        "message": "Search hit the cap of 1 matches. Narrow scope or use search_symbol.",
    }
    out = apply_central_bounds("search_text", {}, result, enforcement)
    assert out["truncated"] is True
    assert out["truncated_fields"] == ["matches"]
    assert out["returned_count"] == {"matches": 1}
    assert out["more_available"] is True
    assert out["retry_hint"] == "narrower_scope"


def test_view_file_bare_next_cursor_does_not_trigger_truncated(enforcement: Enforcement) -> None:
    """NEW-1 (checker, second pass, F-6 follow-up): a fully-satisfied
    window that simply doesn't reach EOF is NOT a truncation of the
    *request* — `next_cursor` alone must never flip `truncated`. An
    earlier version of this test asserted the opposite and passed,
    encoding the bug the checker reproduced: `view_file` on lines 2-4 of a
    10-line file (a complete, un-clamped window) returned `truncated:
    true` with `retry_hint: narrower_scope`, which is wrong on both counts
    — nothing was clamped, and the correct next action is to page via
    `next_cursor`, not narrow the query."""
    result = {
        "path": "app/big.rb",
        "start_line": 2,
        "end_line": 4,
        "lines": ["a\n", "b\n", "c\n"],  # exactly what was requested — no clamping
        "next_cursor": 5,
        "total_lines": 10,
    }
    out = apply_central_bounds("view_file", {}, result, enforcement)
    assert "truncated" not in out
    assert "truncated_fields" not in out
    assert "more_available" not in out
    # The pagination contract itself is untouched — it's the correct,
    # sufficient signal for "the file continues".
    assert out["next_cursor"] == 5
    assert out["total_lines"] == 10


def test_view_file_overflow_signal_promotes_to_truncated(enforcement: Enforcement) -> None:
    """The REQUEST itself was clamped (e.g. asked for more lines than
    max_lines_per_view) — genuine "you asked for more than you got", and
    must still flag `truncated` (mirrors list_dir/search_text's own literal
    `overflow` key — the one tool-level signal apply_central_bounds
    promotes)."""
    result = {
        "path": "app/big.rb",
        "start_line": 1,
        "end_line": 2,
        "lines": ["a\n", "b\n"],  # 2 items, under the central cap (3)
        "overflow": True,
        "next_cursor": 3,
        "total_lines": 500,
    }
    out = apply_central_bounds("view_file", {}, result, enforcement)
    assert out["truncated"] is True
    assert out["truncated_fields"] == ["lines"]
    assert out["returned_count"] == {"lines": 2}
    assert out["more_available"] is True
    assert out["retry_hint"] == "narrower_scope"
    # The pagination contract survives untouched alongside the unified one.
    assert out["next_cursor"] == 3
    assert out["total_lines"] == 500


async def test_view_file_real_window_under_cap_is_not_falsely_truncated(
    config: Config, enforcement: Enforcement, memex: Memex
) -> None:
    """End-to-end regression for NEW-1, through the real view_file tool
    (not just a synthetic fixture — the checker's point that the bug was
    "tested-in" applies to relying on fixtures alone). Reading a middle
    window of a longer file must not report `truncated`."""
    lines = [f"line{i}\n" for i in range(1, 11)]  # 10 lines
    (config.repo / "big.txt").write_text("".join(lines))
    code_graph = CodeGraph(repo=config.repo)

    result = await dispatch_tool_call(
        "view_file",
        {"path": "big.txt", "start_line": 2, "end_line": 4},
        config,
        enforcement,
        memex,
        code_graph,
    )
    assert result["lines"] == ["line2\n", "line3\n", "line4\n"]
    assert "truncated" not in result
    assert result["next_cursor"] == 5
    assert result["total_lines"] == 10


async def test_view_file_real_request_exceeding_max_lines_is_truncated(tmp_path: Path) -> None:
    """End-to-end: a request whose window exceeds max_lines_per_view IS a
    genuine truncation of the request and must be flagged. Uses its own
    Config (default max_bound_field_elements=200) rather than the shared
    `config` fixture (max_bound_field_elements=3) — that fixture's small
    central cap would *also* fire here and confound which cap actually
    produced the truncation; isolating view_file's own max_lines_per_view
    overflow is the point of this test."""
    repo = tmp_path / "repo"
    repo.mkdir()
    isolated_config = Config(repo=repo, memex_root=tmp_path / "memex")
    isolated_enforcement = Enforcement(isolated_config)
    isolated_memex = Memex(isolated_config.memex_root)

    lines = [f"line{i}\n" for i in range(1, 251)]  # 250 lines
    (repo / "huge.txt").write_text("".join(lines))
    code_graph = CodeGraph(repo=repo)

    result = await dispatch_tool_call(
        "view_file",
        {"path": "huge.txt", "start_line": 1, "end_line": 250},
        isolated_config,
        isolated_enforcement,
        isolated_memex,
        code_graph,
    )
    assert len(result["lines"]) == isolated_config.max_lines_per_view
    assert result["truncated"] is True
    assert result["truncated_fields"] == ["lines"]
    assert result["retry_hint"] == "narrower_scope"


# ---- NEW-1 residual (checker, round 2): the invariant, tested directly ----
#
# `overflow` <=> content the caller asked for was withheld. `next_cursor`
# <=> more file exists beyond what was returned (and must never point past
# EOF). These are INDEPENDENT facts — a fully-satisfied window can still
# have more file after it (row "200,1-100" below), and a nominally-clamped
# request can withhold nothing at all if the file itself is short (rows
# "10,1-5000" and "50,1-200" — the exact false-positive the checker
# reproduced against the prior fix: `cap_lines` clamps the *requested*
# range to max_lines_per_view with no knowledge of the file's real length,
# so treating that clamp alone as "overflow" reports withholding — and a
# next_cursor past EOF — for content that was never there to withhold).
#
# Expected values below are computed independently of view_file.py's own
# arithmetic (a fresh formula in the test, not a call into the module under
# test), per the instruction that the assertion must not inherit the
# implementation's own math.


def _expected_view_file_outcome(
    total_lines: int, start: int, end: int, max_lines_per_view: int
) -> tuple[int, bool, int | None, int | None]:
    """Returns (expected_got, expected_overflow, expected_next_cursor,
    expected_total_lines_field), derived from the stated invariant alone."""
    capped_end = min(end, start + max_lines_per_view - 1)
    effective_end = min(end, total_lines)
    returned_end = min(capped_end, total_lines)
    expected_got = returned_end - start + 1
    expected_overflow = capped_end < effective_end
    if capped_end < total_lines:
        return expected_got, expected_overflow, capped_end + 1, total_lines
    return expected_got, expected_overflow, None, None


@pytest.mark.parametrize(
    ("total_lines", "start", "end"),
    [
        (10, 1, 5000),  # request nominally clamped, file far shorter: nothing withheld
        (50, 1, 200),  # same shape, different sizes
        (200, 1, 5000),  # request clamped AND file long enough: genuine withholding
        (200, 1, 100),  # request exactly satisfied, more file remains after
        (10, 2, 4),  # mid-file complete window (the original NEW-1 fixture)
        (10, 1, 10),  # exact full-file read, nothing more, nothing withheld
    ],
)
async def test_view_file_overflow_and_next_cursor_match_the_invariant(
    tmp_path: Path, total_lines: int, start: int, end: int
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    isolated_config = Config(repo=repo, memex_root=tmp_path / "memex")
    isolated_enforcement = Enforcement(isolated_config)
    isolated_memex = Memex(isolated_config.memex_root)
    code_graph = CodeGraph(repo=repo)

    lines = [f"line{i}\n" for i in range(1, total_lines + 1)]
    (repo / "f.txt").write_text("".join(lines))

    expected_got, expected_overflow, expected_next_cursor, expected_total = (
        _expected_view_file_outcome(total_lines, start, end, isolated_config.max_lines_per_view)
    )

    result = await dispatch_tool_call(
        "view_file",
        {"path": "f.txt", "start_line": start, "end_line": end},
        isolated_config,
        isolated_enforcement,
        isolated_memex,
        code_graph,
    )

    assert len(result["lines"]) == expected_got
    assert bool(result.get("truncated", False)) is expected_overflow
    assert result.get("next_cursor") == expected_next_cursor
    assert result.get("total_lines") == expected_total
    if expected_next_cursor is not None:
        assert expected_next_cursor <= total_lines, "a cursor must never point past EOF"


# ---- Same invariant, checked against list_dir / search_text / test_dry_run ----
#
# view_file's bug was a *structural* clamp (cap_lines computes a capped end
# from the requested range alone, with no knowledge of the file's real
# length) standing in for the real invariant ("content withheld"). The
# question for the other three tools with their own overflow signal: is
# theirs grounded in the same way, or is it already computed from what
# actually exists? Empirically checked at the exact cap boundary below —
# not just reasoned about — because that is exactly the boundary the
# previous (incomplete) view_file fix got wrong.


async def test_list_dir_exact_cap_entries_is_not_falsely_flagged(tmp_path: Path) -> None:
    """list_dir's `overflow` is set only inside the iteration loop, exactly
    when a candidate BEYOND the cap is rejected — it never fires from a
    count computed independently of what's actually there. A directory
    with precisely `cap` matching entries must not be flagged."""
    repo = tmp_path / "repo"
    repo.mkdir()
    config = Config(repo=repo, memex_root=tmp_path / "memex", max_entries_per_list=5)
    enforcement = Enforcement(config)
    for i in range(5):  # exactly cap
        (repo / f"f{i}.txt").write_text("x")

    result = await list_dir({"path": "."}, config, enforcement)

    assert len(result["entries"]) == 5
    assert "overflow" not in result
    out = apply_central_bounds("list_dir", {}, result, enforcement)
    assert "truncated" not in out


async def test_list_dir_cap_plus_one_entries_is_correctly_flagged(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    config = Config(repo=repo, memex_root=tmp_path / "memex", max_entries_per_list=5)
    enforcement = Enforcement(config)
    for i in range(6):  # cap + 1
        (repo / f"f{i}.txt").write_text("x")

    result = await list_dir({"path": "."}, config, enforcement)

    assert len(result["entries"]) == 5
    assert result["overflow"] is True
    out = apply_central_bounds("list_dir", {}, result, enforcement)
    assert out["truncated"] is True


async def test_search_text_exact_cap_matches_is_not_falsely_flagged(tmp_path: Path) -> None:
    """search_text's `overflow` is computed from the actual accumulated
    match count returned by rg (`len(matches) > limit`), not from a
    requested-vs-actual proxy. A file with exactly `limit` matching lines
    must not be flagged."""
    repo = tmp_path / "repo"
    repo.mkdir()
    config = Config(repo=repo, memex_root=tmp_path / "memex", max_matches_per_search=5)
    enforcement = Enforcement(config)
    (repo / "f.txt").write_text("\n".join(["needle"] * 5) + "\n")  # exactly limit

    result = await search_text({"pattern": "needle", "scope": "f.txt"}, config, enforcement)

    assert len(result["matches"]) == 5
    assert "overflow" not in result
    out = apply_central_bounds("search_text", {}, result, enforcement)
    assert "truncated" not in out


async def test_search_text_cap_plus_one_matches_is_correctly_flagged(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    config = Config(repo=repo, memex_root=tmp_path / "memex", max_matches_per_search=5)
    enforcement = Enforcement(config)
    (repo / "f.txt").write_text("\n".join(["needle"] * 6) + "\n")  # cap + 1

    result = await search_text({"pattern": "needle", "scope": "f.txt"}, config, enforcement)

    assert len(result["matches"]) == 5
    assert result["overflow"] is True
    out = apply_central_bounds("search_text", {}, result, enforcement)
    assert out["truncated"] is True


class _FakeCompletedProcess:
    def __init__(self, stdout: bytes, stderr: bytes = b""):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = 0

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

    def kill(self) -> None:  # pragma: no cover - not exercised on the happy path
        pass

    async def wait(self) -> None:  # pragma: no cover
        return None


async def test_test_dry_run_truncated_reflects_actual_bytes_not_a_proxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """test_dry_run's `truncated` is `len(stdout_bytes) > cap or
    len(stderr_bytes) > cap` — computed directly from the real subprocess
    output's byte length, never from a requested/structural proxy.
    Confirmed at the exact boundary: exactly `cap` bytes of stdout is NOT
    flagged; `cap + 1` bytes IS."""
    import asyncio

    from atlas_aci.tools import test_dry_run as test_dry_run_module

    repo = tmp_path / "repo"
    repo.mkdir()
    config = Config(repo=repo, memex_root=tmp_path / "memex", max_bytes_per_call=10)
    enforcement = Enforcement(config)
    test_file = repo / "test_x.py"
    test_file.write_text("def test_x():\n    pass\n")

    def _exec_with(stdout: bytes):
        async def _fake_exec(*_args, **_kwargs):
            return _FakeCompletedProcess(stdout)

        return _fake_exec

    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", _exec_with(b"x" * config.max_bytes_per_call)
    )
    exact = await test_dry_run_module.test_dry_run({"path": "test_x.py"}, config, enforcement)
    assert exact["truncated"] is False

    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", _exec_with(b"x" * (config.max_bytes_per_call + 1))
    )
    over = await test_dry_run_module.test_dry_run({"path": "test_x.py"}, config, enforcement)
    assert over["truncated"] is True


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
    # query_limit mirrors run_stdio's production wiring (cap+1, never the
    # bare cap — F-1) so this end-to-end test exercises the same path a
    # real deployment does, not just the default query_limit.
    code_graph = CodeGraph(repo=config.repo, query_limit=config.max_bound_field_elements + 1)
    code_graph.build()

    result = await dispatch_tool_call(
        "search_symbol", {"name": "call"}, config, enforcement, memex, code_graph
    )
    assert len(result["definitions"]) <= config.max_bound_field_elements
    assert result["truncated"] is True


async def test_graph_query_is_bounded(
    config: Config, enforcement: Enforcement, memex: Memex
) -> None:
    # A1: callers_of now queries the materialized `edges` table, which only
    # carries a row for a reference that resolves to >=1 candidate
    # (AC-A1-6) — so the fixture must define `record_vote` somewhere for
    # its 6 call sites to become edges at all (a single definition ->
    # candidate_count == 1 -> each call site resolves INFERRED, since these
    # are bare unqualified calls).
    _rb(config.repo, "app/hub.rb", "class Hub\n  def record_vote(n)\n  end\nend\n")
    for i in range(6):
        _rb(
            config.repo,
            f"app/m{i}.rb",
            f"class M{i}\n  def call\n    record_vote(1)\n  end\nend\n",
        )
    code_graph = CodeGraph(repo=config.repo, query_limit=config.max_bound_field_elements + 1)
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


# ---- F-1 regression: SQL-limit / central-cap boundary collision ----
#
# codegraph.py's `refs` query used a hardcoded `LIMIT 200` that, at
# production defaults, exactly equals `max_bound_field_elements` (also 200).
# SQL silently pre-truncates to exactly the cap, so the central chokepoint
# sees `len(items) == cap`, never `> cap`, and sets no flag — a symbol with
# 250 references returned exactly 200 rows with `truncated: False`. This
# parametrizes the boundary (cap-1, cap, cap+1 rows actually present) so the
# exact-fit case (cap) and the real-overflow case (cap+1) are both pinned.


async def test_references_at_sql_limit_boundary_are_not_silently_swallowed(
    config: Config, enforcement: Enforcement, memex: Memex
) -> None:
    cap = config.max_bound_field_elements  # 3, from the `config` fixture
    query_limit = cap + 1

    # One file, one method, three callees invoked cap-1 / cap / cap+1 times —
    # isolates the "references" field (no matching definitions exist for
    # these callee names, so `definitions` stays empty for every case).
    calls_under = "\n    ".join(["callee_under()"] * (cap - 1))
    calls_exact = "\n    ".join(["callee_exact()"] * cap)
    calls_over = "\n    ".join(["callee_over()"] * (cap + 1))
    body = (
        f"class Hub\n  def call\n    {calls_under}\n    {calls_exact}\n"
        f"    {calls_over}\n  end\nend\n"
    )
    _rb(config.repo, "app/hub.rb", body)
    code_graph = CodeGraph(repo=config.repo, query_limit=query_limit)
    code_graph.build()

    # cap-1 references present: nothing hidden, nothing flagged.
    under = await dispatch_tool_call(
        "search_symbol", {"name": "callee_under"}, config, enforcement, memex, code_graph
    )
    assert len(under["references"]) == cap - 1
    assert "truncated" not in under

    # Exactly cap references present: an exact fit is NOT an overflow — this
    # is the boundary the collision bug got wrong in the other direction
    # (asserting the fix doesn't now over-fire on a case with nothing hidden).
    exact = await dispatch_tool_call(
        "search_symbol", {"name": "callee_exact"}, config, enforcement, memex, code_graph
    )
    assert len(exact["references"]) == cap
    assert "truncated" not in exact

    # cap+1 references present: this is the exact scenario that used to
    # silently return `cap` rows with no signal at all. Must now be flagged.
    over = await dispatch_tool_call(
        "search_symbol", {"name": "callee_over"}, config, enforcement, memex, code_graph
    )
    assert len(over["references"]) == cap
    assert over["truncated"] is True
    assert over["truncated_fields"] == ["references"]
    assert over["returned_count"] == {"definitions": 0, "references": cap}
    assert over["more_available"] is True
    assert over["retry_hint"] == "narrower_scope"


async def test_callers_of_at_sql_limit_boundary_are_not_silently_swallowed(
    config: Config, enforcement: Enforcement, memex: Memex
) -> None:
    """Same boundary, through graph_query's callers_of — which had no SQL
    LIMIT at all (F-2), not just a colliding one."""
    cap = config.max_bound_field_elements
    query_limit = cap + 1

    # A1: callers_of now queries the materialized `edges` table, which only
    # carries a row for a reference resolving to >=1 candidate (AC-A1-6) —
    # so both callees must be defined somewhere for their call sites to
    # become edges (each resolves to exactly 1 candidate -> INFERRED, bare
    # unqualified calls).
    _rb(
        config.repo,
        "app/targets.rb",
        "class Targets\n  def callee_exact\n  end\n  def callee_over\n  end\nend\n",
    )
    calls_exact = "\n    ".join(["callee_exact()"] * cap)
    calls_over = "\n    ".join(["callee_over()"] * (cap + 1))
    _rb(
        config.repo,
        "app/hub.rb",
        f"class Hub\n  def call\n    {calls_exact}\n    {calls_over}\n  end\nend\n",
    )
    code_graph = CodeGraph(repo=config.repo, query_limit=query_limit)
    code_graph.build()

    exact = await dispatch_tool_call(
        "graph_query",
        {"query": "callers_of:callee_exact"},
        config,
        enforcement,
        memex,
        code_graph,
    )
    assert len(exact["edges"]) == cap
    assert "truncated" not in exact

    over = await dispatch_tool_call(
        "graph_query",
        {"query": "callers_of:callee_over"},
        config,
        enforcement,
        memex,
        code_graph,
    )
    assert len(over["edges"]) == cap
    assert over["truncated"] is True
    assert over["truncated_fields"] == ["edges"]
    assert over["returned_count"] == {"edges": cap}
    assert over["more_available"] is True
    assert over["retry_hint"] == "narrower_scope"


async def test_search_symbol_still_works_under_cap(
    config: Config, enforcement: Enforcement, memex: Memex
) -> None:
    """Regression guard: the central cap must not touch a response that's
    already within bounds — no truncated flag, no data loss."""
    _rb(config.repo, "app/a.rb", "class Tallier\n  def call\n  end\nend\n")
    code_graph = CodeGraph(repo=config.repo, query_limit=config.max_bound_field_elements + 1)
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


# ---- AC-H-18 — the mechanical anchor (checker MINOR-2 / condition 1) ----
#
# The frozen criterion names this exact test:
# `test_server.py::test_truncation_signal_iff_content_withheld`. Before this
# commit that name existed nowhere in the repository's history — only inside
# `acceptance-criteria.md` itself, a pointer to nothing. That is precisely
# the "documented but not delivered" class of defect this release exists to
# eliminate, so this test IS the mechanical anchor from here on, not a
# restatement of tests that already existed.
#
# The invariant (verbatim from AC-H-18): for every tool and every
# graph_query verb, a truncation signal SHALL be set if and only if content
# that both exists and was requested was withheld; and no continuation
# cursor SHALL point beyond the end of the available content. Two
# independent halves:
#
#   (1) "truncated iff withheld" — exercised at cap-1/cap/cap+1 across every
#       tool, every graph_query verb (including `subclasses_of`), and the
#       nested `edges[].candidates` sub-field (R-1) — via `apply_central_bounds`
#       directly, the actual mechanism that enforces the invariant.
#   (2) "no cursor past the end" — exercised end-to-end through
#       `dispatch_tool_call` against `view_file` (the one tool with a
#       continuation cursor today), reusing `_expected_view_file_outcome`'s
#       already-independent formula (defined above) rather than duplicating
#       it — a real regression on either half fails this single test.
#
# Every expected value is computed from a fresh formula here, never by
# calling into `apply_central_bounds`/the tool code itself.

# (tool_name, static_extra_fields, field_under_test) — simple single-field
# and multi-field list-bearing tools. `static_extra_fields` pins any OTHER
# bounded field of a multi-field tool at a small, always-safe count so only
# the field under test can trip the invariant.
_ANCHOR_TOOL_SCENARIOS: list[tuple[str, dict[str, list[dict[str, str]]], str]] = [
    ("view_file", {}, "lines"),
    ("list_dir", {}, "entries"),
    ("search_text", {}, "matches"),
    ("search_symbol", {"references": [{"name": "safe"}]}, "definitions"),
    ("search_symbol", {"definitions": [{"name": "safe"}]}, "references"),
]

# (query_string, static_extra_fields, field_under_test) — every graph_query
# verb, including subclasses_of (AC-A1-7/AC-A1-10).
_ANCHOR_VERB_SCENARIOS: list[tuple[str, dict[str, list[dict[str, str]]], str]] = [
    ("callers_of:Foo", {}, "edges"),
    ("definitions_of:Foo", {"references": [{"name": "safe"}]}, "definitions"),
    ("definitions_of:Foo", {"definitions": [{"name": "safe"}]}, "references"),
    ("subclasses_of:Foo", {}, "edges"),
]


def _anchor_list_item(field: str, i: int) -> Any:
    if field in ("definitions", "references", "entries"):
        return {"name": f"x{i}"}
    if field == "matches":
        return {"path": "a.rb", "line": i}
    if field == "lines":
        return f"line{i}\n"
    if field == "edges":
        return {
            "relation": "call",
            "confidence": "INFERRED",
            "source": {"path": "a.rb", "line": i, "name": None, "kind": None},
            "target": {"path": "b.rb", "line": i, "name": "x"},
            "candidates": None,
        }
    raise AssertionError(f"unhandled field {field!r} in anchor test fixture")


def _assert_truncation_iff_withheld(
    out: dict[str, Any], field: str, cap: int, expected_withheld: bool
) -> None:
    """The invariant, checked directly: `truncated`/`field in
    truncated_fields`/`more_available`/`retry_hint` present if and only if
    `expected_withheld` — never proxied by whether the request merely
    *looked* clamped (the exact F-1/NEW-1 defect class)."""
    assert bool(out.get("truncated")) is expected_withheld
    truncated_fields = out.get("truncated_fields", [])
    if expected_withheld:
        assert field in truncated_fields
        assert out.get("more_available") is True
        assert out.get("retry_hint") == "narrower_scope"
        assert len(out[field]) == cap, "content withheld must still cap at an element boundary"
    else:
        assert field not in truncated_fields


@pytest.mark.parametrize(
    "requested_offset", [-1, 0, 1], ids=["cap_minus_1", "at_cap", "cap_plus_1"]
)
async def test_truncation_signal_iff_content_withheld(
    tmp_path: Path, enforcement: Enforcement, requested_offset: int
) -> None:
    cap = enforcement.config.max_bound_field_elements
    n = cap + requested_offset
    expected_withheld = n > cap  # independent of any implementation arithmetic

    # ---- Half 1: every tool, every verb, and the nested candidates[] ----

    for tool, extra, field in _ANCHOR_TOOL_SCENARIOS:
        result = dict(extra)
        result[field] = [_anchor_list_item(field, i) for i in range(n)]
        out = apply_central_bounds(tool, {}, result, enforcement)
        _assert_truncation_iff_withheld(out, field, cap, expected_withheld)

    for query, extra, field in _ANCHOR_VERB_SCENARIOS:
        result = dict(extra)
        result[field] = [_anchor_list_item(field, i) for i in range(n)]
        out = apply_central_bounds("graph_query", {"query": query}, result, enforcement)
        _assert_truncation_iff_withheld(out, field, cap, expected_withheld)

    # Nested edges[].candidates (R-1) — for BOTH callers_of and subclasses_of,
    # since AC-A1-7 gave subclasses_of the identical AMBIGUOUS/candidates
    # shape callers_of has.
    for verb in ("callers_of", "subclasses_of"):
        nested_result = {
            "edges": [
                {
                    "relation": "call",
                    "confidence": "AMBIGUOUS",
                    "source": {"path": "a.rb", "line": 1, "name": None, "kind": None},
                    "target": None,
                    "candidates": [{"path": "c.rb", "line": i, "name": "c"} for i in range(n)],
                }
            ]
        }
        out = apply_central_bounds(
            "graph_query", {"query": f"{verb}:Foo"}, nested_result, enforcement
        )
        assert len(out["edges"]) == 1, "the outer edges list itself is under cap in this scenario"
        nested_key = "edges.candidates"
        assert bool(out.get("truncated")) is expected_withheld
        if expected_withheld:
            assert nested_key in out.get("truncated_fields", [])
            assert len(out["edges"][0]["candidates"]) == cap
            assert out.get("more_available") is True
            assert out.get("retry_hint") == "narrower_scope"
        else:
            assert nested_key not in out.get("truncated_fields", [])
            assert len(out["edges"][0]["candidates"]) == n

    # ---- Half 2: no continuation cursor ever points past the end ----
    # Only run once (not per requested_offset) — this half exercises a
    # different axis (file length vs. requested window) already covering
    # under/at/over-cap shapes on its own; re-running it per offset would
    # only repeat identical assertions.
    if requested_offset != 0:
        return

    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)  # `enforcement`'s own `config` fixture may already own tmp_path/repo
    isolated_config = Config(repo=repo, memex_root=tmp_path / "memex")
    isolated_enforcement = Enforcement(isolated_config)
    isolated_memex = Memex(isolated_config.memex_root)
    code_graph = CodeGraph(repo=repo)

    for total_lines, start, end in (
        (10, 1, 5000),  # request nominally clamped, file far shorter: nothing withheld
        (200, 1, 5000),  # request clamped AND file long enough: genuine withholding
        (200, 1, 100),  # request exactly satisfied, more file remains after
        (10, 1, 10),  # exact full-file read: nothing more, nothing withheld
    ):
        lines = [f"line{i}\n" for i in range(1, total_lines + 1)]
        (repo / "f.txt").write_text("".join(lines))

        expected_got, expected_overflow, expected_next_cursor, expected_total = (
            _expected_view_file_outcome(total_lines, start, end, isolated_config.max_lines_per_view)
        )

        result = await dispatch_tool_call(
            "view_file",
            {"path": "f.txt", "start_line": start, "end_line": end},
            isolated_config,
            isolated_enforcement,
            isolated_memex,
            code_graph,
        )

        assert len(result["lines"]) == expected_got
        assert bool(result.get("truncated", False)) is expected_overflow
        assert result.get("next_cursor") == expected_next_cursor
        if expected_next_cursor is not None:
            assert expected_next_cursor <= total_lines, "a cursor must never point past EOF"
        assert result.get("total_lines") == expected_total
