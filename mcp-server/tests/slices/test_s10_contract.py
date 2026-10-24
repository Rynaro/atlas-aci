"""Affected binding must preserve graph truth and perform real selective work."""

import re

from atlas_aci.codegraph import CodeGraph


def test_call_only_reuses_nonempty_same_language_unit(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("missing_a()\n")
    (repo / "b.py").write_text("def B():\n missing_b()\n")
    graph = CodeGraph(repo)
    graph.build()
    before = [r for r in graph.evidence_records() if r["type"] == "assertion"]
    assert len(before) == 2
    (repo / "a.py").write_text("missing_c()\n")  # same bytes/width, no declaration change
    statements = []
    original = CodeGraph._resolve_edges

    def traced(self, conn, *args, **kwargs):
        conn.set_trace_callback(statements.append)
        try:
            return original(self, conn, *args, **kwargs)
        finally:
            conn.set_trace_callback(None)

    monkeypatch.setattr(CodeGraph, "_resolve_edges", traced)
    graph.build(since="HEAD")
    rewritten_b = [
        s
        for s in statements
        if re.search(r"INTO assertions\(id,path,payload\).*VALUES \('[^']*','b.py',", s)
    ]
    assert not rewritten_b, "unchanged nonempty same-language binding unit was executed again"
    after = [r for r in graph.evidence_records() if r["type"] == "assertion"]
    unchanged = next(r for r in before if r["raw_target"] == "missing_b")
    assert unchanged in after
    graph.close()


def make(tmp_path):
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg/__init__.py").write_text("")
    (repo / "pkg/b.py").write_text("def B(): pass\n")
    (repo / "pkg/a.py").write_text("from .b import B\ndef A():\n # NOTE: calls B\n B()\n")
    graph = CodeGraph(repo)
    graph.build()
    assert graph.query("callers_of:B")["edges"]
    return graph


def operational_ids_removed(value):
    if isinstance(value, dict):
        return {
            key: operational_ids_removed(item)
            for key, item in value.items()
            if not (key == "id" and type(item) is int)
        }
    if isinstance(value, list):
        return [operational_ids_removed(item) for item in value]
    return value


def equivalent(graph, tmp_path, langs=None):
    oracle = CodeGraph(graph.repo, langs=langs, graph_root=tmp_path / "oracle")
    oracle.build()
    actual, expected = tmp_path / "incremental.jsonl", tmp_path / "full.jsonl"
    graph.export_jsonl(actual)
    oracle.export_jsonl(expected)
    assert actual.read_bytes() == expected.read_bytes()
    assert graph.evidence_records() == oracle.evidence_records()
    for query in ("callers_of:B", "callers_of:A", "definitions_of:B"):
        assert operational_ids_removed(graph.query(query)) == operational_ids_removed(
            oracle.query(query)
        )
    oracle.close()


def test_export_change_equivalence(tmp_path):
    graph = make(tmp_path)
    (graph.repo / "pkg/b.py").write_text("def C(): pass\n")
    stats = graph.build(since="HEAD")
    assert stats["binding_resolution"]["mode"] == "affected"
    assert not graph.query("callers_of:B")["edges"]
    assert (
        graph.search_symbol("B")["references"][0]["unresolved_reason"]
        == "relative_import_missing_target"
    )
    equivalent(graph, tmp_path)
    graph.close()


def test_delete_equivalence(tmp_path):
    graph = make(tmp_path)
    (graph.repo / "pkg/b.py").unlink()
    stats = graph.build(since="HEAD")
    assert stats["binding_resolution"]["removed_paths"] == ["pkg/b.py"]
    assert not graph.query("callers_of:B")["edges"]
    equivalent(graph, tmp_path)
    graph.close()


def test_rename_equivalence(tmp_path):
    graph = make(tmp_path)
    (graph.repo / "pkg/b.py").rename(graph.repo / "pkg/c.py")
    graph.build(since="HEAD")
    assert graph.search_symbol("B")["definitions"][0]["path"] == "pkg/c.py"
    assert not graph.query("callers_of:B")["edges"]
    equivalent(graph, tmp_path)
    graph.close()


def test_resolver_config_equivalence(tmp_path):
    graph = make(tmp_path)
    graph.langs = {"python"}
    stats = graph.build(since="HEAD")
    assert stats["binding_resolution"]["mode"] == "full"
    assert stats["binding_resolution"]["reason"] == "full_requested_or_incompatible_inputs"
    equivalent(graph, tmp_path, langs=["python"])
    graph.close()


def test_unknown_dependency_fallback(tmp_path, monkeypatch):
    import json
    import sqlite3

    from atlas_aci.resolvers import dependencies as dep

    graph = make(tmp_path)
    with sqlite3.connect(graph.db_path) as conn:
        witness = json.loads(
            conn.execute("SELECT value FROM manifest WHERE key=?", (dep.KEY,)).fetchone()[0]
        )
        witness["profile"] = "unknown-future-resolver"
        conn.execute("UPDATE manifest SET value=? WHERE key=?", (json.dumps(witness), dep.KEY))
    called = []
    original = CodeGraph._resolve_binding_unit

    def real(self, conn, path, *args):
        called.append(path)
        return original(self, conn, path, *args)

    monkeypatch.setattr(CodeGraph, "_resolve_binding_unit", real)
    stats = graph.build(since="HEAD")
    assert stats["binding_resolution"]["mode"] == "full"
    assert stats["binding_resolution"]["reason"] == "incompatible_witness_profile"
    assert sorted(called) == ["pkg/__init__.py", "pkg/a.py", "pkg/b.py"]
    equivalent(graph, tmp_path)
    graph.close()


def test_incremental_replay(tmp_path, monkeypatch):
    graph = make(tmp_path)
    (graph.repo / "pkg/a.py").write_text(
        "from .b import B\ndef A():\n # NOTE: calls B\n B(); B()\n"
    )
    graph.build(since="HEAD")
    first = tmp_path / "first.jsonl"
    graph.export_jsonl(first)
    called = []
    original = CodeGraph._resolve_binding_unit

    def real(self, conn, path, *args):
        called.append(path)
        return original(self, conn, path, *args)

    monkeypatch.setattr(CodeGraph, "_resolve_binding_unit", real)
    stats = graph.build(since="HEAD")
    assert not called
    assert stats["binding_resolution"]["reused_units"] == 3
    replay = tmp_path / "replay.jsonl"
    graph.export_jsonl(replay)
    assert replay.read_bytes() == first.read_bytes()
    graph.close()
