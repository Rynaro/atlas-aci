"""Actual extraction packets: invalid derived cache data reparses real source."""

import copy
import json

import pytest
from test_s09_contract import counted

from atlas_aci.codegraph import CodeGraph
from atlas_aci.indexing import extraction_cache as xc
from atlas_aci.storage import evidence as ev


def prepare(tmp_path):
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg/__init__.py").write_text("")
    (repo / "pkg/b.py").write_text("def B(): pass\n")
    (repo / "pkg/a.py").write_text("from .b import B\ndef A(value):\n # NOTE: calls B\n B(); B()\n")
    graph = CodeGraph(repo)
    graph.build()
    entries = list((graph.atlas_dir / "extraction-cache/v1").glob("*/*.json"))
    path = next(p for p in entries if json.loads(p.read_text())["inputs"]["path"] == "pkg/a.py")
    return graph, path


def store(path, item):
    item["checksum"] = xc.digest({"packet": item["packet"], "failure": item["failure"]})
    path.write_text(json.dumps(item))


@pytest.mark.parametrize(
    "family", ["symbols", "refs", "rationale", "imports", "records", "assignments", "bindings"]
)
def test_duplicate_raw_rows_miss(tmp_path, monkeypatch, family):
    counts = counted(monkeypatch)
    graph, path = prepare(tmp_path)
    before = graph.query("callers_of:B")
    item = json.loads(path.read_text())
    assert item["packet"][family], "test mutation must be non-vacuous"
    item["packet"][family].append(copy.deepcopy(item["packet"][family][0]))
    store(path, item)
    counts.update(parser=0, extract=0)
    stats = graph.build()
    assert stats["extraction_cache"]["miss_reasons"] == {"invalid": 1}
    assert counts == {"parser": 1, "extract": 1}
    assert graph.query("callers_of:B") == before
    counts.update(parser=0, extract=0)
    assert graph.build()["extraction_cache"]["disk_hits"] == 3
    assert counts == {"parser": 0, "extract": 0}
    graph.close()


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "format",
        "key",
        "inputs",
        "checksum",
        "failed",
        "bool",
        "resolved",
        "link",
        "path",
        "source",
        "producer",
        "scope",
        "byte",
        "rationale_line",
        "conflicting_ref",
        "import_byte",
        "assignment_byte",
        "input_bool",
    ],
)
def test_invalid_packet_miss(tmp_path, monkeypatch, mutation):
    counts = counted(monkeypatch)
    graph, path = prepare(tmp_path)
    item = json.loads(path.read_text())
    p = item["packet"]
    if mutation == "input_bool":
        item["inputs"]["packet_version"] = True
    elif mutation == "conflicting_ref":
        row = copy.deepcopy(p["refs"][0])
        b = json.loads(row["binding"])
        b["unsupported"] = True
        row["binding"] = json.dumps(b)
        p["refs"].append(row)
    elif mutation == "import_byte":
        p["imports"][0]["byte_start"] = 100000
    elif mutation == "assignment_byte":
        p["assignments"][0][3] = 100000
        p["bindings"][0]["fact"][3] = 100000
    elif mutation == "missing":
        del p["imports"]
    elif mutation == "format":
        item["format"] = "atlas-extraction-cache.v99"
    elif mutation == "key":
        item["key"] = "0" * 64
    elif mutation == "inputs":
        item["inputs"]["source_sha256"] = "0" * 64
    elif mutation == "checksum":
        item["checksum"] = "0" * 64
    elif mutation == "failed":
        item.update(
            state="failed", packet=None, failure={"reason": "parse_error", "location": None}
        )
    elif mutation == "bool":
        p["refs"][0]["line"] = True
    elif mutation == "resolved":
        p["refs"][0]["target_entity_id"] = "0" * 64
    elif mutation == "link":
        p["refs"][0]["occurrence_id"] = p["refs"][1]["occurrence_id"]
    elif mutation == "path":
        p["symbols"][0]["path"] = "elsewhere.py"
    elif mutation == "source":
        next(r for r in p["records"] if r["type"] == "occurrence")["source_sha256"] = "0" * 64
    elif mutation == "producer":
        next(r for r in p["records"] if r["type"] == "producer")["fingerprint"]["modules"].pop(
            "indexing/extraction_cache.py"
        )
    elif mutation in {"scope", "byte"}:
        b = json.loads(p["refs"][0]["binding"])
        b[mutation] = "python:foreign.py:root" if mutation == "scope" else 0
        p["refs"][0]["binding"] = json.dumps(b)
    elif mutation == "rationale_line":
        p["rationale"][0]["line"] = 99999
    if mutation == "checksum":
        path.write_text(json.dumps(item))
    else:
        store(path, item)
    counts.update(parser=0, extract=0)
    stats = graph.build()
    assert stats["extraction_cache"]["disk_hits"] == 2
    assert stats["extraction_cache"]["misses"] == 1
    assert counts == {"parser": 1, "extract": 1}
    graph.close()


@pytest.mark.parametrize("component", ["extraction-cache", "v1", "prefix", "entry"])
def test_cache_symlink_cannot_escape_namespace(tmp_path, monkeypatch, component):
    import shutil

    counts = counted(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def A(): pass\n")
    graph = CodeGraph(repo, graph_root=tmp_path / "graphs")
    graph.build()
    entry = next((graph.atlas_dir / "extraction-cache/v1").glob("*/*.json"))
    selected = {
        "extraction-cache": graph.atlas_dir / "extraction-cache",
        "v1": entry.parent.parent,
        "prefix": entry.parent,
        "entry": entry,
    }[component]
    destination = repo / "redirected"
    if selected.is_dir():
        shutil.move(str(selected), destination)
        selected.symlink_to(destination, target_is_directory=True)
    else:
        selected.replace(destination)
        selected.symlink_to(destination)
    before = {str(p.relative_to(repo)): p.read_bytes() for p in repo.rglob("*") if p.is_file()}
    counts.update(parser=0, extract=0)
    stats = graph.build()
    assert counts == {"parser": 1, "extract": 1}
    assert stats["extraction_cache"]["write_failures"] == 1
    assert graph.search_symbol("A")["definitions"]
    assert {
        str(p.relative_to(repo)): p.read_bytes() for p in repo.rglob("*") if p.is_file()
    } == before
    assert not (repo / ".atlas").exists()
    graph.close()


def test_failed_retry_recovery_and_global_resolution(tmp_path, monkeypatch):
    counts = counted(monkeypatch)
    graph, _ = prepare(tmp_path)
    repo = graph.repo
    old = graph.query("callers_of:B")["edges"]
    assert len(old) == 2
    (repo / "pkg/b.py").write_text("def B(:\n pass\n")
    counts.update(parser=0, extract=0)
    stats = graph.build()
    assert stats["extraction_cache"]["disk_hits"] == 2
    assert counts == {"parser": 1, "extract": 0}
    assert not graph.query("callers_of:B")["edges"]
    assert graph.search_symbol("B")["historical_definitions"]
    assert not graph.confident_edges()
    counts.update(parser=0, extract=0)
    stats = graph.build(since="HEAD")
    assert counts == {"parser": 1, "extract": 0}
    assert stats["extraction_cache"]["miss_reasons"] == {"failed": 1}
    assert stats["extraction_cache"]["predecessor_reused"] == 2
    (repo / "pkg/b.py").write_text("def B(): pass\n")
    counts.update(parser=0, extract=0)
    stats = graph.build()
    # Exact old valid bytes can reuse a complete packet: this is a new read,
    # not promotion of a failed observation or cached resolved edge.
    assert counts == {"parser": 0, "extract": 0}
    assert stats["extraction_cache"]["disk_hits"] == 3
    assert len(graph.query("callers_of:B")["edges"]) == 2
    assert not graph.search_symbol("B").get("historical_definitions")
    (repo / "pkg/b.py").unlink()
    counts.update(parser=0, extract=0)
    graph.build()
    assert counts == {"parser": 0, "extract": 0}
    assert not graph.query("callers_of:B")["edges"]
    graph.close()


def test_path_bound_packets_and_configuration(tmp_path, monkeypatch):
    counts = counted(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    for name in ("a.py", "b.py"):
        (repo / name).write_text("def same(): pass\n")
    graph = CodeGraph(repo)
    graph.build()
    ids = {
        r["id"] for r in graph.evidence_records() if r["type"] == "entity" and r["name"] == "same"
    }
    assert len(ids) == 2
    (repo / "b.py").rename(repo / "c.py")
    counts.update(parser=0, extract=0)
    stats = graph.build()
    assert stats["extraction_cache"]["disk_hits"] == 1
    assert counts == {"parser": 1, "extract": 1}
    assert {r["path"] for r in graph.evidence_records() if r["type"] == "entity"} == {
        "a.py",
        "c.py",
    }
    graph.close()
    counts.update(parser=0, extract=0)
    disabled = CodeGraph(repo, langs=["ruby"])
    disabled.build()
    assert counts == {"parser": 0, "extract": 0}
    assert all(r["state"] == "disabled" for r in disabled.observation_records())
    disabled.close()


def test_cache_io_optional_but_provider_failure_fatal(tmp_path, monkeypatch):
    counts = counted(monkeypatch)
    graph, _ = prepare(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(xc.ExtractionCache, "write", lambda *args: False)
        for path in (graph.atlas_dir / "extraction-cache/v1").glob("*/*.json"):
            path.unlink()
        counts.update(parser=0, extract=0)
        stats = graph.build()
        assert counts == {"parser": 3, "extract": 3}
        assert stats["extraction_cache"]["write_failures"] == 3
    graph.build()
    previous_bytes = graph.db_path.read_bytes()
    previous_records = graph.evidence_records()
    # Held reader remains on G1 while real extraction happens before a fatal
    # injected provider error; the cache never turns it into failed-success.
    with graph.reader_lease() as held:
        (graph.repo / "pkg/b.py").write_text("def changed(): pass\n")
        real = CodeGraph._extract
        with monkeypatch.context() as patch:

            def failing(self, *args):
                result = real(self, *args)
                if args[2] == "pkg/b.py":
                    raise RuntimeError("controlled fatal after real extraction")
                return result

            patch.setattr(CodeGraph, "_extract", failing)
            with pytest.raises(RuntimeError, match="controlled fatal"):
                graph.build()
        assert graph.db_path.read_bytes() == previous_bytes
        assert list(ev.records(held.db)) == previous_records
        assert not list(graph.atlas_dir.glob(".graph.*.tmp.*"))
    graph.close()


def test_read_once_and_warm_pinned_lease(tmp_path, monkeypatch):
    counts = counted(monkeypatch)
    graph, _ = prepare(tmp_path)
    before = graph.evidence_records()
    path = graph.repo / "pkg/b.py"
    path.write_bytes(b"\n" + path.read_bytes())
    original_read = type(path).read_bytes
    reads = []

    def read(self):
        if self.suffix == ".py" and self.is_relative_to(graph.repo):
            reads.append(str(self.relative_to(graph.repo)))
        return original_read(self)

    with graph.reader_lease() as held:
        with monkeypatch.context() as patch:
            patch.setattr(type(path), "read_bytes", read)
            counts.update(parser=0, extract=0)
            stats = graph.build()
        assert sorted(reads) == ["pkg/__init__.py", "pkg/a.py", "pkg/b.py"]
        assert stats["extraction_cache"]["disk_hits"] == 2
        assert counts == {"parser": 1, "extract": 1}
        assert list(ev.records(held.db)) == before
        assert held.search_symbol("B")["definitions"][0]["line_start"] == 1
    assert graph.search_symbol("B")["definitions"][0]["line_start"] == 2
    graph.close()


def test_readonly_operations_never_touch_cache(tmp_path, monkeypatch):
    graph, _ = prepare(tmp_path)
    graph.close()
    repo = graph.repo
    cache_dir = graph.atlas_dir / "extraction-cache"
    before = {
        str(p.relative_to(cache_dir)): (p.stat().st_mtime_ns, p.read_bytes())
        for p in cache_dir.rglob("*")
        if p.is_file()
    }

    def forbidden(*args):
        raise AssertionError("read-only operation used extraction cache")

    monkeypatch.setattr(xc.ExtractionCache, "lookup", forbidden)
    monkeypatch.setattr(xc.ExtractionCache, "write", forbidden)
    reader = CodeGraph(repo, read_only=True)
    with reader.reader_lease() as lease:
        assert lease.query("callers_of:B")["edges"]
        assert lease.status()["indexed"]
        lease.export_jsonl(tmp_path / "portable.jsonl")
    reader.close()
    destination = tmp_path / "imported"
    destination.mkdir()
    imported = CodeGraph(destination)
    imported.import_jsonl(tmp_path / "portable.jsonl")
    assert not (imported.atlas_dir / "extraction-cache").exists()
    imported.close()
    assert {
        str(p.relative_to(cache_dir)): (p.stat().st_mtime_ns, p.read_bytes())
        for p in cache_dir.rglob("*")
        if p.is_file()
    } == before


def test_interrupted_atomic_cache_writer(tmp_path, monkeypatch):
    import os
    import selectors
    import subprocess
    import sys

    counts = counted(monkeypatch)
    graph, path = prepare(tmp_path)
    entry = json.loads(path.read_text())
    path.unlink()
    payload_path = tmp_path / "packet.json"
    payload_path.write_text(json.dumps(entry))
    script = """
import json,sys
from pathlib import Path
from atlas_aci.indexing import extraction_cache as xc
from atlas_aci.storage import evidence as ev
item=json.loads(Path(sys.argv[2]).read_text())
cache=xc.ExtractionCache(Path(sys.argv[1]))
original=xc.os.replace
def barrier(a,b):
 print("READY",flush=True)
 sys.stdin.readline()
 original(a,b)
xc.os.replace=barrier
cache.write(item["inputs"],xc.ExtractionPacket(item["packet"]))
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(graph.atlas_dir), str(payload_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(10), "cache writer did not reach pre-replace barrier"
        assert process.stdout.readline().strip() == b"READY"
        assert not path.exists()
        assert list(path.parent.glob(".entry-*"))
        source = (graph.repo / "pkg/a.py").read_bytes()
        assert xc.ExtractionCache(graph.atlas_dir).lookup(entry["inputs"], source) == (
            None,
            "missing",
        )
        process.terminate()
        process.communicate(timeout=10)
        counts.update(parser=0, extract=0)
        stats = graph.build()
        assert stats["extraction_cache"]["miss_reasons"] == {"missing": 1}
        assert counts == {"parser": 1, "extract": 1}
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)
        graph.close()


def test_parser_package_and_skip_inputs_invalidate(tmp_path, monkeypatch):
    import atlas_aci.config as config

    counts = counted(monkeypatch)
    graph, _ = prepare(tmp_path)
    original = CodeGraph._producer_inputs
    with monkeypatch.context() as patch:

        def changed(self):
            result = original(self)
            result["packages"]["tree-sitter"] += "+test-version"
            return result

        patch.setattr(CodeGraph, "_producer_inputs", changed)
        counts.update(parser=0, extract=0)
        graph.build(since="HEAD")
        assert counts == {"parser": 3, "extract": 3}
    with monkeypatch.context() as patch:
        patch.setattr(
            config,
            "DEFAULT_SKIP_PATTERNS",
            [*config.DEFAULT_SKIP_PATTERNS, "never-match-this-fixture"],
        )
        counts.update(parser=0, extract=0)
        graph.build(since="HEAD")
        assert counts == {"parser": 3, "extract": 3}
    graph.close()


def test_exact_producer_module_shapes():
    fingerprint = {
        "codegraph_sha256": "1" * 64,
        "queries_sha256": "2" * 64,
        "packages": {"tree-sitter": "0.25.2", "tree-sitter-language-pack": "1.6.0"},
    }
    ev.validate_record(ev.producer(fingerprint, legacy=True))
    fingerprint["modules"] = {
        "storage/evidence.py": "3" * 64,
        "providers/python_imports.py": "4" * 64,
    }
    ev.validate_record(ev.producer(fingerprint))  # exact historical S07 shape
    fingerprint["modules"]["indexing/extraction_cache.py"] = "5" * 64
    ev.validate_record(ev.producer(fingerprint))
    fingerprint["modules"]["arbitrary.py"] = "6" * 64
    with pytest.raises(ValueError, match="module set"):
        ev.validate_record(ev.producer(fingerprint))
    del fingerprint["modules"]["arbitrary.py"]
    del fingerprint["modules"]["storage/evidence.py"]
    with pytest.raises(ValueError, match="module set"):
        ev.validate_record(ev.producer(fingerprint))


def test_two_index_processes_share_writer_lock(tmp_path):
    import os
    import selectors
    import subprocess
    import sys

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def A(): pass\n")
    script = """
import fcntl,json,sys
from pathlib import Path
from atlas_aci.codegraph import CodeGraph
mode=sys.argv[2]
original=CodeGraph._run_build
if mode=="first":
 def blocked(self,*args,**kwargs):
  print("HELD",flush=True)
  sys.stdin.readline()
  return original(self,*args,**kwargs)
 CodeGraph._run_build=blocked
else:
 lock=fcntl.flock
 def attempt(fd,operation):
  if operation==fcntl.LOCK_EX: print("ATTEMPT",flush=True)
  return lock(fd,operation)
 fcntl.flock=attempt
graph=CodeGraph(Path(sys.argv[1]))
print(json.dumps(graph.build()),flush=True)
graph.close()
"""
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    processes = []

    def launch(mode):
        process = subprocess.Popen(
            [sys.executable, "-c", script, str(repo), mode],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
        processes.append(process)
        return process

    def marker(process, expected):
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(15), "writer barrier deadline"
        assert process.stdout.readline().decode().strip() == expected

    try:
        first = launch("first")
        marker(first, "HELD")  # real build is inside shared writer lock
        second = launch("second")
        marker(second, "ATTEMPT")  # actual LOCK_EX called while first holds it
        first.stdin.write(b"release\n")
        first.stdin.flush()
        out1, err1 = first.communicate(timeout=15)
        out2, err2 = second.communicate(timeout=15)
        assert first.returncode == second.returncode == 0, (err1, err2)
        one, two = json.loads(out1.splitlines()[-1]), json.loads(out2.splitlines()[-1])
        assert one["extraction_cache"]["parser_invocations"] == 1
        assert two["extraction_cache"]["parser_invocations"] == 0
        assert two["extraction_cache"]["disk_hits"] == 1
        graph = CodeGraph(repo, read_only=True)
        assert graph.search_symbol("A")["definitions"]
        graph.close()
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)
