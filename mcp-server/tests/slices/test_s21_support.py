"""Shared ACI-S21 helpers: the real coverage.py 7.16.2 runs, their labels, a built graph."""

import hashlib
import json
import shutil
from pathlib import Path

from test_s16_support import current_id, published_bytes, rows, stage_leftovers

from atlas_aci.codegraph import CodeGraph
from atlas_aci.providers import coverage_py
from atlas_aci.storage import evidence as ev

__all__ = ["current_id", "published_bytes", "rows", "stage_leftovers"]

FIXTURE = Path(__file__).parents[1] / "fixtures/coverage"
PROJECT = FIXTURE / "project"
LABELS = json.loads((FIXTURE / "labels.json").read_text())["runs"]
PROVENANCE = json.loads((FIXTURE / "provenance.json").read_text())
RUNS = {
    run: {
        "report": FIXTURE / "runs" / run / "coverage.json",
        "manifest": FIXTURE / "runs" / run / "atlas-coverage-manifest.json",
        "environment": FIXTURE / "runs" / run / "environment.json",
    }
    for run in ("a", "b")
}
A, B = RUNS["a"], RUNS["b"]


def workspace(tmp_path, name="repo"):
    """The producer's working tree: the nested fixture project, byte-exact."""
    repo = tmp_path / name
    shutil.copytree(PROJECT, repo)
    return repo


def built(tmp_path, name="repo", root=None, **options):
    repo = workspace(tmp_path, name)
    graph = CodeGraph(repo, graph_root=root, **options)
    graph.build()
    return repo, graph


def imported(tmp_path, run="a", name="repo", root=None, **options):
    repo, graph = built(tmp_path, name, root, **options)
    result = graph.import_coverage(RUNS[run]["report"], RUNS[run]["manifest"])
    return repo, graph, result


def import_run(graph, run):
    return graph.import_coverage(RUNS[run]["report"], RUNS[run]["manifest"])


def report_json(run="a"):
    return json.loads(RUNS[run]["report"].read_text())


def write_json(tmp_path, data, name):
    target = tmp_path / name
    target.write_text(json.dumps(data, sort_keys=True))
    return target


def edited_report(tmp_path, mutate, name="edited.json", run="a"):
    data = report_json(run)
    mutate(data)
    return write_json(tmp_path, data, name)


def remade_manifest(tmp_path, repo, report, run="a", name="remade.json"):
    """A sidecar re-made by the real `coverage-manifest` code over ``repo`` and ``report``."""
    target = tmp_path / name
    target.write_bytes(coverage_py.build_manifest(repo, report, RUNS[run]["environment"]))
    return target


def edited_manifest(tmp_path, mutate, name="edited-manifest.json", run="a"):
    data = json.loads(RUNS[run]["manifest"].read_text())
    mutate(data)
    target = tmp_path / name
    target.write_text(ev.canonical(data) + "\n")
    return target


def records(graph):
    with graph.reader_lease() as lease:
        return list(ev.records(lease.db))


def coverage_records(graph):
    everything = records(graph)
    chosen = coverage_py.record_ids(everything)
    return [r for r in everything if r["id"] in chosen]


def coverage_producers(graph):
    return [
        r
        for r in records(graph)
        if r["type"] == "producer" and "coverage" in (r["fingerprint"] or {})
    ]


def inputs_of(graph):
    with graph.reader_lease() as lease:
        return json.loads(
            lease.db.execute("SELECT value FROM manifest WHERE key='generation_inputs'").fetchone()[
                0
            ]
        )


def status_of(repo, root=None):
    reader = CodeGraph(repo, read_only=True, graph_root=root)
    try:
        return reader.status()
    finally:
        reader.close()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def run_id(run):
    return coverage_py.run_id_of(
        PROVENANCE["runs"][run]["artifact_sha256"], PROVENANCE["runs"][run]["manifest_sha256"]
    )


def observed(graph):
    """{(nodeid, path, line): (role, occurrence, producer_id)} of every `test_executes` record."""
    out = {}
    by_id = {r["id"]: r for r in records(graph)}
    for item in by_id.values():
        if item["type"] != "assertion" or item["relation"] != ev.COVERAGE_RELATION:
            continue
        source = by_id[item["source_entity_id"]]
        proof = by_id[item["evidence_id"]]
        occurrence = by_id[proof["occurrence_id"]]
        out.setdefault((source["name"], occurrence["path"], occurrence["line"]), []).append(
            (item, occurrence, proof["producer_id"])
        )
    return out


def canonical_set(graph):
    """What must be reproducible: coverage records, the block and the status block."""
    status = status_of(graph.repo, graph.graph_root)
    return (
        sorted(ev.canonical(r) for r in coverage_records(graph)),
        ev.canonical(inputs_of(graph)["coverage"]),
        ev.canonical(status["execution_observations"]),
    )
