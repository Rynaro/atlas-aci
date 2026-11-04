"""Shared ACI-S25 helpers: the Rails models fixture, real builds, shared-store fact readers."""

import atexit
import hashlib
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

from atlas_aci.codegraph import CodeGraph
from atlas_aci.frameworks import rails_models
from atlas_aci.storage import evidence as ev

FIXTURE = Path(__file__).parents[1] / "fixtures/rails_models"
PROJECT = FIXTURE / "project"
MAP_PATH = FIXTURE / "table-map.json"
MAP_BYTES = MAP_PATH.read_bytes()
MAP_SHA = hashlib.sha256(MAP_BYTES).hexdigest()
PROVENANCE = json.loads((FIXTURE / "provenance.json").read_text())
USERS = "app/models/user.rb"
POST = "app/models/blog/post.rb"
UP = "db/migrate/20240102000000_alter_posts.rb"
CREATE = "db/migrate/20240101000000_create_users.rb"
MODELS = ("rails_models",)
RELATIONS = ("association", "model_table", "migration_mentions")
SENTINELS = (
    "ATLAS_S25_SENTINEL_CALLBACK",
    "ATLAS_S25_SENTINEL_MIGRATION",
    "ATLAS_S25_SENTINEL_SEEDS",
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
        base = Path(tempfile.mkdtemp(prefix="atlas-s25-warm-"))
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


def make(repo, tmp_path, root, frameworks, table_map, warm):
    graph = CodeGraph(
        repo,
        graph_root=tmp_path / root,
        frameworks=frameworks,
        rails_table_map=table_map if "rails_models" in frameworks else None,
    )
    return prime(graph) if warm else graph


def built(
    tmp_path,
    name="repo",
    root="g",
    frameworks=MODELS,
    repo=None,
    table_map=MAP_BYTES,
    since=None,
    warm=True,
):
    repo = repo or workspace(tmp_path, name)
    graph = make(repo, tmp_path, root, frameworks, table_map, warm)
    graph.build(since=since)
    return repo, graph


def rebuilt(repo, tmp_path, root, frameworks=MODELS, since=None, table_map=MAP_BYTES, warm=True):
    graph = make(repo, tmp_path, root, frameworks, table_map, warm)
    return graph, graph.build(since=since)


def records(graph):
    with graph.reader_lease() as lease:
        return list(ev.records(lease.db))


def model_records(graph):
    everything = records(graph)
    chosen = rails_models.record_ids(everything)
    return [r for r in everything if r["id"] in chosen]


def model_lines(graph):
    """The canonical rails_models fact set: sorted canonical JSON lines."""
    return sorted(ev.canonical(r) for r in model_records(graph))


def manifest_value(graph, key="rails_models"):
    with graph.reader_lease() as lease:
        row = lease.db.execute("SELECT value FROM manifest WHERE key=?", (key,)).fetchone()
    return None if row is None else row[0]


def facts(graph):
    """Every rails_models fact joined to its evidence and occurrence."""
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
                result.append(
                    {
                        "assertion": assertion,
                        "evidence": proof,
                        "occurrence": occurrence,
                        "raw": json.loads(assertion["raw_target"]),
                    }
                )
    return sorted(
        result,
        key=lambda f: (
            f["occurrence"]["path"],
            f["occurrence"]["start_byte"],
            f["occurrence"]["role"],
        ),
    )


def in_file(found, path, relation=None):
    return [
        f
        for f in found
        if f["occurrence"]["path"] == path
        and (relation is None or f["assertion"]["relation"] == relation)
    ]


def class_entities(graph, path, namespace):
    """Independent SQL on `symbols`: the class entity ids named `namespace` in `path`."""
    with graph.reader_lease() as lease:
        rows = lease.db.execute(
            "SELECT entity_id, binding FROM symbols WHERE kind='class' AND path=?", (path,)
        ).fetchall()
    return [r[0] for r in rows if json.loads(r[1]).get("namespace") == namespace]


def entity_of(graph, path, namespace):
    found = class_entities(graph, path, namespace)
    assert len(found) == 1, (path, namespace, found)
    return found[0]


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
