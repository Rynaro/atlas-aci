"""Real parsed predecessor faults and conservative dependency changes."""

import json
import sqlite3

import pytest
from test_s10_contract import equivalent, make

from atlas_aci.codegraph import CodeGraph
from atlas_aci.resolvers import dependencies as dep


@pytest.mark.parametrize("fault", ["assertion", "edge", "reason", "occurrence"])
def test_incoherent_outputs_force_full_repair(tmp_path, monkeypatch, fault):
    graph = make(tmp_path)
    with sqlite3.connect(graph.db_path) as conn:
        conn.row_factory = sqlite3.Row
        if fault == "assertion":
            conn.execute("DELETE FROM assertions")
        elif fault == "edge":
            conn.execute("DELETE FROM edges")
        elif fault == "reason":
            conn.execute("UPDATE refs SET unresolved_reason='forged_reason'")
        else:
            conn.execute(
                "DELETE FROM occurrences WHERE id=(SELECT occurrence_id FROM refs LIMIT 1)"
            )
        witness = json.loads(
            conn.execute("SELECT value FROM manifest WHERE key=?", (dep.KEY,)).fetchone()[0]
        )
        for unit in witness["units"]:
            unit["output"] = dep.outputs(conn, unit["path"])
        conn.execute("UPDATE manifest SET value=? WHERE key=?", (json.dumps(witness), dep.KEY))
    called = []
    original = CodeGraph._resolve_binding_unit

    def real(self, conn, path, *args):
        called.append(path)
        return original(self, conn, path, *args)

    monkeypatch.setattr(CodeGraph, "_resolve_binding_unit", real)
    stats = graph.build(since="HEAD")
    assert stats["binding_resolution"]["mode"] == "full"
    if fault != "occurrence":
        assert stats["binding_resolution"]["reason"] == "invalid_predecessor_outputs"
    else:
        # Existing generation validation detects missing consumed provenance
        # before incremental eligibility; ordinary full rebuild already repairs.
        assert stats["binding_resolution"]["reason"] == "full_requested_or_incompatible_inputs"
    assert "pkg/a.py" in called
    assert graph.query("callers_of:B")["edges"]
    equivalent(graph, tmp_path)
    graph.close()


@pytest.mark.parametrize(
    "case",
    [
        "missing_module",
        "package_marker",
        "module_ambiguity",
        "conditional_export",
        "target_blocker",
        "duplicate_export",
        "wildcard_caller",
    ],
)
def test_negative_dependencies_match_full(tmp_path, monkeypatch, case):
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg/a.py").write_text("from .b import B\ndef A(): B()\n")
    if case != "package_marker":
        (repo / "pkg/__init__.py").write_text("")
    if case != "missing_module":
        (repo / "pkg/b.py").write_text("def B(): pass\n")
    graph = CodeGraph(repo)
    graph.build()
    before = graph.query("callers_of:B")["edges"]
    assert bool(before) == (case not in {"missing_module", "package_marker"})
    if case == "missing_module":
        (repo / "pkg/b.py").write_text("def B(): pass\n")
    elif case == "package_marker":
        (repo / "pkg/__init__.py").write_text("")
    elif case == "module_ambiguity":
        (repo / "pkg/b").mkdir()
        (repo / "pkg/b/__init__.py").write_text("def B(): pass\n")
    elif case == "conditional_export":
        (repo / "pkg/b.py").write_text("if False:\n def B(): pass\n")
    elif case == "target_blocker":
        (repo / "pkg/b.py").write_text("def B(): pass\nB=0\n")
    elif case == "duplicate_export":
        (repo / "pkg/b.py").write_text("def B(): pass\ndef B(): pass\n")
    else:
        (repo / "pkg/a.py").write_text("from .b import B\nfrom external import *\ndef A(): B()\n")
    called = []
    original = CodeGraph._resolve_binding_unit

    def real(self, conn, path, *args):
        called.append(path)
        return original(self, conn, path, *args)

    monkeypatch.setattr(CodeGraph, "_resolve_binding_unit", real)
    graph.build(since="HEAD")
    assert "pkg/a.py" in called
    assert bool(graph.query("callers_of:B")["edges"]) == (
        case in {"missing_module", "package_marker"}
    )
    equivalent(graph, tmp_path)
    graph.close()


def test_nonempty_same_language_unit_execution(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("unknown_a()\n")
    (repo / "b.py").write_text("def B():\n unknown_b()\n")
    graph = CodeGraph(repo)
    graph.build()
    original_b = [
        r
        for r in graph.evidence_records()
        if r["type"] == "assertion" and r["raw_target"] == "unknown_b"
    ]
    assert len(original_b) == 1
    called = []
    original = CodeGraph._resolve_binding_unit

    def real(self, conn, path, *args):
        called.append(path)
        return original(self, conn, path, *args)

    monkeypatch.setattr(CodeGraph, "_resolve_binding_unit", real)
    (repo / "a.py").write_text("unknown_c()\n")
    stats = graph.build(since="HEAD")
    assert called == ["a.py"]
    assert stats["binding_resolution"]["reused_paths"] == ["b.py"]
    assert original_b[0] in graph.evidence_records()
    equivalent(graph, tmp_path)
    graph.close()


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "truncated",
        "extra",
        "duplicate_unit",
        "missing_unit",
        "wrong_local",
        "wrong_output",
        "bool_schema",
    ],
)
def test_untrusted_witness_full_fallback(tmp_path, monkeypatch, fault):
    graph = make(tmp_path)
    with sqlite3.connect(graph.db_path) as conn:
        witness = json.loads(
            conn.execute("SELECT value FROM manifest WHERE key=?", (dep.KEY,)).fetchone()[0]
        )
        if fault == "missing":
            conn.execute("DELETE FROM manifest WHERE key=?", (dep.KEY,))
        else:
            if fault == "truncated":
                raw = '{"format":'
            else:
                if fault == "extra":
                    witness["unknown"] = 1
                elif fault == "duplicate_unit":
                    witness["units"].append(witness["units"][0])
                elif fault == "missing_unit":
                    witness["units"].pop()
                elif fault == "wrong_local":
                    witness["units"][0]["local"] = "0" * 64
                elif fault == "wrong_output":
                    witness["units"][0]["output"] = "0" * 64
                else:
                    witness["schema"]["epoch"] = True
                raw = json.dumps(witness)
            conn.execute("UPDATE manifest SET value=? WHERE key=?", (raw, dep.KEY))
    called = []
    original = CodeGraph._resolve_binding_unit

    def real(self, conn, path, *args):
        called.append(path)
        return original(self, conn, path, *args)

    monkeypatch.setattr(CodeGraph, "_resolve_binding_unit", real)
    stats = graph.build(since="HEAD")
    assert stats["binding_resolution"]["mode"] == "full"
    assert sorted(called) == ["pkg/__init__.py", "pkg/a.py", "pkg/b.py"]
    equivalent(graph, tmp_path)
    graph.close()


def test_partial_history_matches_same_predecessor_full_oracle(tmp_path):
    import shutil

    graph = make(tmp_path)
    oracle = CodeGraph(graph.repo, graph_root=tmp_path / "history-oracle")
    oracle.atlas_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(graph.db_path, oracle.db_path)
    shutil.copytree(graph.atlas_dir / "source-snapshots", oracle.atlas_dir / "source-snapshots")
    # Both writers start with the same real G1 source/evidence; a fresh source-
    # only oracle could not supply the historical target closure.
    for index, content in enumerate(["def B(:\n pass\n", "def B(): pass\n"]):
        (graph.repo / "pkg/b.py").write_text(content)
        graph.build(since="HEAD")
        oracle.build()
        actual, expected = tmp_path / f"inc-{index}.jsonl", tmp_path / f"full-{index}.jsonl"
        graph.export_jsonl(actual)
        oracle.export_jsonl(expected)
        assert actual.read_bytes() == expected.read_bytes()
        assert graph.observation_records() == oracle.observation_records()
        assert graph.evidence_records() == oracle.evidence_records()
        if index == 0:
            assert graph.search_symbol("B")["historical_definitions"]
            assert not graph.query("callers_of:B")["edges"]
            assert graph.search_symbol("B")["references"][0]["unresolved_reason"] == "stale_target"
        else:
            assert graph.query("callers_of:B")["edges"]
            assert not graph.search_symbol("B").get("historical_definitions")
    graph.close()
    oracle.close()


def test_owner_and_ambiguous_bucket_changes(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.rb").write_text("class W\n def self.close; end\nend\nW.close\n")
    graph = CodeGraph(repo)
    graph.build()
    assert graph.query("callers_of:close")["edges"][0]["confidence"] == "EXTRACTED"
    (repo / "b.rb").write_text("class W\n def self.close; end\nend\n")
    graph.build(since="HEAD")
    edges = graph.query("callers_of:close")["edges"]
    assert len(edges) == 1 and edges[0]["confidence"] == "AMBIGUOUS"
    assert len(edges[0]["candidates"]) == 2
    equivalent(graph, tmp_path)
    (repo / "b.rb").unlink()
    graph.build(since="HEAD")
    assert graph.query("callers_of:close")["edges"][0]["confidence"] == "EXTRACTED"
    equivalent(graph, tmp_path)
    graph.close()


def test_unsupported_dependency_profile_is_explicit_full(tmp_path, monkeypatch):
    graph = make(tmp_path)
    monkeypatch.setattr(dep, "LANGUAGES", dep.LANGUAGES - {"python"})
    stats = graph.build(since="HEAD")
    assert stats["binding_resolution"]["mode"] == "full"
    assert stats["binding_resolution"]["reason"] == "unknown_dependency_inputs"
    assert graph.query("callers_of:B")["edges"]
    with graph.reader_lease() as lease:
        assert not lease.db.execute("SELECT value FROM manifest WHERE key=?", (dep.KEY,)).fetchone()
    equivalent(graph, tmp_path)
    graph.close()


def test_removed_source_cleans_edges_imports_and_rationale(tmp_path):
    graph = make(tmp_path)
    with graph.reader_lease() as lease:
        assert lease.db.execute("SELECT COUNT(*) FROM rationale").fetchone()[0] == 1
        assert (
            lease.db.execute("SELECT COUNT(*) FROM assertions WHERE path='pkg/a.py'").fetchone()[0]
            == 3
        )
    (graph.repo / "pkg/a.py").unlink()
    graph.build(since="HEAD")
    with graph.reader_lease() as lease:
        for table, field in [
            ("edges", "source_path"),
            ("assertions", "path"),
            ("rationale", "path"),
            ("import_bindings", "path"),
        ]:
            assert (
                lease.db.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {field}=?", ("pkg/a.py",)
                ).fetchone()[0]
                == 0
            )
    equivalent(graph, tmp_path)
    graph.close()


def test_fatal_selected_binding_preserves_pinned_g1(tmp_path, monkeypatch):
    from atlas_aci.storage import evidence as ev

    graph = make(tmp_path)
    previous_bytes = graph.db_path.read_bytes()
    previous_records = graph.evidence_records()
    original = CodeGraph._resolve_binding_unit
    with graph.reader_lease() as held:
        (graph.repo / "pkg/a.py").write_text(
            "from .b import B\ndef A():\n # NOTE: calls B\n B(); B()\n"
        )
        with monkeypatch.context() as patch:

            def fail(self, conn, path, *args):
                original(self, conn, path, *args)
                if path == "pkg/a.py":
                    raise RuntimeError("controlled failure after real selected binding")

            patch.setattr(CodeGraph, "_resolve_binding_unit", fail)
            with pytest.raises(RuntimeError, match="after real selected binding"):
                graph.build(since="HEAD")
        assert graph.db_path.read_bytes() == previous_bytes
        assert list(ev.records(held.db)) == previous_records
        assert not list(graph.atlas_dir.glob(".graph.*.tmp.*"))
        graph.build(since="HEAD")
        assert list(ev.records(held.db)) == previous_records
        assert len(held.query("callers_of:B")["edges"]) == 1
    assert len(graph.query("callers_of:B")["edges"]) == 2
    equivalent(graph, tmp_path)
    graph.close()


def test_witness_is_local_operational_metadata(tmp_path, monkeypatch):
    graph = make(tmp_path)
    before = graph.status()["status_id"]
    artifact = tmp_path / "artifact.jsonl"
    graph.export_jsonl(artifact)
    assert dep.FORMAT.encode() not in artifact.read_bytes()
    with sqlite3.connect(graph.db_path) as conn:
        conn.execute("DELETE FROM manifest WHERE key=?", (dep.KEY,))
    assert graph.status()["status_id"] == before

    def forbidden(*args):
        raise AssertionError("reader used or repaired operational dependency metadata")

    with monkeypatch.context() as patch:
        patch.setattr(dep, "snapshot", forbidden)
        patch.setattr(dep, "witness", forbidden)
        with graph.reader_lease() as lease:
            assert lease.query("callers_of:B")["edges"]
            lease.status()
            lease.export_jsonl(tmp_path / "readonly.jsonl")
        imported = CodeGraph(graph.repo, graph_root=tmp_path / "imported")
        imported.import_jsonl(artifact)
        with imported.reader_lease() as lease:
            assert not lease.db.execute(
                "SELECT value FROM manifest WHERE key=?", (dep.KEY,)
            ).fetchone()
            assert lease.query("callers_of:B")["edges"]
    stats = imported.build(since="HEAD")
    assert stats["binding_resolution"]["mode"] == "full"
    imported.close()
    graph.close()


def test_failed_caller_preserves_original_target_history(tmp_path):
    import shutil

    graph = make(tmp_path)
    oracle = CodeGraph(graph.repo, graph_root=tmp_path / "history-oracle")
    oracle.atlas_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(graph.db_path, oracle.db_path)
    shutil.copytree(graph.atlas_dir / "source-snapshots", oracle.atlas_dir / "source-snapshots")
    (graph.repo / "pkg/a.py").write_text("def A(:\n B()\n")
    (graph.repo / "pkg/b.py").write_text("\ndef B(): pass\n")
    (graph.repo / "pkg/c.py").write_text("from .a import A\ndef C(): A()\n")
    graph.build(since="HEAD")
    oracle.build()
    actual, expected = tmp_path / "inc.jsonl", tmp_path / "full.jsonl"
    graph.export_jsonl(actual)
    oracle.export_jsonl(expected)
    assert actual.read_bytes() == expected.read_bytes()
    assert graph.search_symbol("A")["historical_definitions"]
    assert graph.search_symbol("B")["definitions"][0]["line_start"] == 2
    assert not graph.query("callers_of:A")["edges"]
    assert graph.search_symbol("A")["references"][0]["unresolved_reason"] == "stale_target"
    assert graph.observation_records() == oracle.observation_records()
    (graph.repo / "pkg/a.py").unlink()
    graph.build(since="HEAD")
    oracle.build()
    graph.export_jsonl(actual)
    oracle.export_jsonl(expected)
    assert actual.read_bytes() == expected.read_bytes()
    assert not graph.search_symbol("A").get("historical_definitions")
    graph.close()
    oracle.close()


def test_snapshot_reads_are_batched_and_hashes_match_prior_oracle(tmp_path, monkeypatch):
    import importlib.machinery
    import importlib.util
    from pathlib import Path

    repo = tmp_path / "repo"
    repo.mkdir()
    for i in range(12):
        (repo / f"file{i}.py").write_text(
            f"def target{i}(): pass\ndef caller{i}():\n # NOTE: target\n target{i}()\n"
        )
    graph = CodeGraph(repo)
    graph.build()
    source = (
        Path(__file__).resolve().parents[3]
        / ".spectra/evidence/aci-s10/read-work/previous-dependencies.py.txt"
    )
    loader = importlib.machinery.SourceFileLoader("prior_s10_dependencies", str(source))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    old = importlib.util.module_from_spec(spec)
    loader.exec_module(old)  # trusted frozen pre-refactor implementation, not cached data
    with graph.reader_lease() as lease:
        expected = old.snapshot(lease.db)
        statements = []
        lease.db.set_trace_callback(statements.append)
        actual = dep.snapshot(lease.db)
        lease.db.set_trace_callback(None)
        assert actual == expected
        base = graph._build_inputs()
        assert dep.witness(lease.db, base) == old.witness(lease.db, base)
        assert len(actual) == 12 and all(unit["output"] for unit in actual.values())
        assert sum("FROM refs" in statement for statement in statements) <= 2
        assert sum("FROM edges" in statement for statement in statements) <= 1
    statements, called = [], []
    original_edges, original_unit = CodeGraph._resolve_edges, CodeGraph._resolve_binding_unit

    def trace(self, conn, *args):
        conn.set_trace_callback(statements.append)
        try:
            return original_edges(self, conn, *args)
        finally:
            conn.set_trace_callback(None)

    def unit(self, conn, path, *args):
        called.append(path)
        return original_unit(self, conn, path, *args)

    monkeypatch.setattr(CodeGraph, "_resolve_edges", trace)
    monkeypatch.setattr(CodeGraph, "_resolve_binding_unit", unit)
    graph.build()  # full oracle really binds all twelve nonempty units
    assert len(called) == 12
    assert sum("FROM refs" in statement for statement in statements) == 1
    graph.close()
