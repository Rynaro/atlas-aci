"""Coherent source/graph bytes, independently observed through real SQLite/parser."""

import asyncio
import hashlib
import json
import os
import subprocess

import pytest

from atlas_aci.codegraph import CodeGraph
from atlas_aci.config import Config
from atlas_aci.enforcement import ToolError
from atlas_aci.server import build_server_state, dispatch_tool_call
from atlas_aci.storage import snapshots as snap

OLD = b"def old_target():\n    return 1\n"
NEW = b"def new_target():\n    return 2\n"


def make(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_bytes(OLD)
    graph = CodeGraph(repo)
    graph.build()
    return repo, graph


def test_snapshot_source_coherence(tmp_path):
    repo, graph = make(tmp_path)
    config = Config(repo=repo, memex_root=tmp_path / "memex")
    enforcement, memex, factory = build_server_state(config)
    try:
        with factory.reader_lease() as old:
            assert old.search_symbol("old_target")["definitions"]
            token = old.snapshot_id
            pack_fd = old._source_lease.fd
            (repo / "a.py").write_bytes(NEW)
            graph.build()
            graph.build(overlays={"a.py": b"def third_target():\n pass\n"})
            assert old.read_source("a.py") == OLD
            assert old.search_symbol("old_target")["definitions"]
            assert os.fstat(pack_fd)
            with pytest.raises(ToolError, match="snapshot"), factory.source_lease(token):
                pass
            with graph.reader_lease() as current:
                viewed = asyncio.run(
                    dispatch_tool_call(
                        "view_file",
                        {
                            "path": "a.py",
                            "start_line": 1,
                            "end_line": 2,
                            "snapshot_id": current.snapshot_id,
                        },
                        config,
                        enforcement,
                        memex,
                        factory,
                    )
                )
                assert "".join(viewed["lines"]) == "def third_target():\n pass\n"
                assert viewed["source_mode"] == "snapshot"
            live = asyncio.run(
                dispatch_tool_call(
                    "view_file",
                    {"path": "a.py", "start_line": 1, "end_line": 2},
                    config,
                    enforcement,
                    memex,
                    factory,
                )
            )
            assert "".join(live["lines"]) == NEW.decode() and live["source_mode"] == "live"
        with pytest.raises(OSError):
            os.fstat(pack_fd)
    finally:
        factory.close()
        graph.close()


def git(repo, *args):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_worktree_isolation(tmp_path):
    one = tmp_path / "one"
    one.mkdir()
    git(one, "init")
    git(one, "config", "user.email", "fixture@example.invalid")
    git(one, "config", "user.name", "fixture")
    (one / "a.py").write_bytes(OLD)
    git(one, "add", "a.py")
    git(one, "commit", "-m", "fixture")
    two = tmp_path / "two"
    git(one, "worktree", "add", "--detach", str(two), "HEAD")
    (two / "a.py").write_bytes(OLD.replace(b"return 1", b"return 2"))
    graphs = [CodeGraph(root, graph_root=tmp_path / "graphs") for root in (one, two)]
    try:
        ids = [g.build()["snapshot_id"] for g in graphs]
        assert len(set(ids)) == 2
        identities = []
        for index, g in enumerate(graphs):
            with g.reader_lease() as r:
                identities.append(r._source_lease.manifest["context"]["identity"])
                assert r.read_source("a.py") == (g.repo / "a.py").read_bytes()
                assert r.snapshot_id == ids[index]
            config = Config(
                repo=g.repo, graph_root=g.graph_root, memex_root=tmp_path / f"memex-{index}"
            )
            enforcement, memex, factory = build_server_state(config)
            try:
                result = asyncio.run(
                    dispatch_tool_call(
                        "search_symbol", {"name": "old_target"}, config, enforcement, memex, factory
                    )
                )
                assert result["definitions"] and result["snapshot_id"] == ids[index]
            finally:
                factory.close()
        assert identities[0]["common_dir"] == identities[1]["common_dir"]
        assert identities[0]["git_dir"] != identities[1]["git_dir"]
        git(one, "branch", "new-branch")
        git(one, "checkout", "new-branch")
        assert graphs[0].build()["snapshot_id"] == ids[0]
        git(one, "commit", "--allow-empty", "-m", "HEAD-only diagnostic change")
        assert graphs[0].build()["snapshot_id"] == ids[0]
        non = tmp_path / "non"
        non.mkdir()
        (non / "a.py").write_bytes(OLD)
        g = CodeGraph(non, graph_root=tmp_path / "graphs")
        try:
            assert g.build()["snapshot_id"] not in ids
            with g.reader_lease() as r:
                assert r._source_lease.manifest["context"]["identity"]["kind"] == "non_git"
        finally:
            g.close()
    finally:
        for g in graphs:
            g.close()
        git(one, "worktree", "remove", "--force", str(two))


def test_dirty_digest(tmp_path):
    repo, graph = make(tmp_path)
    path = repo / "a.py"
    stat = path.stat()
    first = graph.build()["snapshot_id"]
    path.write_bytes(NEW)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert len(NEW) == len(OLD)
    second = graph.build(since="HEAD")["snapshot_id"]
    assert second != first
    overlay = {"a.py": OLD, "new/unsaved.py": b"def unsaved(): pass\n"}
    third = graph.build(overlays=overlay)["snapshot_id"]
    assert path.read_bytes() == NEW and not (repo / "new").exists()
    with graph.reader_lease() as r:
        assert r.read_source("new/unsaved.py") == overlay["new/unsaved.py"]
    path.write_bytes(OLD)
    (repo / "new").mkdir()
    (repo / "new/unsaved.py").write_bytes(overlay["new/unsaved.py"])
    assert graph.build()["snapshot_id"] == third
    for value in ({"../bad.py": OLD}, {"a.txt": OLD}, {".git/bad.py": OLD}, {"a.py": "not bytes"}):
        with pytest.raises(ValueError):
            graph.build(overlays=value)
    graph.close()


def test_incomplete_manifest(tmp_path, monkeypatch):
    repo, graph = make(tmp_path)
    before = graph.db_path.read_bytes()
    real = snap.Builder.publish

    def fail(self, *args, **kwargs):
        sha = hashlib.sha256(NEW).hexdigest()
        os.unlink(sha, dir_fd=self.fd)
        return real(self, *args, **kwargs)

    (repo / "a.py").write_bytes(NEW)
    with monkeypatch.context() as m:
        m.setattr(snap.Builder, "publish", fail)
        with pytest.raises(FileNotFoundError):
            graph.build()
    assert graph.db_path.read_bytes() == before and not list(graph.atlas_dir.glob("*.tmp.*"))
    assert not list(graph.atlas_dir.glob(".sources-stage.*"))
    with graph.reader_lease() as r:
        assert r.read_source("a.py") == OLD
    graph.build()
    with graph.reader_lease() as r:
        assert r.read_source("a.py") == NEW
    graph.close()


def test_active_lease_retained(tmp_path):
    repo, graph = make(tmp_path)
    script = """import sys,json,os
from pathlib import Path
from atlas_aci.codegraph import CodeGraph
g=CodeGraph(Path(sys.argv[1]),read_only=True)
with g.reader_lease() as r:
 print(json.dumps({'ready':True,'token':r.snapshot_id,'fd':r._source_lease.fd}),flush=True)
 assert sys.stdin.readline().strip()=='READ'
 print(json.dumps({'bytes':r.read_source('a.py').decode(),'defs':r.search_symbol('old_target')['definitions']}),flush=True)
g.close()
"""
    process = subprocess.Popen(
        [os.sys.executable, "-c", script, str(repo)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        ready = json.loads(process.stdout.readline())
        assert ready["ready"]
        for number in range(3):
            graph.build(overlays={"a.py": f"def target_{number}(): pass\n".encode()})
        process.stdin.write("READ\n")
        process.stdin.flush()
        answer = json.loads(process.stdout.readline())
        assert answer["bytes"] == OLD.decode() and answer["defs"]
        assert process.wait(timeout=20) == 0, process.stderr.read()
        store = graph.atlas_dir / "source-snapshots"
        assert (
            len(list(store.glob("manifest.*.json"))) == 2
            and len(list(store.glob("pack.*.bin"))) <= 2
        )
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        graph.close()


def test_snapshot_replay(tmp_path):
    repo, graph = make(tmp_path)
    first = graph.build()["snapshot_id"]
    artifact = tmp_path / "first.jsonl"
    graph.export_jsonl(artifact)
    for since in (None, "HEAD"):
        assert graph.build(since=since)["snapshot_id"] == first
        other = tmp_path / "other.jsonl"
        graph.export_jsonl(other)
        assert other.read_bytes() == artifact.read_bytes()
    graph.close()
    graph = CodeGraph(repo)
    assert graph.build()["snapshot_id"] == first
    with graph.reader_lease() as r:
        with pytest.raises(ToolError):
            r.read_source("a.py", "0" * 64)
        assert r.read_source("a.py") == OLD
    destination = tmp_path / "imported"
    destination.mkdir()
    imported = CodeGraph(destination)
    imported.import_jsonl(artifact)
    with imported.reader_lease() as r:
        assert r.snapshot_id is None and r.search_symbol("old_target")["definitions"]
        with pytest.raises(ToolError):
            r.read_source("a.py")
    imported.close()
    graph.close()
