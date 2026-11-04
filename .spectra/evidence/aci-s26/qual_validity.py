"""Day-1 (c): record validity on a hand-built database (scratch copy of the tree with EV1-EV5 and
CG6/CG7 applied; the build itself is base code). Scratch evidence, not product code.

    python qual_validity.py <fixture-project-dir>
"""
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

from atlas_aci.codegraph import CodeGraph
from atlas_aci.frameworks.rails_models import Span
from atlas_aci.storage import evidence as ev

fixture = Path(sys.argv[1])
work = Path(tempfile.mkdtemp())
repo = work / "repo"
shutil.copytree(fixture, repo)
graph = CodeGraph(repo, graph_root=work / "g")
graph.build()
db_copy = work / "copy.db"
shutil.copy(graph.db_path, db_copy)
conn = sqlite3.connect(db_copy)
conn.row_factory = sqlite3.Row
path = "app/services/enqueuer.rb"
source = (repo / path).read_bytes()


def span_of(text: str, nth: int = 0) -> Span:
    start = -1
    for _ in range(nth + 1):
        start = source.index(text.encode(), start + 1)
    end = start + len(text.encode())
    row = source[:start].count(b"\n")
    col = start - (source[:start].rfind(b"\n") + 1)
    end_row = source[:end].count(b"\n")
    end_col = end - (source[:end].rfind(b"\n") + 1)
    return Span(start, end, (row, col), (end_row, end_col))


def entity(name, kind, p):
    return conn.execute(
        "SELECT entity_id FROM symbols WHERE path=? AND kind=? AND name=? LIMIT 1", (p, kind, name)
    ).fetchone()[0]


def perform_of(p):
    return conn.execute(
        "SELECT entity_id FROM symbols WHERE path=? AND kind='method' ORDER BY line_start LIMIT 1",
        (p,),
    ).fetchone()[0]


run_method = conn.execute(
    "SELECT entity_id FROM symbols WHERE path=? AND kind='method' ORDER BY line_start LIMIT 1",
    (path,),
).fetchone()[0]
file_entity = ev.file_entity(path, "ruby")["id"]
target = perform_of("app/workers/hard_worker.rb")
other = perform_of("app/jobs/export_job.rb")
other2 = perform_of("lib/legacy/export_job.rb")
klass = conn.execute("SELECT entity_id FROM symbols WHERE kind='class' LIMIT 1").fetchone()[0]
native = next(
    json.loads(p)["id"]
    for (p,) in conn.execute("SELECT payload FROM producers")
    if "codegraph_sha256" in (json.loads(p)["fingerprint"] or {})
)
producer = ev.producer(
    {
        "rails_jobs": {
            "adapter": "rails-jobs.v1",
            "adapter_sha256": "a" * 64,
            "graph_producer_id": native,
        }
    }
)
result = {}
enqueue_span = span_of("HardWorker.perform_async(user.id)")
raw_ok = ev.canonical(
    {
        "arguments": "mapped",
        "arity": 1,
        "kind": "perform_async",
        "mapping": "rails_job_receiver.v1",
        "mechanism": "sidekiq",
        "receiver": "HardWorker",
    }
)


def build(role, span, source_entity, relation, resolution, tgt, raw, candidates=(), reason=None):
    occ = ev.occurrence(span, path, "ruby", source, source_entity, "rails_job", role)
    proof = ev.provenance(occ, producer["id"])
    return occ, proof, ev.assertion(
        source_entity, tgt, relation, proof["id"], resolution, raw, list(candidates), reason
    )


def check(name, record):
    try:
        ev.validate_record(record)
        result[name] = "accepted"
    except ValueError as exc:
        result[name] = "rejected: " + str(exc)


arg_raw = None
enq = build("rails_job:enqueue:perform_async", enqueue_span, run_method, "async_enqueue", "INFERRED", target, raw_ok)
arg_raw = ev.canonical(
    {
        "arg": 0,
        "enqueue": enq[0]["id"],
        "mapping": "rails_job_positional.v1",
        "param": 0,
        "param_name": "user_id",
    }
)
check("async_enqueue INFERRED with target", enq[2])
check(
    "async_argument INFERRED with target",
    build("rails_job:argument:mapped", span_of("user.id"), run_method, "async_argument", "INFERRED", target, arg_raw)[2],
)
for relation, raw in (("async_enqueue", raw_ok), ("async_argument", arg_raw)):
    for resolution, tgt in (("EXTRACTED", target), ("EXTRACTED", None), ("INFERRED", None)):
        check(
            f"{relation} {resolution} {'with' if tgt else 'no'} target",
            ev.assertion(run_method, tgt, relation, enq[1]["id"], resolution, raw, [], None),
        )
check(
    "async_argument AMBIGUOUS",
    ev.assertion(run_method, None, "async_argument", enq[1]["id"], "AMBIGUOUS", arg_raw, sorted([other, other2]), "ambiguous_job_target"),
)
check(
    "async_enqueue AMBIGUOUS",
    ev.assertion(run_method, None, "async_enqueue", enq[1]["id"], "AMBIGUOUS", raw_ok, sorted([other, other2]), "ambiguous_job_target"),
)
check(
    "async_enqueue UNRESOLVED",
    ev.assertion(run_method, None, "async_enqueue", enq[1]["id"], "UNRESOLVED", raw_ok, [], "job_class_not_found"),
)
check("producer rails_jobs", producer)
for bad in (
    {"adapter": "rails-jobs.v1", "adapter_sha256": "a" * 64},
    {"adapter": "x", "adapter_sha256": "a" * 64, "graph_producer_id": native},
):
    check("producer bad " + sorted(bad)[0] + str(len(bad)), ev.producer({"rails_jobs": bad}))
bad_occ = ev.occurrence(enqueue_span, path, "ruby", source, run_method, "rails_job", "rails_model:x")
check("occurrence rails_job with a foreign role prefix", bad_occ)
check("occurrence non-job kind with job role", ev.occurrence(enqueue_span, path, "ruby", source, run_method, "reference", "rails_job:enqueue:perform_async"))

# hand-built database: one record per role
spans = {
    "unsupported": span_of("job_class.perform_later(1)"),
    "diag_arg": span_of("*ids"),
}
records = [
    enq,
    build("rails_job:argument:mapped", span_of("user.id"), run_method, "async_argument", "INFERRED", target, arg_raw),
    build(
        "rails_job:enqueue:perform_later",
        span_of("ExportJob.perform_later(1)"),
        run_method,
        "async_enqueue",
        "AMBIGUOUS",
        None,
        ev.canonical({"arguments": "unbound", "arity": 1, "kind": "perform_later", "mapping": "rails_job_receiver.v1", "mechanism": "active_job", "receiver": "ExportJob"}),
        sorted([other, other2]),
        "ambiguous_job_target",
    ),
    build(
        "rails_job:enqueue:unsupported:dynamic_receiver",
        spans["unsupported"],
        run_method,
        "async_enqueue",
        "UNRESOLVED",
        None,
        ev.canonical({"construct": "dynamic_receiver", "kind": "perform_later", "mapping": "unexpanded"}),
        (),
        "unsupported_argument_mapping",
    ),
]
enq_u = build(
    "rails_job:enqueue:perform_async",
    span_of("HardWorker.perform_async(*ids)"),
    file_entity,
    "async_enqueue",
    "INFERRED",
    target,
    ev.canonical({"arguments": "unsupported", "arity": 1, "construct": "splat", "kind": "perform_async", "mapping": "rails_job_receiver.v1", "mechanism": "sidekiq", "receiver": "HardWorker"}),
)
records.append(enq_u)
records.append(
    build(
        "rails_job:argument:unsupported:splat",
        spans["diag_arg"],
        file_entity,
        "async_argument",
        "UNRESOLVED",
        None,
        ev.canonical({"construct": "splat", "enqueue": enq_u[0]["id"], "mapping": "unexpanded"}),
        (),
        "unsupported_argument_mapping",
    )
)
ev.put(conn, producer)
for occ, proof, assertion in records:
    ev.put(conn, occ, path)
    ev.put(conn, proof, path)
    ev.put(conn, assertion, path)
ev.validate_database(conn)
result["validate_database on hand-built db (6 records, all roles)"] = "pass"
graph._validate_evidence_projections(conn)
result["_validate_evidence_projections on hand-built db (CG6/CG7)"] = "pass"
for name, relations in (("ev.RELATIONS", ev.RELATIONS), ("REFERENCE_RELATIONS", ev.REFERENCE_RELATIONS), ("EDGE_RELATIONS", ev.EDGE_RELATIONS)):
    result[name + " has no async relation"] = not (relations & {"async_enqueue", "async_argument"})
result["ASSERTION_RELATIONS has both"] = {"async_enqueue", "async_argument"} <= ev.ASSERTION_RELATIONS
# a planted job occurrence on a class entity of an unrelated kind, wrong source entity
try:
    ev.put(conn, ev.occurrence(enqueue_span, path, "ruby", source, klass, "rails_job", "rails_job:enqueue:perform_async"), path)
    result["occurrence on a class entity from another file (location check)"] = "accepted"
except ValueError as exc:
    result["occurrence on a class entity from another file (location check)"] = "rejected: " + str(exc)
conn.commit()
try:
    ev.validate_database(conn)
    result["validate_database with the stray occurrence"] = "pass"
except ValueError as exc:
    result["validate_database with the stray occurrence"] = "rejected: " + str(exc)
print(json.dumps(result, indent=1, sort_keys=True))
