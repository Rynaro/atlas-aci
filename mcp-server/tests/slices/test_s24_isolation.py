"""ACI-S24 invariance gates: the opt-in Rails adapter changes nothing it does not own.

G-OFF, G-ONOFF, G-ENVELOPE, G-MCP, G-NOLEAK and G-QUERY over real builds of the Rails fixture
(and the Python coverage fixture, which has no routes). The base-image comparison (G-IMAGE)
runs outside pytest through ``.spectra/evidence/aci-s24/compare_graphs.py``.
"""

import asyncio
import hashlib
import json
import shutil
from pathlib import Path

from click.testing import CliRunner
from test_s24_support import (
    PROJECT,
    R,
    built,
    edit,
    facts,
    manifest_value,
    rails_records,
    rebuilt,
    records,
    symbol_entity,
    table_counts,
    workspace,
)

from atlas_aci.__main__ import cli
from atlas_aci.codegraph import KNOWN_EDGE_RELATIONS, CodeGraph
from atlas_aci.config import Config
from atlas_aci.enforcement import READ_ONLY_TOOLS
from atlas_aci.frameworks import rails_routes
from atlas_aci.protocol import results
from atlas_aci.query import admission, impact, impact_admission, paging
from atlas_aci.query import diff as symbol_diff
from atlas_aci.query.diff import Side
from atlas_aci.server import build_server_state, call_tool_result
from atlas_aci.storage import evidence as ev

BASE_TOOLS_SHA256 = "94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56"
BASE_TOOLS_BYTES = 1631472
COVERAGE_PROJECT = Path(__file__).parents[1] / "fixtures/coverage/project"


def inputs_of(graph):
    with graph.reader_lease() as lease:
        return json.loads(
            lease.db.execute("SELECT value FROM manifest WHERE key='generation_inputs'").fetchone()[
                0
            ]
        )


def pair(tmp_path, repo=None):
    repo = repo or workspace(tmp_path)
    _r, on = built(tmp_path, root="on", repo=repo)
    _r, off = built(tmp_path, root="off", repo=repo, frameworks=())
    return repo, on, off


def ids_by_type(rows):
    result: dict[str, set[str]] = {}
    for item in rows:
        result.setdefault(item["type"], set()).add(item["id"])
    return result


def without_snapshot(inputs):
    return {k: v for k, v in inputs.items() if k != "snapshot"}


# ---- G-OFF ----
def test_a_build_without_the_flag_has_no_rails_trace(tmp_path):
    _repo, _on, off = pair(tmp_path)
    assert records(off) and rails_records(off) == []
    for item in records(off):
        assert item.get("kind") != "rails_route" and item.get("relation") != "routes_to"
        assert "rails" not in (item.get("fingerprint") or {})
    assert manifest_value(off) is None
    assert "frameworks" not in json.dumps(inputs_of(off))
    assert "rails" not in json.dumps(inputs_of(off))


def test_a_repository_without_routes_builds_identically_on_and_off(tmp_path):
    repo = workspace(tmp_path)
    (repo / R).unlink()
    _repo, on, off = pair(tmp_path, repo)
    assert inputs_of(on) == inputs_of(off) and inputs_of(on)["snapshot"]["state"] == "available"
    assert [r["id"] for r in records(on)] == [r["id"] for r in records(off)]
    assert json.loads(manifest_value(on))["state"] == "absent" and manifest_value(off) is None
    # A Python repository: nothing to read at all, the whole graph is unchanged.
    python = tmp_path / "python"
    shutil.copytree(COVERAGE_PROJECT, python)
    _r, py_on = built(tmp_path, root="py-on", repo=python)
    _r, py_off = built(tmp_path, root="py-off", repo=python, frameworks=())
    assert records(py_on) and [r["id"] for r in records(py_on)] == [
        r["id"] for r in records(py_off)
    ]
    assert inputs_of(py_on) == inputs_of(py_off)
    assert table_counts(py_on) == table_counts(py_off) and any(table_counts(py_on).values())


# ---- G-ONOFF ----
def test_on_minus_rails_owned_equals_off_for_every_record_type(tmp_path):
    _repo, on, off = pair(tmp_path)
    owned = rails_routes.record_ids(records(on))
    assert len(owned) == 106
    on_ids, off_ids = ids_by_type(records(on)), ids_by_type(records(off))
    assert set(on_ids) == set(off_ids)
    for kind, found in on_ids.items():
        assert found - owned == off_ids[kind], kind
    assert {k: len(v & owned) for k, v in on_ids.items() if v & owned} == {
        "assertion": 35,
        "evidence": 35,
        "occurrence": 35,
        "producer": 1,
    }
    with on.reader_lease() as a, off.reader_lease() as b:
        projections_on = ids_by_type(on._projection_records(a.db))
        projections_off = ids_by_type(off._projection_records(b.db))
    assert projections_on == projections_off and projections_on["projection"]
    assert table_counts(on) == table_counts(off) and any(table_counts(on).values())
    on_inputs, off_inputs = inputs_of(on), inputs_of(off)
    assert without_snapshot(on_inputs) == without_snapshot(off_inputs)
    assert on_inputs["snapshot"] != off_inputs["snapshot"]  # the snapshot binds the new records
    assert on_inputs["retrieval"]["document_digest"] == off_inputs["retrieval"]["document_digest"]
    assert "rails_routes" not in json.dumps(on_inputs)


def test_incremental_toggle_stays_affected_and_matches_fresh_builds(tmp_path):
    repo, on, off = pair(tmp_path)
    graph, stats = rebuilt(repo, tmp_path, "toggle", since="x")  # first build in this root
    assert rails_records(graph)
    steps = [((), "off"), (("rails",), "on"), ((), "off"), (("rails",), "on")]
    for frameworks, label in steps:
        graph, stats = rebuilt(repo, tmp_path, "toggle", frameworks=frameworks, since="x")
        resolution = stats["binding_resolution"]
        assert resolution["mode"] == "affected", (label, resolution)
        assert resolution["reason"] == "complete_dependency_match", (label, resolution)
        assert resolution["affected_units"] == 0, (label, resolution)
        expected = on if frameworks else off
        assert [r["id"] for r in records(graph)] == [r["id"] for r in records(expected)]
        assert (manifest_value(graph) is not None) == bool(frameworks)


# ---- G-ENVELOPE ----
def test_envelope_minimums_and_relation_sets_are_the_base_values():
    assert admission.MINIMUM_BUDGET == 3436
    assert paging.COLLECTION_MINIMUM_BUDGET == 3448
    assert results.FIXED_ERROR_ENVELOPE_CAP == 797
    assert impact_admission.IMPACT_MINIMUM_BUDGET == 7730
    assert (
        frozenset(
            {
                "call",
                "construct",
                "import",
                "superclass",
                "include",
                "extend",
                "prepend",
                "rationale_for",
            }
        )
        == ev.RELATIONS
    )
    assert ev.RELATIONS | {"jsx", "reference"} == ev.REFERENCE_RELATIONS
    assert (ev.RELATIONS - {"rationale_for"}) | {"reference"} == ev.EDGE_RELATIONS
    assert len(ev.RELATIONS) == 8
    for relations in (ev.RELATIONS, ev.REFERENCE_RELATIONS, ev.EDGE_RELATIONS):
        assert "routes_to" not in relations
    assert "routes_to" not in KNOWN_EDGE_RELATIONS
    assert "routes_to" in ev.ASSERTION_RELATIONS


# ---- G-MCP ----
def test_the_tool_surface_is_byte_identical_and_read_only(tmp_path):
    assert len(READ_ONLY_TOOLS) == 7
    assert not any("rails" in name or "route" in name for name in READ_ONLY_TOOLS)
    out = CliRunner().invoke(
        cli, ["tools", "--repo", str(workspace(tmp_path))], catch_exceptions=False
    )
    assert out.exit_code == 0
    raw = out.stdout.encode()
    assert len(raw) == BASE_TOOLS_BYTES and hashlib.sha256(raw).hexdigest() == BASE_TOOLS_SHA256
    assert "routes_to" not in out.stdout and "rails" not in out.stdout.lower()
    assert "index" in cli.commands
    assert "frameworks" in {p.name for p in cli.commands["index"].params}
    assert "frameworks" not in {p.name for p in cli.commands["serve"].params}


# ---- G-NOLEAK ----
GENERATION_DERIVED = {
    "generation_id",
    "snapshot_id",
    "id",
    "manifest_sha256",
    "uri",
    "query_digest",
    "retrieval_record_id",
}


def scrub(value):
    """Drop generation/snapshot-derived identities, which legitimately differ between graphs
    whose snapshots bind different record sets. Entity ids and every content field stay."""
    if isinstance(value, dict):
        # Resource ids are snapshot-derived hashes: keep only how many there are.
        return {
            k: len(v) if k == "source_resource_ids" else scrub(v)
            for k, v in value.items()
            if k not in GENERATION_DERIVED
        }
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def first_difference(left, right, path="$"):
    """Iterative diagnostic: the first JSON path where two documents differ."""
    pending = [(left, right, path)]
    while pending:
        a, b, where = pending.pop()
        if isinstance(a, dict) and isinstance(b, dict):
            if set(a) != set(b):
                return f"{where}: keys {sorted(set(a) ^ set(b))}"
            pending.extend((a[k], b[k], f"{where}.{k}") for k in sorted(a, reverse=True))
        elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
            pending.extend(
                (x, y, f"{where}[{i}]") for i, (x, y) in enumerate(zip(a, b, strict=True))
            )
        elif a != b:
            return f"{where}: {str(a)[:120]} != {str(b)[:120]}"
    return "equal"


def server(tmp_path, repo, root):
    config = Config(
        repo=repo, memex_root=tmp_path / "memex", max_calls_per_minute=0, graph_root=root
    )
    enforcement, memex, factory = build_server_state(config)
    return config, enforcement, memex, factory


async def graph_call(state, **arguments):
    config, enforcement, memex, factory = state
    return await call_tool_result(
        "graph_query",
        {"schema_version": "atlas.graph.v1", **arguments},
        config,
        enforcement,
        memex,
        factory,
    )


def test_mcp_neighbors_and_context_are_equal_on_and_off(tmp_path):
    repo, on, _off = pair(tmp_path)
    target = symbol_entity(on, "app/controllers/users_controller.rb", "index")[0]
    seeds = [ev.file_entity(R, "ruby")["id"], target]
    states = [server(tmp_path, repo, tmp_path / "on"), server(tmp_path, repo, tmp_path / "off")]

    async def run():
        collected: list[list] = [[], []]
        try:
            for slot, state in enumerate(states):
                for seed in seeds:
                    for direction in ("incoming", "outgoing"):
                        result = await graph_call(
                            state,
                            op="neighborhood",
                            seed={"entity_id": seed},
                            direction=direction,
                            max_depth=2,
                            deadline_ms=2000,
                        )
                        assert not result.isError, result
                        collected[slot].append(result.content[0].text)
                for text in ("UsersController", "users#index", "get /users"):
                    retrieved = await graph_call(state, op="retrieve", text=text)
                    assert not retrieved.isError, retrieved
                    collected[slot].append(json.dumps(retrieved.structuredContent))
                    context = await graph_call(
                        state, op="context", text=text, source_policy="references"
                    )
                    assert not context.isError, context
                    collected[slot].append(context.content[0].text)
                found = await call_tool_result(
                    "search_symbol",
                    {"name": "index"},
                    state[0],
                    state[1],
                    state[2],
                    state[3],
                )
                collected[slot].append(found.content[0].text)
        finally:
            for state in states:
                state[3].close()
        return collected

    with_rails, without = asyncio.run(run())
    assert len(with_rails) == len(without) > 8
    for a, b in zip(with_rails, without, strict=True):
        assert "routes_to" not in a and "rails_route" not in a and "rails_convention" not in a
        assert scrub(json.loads(a)) == scrub(json.loads(b)), first_difference(
            scrub(json.loads(a)), scrub(json.loads(b))
        )
    assert any("UsersController" in text for text in with_rails)


# ---- G-QUERY ----
def test_change_set_and_admission_arithmetic(tmp_path):
    repo = workspace(tmp_path)
    _r, on_before = built(tmp_path, root="on-before", repo=repo)
    _r, off_before = built(tmp_path, root="off-before", repo=repo, frameworks=())
    edit(
        repo,
        "app/controllers/users_controller.rb",
        "  def index\n    []\n",
        "  def index\n    [1]\n",
    )
    _r, on_after = built(tmp_path, root="on-after", repo=repo)
    _r, off_after = built(tmp_path, root="off-after", repo=repo, frameworks=())
    on_set = symbol_diff.compare(
        Side(repo, tmp_path / "on-before"), Side(repo, tmp_path / "on-after")
    )
    off_set = symbol_diff.compare(
        Side(repo, tmp_path / "off-before"), Side(repo, tmp_path / "off-after")
    )
    assert on_set["changes"], "the edit must be a real change"

    def projected(change_set):
        return [(c["category"], c.get("entity_id"), c.get("path")) for c in change_set["changes"]]

    assert projected(on_set) == projected(off_set)
    with (
        on_before.reader_lease() as a,
        on_after.reader_lease() as b,
        off_before.reader_lease() as c,
        off_after.reader_lease() as d,
    ):
        limits = {"max_entities": 10**6, "max_occurrences": 10**6, "max_changed_files": 100}
        with_rails = impact._admit_diff(a, b, limits)
        plain = impact._admit_diff(c, d, limits)
    assert with_rails["before_entities"] == plain["before_entities"]
    assert with_rails["after_entities"] == plain["after_entities"]
    # Rails occurrences are counted by the diff admission (documented): exactly the 35.
    assert with_rails["before_occurrences"] == plain["before_occurrences"] + 35
    assert with_rails["after_occurrences"] == plain["after_occurrences"] + 35


# ---- export / import ----
def test_export_import_round_trip_validates_the_rails_records(tmp_path):
    repo, on, _off = pair(tmp_path)
    out = tmp_path / "graph.jsonl"
    exported = CliRunner().invoke(
        cli, ["export", "--repo", str(repo), "--graph-root", str(tmp_path / "on"), str(out)]
    )
    assert exported.exit_code == 0, exported.output
    assert out.read_text().count('"rails_route"') >= 35
    imported = CliRunner().invoke(
        cli, ["import", str(out), "--repo", str(repo), "--graph-root", str(tmp_path / "imported")]
    )
    assert imported.exit_code == 0, imported.output
    graph = CodeGraph(repo, graph_root=tmp_path / "imported", read_only=True)
    assert len(facts(graph)) == 35
    assert [r["id"] for r in rails_records(graph)] == [r["id"] for r in rails_records(on)]
    graph.close()
    assert PROJECT.exists()
