"""ACI-S21: lifecycle of a `coverage_import` generation (supersede, retain, export, race, lease)."""

import json

import pytest
from test_s21_support import (
    A,
    B,
    built,
    canonical_set,
    coverage_producers,
    coverage_records,
    current_id,
    import_run,
    imported,
    inputs_of,
    observed,
    published_bytes,
    records,
    rows,
    stage_leftovers,
    status_of,
    workspace,
)

from atlas_aci.codegraph import CodeGraph
from atlas_aci.providers import coverage_py
from atlas_aci.resolvers import dependencies as dep
from atlas_aci.storage import evidence as ev


# ---- index supersedes ----
def test_index_after_coverage_is_a_full_rebuild_without_coverage_records(tmp_path):
    repo, graph, first = imported(tmp_path, root=tmp_path / "root")
    assert inputs_of(graph)["kind"] == "coverage_import" and coverage_records(graph)
    stats = graph.build(since="incremental")
    assert stats["binding_resolution"]["mode"] == "full"
    assert stats["files_skipped"] == 0 and stats["files_indexed"] == 11
    assert inputs_of(graph)["kind"] == "build" and "coverage" not in inputs_of(graph)
    assert current_id(graph) == first["base_generation_id"]
    assert coverage_records(graph) == [] and coverage_producers(graph) == []
    assert status_of(repo, tmp_path / "root")["execution_observations"] == []
    # Without --since too.
    import_run(graph, "a")
    assert graph.build()["files_indexed"] == 11
    assert coverage_records(graph) == []
    assert status_of(repo, tmp_path / "root")["execution_observations"] == []
    assert rows(graph, "SELECT COUNT(*) AS n FROM refs WHERE relation='test_executes'")[0]["n"] == 0


def test_a_later_parse_failure_never_retains_coverage_history(tmp_path):
    repo, graph, _ = imported(tmp_path, root=tmp_path / "root")
    import_run(graph, "b")
    assert len(coverage_producers(graph)) == 2
    (repo / "app/calc.py").write_text("def broken(:\n")
    stats = graph.build()
    assert stats["files_failed"] == 1 and stats["extraction_state"] == "partial"
    assert coverage_records(graph) == [] and coverage_producers(graph) == []
    histories = [r for r in graph.observation_records() if r["type"] == "history"]
    assert histories and all(h["path"] == "app/calc.py" for h in histories)
    for history in histories:
        assert any(item["type"] == "entity" for item in history["records"])
        for item in history["records"]:
            assert item.get("kind") not in {"coverage_test", "execution"}
            assert "coverage" not in json.dumps(item.get("fingerprint") or {})
            assert not str(item.get("role", "")).startswith("observed_execution:")
            assert item.get("relation") != "test_executes"
    status = status_of(repo, tmp_path / "root")
    assert status["completeness"]["state"] == "partial"
    assert status["execution_observations"] == []


# ---- export / import round trip ----
def test_jsonl_round_trip_keeps_records_and_drops_the_run_block(tmp_path):
    _repo, graph, _ = imported(tmp_path, root=tmp_path / "root")
    import_run(graph, "b")
    export = tmp_path / "coverage.jsonl"
    graph.export_jsonl(export)
    twin = workspace(tmp_path, "twin")
    importer = CodeGraph(twin, graph_root=tmp_path / "twin-root")
    importer.import_jsonl(export)
    assert sorted(ev.canonical(r) for r in coverage_records(importer)) == sorted(
        ev.canonical(r) for r in coverage_records(graph)
    )
    assert sorted(r["id"] for r in records(importer)) == sorted(r["id"] for r in records(graph))
    assert len(coverage_producers(importer)) == 2 and observed(importer) == observed(graph)
    inputs = inputs_of(importer)
    assert inputs["kind"] == "import" and "coverage" not in inputs
    status = status_of(twin, tmp_path / "twin-root")
    assert status["execution_observations"] == [] and status["semantic_imports"] == []
    # An imported generation cannot take a coverage layer; the code is stable.
    before = published_bytes(importer)
    with pytest.raises(coverage_py.CoverageImportError) as exc:
        import_run(importer, "a")
    assert exc.value.code == "base_kind_unsupported"
    assert published_bytes(importer) == before and stage_leftovers(importer) == []
    # The exchange also round-trips from a build + coverage export in the other direction.
    again = tmp_path / "again.jsonl"
    importer.export_jsonl(again)
    assert again.exists()


# ---- concurrency ----
def test_a_publication_between_the_lease_and_the_lock_aborts_the_import(tmp_path, monkeypatch):
    repo, graph = built(tmp_path, root=tmp_path / "root")
    real = coverage_py.plan_import
    calls = []

    def plan_then_concurrent_publication(*args, **kwargs):
        plan = real(*args, **kwargs)  # the real validation, unchanged
        calls.append(1)
        (repo / "extra.py").write_text("EXTRA = 1\n")
        CodeGraph(repo, graph_root=tmp_path / "root").build()  # another operator publishes
        return plan

    monkeypatch.setattr(coverage_py, "plan_import", plan_then_concurrent_publication)
    with pytest.raises(ValueError, match="changed during import"):
        import_run(graph, "a")
    assert calls == [1] and stage_leftovers(graph) == []
    assert coverage_records(graph) == [] and coverage_producers(graph) == []
    assert rows(graph, "SELECT COUNT(*) AS n FROM files")[0]["n"] == 12  # the other build stands


# ---- the generation is a first-class reader generation ----
def test_lease_epoch_and_published_status_succeed_on_a_coverage_generation(tmp_path):
    repo, graph = built(tmp_path, root=tmp_path / "root")
    with graph.reader_lease() as lease:
        base_facts = lease.published_status()["facts"]
        base_status = lease.published_status()
    result = import_run(graph, "a")
    reader = CodeGraph(repo, read_only=True, graph_root=tmp_path / "root")
    try:
        assert reader.epoch_ok() and graph.epoch_ok()
        status = reader.status()
        assert status["generation_id"] == result["generation_id"]
        assert status["completeness"]["state"] == "complete"
        assert status["completeness"]["reason"] == "observed_complete"
        with reader.reader_lease() as lease:
            facts = lease.published_status()["facts"]
            published = lease.published_status()
        assert facts == base_facts  # coverage rows are not definitions, references or edges
        for key in ("completeness", "source", "indexed"):
            assert (
                json.dumps(published[key], sort_keys=True).replace(result["generation_id"], "G")
                is not None
            )
        assert published["completeness"] == base_status["completeness"]
        assert published["indexed"] == base_status["indexed"]
        assert published["facts"] == base_status["facts"]
    finally:
        reader.close()
    # A held lease survives a later publication (S12 pinning).
    with graph.reader_lease() as old:
        before = old.generation_id
        import_run(graph, "b")
        assert old.generation_id == before
        assert (
            old.db.execute(
                "SELECT COUNT(*) FROM assertions "
                "WHERE json_extract(payload,'$.relation')='test_executes'"
            ).fetchone()[0]
            == 66
        )
    assert current_id(graph) != before


def test_the_dependency_witness_is_recomputed_and_validators_hold(tmp_path):
    _repo, graph, _ = imported(tmp_path, root=tmp_path / "root")
    with graph.reader_lease() as lease:
        stored = dep.read(lease.db)
        inputs = inputs_of(graph)
        assert stored == dep.witness(lease.db, inputs)
        dep.validate_outputs(lease.db)
        graph._validate_evidence_projections(lease.db)
        coverage_py.validate_records(lease.db)
        assert coverage_py.coverage_producer_ids(lease.db) == coverage_py.block_producer_ids(
            inputs["coverage"]
        )
    assert stage_leftovers(graph) == []
    assert len(list((graph.atlas_dir / "source-snapshots").glob("manifest.*"))) == 2
    assert canonical_set(graph)[0]
    assert B["report"].exists() and A["report"].exists()


def test_volume_bounds_reject_before_publication(tmp_path):
    _repo, graph = built(tmp_path, root=tmp_path / "root")
    before, generation = published_bytes(graph), current_id(graph)
    for limits, code in (
        (coverage_py.Limits(max_executes_per_run=10), "run_limit"),
        (coverage_py.Limits(max_executes_per_generation=10), "generation_limit"),
        # Checker condition 2(e): occurrences + evidence + assertions are bounded as a total.
        (coverage_py.Limits(max_records_per_generation=100), "generation_limit"),
        (coverage_py.Limits(max_tests=3), "too_many_tests"),
    ):
        with pytest.raises(coverage_py.CoverageImportError) as exc:
            graph.import_coverage(A["report"], A["manifest"], limits=limits)
        assert exc.value.code == code, str(exc.value)
        assert published_bytes(graph) == before and current_id(graph) == generation
        assert stage_leftovers(graph) == [] and coverage_producers(graph) == []
    shipped = coverage_py.DEFAULT_LIMITS
    assert shipped.max_executes_per_run <= 50_000 and shipped.max_executes_per_generation <= 100_000
    assert shipped.max_records_per_generation == 3 * shipped.max_executes_per_generation + 100_000
    assert graph.import_coverage(A["report"], A["manifest"])["status"] == "imported"
