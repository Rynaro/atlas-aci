"""Private acquisition-derived chunk authentication, actual pack reads and tamper."""

import hashlib
import os

import pytest

from atlas_aci.codegraph import CodeGraph
from atlas_aci.storage import snapshots as snap


def built(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    data = b"# " + b"a" * 150000 + b"\xc3\xa9\x00tail\n"
    (repo / "blob.py").write_bytes(data)
    (repo / "a.py").write_text("def A():\n pass\n")
    graph = CodeGraph(repo)
    graph.build()
    return graph, data


def test_bounded_verified_span_and_empty(tmp_path, monkeypatch):
    graph, data = built(tmp_path)
    sha = hashlib.sha256(data).hexdigest()
    try:
        with graph.reader_lease(chunk_catalog_bytes=1024 * 1024) as reader:
            source = reader._source_lease
            calls = []
            pread = os.pread

            def observed(fd, count, offset):
                calls.append((count, offset))
                return pread(fd, count, offset)

            monkeypatch.setattr(snap.os, "pread", observed)
            assert source.read_span("blob.py", sha, 65530, 150010, 0, 30) == data[65530:65560]
            assert sum(c[0] for c in calls) <= 30 + 2 * 65536
            calls.clear()
            assert source.read_span("blob.py", sha, len(data), len(data), 0, 1) == b""
            assert calls == []
            assert source.read_span("blob.py", sha, 0, len(data), len(data) - 4, 100) == data[-4:]
            assert source.chunk_catalog_bytes > 0
    finally:
        graph.close()


def test_post_acquisition_tamper_rejected(tmp_path):
    graph, data = built(tmp_path)
    sha = hashlib.sha256(data).hexdigest()
    try:
        with graph.reader_lease(chunk_catalog_bytes=1024 * 1024) as reader:
            source = reader._source_lease
            pack = (
                graph.atlas_dir
                / "source-snapshots"
                / ("pack." + source.manifest["pack"]["sha256"] + ".bin")
            )
            with pack.open("r+b") as corrupt:
                corrupt.seek(source.index[sha]["offset"] + 65536)
                corrupt.write(b"Z")
            with pytest.raises(ValueError, match="chunk"):
                source.read_span("blob.py", sha, 65536, 65540, 0, 4)
    finally:
        graph.close()


def test_catalog_capacity_before_exposure_and_ordinary_unchanged(tmp_path):
    graph, _ = built(tmp_path)
    try:
        with pytest.raises(Exception, match="catalog"), graph.reader_lease(chunk_catalog_bytes=1):
            pytest.fail("catalog cap ignored")
        with graph.reader_lease() as reader:
            assert reader._source_lease.chunk_catalog is None
    finally:
        graph.close()
