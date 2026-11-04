"""Shared ACI-S24 helpers: the Rails routes fixture, real builds, shared-store fact readers."""

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

from atlas_aci.codegraph import CodeGraph
from atlas_aci.frameworks import rails_routes
from atlas_aci.storage import evidence as ev

FIXTURE = Path(__file__).parents[1] / "fixtures/rails_routes"
PROJECT = FIXTURE / "project"
PROVENANCE = json.loads((FIXTURE / "provenance.json").read_text())
R = "config/routes.rb"
F = ev.file_entity(R, "ruby")["id"]

# (verb, path, target) of the twelve supported literal routes, in source order.
ROUTES = [
    ("get", "/users", "users#index"),
    ("get", "/users/:id", "users#show"),
    ("get", "/admin/users", "admin/users#index"),
    ("delete", "/admin/users/:id", "admin/users#destroy"),
    ("post", "/api/v1/widgets", "api/v1/widgets#create"),
    ("patch", "/api/v1/widgets/:id", "api/v1/widgets#update"),
    ("put", "/reports/:id", "reports#show"),
    ("get", "/orders", "legacy/orders#index"),
    ("get", "/missing", "ghosts#index"),
    ("get", "/users/export", "users#export"),
    ("get", "/users/build", "users#build"),
    ("get", "/ping", "api/v1/ping#show"),
]
# (statement bytes, construct code) of the 23 diagnostics, in source order.
DIAGNOSTICS = [
    ('get "/greet/#{name}", to: "users#index"', "interpolated_literal"),
    ('get "/dyn", to: "users##{action_name}"', "interpolated_literal"),
    ("resources :photos", "resources"),
    ('mount Sidekiq::Web => "/sidekiq"', "mount"),
    ('get "/old", to: redirect("/users")', "non_literal_target"),
    ('get "/rocket" => "users#index"', "hash_rocket_target"),
    ('get "/ctrl", controller: "users", action: "index"', "unsupported_option"),
    ('get "/fmt", to: "users#index", format: false', "unsupported_option"),
    ('get "/bad", to: "Users#Index"', "malformed_target"),
    ('get :sym_path, to: "users#index"', "non_literal_path"),
    ('get "/implicit"', "missing_target"),
    ('get "/blk", to: "users#index" do end', "route_block"),
    ('get "/a", "/b", to: "users#index"', "extra_arguments"),
    ('namespace :admin do\n    get "/stats", to: "stats#index"\n  end', "namespace"),
    ('scope "/v2" do\n    get "/x", to: "users#index"\n  end', "scope"),
    ('constraints(subdomain: "api") do\n    get "/c", to: "users#index"\n  end', "constraints"),
    ('match "/legacy", to: "users#index", via: [:get, :post]', "match"),
    ('root to: "users#index"', "root"),
    ('get "/cond", to: "users#index" if ENV["FEATURE"]', "conditional"),
    ('system("touch ATLAS_S24_SENTINEL_DRAW")', "other_statement"),
    ("draw :extra", "draw"),
    ("concern :commentable do end", "concern"),
    (
        'Blog::Engine.routes.draw do\n  get "/e", to: "users#index"\nend',
        "unrecognized_draw_receiver",
    ),
]
SENTINELS = ("ATLAS_S24_SENTINEL_TOP", "ATLAS_S24_SENTINEL_DRAW")


def workspace(tmp_path, name="repo"):
    repo = tmp_path / name
    shutil.copytree(PROJECT, repo)
    return repo


def built(tmp_path, name="repo", root="g", frameworks=("rails",), repo=None):
    repo = repo or workspace(tmp_path, name)
    graph = CodeGraph(repo, graph_root=tmp_path / root, frameworks=frameworks)
    graph.build()
    return repo, graph


def rebuilt(repo, tmp_path, root, frameworks=("rails",), since=None):
    graph = CodeGraph(repo, graph_root=tmp_path / root, frameworks=frameworks)
    return graph, graph.build(since=since)


def records(graph):
    with graph.reader_lease() as lease:
        return list(ev.records(lease.db))


def rails_records(graph):
    everything = records(graph)
    chosen = rails_routes.record_ids(everything)
    return [r for r in everything if r["id"] in chosen]


def rails_lines(graph):
    """The canonical rails fact set: sorted canonical JSON lines."""
    return sorted(ev.canonical(r) for r in rails_records(graph))


def manifest_value(graph, key="rails_routes"):
    with graph.reader_lease() as lease:
        row = lease.db.execute("SELECT value FROM manifest WHERE key=?", (key,)).fetchone()
    return None if row is None else row[0]


def facts(graph):
    """Every routes_to fact joined to its evidence and occurrence."""
    result = []
    with graph.reader_lease() as lease:
        db = lease.db
        for (payload,) in db.execute(
            "SELECT payload FROM assertions WHERE json_extract(payload,'$.relation')='routes_to'"
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
    return sorted(result, key=lambda f: (f["occurrence"]["start_byte"], f["assertion"]["id"]))


def routes_of(graph):
    return [f for f in facts(graph) if f["raw"].get("mapping") == "rails_convention.v1"]


def diagnostics_of(graph):
    return [f for f in facts(graph) if f["raw"].get("mapping") == "unexpanded"]


def tuples(graph):
    """(verb, path, target, resolution, target_entity, candidates, reason) per route."""
    return [
        (
            f["raw"]["verb"],
            f["raw"]["path"],
            f["raw"]["target"],
            f["assertion"]["resolution"],
            f["assertion"]["target_entity_id"],
            tuple(f["assertion"]["candidate_ids"]),
            f["assertion"]["unresolved_reason"],
        )
        for f in routes_of(graph)
    ]


def symbol_entity(graph, path, name, owner=None):
    """Independent SQL on `symbols`: the entity ids of method `name` in `path`."""
    with graph.reader_lease() as lease:
        rows = lease.db.execute(
            "SELECT entity_id, binding FROM symbols WHERE kind='method' AND name=? AND path=?",
            (name, path),
        ).fetchall()
    found = [
        r[0] for r in rows if owner is None or json.loads(r[1]).get("owner") == "ruby:" + owner
    ]
    return found


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
