"""ACI-S35 frozen criteria: verified outcome events and a separate, optional ranking overlay.

Real indexer builds, real MCP records (`call_tool_result`), real pytest-layout JUnit artifacts,
the real CLI. Every expectation is recomputed here independently of the package. The tests that
reach the baseline are plain functions: the baseline runs `asyncio.run` inside.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import ExitStack
from types import SimpleNamespace

import pytest
from test_s35_support import (
    BASE_TOOLS_BYTES,
    BASE_TOOLS_SHA256,
    NODE_A,
    NODE_ABSENT,
    NODE_B,
    NODE_C,
    NODE_D,
    NODE_E,
    POLICY_ID,
    POLICY_NAME,
    PRODUCER,
    World,
    annotation,
    assert_rejected,
    assert_untruncated,
    assert_usage,
    build_world,
    bundle_ids,
    bundle_sources,
    claim,
    cli_run,
    dumps,
    envelope,
    expected_order,
    fixture,
    import_args,
    import_ok,
    mcp_result,
    observations,
    produce_record,
    rank_args,
    request_arguments,
    run_identity,
    run_import,
    run_rank,
    serial,
    sha,
    stat_inventory,
    store_of,
    temp_leftovers,
)

from atlas_aci.enforcement import READ_ONLY_TOOLS
from atlas_aci.feedback import outcomes, ranking
from atlas_aci.overlays import runtime
from atlas_aci.query.context import context
from atlas_aci.storage import evidence as ev

ARTIFACT = fixture("junit-outcomes.xml")
SECOND = fixture("junit-second.xml")
REJECT = "Feedback import rejected: "


@pytest.fixture(scope="module")
def world(tmp_path_factory) -> World:
    return build_world(tmp_path_factory.mktemp("s35"))


@pytest.fixture(scope="module")
def r1(world):
    """The record of "process_payment" on root-before: six bundles, never truncated."""
    raw, record = produce_record(world, "before")
    assert_untruncated(record)
    assert len(bundle_ids(record)) >= 3
    return raw, record


def library_context(world: World, name: str, arguments: dict) -> dict:
    """The baseline through `context()` itself, with an independent duck-typed config."""
    settings = SimpleNamespace(
        max_response_bytes=1024 * 1024, max_bound_field_elements=200, context_tokenizer=None
    )
    with ExitStack() as stack:
        reader = runtime.open_active(stack, world.side(name))
        return asyncio.run(context(reader, dict(arguments), settings, structured=True))


def good_claims(record):
    last = len(bundle_ids(record)) - 1
    return [
        claim(record, "claim-a", NODE_A, "passed", [last]),
        claim(record, "claim-b", NODE_B, "failed", [1]),
    ]


@pytest.fixture(scope="module")
def good_store(world, r1):
    """The store both the ranking tests and the idempotence test start from."""
    raw, record = r1
    document = envelope(
        record, good_claims(record), [annotation(record, "note-1", "useful", [2], "fine")],
        artifact=ARTIFACT,
    )  # fmt: skip
    return import_ok(world, "before", document, raw, ARTIFACT)


# ---- AC-S35-001 --------------------------------------------------------------------------------


def test_feedback_verified_event_requires_artifact(world, r1):
    raw, record = r1
    ids = bundle_ids(record)
    last = len(ids) - 1
    assert last >= 2
    claims = good_claims(record)
    document = envelope(record, claims, artifact=ARTIFACT)
    result, out = run_import(world, "before", document, raw, ARTIFACT)
    assert result.exit_code == 0, result.stderr
    store = store_of(out)
    assert len(store["events"]) == 2 and store["annotations"] == []
    snapshot = {
        "snapshot_id": world.snapshot("before"),
        "generation_id": world.generation("before"),
    }
    seen = observations(world.side("before"))
    pairs = {
        "passed": ("tests.test_payments", "test_process_payment_ok"),
        "failed": ("tests.test_payments.TestCards", "test_declined"),
    }
    for event in store["events"]:
        assert event["type"] == "feedback_event" and event["status"] == "verified_artifact"
        assert event["retrieval"]["record_id"] == record["data"]["retrieval_record_id"]
        assert event["retrieval"]["record_bundle_ids"] == ids
        assert event["retrieval"]["ranker"] == record["data"]["ranker"]
        assert event["run"]["run_id"] == run_identity(ARTIFACT, PRODUCER, tuple(snapshot.values()))
        assert event["run"]["producer"] == PRODUCER
        assert event["snapshot"] == snapshot
        assert event["artifact"] == {"sha256": sha(ARTIFACT), "bytes": len(ARTIFACT)}
        outcome = event["claim"]["outcome"]
        assert event["verification"]["derived_outcome"] == outcome
        assert event["verification"]["attestation"] == "operator_declared"
        assert event["verification"]["profile"] == "atlas-junit-xml-profile.v1"
        testcase = event["verification"]["testcase"]
        assert (testcase["classname"], testcase["name"]) == pairs[outcome]
        for item in event["results"]:
            assert item["position"] == ids.index(item["bundle_id"])
            expected = bundle_sources(record, item["bundle_id"])
            assert [[s["path"], s["source_sha256"]] for s in item["sources"]] == expected
            assert all(seen[path] == digest for path, digest in expected)
    by_outcome = {e["claim"]["outcome"]: e for e in store["events"]}
    assert [r["bundle_id"] for r in by_outcome["passed"]["results"]] == [ids[last]]
    assert [r["bundle_id"] for r in by_outcome["failed"]["results"]] == [ids[1]]
    outcomes.validate_store(store)
    # The CLI bytes equal the library bytes; stdout is the canonical summary.
    library_store, summary = outcomes.import_feedback(
        dumps(document), raw, ARTIFACT, None, world.side("before"), outcomes.normalize_limits()
    )
    assert out.read_bytes() == outcomes.canonical_bytes(library_store)
    assert result.stdout_bytes == outcomes.canonical_bytes(summary)
    assert json.loads(result.stdout_bytes)["counts"]["events"] == 2
    # Without the artifact the same envelope lacks its provenance.
    missing, never = run_import(world, "before", document, raw, None)
    assert_rejected(missing, REJECT + "unverifiable_outcome: missing_provenance", never)
    # An annotation-only envelope yields only an unverified annotation and no event.
    notes = envelope(record, [], [annotation(record, "only-note", "dead_end", [0], None)])
    only, out_only = run_import(world, "before", notes, raw, None)
    assert only.exit_code == 0, only.stderr
    kept = store_of(out_only)
    assert kept["events"] == [] and len(kept["annotations"]) == 1
    assert kept["annotations"][0]["status"] == "unverified_annotation"
    assert kept["annotations"][0]["verification"] == "none"


# ---- AC-S35-002 --------------------------------------------------------------------------------


def flip(data: bytes, index: int = 40) -> bytes:
    return data[:index] + bytes([data[index] ^ 1]) + data[index + 1 :]


def forged_cases(world, r1, r3_bytes):
    """(label, document or bytes, record bytes, artifact bytes, exact text after `rejected: `)."""
    raw, record = r1
    last = len(bundle_ids(record)) - 1
    good = envelope(record, good_claims(record), artifact=ARTIFACT)
    forged = json.loads(raw)
    forged["data"]["bundles"][0]["records"]["entity"]["name"] = "process_paymenT"
    tampered = json.dumps(forged).encode()

    def edited(change, **kwargs):
        document = json.loads(json.dumps(good))
        change(document)
        return document

    def one(nodeid, outcome, picks=(last,), artifact=ARTIFACT):
        return envelope(
            record, [claim(record, "c", nodeid, outcome, list(picks))], artifact=artifact
        )

    cases = [
        ("b claimed passed", one(NODE_B, "passed"), raw, ARTIFACT, "claim_disagrees_with_artifact"),
        ("c skipped", one(NODE_C, "passed"), raw, ARTIFACT, "outcome_not_objective"),
        ("d error", one(NODE_D, "failed"), raw, ARTIFACT, "outcome_not_objective"),
        ("e duplicated", one(NODE_E, "passed"), raw, ARTIFACT, "outcome_not_objective"),
        ("absent selector", one(NODE_ABSENT, "passed"), raw, ARTIFACT, "test_not_in_artifact"),
        ("flipped byte", good, raw, flip(ARTIFACT), "artifact_digest_mismatch"),
        (
            "bytes off by one",
            edited(lambda d: d["artifact"].update(bytes=d["artifact"]["bytes"] + 1)),
            raw,
            ARTIFACT,
            "artifact_size_mismatch",
        ),
        (
            "wrong run id",
            edited(lambda d: d["run"].update(run_id="0" * 64)),
            raw,
            ARTIFACT,
            "run_identity_mismatch",
        ),
        (
            "producer edit",
            edited(lambda d: d["run"]["producer"].update(version="9.9.9")),
            raw,
            ARTIFACT,
            "run_identity_mismatch",
        ),
        ("null run", edited(lambda d: d.update(run=None)), raw, ARTIFACT, "missing_provenance"),
        (
            "null artifact",
            edited(lambda d: d.update(artifact=None)),
            raw,
            ARTIFACT,
            "missing_provenance",
        ),
        (
            "null selector",
            edited(lambda d: d["claims"][0].update(selector=None)),
            raw,
            ARTIFACT,
            "missing_provenance",
        ),
        (
            "profile x",
            edited(lambda d: d["artifact"].update(profile="x")),
            raw,
            ARTIFACT,
            "unsupported_profile",
        ),
        ("edited record", good, tampered, ARTIFACT, "record_invalid"),
        (
            "different record id",
            edited(
                lambda d: d.update(
                    retrieval_record_id=json.loads(r3_bytes)["data"]["retrieval_record_id"]
                )
            ),
            raw,
            ARTIFACT,
            "record_id_mismatch",
        ),
        (
            "unknown bundle",
            edited(lambda d: d["claims"][0].update(results=["f" * 64])),
            raw,
            ARTIFACT,
            "result_not_in_record",
        ),
        (
            "wrong source files",
            edited(lambda d: d["claims"][0].update(source_files={"app/payments.py": "a" * 64})),
            raw,
            ARTIFACT,
            "source_fingerprint_mismatch",
        ),
    ]
    for name in ("doctype", "pi", "comment", "namespace", "depth", "nodecl", "misplaced"):
        data = fixture(f"junit-{name}.xml")
        cases.append(
            (
                f"invalid xml {name}",
                one(NODE_A, "passed", artifact=data),
                raw,
                data,
                "artifact_invalid",
            )
        )
    for label, data in (
        ("nul", ARTIFACT.replace(b"captured", b"cap\x00tured")),
        ("invalid utf-8", ARTIFACT.replace(b"captured", b"cap\xfftured")),
        ("truncated", ARTIFACT[:-20]),
    ):
        cases.append(
            (
                f"invalid xml {label}",
                one(NODE_A, "passed", artifact=data),
                raw,
                data,
                "artifact_invalid",
            )
        )
    return cases


def test_feedback_forged_success_is_rejected(world, r1):
    raw, record = r1
    _, r3 = produce_record(world, "before", "process_payment", max_candidates=16)
    r3_bytes = json.dumps(r3).encode()
    # A previous store (valid) and an output holding sentinel bytes: both must stay as they are.
    previous = import_ok(
        world, "before", envelope(record, good_claims(record), artifact=ARTIFACT), raw, ARTIFACT
    )
    previous_bytes = previous.read_bytes()
    for label, document, record_bytes, artifact, text in forged_cases(world, r1, r3_bytes):
        sentinel = b"SENTINEL-" + label.encode() + b"\x00\xff"
        n = serial()
        out = world.output(f"forged-{n}") / "out.json"
        out.write_bytes(sentinel)
        envelope_path = world.input(f"forged-env-{n}.json", dumps(document))
        record_path = world.input(f"forged-rec-{n}.json", record_bytes)
        artifact_path = world.input(f"forged-art-{n}.xml", artifact)
        result = cli_run(
            *import_args(
                world,
                "before",
                envelope_path,
                record_path,
                out,
                artifact=artifact_path,
                store=previous,
            )
        )
        assert_rejected(result, REJECT + "unverifiable_outcome: " + text, out, sentinel)
        assert previous.read_bytes() == previous_bytes, label
    # A record of the later generation named against the earlier snapshot.
    raw2, after = produce_record(world, "after")
    last2 = len(bundle_ids(after)) - 1
    other = envelope(after, [claim(after, "c", NODE_A, "passed", [last2])], artifact=ARTIFACT)
    result, out = run_import(world, "before", other, raw2, ARTIFACT, store=previous)
    assert_rejected(result, REJECT + "unverifiable_outcome: snapshot_mismatch", out)
    # Too many testcases for the declared limit.
    result, out = run_import(
        world, "before", envelope(record, good_claims(record), artifact=ARTIFACT), raw, ARTIFACT,
        extra=("--max-testcases", 3),
    )  # fmt: skip
    assert_rejected(result, REJECT + "unverifiable_outcome: artifact_invalid", out)
    # All or nothing: one good claim plus one forged claim writes nothing.
    claims = [
        claim(record, "good", NODE_A, "passed", [len(bundle_ids(record)) - 1]),
        claim(record, "forged", NODE_B, "passed", [1]),
    ]
    result, out = run_import(
        world, "before", envelope(record, claims, artifact=ARTIFACT), raw, ARTIFACT
    )
    assert_rejected(result, REJECT + "unverifiable_outcome: claim_disagrees_with_artifact", out)
    # An outcome the vocabulary does not have is an invalid envelope, never a verified event.
    bad = envelope(record, good_claims(record), artifact=ARTIFACT)
    bad["claims"][0]["outcome"] = "success"
    result, out = run_import(world, "before", bad, raw, ARTIFACT)
    assert_rejected(result, REJECT + "invalid_envelope", out)
    # Free text in a claim is not part of the envelope either.
    bad = envelope(record, good_claims(record), artifact=ARTIFACT)
    bad["claims"][0]["note"] = "this test passed"
    result, out = run_import(world, "before", bad, raw, ARTIFACT)
    assert_rejected(result, REJECT + "invalid_envelope", out)


# ---- AC-S35-003 --------------------------------------------------------------------------------


def table_counts(path) -> dict[str, int]:
    connection = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    try:
        names = [
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        ]
        return {n: connection.execute(f'SELECT count(*) FROM "{n}"').fetchone()[0] for n in names}
    finally:
        connection.close()


def test_feedback_overlay_preserves_canonical_facts(tmp_path_factory):
    private = build_world(tmp_path_factory.mktemp("s35-facts"))
    raw, record = produce_record(private, "before")
    assert_untruncated(record)
    claims = good_claims(record)
    document = envelope(record, claims, artifact=ARTIFACT)
    # Every input is staged first: the inputs directory is itself inventoried.
    paths = {
        "envelope": private.input("e.json", dumps(document)),
        "record": private.input("r.json", raw),
        "artifact": private.input("a.xml", ARTIFACT),
        "request": private.input("q.json", dumps(request_arguments())),
        "broken": private.input("broken-store.json", b'{"format": "atlas-feedback-events.v1"}'),
    }
    forged = envelope(record, [claim(record, "f", NODE_B, "passed", [1])], artifact=ARTIFACT)
    paths["forged"] = private.input("f.json", dumps(forged))
    outputs = private.output("outputs")
    dbs = sorted(p for root in private.roots for p in root.rglob("graph.*.db"))
    facts_before = {str(p): (sha(p.read_bytes()), table_counts(p)) for p in dbs}
    assert facts_before
    tools_before = cli_run("tools", "--repo", private.repo)
    inventory = stat_inventory(*private.trees)
    mcp_before = produce_record(private, "before")[0]
    memex_before = stat_inventory(private.memex)

    def assert_unchanged(label: str) -> None:
        assert stat_inventory(*private.trees) == inventory, label
        assert temp_leftovers(private.tmp) == [], label

    store_path = outputs / "store.json"
    args = import_args(
        private,
        "before",
        paths["envelope"],
        paths["record"],
        store_path,
        artifact=paths["artifact"],
    )
    assert cli_run(*args).exit_code == 0
    assert_unchanged("import ok")
    assert sorted(p.name for p in outputs.iterdir()) == ["store.json"]
    rejected = outputs / "rejected.json"
    result = cli_run(
        *import_args(
            private,
            "before",
            paths["forged"],
            paths["record"],
            rejected,
            artifact=paths["artifact"],
        )
    )
    assert result.exit_code == 1 and not rejected.exists()
    assert_unchanged("import rejected")
    inside = private.root("before") / "inside.json"
    result = cli_run(
        *import_args(
            private,
            "before",
            paths["envelope"],
            paths["record"],
            inside,
            artifact=paths["artifact"],
        )
    )
    assert_usage(result, "--output must be outside the repository and graph root.", inside)
    assert_unchanged("import usage")
    disabled = cli_run(*rank_args(private, "before", paths["request"]))
    assert disabled.exit_code == 0
    assert_unchanged("rank disabled")
    enabled = cli_run(
        *rank_args(private, "before", paths["request"], store=store_path, overlay=True)
    )
    assert enabled.exit_code == 0
    assert_unchanged("rank enabled")
    invalid = cli_run(
        *rank_args(private, "before", paths["request"], store=paths["broken"], overlay=True)
    )
    assert invalid.exit_code == 1 and invalid.stdout_bytes == b""
    assert invalid.stderr == "Error: Feedback ranking rejected: invalid_store\n"
    assert_unchanged("rank invalid_store")
    # The database bytes and per-table counts, the memex root, the relations and the tools JSON.
    assert {str(p): (sha(p.read_bytes()), table_counts(p)) for p in dbs} == facts_before
    assert stat_inventory(private.memex) == memex_before
    assert produce_record(private, "before")[0] == mcp_before
    tools_after = cli_run("tools", "--repo", private.repo)
    assert tools_after.stdout_bytes == tools_before.stdout_bytes
    assert len(tools_after.stdout_bytes) == BASE_TOOLS_BYTES
    assert hashlib.sha256(tools_after.stdout_bytes).hexdigest() == BASE_TOOLS_SHA256
    assert len(READ_ONLY_TOOLS) == 7
    assert (
        frozenset(
            {
                "call",
                "construct",
                "import",
                "superclass",
                "include",
                "extend",
                "prepend",
                "rationale_for",
            }
        )
        == ev.RELATIONS
    )


# ---- AC-S35-004 --------------------------------------------------------------------------------


def rank(world, name, store, arguments=None):
    result = run_rank(world, name, arguments or request_arguments(), store=store, overlay=True)
    assert result.exit_code == 0, result.stderr
    document = json.loads(result.stdout_bytes)
    assert document["baseline"]["collection_stop"] is None
    return document, result


def entry_of(document, bundle_id):
    return next(e for e in document["ranking"] if e["bundle_id"] == bundle_id)


def test_feedback_opt_in_ranking_is_explained(world, r1, good_store):
    raw, record = r1
    ids = bundle_ids(record)
    a_id, b_id = ids[-1], ids[1]
    document, result = rank(world, "before", good_store)
    store = store_of(good_store)
    by_outcome = {e["claim"]["outcome"]: e["id"] for e in store["events"]}
    assert document["overlay"]["enabled"] is True
    assert document["overlay"]["policy_name"] == POLICY_NAME
    assert document["overlay"]["policy_id"] == POLICY_ID
    assert document["baseline"]["collection_stop"] is None
    assert [b["bundle_id"] for b in document["baseline"]["bundles"]] == ids
    entry = entry_of(document, a_id)
    assert entry["adjustment"] == {
        "delta": 1,
        "rule": "verified_passed_distinct_artifact_selector",
        "supporting_event_ids": [by_outcome["passed"]],
    }
    assert entry["baseline_position"] - entry["position"] == 1
    assert [e["bundle_id"] for e in document["ranking"]] == expected_order(ids, {a_id: 1})
    active = {a["event_id"]: a for a in document["events"]["active"]}
    assert active[by_outcome["failed"]]["scored"] is False
    assert active[by_outcome["passed"]]["scored"] is True
    assert all(a["attestation"] == "operator_declared" for a in active.values())
    assert entry_of(document, b_id)["adjustment"] is None
    assert document["annotations"] == {"count": 1, "scored": 0}
    assert document["events"]["excluded"] == []
    ranking.validate_ranking(document)
    assert b"applied" not in result.stdout_bytes
    # The same artifact and selector through a second record: two ids, the delta counted once.
    raw3, r3 = produce_record(world, "before", "process_payment", max_candidates=16)
    assert_untruncated(r3)
    assert (
        bundle_ids(r3) == ids
        and r3["data"]["retrieval_record_id"] != record["data"]["retrieval_record_id"]
    )
    via_r3 = envelope(r3, [claim(r3, "c", NODE_A, "passed", [a_id])], artifact=ARTIFACT)
    s_r3 = import_ok(world, "before", via_r3, raw3, ARTIFACT, store=good_store)
    doc3, _ = rank(world, "before", s_r3)
    support3 = entry_of(doc3, a_id)["adjustment"]
    assert support3["delta"] == 1 and len(support3["supporting_event_ids"]) == 2
    assert support3["supporting_event_ids"] == sorted(support3["supporting_event_ids"])
    # A second artifact for the same selector adds one distinct unit: delta 2, two sorted ids.
    second = envelope(record, [claim(record, "c", NODE_A, "passed", [a_id])], artifact=SECOND)
    s_two = import_ok(world, "before", second, raw, SECOND, store=good_store)
    doc2, result2 = rank(world, "before", s_two)
    support2 = entry_of(doc2, a_id)["adjustment"]
    assert support2["delta"] == 2 and len(support2["supporting_event_ids"]) == 2
    assert support2["supporting_event_ids"] == sorted(support2["supporting_event_ids"])
    assert [e["bundle_id"] for e in doc2["ranking"]] == expected_order(ids, {a_id: 2})
    assert entry_of(doc2, a_id)["baseline_position"] - entry_of(doc2, a_id)["position"] == 2
    ranking.validate_ranking(doc2)
    assert b"applied" not in result2.stdout_bytes
    # Both: three events, two distinct units.
    s_all = import_ok(world, "before", second, raw, SECOND, store=s_r3)
    doc_all, _ = rank(world, "before", s_all)
    support_all = entry_of(doc_all, a_id)["adjustment"]
    assert support_all["delta"] == 2 and len(support_all["supporting_event_ids"]) == 3
    # The overlay only permutes what the baseline returned.
    for item in (document, doc2, doc3, doc_all):
        assert sorted(e["bundle_id"] for e in item["ranking"]) == sorted(ids)


# ---- AC-S35-005 --------------------------------------------------------------------------------


def test_feedback_stale_source_is_excluded(world, r1, good_store):
    _, record = r1
    ids = bundle_ids(record)
    store = store_of(good_store)
    every = sorted(e["id"] for e in store["events"])
    for name, reason in (
        ("after", "source_fingerprint_mismatch"),
        ("unrelated", "generation_mismatch"),
        ("removed", "path_not_indexed"),
    ):
        document, _ = rank(world, name, good_store)
        excluded = document["events"]["excluded"]
        assert sorted(e["event_id"] for e in excluded) == every, name
        assert {e["reason"] for e in excluded} == {reason}, name
        assert document["events"]["active"] == [] and document["counts"]["adjusted"] == 0
        assert all(e["adjustment"] is None for e in document["ranking"])
        base = [b["bundle_id"] for b in document["baseline"]["bundles"]]
        assert [e["bundle_id"] for e in document["ranking"]] == base
        ranking.validate_ranking(document)
    rebuilt, _ = rank(world, "rebuild", good_store)
    assert rebuilt["events"]["excluded"] == []
    assert sorted(a["event_id"] for a in rebuilt["events"]["active"]) == every
    assert [e["bundle_id"] for e in rebuilt["ranking"]] == expected_order(ids, {ids[-1]: 1})
    # A store holding both generations: on the later one only its own events support anything.
    raw2, after = produce_record(world, "after")
    assert_untruncated(after)
    ids2 = bundle_ids(after)
    later = envelope(
        after, [claim(after, "c", NODE_A, "passed", [len(ids2) - 1])], artifact=ARTIFACT
    )
    mixed = import_ok(world, "after", later, raw2, ARTIFACT, store=good_store)
    both = store_of(mixed)
    assert len(both["events"]) == 3
    new_ids = sorted(
        e["id"] for e in both["events"] if e["snapshot"]["snapshot_id"] == world.snapshot("after")
    )
    assert len(new_ids) == 1
    document, _ = rank(world, "after", mixed)
    assert [a["event_id"] for a in document["events"]["active"]] == new_ids
    assert sorted(e["event_id"] for e in document["events"]["excluded"]) == every
    assert all(e["reason"] == "source_fingerprint_mismatch" for e in document["events"]["excluded"])
    adjusted = [e for e in document["ranking"] if e["adjustment"]]
    assert [a["adjustment"]["supporting_event_ids"] for a in adjusted] == [new_ids]
    assert [e["bundle_id"] for e in document["ranking"]] == expected_order(ids2, {ids2[-1]: 1})
    # The earlier generation still sees only its own events.
    older, _ = rank(world, "before", mixed)
    assert sorted(a["event_id"] for a in older["events"]["active"]) == every
    assert {e["event_id"]: e["reason"] for e in older["events"]["excluded"]} == {
        new_ids[0]: "source_fingerprint_mismatch"
    }


# ---- AC-S35-006 --------------------------------------------------------------------------------


def run_module(seed: str, *args):
    env = {**os.environ, "PYTHONHASHSEED": seed}
    return subprocess.run(
        [sys.executable, "-m", "atlas_aci", *[str(a) for a in args]],
        capture_output=True,
        env=env,
        timeout=120,
    )


def test_feedback_reimport_is_idempotent(world, r1):
    raw, record = r1
    ids = bundle_ids(record)
    claims = good_claims(record)
    notes = [annotation(record, "note-1", "useful", [2], "fine")]
    document = envelope(record, claims, notes, artifact=ARTIFACT)
    first = import_ok(world, "before", document, raw, ARTIFACT)
    second = import_ok(world, "before", document, raw, ARTIFACT)
    assert first.read_bytes() == second.read_bytes()
    # Permuted claims, renamed labels and a different JSON layout change nothing.
    shuffled = envelope(
        record,
        [dict(c, claim_id="zz-" + c["claim_id"]) for c in reversed(claims)],
        [dict(n, annotation_id="other") for n in notes],
        artifact=ARTIFACT,
    )
    compact = json.dumps(shuffled, separators=(",", ":")).encode()
    third = import_ok(world, "before", compact, raw, ARTIFACT)
    assert third.read_bytes() == first.read_bytes()
    # The order of the command-line options changes nothing, and neither does a limit.
    shuffled_args = import_args(
        world,
        "before",
        world.input("opt-envelope.json", dumps(document)),
        world.input("opt-record.json", raw),
        world.output("opt") / "store.json",
        artifact=world.input("opt-artifact.xml", ARTIFACT),
        extra=("--max-claims", 500),
    )
    pairs = [shuffled_args[i : i + 2] for i in range(1, len(shuffled_args), 2)]
    reordered = cli_run(shuffled_args[0], *[a for pair in reversed(pairs) for a in pair])
    assert reordered.exit_code == 0, reordered.stderr
    assert (world.output("opt") / "store.json").read_bytes() == first.read_bytes()
    # Importing again over the store changes nothing and says so.
    result, again = run_import(world, "before", document, raw, ARTIFACT, store=first)
    assert result.exit_code == 0, result.stderr
    assert again.read_bytes() == first.read_bytes()
    counts = json.loads(result.stdout_bytes)["counts"]
    assert counts["duplicate_events"] == 2 and counts["new_events"] == 0
    assert counts["duplicate_annotations"] == 1 and counts["new_annotations"] == 0
    assert json.loads(result.stdout_bytes)["previous_store_id"] == store_of(first)["id"]
    # The same bytes from fresh interpreters with different hash seeds.
    artifact_path = world.input("seed-artifact.xml", ARTIFACT)
    envelope_path = world.input("seed-envelope.json", dumps(document))
    record_path = world.input("seed-record.json", raw)
    request_path = world.input("seed-request.json", dumps(request_arguments()))
    produced = []
    for seed in ("1", "2"):
        out = world.output(f"seed-{seed}") / "store.json"
        done = run_module(
            seed,
            *import_args(world, "before", envelope_path, record_path, out, artifact=artifact_path),
        )
        assert done.returncode == 0, done.stderr
        assert out.read_bytes() == first.read_bytes()
        ranked = run_module(
            seed, *rank_args(world, "before", request_path, store=out, overlay=True)
        )
        assert ranked.returncode == 0, ranked.stderr
        produced.append(ranked.stdout)
    assert produced[0] == produced[1]
    # The ranking sidecars of the two stores are byte-equal.
    one = run_rank(world, "before", request_arguments(), store=first, overlay=True)
    two = run_rank(world, "before", request_arguments(), store=again, overlay=True)
    assert one.stdout_bytes == two.stdout_bytes == produced[0]
    assert json.loads(one.stdout_bytes)["baseline"]["collection_stop"] is None
    # A different result set for one (record, artifact, selector) is a conflict, not a merge.
    wider = envelope(
        record, [claim(record, "c", NODE_A, "passed", [len(ids) - 1, 1])], artifact=ARTIFACT
    )
    result, out = run_import(world, "before", wider, raw, ARTIFACT, store=first)
    assert_rejected(result, REJECT + "conflicting_duplicate", out)
    # A producer version change under the same artifact and selector is a conflict too.
    relabelled = envelope(
        record, claims[:1], artifact=ARTIFACT, producer={"name": "pytest", "version": "9.0.4"}
    )
    result, out = run_import(world, "before", relabelled, raw, ARTIFACT, store=first)
    assert_rejected(result, REJECT + "conflicting_duplicate", out)
    # So is a changed annotation note: the key excludes the note, the id includes it.
    edited = envelope(
        record, claims, [annotation(record, "note-1", "useful", [2], "changed")], artifact=ARTIFACT
    )
    result, out = run_import(world, "before", edited, raw, ARTIFACT, store=first)
    assert_rejected(result, REJECT + "conflicting_duplicate", out)


# ---- AC-S35-007 --------------------------------------------------------------------------------


def test_feedback_disabled_preserves_baseline(world, r1, good_store):
    _, record = r1
    arguments = request_arguments()
    assert good_store.exists()
    result = run_rank(world, "before", arguments)
    assert result.exit_code == 0, result.stderr
    document = json.loads(result.stdout_bytes)
    library = library_context(world, "before", arguments)
    mcp = mcp_result(world, "before", arguments)
    assert not mcp.isError
    mcp_ids = [b["id"] for b in mcp.structuredContent["data"]["bundles"]]
    library_ids = [b["id"] for b in library["data"]["bundles"]]
    ranked = [e["bundle_id"] for e in document["ranking"]]
    baseline = [b["bundle_id"] for b in document["baseline"]["bundles"]]
    assert ranked == baseline == mcp_ids == library_ids == bundle_ids(record)
    assert_untruncated(library)
    assert document["baseline"]["collection_stop"] is None
    assert [e["position"] for e in document["ranking"]] == list(range(len(ranked)))
    assert [e["baseline_position"] for e in document["ranking"]] == list(range(len(ranked)))
    assert all(e["adjustment"] is None for e in document["ranking"])
    assert document["overlay"] == {
        "enabled": False, "policy_name": None, "policy_id": None, "policy": None,
    }  # fmt: skip
    assert document["store_id"] is None
    assert document["events"] == {"active": [], "excluded": []}
    assert document["annotations"] == {"count": 0, "scored": 0}
    assert document["baseline"]["retrieval_record_id"] == library["data"]["retrieval_record_id"]
    assert document["baseline"]["retrieval_record_id"] == record["data"]["retrieval_record_id"]
    ranking.validate_ranking(document)
    # `--store` alone is a usage error: the store is never read without the overlay.
    request_path = world.input("q-usage.json", dumps(arguments))
    usage = cli_run(*rank_args(world, "before", request_path, store=good_store))
    assert_usage(usage, "--store requires --overlay.")
    usage = cli_run(*rank_args(world, "before", request_path, overlay=True))
    assert_usage(usage, "--overlay requires --store.")
    # The overlay with no active event still yields the baseline order (root-after, stale).
    stale = run_rank(world, "after", arguments, store=good_store, overlay=True)
    document = json.loads(stale.stdout_bytes)
    assert [e["bundle_id"] for e in document["ranking"]] == [
        b["bundle_id"] for b in document["baseline"]["bundles"]
    ]
