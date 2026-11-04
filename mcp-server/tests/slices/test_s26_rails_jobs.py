"""ACI-S26 acceptance criteria: direct Rails job enqueues and positional argument paths.

Every graph is a real build of a tmp copy of ``tests/fixtures/rails_jobs/project`` with an
external graph root. Expected entity ids come from independent SQL on ``symbols`` and ``refs``.
Nothing the adapter does is mocked: the spies in AC-007 only observe (and refuse) process,
network and repository-database activity.
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
import sys
from pathlib import Path

import pytest
from test_s26_support import (
    DAILY,
    ENQUEUER,
    NIGHTLY,
    OUTER,
    PROJECT,
    PROVENANCE,
    REPORT_JOB,
    SENTINELS,
    SPEC,
    arguments_of,
    built,
    edit,
    enqueues,
    facts,
    file_id,
    inventory,
    job_lines,
    job_records,
    manifest_value,
    method_id,
    one,
    perform_ids,
    planted,
    rebuilt,
    symbol_rows,
    workspace,
)

from atlas_aci.codegraph import KNOWN_EDGE_RELATIONS
from atlas_aci.frameworks import rails_jobs
from atlas_aci.storage import evidence as ev

SRC = Path(rails_jobs.__file__).parent
E1 = "HardWorker.perform_async(user.id)"
E2 = 'HardWorker.perform_async(user.id, "fast")'
E3 = "ReportJob.perform_later(report, :pdf)"
E4 = "::ReportJob.perform_later report, :csv"
B2 = "ReportJob.perform_later(1, 2)"


@pytest.fixture(scope="module")
def shared(tmp_path_factory):
    """One real ON build, shared by the read-only AC tests."""
    tmp = tmp_path_factory.mktemp("s26")
    repo, graph = built(tmp)
    return repo, graph, facts(graph, repo)


def target_of(graph, entity_id):
    with graph.reader_lease() as lease:
        return ev.get(lease.db, "entity", entity_id)


def definition_text(graph, repo, entity_id):
    with graph.reader_lease() as lease:
        row = lease.db.execute(
            "SELECT path, occurrence_id FROM symbols WHERE entity_id=?", (entity_id,)
        ).fetchone()
        occurrence = ev.get(lease.db, "occurrence", row[1])
    return (repo / row[0]).read_bytes()[occurrence["start_byte"] : occurrence["end_byte"]]


# ---- AC-S26-001 ----
def test_rails_direct_enqueue_target(shared):
    repo, graph, found = shared
    run = method_id(graph, ENQUEUER, "run")
    expected = [
        (ENQUEUER, E1, "HardWorker", run),
        (ENQUEUER, E2, "HardWorker", run),
        (ENQUEUER, E3, "ReportJob", run),
        (ENQUEUER, E4, "ReportJob", run),
        (ENQUEUER, "PingWorker.perform_async", "PingWorker", run),
        (ENQUEUER, "PingWorker.perform_async()", "PingWorker", run),
        (ENQUEUER, "Billing::SyncJob.perform_later(user)", "Billing::SyncJob", run),
        (NIGHTLY, B2, "ReportJob", method_id(graph, NIGHTLY, "run")),
        (DAILY, B2, "ReportJob", method_id(graph, DAILY, "go")),
        (SPEC, B2, "ReportJob", file_id(SPEC)),
    ]
    assert len(expected) == 10
    for path, text, namespace, source in expected:
        site = one(found, path, text)
        assertion, occurrence = site["assertion"], site["occurrence"]
        performs = perform_ids(graph, namespace)
        assert len(performs) == 1, namespace
        assert assertion["relation"] == "async_enqueue"
        assert assertion["resolution"] == "INFERRED" and assertion["unresolved_reason"] is None
        assert assertion["target_entity_id"] == performs[0] and assertion["candidate_ids"] == []
        assert assertion["source_entity_id"] == source == occurrence["entity_id"]
        assert occurrence["kind"] == "rails_job" and occurrence["language"] == "ruby"
        assert occurrence["role"].startswith("rails_job:enqueue:perform_")
        assert site["text"] == text  # the occurrence bytes are the call text
        entity = target_of(graph, performs[0])
        assert entity["kind"] == "method" and entity["name"] == "perform"
        assert definition_text(graph, repo, performs[0]).startswith(b"def perform")
    first = one(found, ENQUEUER, E1)
    assert first["assertion"]["raw_target"] == (
        '{"arguments":"mapped","arity":1,"kind":"perform_async",'
        '"mapping":"rails_job_receiver.v1","mechanism":"sidekiq","receiver":"HardWorker"}'
    )
    assert first["raw"] == json.loads(first["assertion"]["raw_target"])
    # The two mechanisms are paired with their receiver kind.
    assert one(found, ENQUEUER, E3)["raw"]["mechanism"] == "active_job"
    assert one(found, ENQUEUER, E4)["raw"]["receiver"] == "::ReportJob"
    # One rails_jobs producer owns all the evidence; its identity is the native producer's.
    producers = {f["evidence"]["producer_id"] for f in found}
    assert len(producers) == 1
    with graph.reader_lease() as lease:
        producer = ev.get(lease.db, "producer", next(iter(producers)))
    assert set(producer["fingerprint"]) == {"rails_jobs"}
    assert (
        producer["fingerprint"]["rails_jobs"]["graph_producer_id"]
        == (graph._capture_producer["id"])
    )
    # The unresolved reasons of the receivers that must not link.
    reasons = {
        "GhostJob.perform_later(1)": "job_class_not_found",
        "NoPerformJob.perform_later(1)": "perform_not_found",
        "PlainTask.perform_async(1)": "target_not_recognized_job",
        "HardWorker.perform_later(1)": "target_not_recognized_job",
        "ReportJob.perform_async(1, 2)": "target_not_recognized_job",
        "InheritedJob.perform_later(1, 2)": "target_not_recognized_job",
    }
    for text, reason in reasons.items():
        item = one(found, ENQUEUER, text)["assertion"]
        assert item["resolution"] == "UNRESOLVED" and item["unresolved_reason"] == reason
        assert item["target_entity_id"] is None and item["candidate_ids"] == []
    # An INFERRED enqueue relabelled EXTRACTED is not a valid record.
    good = first["assertion"]
    relabelled = ev.assertion(
        good["source_entity_id"],
        good["target_entity_id"],
        "async_enqueue",
        good["evidence_id"],
        "EXTRACTED",
        good["raw_target"],
    )
    with pytest.raises(ValueError, match="async_enqueue assertion"):
        ev.validate_record(relabelled)
    # Namespaces are lexical only: `Inner::Thing` has a compact name inside `Outer`, and the
    # rooted `::Rooted` is not a lexical scope, so neither site is bound.
    for text_path in (OUTER,):
        diag = [f for f in enqueues(found, text_path) if f["raw"].get("construct")]
        assert [f["raw"]["construct"] for f in diag] == ["unbound_namespace"] * 2


# ---- AC-S26-002 ----
def test_rails_positional_argument_mapping(shared):
    _repo, graph, found = shared
    expected = {
        (ENQUEUER, E1): [("user.id", "user_id")],
        (ENQUEUER, E2): [("user.id", "user_id"), ('"fast"', "mode")],
        (ENQUEUER, E3): [("report", "report"), (":pdf", "format")],
        (ENQUEUER, E4): [("report", "report"), (":csv", "format")],
        (ENQUEUER, "Billing::SyncJob.perform_later(user)"): [("user", "account")],
        (NIGHTLY, B2): [("1", "report"), ("2", "format")],
        (SPEC, B2): [("1", "report"), ("2", "format")],
    }
    seen = 0
    for (path, text), mapping in expected.items():
        enqueue = one(found, path, text)
        arguments = arguments_of(found, enqueue)
        assert len(arguments) == len(mapping) == enqueue["raw"]["arity"]
        assert enqueue["raw"]["arguments"] == "mapped"
        data = (_repo / path).read_bytes()
        for ordinal, (item, (source, name)) in enumerate(zip(arguments, mapping, strict=True)):
            occurrence, assertion = item["occurrence"], item["assertion"]
            assert item["raw"] == {
                "arg": ordinal,
                "enqueue": enqueue["occurrence"]["id"],
                "mapping": "rails_job_positional.v1",
                "param": ordinal,
                "param_name": name,
            }
            assert occurrence["role"] == "rails_job:argument:mapped"
            assert data[occurrence["start_byte"] : occurrence["end_byte"]].decode() == source
            assert assertion["relation"] == "async_argument"
            assert assertion["resolution"] == "INFERRED" and assertion["unresolved_reason"] is None
            assert assertion["target_entity_id"] == enqueue["assertion"]["target_entity_id"]
            assert assertion["source_entity_id"] == enqueue["assertion"]["source_entity_id"]
            assert assertion["source_entity_id"] == occurrence["entity_id"]
            assert enqueue["occurrence"]["start_byte"] <= occurrence["start_byte"]
            assert occurrence["end_byte"] <= enqueue["occurrence"]["end_byte"]
            seen += 1
    assert seen == 12
    for text in ("PingWorker.perform_async", "PingWorker.perform_async()"):
        enqueue = one(found, ENQUEUER, text)
        assert enqueue["raw"]["arguments"] == "none" and enqueue["raw"]["arity"] == 0
        assert arguments_of(found, enqueue) == []
    # Values are never copied: no raw_target holds an argument expression.
    blob = " ".join(f["assertion"]["raw_target"] for f in found)
    for text in ("user.id", "fast", "pdf", "csv"):
        assert text not in blob
    # Every mapped record is independently checked against the perform parameters in `symbols`.
    parameters = {
        "HardWorker": ["user_id", "mode"],
        "ReportJob": ["report", "format"],
        "Billing::SyncJob": ["account"],
    }
    mapped = [f for f in found if f["occurrence"]["role"] == "rails_job:argument:mapped"]
    assert len(mapped) == 16 == PROVENANCE["expected"]["facts"]["async_argument"]["INFERRED"]
    by_target = {perform_ids(graph, name)[0]: names for name, names in parameters.items()}
    for item in mapped:
        names = by_target.get(item["assertion"]["target_entity_id"])
        if names is not None:
            assert item["raw"]["param_name"] == names[item["raw"]["param"]]


# ---- AC-S26-003 ----
def test_rails_enqueue_relation_is_not_synchronous(shared):
    repo, graph, found = shared
    chosen = enqueues(found)
    assert chosen and all(f["occurrence"]["role"].startswith("rails_job:enqueue:") for f in chosen)
    assert all(f["assertion"]["relation"] == "async_enqueue" for f in chosen)
    assert not any(f["assertion"]["relation"] == "call" for f in found)
    for relation in ("async_enqueue", "async_argument"):
        for relations in (
            ev.RELATIONS,
            ev.REFERENCE_RELATIONS,
            ev.EDGE_RELATIONS,
            KNOWN_EDGE_RELATIONS,
        ):
            assert relation not in relations
        assert relation in ev.ASSERTION_RELATIONS
    with graph.reader_lease() as lease:
        db = lease.db
        assert (
            db.execute(
                "SELECT COUNT(*) FROM edges WHERE relation IN ('async_enqueue','async_argument')"
            ).fetchone()[0]
            == 0
        )
        assert db.execute("SELECT COUNT(*) FROM edges").fetchone()[0] > 0
        performs = {
            row[0]
            for row in db.execute(
                "SELECT entity_id FROM symbols WHERE lang='ruby' AND kind='method' "
                "AND name='perform'"
            )
        }
        spans = {}
        for item in chosen:
            spans.setdefault(item["occurrence"]["path"], []).append(
                (item["occurrence"]["start_byte"], item["occurrence"]["end_byte"])
            )
        # No `call` assertion targeting a perform has its occurrence inside an enqueue span.
        calls = 0
        for (payload,) in db.execute(
            "SELECT payload FROM assertions WHERE json_extract(payload,'$.relation')='call'"
        ):
            assertion = json.loads(payload)
            if assertion["target_entity_id"] not in performs:
                continue
            calls += 1
            proof = ev.get(db, "evidence", assertion["evidence_id"])
            occurrence = ev.get(db, "occurrence", proof["occurrence_id"])
            for start, end in spans.get(occurrence["path"], []):
                assert not (start <= occurrence["start_byte"] and occurrence["end_byte"] <= end)
        assert calls >= 0
    # perform_now / .new.perform / perform_inline / perform_sync / delay / perform_bulk, a comment
    # and a string literal carry no rails_job record.
    source = (repo / ENQUEUER).read_bytes()
    silent = [
        "ReportJob.perform_now(report, :pdf)",
        "HardWorker.new.perform(1)",
        "HardWorker.perform_inline(1)",
        "HardWorker.perform_sync(1)",
        "HardWorker.delay.ping",
        "HardWorker.perform_bulk([[1]])",
        "# HardWorker.perform_async(1)",
        '"ReportJob.perform_later"',
    ]
    for text in silent:
        start = source.index(text.encode())
        end = start + len(text)
        assert not [
            f
            for f in found
            if f["occurrence"]["path"] == ENQUEUER
            and f["occurrence"]["start_byte"] < end
            and start < f["occurrence"]["end_byte"]
        ], text
    # A resolved async_enqueue cannot be EXTRACTED, and a single tampered relation is refused.
    good = one(found, ENQUEUER, E1)["assertion"]
    for resolution in ("EXTRACTED",):
        with pytest.raises(ValueError, match="async_enqueue assertion"):
            ev.validate_record(
                ev.assertion(
                    good["source_entity_id"],
                    good["target_entity_id"],
                    "async_enqueue",
                    good["evidence_id"],
                    resolution,
                    good["raw_target"],
                )
            )


# ---- AC-S26-004 ----
ENQUEUE_DIAGNOSTICS = {
    "job_class.perform_later(1)": ("dynamic_receiver", "perform_later"),
    "self.class.perform_async(1)": ("dynamic_receiver", "perform_async"),
    '"ReportJob".constantize.perform_later(1, 2)': ("dynamic_receiver", "perform_later"),
    "perform_async(1)": ("dynamic_receiver", "perform_async"),
    "ReportJob.set(wait: 300).perform_later(report, :pdf)": ("set_chain", "perform_later"),
    "HardWorker.perform_in(300, user.id)": ("delayed_enqueue", "perform_in"),
    "HardWorker.perform_at(t, user.id)": ("delayed_enqueue", "perform_at"),
}
ARGUMENT_DIAGNOSTICS = {
    "HardWorker.perform_async(*ids)": ("splat", "*ids"),
    "ReportJob.perform_later(**opts)": ("hash_splat", "**opts"),
    "ReportJob.perform_later(report, format: :pdf)": ("keyword_arguments", "format: :pdf"),
    "HardWorker.perform_async(user.id, &blk)": ("block_argument", "&blk"),
    "HardWorker.perform_async(user.id) { |x| x }": ("block", "{ |x| x }"),
    "ReportJob.perform_later(1, 2, 3)": ("arity_mismatch", "(1, 2, 3)"),
    "HardWorker.perform_async": ("arity_mismatch", "HardWorker.perform_async"),
    "SplatWorker.perform_async(1)": ("unsupported_signature", "(1)"),
    "OrderedWorker.perform_async(1, 2)": ("unsupported_signature", "(1, 2)"),
    "StrictKwWorker.perform_async(1)": ("unsupported_signature", "(1)"),
    "ExportJob.perform_later(*ids)": ("splat", "*ids"),
    "HardWorker.perform_async(...)": ("forwarded_arguments", "..."),
}


def test_rails_transformed_arguments_are_unresolved(shared):
    repo, _graph, found = shared
    reached = set()
    for text, (construct, kind) in ENQUEUE_DIAGNOSTICS.items():
        site = one(found, ENQUEUER, text)
        assertion = site["assertion"]
        assert site["occurrence"]["role"] == "rails_job:enqueue:unsupported:" + construct
        assert site["raw"] == {"construct": construct, "kind": kind, "mapping": "unexpanded"}
        assert assertion["relation"] == "async_enqueue" and assertion["resolution"] == "UNRESOLVED"
        assert assertion["unresolved_reason"] == "unsupported_argument_mapping"
        assert assertion["target_entity_id"] is None and assertion["candidate_ids"] == []
        assert arguments_of(found, site) == []
        reached.add(construct)
    rooted = [f for f in enqueues(found, OUTER) if f["raw"].get("construct")]
    assert len(rooted) == 2
    for site in rooted:
        assert site["raw"]["construct"] == "unbound_namespace"
        assert site["assertion"]["unresolved_reason"] == "unsupported_argument_mapping"
        assert arguments_of(found, site) == []
    reached.add("unbound_namespace")
    for text, (construct, span_text) in ARGUMENT_DIAGNOSTICS.items():
        path = ENQUEUER
        site = one(found, path, text)
        assertion = site["assertion"]
        assert site["occurrence"]["role"].startswith("rails_job:enqueue:perform_")
        assert site["raw"]["arguments"] == "unsupported" and site["raw"]["construct"] == construct
        # The enqueue keeps its resolution; only the diagnostic carries the reason.
        assert assertion["resolution"] in {"INFERRED", "AMBIGUOUS"}
        assert assertion["resolution"] == ("AMBIGUOUS" if "Export" in text else "INFERRED")
        assert (assertion["unresolved_reason"] is None) == (assertion["resolution"] == "INFERRED")
        children = arguments_of(found, site)
        assert len(children) == 1  # exactly one diagnostic, never a partial mapping
        child = children[0]
        assert child["occurrence"]["role"] == "rails_job:argument:unsupported:" + construct
        assert child["raw"] == {
            "construct": construct,
            "enqueue": site["occurrence"]["id"],
            "mapping": "unexpanded",
        }
        assert child["assertion"]["relation"] == "async_argument"
        assert child["assertion"]["resolution"] == "UNRESOLVED"
        assert child["assertion"]["unresolved_reason"] == "unsupported_argument_mapping"
        assert child["assertion"]["target_entity_id"] is None
        assert child["assertion"]["candidate_ids"] == []
        assert child["assertion"]["source_entity_id"] == assertion["source_entity_id"]
        data = (repo / path).read_bytes()
        shown = data[child["occurrence"]["start_byte"] : child["occurrence"]["end_byte"]]
        assert shown.decode() == span_text
        assert site["occurrence"]["start_byte"] <= child["occurrence"]["start_byte"]
        assert child["occurrence"]["end_byte"] <= site["occurrence"]["end_byte"]
        reached.add(construct)
    # Every construct of the closed sets (but the two cap codes, covered by the cap tests) is
    # reached by the fixture.
    assert reached == (
        (rails_jobs.ENQUEUE_CONSTRUCTS - {"site_limit"})
        | (rails_jobs.ARGUMENT_CONSTRUCTS - {"argument_limit"})
    )
    mapped = [f for f in found if f["occurrence"]["role"] == "rails_job:argument:mapped"]
    unsupported = [f for f in enqueues(found) if f["raw"].get("arguments") == "unsupported"]
    assert len(unsupported) == len(ARGUMENT_DIAGNOSTICS)
    for site in unsupported:
        assert not [m for m in mapped if m["raw"]["enqueue"] == site["occurrence"]["id"]]


# ---- AC-S26-005 ----
def test_rails_job_collision_is_unresolved(shared, tmp_path):
    _repo, graph, found = shared
    exports = sorted(
        perform_ids(graph, "ExportJob", "app/jobs/export_job.rb")
        + perform_ids(graph, "ExportJob", "lib/legacy/export_job.rb")
    )
    assert len(exports) == 2
    syncs = sorted(perform_ids(graph, "Billing::SyncJob") + perform_ids(graph, "SyncJob"))
    assert len(syncs) == 2
    for path, text, candidates in (
        (ENQUEUER, "ExportJob.perform_later(1)", exports),
        (NIGHTLY, "SyncJob.perform_later(1)", syncs),
    ):
        site = one(found, path, text)
        assertion = site["assertion"]
        assert assertion["resolution"] == "AMBIGUOUS"
        assert assertion["unresolved_reason"] == "ambiguous_job_target"
        assert assertion["candidate_ids"] == candidates and assertion["target_entity_id"] is None
        assert site["raw"]["arguments"] == "unbound"
        assert arguments_of(found, site) == []
    # The candidate set is kept when the call also has an unsupported form (splat).
    splat = one(found, ENQUEUER, "ExportJob.perform_later(*ids)")
    assert splat["assertion"]["candidate_ids"] == exports
    assert [c["raw"]["construct"] for c in arguments_of(found, splat)] == ["splat"]
    # A record claiming one candidate, or a target, is not a valid AMBIGUOUS record.
    good = one(found, ENQUEUER, "ExportJob.perform_later(1)")["assertion"]
    conn = planted(
        graph,
        tmp_path,
        "one-candidate",
        lambda p: p.update(candidate_ids=p["candidate_ids"][:1]),
        select="json_extract(payload,'$.resolution')='AMBIGUOUS'",
    )
    with pytest.raises(ValueError, match="ambiguous enqueue"):
        rails_jobs.validate_records(conn)
    with pytest.raises(ValueError, match="unresolved semantics"):
        ev.validate_record(
            ev.assertion(
                good["source_entity_id"],
                exports[0],
                "async_enqueue",
                good["evidence_id"],
                "AMBIGUOUS",
                good["raw_target"],
                exports,
                "ambiguous_job_target",
            )
        )


# ---- AC-S26-006 ----
def test_rails_job_reindex_is_idempotent(tmp_path):
    repo, first = built(tmp_path, root="one")
    lines = job_lines(first)
    assert len(lines) == 3 * PROVENANCE["expected"]["records"]["assertion"] + 1
    manifest = manifest_value(first)
    second_graph, _stats = rebuilt(repo, tmp_path, "two")
    assert job_lines(second_graph) == lines and manifest_value(second_graph) == manifest
    again, stats = rebuilt(repo, tmp_path, "one", since="x")
    assert stats["binding_resolution"]["affected_units"] == 0
    assert job_lines(again) == lines and manifest_value(again) == manifest
    summary = json.loads(manifest)
    producers = [r for r in job_records(first) if r["type"] == "producer"]
    assert len(producers) == 1
    fingerprint = producers[0]["fingerprint"]["rails_jobs"]
    assert (
        fingerprint["adapter_sha256"]
        == hashlib.sha256((SRC / "rails_jobs.py").read_bytes()).hexdigest()
    )
    assert fingerprint["adapter_sha256"] == summary["adapter_sha256"]
    assert fingerprint["graph_producer_id"] == first._capture_producer["id"]
    assert summary["producer_id"] == producers[0]["id"]
    for key in ("files", "classes", "performs", "sites", "facts", "truncated"):
        assert summary[key] == PROVENANCE["expected"][key], key  # the pinned totals hold
    # Non-vacuous controls: an argument edit and a signature edit each change the fact set.
    lead = "    HardWorker.perform_async(user.id)\n    HardWorker.perform_async(user.id, "
    edit(repo, ENQUEUER, lead, lead.replace("(user.id)", "(user.name)"))
    changed, _stats = rebuilt(repo, tmp_path, "one", since="x")
    assert job_lines(changed) != lines
    edit(repo, ENQUEUER, lead.replace("(user.id)", "(user.name)"), lead)
    restored, _stats = rebuilt(repo, tmp_path, "one", since="x")
    assert job_lines(restored) == lines
    edit(repo, REPORT_JOB, "def perform(report, format)", "def perform(report, format, extra)")
    widened, _stats = rebuilt(repo, tmp_path, "one", since="x")
    found = facts(widened, repo)
    e3 = one(found, ENQUEUER, E3)
    assert e3["raw"]["arguments"] == "unsupported" and e3["raw"]["construct"] == "arity_mismatch"
    assert job_lines(widened) != lines


# ---- AC-S26-007 ----
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
    "redis",
    "sidekiq",
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
    assert reads == 3  # one Path(__file__).read_bytes() per adapter, for adapter_sha256
    jobs = ast.parse((SRC / "rails_jobs.py").read_text())
    imported = {
        n.module if isinstance(n, ast.ImportFrom) else a.name
        for n in ast.walk(jobs)
        if isinstance(n, (ast.Import, ast.ImportFrom))
        for a in (n.names if isinstance(n, ast.Import) else [n])
    }
    assert not any(m and m.split(".")[0] in {"redis", "sidekiq"} for m in imported)


def install_spies(monkeypatch, launches, connections, tmp_path):
    def popen(argv, *args, **kwargs):
        launches.append(list(argv) if not isinstance(argv, str) else [argv])
        raise FileNotFoundError("process launch refused by the S26 spy")

    monkeypatch.setattr(subprocess, "Popen", popen)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("the Rails jobs adapter must never launch a process or connect")

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


def test_rails_jobs_never_enqueue(tmp_path, monkeypatch):
    assert "redis" not in sys.modules and "sidekiq" not in sys.modules
    repo = workspace(tmp_path)
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
    found = facts(on)
    inferred = [
        f for f in found if f["area"] == "enqueue" and f["assertion"]["resolution"] == "INFERRED"
    ]
    assert len(inferred) >= 8
    assert on_launches, "the pre-existing snapshot identity probe is the only launch"
    for argv in on_launches:
        assert argv[:2] == ["git", "-C"] and "rev-parse" in argv
        for token in [*argv[:2], *argv[3:]]:  # argv[2] is the tmp repo path
            assert not re.search(r"ruby|rails|rake|bundle|sidekiq|redis|\.rb", token), argv
    assert on_connections, "the graph itself opens SQLite databases"
    for target in on_connections:
        assert target.is_relative_to(graph_root.resolve()) or target.is_relative_to(tmp_path)
        assert not target.is_relative_to(repo.resolve()), target
    _r, off = built(tmp_path, root="off", repo=repo, frameworks=(), warm=False)
    assert [list(a) for a in launches] == on_launches
    assert job_records(off) == []
    launches.clear()
    # The fixture workers would write a sentinel file if any of their code ever ran.
    for name in SENTINELS:
        assert not (repo / name).exists() and not (Path.cwd() / name).exists()
        assert not (tmp_path / name).exists()
    assert inventory(repo) == before
    assert "redis" not in sys.modules and "sidekiq" not in sys.modules
    # A pure scan plus refresh over a staging copy launches nothing, under an all-raising spy.
    import tree_sitter_language_pack as pack

    conn = planted(on, tmp_path, "staging", lambda p: None, select="1=1")
    observations = json.loads(
        conn.execute("SELECT value FROM manifest WHERE key='source_observations'").fetchone()[0]
    )
    native = next(
        json.loads(p)["id"]
        for (p,) in conn.execute("SELECT payload FROM producers")
        if "codegraph_sha256" in (json.loads(p)["fingerprint"] or {})
    )
    capture = rails_jobs.Capture()
    for path in sorted(p.relative_to(repo).as_posix() for p in repo.rglob("*.rb")):
        if path.startswith("tmp/"):
            continue  # a skip pattern: the build never observed it
        data = (repo / path).read_bytes()
        capture.offer(path, data, hashlib.sha256(data).hexdigest())
    assert len(capture.files) == PROVENANCE["expected"]["files"]["captured"]
    summary = rails_jobs.refresh(
        conn,
        enabled=True,
        capture=capture,
        observations=observations,
        get_parser=pack.get_parser,
        graph_producer_id=native,
    )
    assert summary is not None and summary["files"]["failed"] == 0 and launches == []
    assert summary["facts"]["async_enqueue"]["INFERRED"] == 23
    for name in SENTINELS:
        assert not (Path.cwd() / name).exists() and not (tmp_path / name).exists()
    for target in connections:
        assert not target.is_relative_to(repo.resolve()), target
    assert (PROJECT / "app/workers/sentinel_worker.rb").exists()
    # The independent expectations really come from `symbols`: the sentinel workers are indexed.
    assert any(row[2] == "perform" for row in symbol_rows(on, "app/workers/sentinel_worker.rb"))
