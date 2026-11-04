"""ACI-S21 invariance gates: nothing about observed execution leaks into any MCP/query surface."""

import asyncio
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner
from test_s16_support import imported as scip_imported
from test_s21_support import (
    FIXTURE,
    A,
    built,
    coverage_producers,
    current_id,
    import_run,
    imported,
    inputs_of,
    observed,
    published_bytes,
    records,
    stage_leftovers,
    workspace,
)

from atlas_aci.__main__ import cli
from atlas_aci.codegraph import CodeGraph
from atlas_aci.config import Config
from atlas_aci.enforcement import READ_ONLY_TOOLS
from atlas_aci.protocol import results
from atlas_aci.providers import coverage_py
from atlas_aci.query import admission, impact, paging
from atlas_aci.query import diff as symbol_diff
from atlas_aci.query.diff import Side
from atlas_aci.query.indexed import IndexedReader
from atlas_aci.server import build_server_state, call_tool_result
from atlas_aci.storage import evidence as ev

BASE_TOOLS_SHA256 = "94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56"
BASE_TOOLS_BYTES = 1631472
NODEID = "tests/test_basic.py::test_add"


# ---- G-ENVELOPE ----
def test_envelope_minimums_and_relation_sets_are_the_base_values():
    assert admission.MINIMUM_BUDGET == 3436
    assert paging.COLLECTION_MINIMUM_BUDGET == 3448
    assert results.FIXED_ERROR_ENVELOPE_CAP == 797
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
        assert "test_executes" not in relations
    assert (
        "test_executes"
        not in __import__("atlas_aci.codegraph", fromlist=["x"]).KNOWN_EDGE_RELATIONS
    )


# ---- G-MCP ----
def test_the_tool_surface_is_byte_identical_and_read_only(tmp_path):
    assert len(READ_ONLY_TOOLS) == 7
    assert not any("coverage" in name or "import" in name for name in READ_ONLY_TOOLS)
    out = CliRunner().invoke(
        cli, ["tools", "--repo", str(workspace(tmp_path))], catch_exceptions=False
    )
    assert out.exit_code == 0
    raw = out.stdout.encode()
    assert len(raw) == BASE_TOOLS_BYTES and hashlib.sha256(raw).hexdigest() == BASE_TOOLS_SHA256
    assert "test_executes" not in out.stdout and "coverage" not in {
        t["name"] for t in json.loads(out.stdout)
    }
    # The CLI-only commands are registered, and none is an MCP tool.
    assert {"coverage-probe", "coverage-manifest", "import-coverage"} <= set(cli.commands)


def test_no_runtime_or_dev_dependency_was_added():
    root = Path(__file__).parents[2]
    text = (root / "pyproject.toml").read_text().lower() + (root / "uv.lock").read_text().lower()
    assert "pytest-cov" not in text
    assert 'name = "coverage"' not in text


# ---- G-NOLEAK ----
async def graph_call(state, **arguments):
    _w, config, enforcement, memex, factory = state
    return await call_tool_result(
        "graph_query",
        {"schema_version": "atlas.graph.v1", **arguments},
        config,
        enforcement,
        memex,
        factory,
    )


def server(tmp_path, repo):
    config = Config(repo=repo, memex_root=tmp_path / "memex", max_calls_per_minute=0)
    enforcement, memex, factory = build_server_state(config)
    return None, config, enforcement, memex, factory


def lease_status(graph):
    with graph.reader_lease() as lease:
        status = lease.published_status()
        return status, lease


def table_counts(graph):
    tables = ("symbols", "refs", "edges", "rationale", "import_bindings", "assignment_targets")
    with graph.reader_lease() as lease:
        return {t: lease.db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}


def scrub(value):
    """Drop generation/snapshot identities, which legitimately differ between generations."""
    if isinstance(value, dict):
        return {
            k: scrub(v)
            for k, v in value.items()
            if k not in {"generation_id", "snapshot_id", "id", "manifest_sha256", "uri"}
        }
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def test_published_status_and_retrieval_are_unchanged_by_a_coverage_import(tmp_path):
    repo, plain = built(tmp_path, root=tmp_path / "root-build")
    twin = CodeGraph(repo, graph_root=tmp_path / "root-coverage")
    twin.build()
    import_run(twin, "a")
    assert inputs_of(twin)["kind"] == "coverage_import" and inputs_of(plain)["kind"] == "build"
    plain_status, _ = lease_status(plain)
    twin_status, _ = lease_status(twin)
    assert scrub(plain_status) == scrub(twin_status)
    assert plain_status["facts"] == twin_status["facts"]
    assert inputs_of(plain)["retrieval"] == inputs_of(twin)["retrieval"]
    assert (
        inputs_of(plain)["retrieval"]["document_digest"]
        == inputs_of(twin)["retrieval"]["document_digest"]
    )
    assert (
        inputs_of(plain)["retrieval"]["entity_count"]
        == inputs_of(twin)["retrieval"]["entity_count"]
    )
    with twin.reader_lease() as lease:
        assert (
            lease.db.execute("SELECT COUNT(*) FROM retrieval_documents").fetchone()[0]
            == (inputs_of(twin)["retrieval"]["document_count"])
        )
        assert (
            lease.db.execute(
                "SELECT COUNT(*) FROM retrieval_documents WHERE provenance LIKE '%coverage_test%'"
            ).fetchone()[0]
            == 0
        )
    # Projection rows (symbols, refs, edges, ...) are untouched too.
    assert table_counts(plain) == table_counts(twin) and any(table_counts(twin).values())


def test_mcp_never_returns_a_coverage_test_or_a_test_executes_edge(tmp_path):
    repo, graph, _ = imported(tmp_path)
    everything = records(graph)
    tests = [r for r in everything if r["type"] == "entity" and r["kind"] == "coverage_test"]
    assert len(tests) == 14
    test_entity = next(r for r in tests if r["name"] == NODEID)
    file_entity = ev.file_entity("app/calc.py", "python")
    state = server(tmp_path, repo)
    config, enforcement, memex, factory = state[1:]

    async def run():
        try:
            for name in (NODEID, "test_add", "tests/test_basic.py", "TestTotals::test_total"):
                found = await call_tool_result(
                    "search_symbol", {"name": name}, config, enforcement, memex, factory
                )
                assert not found.isError, found
                assert "coverage_test" not in found.content[0].text
                assert NODEID not in found.content[0].text
            seen = []
            for text in (NODEID, "test_add", "tests/test_basic.py::TestTotals::test_total"):
                retrieved = await graph_call(state, op="retrieve", text=text)
                assert not retrieved.isError, retrieved
                payload = json.dumps(retrieved.structuredContent)
                assert "coverage_test" not in payload and "observed_execution" not in payload
                seen.extend(retrieved.structuredContent["data"]["candidates"])
                context = await graph_call(
                    state, op="context", text=text, source_policy="references"
                )
                assert not context.isError, context
                payload = json.dumps(json.loads(context.content[0].text))
                assert "coverage_test" not in payload and "test_executes" not in payload
            # Condition 1: a real coverage_test entity id is not a seed, in either operation.
            for operation in (
                {"op": "neighborhood"},
                {"op": "neighborhood", "direction": "incoming"},
            ):
                result = await graph_call(
                    state,
                    seed={"entity_id": test_entity["id"]},
                    max_depth=2,
                    deadline_ms=2000,
                    **operation,
                )
                assert not result.isError, result
                data = json.loads(result.content[0].text)["data"]
                assert data["seed"]["state"] == "absent", data["seed"]
                assert data["edges"] == [] and "coverage_test" not in json.dumps(data)
            result = await graph_call(
                state,
                op="path",
                seed={"entity_id": test_entity["id"]},
                target={"entity_id": file_entity["id"]},
                max_depth=3,
                deadline_ms=2000,
            )
            assert not result.isError, result
            data = json.loads(result.content[0].text)["data"]
            assert data["seed"]["state"] == "absent" and "test_executes" not in json.dumps(data)
            result = await graph_call(
                state,
                op="path",
                seed={"entity_id": file_entity["id"]},
                target={"entity_id": test_entity["id"]},
                max_depth=3,
                deadline_ms=2000,
            )
            assert not result.isError, result
            assert "test_executes" not in json.dumps(json.loads(result.content[0].text)["data"])
            # An incoming neighborhood on the executed file, over every relation, has no edge.
            for direction in ("incoming", "outgoing"):
                result = await graph_call(
                    state,
                    op="neighborhood",
                    seed={"entity_id": file_entity["id"]},
                    direction=direction,
                    relations=sorted(ev.RELATIONS),
                    max_depth=2,
                    deadline_ms=2000,
                )
                assert not result.isError, result
                data = json.loads(result.content[0].text)["data"]
                assert data["seed"]["state"] == "unique"
                assert "test_executes" not in json.dumps(
                    data
                ) and "coverage_test" not in json.dumps(data)
                assert {edge["relation"] for edge in data["edges"]} <= ev.RELATIONS
            assert seen, "retrieval must have found the tree-sitter test functions"
            assert all(c.get("kind") != "coverage_test" for c in seen)
        finally:
            factory.close()

    asyncio.run(run())


def test_indexed_reader_hides_coverage_tests_by_id_and_by_name(tmp_path):
    _repo, graph, _ = imported(tmp_path)
    test_entity = next(
        r for r in records(graph) if r["type"] == "entity" and r["kind"] == "coverage_test"
    )
    file_entity = ev.file_entity("app/calc.py", "python")
    with graph.reader_lease() as lease:
        reader = IndexedReader(lease, max_examined_edges=100, deadline_ms=2000)
        assert lease.db.execute(
            "SELECT 1 FROM entities WHERE id=?", (test_entity["id"],)
        ).fetchone()
        assert reader.record("entity", test_entity["id"]) is None
        assert reader.record("entity", file_entity["id"]) == file_entity
        assert reader.name_candidates(test_entity["name"]) == []
        assert reader.name_candidates("add") != [] and all(
            c["kind"] != "coverage_test" for c in reader.name_candidates("add")
        )


# ---- G-S16 ----
def test_scip_and_coverage_do_not_chain_in_either_direction(tmp_path):
    repo, graph, first = imported(tmp_path)
    before = published_bytes(graph)
    from test_s16_support import INDEX, MANIFEST

    with pytest.raises(ValueError, match="needs a `build` generation"):
        graph.import_scip(INDEX, MANIFEST)
    assert published_bytes(graph) == before and stage_leftovers(graph) == []
    assert current_id(graph) == first["generation_id"]
    scip_repo, scip_graph, _ = scip_imported(tmp_path, name="scip-repo")
    scip_before = published_bytes(scip_graph)
    with pytest.raises(coverage_py.CoverageImportError) as exc:
        scip_graph.import_coverage(A["report"], A["manifest"])
    assert exc.value.code == "base_kind_unsupported"
    assert published_bytes(scip_graph) == scip_before and scip_repo != repo
    assert os.path.exists(FIXTURE)


# ---- G-DIFF-NEUTRAL and S20 admission ----
def test_symbol_diff_of_a_build_and_its_coverage_import_is_empty(tmp_path):
    repo, plain = built(tmp_path, root=tmp_path / "root-build")
    twin = CodeGraph(repo, graph_root=tmp_path / "root-coverage")
    twin.build()
    import_run(twin, "a")
    import_run(twin, "b")
    change_set = symbol_diff.compare(
        Side(repo, tmp_path / "root-build"), Side(repo, tmp_path / "root-coverage")
    )
    assert change_set["changes"] == []
    reverse = symbol_diff.compare(
        Side(repo, tmp_path / "root-coverage"), Side(repo, tmp_path / "root-build")
    )
    assert reverse["changes"] == [] and plain is not None


def test_impact_admission_ignores_observed_execution_volume(tmp_path):
    repo, plain = built(tmp_path, root=tmp_path / "root-build")
    twin = CodeGraph(repo, graph_root=tmp_path / "root-coverage")
    twin.build()
    import_run(twin, "a")
    import_run(twin, "b")
    with plain.reader_lease() as before, twin.reader_lease() as after:
        raw_entities = after.db.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        raw_occurrences = after.db.execute("SELECT COUNT(*) FROM occurrences").fetchone()[0]
        plain_entities = before.db.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        plain_occurrences = before.db.execute("SELECT COUNT(*) FROM occurrences").fetchone()[0]
        assert raw_occurrences > plain_occurrences and raw_entities > plain_entities
        # Limits sit exactly at the build's own size: a build passes, so the coverage
        # generation of the same source must pass too (the raw counts would trip it).
        limits = {
            "max_entities": plain_entities,
            "max_occurrences": plain_occurrences,
            "max_changed_files": 100,
        }
        assert impact._admit_diff(before, before, limits)
        counts = impact._admit_diff(before, after, limits)
        assert counts["after_entities"] == plain_entities
        assert counts["after_occurrences"] == plain_occurrences
        assert counts["before_entities"] == plain_entities
        # The counts are real counts: a genuinely smaller limit still refuses.
        with pytest.raises(impact.ImpactError) as exc:
            impact._admit_diff(before, after, {**limits, "max_occurrences": plain_occurrences - 1})
        assert exc.value.reason == "diff_occurrence_limit"


# ---- G-RO ----
def tree_inventory(root):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(Path(root).rglob("*"))
        if p.is_file() and not p.is_symlink()
    }


def test_the_source_repository_is_read_only_for_manifest_and_import(tmp_path):
    repo, graph = built(tmp_path, root=tmp_path / "root")
    before = tree_inventory(repo)
    out = tmp_path / "sidecar.json"
    result = CliRunner().invoke(
        cli,
        [
            "coverage-manifest",
            "--repo",
            str(repo),
            "--environment",
            str(A["environment"]),
            "--output",
            str(out),
            str(A["report"]),
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0 and tree_inventory(repo) == before
    assert out.read_bytes() == A["manifest"].read_bytes()
    graph.import_coverage(A["report"], out)
    assert tree_inventory(repo) == before
    graph.import_coverage(A["report"], out)  # unchanged
    assert tree_inventory(repo) == before
    # Every rejection leaves the tree and the graph alone.
    rejected = tmp_path / "bad.json"
    rejected.write_text("[]")
    published = published_bytes(graph)
    with pytest.raises(coverage_py.CoverageImportError):
        graph.import_coverage(rejected, out)
    result = CliRunner().invoke(
        cli,
        [
            "coverage-manifest",
            "--repo",
            str(repo),
            "--environment",
            str(A["environment"]),
            "--output",
            str(repo / "x.json"),
            str(A["report"]),
        ],
    )
    assert result.exit_code == 1
    assert tree_inventory(repo) == before and published_bytes(graph) == published


# ---- G-NOEXEC ----
def test_import_succeeds_while_every_process_spawn_is_forbidden(tmp_path, monkeypatch):
    _repo, graph = built(tmp_path, root=tmp_path / "root")

    def forbidden(*args, **kwargs):
        raise AssertionError("coverage import must not start a process")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    for name in (
        "execv",
        "execve",
        "execl",
        "execlp",
        "execvp",
        "execvpe",
        "posix_spawn",
        "posix_spawnp",
        "fork",
        "system",
        "popen",
    ):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, forbidden)
    result = graph.import_coverage(A["report"], A["manifest"])
    assert result["status"] == "imported" and len(coverage_producers(graph)) == 1
    assert observed(graph)
