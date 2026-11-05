"""Shared ACI-S26 helpers: the Rails jobs fixture, real builds, shared-store fact readers."""

import atexit
import hashlib
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

from atlas_aci.codegraph import CodeGraph
from atlas_aci.frameworks import rails_jobs
from atlas_aci.storage import evidence as ev

FIXTURE = Path(__file__).parents[1] / "fixtures/rails_jobs"
PROJECT = FIXTURE / "project"
PROVENANCE = json.loads((FIXTURE / "provenance.json").read_text())
ENQUEUER = "app/services/enqueuer.rb"
NIGHTLY = "app/services/billing/nightly.rb"
OUTER = "app/services/outer.rb"
DAILY = "app/services/reports/daily.rb"
SPEC = "spec/jobs/report_job_spec.rb"
REPORT_JOB = "app/jobs/report_job.rb"
JOBS = ("rails_jobs",)
RELATIONS = ("async_enqueue", "async_argument")
SENTINELS = (
    "ATLAS_S26_SENTINEL_AFTER_ENQUEUE",
    "ATLAS_S26_SENTINEL_BEFORE_PERFORM",
    "ATLAS_S26_SENTINEL_CLASS_BODY",
    "ATLAS_S26_SENTINEL_ASYNC",
    "ATLAS_S26_SENTINEL_LATER",
)


def workspace(tmp_path, name="repo"):
    repo = tmp_path / name
    shutil.copytree(PROJECT, repo)
    return repo


_WARM: list[Path] = []


def warm_cache():
    """The extraction cache of one real build of the fixture (built once per process).

    Copying it into a new graph root only skips tree-sitter extraction of already-seen file
    contents (the cache is keyed by content); the adapter parses outside the cache, and every
    fact still comes from a real, full build.
    """
    if not _WARM:
        base = Path(tempfile.mkdtemp(prefix="atlas-s26-warm-"))
        atexit.register(shutil.rmtree, base, True)
        repo = base / "repo"
        shutil.copytree(PROJECT, repo)
        graph = CodeGraph(repo, graph_root=base / "g")
        graph.build()
        _WARM.append(graph.atlas_dir / "extraction-cache")
    return _WARM[0]


def prime(graph):
    target = graph.atlas_dir / "extraction-cache"
    if not target.exists():
        shutil.copytree(warm_cache(), target)
    return graph


def make(repo, tmp_path, root, frameworks, warm):
    graph = CodeGraph(repo, graph_root=tmp_path / root, frameworks=frameworks)
    return prime(graph) if warm else graph


def built(tmp_path, name="repo", root="g", frameworks=JOBS, repo=None, since=None, warm=True):
    repo = repo or workspace(tmp_path, name)
    graph = make(repo, tmp_path, root, frameworks, warm)
    graph.build(since=since)
    return repo, graph


def rebuilt(repo, tmp_path, root, frameworks=JOBS, since=None, warm=True):
    graph = make(repo, tmp_path, root, frameworks, warm)
    return graph, graph.build(since=since)


def records(graph):
    with graph.reader_lease() as lease:
        return list(ev.records(lease.db))


def job_records(graph):
    everything = records(graph)
    chosen = rails_jobs.record_ids(everything)
    return [r for r in everything if r["id"] in chosen]


def job_lines(graph):
    """The canonical rails_jobs fact set: sorted canonical JSON lines."""
    return sorted(ev.canonical(r) for r in job_records(graph))


def manifest_value(graph, key="rails_jobs"):
    with graph.reader_lease() as lease:
        row = lease.db.execute("SELECT value FROM manifest WHERE key=?", (key,)).fetchone()
    return None if row is None else row[0]


def facts(graph, repo=None):
    """Every rails_jobs fact joined to its evidence and occurrence (and its source text)."""
    result = []
    with graph.reader_lease() as lease:
        db = lease.db
        for relation in RELATIONS:
            for (payload,) in db.execute(
                "SELECT payload FROM assertions WHERE json_extract(payload,'$.relation')=?",
                (relation,),
            ):
                assertion = json.loads(payload)
                proof = ev.get(db, "evidence", assertion["evidence_id"])
                occurrence = ev.get(db, "occurrence", proof["occurrence_id"])
                item = {
                    "assertion": assertion,
                    "evidence": proof,
                    "occurrence": occurrence,
                    "raw": json.loads(assertion["raw_target"]),
                    "area": occurrence["role"].split(":")[1],
                }
                if repo is not None:
                    data = (repo / occurrence["path"]).read_bytes()
                    item["text"] = data[occurrence["start_byte"] : occurrence["end_byte"]].decode()
                result.append(item)
    return sorted(
        result,
        key=lambda f: (
            f["occurrence"]["path"],
            f["occurrence"]["start_byte"],
            f["occurrence"]["end_byte"],
            f["occurrence"]["role"],
        ),
    )


def enqueues(found, path=None, text=None):
    """The enqueue-area facts (regular and diagnostic), optionally of one file / call text."""
    return [
        f
        for f in found
        if f["area"] == "enqueue"
        and (path is None or f["occurrence"]["path"] == path)
        and (text is None or f.get("text") == text)
    ]


def one(found, path, text):
    chosen = enqueues(found, path, text)
    assert len(chosen) == 1, (path, text, len(chosen))
    return chosen[0]


def arguments_of(found, enqueue):
    """The argument-area facts that name this enqueue occurrence."""
    return sorted(
        (
            f
            for f in found
            if f["area"] == "argument" and f["raw"]["enqueue"] == enqueue["occurrence"]["id"]
        ),
        key=lambda f: f["occurrence"]["start_byte"],
    )


def symbol_rows(graph, path=None):
    with graph.reader_lease() as lease:
        rows = lease.db.execute(
            "SELECT s.path, s.kind, s.name, s.entity_id, s.binding, s.occurrence_id "
            "FROM symbols s WHERE s.lang='ruby'"
            + ("" if path is None else " AND s.path=?")
            + " ORDER BY s.path, s.line_start, s.id",
            () if path is None else (path,),
        ).fetchall()
    return [tuple(r) for r in rows]


def perform_ids(graph, namespace, path=None):
    """Independent SQL on `symbols`: instance `perform` method entities owned by `namespace`."""
    return sorted(
        row[3]
        for row in symbol_rows(graph, path)
        if row[1] == "method"
        and row[2] == "perform"
        and json.loads(row[4]).get("owner") == "ruby:" + namespace
        and json.loads(row[4]).get("category") == "instance"
    )


def method_id(graph, path, name):
    found = [r[3] for r in symbol_rows(graph, path) if r[1] == "method" and r[2] == name]
    assert len(found) == 1, (path, name, found)
    return found[0]


def file_id(path):
    return ev.file_entity(path, "ruby")["id"]


def edit(repo, relative, old, new, count=1):
    target = repo / relative
    text = target.read_text()
    assert text.count(old) == count, (relative, old)
    target.write_text(text.replace(old, new))


def inventory(root):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def copy_db(graph, tmp_path, name="copy.db"):
    """A private writable copy of the published database, for planted corruptions."""
    target = tmp_path / name
    shutil.copy(graph.db_path, target)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    return conn


def table_counts(graph):
    tables = ("symbols", "refs", "edges", "rationale", "import_bindings", "assignment_targets")
    with graph.reader_lease() as lease:
        return {t: lease.db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}


def planted(graph, tmp_path, name, mutate, table="assertions", select=None):
    """A private copy of the published database with the first selected row's payload mutated."""
    conn = copy_db(graph, tmp_path, name + ".db")
    row = conn.execute(
        f"SELECT id, payload FROM {table} WHERE {select or '1=1'} LIMIT 1"
    ).fetchone()
    assert row is not None, name
    payload = json.loads(row[1])
    mutate(payload)
    conn.execute(f"UPDATE {table} SET payload=? WHERE id=?", (ev.canonical(payload), row[0]))
    return conn
