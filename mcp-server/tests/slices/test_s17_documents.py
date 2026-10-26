"""Actual parser/SQLite derivative and BOTH-store semantic closure controls."""

import json
import sqlite3

import pytest

from atlas_aci.codegraph import CodeGraph
from atlas_aci.indexing import retrieval as ret


def test_actual_documents_and_unindexed_id_swap(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def A():\n B()\n# unique task phrase café\n")
    (repo / "b.py").write_text("def B():\n pass\n")
    graph = CodeGraph(repo)
    graph.build()
    try:
        with graph.reader_lease() as reader:
            conn = sqlite3.connect(tmp_path / "derivative.db")
            reader.db.backup(conn)
            conn.executescript(ret.DDL)
            descriptor = ret.populate(conn, reader._source_lease, "available")
            assert descriptor["source_count"] == 2 and descriptor["coverage"]["state"] == "complete"
            matches = list(
                conn.execute(
                    "SELECT candidate_id FROM retrieval_fts WHERE retrieval_fts MATCH ?",
                    ('"unique task phrase"',),
                )
            )
            assert len(matches) == 1
            rows = list(
                conn.execute("SELECT rowid,candidate_id FROM retrieval_fts ORDER BY rowid LIMIT 2")
            )
            assert len(rows) == 2 and rows[0][1] != rows[1][1]
            # UNINDEXED IDs can be swapped while postings and SQLite remain healthy.
            for row, other in ((rows[0], rows[1]), (rows[1], rows[0])):
                conn.execute("UPDATE retrieval_fts_content SET c0=? WHERE id=?", (other[1], row[0]))
            assert conn.execute("PRAGMA integrity_check(retrieval_fts)").fetchall() == [("ok",)]
            with pytest.raises(ValueError, match="bijection"):
                ret.validate(conn, descriptor, reader._source_lease, "available")
            conn.close()
    finally:
        graph.close()


def test_coherently_rehashed_wrong_text_still_fails_source_closure(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def A():\n pass\n")
    graph = CodeGraph(repo)
    graph.build()
    try:
        with graph.reader_lease() as reader:
            conn = sqlite3.connect(tmp_path / "changed.db")
            reader.db.backup(conn)
            conn.executescript(ret.DDL)
            descriptor = ret.populate(conn, reader._source_lease, "available")
            identifier = conn.execute(
                "SELECT candidate_id FROM retrieval_documents WHERE kind='source_file'"
            ).fetchone()[0]
            conn.execute(
                "UPDATE retrieval_documents SET text=? WHERE candidate_id=?",
                ("different", identifier),
            )
            conn.execute(
                "UPDATE retrieval_fts SET text=? WHERE candidate_id=?", ("different", identifier)
            )
            docs = [
                dict(zip(ret.COLUMNS, row, strict=True))
                for row in conn.execute(
                    "SELECT "
                    + ",".join(ret.COLUMNS)
                    + " FROM retrieval_documents ORDER BY candidate_id"
                )
            ]
            forged = ret.descriptor(docs, descriptor["coverage"])
            assert conn.execute("PRAGMA integrity_check(retrieval_fts)").fetchall() == [("ok",)]
            with pytest.raises(ValueError, match="canonical/source"):
                ret.validate(conn, forged, reader._source_lease, "available")
            conn.close()
    finally:
        graph.close()


def test_sourcefree_metadata_only_never_reads_destination(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def A():\n pass\n")
    graph = CodeGraph(repo)
    graph.build()
    try:
        with graph.reader_lease() as reader:
            conn = sqlite3.connect(tmp_path / "sourcefree.db")
            reader.db.backup(conn)
            conn.executescript(ret.DDL)
            descriptor = ret.populate(conn, None, "import")
            assert descriptor["source_count"] == 0 and descriptor["entity_count"] > 0
            assert descriptor["coverage"]["state"] == "unavailable"
            assert all(
                json.loads(r[0])["source"]["state"] == "unavailable"
                for r in conn.execute("SELECT provenance FROM retrieval_documents")
            )
            ret.validate(conn, descriptor, None, "import")
            conn.close()
    finally:
        graph.close()


def test_validation_documents_not_retained_by_open_lease_generator(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def A():\n pass\n# " + "x" * 50000 + "\n")
    graph = CodeGraph(repo)
    graph.build()
    context = graph.reader_lease()
    try:
        with context as reader:
            assert reader._acquisition_retrieval_documents is None
            retained = context.gen.gi_frame.f_locals
            assert not any(
                isinstance(value, list)
                and any(isinstance(row, dict) and "candidate_id" in row for row in value)
                for value in retained.values()
            )
            assert reader.read_source("a.py") == (repo / "a.py").read_bytes()
    finally:
        graph.close()


def test_consumed_buffer_and_cold_cache_incremental_rowid_replay(tmp_path, monkeypatch):
    import asyncio
    import hashlib

    from atlas_aci.query.retrieval import retrieve
    from atlas_aci.storage import snapshots

    repo = tmp_path / "repo"
    repo.mkdir()
    path = repo / "a.py"
    original = b"def Alpha():\n pass\n# exact consumed task phrase caf\xc3\xa9\n"
    changed = b"def Changed():\n pass\n# different live bytes\n"
    path.write_bytes(original)
    graph = CodeGraph(repo)
    reads = []
    safe_source = snapshots.safe_source

    def edit_after_read(root, relative):
        raw, metadata = safe_source(root, relative)
        reads.append(relative)
        path.write_bytes(changed)
        return raw, metadata

    def candidates():
        with graph.reader_lease() as reader:
            data = asyncio.run(retrieve(reader, text="consumed task"))
            assert len(data["candidates"]) == 1
            ref = data["candidates"][0]["source"]
            assert ref["source_sha256"] == hashlib.sha256(original).hexdigest()
            assert reader.read_source("a.py", ref["source_sha256"]) == original
            return data["candidates"], json.loads(
                reader.db.execute(
                    "SELECT value FROM manifest WHERE key='generation_inputs'"
                ).fetchone()[0]
            )["retrieval"]

    try:
        with monkeypatch.context() as patch:
            patch.setattr(snapshots, "safe_source", edit_after_read)
            graph.build()
        assert reads == ["a.py"] and path.read_bytes() == changed
        baseline, descriptor = candidates()
        path.write_bytes(original)
        stats = graph.build()
        assert stats["extraction_cache"]["disk_hits"] == 1
        assert candidates() == (baseline, descriptor)
        graph.build(since="HEAD")
        assert candidates() == (baseline, descriptor)
        with sqlite3.connect(graph.db_path) as conn:
            rows = conn.execute(
                "SELECT candidate_id,text FROM retrieval_fts ORDER BY rowid"
            ).fetchall()
            assert len(rows) >= 2
            conn.execute("DELETE FROM retrieval_fts")
            conn.executemany(
                "INSERT INTO retrieval_fts(candidate_id,text) VALUES (?,?)", reversed(rows)
            )
            assert conn.execute(
                "SELECT candidate_id FROM retrieval_fts ORDER BY rowid"
            ).fetchall() == [(r[0],) for r in reversed(rows)]
        assert candidates() == (baseline, descriptor)
    finally:
        graph.close()
