"""ACI-S24 acceptance criteria: explicit Rails routes mapped to controller actions.

Every graph is a real build of a tmp copy of ``tests/fixtures/rails_routes/project`` with an
external graph root. Expected entity ids come from independent SQL on ``symbols``. Nothing the
adapter does is mocked: the spies in AC-007 only observe (and refuse) process launches.
"""

import ast
import asyncio
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

import pytest
import tree_sitter_language_pack as pack
from click.testing import CliRunner
from test_s24_support import (
    DIAGNOSTICS,
    PROJECT,
    PROVENANCE,
    ROUTES,
    SENTINELS,
    F,
    R,
    built,
    copy_db,
    diagnostics_of,
    edit,
    facts,
    inventory,
    manifest_value,
    rails_lines,
    rails_records,
    rebuilt,
    records,
    routes_of,
    symbol_entity,
    tuples,
    workspace,
)

import atlas_aci.frameworks as frameworks
from atlas_aci.__main__ import cli
from atlas_aci.codegraph import CodeGraph
from atlas_aci.frameworks import rails_routes
from atlas_aci.storage import evidence as ev

SRC = Path(rails_routes.__file__).parent
ROUTES_BYTES = (PROJECT / R).read_bytes()
UNIX = "/users"


def parse(text):
    source = text.encode()
    return pack.get_parser("ruby").parse(source), source


def scanned(text):
    tree, source = parse(text)
    return rails_routes.scan(tree, source)


def drawn(statement):
    return scanned("Rails.application.routes.draw do\n  " + statement + "\nend\n")


def only_code(statement):
    declarations, truncated = drawn(statement)
    assert len(declarations) == 1 and not truncated, statement
    return declarations[0].construct


# ---- fixture integrity ----
def test_fixture_matches_its_provenance():
    assert PROVENANCE["files"]
    for name, meta in PROVENANCE["files"].items():
        data = (PROJECT / name).read_bytes()
        assert data.isascii()
        assert len(data) == meta["bytes"] and hashlib.sha256(data).hexdigest() == meta["sha256"]
    assert {p.relative_to(PROJECT).as_posix() for p in PROJECT.rglob("*") if p.is_file()} == set(
        PROVENANCE["files"]
    )


# ---- AC-S24-001 ----
def test_rails_literal_route_fact(tmp_path):
    _repo, graph = built(tmp_path)
    routes = routes_of(graph)
    assert len(routes) == len(ROUTES) == 12
    first = [f for f in routes if f["raw"]["path"] == UNIX and f["raw"]["verb"] == "get"]
    assert len(first) == 1
    fact = first[0]
    occurrence, proof = fact["occurrence"], fact["evidence"]
    expected = ev.canonical(
        {
            "mapping": "rails_convention.v1",
            "path": "/users",
            "target": "users#index",
            "verb": "get",
        }
    )
    assert fact["assertion"]["raw_target"] == expected
    assert occurrence["role"] == "rails_route:get" and occurrence["kind"] == "rails_route"
    start, end = occurrence["start_byte"], occurrence["end_byte"]
    assert ROUTES_BYTES[start:end] == b'get "/users", to: "users#index"'
    lines = ROUTES_BYTES.split(b"\n")
    line = next(i for i, text in enumerate(lines, 1) if text.strip().startswith(b'get "/users",'))
    assert occurrence["line"] == occurrence["end_line"] == line
    assert occurrence["source_sha256"] == hashlib.sha256(ROUTES_BYTES).hexdigest()
    assert occurrence["source_size"] == len(ROUTES_BYTES)
    assert occurrence["entity_id"] == F and occurrence["path"] == R
    for key in ("source_sha256", "source_size", "start_byte", "end_byte"):
        assert proof[key] == occurrence[key]
    producer = next(r for r in records(graph) if r["id"] == proof["producer_id"])
    assert set(producer["fingerprint"]) == {"rails"}
    # Across the graph: exactly R1-R12, one occurrence per declaration, every verb present.
    found = [(f["raw"]["verb"], f["raw"]["path"], f["raw"]["target"]) for f in routes]
    assert found == ROUTES and {v for v, _p, _t in found} == rails_routes.VERBS
    everything = routes + diagnostics_of(graph)
    spans = {(f["occurrence"]["start_byte"], f["occurrence"]["end_byte"]) for f in everything}
    assert len(everything) == len(spans) == 35
    assert len({f["occurrence"]["id"] for f in everything}) == 35


# ---- AC-S24-002 ----
def test_rails_exact_controller_action_mapping(tmp_path):
    _repo, graph = built(tmp_path)
    by_path = {f["raw"]["path"]: f for f in routes_of(graph)}
    users_index = symbol_entity(graph, "app/controllers/users_controller.rb", "index")
    admin_index = symbol_entity(graph, "app/controllers/admin/users_controller.rb", "index")
    assert len(users_index) == len(admin_index) == 1
    assert symbol_entity(
        graph, "app/controllers/users_controller.rb", "index", "UsersController"
    ) == [users_index[0]]
    first = by_path["/users"]["assertion"]
    assert first["relation"] == "routes_to" and first["resolution"] == "INFERRED"
    assert first["target_entity_id"] == users_index[0]
    assert first["candidate_ids"] == [] and first["unresolved_reason"] is None
    assert by_path["/users"]["raw"]["mapping"] == "rails_convention.v1"
    assert by_path["/admin/users"]["assertion"]["target_entity_id"] == admin_index[0]
    assert admin_index[0] != users_index[0]
    compact = symbol_entity(graph, "app/controllers/api/v1/widgets_controller.rb", "create")
    assert by_path["/api/v1/widgets"]["assertion"]["target_entity_id"] == compact[0]
    inferred = [f for f in routes_of(graph) if f["assertion"]["resolution"] == "INFERRED"]
    assert len(inferred) == 7
    with graph.reader_lease() as lease:
        for fact in inferred:
            target = ev.get(lease.db, "entity", fact["assertion"]["target_entity_id"])
            assert target["kind"] == "method" and target["language"] == "ruby"
    # The convention label cannot be dropped and a rails mapping is never EXTRACTED.
    relabelled = dict(first, resolution="EXTRACTED")
    relabelled.pop("id")
    with pytest.raises(ValueError, match="routes_to assertion"):
        ev.validate_record(ev.record("assertion", **relabelled))
    conn = copy_db(graph, tmp_path)
    row = conn.execute("SELECT id, payload FROM assertions WHERE id=?", (first["id"],)).fetchone()
    payload = json.loads(row[1])
    raw = json.loads(payload["raw_target"])
    del raw["mapping"]
    payload["raw_target"] = ev.canonical(raw)
    conn.execute("UPDATE assertions SET payload=? WHERE id=?", (ev.canonical(payload), row[0]))
    with pytest.raises(ValueError, match="Invalid evidence: rails"):
        rails_routes.validate_records(conn)


# ---- AC-S24-003 ----
def test_rails_ambiguous_target_is_unresolved(tmp_path):
    _repo, graph = built(tmp_path)
    orders = next(f for f in routes_of(graph) if f["raw"]["path"] == "/orders")["assertion"]
    expected = sorted(
        [
            symbol_entity(graph, "app/controllers/legacy/orders_controller.rb", "index")[0],
            symbol_entity(graph, "lib/legacy/orders_controller.rb", "index")[0],
        ]
    )
    assert len(expected) == 2 and orders["candidate_ids"] == expected
    assert orders["resolution"] == "AMBIGUOUS" and orders["target_entity_id"] is None
    assert orders["unresolved_reason"] == "ambiguous_controller_action"
    for fact in routes_of(graph):
        assert fact["assertion"]["target_entity_id"] not in expected
    # evidence.py accepts candidates only on AMBIGUOUS, which is why that is the unresolved shape.
    payload = {k: v for k, v in orders.items() if k != "id"}
    ev.validate_record(ev.record("assertion", **payload))
    with pytest.raises(ValueError, match="ambiguity candidates"):
        ev.validate_record(ev.record("assertion", **dict(payload, resolution="UNRESOLVED")))


# ---- AC-S24-004 ----
def test_rails_dynamic_route_diagnostic(tmp_path):
    _repo, graph = built(tmp_path)
    diagnostics = diagnostics_of(graph)
    assert len(diagnostics) == len(DIAGNOSTICS) == 23
    for fact, (statement, code) in zip(diagnostics, DIAGNOSTICS, strict=True):
        occurrence, assertion = fact["occurrence"], fact["assertion"]
        assert occurrence["role"] == "rails_route:unsupported:" + code, statement
        assert ROUTES_BYTES[occurrence["start_byte"] : occurrence["end_byte"]] == statement.encode()
        assert assertion["resolution"] == "UNRESOLVED"
        assert assertion["unresolved_reason"] == "unsupported_route_construct"
        assert fact["raw"] == {"construct": code, "mapping": "unexpanded"}
        assert assertion["target_entity_id"] is None and assertion["candidate_ids"] == []
    codes = {f["raw"]["construct"] for f in diagnostics}
    assert {"interpolated_literal", "resources", "mount", "non_literal_target"} <= codes
    assert "unrecognized_draw_receiver" in codes
    # Nothing was expanded.
    expanded = {f["raw"]["path"] for f in routes_of(graph)}
    assert not expanded & {"/stats", "/e", "/x", "/c", "/legacy"}
    assert not any(p.startswith("/greet/") for p in expanded)
    summary = json.loads(manifest_value(graph))
    assert (summary["routes"], summary["diagnostics"], summary["truncated"]) == (12, 23, False)
    assert summary["declarations"] == 35 and summary["state"] == "indexed"
    assert summary["resolutions"] == {"AMBIGUOUS": 1, "INFERRED": 7, "UNRESOLVED": 27}


# ---- AC-S24-005 ----
def affected(stats):
    resolution = stats["binding_resolution"]
    assert resolution["mode"] == "affected", resolution
    return resolution["affected_paths"]


def test_rails_removed_route_retracts_mapping(tmp_path):
    repo, graph = built(tmp_path)
    before = tuples(graph)
    assert len(before) == 12 and len(rails_records(graph)) == 106
    # (i) delete the declaration of R2.
    edit(repo, R, '  get "/users/:id", to: "users#show", as: "user"\n', "")
    graph, stats = rebuilt(repo, tmp_path, "g", since="x")
    assert R in affected(stats)
    after = tuples(graph)
    assert not any(t[1] == "/users/:id" for t in after)
    assert after == [t for t in before if t[1] != "/users/:id"]
    full, _stats = rebuilt(repo, tmp_path, "fresh1")
    assert rails_lines(graph) == rails_lines(full) and rails_lines(graph)
    # (ii) delete a controller; routes.rb bytes are unchanged.
    (repo / "app/controllers/reports_controller.rb").unlink()
    graph, stats = rebuilt(repo, tmp_path, "g", since="x")
    assert R in affected(stats)
    reports = next(t for t in tuples(graph) if t[1] == "/reports/:id")
    assert reports[3:] == ("UNRESOLVED", None, (), "controller_not_found")
    with graph.reader_lease() as lease:
        graph._validate_evidence_projections(lease.db)
        known = {json.loads(p)["id"] for (p,) in lease.db.execute("SELECT payload FROM entities")}
    for fact in facts(graph):
        linked = [fact["assertion"]["target_entity_id"], *fact["assertion"]["candidate_ids"]]
        assert all(i in known for i in linked if i is not None)
    full, _stats = rebuilt(repo, tmp_path, "fresh2")
    assert rails_lines(graph) == rails_lines(full)
    # (v) controller-only declaration edit, routes.rb bytes unchanged: R is affected because
    # the Ruby bucket digest changed, and the facts equal a fresh full build.
    routes_before = (repo / R).read_bytes()
    edit(repo, "app/controllers/users_controller.rb", "  def index\n", "  def listing\n")
    graph, stats = rebuilt(repo, tmp_path, "g", since="x")
    assert (repo / R).read_bytes() == routes_before
    assert R in affected(stats)
    shown = next(t for t in tuples(graph) if t[1] == "/users" and t[0] == "get")
    assert shown[3:] == ("UNRESOLVED", None, (), "action_not_found")
    full, _stats = rebuilt(repo, tmp_path, "fresh5")
    assert rails_lines(graph) == rails_lines(full)
    # (iii) a syntax error in routes.rb retracts every fact while its history exists.
    good = (repo / R).read_text()
    (repo / R).write_text(good + 'get "/broken", to:\n  (((\n')
    graph, stats = rebuilt(repo, tmp_path, "g", since="x")
    assert rails_records(graph) == []
    assert json.loads(manifest_value(graph))["state"] == "failed"
    with graph.reader_lease() as lease:
        observed = json.loads(
            lease.db.execute(
                "SELECT value FROM manifest WHERE key='source_observations'"
            ).fetchone()[0]
        )
    history = [h for h in observed if h["type"] == "history" and h["path"] == R]
    assert history, "R's history record must exist while the adapter state is failed"
    assert not rails_routes.record_ids(history[0]["records"])
    # (iv) disabling the adapter purges the surviving facts and the manifest key.
    (repo / R).write_text(good)
    graph, _stats = rebuilt(repo, tmp_path, "g", since="x")
    assert rails_records(graph)
    off, _stats = rebuilt(repo, tmp_path, "g", frameworks=(), since="x")
    assert rails_records(off) == [] and manifest_value(off) is None


# ---- AC-S24-006 ----
def test_rails_route_reindex_is_idempotent(tmp_path):
    repo, one = built(tmp_path, root="one")
    _r, two = built(tmp_path, root="two", repo=repo)
    first = rails_lines(one)
    assert first and first == rails_lines(two)
    assert manifest_value(one) == manifest_value(two)
    graph, stats = rebuilt(repo, tmp_path, "one", since="x")
    assert rails_lines(graph) == first and manifest_value(graph) == manifest_value(one)
    resolution = stats["binding_resolution"]
    assert (resolution["mode"], resolution["reason"], resolution["affected_units"]) == (
        "affected",
        "complete_dependency_match",
        0,
    )
    _off, off_stats = rebuilt(repo, tmp_path, "off", frameworks=())
    _again, again_stats = rebuilt(repo, tmp_path, "off", frameworks=(), since="x")
    assert again_stats["binding_resolution"]["affected_units"] == 0
    assert off_stats["files_indexed"] == 8
    summary = json.loads(manifest_value(one))
    assert (
        summary["adapter_sha256"]
        == hashlib.sha256((SRC / "rails_routes.py").read_bytes()).hexdigest()
    )
    producers = [r for r in rails_records(one) if r["type"] == "producer"]
    assert len(producers) == 1
    native = CodeGraph(repo, graph_root=tmp_path / "one", frameworks=("rails",))
    native.build(since="x")
    assert (
        producers[0]["fingerprint"]["rails"]["graph_producer_id"] == native._capture_producer["id"]
    )
    # Non-vacuous control: editing a literal changes the set.
    edit(repo, R, 'get "/users", to: "users#index"', 'get "/users2", to: "users#index"')
    changed, _stats = rebuilt(repo, tmp_path, "one", since="x")
    assert rails_lines(changed) != first


# ---- AC-S24-007 ----
BANNED_IMPORTS = {
    "os",
    "subprocess",
    "socket",
    "urllib",
    "http",
    "time",
    "datetime",
    "random",
    "importlib",
    "ctypes",
    "multiprocessing",
    "pty",
    "runpy",
}
ALLOWED_IMPORTS = {
    "__future__",
    "hashlib",
    "json",
    "re",
    "sqlite3",
    "dataclasses",
    "pathlib",
    "typing",
    "collections.abc",
    "atlas_aci.storage",
    "atlas_aci.storage.evidence",
    "atlas_aci.frameworks",
    "atlas_aci",
}
BANNED_NAMES = {
    "eval",
    "exec",
    "compile",
    "__import__",
    "open",
    "getattr",
    "setattr",
    "globals",
    "locals",
    "vars",
    "breakpoint",
    "input",
    "__builtins__",
}


SQLITE_EXECUTE = {"execute", "executemany", "executescript"}  # sqlite3 cursor methods


def banned_attribute(name):
    if name in SQLITE_EXECUTE:
        return False
    return name in {"system", "popen"} or name.startswith(("spawn", "exec", "fork"))


def test_adapter_sources_are_static_ascii_and_never_call_themselves():
    sources = sorted(SRC.glob("*.py"))
    assert {p.name for p in sources} == {
        "__init__.py",
        "rails_jobs.py",
        "rails_models.py",
        "rails_routes.py",
    }
    reads = 0
    for path in sources:
        text = path.read_text()
        assert text.isascii(), path
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                assert node.level == 0
                names = [node.module]
            else:
                names = []
            for name in names:
                assert name.split(".")[0] not in BANNED_IMPORTS, (path, name)
                assert name in ALLOWED_IMPORTS, (path, name)
            if isinstance(node, ast.Name):
                assert node.id not in BANNED_NAMES, (path, node.id)
            if isinstance(node, ast.Attribute):
                assert not banned_attribute(node.attr), (path, node.attr)
                reads += node.attr in {"read_bytes", "read_text", "write_bytes", "write_text"}
            if isinstance(node, ast.FunctionDef):
                for call in ast.walk(node):
                    if isinstance(call, ast.Call):
                        target = call.func
                        called = target.id if isinstance(target, ast.Name) else None
                        called = called or (
                            target.attr
                            if isinstance(target, ast.Attribute)
                            and isinstance(target.value, ast.Name)
                            and target.value.id in {"self", "cls"}
                            else None
                        )
                        assert called != node.name, (path, node.name)
        for pattern in (r"\\d", r"\\w"):
            assert re.search(pattern, text) is None, (path, pattern)
    assert reads == 3  # Path(__file__).read_bytes() for adapter_sha256, once per adapter


def install_spies(monkeypatch, launches):
    def popen(argv, *args, **kwargs):
        launches.append(list(argv) if not isinstance(argv, str) else [argv])
        raise FileNotFoundError("process launch refused by the S24 spy")

    monkeypatch.setattr(subprocess, "Popen", popen)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("the Rails adapter must never launch a process")

    for name in (
        "system",
        "popen",
        "fork",
        "forkpty",
        "posix_spawn",
        "posix_spawnp",
        *[n for n in dir(os) if n.startswith(("exec", "spawn"))],
    ):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, forbidden)
    for name in ("create_subprocess_exec", "create_subprocess_shell"):
        monkeypatch.setattr(asyncio, name, forbidden)
    monkeypatch.setattr(asyncio.subprocess, "create_subprocess_exec", forbidden)
    monkeypatch.setattr(asyncio.subprocess, "create_subprocess_shell", forbidden)


def test_rails_routes_never_execute_application(tmp_path, monkeypatch):
    repo = workspace(tmp_path)
    before = inventory(repo)
    launches: list[list[str]] = []
    install_spies(monkeypatch, launches)
    # Liveness: the spy sees a launch and is then reset.
    with pytest.raises(FileNotFoundError):
        subprocess.run(["true"], check=False)
    assert launches == [["true"]]
    launches.clear()
    _r, on = built(tmp_path, root="on", repo=repo)
    on_launches = [list(argv) for argv in launches]
    launches.clear()
    inferred = [f for f in routes_of(on) if f["assertion"]["resolution"] == "INFERRED"]
    assert len(inferred) >= 7
    assert on_launches, "the pre-existing snapshot identity probe is the only launch"
    for argv in on_launches:
        assert argv[:2] == ["git", "-C"] and "rev-parse" in argv
        for token in [*argv[:2], *argv[3:]]:  # argv[2] is the tmp repo path
            assert not re.search(r"ruby|rails|rake|bundle|routes\.rb", token), argv
    _r, off = built(tmp_path, root="off", repo=repo, frameworks=())
    assert [list(a) for a in launches] == on_launches
    assert rails_records(off) == []
    launches.clear()
    for name in SENTINELS:
        assert not (repo / name).exists() and not (Path.cwd() / name).exists()
    assert inventory(repo) == before
    # A pure scan plus refresh over a staging copy launches nothing, under an all-raising spy.
    conn = copy_db(on, tmp_path)
    observations = json.loads(
        conn.execute("SELECT value FROM manifest WHERE key='source_observations'").fetchone()[0]
    )
    native = next(
        json.loads(p)["id"]
        for (p,) in conn.execute("SELECT payload FROM producers")
        if "codegraph_sha256" in (json.loads(p)["fingerprint"] or {})
    )
    tree, source = parse(ROUTES_BYTES.decode())
    assert rails_routes.scan(tree, source)[0]
    summary = rails_routes.refresh(
        conn,
        enabled=True,
        capture=(ROUTES_BYTES, hashlib.sha256(ROUTES_BYTES).hexdigest()),
        observations=observations,
        get_parser=pack.get_parser,
        graph_producer_id=native,
    )
    assert summary is not None and summary["state"] == "indexed" and launches == []
    for name in SENTINELS:
        assert not (Path.cwd() / name).exists() and not (tmp_path / name).exists()


# ---- supporting: scan constructs ----
def test_vocabularies_are_the_closed_sets():
    assert {"get", "post", "put", "patch", "delete"} == rails_routes.VERBS
    assert len(rails_routes.STATEMENT_CONSTRUCTS) == 20
    assert (
        set(
            [
                "collection",
                "concern",
                "concerns",
                "constraints",
                "controller",
                "defaults",
                "devise_for",
                "direct",
                "draw",
                "match",
                "member",
                "mount",
                "namespace",
                "nested",
                "resolve",
                "resource",
                "resources",
                "root",
                "scope",
                "shallow",
            ]
        )
        == rails_routes.STATEMENT_CONSTRUCTS
    )
    assert (
        set(
            [
                "route_block",
                "hash_rocket_target",
                "extra_arguments",
                "non_literal_path",
                "interpolated_literal",
                "malformed_literal",
                "unsupported_option",
                "missing_target",
                "non_literal_target",
                "malformed_target",
            ]
        )
        == rails_routes.VERB_CONSTRUCTS
    )
    assert {
        "conditional",
        "other_statement",
        "unrecognized_draw_receiver",
        "declaration_limit",
    } == rails_routes.OTHER_CONSTRUCTS
    assert len(rails_routes.CONSTRUCTS) == 34
    assert {
        "ambiguous_controller_action",
        "action_not_found",
        "controller_not_found",
        "unsupported_route_construct",
    } == rails_routes.REASONS
    assert {"rails_convention.v1", "unexpanded"} == rails_routes.LABELS
    assert (rails_routes.MAX_DECLARATIONS, rails_routes.MAX_PATH_BYTES) == (5000, 512)
    assert rails_routes.MAX_TARGET_BYTES == 256
    assert ev.RAILS_RELATION == "routes_to" and "routes_to" not in ev.RELATIONS
    assert "routes_to" not in ev.REFERENCE_RELATIONS and "routes_to" not in ev.EDGE_RELATIONS
    assert "routes_to" in ev.ASSERTION_RELATIONS


def test_every_construct_code_is_reachable():
    declarations, truncated = rails_routes.scan(*parse(ROUTES_BYTES.decode()))
    seen = {d.construct for d in declarations if d.construct}
    assert not truncated and len(declarations) == 35
    inline = {
        "resource": "resource :profile",
        "member": "member do end",
        "collection": "collection do end",
        "nested": "nested do end",
        "shallow": "shallow do end",
        "controller": "controller :x do end",
        "devise_for": "devise_for :users",
        "direct": 'direct :x do "/" end',
        "resolve": 'resolve("X") { }',
        "concerns": "concerns :a",
        "defaults": "defaults format: :json do end",
        "malformed_literal": 'get "/a\\nb", to: "users#index"',
    }
    for code, statement in inline.items():
        assert only_code(statement) == code, statement
        seen.add(code)
    original = rails_routes.MAX_DECLARATIONS
    try:
        rails_routes.MAX_DECLARATIONS = 2
        found, cut = drawn('get "/a", to: "u#a"\n  get "/b", to: "u#b"\n  get "/c", to: "u#c"')
        assert cut and [d.construct for d in found] == [None, None, "declaration_limit"]
        seen.add("declaration_limit")
        found, cut = drawn('get "/a", to: "u#a"\n  get "/b", to: "u#b"')
        assert not cut and len(found) == 2
    finally:
        rails_routes.MAX_DECLARATIONS = original
    assert seen == rails_routes.CONSTRUCTS


@pytest.mark.parametrize(
    "statement,code",
    [
        ("get '/a#{x}', to: 'users#index'", None),  # single-quoted: a plain literal
        ('get "/a#b#c", to: "users#index"', None),
        ('get "/#", to: "users#index"', None),
        ('get "/a#@", to: "users#index"', None),  # several string_content children
        ("get %q(/a), to: 'users#index'", "malformed_literal"),
        ('get "/a" "/b", to: "users#index"', "non_literal_path"),
        ("get <<~EOS, to: 'users#index'\n  /a\nEOS", "non_literal_path"),
        ('get "", to: "users#index"', "malformed_literal"),
        ('get "/a", "to": "users#index"', "unsupported_option"),
        ('get "/a", to: "users#index", as: "u#{x}"', "unsupported_option"),
        ('get "/a", to: "users#index", as: "u\\n"', "unsupported_option"),
        ('get "/a", to: "users#index", as: :user', None),
        ('get "/a", to: "users#index", as: "user"', None),
        ('get "/a", to: "users#index", to: "users#show"', "unsupported_option"),
        ('get "/a", to: "users#index", constraints: {id: 1}', "unsupported_option"),
        ('get "/a", **opts', "extra_arguments"),
        ('get "/a", *args', "extra_arguments"),
        ('get "/a", {to: "users#index"}', "extra_arguments"),
        ('get "/a", to: "users#index" { }', "route_block"),
        ('foo.get "/a", to: "users#index"', "other_statement"),
        ("x = 1", "other_statement"),
        ('unless x\n    get "/a", to: "users#index"\n  end', "conditional"),
        ("case x\n  when 1 then 1\n  end", "conditional"),
        ('get("/a", to: "users#index")', None),
        ('get "/a", to: "Users#index"', "malformed_target"),
        ('get "/a", to: "users#index?"', "malformed_target"),
        ('get "/a", to: "users#"', "malformed_target"),
        ('get "/a", to: "users/#index"', "malformed_target"),
    ],
)
def test_literal_and_option_rules(statement, code):
    declarations, _truncated = drawn(statement)
    assert len(declarations) == 1
    assert declarations[0].construct == code, statement
    if code is None:
        assert declarations[0].verb == "get" and declarations[0].target == "users#index"


def test_literal_is_the_source_bytes_between_the_delimiters():
    for text, path in [
        ('get "/a#@", to: "users#index"', "/a#@"),
        ('get "/a#b#c", to: "users#index"', "/a#b#c"),
        ("get '/a#{x}', to: 'users#index'", "/a#{x}"),
    ]:
        tree, source = parse("Rails.application.routes.draw do\n  " + text + "\nend\n")
        found = rails_routes.scan(tree, source)[0]
        assert found[0].path == path
    tree, source = parse('get "/a#@", to: "x"')
    string = tree.root_node.named_children[0].child_by_field_name("arguments").named_children[0]
    assert [c.type for c in string.children].count("string_content") == 2


def test_draw_wrapped_in_module_if_or_begin_yields_nothing():
    for wrapper in ("module Foo\n%s\nend", "if true\n%s\nend", "begin\n%s\nend"):
        body = 'Rails.application.routes.draw do\n  get "/a", to: "users#index"\nend'
        declarations, truncated = scanned(wrapper % body)
        assert declarations == [] and not truncated
    declarations, _t = scanned('Rails.application.routes.draw { get "/a", to: "users#index" }')
    assert [d.verb for d in declarations] == ["get"]
    declarations, _t = scanned("MyApp::Application.routes.draw do\n  root to: 'a#b'\nend")
    assert [d.construct for d in declarations] == ["root"]
    declarations, _t = scanned("Rails.application.routes.draw(x) do end")
    assert [d.construct for d in declarations] == ["unrecognized_draw_receiver"]
    declarations, _t = scanned("draw do\n  get '/a', to: 'u#i'\nend")
    assert [d.construct for d in declarations] == ["unrecognized_draw_receiver"]


def test_camelize_table():
    table = {
        "users": "UsersController",
        "admin/users": "Admin::UsersController",
        "api/v1/widgets": "Api::V1::WidgetsController",
        "a__b": "ABController",
        "foo_bar/baz_qux": "FooBar::BazQuxController",
        "api/v1/ping": "Api::V1::PingController",
    }
    for given, expected in table.items():
        assert rails_routes.camelize(given) == expected
    assert rails_routes.TARGET_RE.fullmatch("a/b_c#d9") and not rails_routes.TARGET_RE.fullmatch(
        "a#b\n"
    )


def test_controller_not_found_means_no_indexed_class(tmp_path):
    repo = workspace(tmp_path)
    skipped = repo / "app/controllers/storage"
    skipped.mkdir(parents=True)
    (skipped / "files_controller.rb").write_text(
        "module Storage\n  class FilesController\n    def index\n    end\n  end\nend\n"
    )
    edit(
        repo,
        R,
        '  get "/ping", to: "api/v1/ping#show"\n',
        '  get "/ping", to: "api/v1/ping#show"\n  get "/files", to: "storage/files#index"\n',
    )
    _r, graph = built(tmp_path, repo=repo)
    files = next(t for t in tuples(graph) if t[1] == "/files")
    assert files[3:] == ("UNRESOLVED", None, (), "controller_not_found")  # a skip pattern
    assert symbol_entity(graph, "app/controllers/storage/files_controller.rb", "index") == []


# ---- supporting: CLI ----
def test_cli_frameworks_flag(tmp_path):
    repo = workspace(tmp_path)
    runner = CliRunner()
    root = tmp_path / "cli-root"
    out = runner.invoke(
        cli,
        ["index", "--repo", str(repo), "--graph-root", str(root), "--frameworks", "rails,rails"],
    )
    assert out.exit_code == 0, out.output
    graph = CodeGraph(repo, graph_root=root, read_only=True)
    assert len(facts(graph)) == 35
    graph.close()
    default = tmp_path / "default-root"
    out = runner.invoke(cli, ["index", "--repo", str(repo), "--graph-root", str(default)])
    assert out.exit_code == 0
    graph = CodeGraph(repo, graph_root=default, read_only=True)
    assert facts(graph) == []
    graph.close()
    for order in (
        ["--frameworks", "django", "--langs", "ruby"],
        ["--langs", "ruby", "--frameworks", "django"],
        ["--frameworks", "rails", "--langs", "python"],
        ["--langs", "python", "--frameworks", "rails"],
    ):
        reject = tmp_path / f"reject-{'-'.join(order).replace('--', '')}"
        out = runner.invoke(
            cli, ["index", "--repo", str(repo), "--graph-root", str(reject), *order]
        )
        assert out.exit_code == 2, (order, out.output)
        assert "Unknown framework adapter" in out.output or "requires language ruby" in out.output
        assert not reject.exists()
    out = runner.invoke(
        cli,
        [
            "index",
            "--repo",
            str(repo),
            "--graph-root",
            str(tmp_path / "ok"),
            "--langs",
            "ruby",
            "--frameworks",
            "rails",
        ],
    )
    assert out.exit_code == 0, out.output


def test_frameworks_registry_normalization():
    assert frameworks.normalize([" rails ", "", "rails"], {"ruby"}) == ("rails",)
    assert frameworks.normalize("rails,rails", {"ruby"}) == ("rails",)
    assert frameworks.normalize((), set()) == ()
    with pytest.raises(ValueError, match="Unknown framework adapter: django"):
        frameworks.normalize(["django"], {"ruby"})
    with pytest.raises(ValueError, match="Unknown framework adapter: " + "x" * 64 + "$"):
        frameworks.normalize(["x" * 200], {"ruby"})
    with pytest.raises(ValueError, match="requires language ruby"):
        frameworks.normalize(["rails"], {"python"})
    with pytest.raises(ValueError, match="requires language ruby"):
        CodeGraph(Path("."), langs=["python"], frameworks=("rails",))


# ---- supporting: validation, planted corruption, producer shape ----
def planted(graph, tmp_path, name, mutate, table="assertions", select=None):
    conn = copy_db(graph, tmp_path, name + ".db")
    where = select or "1=1"
    row = conn.execute(f"SELECT id, payload FROM {table} WHERE {where} LIMIT 1").fetchone()
    payload = json.loads(row[1])
    mutate(payload)
    conn.execute(f"UPDATE {table} SET payload=? WHERE id=?", (ev.canonical(payload), row[0]))
    return conn


def test_planted_corruptions_each_raise(tmp_path):
    _repo, graph = built(tmp_path)
    kind = "json_extract(payload,'$.kind')='rails_route'"
    relation = "json_extract(payload,'$.relation')='routes_to'"

    def raw(value):
        return lambda p: p.update(raw_target=value)

    cases = {
        "list": planted(graph, tmp_path, "list", raw("[1]"), select=relation),
        "scalar": planted(graph, tmp_path, "scalar", raw("3"), select=relation),
        "not_json": planted(graph, tmp_path, "notjson", raw("{"), select=relation),
        "not_canonical": planted(
            graph,
            tmp_path,
            "noncanon",
            raw('{"mapping": "unexpanded", "construct": "root"}'),
            select=relation,
        ),
        "missing_key": planted(
            graph, tmp_path, "missing", raw('{"mapping":"unexpanded"}'), select=relation
        ),
        "construct_outside_vocabulary": planted(
            graph,
            tmp_path,
            "vocab",
            raw('{"construct":"zzz","mapping":"unexpanded"}'),
            select=relation + " AND json_extract(payload,'$.raw_target') LIKE '%unexpanded%'",
        ),
        "source_not_file": planted(
            graph,
            tmp_path,
            "source",
            lambda p: p.update(source_entity_id="0" * 64),
            select=relation,
        ),
    }
    for name in ("list", "scalar", "not_json", "not_canonical", "missing_key"):
        with pytest.raises(ValueError, match="Invalid evidence: rails"):
            rails_routes.validate_records(cases[name])
    with pytest.raises(ValueError, match="Invalid evidence: rails"):
        rails_routes.validate_records(cases["construct_outside_vocabulary"])
    with pytest.raises(ValueError, match="Invalid evidence: rails"):
        rails_routes.validate_records(cases["source_not_file"])
    # INFERRED to a class entity, not a method.
    conn = copy_db(graph, tmp_path, "klass.db")
    class_id = conn.execute("SELECT entity_id FROM symbols WHERE kind='class' LIMIT 1").fetchone()[
        0
    ]
    row = conn.execute(
        "SELECT id, payload FROM assertions WHERE "
        + relation
        + " AND json_extract(payload,'$.resolution')='INFERRED' LIMIT 1"
    ).fetchone()
    payload = json.loads(row[1])
    payload["target_entity_id"] = class_id
    conn.execute("UPDATE assertions SET payload=? WHERE id=?", (ev.canonical(payload), row[0]))
    with pytest.raises(ValueError, match="not a ruby method entity"):
        rails_routes.validate_records(conn)
    # An orphan rails occurrence: no evidence cites it.
    conn = copy_db(graph, tmp_path, "orphan.db")
    row = conn.execute("SELECT id, payload FROM occurrences WHERE " + kind + " LIMIT 1").fetchone()
    body = {k: v for k, v in json.loads(row[1]).items() if k != "id"}
    body.update(start_byte=0, end_byte=1, line=1, column=0, end_line=1, end_column=1)
    ev.put(conn, ev.record("occurrence", **body), R)
    with pytest.raises(ValueError, match="Invalid evidence: rails"):
        rails_routes.validate_records(conn)
    # Role/kind mismatch and an extra fingerprint key fail at the record layer.
    good = next(r for r in rails_records(graph) if r["type"] == "occurrence")
    body = {k: v for k, v in good.items() if k != "id"}
    with pytest.raises(ValueError, match="rails route occurrence role"):
        ev.validate_record(ev.record("occurrence", **dict(body, role="definition:class")))
    with pytest.raises(ValueError, match="rails route occurrence role"):
        ev.validate_record(ev.record("occurrence", **dict(body, kind="reference")))
    with pytest.raises(ValueError, match="rails route occurrence shape"):
        ev.validate_record(ev.record("occurrence", **dict(body, path="app/routes.rb")))
    producer = next(r for r in rails_records(graph) if r["type"] == "producer")
    fingerprint = dict(producer["fingerprint"]["rails"])
    ev.validate_record(ev.producer({"rails": fingerprint}))
    with pytest.raises(ValueError, match="rails producer fields"):
        ev.validate_record(ev.producer({"rails": dict(fingerprint, extra="x")}))
    with pytest.raises(ValueError, match="producer fields"):
        ev.validate_record(ev.producer({"rails": fingerprint, "scip": {}}))
    # A different valid adapter hash is accepted (history may carry another adapter build).
    ev.validate_record(ev.producer({"rails": dict(fingerprint, adapter_sha256="7" * 64)}))
    with pytest.raises(ValueError, match="rails producer hashes"):
        ev.validate_record(ev.producer({"rails": dict(fingerprint, adapter_sha256="XYZ")}))


def test_validate_records_accepts_a_different_adapter_hash(tmp_path):
    _repo, graph = built(tmp_path)
    conn = copy_db(graph, tmp_path)
    row = conn.execute(
        "SELECT id, payload FROM producers "
        "WHERE json_extract(payload,'$.fingerprint.rails') IS NOT NULL"
    ).fetchone()
    old = json.loads(row[1])
    fingerprint = dict(old["fingerprint"]["rails"], adapter_sha256="7" * 64)
    rewritten = ev.producer({"rails": fingerprint})
    ev.validate_record(rewritten)
    conn.execute(
        "UPDATE producers SET id=?, payload=? WHERE id=?",
        (rewritten["id"], ev.canonical(rewritten), row[0]),
    )
    conn.execute(
        "UPDATE evidence SET payload=json_set(payload,'$.producer_id',?) "
        "WHERE json_extract(payload,'$.producer_id')=?",
        (rewritten["id"], row[0]),
    )
    # Format only: the running adapter's hash is never compared.
    rails_routes.validate_records(conn)
    assert rewritten["fingerprint"]["rails"]["adapter_sha256"] != rails_routes.adapter_sha256()


def test_projection_validation_passes_on_a_copy_of_an_on_database(tmp_path):
    _repo, graph = built(tmp_path)
    conn = copy_db(graph, tmp_path)
    graph._validate_evidence_projections(conn)
    ev.validate_database(conn)
    rails_routes.validate_records(conn)


def test_producer_exists_only_with_declarations_and_disabled_purges(tmp_path):
    repo = workspace(tmp_path)
    (repo / R).write_text("# no draw block\nputs 1\n")
    _r, graph = built(tmp_path, repo=repo)
    assert rails_records(graph) == []
    summary = json.loads(manifest_value(graph))
    assert summary["state"] == "indexed" and summary["declarations"] == 0
    assert summary["producer_id"] is None
    only = tmp_path / "norepo"
    only.mkdir()
    (only / "a.rb").write_text("class A; end\n")
    _r, graph = built(tmp_path, repo=only, root="absent")
    assert json.loads(manifest_value(graph))["state"] == "absent"


def test_on_to_off_incremental_purges_every_rails_record(tmp_path):
    repo, graph = built(tmp_path)
    assert len(rails_records(graph)) == 106
    off, stats = rebuilt(repo, tmp_path, "g", frameworks=(), since="x")
    assert rails_records(off) == [] and manifest_value(off) is None
    with off.reader_lease() as lease:
        for table in ("producers", "evidence", "occurrences", "assertions"):
            for (payload,) in lease.db.execute(f"SELECT payload FROM {table}"):
                text = payload
                assert "routes_to" not in text and "rails_route" not in text
                assert '"rails"' not in text
    _r, fresh = built(tmp_path, root="fresh-off", repo=repo, frameworks=())
    assert [r["id"] for r in records(off)] == [r["id"] for r in records(fresh)]
    assert stats["binding_resolution"]["mode"] == "affected"
    on_again, stats = rebuilt(repo, tmp_path, "g", since="x")
    assert stats["binding_resolution"]["mode"] == "affected"
    assert len(rails_records(on_again)) == 106


def test_parser_is_only_used_when_enabled_and_routes_are_indexed(tmp_path, monkeypatch):
    calls: list[bytes] = []
    real = pack.get_parser

    class Counting:
        def __init__(self, parser):
            self.parser = parser

        def parse(self, source):
            calls.append(bytes(source))
            return self.parser.parse(source)

    monkeypatch.setattr(pack, "get_parser", lambda lang: Counting(real(lang)))
    repo, _graph = built(tmp_path, frameworks=())  # warm the extraction cache
    calls.clear()
    rebuilt(repo, tmp_path, "g", frameworks=())
    assert calls == []  # OFF: nothing is parsed at all
    rebuilt(repo, tmp_path, "g", frameworks=("rails",))
    assert calls == [ROUTES_BYTES]  # ON: routes.rb is re-parsed exactly once, outside the cache
    calls.clear()
    rebuilt(repo, tmp_path, "g", frameworks=("rails",))
    assert calls == [ROUTES_BYTES]
    # Absent routes.rb: no parse by the adapter.
    other = tmp_path / "other"
    other.mkdir()
    (other / "a.rb").write_text("class A; end\n")
    built(tmp_path, repo=other, root="other-g", frameworks=("rails",))
    calls.clear()
    rebuilt(other, tmp_path, "other-g", frameworks=("rails",))
    assert calls == []
    # Failed routes.rb: the adapter adds no parse beyond the native one.
    (repo / R).write_text("get ((((\n")
    broken = b"get ((((\n"
    calls.clear()
    graph, _stats = rebuilt(repo, tmp_path, "g", frameworks=("rails",))
    on_count = calls.count(broken)
    assert json.loads(manifest_value(graph))["state"] == "failed"
    calls.clear()
    rebuilt(repo, tmp_path, "g", frameworks=())
    assert on_count == calls.count(broken)


def test_export_import_round_trip_keeps_rails_records(tmp_path):
    repo, graph = built(tmp_path)
    exported = tmp_path / "graph.jsonl"
    runner = CliRunner()
    out = runner.invoke(
        cli, ["export", "--repo", str(repo), "--graph-root", str(tmp_path / "g"), str(exported)]
    )
    assert out.exit_code == 0, out.output
    other = tmp_path / "other"
    out = runner.invoke(
        cli, ["import", str(exported), "--repo", str(repo), "--graph-root", str(other)]
    )
    assert out.exit_code == 0, out.output
    imported = CodeGraph(repo, graph_root=other, read_only=True)
    assert rails_lines(imported) == rails_lines(graph) and rails_lines(graph)
    imported.close()
