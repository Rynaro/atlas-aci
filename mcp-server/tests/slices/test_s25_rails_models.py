"""ACI-S25 acceptance criteria: explicit Rails associations, tables and migration mentions.

Every graph is a real build of a tmp copy of ``tests/fixtures/rails_models/project`` with an
external graph root. Expected entity ids come from independent SQL on ``symbols``. Nothing the
adapter does is mocked: the spies in AC-007 only observe (and refuse) process, network and
repository-database activity.
"""

import ast
import asyncio
import hashlib
import json
import os
import re
import socket
import sqlite3
import subprocess
from pathlib import Path

import pytest
import tree_sitter_language_pack as pack
from test_s25_support import (
    CREATE,
    MAP_BYTES,
    MAP_SHA,
    POST,
    PROJECT,
    PROVENANCE,
    RELATIONS,
    SENTINELS,
    UP,
    USERS,
    built,
    copy_db,
    edit,
    entity_of,
    facts,
    in_file,
    inventory,
    manifest_value,
    model_lines,
    model_records,
    prime,
    rebuilt,
    workspace,
)

from atlas_aci.codegraph import CodeGraph
from atlas_aci.frameworks import rails_models
from atlas_aci.storage import evidence as ev

SRC = Path(rails_models.__file__).parent
POST_BYTES = (PROJECT / POST).read_bytes()


@pytest.fixture(scope="module")
def shared(tmp_path_factory):
    """One real ON build, shared by the read-only AC tests."""
    tmp = tmp_path_factory.mktemp("s25")
    repo, graph = built(tmp)
    return repo, graph, facts(graph)


def source_of(path):
    return (PROJECT / path).read_bytes()


def text_of(fact):
    occurrence = fact["occurrence"]
    return source_of(occurrence["path"])[occurrence["start_byte"] : occurrence["end_byte"]].decode()


def pick(found, path, **raw):
    chosen = [f for f in in_file(found, path) if all(f["raw"].get(k) == v for k, v in raw.items())]
    assert len(chosen) == 1, (path, raw, [f["raw"] for f in in_file(found, path)])
    return chosen[0]


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
    assert PROVENANCE["table_map"]["sha256"] == MAP_SHA


def test_fixture_totals_are_the_pinned_ones(shared):
    _repo, graph, found = shared
    expected = PROVENANCE["expected"]
    summary = json.loads(manifest_value(graph))
    for key in ("facts", "declarations", "files", "classes", "map"):
        assert summary[key] == expected[key], key
    assert len(found) == expected["records"]["assertions"]
    kinds = {}
    for item in model_records(graph):
        kinds[item["type"]] = kinds.get(item["type"], 0) + 1
    assert kinds == {"assertion": 58, "evidence": 58, "occurrence": 58, "producer": 1}


# ---- AC-S25-001 ----
def test_rails_explicit_association_target(shared):
    _repo, graph, found = shared
    post_entity = entity_of(graph, POST, "Blog::Post")
    user_entity = entity_of(graph, USERS, "User")
    a1 = pick(found, USERS, class_name="Blog::Post")
    assert a1["assertion"]["relation"] == "association"
    assert a1["assertion"]["resolution"] == "INFERRED"
    assert a1["assertion"]["target_entity_id"] == post_entity
    assert a1["assertion"]["unresolved_reason"] is None and a1["assertion"]["candidate_ids"] == []
    expected = ev.canonical(
        {
            "class_name": "Blog::Post",
            "kind": "has_many",
            "mapping": "rails_class_name.v1",
            "name": "posts",
        }
    )
    assert a1["assertion"]["raw_target"] == expected
    assert a1["occurrence"]["role"] == "rails_model:association:has_many"
    assert a1["occurrence"]["kind"] == "rails_model"
    assert a1["assertion"]["source_entity_id"] == user_entity == a1["occurrence"]["entity_id"]
    assert text_of(a1) == 'has_many :posts, class_name: "Blog::Post", dependent: :destroy'
    a2 = pick(found, USERS, class_name="::Profile")
    a8 = pick(found, POST, class_name="User")
    a10 = pick(found, POST, class_name="::Tag")
    assert a2["assertion"]["target_entity_id"] == entity_of(
        graph, "app/models/profile.rb", "Profile"
    )
    assert a8["assertion"]["target_entity_id"] == user_entity
    assert a10["assertion"]["target_entity_id"] == entity_of(graph, "app/models/tag.rb", "Tag")
    assert {f["raw"]["kind"] for f in (a1, a2, a8)} == {"has_many", "has_one", "belongs_to"}
    assert {f["occurrence"]["role"] for f in (a1, a2, a8)} == {
        "rails_model:association:has_many",
        "rails_model:association:has_one",
        "rails_model:association:belongs_to",
    }
    for fact in (a1, a2, a8, a10):
        assert fact["assertion"]["resolution"] == "INFERRED"
    assert a8["assertion"]["source_entity_id"] == post_entity
    # AMBIGUOUS: a reopened class (two entities) and two models reached by the candidate list.
    a3 = pick(found, USERS, class_name="Invoice")
    invoices = sorted(
        entity_of(graph, p, "Invoice")
        for p in ("app/models/invoice.rb", "app/models/invoice_reopened.rb")
    )
    assert a3["assertion"]["resolution"] == "AMBIGUOUS"
    assert a3["assertion"]["unresolved_reason"] == "ambiguous_class_name"
    assert a3["assertion"]["target_entity_id"] is None
    assert a3["assertion"]["candidate_ids"] == invoices and len(invoices) == 2
    a9 = pick(found, POST, class_name="Tag")
    tags = sorted(
        [
            entity_of(graph, "app/models/blog/tag.rb", "Blog::Tag"),
            entity_of(graph, "app/models/tag.rb", "Tag"),
        ]
    )
    assert a9["assertion"]["resolution"] == "AMBIGUOUS" and a9["assertion"]["candidate_ids"] == tags
    # UNRESOLVED with the closed reasons.
    for class_name, reason in (
        ("Team", "target_not_recognized_model"),
        ("Widget", "target_not_recognized_model"),
        ("Ghost", "class_not_found"),
        ("Vault", "class_not_found"),
    ):
        fact = pick(found, USERS, class_name=class_name)
        assert fact["assertion"]["resolution"] == "UNRESOLVED", class_name
        assert fact["assertion"]["unresolved_reason"] == reason, class_name
        assert fact["assertion"]["target_entity_id"] is None
        assert fact["assertion"]["candidate_ids"] == []
    associations = [f for f in found if f["assertion"]["relation"] == "association"]
    assert associations and not any(
        f["assertion"]["resolution"] == "EXTRACTED" for f in associations
    )
    assert {f["assertion"]["resolution"] for f in associations} == {
        "INFERRED",
        "AMBIGUOUS",
        "UNRESOLVED",
    }
    relabelled = ev.assertion(
        a1["assertion"]["source_entity_id"],
        a1["assertion"]["target_entity_id"],
        "association",
        a1["assertion"]["evidence_id"],
        "EXTRACTED",
        a1["assertion"]["raw_target"],
    )
    with pytest.raises(ValueError, match="association assertion"):
        ev.validate_record(relabelled)
    ev.validate_record(a1["assertion"])


def test_a_plain_object_shadowing_a_model_is_never_inferred(shared):
    """Blog::Label (a plain class) comes first in the candidate list and shadows the model
    ::Label: the model is not the answer, so the fact is not INFERRED to it."""
    _repo, graph, found = shared
    shadow = pick(found, POST, class_name="Label")
    assert shadow["assertion"]["resolution"] == "UNRESOLVED"
    assert shadow["assertion"]["unresolved_reason"] == "target_not_recognized_model"
    assert shadow["assertion"]["target_entity_id"] is None
    assert entity_of(graph, "app/models/label.rb", "Label")  # the model does exist
    assert entity_of(graph, "lib/blog/label.rb", "Blog::Label")  # and so does the shadow
    # A class name Rails would resolve to exactly one existing candidate stays INFERRED.
    assert pick(found, POST, class_name="User")["assertion"]["resolution"] == "INFERRED"


# ---- AC-S25-002 ----
def test_rails_explicit_model_table(shared, tmp_path):
    _repo, graph, found = shared
    profile = entity_of(graph, "app/models/profile.rb", "Profile")
    t1 = pick(found, "app/models/profile.rb", table="user_profiles")
    assert t1["assertion"]["relation"] == "model_table"
    assert t1["assertion"]["resolution"] == "EXTRACTED"
    assert t1["assertion"]["target_entity_id"] is None and t1["assertion"]["candidate_ids"] == []
    assert t1["assertion"]["raw_target"] == ev.canonical(
        {"source": "literal", "table": "user_profiles"}
    )
    assert t1["assertion"]["source_entity_id"] == profile == t1["occurrence"]["entity_id"]
    assert text_of(t1) == 'self.table_name = "user_profiles"'
    assert t1["occurrence"]["role"] == "rails_model:table:literal"
    t2 = pick(found, "app/models/blog/tag.rb", table="blog_tags")
    assert t2["assertion"]["resolution"] == "EXTRACTED"
    assert t2["assertion"]["raw_target"] == ev.canonical(
        {"map_sha256": MAP_SHA, "source": "operator_map", "table": "blog_tags"}
    )
    assert text_of(t2) == "class Blog::Tag < ApplicationRecord\nend"
    assert t2["occurrence"]["role"] == "rails_model:table:operator_map"
    assert t2["assertion"]["source_entity_id"] == entity_of(
        graph, "app/models/blog/tag.rb", "Blog::Tag"
    )
    # Exactly one EXTRACTED model_table per bound class; the others carry their reason.
    extracted = [
        f
        for f in found
        if f["assertion"]["relation"] == "model_table"
        and f["assertion"]["resolution"] == "EXTRACTED"
    ]
    assert sorted(f["occurrence"]["path"] for f in extracted) == [
        "app/models/blog/tag.rb",
        "app/models/profile.rb",
    ]
    reasons = {
        "app/models/tag.rb": ["conflicting_table_mapping"] * 2,
        "app/models/legacy/order.rb": ["ambiguous_table_name"] * 2,
        "app/models/invoice.rb": ["ambiguous_model"],
        "app/models/invoice_reopened.rb": ["ambiguous_model"],
        "app/models/blog/post.rb": ["unsupported_table_name"],
        "app/models/archive.rb": ["unsupported_table_name"] * 2,
    }
    for path, expected in reasons.items():
        mine = in_file(found, path, "model_table")
        assert [f["assertion"]["unresolved_reason"] for f in mine] == expected, path
        assert all(f["assertion"]["resolution"] == "UNRESOLVED" for f in mine)
    summary = json.loads(manifest_value(graph))
    assert summary["map"] == {
        "entries": 7,
        "bound": 1,
        "confirmed": 1,
        "conflicting": 1,
        "ambiguous": 1,
        "suppressed": 1,
        "unmatched": 2,
    }
    assert summary["table_map_sha256"] == MAP_SHA
    # No map: nothing is guessed. Blog::Tag has no table fact; Tag's literal is EXTRACTED.
    _r, plain = built(tmp_path, root="nomap", table_map=None)
    plain_facts = facts(plain)
    assert in_file(plain_facts, "app/models/blog/tag.rb") == []
    tag = in_file(plain_facts, "app/models/tag.rb", "model_table")
    assert [f["assertion"]["resolution"] for f in tag] == ["EXTRACTED"]
    assert tag[0]["raw"] == {"source": "literal", "table": "tags_v2"}
    assert in_file(plain_facts, "app/models/invoice.rb") == []
    summary = json.loads(manifest_value(plain))
    assert summary["map"] is None and summary["table_map_sha256"] is None
    # The record layer rejects an INFERRED table fact and a table fact with a target.
    base = t1["assertion"]
    args = (base["source_entity_id"], None, "model_table", base["evidence_id"])
    with pytest.raises(ValueError, match="rails model assertion"):
        ev.validate_record(ev.assertion(*args, "INFERRED", base["raw_target"]))
    with pytest.raises(ValueError, match="rails model assertion"):
        ev.validate_record(
            ev.assertion(args[0], profile, args[2], args[3], "EXTRACTED", base["raw_target"])
        )
    ev.validate_record(base)


# ---- AC-S25-003 ----
MIGRATIONS = {
    CREATE: [
        (
            "create_table :users do |t|\n      t.string :name\n    end",
            {"op": "create_table", "table": "users"},
        ),
        ('create_table "teams", force: true', {"op": "create_table", "table": "teams"}),
        (
            "add_column :users, :email, :string, null: false",
            {"column": "email", "op": "add_column", "table": "users"},
        ),
        (
            "remove_column :users, :legacy_flag, :boolean",
            {"column": "legacy_flag", "op": "remove_column", "table": "users"},
        ),
    ],
    UP: [
        (
            'add_column "blog_posts", "title", :string',
            {"column": "title", "op": "add_column", "table": "blog_posts"},
        ),
        (
            "remove_column :blog_posts, :title",
            {"column": "title", "op": "remove_column", "table": "blog_posts"},
        ),
    ],
    "db/migrate/20240103000000_legacy_rows.rb": [
        ("create_table :legacy_rows", {"op": "create_table", "table": "legacy_rows"})
    ],
}
MIGRATION_DIAGNOSTICS = [
    ("change_table :posts do |t|", "change_table"),
    ("drop_table :old_posts", "drop_table"),
    ("rename_table :posts, :articles", "rename_table"),
    ("rename_column :users, :name, :full_name", "rename_column"),
    ("add_reference :posts, :user", "add_reference"),
    ("add_index :users, :email", "add_index"),
    ('execute "DELETE FROM users"', "execute"),
    ("reversible do |dir|", "reversible"),
    ('add_index :users, :name if ENV["ATLAS"]', "conditional"),
    ("add_column table_name, :a, :string", "non_literal_table"),
    ("add_column :users, column_name, :string", "non_literal_column"),
    ('create_table "logs_#{year}"', "interpolated_literal"),
    ("remove_column :users", "missing_arguments"),
    ('File.write("ATLAS_S25_SENTINEL_MIGRATION", "x")', "other_statement"),
]


def test_rails_migration_table_mentions(shared):
    _repo, _graph, found = shared
    mentions = [f for f in found if f["assertion"]["relation"] == "migration_mentions"]
    supported = [f for f in mentions if f["assertion"]["resolution"] == "EXTRACTED"]
    assert len(supported) == 7 == sum(len(v) for v in MIGRATIONS.values())
    for path, expected in MIGRATIONS.items():
        mine = [f for f in in_file(found, path, "migration_mentions") if f in supported]
        assert [text_of(f) for f in mine] == [text for text, _raw in expected], path
        assert [f["raw"] for f in mine] == [raw for _text, raw in expected], path
        file_id = ev.file_entity(path, "ruby")["id"]
        for fact in mine:
            assertion = fact["assertion"]
            assert assertion["raw_target"] == ev.canonical(fact["raw"])
            assert assertion["resolution"] == "EXTRACTED"
            assert assertion["target_entity_id"] is None and assertion["candidate_ids"] == []
            assert assertion["unresolved_reason"] is None
            assert assertion["source_entity_id"] == file_id == fact["occurrence"]["entity_id"]
            assert fact["occurrence"]["role"] == "rails_model:migration:" + fact["raw"]["op"]
    # Nothing is read from helper classes, def self.up, schema.rb, seeds or nested directories.
    tables = {f["raw"].get("table") for f in supported}
    assert tables.isdisjoint({"old_style", "nope", "schema_only", "nested_old"})
    assert {f["occurrence"]["path"] for f in mentions} == set(MIGRATIONS) | {UP}
    assert not any(
        f["occurrence"]["path"] in {"db/schema.rb", "db/seeds.rb"}
        or f["occurrence"]["path"].startswith("db/migrate/archive/")
        for f in found
    )
    assert "name" not in {f["raw"].get("column") for f in supported}
    # Every other statement is a visible diagnostic with its closed code.
    diagnostics = [f for f in in_file(found, UP, "migration_mentions") if f not in supported]
    assert len(diagnostics) == len(MIGRATION_DIAGNOSTICS)
    for fact, (start, code) in zip(diagnostics, MIGRATION_DIAGNOSTICS, strict=True):
        assert text_of(fact).startswith(start), (text_of(fact), start)
        assert fact["assertion"]["resolution"] == "UNRESOLVED"
        assert fact["assertion"]["unresolved_reason"] == "unsupported_migration_op"
        assert fact["raw"] == {"construct": code, "mapping": "unexpanded"}
        assert fact["occurrence"]["role"] == "rails_model:migration:unsupported:" + code
    # The record layer accepts the target-less EXTRACTED mention, and only for this relation.
    sample = supported[0]["assertion"]
    ev.validate_record(sample)
    for relation in ("call", "routes_to", "superclass"):
        wrong = ev.assertion(
            sample["source_entity_id"],
            None,
            relation,
            sample["evidence_id"],
            "EXTRACTED",
            sample["raw_target"],
        )
        with pytest.raises(ValueError, match="Invalid evidence"):
            ev.validate_record(wrong)


# ---- AC-S25-004 ----
POST_DIAGNOSTICS = [
    ('has_many :taggings, through: :post_tags, class_name: "Tagging"', "has_many", "through"),
    ("belongs_to :commentable, polymorphic: true", "belongs_to", "polymorphic"),
    ('has_many :readers, class_name: "#{PREFIX}User"', "has_many", "computed_class_name"),
    ("has_many :editors, class_name: User.name", "has_many", "computed_class_name"),
    ("has_many :owners, class_name: :User", "has_many", "symbol_class_name"),
    ('has_many :partners, class_name: "user"', "has_many", "malformed_class_name"),
    ('has_many :recent, -> { order(:id) }, class_name: "Comment"', "has_many", "scope_argument"),
    ('has_many :versions, class_name: "Version" do\n    end', "has_many", "block"),
    ("has_many(*SHARED)", "has_many", "splat"),
    ('has_many relation_name, class_name: "User"', "has_many", "non_literal_name"),
    ("has_and_belongs_to_many :categories", "has_and_belongs_to_many", "has_and_belongs_to_many"),
    ("has_many :tenants, as: :tenant", "has_many", "as"),
    ("has_many :loners, anonymous_class: Object", "has_many", "unsupported_option"),
    ('has_many :y, :z, class_name: "User"', "has_many", "extra_arguments"),
]


def test_rails_unsupported_association_is_visible(shared):
    _repo, _graph, found = shared
    diagnostics = [
        f
        for f in in_file(found, POST, "association")
        if f["assertion"]["unresolved_reason"] == "unsupported_association"
    ]
    assert len(diagnostics) == len(POST_DIAGNOSTICS) == 14
    for fact, (statement, method, code) in zip(diagnostics, POST_DIAGNOSTICS, strict=True):
        assertion = fact["assertion"]
        assert text_of(fact) == statement
        assert fact["occurrence"]["role"] == "rails_model:association:unsupported:" + code
        assert assertion["resolution"] == "UNRESOLVED"
        assert assertion["unresolved_reason"] == "unsupported_association"
        assert assertion["raw_target"] == ev.canonical(
            {"construct": code, "kind": method, "mapping": "unexpanded"}
        )
        assert assertion["target_entity_id"] is None and assertion["candidate_ids"] == []
        start, end = fact["occurrence"]["start_byte"], fact["occurrence"]["end_byte"]
        assert POST_BYTES[start:end].decode() == statement
    supported_names = {
        f["raw"]["name"]
        for f in found
        if f["assertion"]["relation"] == "association" and "name" in f["raw"]
    }
    assert supported_names.isdisjoint(
        {"taggings", "commentable", "readers", "editors", "owners", "partners", "tenants"}
    )
    implicit = [
        f
        for f in in_file(found, USERS, "association")
        if f["raw"].get("construct") == "implicit_class_name"
    ]
    assert len(implicit) == 1 and text_of(implicit[0]) == "has_many :comments"
    # Silent by design: with_options, validations, callbacks, STI subclasses and concerns.
    assert in_file(found, "app/models/admin.rb") == []
    assert in_file(found, "app/models/concerns/taggable.rb") == []
    assert not any("with_options" in text_of(f) or "after_save" in text_of(f) for f in found)


# ---- AC-S25-005 ----
def line_and_column(source, offset):
    line = source.count(b"\n", 0, offset) + 1
    return line, offset - (source.rfind(b"\n", 0, offset) + 1)


def test_rails_model_relation_provenance(tmp_path):
    repo, graph = built(tmp_path)
    found = facts(graph)
    assert {f["assertion"]["relation"] for f in found} == set(RELATIONS)
    with graph.reader_lease() as lease:
        observed = {
            o["path"]: o
            for o in json.loads(
                lease.db.execute(
                    "SELECT value FROM manifest WHERE key='source_observations'"
                ).fetchone()[0]
            )
            if o["type"] == "observation"
        }
        inputs = json.loads(
            lease.db.execute("SELECT value FROM manifest WHERE key='generation_inputs'").fetchone()[
                0
            ]
        )
        assert lease.snapshot_id and lease.snapshot_id == inputs["snapshot"]["id"]
        sample = {}
        for fact in found:
            occurrence, proof = fact["occurrence"], fact["evidence"]
            path = occurrence["path"]
            data = (repo / path).read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            for key in ("source_sha256", "source_size", "start_byte", "end_byte"):
                assert proof[key] == occurrence[key], key
            assert occurrence["source_sha256"] == observed[path]["observed_source_sha256"] == sha
            assert occurrence["source_size"] == len(data)
            start, end = occurrence["start_byte"], occurrence["end_byte"]
            assert (occurrence["line"], occurrence["column"]) == line_and_column(data, start)
            assert (occurrence["end_line"], occurrence["end_column"]) == line_and_column(data, end)
            assert lease.read_source(path, sha)[start:end] == data[start:end]
            assert occurrence["provenance_state"] == "source" == proof["provenance_state"]
            sample[occurrence["id"]] = (path, sha, start, end, data[start:end])
    assert len(sample) == len(found) == 58
    # Bound by the snapshot, not by the working tree: overwrite a file, the bytes stay.
    path = USERS
    original = (repo / path).read_bytes()
    victim = next(f for f in found if f["occurrence"]["path"] == path)
    (repo / path).write_bytes(b"# overwritten after the build\n" * 40)
    with graph.reader_lease() as lease:
        _path, sha, start, end, expected = sample[victim["occurrence"]["id"]]
        assert lease.read_source(path, sha)[start:end] == expected
        assert b"overwritten" not in lease.read_source(path, sha)
    (repo / path).write_bytes(original)
    # Map facts: the class span plus the map identity.
    maps = [f for f in found if f["raw"].get("source") == "operator_map"]
    assert len(maps) == 4
    producer = next(r for r in model_records(graph) if r["type"] == "producer")
    assert producer["fingerprint"]["rails_models"]["table_map_sha256"] == MAP_SHA
    for fact in maps:
        assert fact["raw"]["map_sha256"] == MAP_SHA
        data = (repo / fact["occurrence"]["path"]).read_bytes()
        covered = data[fact["occurrence"]["start_byte"] : fact["occurrence"]["end_byte"]]
        assert covered.startswith(b"class ") and covered.rstrip().endswith(b"end")


# ---- AC-S25-006 ----
def test_rails_model_reindex_is_idempotent(tmp_path):
    repo, one = built(tmp_path, root="one")
    _r, two = built(tmp_path, root="two", repo=repo)
    first = model_lines(one)
    assert first and first == model_lines(two)
    assert manifest_value(one) == manifest_value(two)
    graph, stats = rebuilt(repo, tmp_path, "one", since="x")
    assert model_lines(graph) == first and manifest_value(graph) == manifest_value(one)
    resolution = stats["binding_resolution"]
    assert (resolution["mode"], resolution["reason"], resolution["affected_units"]) == (
        "affected",
        "complete_dependency_match",
        0,
    )
    summary = json.loads(manifest_value(one))
    assert (
        summary["adapter_sha256"]
        == hashlib.sha256((SRC / "rails_models.py").read_bytes()).hexdigest()
    )
    assert summary["table_map_sha256"] == MAP_SHA
    producers = [r for r in model_records(one) if r["type"] == "producer"]
    assert len(producers) == 1
    native = CodeGraph(repo, graph_root=tmp_path / "one", frameworks=("rails_models",))
    native.build(since="x")
    fingerprint = producers[0]["fingerprint"]["rails_models"]
    assert fingerprint["graph_producer_id"] == native._capture_producer["id"]
    assert fingerprint["adapter_sha256"] == summary["adapter_sha256"]
    assert fingerprint["table_map_sha256"] == MAP_SHA
    # Non-vacuous controls: a literal edit and a changed map value each change the set.
    edit(repo, USERS, ':posts, class_name: "Blog::Post"', ':posts, class_name: "Blog::Posts"')
    changed, _stats = rebuilt(repo, tmp_path, "one", since="x")
    assert model_lines(changed) != first
    edit(repo, USERS, ':posts, class_name: "Blog::Posts"', ':posts, class_name: "Blog::Post"')
    restored, _stats = rebuilt(repo, tmp_path, "one", since="x")
    assert model_lines(restored) == first
    remapped = MAP_BYTES.replace(b'"blog_tags"', b'"blog_labels"')
    assert remapped != MAP_BYTES
    other, _stats = rebuilt(repo, tmp_path, "one", since="x", table_map=remapped)
    assert model_lines(other) != first
    assert json.loads(manifest_value(other))["table_map_sha256"] != MAP_SHA


# ---- AC-S25-007 ----
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
    "shutil",
    "tempfile",
    "glob",
    "asyncio",
    "threading",
    "ssl",
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
SQLITE_EXECUTE = {"execute", "executemany", "executescript"}
BANNED_EXTRA_ATTRIBUTES = {"connect", "create_connection", "urlopen"}


def banned_attribute(name):
    if name in SQLITE_EXECUTE:
        return False
    return name in {"system", "popen"} | BANNED_EXTRA_ATTRIBUTES or name.startswith(
        ("spawn", "exec", "fork")
    )


def regex_literals(tree):
    """String constants passed as the first argument of a `re.<function>` call."""
    found = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "re"
            and node.args
        ):
            found.extend(
                const.value
                for const in ast.walk(node.args[0])
                if isinstance(const, ast.Constant) and isinstance(const.value, (str, bytes))
            )
    return found


def test_adapter_sources_are_static_ascii_and_never_call_themselves():
    sources = sorted(SRC.glob("*.py"))
    assert {p.name for p in sources} == {"__init__.py", "rails_models.py", "rails_routes.py"}
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
            if isinstance(node, ast.arg):
                assert node.arg not in BANNED_NAMES, (path, node.arg)
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
        for literal in regex_literals(tree):
            assert "$" not in str(literal), (path, literal)
        assert "alter table" not in text.lower() and "nx.compose(" not in text
    assert reads == 2  # one Path(__file__).read_bytes() per adapter, for adapter_sha256


def install_spies(monkeypatch, launches, connections, tmp_path):
    def popen(argv, *args, **kwargs):
        launches.append(list(argv) if not isinstance(argv, str) else [argv])
        raise FileNotFoundError("process launch refused by the S25 spy")

    monkeypatch.setattr(subprocess, "Popen", popen)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("the Rails model adapter must never launch a process or connect")

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
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    real_connect = sqlite3.connect

    def connect(database, *args, **kwargs):
        target = str(database)
        if target.startswith("file:"):
            target = target[len("file:") :].split("?", 1)[0]
        if target not in {"", ":memory:"}:
            connections.append(Path(target).resolve())
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)


def test_rails_models_never_execute_database_code(tmp_path, monkeypatch):
    repo = workspace(tmp_path)
    app_db = repo / "db/development.sqlite3"
    seed = sqlite3.connect(app_db)  # a real application database, created before the spy
    seed.execute("CREATE TABLE users (id INTEGER)")
    seed.commit()
    seed.close()
    before = inventory(repo)
    launches: list[list[str]] = []
    connections: list[Path] = []
    install_spies(monkeypatch, launches, connections, tmp_path)
    # Liveness: each spy sees its own kind of activity, then is reset.
    with pytest.raises(FileNotFoundError):
        subprocess.run(["true"], check=False)
    assert launches == [["true"]]
    probe = tmp_path / "probe.db"
    sqlite3.connect("file:" + str(probe) + "?mode=rwc", uri=True).close()
    sqlite3.connect(":memory:").close()
    assert connections == [probe.resolve()]
    with pytest.raises(AssertionError):
        socket.create_connection(("127.0.0.1", 9))
    launches.clear()
    connections.clear()
    graph_root = tmp_path / "on"
    _r, on = built(tmp_path, root="on", repo=repo, warm=False)
    on_launches = [list(argv) for argv in launches]
    on_connections = list(connections)
    launches.clear()
    inferred = [f for f in facts(on) if f["assertion"]["resolution"] == "INFERRED"]
    assert len(inferred) >= 4
    assert on_launches, "the pre-existing snapshot identity probe is the only launch"
    for argv in on_launches:
        assert argv[:2] == ["git", "-C"] and "rev-parse" in argv
        for token in [*argv[:2], *argv[3:]]:  # argv[2] is the tmp repo path
            assert not re.search(r"ruby|rails|rake|bundle|migrate|\.rb", token), argv
    assert on_connections, "the graph itself opens SQLite databases"
    for target in on_connections:
        assert target.is_relative_to(graph_root.resolve()) or target.is_relative_to(tmp_path)
        assert not target.is_relative_to(repo.resolve()), target
    _r, off = built(tmp_path, root="off", repo=repo, frameworks=(), warm=False)
    assert [list(a) for a in launches] == on_launches
    assert model_records(off) == []
    launches.clear()
    for name in SENTINELS:
        assert not (repo / name).exists() and not (Path.cwd() / name).exists()
    assert inventory(repo) == before and "db/development.sqlite3" in before
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
    parser = pack.get_parser("ruby")
    budget = rails_models.Budget()
    source = (PROJECT / USERS).read_bytes()
    assert rails_models.scan_models(parser.parse(source), source, budget)
    capture = rails_models.Capture()
    for path in sorted(p.relative_to(repo).as_posix() for p in repo.rglob("*.rb")):
        if "/storage/" in path:
            continue  # a skip pattern: the build never observed it
        data = (repo / path).read_bytes()
        capture.offer(path, data, hashlib.sha256(data).hexdigest())
    assert len(capture.files) == 17
    summary = rails_models.refresh(
        conn,
        enabled=True,
        capture=capture,
        table_map=rails_models.parse_table_map(MAP_BYTES),
        observations=observations,
        get_parser=pack.get_parser,
        graph_producer_id=native,
    )
    assert summary is not None and summary["files"]["failed"] == 0 and launches == []
    assert summary["facts"]["association"]["INFERRED"] == 4
    for name in SENTINELS:
        assert not (Path.cwd() / name).exists() and not (tmp_path / name).exists()
    for target in connections:
        assert not target.is_relative_to(repo.resolve()), target


# ---- supporting: vocabularies and limits ----
def test_vocabularies_are_the_closed_sets():
    m = rails_models
    assert {"belongs_to", "has_one", "has_many"} == m.ASSOCIATION_KINDS
    assert m.ASSOCIATION_KINDS | {"has_and_belongs_to_many"} == m.ASSOCIATION_METHODS
    assert {
        "has_and_belongs_to_many",
        "through",
        "source",
        "source_type",
        "polymorphic",
        "as",
        "block",
        "splat",
        "scope_argument",
        "extra_arguments",
        "non_literal_name",
        "unsupported_option",
        "implicit_class_name",
        "symbol_class_name",
        "computed_class_name",
        "malformed_class_name",
        "declaration_limit",
    } == m.ASSOCIATION_CONSTRUCTS
    assert {
        "class_name",
        "foreign_key",
        "primary_key",
        "inverse_of",
        "dependent",
        "optional",
        "required",
        "touch",
        "autosave",
        "validate",
        "counter_cache",
        "strict_loading",
    } == m.ALLOWED_ASSOCIATION_OPTIONS
    assert {
        "symbol_table_name",
        "computed_table_name",
        "malformed_table_name",
        "table_name_method",
        "declaration_limit",
    } == m.TABLE_CONSTRUCTS
    assert {"create_table", "add_column", "remove_column"} == m.MIGRATION_OPS
    assert {
        "change_table",
        "drop_table",
        "rename_table",
        "rename_column",
        "add_reference",
        "remove_reference",
        "add_belongs_to",
        "remove_belongs_to",
        "add_index",
        "remove_index",
        "add_timestamps",
        "remove_timestamps",
        "add_foreign_key",
        "remove_foreign_key",
        "change_column",
        "change_column_default",
        "change_column_null",
        "create_join_table",
        "drop_join_table",
        "execute",
        "reversible",
        "revert",
        "up_only",
        "remove_columns",
    } == m.MIGRATION_STATEMENT_CONSTRUCTS
    assert {
        "block",
        "splat",
        "missing_arguments",
        "extra_arguments",
        "non_literal_table",
        "non_literal_column",
        "interpolated_literal",
        "malformed_literal",
    } == m.MIGRATION_OP_CONSTRUCTS
    assert {"conditional", "other_statement", "declaration_limit"} == m.MIGRATION_OTHER_CONSTRUCTS
    counts = (
        len(m.ASSOCIATION_CONSTRUCTS),
        len(m.TABLE_CONSTRUCTS),
        len(m.MIGRATION_STATEMENT_CONSTRUCTS),
        len(m.MIGRATION_OP_CONSTRUCTS),
        len(m.MIGRATION_OTHER_CONSTRUCTS),
        len(m.REASONS),
    )
    assert counts == (17, 5, 24, 8, 3, 9)
    assert {
        "ambiguous_class_name",
        "target_not_recognized_model",
        "class_not_found",
        "unsupported_association",
        "conflicting_table_mapping",
        "ambiguous_table_name",
        "ambiguous_model",
        "unsupported_table_name",
        "unsupported_migration_op",
    } == m.REASONS
    assert {"rails_class_name.v1", "unexpanded"} == m.LABELS
    assert {"literal", "operator_map"} == m.TABLE_SOURCES
    limits = (
        m.MAX_FILES,
        m.MAX_FILE_BYTES,
        m.MAX_CAPTURE_BYTES,
        m.MAX_DECLARATIONS,
        m.MAX_MAP_BYTES,
        m.MAX_MAP_ENTRIES,
        m.MAX_MAP_NESTING,
        m.MAX_NAME_BYTES,
        m.MAX_CLASS_NAME_BYTES,
        m.MAX_TABLE_BYTES,
    )
    assert limits == (10000, 1048576, 67108864, 20000, 1048576, 10000, 4, 128, 256, 64)
    assert m.CLASS_NAME_RE.pattern == r"(::)?[A-Z][A-Za-z0-9_]*(::[A-Z][A-Za-z0-9_]*)*"
    assert m.MODEL_KEY_RE.pattern == r"[A-Z][A-Za-z0-9_]*(::[A-Z][A-Za-z0-9_]*)*"
    assert (m.NAME_RE.pattern, m.IDENT_RE.pattern) == (
        r"[a-z_][a-z0-9_]*",
        r"[A-Za-z_][A-Za-z0-9_]*",
    )
    assert m.MIGRATION_BASE_RE.pattern == rb"ActiveRecord::Migration(\[[0-9]+\.[0-9]+\])?"
    assert {b"ApplicationRecord", b"ActiveRecord::Base", b"::ActiveRecord::Base"} == m.MODEL_BASES
    assert m.is_model_path("app/models/a.rb") and m.is_model_path("app/models/x/y.rb")
    assert not m.is_model_path("app/models/concerns/a.rb")
    assert not m.is_model_path("app/models/a.py") and not m.is_model_path("lib/a.rb")
    assert m.is_migration_path("db/migrate/1_a.rb")
    assert not m.is_migration_path("db/migrate/x/1_a.rb")
    assert not m.is_migration_path("db/schema.rb") and not m.is_migration_path("db/migrate/a.py")


def test_class_name_candidates_follow_the_compute_type_list():
    candidates = rails_models.class_candidates
    assert candidates("Blog::Post::Comment", "Tag") == [
        "Blog::Post::Comment::Tag",
        "Blog::Post::Tag",
        "Blog::Tag",
        "Tag",
    ]
    assert candidates("User", "Blog::Post") == ["User::Blog::Post", "Blog::Post"]
    assert candidates("Blog::Post", "::Tag") == ["Tag"]
    assert candidates("Tag", "Tag") == ["Tag::Tag", "Tag"]


# ---- supporting: scans ----
def scan_body(body, header="class M < ApplicationRecord"):
    source = (header + "\n" + body + "\nend\n").encode()
    tree = pack.get_parser("ruby").parse(source)
    assert not tree.root_node.has_error, body
    return rails_models.scan_models(tree, source, rails_models.Budget()), source


def only_declaration(body):
    classes, _source = scan_body(body)
    assert len(classes) == 1 and len(classes[0].declarations) == 1, body
    return classes[0].declarations[0]


ASSOCIATION_CASES = [
    ("has_and_belongs_to_many :x", "has_and_belongs_to_many"),
    ("has_many :x, through: :y", "through"),
    ("has_many :x, source: :y", "source"),
    ('has_many :x, source_type: "Y"', "source_type"),
    ("belongs_to :x, polymorphic: true", "polymorphic"),
    ("has_many :x, as: :y", "as"),
    ('has_many :x, class_name: "A" do\n  end', "block"),
    ("has_many(*LIST)", "splat"),
    ("has_many :x, **OPTIONS", "splat"),
    ("has_many :x, &callback", "splat"),
    ("has_many :x, -> { where(a: 1) }", "scope_argument"),
    ("has_many :x, :y", "extra_arguments"),
    ('has_many({class_name: "A"})', "extra_arguments"),
    ("has_many relation", "non_literal_name"),
    ("has_many", "non_literal_name"),
    ('has_many class_name: "A"', "non_literal_name"),
    ('has_many :"x y", class_name: "A"', "non_literal_name"),
    ('has_many "Upper", class_name: "A"', "non_literal_name"),
    ('has_many "a#{b}", class_name: "A"', "non_literal_name"),
    ("has_many :" + "x" * 129 + ', class_name: "A"', "non_literal_name"),
    ('has_many :x, class_name: "A", anonymous_class: B', "unsupported_option"),
    ('has_many :x, "class_name" => "A"', "unsupported_option"),
    ('has_many :x, dependent: :a, dependent: :b, class_name: "A"', "unsupported_option"),
    ("has_many :x", "implicit_class_name"),
    ("has_many :x, dependent: :destroy", "implicit_class_name"),
    ("has_many :x, class_name: :A", "symbol_class_name"),
    ('has_many :x, class_name: :"A"', "symbol_class_name"),
    ('has_many :x, class_name: "A#{b}"', "computed_class_name"),
    ("has_many :x, class_name: A.name", "computed_class_name"),
    ("has_many :x, class_name: NAME", "computed_class_name"),
    ('has_many :x, class_name: "a"', "malformed_class_name"),
    ('has_many :x, class_name: ""', "malformed_class_name"),
    ('has_many :x, class_name: "A::"', "malformed_class_name"),
    ("has_many :x, class_name: %q(A)", "malformed_class_name"),
    ('has_many :x, class_name: "A' + "b" * 256 + '"', "malformed_class_name"),
    ('has_many :x, class_name: "A"', None),
    ('belongs_to :x, class_name: "::A::B", optional: true', None),
    ("has_one 'x', class_name: 'A_1', foreign_key: :a_id, inverse_of: :b", None),
]
TABLE_CASES = [
    ('self.table_name = "x"', "literal"),
    ("self.table_name = :x", "symbol_table_name"),
    ('self.table_name = :"x"', "symbol_table_name"),
    ('self.table_name = "a_#{b}"', "computed_table_name"),
    ("self.table_name = compute", "computed_table_name"),
    ('self.table_name ||= "x"', "computed_table_name"),
    ('self.table_name = "bad-name"', "malformed_table_name"),
    ('self.table_name = ""', "malformed_table_name"),
    ('self.table_name = "' + "t" * 65 + '"', "malformed_table_name"),
    ('def self.table_name\n    "x"\n  end', "table_name_method"),
]
SILENT_STATEMENTS = [
    'other.table_name = "x"',
    'table_name = "x"',
    'self.table_names = "x"',
    "validates :x, presence: true",
    "self.abstract_class = true",
    "scope :a, -> { all }",
    'def table_name; "x"; end',
    "class << self\n    def table_name; end\n  end",
    'with_options(dependent: :destroy) do\n    has_many :x, class_name: "A"\n  end',
    'if ENV["X"]\n    has_many :x, class_name: "A"\n  end',
    'included do\n    has_many :x, class_name: "A"\n  end',
    "x.has_many :a",
]


@pytest.mark.parametrize(("statement", "code"), ASSOCIATION_CASES)
def test_association_rules(statement, code):
    declaration = only_declaration(statement)
    assert declaration.area == "association"
    assert declaration.construct == code, (statement, declaration.construct)
    if code is None:
        assert declaration.class_name is not None
        assert declaration.role.startswith("rails_model:association:")
        assert not declaration.role.startswith("rails_model:association:unsupported")


@pytest.mark.parametrize(("statement", "code"), TABLE_CASES)
def test_table_rules(statement, code):
    declaration = only_declaration(statement)
    assert declaration.area == "table"
    if code == "literal":
        assert declaration.construct is None and declaration.table == "x"
    else:
        assert declaration.construct == code and declaration.table is None


@pytest.mark.parametrize("statement", SILENT_STATEMENTS)
def test_unrecognised_statements_are_silent(statement):
    classes, _source = scan_body(statement)
    assert len(classes) == 1 and classes[0].declarations == []


def migration_scan(body, header="class M < ActiveRecord::Migration[7.1]"):
    source = (header + "\n  def change\n    " + body + "\n  end\nend\n").encode()
    tree = pack.get_parser("ruby").parse(source)
    assert not tree.root_node.has_error, body
    scan = rails_models.scan_migrations(tree, source, rails_models.Budget())
    return scan, source


MIGRATION_CASES = [
    ("create_table :x", None),
    ('create_table "x", force: true do |t|\n      t.string :a\n    end', None),
    ("add_column :x, :c, :string, null: false", None),
    ("remove_column :x, :c", None),
    ("remove_column :x, :c, :string", None),
    ("add_column :x, :c, :string do end", "block"),
    ("add_column(*ARGS)", "splat"),
    ("create_table :x, **OPTIONS", "splat"),
    ("add_column :x, :c", "missing_arguments"),
    ("remove_column :x", "missing_arguments"),
    ("create_table :x, :y", "extra_arguments"),
    ("add_column :x, :c, :t, :u", "extra_arguments"),
    ("create_table table", "non_literal_table"),
    ('create_table :"x y"', "non_literal_table"),
    ("add_column :x, column, :t", "non_literal_column"),
    ('create_table "x#{y}"', "interpolated_literal"),
    ('create_table "x-y"', "malformed_literal"),
    ('create_table ""', "malformed_literal"),
    ("create_table :" + "x" * 65, "malformed_literal"),
    ('add_column :x, "c-d", :t', "malformed_literal"),
    ("foo if bar", "conditional"),
    ("foo unless bar", "conditional"),
    ("if a\n      foo\n    end", "conditional"),
    ("case a\n    when 1 then foo\n    end", "conditional"),
    ("x = 1", "other_statement"),
    ('File.write("a", "b")', "other_statement"),
    ("self.foo", "other_statement"),
    ("create_table", "other_statement"),
    *[(name + " :x", name) for name in sorted(rails_models.MIGRATION_STATEMENT_CONSTRUCTS)],
    ("up_only do\n      foo\n    end", "up_only"),
]


@pytest.mark.parametrize(("statement", "code"), MIGRATION_CASES)
def test_migration_rules(statement, code):
    scan, _source = migration_scan(statement)
    assert scan.classes == 1 and len(scan.declarations) == 1, statement
    declaration = scan.declarations[0]
    assert declaration.area == "migration"
    assert declaration.construct == code, (statement, declaration.construct)


def test_every_construct_code_is_reachable():
    association = {code for _s, code in ASSOCIATION_CASES if code}
    table = {code for _s, code in TABLE_CASES if code != "literal"}
    migration = {code for _s, code in MIGRATION_CASES if code}
    # `declaration_limit` is reached through the budget, in each area.
    association.add("declaration_limit")
    table.add("declaration_limit")
    migration.add("declaration_limit")
    assert association == rails_models.ASSOCIATION_CONSTRUCTS
    assert table == rails_models.TABLE_CONSTRUCTS
    assert migration == rails_models.MIGRATION_CONSTRUCTS


def test_declaration_limit_stops_the_scan_and_marks_the_class_incomplete(monkeypatch):
    monkeypatch.setattr(rails_models, "MAX_DECLARATIONS", 2)
    source = (
        b"class A < ApplicationRecord\n"
        b'  has_many :a, class_name: "X"\n'
        b'  self.table_name = "a"\n'
        b'  has_many :b, class_name: "X"\n'
        b'  has_many :c, class_name: "X"\n'
        b"end\n"
        b"class B < ApplicationRecord\n"
        b'  self.table_name = "b"\n'
        b"end\n"
    )
    tree = pack.get_parser("ruby").parse(source)
    budget = rails_models.Budget()
    classes = rails_models.scan_models(tree, source, budget)
    assert budget.stopped and budget.used == 2
    first, second = classes
    assert not first.complete and not second.complete
    codes = [d.construct for d in first.declarations]
    assert (
        codes == [None, None, "declaration_limit"] and first.declarations[-1].area == "association"
    )
    assert second.declarations == []  # classes are still listed (the model index stays whole)
    # The same budget carries over to migrations, where the first statement is the limit.
    migration = (
        b"class M < ActiveRecord::Migration[7.0]\n  def change\n    execute 'x'\n  end\nend\n"
    )
    scan = rails_models.scan_migrations(pack.get_parser("ruby").parse(migration), migration, budget)
    assert [d.construct for d in scan.declarations] == []  # stopped: nothing more is read


def test_the_declaration_limit_in_a_build_reports_truncation(tmp_path, monkeypatch):
    monkeypatch.setattr(rails_models, "MAX_DECLARATIONS", 2)
    _repo, graph = built(tmp_path)
    found = facts(graph)
    limit = [f for f in found if f["raw"].get("construct") == "declaration_limit"]
    assert len(limit) == 1 and limit[0]["occurrence"]["path"] == "app/models/blog/post.rb"
    assert limit[0]["occurrence"]["role"] == "rails_model:table:unsupported:declaration_limit"
    summary = json.loads(manifest_value(graph))
    assert summary["truncated"] is True and summary["declarations"] == 3
    assert len(found) <= 2 + 1 + rails_models.MAX_MAP_ENTRIES
    # The model index stays complete: associations resolve although the scan stopped early.
    assert summary["classes"]["models"] == 11


def test_scans_return_plain_spans_and_hold_no_parse_tree():
    classes, _source = scan_body('has_many :x, class_name: "A"\n  self.table_name = "t"')
    scan, _src = migration_scan("create_table :x")
    seen = []
    pending = [classes, scan]
    while pending:
        item = pending.pop()
        if isinstance(item, (list, tuple)):
            pending.extend(item)
        elif hasattr(item, "__dataclass_fields__"):
            seen.append(type(item).__name__)
            pending.extend(getattr(item, name) for name in item.__dataclass_fields__)
        else:
            assert type(item).__module__ in {"builtins"}, (type(item), item)
    assert {"Span", "Declaration", "ModelClass", "MigrationScan"} <= set(seen)
    declaration = classes[0].declarations[0]
    assert (declaration.span.start_byte, declaration.span.end_byte) < (10**6, 10**6)
    assert declaration.span.start_point[0] == 1


# ---- supporting: capture bounds (checker condition 2) ----
def eligible_sizes():
    return sorted(
        (name, meta["bytes"])
        for name, meta in PROVENANCE["files"].items()
        if (rails_models.is_model_path(name) or rails_models.is_migration_path(name))
        and "/storage/" not in name
    )


def test_capture_is_bounded_by_count_per_file_bytes_and_total_bytes(tmp_path, monkeypatch):
    eligible = eligible_sizes()
    assert len(eligible) == 17
    monkeypatch.setattr(rails_models, "MAX_FILES", 5)
    repo, graph = built(tmp_path, root="count")
    summary = json.loads(manifest_value(graph))
    assert summary["files"]["limited"] == 12 and summary["truncated"] is True
    assert summary["files"]["models"] + summary["files"]["migrations"] == 5
    monkeypatch.setattr(rails_models, "MAX_FILES", 10000)
    monkeypatch.setattr(rails_models, "MAX_FILE_BYTES", 400)
    _r, graph = built(tmp_path, root="file", repo=repo)
    summary = json.loads(manifest_value(graph))
    big = [name for name, size in eligible if size > 400]
    assert big and summary["files"]["limited"] == len(big) and summary["truncated"] is True
    assert summary["files"]["models"] + summary["files"]["migrations"] == 17 - len(big)
    monkeypatch.setattr(rails_models, "MAX_FILE_BYTES", 1048576)
    monkeypatch.setattr(rails_models, "MAX_CAPTURE_BYTES", 1500)
    _r, graph = built(tmp_path, root="total", repo=repo)
    summary = json.loads(manifest_value(graph))
    taken, total = 0, 0
    for _name, size in eligible:  # the build offers files in sorted path order
        if total + size <= 1500:
            taken, total = taken + 1, total + size
    assert 0 < taken < 17
    assert summary["files"]["limited"] == 17 - taken and summary["truncated"] is True
    assert summary["files"]["models"] + summary["files"]["migrations"] == taken
    # Not offered when the adapter is off: no capture at all.
    capture = rails_models.Capture()
    capture.offer("lib/a.rb", b"x", "0" * 64)
    assert capture.files == {} and capture.limited == 0


def test_capture_is_flag_gated(tmp_path, monkeypatch):
    offered = []
    real = rails_models.Capture.offer

    def spy(self, path, source, source_hash):
        offered.append(path)
        return real(self, path, source, source_hash)

    monkeypatch.setattr(rails_models.Capture, "offer", spy)
    repo, graph = built(tmp_path, frameworks=())
    assert offered == [] and model_records(graph) == []
    built(tmp_path, root="rails-only", frameworks=("rails",), repo=repo)
    assert offered == []
    built(tmp_path, root="models", repo=repo)
    assert offered and all(p.endswith(".rb") for p in offered)


# ---- supporting: class binding (checker condition 3) ----
def test_unbound_classes_are_counted_and_never_guessed(tmp_path):
    _r, graph = built(tmp_path)
    found = facts(graph)
    assert json.loads(manifest_value(graph))["classes"]["unbound"] == 1
    assert in_file(found, "app/models/zed/wrapped.rb") == []  # compact name inside a module
    repo = tmp_path / "wrapped"
    (repo / "app/models").mkdir(parents=True)
    (repo / "app/models/user.rb").write_text("class User < ApplicationRecord\nend\n")
    (repo / "app/models/rooted.rb").write_text(
        "module ::Rooted\n  class Thing < ApplicationRecord\n"
        '    has_many :a, class_name: "User"\n    self.table_name = "things"\n  end\nend\n'
    )
    (repo / "app/models/nested.rb").write_text(
        "class Outer < ApplicationRecord\n  class Inner::Deep < ApplicationRecord\n"
        '    belongs_to :u, class_name: "User"\n  end\nend\n'
    )
    (repo / "app/models/plain.rb").write_text(
        'class Plain < ApplicationRecord\n  belongs_to :u, class_name: "User"\nend\n'
    )
    _r, other = built(tmp_path, root="wrapped-g", repo=repo, table_map=None)
    summary = json.loads(manifest_value(other))
    assert summary["classes"] == {"models": 3, "migrations": 0, "unbound": 2}
    fact_paths = {f["occurrence"]["path"] for f in facts(other)}
    assert fact_paths == {"app/models/plain.rb"}
    # A map entry cannot bind an unbound class either: it counts as unmatched.
    mapped = json.dumps(
        {
            "format": "atlas-rails-table-map.v1",
            "models": {"Rooted::Thing": "things", "Zed::Outer::Inner": "zed"},
        }
    ).encode()
    _r, again = built(tmp_path, root="wrapped-m", repo=repo, table_map=mapped)
    assert json.loads(manifest_value(again))["map"]["unmatched"] == 2


def test_the_emitted_map_records_are_capped_and_flag_truncation(tmp_path, monkeypatch):
    repo, full = built(tmp_path, root="full")
    uncapped = facts(full)
    assert len([f for f in uncapped if f["raw"].get("source") == "operator_map"]) == 4
    graph = CodeGraph(
        repo,
        graph_root=tmp_path / "capped",
        frameworks=("rails_models",),
        rails_table_map=MAP_BYTES,
    )
    prime(graph)
    monkeypatch.setattr(rails_models, "MAX_MAP_ENTRIES", 2)  # after the (full) parse
    graph.build()
    found = facts(graph)
    mapped = [f for f in found if f["raw"].get("source") == "operator_map"]
    assert len(mapped) == 2
    summary = json.loads(manifest_value(graph))
    assert summary["truncated"] is True and summary["map"]["entries"] == 7
    assert len(found) == len(uncapped) - 2
    # The validator cap derives from the same constants, so the capped set stays valid.
    assert len(found) <= rails_models.MAX_DECLARATIONS + 1 + rails_models.MAX_MAP_ENTRIES
    with graph.reader_lease() as lease:
        rails_models.validate_records(lease.db)


# ---- supporting: the operator map ----
def table_map(models=None, **extra):
    document = {"format": "atlas-rails-table-map.v1", "models": models or {}}
    document.update(extra)
    return json.dumps(document).encode()


def rejection(raw):
    with pytest.raises(rails_models.TableMapError) as error:
        rails_models.parse_table_map(raw)
    return error.value


def test_a_valid_map_is_parsed_and_identified_by_its_bytes():
    parsed = rails_models.parse_table_map(MAP_BYTES)
    assert parsed.sha256 == MAP_SHA and parsed.models["Blog::Tag"] == "blog_tags"
    assert len(parsed.models) == 7
    assert rails_models.parse_table_map(table_map()).models == {}  # an empty map is allowed
    spaced = MAP_BYTES + b"\n"
    assert rails_models.parse_table_map(spaced).sha256 != MAP_SHA  # the artifact is its bytes


def test_every_map_rejection_has_a_closed_reason_and_fixed_text(monkeypatch):
    secret = "token-4f19c"
    deep = b'{"format":"atlas-rails-table-map.v1","models":{"A":{"b":[[1]]}}}'
    cases = {
        "oversize": (b" " * (rails_models.MAX_MAP_BYTES + 1), "too_large"),
        "bom": (b"\xef\xbb\xbf" + MAP_BYTES, "invalid_json"),
        "depth 5": (deep, "invalid_json"),
        "duplicate top key": (b'{"format":"x","format":"y","models":{}}', "invalid_json"),
        "duplicate model key": (
            b'{"format":"atlas-rails-table-map.v1","models":{"A":"a","A":"b"}}',
            "invalid_json",
        ),
        "nan": (b'{"format":"atlas-rails-table-map.v1","models":{"A":NaN}}', "invalid_json"),
        "infinity": (
            b'{"format":"atlas-rails-table-map.v1","models":{"A":Infinity}}',
            "invalid_json",
        ),
        "not utf8": (
            b'{"format":"atlas-rails-table-map.v1","models":{"A":"\xff"}}',
            "invalid_json",
        ),
        "lone surrogate": (
            b'{"format":"atlas-rails-table-map.v1","models":{"\\ud800":"t"}}',
            "invalid_json",
        ),
        "lone surrogate value": (
            b'{"format":"atlas-rails-table-map.v1","models":{"A":"\\udfff"}}',
            "invalid_json",
        ),
        "truncated json": (b'{"format":', "invalid_json"),
        "huge integer": (
            b'{"format":"atlas-rails-table-map.v1","models":{"A":' + b"9" * 5000 + b"}}",
            "invalid_json",
        ),
        "not an object": (b"[]", "schema"),
        "scalar": (b"3", "schema"),
        "format missing": (b'{"models":{}}', "schema"),
        "wrong format": (
            table_map().replace(b"table-map.v1", b"table-map.v2"),
            "unsupported_format",
        ),
        "format not a string": (b'{"format":1,"models":{}}', "unsupported_format"),
        "extra field": (table_map(extra=1), "schema"),
        "models missing": (b'{"format":"atlas-rails-table-map.v1"}', "schema"),
        "models a list": (b'{"format":"atlas-rails-table-map.v1","models":[]}', "schema"),
        "leading colons": (table_map({"::A": "t"}), "malformed_model"),
        "lowercase key": (table_map({"a": "t"}), "malformed_model"),
        "non ascii key": (table_map({"Café": "t"}), "malformed_model"),
        "long key": (table_map({"A" + "b" * 256: "t"}), "malformed_model"),
        "empty key": (table_map({"": "t"}), "malformed_model"),
        "trailing colons": (table_map({"A::": "t"}), "malformed_model"),
        "bad table": (table_map({"A": "bad-name"}), "malformed_table"),
        "digit table": (table_map({"A": "1table"}), "malformed_table"),
        "numeric table": (
            b'{"format":"atlas-rails-table-map.v1","models":{"A":1}}',
            "malformed_table",
        ),
        "long table": (table_map({"A": "t" * 65}), "malformed_table"),
        "empty table": (table_map({"A": ""}), "malformed_table"),
        "null table": (
            b'{"format":"atlas-rails-table-map.v1","models":{"A":null}}',
            "malformed_table",
        ),
    }
    for name, (raw, reason) in cases.items():
        error = rejection(raw)
        assert error.reason == reason, (name, error.reason)
        assert str(error) == "Invalid Rails table map: " + reason, name
    assert {r for _raw, r in cases.values()} == rails_models.MAP_REASONS - {"too_many_entries"}
    # Too many entries (the limit is monkeypatched to keep the test small).
    monkeypatch.setattr(rails_models, "MAX_MAP_ENTRIES", 2)
    error = rejection(table_map({"A": "a", "B": "b", "C": "c"}))
    assert error.reason == "too_many_entries"
    assert rails_models.parse_table_map(table_map({"A": "a", "B": "b"})).models
    # Document text is never echoed: not even in a value that is rejected.
    leaked = rejection(table_map({"A": secret + "-bad"}))
    assert secret not in str(leaked) and secret not in repr(leaked)
    assert rejection("not bytes").reason == "schema"  # type: ignore[arg-type]


# ---- supporting: CLI ----
def index_args(repo, root, *extra):
    return ["index", "--repo", str(repo), "--graph-root", str(root), *extra]


def test_cli_rails_models_and_table_map(tmp_path):
    from click.testing import CliRunner

    from atlas_aci.__main__ import cli

    runner = CliRunner()
    repo = workspace(tmp_path)
    mapfile = tmp_path / "map.json"
    mapfile.write_bytes(MAP_BYTES)
    root = tmp_path / "cli-root"
    out = runner.invoke(
        cli,
        index_args(repo, root, "--frameworks", "rails_models", "--rails-table-map", str(mapfile)),
    )
    assert out.exit_code == 0, out.output
    graph = CodeGraph(repo, graph_root=root, read_only=True)
    assert len(facts(graph)) == 58
    assert json.loads(manifest_value(graph))["table_map_sha256"] == MAP_SHA
    graph.close()
    tiny = tmp_path / "tiny"
    (tiny / "app/models").mkdir(parents=True)
    (tiny / "app/models/a.rb").write_text("class A < ApplicationRecord\nend\n")
    plain = tmp_path / "plain-root"
    out = runner.invoke(cli, index_args(tiny, plain, "--frameworks", "rails,rails_models"))
    assert out.exit_code == 0, out.output
    graph = CodeGraph(tiny, graph_root=plain, read_only=True)
    assert json.loads(manifest_value(graph))["map"] is None
    assert manifest_value(graph, "rails_routes") is not None
    graph.close()
    # --rails-table-map needs the adapter, in either option order.
    for order in (
        ["--rails-table-map", str(mapfile), "--frameworks", "rails"],
        ["--frameworks", "rails", "--rails-table-map", str(mapfile)],
        ["--rails-table-map", str(mapfile)],
    ):
        reject = tmp_path / ("reject-" + str(len(order)) + order[0].strip("-")[:3] + order[1][-4:])
        out = runner.invoke(cli, index_args(repo, reject, *order))
        assert out.exit_code == 2, (order, out.output)
        assert "requires --frameworks rails_models" in out.output
        assert not reject.exists()
    out = runner.invoke(
        cli,
        index_args(
            repo,
            tmp_path / "py",
            "--frameworks",
            "rails_models",
            "--langs",
            "python",
        ),
    )
    assert out.exit_code == 2 and "requires language ruby" in out.output
    assert not (tmp_path / "py").exists()


def test_cli_rejects_bad_map_files_with_fixed_text_and_no_graph(tmp_path):
    from click.testing import CliRunner

    from atlas_aci.__main__ import cli

    runner = CliRunner()
    repo = workspace(tmp_path)
    secret = "secret-document-text"
    bad = tmp_path / "private-name-xyz.json"
    bad.write_text('{"format":"' + secret + '"}')
    oversize = tmp_path / "big.json"
    oversize.write_bytes(b" " * (rails_models.MAX_MAP_BYTES + 5))
    fifo = tmp_path / "fifo-map"
    os.mkfifo(fifo)
    cases = {
        "invalid": (bad, "unsupported_format"),
        "oversize": (oversize, "too_large"),
        "missing": (tmp_path / "missing-map.json", "Cannot read the Rails table map"),
        "directory": (tmp_path, "Cannot read the Rails table map"),
        "fifo": (fifo, "is not a regular file"),
    }
    for name, (path, text) in cases.items():
        root = tmp_path / ("root-" + name)
        out = runner.invoke(
            cli,
            index_args(repo, root, "--frameworks", "rails_models", "--rails-table-map", str(path)),
        )
        assert out.exit_code == 2, (name, out.output)
        assert text in out.output, (name, out.output)
        assert secret not in out.output and path.name not in out.output, name
        assert not root.exists(), name
    with pytest.raises(ValueError, match="requires framework adapter rails_models"):
        CodeGraph(repo, graph_root=tmp_path / "ctor", rails_table_map=MAP_BYTES)
    with pytest.raises(ValueError, match="requires framework adapter rails_models"):
        CodeGraph(
            repo, graph_root=tmp_path / "ctor", frameworks=("rails",), rails_table_map=MAP_BYTES
        )
    with pytest.raises(rails_models.TableMapError):
        CodeGraph(
            repo,
            graph_root=tmp_path / "ctor",
            frameworks=("rails_models",),
            rails_table_map=b"{}",
        )
    assert not (tmp_path / "ctor").exists()
