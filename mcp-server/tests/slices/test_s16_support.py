"""Shared ACI-S16 helpers: the real scip-typescript 0.4.0 artifact, its labels, a built graph."""

import functools
import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

from atlas_aci.codegraph import CodeGraph
from atlas_aci.storage import evidence as ev

FIXTURE = Path(__file__).parents[1] / "fixtures/scip"
INDEX = FIXTURE / "index.scip"
MANIFEST = FIXTURE / "atlas-scip-manifest.json"
LABELS = json.loads((FIXTURE / "labels.json").read_text())
PROVENANCE = json.loads((FIXTURE / "provenance.json").read_text())
ARTIFACT_SHA256 = "b1cedcc22c4af4faa1867dfa1b0fd63eccc6551857ffa2e4753e34635108f31f"
SCHEMA_SHA256 = "b855eb9abdb3fe7f07600ee92f24a6c40f75fceef37136ccdc6844b300936a31"


@functools.cache
def official():
    """The committed official protoc bindings (dev-only protobuf runtime)."""
    spec = importlib.util.spec_from_file_location("scip_pb2_official", FIXTURE / "scip_pb2.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def workspace(tmp_path, name="repo"):
    """The producer's working tree: project sources only (no node_modules, no lock)."""
    repo = tmp_path / name
    shutil.copytree(
        FIXTURE / "project",
        repo,
        ignore=shutil.ignore_patterns("node_modules", "package-lock.json"),
    )
    return repo


def built(tmp_path, name="repo", **options):
    repo = workspace(tmp_path, name)
    graph = CodeGraph(repo, **options)
    graph.build()
    return repo, graph


def imported(tmp_path, name="repo", index=INDEX, manifest=MANIFEST, **options):
    repo, graph = built(tmp_path, name, **options)
    result = graph.import_scip(index, manifest)
    return repo, graph, result


def published_bytes(graph):
    """Every byte the published graph consists of: db file plus the snapshot store."""
    state = {"db": graph.db_path.read_bytes()}
    store = graph.atlas_dir / "source-snapshots"
    if store.exists():
        state["store"] = {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(store.iterdir())
        }
    return state


def stage_leftovers(graph):
    return sorted(
        p.name for p in graph.atlas_dir.iterdir() if ".tmp." in p.name or ".stage" in p.name
    )


def current_id(graph):
    reader = CodeGraph(graph.repo, read_only=True, graph_root=graph.graph_root)
    try:
        return reader.generation_id
    finally:
        reader.close()


def records(graph):
    with graph.reader_lease() as lease:
        return list(ev.records(lease.db))


def scip_records(graph):
    from atlas_aci.providers import scip

    everything = records(graph)
    chosen = scip.scip_record_ids(everything)
    return [r for r in everything if r["id"] in chosen]


def rows(graph, sql, *args):
    with graph.reader_lease() as lease:
        return [dict(row) for row in lease.db.execute(sql, args)]


def snippet(repo, occurrence):
    source = (repo / occurrence["path"]).read_bytes()
    assert hashlib.sha256(source).hexdigest() == occurrence["source_sha256"]
    return source[occurrence["start_byte"] : occurrence["end_byte"]].decode()


def write_manifest(tmp_path, repo, index=INDEX, encoding="utf-16", name="manifest.json"):
    from atlas_aci.providers import scip

    target = tmp_path / name
    target.write_bytes(scip.build_manifest(repo, index, encoding))
    return target


def manifest_for(tmp_path, index_bytes, name="crafted-manifest.json", **changes):
    """A syntactically perfect manifest for arbitrary index bytes (isolates decode defects)."""
    base = json.loads(MANIFEST.read_text())
    base["index_sha256"] = hashlib.sha256(index_bytes).hexdigest()
    base.update(changes)
    target = tmp_path / name
    target.write_text(ev.canonical(base) + "\n")
    return target


def craft(tmp_path, mutate, name="crafted.scip"):
    """Official bindings parse the real artifact, ``mutate`` edits it, then it is re-serialized."""
    index = official().Index()
    index.ParseFromString(INDEX.read_bytes())
    mutate(index)
    target = tmp_path / name
    target.write_bytes(index.SerializeToString())
    return target


def scip_view(graph):
    """(definitions, references) of the published SCIP layer as comparable tuples."""
    defs, refs = [], []
    for row in rows(graph, "SELECT * FROM symbols WHERE kind LIKE 'scip\\_%' ESCAPE '\\'"):
        defs.append(
            (row["path"], json.loads(row["binding"])["scip"]["symbol"], row["occurrence_id"], row)
        )
    for row in rows(graph, "SELECT * FROM refs WHERE relation='reference'"):
        refs.append((row["path"], row["callee_name"], row["occurrence_id"], row))
    return defs, refs


def occurrence_index(graph):
    return {r["id"]: r for r in records(graph) if r["type"] == "occurrence"}
