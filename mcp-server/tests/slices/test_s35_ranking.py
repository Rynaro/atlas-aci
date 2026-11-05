"""ACI-S35 ranking: the D9 order, D8 staleness, every rank rejection, D10, the validators."""

from __future__ import annotations

import itertools
import json

import pytest
from test_s35_support import (
    NODE_A,
    NODE_B,
    World,
    annotation,
    build_world,
    bundle_ids,
    claim,
    cli_run,
    domain_digest,
    dumps,
    envelope,
    expected_order,
    fixture,
    import_ok,
    mcp_result,
    observations,
    produce_record,
    rank_args,
    request_arguments,
    run_rank,
    store_of,
)

from atlas_aci.feedback import outcomes as fo
from atlas_aci.feedback import ranking
from atlas_aci.overlays import runtime
from atlas_aci.query.diff import Side

ARTIFACT = fixture("junit-outcomes.xml")
SECOND = fixture("junit-second.xml")
LIMITS = fo.normalize_limits()
REQUEST = dumps(request_arguments())


@pytest.fixture(scope="module")
def world(tmp_path_factory) -> World:
    return build_world(tmp_path_factory.mktemp("s35r"))


@pytest.fixture(scope="module")
def r1(world):
    return produce_record(world, "before")


@pytest.fixture(scope="module")
def store_path(world, r1):
    raw, record = r1
    last = len(bundle_ids(record)) - 1
    document = envelope(
        record,
        [claim(record, "a", NODE_A, "passed", [last]), claim(record, "b", NODE_B, "failed", [1])],
        [annotation(record, "n", "useful", [2], "fine")],
        artifact=ARTIFACT,
    )
    return import_ok(world, "before", document, raw, ARTIFACT)


@pytest.fixture(scope="module")
def doc(world, store_path):
    result = run_rank(world, "before", request_arguments(), store=store_path, overlay=True)
    assert result.exit_code == 0, result.stderr
    return json.loads(result.stdout_bytes)


# ---- D9: the order ------------------------------------------------------------------------------


def ids_of(n: int) -> list[str]:
    return [f"{(7 * i + 3) % 16:x}" * 64 for i in range(n)]


def test_the_order_matches_an_independent_recomputation_for_every_delta_assignment():
    ids = ids_of(5)
    for assignment in itertools.product(range(5), repeat=5):
        deltas = {b: d for b, d in zip(ids, assignment, strict=True) if d}
        assert ranking.order(ids, deltas) == expected_order(ids, deltas), assignment
        assert ranking.order(ids, deltas)[0] == ids[0]
        assert sorted(ranking.order(ids, deltas)) == sorted(ids)
    assert ranking.order([], {}) == []
    assert ranking.order(ids[:1], {ids[0]: 3}) == ids[:1]


def test_a_delta_moves_a_bundle_exactly_that_many_slots_and_never_above_position_one():
    ids = ids_of(8)
    for position in range(1, 8):
        for delta in range(1, 4):
            moved = ranking.order(ids, {ids[position]: delta})
            assert moved.index(ids[position]) == max(1, position - delta)
            others = [b for b in moved if b != ids[position]]
            assert others == [b for b in ids if b != ids[position]]
    # An adjusted bundle goes ahead of an unadjusted one it ties with.
    moved = ranking.order(ids, {ids[3]: 1})
    assert moved[2] == ids[3] and moved[3] == ids[2]
    # Two adjusted bundles keep the larger delta first on a tie of (position - delta).
    moved = ranking.order(ids, {ids[4]: 2, ids[3]: 1})
    assert moved.index(ids[4]) < moved.index(ids[3])
    assert moved[:4] == [ids[0], ids[1], ids[4], ids[3]]


def fake_event(event_id, outcome, bundle_id, artifact, selector="tests/t.py::t"):
    return {
        "id": event_id,
        "claim": {"outcome": outcome, "selector": {"kind": "pytest_nodeid", "value": selector}},
        "artifact": {"sha256": artifact, "bytes": 1},
        "results": [{"bundle_id": bundle_id}],
    }


def test_scoring_counts_distinct_units_caps_at_three_and_ignores_failures():
    ids = ids_of(4)
    target = ids[3]
    events = [fake_event(f"{i:064x}", "passed", target, f"{i:064x}") for i in range(1, 6)]
    for count, delta in ((1, 1), (2, 2), (3, 3), (4, 3), (5, 3)):
        found = ranking.adjustments(ids, events[:count])
        assert found == {target: (delta, sorted(e["id"] for e in events[:count]))}
    # The same artifact and selector, however many events carry it, is one unit.
    same = [fake_event(f"{i:064x}", "passed", target, "a" * 64) for i in range(1, 4)]
    assert ranking.adjustments(ids, same)[target][0] == 1
    # A second selector under the same artifact is a second unit.
    other = [*same, fake_event("f" * 64, "passed", target, "a" * 64, "tests/t.py::other")]
    assert ranking.adjustments(ids, other)[target][0] == 2
    # A failed outcome is recorded, never scored.
    failed = [fake_event("1" * 64, "failed", target, "b" * 64)]
    assert ranking.adjustments(ids, failed) == {}
    assert ranking.adjustments(ids, [*failed, *same])[target][1] == sorted(e["id"] for e in same)


# ---- D8: staleness ------------------------------------------------------------------------------


def test_the_reason_precedence_is_path_then_fingerprint_then_generation(world, store_path):
    store = store_of(store_path)
    queried = (world.snapshot("before"), world.generation("before"))
    seen = observations(world.side("before"))
    active, excluded, notes = ranking.activity(store, seen, *queried)
    assert len(active) == 2 and excluded == [] and notes == 1
    wrong_digest = {**seen, "app/payments.py": "0" * 64}
    missing = {k: v for k, v in seen.items() if k != "app/payments.py"}
    other_generation = ("1" * 64, "2" * 64)
    every = sorted(e["id"] for e in store["events"])

    def reasons(observed, pair):
        _, rows, _ = ranking.activity(store, observed, *pair)
        assert [r["event_id"] for r in rows] == every
        return {r["reason"] for r in rows}

    assert reasons(missing, other_generation) == {"path_not_indexed"}
    assert reasons(missing, queried) == {"path_not_indexed"}
    assert reasons(wrong_digest, other_generation) == {"source_fingerprint_mismatch"}
    assert reasons(wrong_digest, queried) == {"source_fingerprint_mismatch"}
    assert reasons(seen, other_generation) == {"generation_mismatch"}
    assert reasons(seen, ("1" * 64, queried[1])) == {"generation_mismatch"}
    assert reasons(seen, (queried[0], "2" * 64)) == {"generation_mismatch"}
    # Stale annotations are not counted as active either.
    assert ranking.activity(store, {}, *queried)[2] == 0


# ---- the request --------------------------------------------------------------------------------


def test_the_request_is_validated_like_the_mcp_path(world):
    ok = request_arguments(max_candidates=16, relations=["call"], direction="outgoing")
    assert ranking.parse_request(dumps(ok), LIMITS) == ok
    assert not mcp_result(world, "before", ok).isError
    invalid = {
        "no text": {"schema_version": "atlas.graph.v1", "op": "context"},
        "text type": request_arguments(text=1),
        "wrong op": request_arguments() | {"op": "retrieve"},
        "no schema version": {"op": "context", "text": "x"},
        "wrong schema version": request_arguments() | {"schema_version": "atlas.graph.v2"},
        "continuation": request_arguments(continuation=True),
        "expected generation": request_arguments(expected_generation_id="a" * 64),
        "unknown key": request_arguments(extra=1),
        "float limit": request_arguments(max_candidates=1.5),
        "bool limit": request_arguments(max_candidates=True),
        "limit above maximum": request_arguments(max_candidates=101),
        "bad relation": request_arguments(relations=["other"]),
        "bad direction": request_arguments(direction="sideways"),
        "bad policy": request_arguments(source_policy="all"),
        "bad budget": request_arguments(max_bytes=-1),
        "legacy mixed": request_arguments(query="x"),
    }
    for label, request in invalid.items():
        with pytest.raises(fo.FeedbackRejected) as caught:
            ranking.parse_request(json.dumps(request).encode(), LIMITS)
        assert caught.value.reason == "invalid_request", label
    for raw in (
        b"",
        b"[]",
        b"x",
        b"\xff",
        b"[" * 20000,
        b'{"text":"x","text":"y"}',
        b'{"op":NaN}',
    ):
        with pytest.raises(fo.FeedbackRejected) as caught:
            ranking.parse_request(raw, LIMITS)
        assert caught.value.reason == "invalid_request"
    with pytest.raises(fo.FeedbackRejected) as caught:
        ranking.parse_request(dumps(ok), fo.normalize_limits({"max_request_bytes": 10}))
    assert caught.value.reason == "request_too_large"


# ---- every rank rejection -----------------------------------------------------------------------


class Proxy:
    def __init__(self, reader, **replaced):
        self._reader = reader
        self._replaced = replaced

    def __getattr__(self, name):
        return self._replaced[name] if name in self._replaced else getattr(self._reader, name)


class NoRow:
    def execute(self, *args):
        return self

    def fetchone(self):
        return None


def spy(patch, **replaced):
    real = runtime.open_active
    patch.setattr(runtime, "open_active", lambda stack, side: Proxy(real(stack, side), **replaced))


def refused(world, request=REQUEST, store=None, enabled=False, limits=LIMITS, side=None):
    with pytest.raises(fo.FeedbackRejected) as caught:
        ranking.rank_feedback(request, side or world.side("before"), store, enabled, limits)
    return caught.value.reason


def test_every_rank_rejection_is_reachable(world, r1, store_path, tmp_path, monkeypatch):
    stored = store_path.read_bytes()
    small = lambda **change: fo.normalize_limits(change)  # noqa: E731
    (tmp_path / "bare").mkdir()
    reached = {
        refused(world, limits=small(max_request_bytes=10)),
        refused(world, request=b"{}"),
        refused(world, store=stored, enabled=True, limits=small(max_store_bytes=10)),
        refused(world, store=b"{}", enabled=True),
        refused(world, store=b'{"format": "atlas-feedback-events.v1"}', enabled=True),
        refused(world, store=b'{"format": "v2"}', enabled=True),
        refused(world, store=stored, enabled=True, limits=small(max_events=1)),
        refused(world, side=Side(tmp_path / "bare", tmp_path / "g")),
        refused(world, request=dumps(request_arguments(max_bytes=1))),
        refused(world, limits=small(max_output_bytes=1)),
    }
    with monkeypatch.context() as patch:
        spy(patch, _lease_schema_epoch=8)
        reached.add(refused(world))
    with monkeypatch.context() as patch:
        spy(patch, db=NoRow())
        reached.add(refused(world))
    with monkeypatch.context() as patch:
        patch.setattr(ranking, "_document", lambda **kwargs: {"format": ranking.RANKING_FORMAT})
        reached.add(refused(world))
    assert reached == set(ranking.RANK_REJECTIONS)
    # The store is never read without the overlay, and the combinations are usage errors.
    document = ranking.rank_feedback(REQUEST, world.side("before"), None, False, LIMITS)
    assert document["overlay"]["enabled"] is False
    for store, enabled, what in (
        (stored, False, "store_without_overlay"),
        (None, True, "overlay_without_store"),
    ):
        with pytest.raises(fo.FeedbackUsageError) as caught:
            ranking.rank_feedback(REQUEST, world.side("before"), store, enabled, LIMITS)
        assert caught.value.what == what


def test_the_command_line_texts(world, store_path, monkeypatch):
    result = run_rank(world, "before", request_arguments(max_bytes=1))
    assert result.exit_code == 1 and result.stdout_bytes == b""
    assert result.stderr == "Error: Feedback ranking rejected: baseline_unavailable\n"
    path = world.input("q-other.json", REQUEST)
    result = cli_run(*rank_args(world, "before", path, store=store_path), "--overlay", "other")
    assert result.exit_code == 2 and result.stdout_bytes == b""
    assert result.stderr.splitlines()[-1] == "Error: Unsupported ranking policy."
    monkeypatch.setattr(
        ranking, "rank_feedback", lambda *a: (_ for _ in ()).throw(KeyError("/secret"))
    )
    result = run_rank(world, "before", request_arguments())
    assert result.stderr == "Error: Feedback ranking failed: KeyError\n"


# ---- D10 ----------------------------------------------------------------------------------------


def record_variants(world, ids):
    for options in (
        {"max_candidates": 16},
        {"max_candidates": 8},
        {"max_depth": 3},
        {"max_depth": 4},
        {"max_bundles": 8},
        {"max_seeds": 4},
        {"max_examined_edges": 128},
    ):
        raw, record = produce_record(world, "before", "process_payment", **options)
        if bundle_ids(record) == ids:
            yield raw, record


def fold(record) -> int:
    return int(record["data"]["retrieval_record_id"][:8], 16) % 5


def test_held_out_evaluation_is_a_declared_order_report(world, r1):
    raw, record = r1
    ids = bundle_ids(record)
    last = len(ids) - 1
    residue = fold(record)
    other = next(((r, rec) for r, rec in record_variants(world, ids) if fold(rec) != residue), None)
    assert other is not None, "no record variant lands in another fold"
    raw_k, rec_k = other
    path = import_ok(
        world,
        "before",
        envelope(
            record,
            [
                claim(record, "a", NODE_A, "passed", [last]),
                claim(record, "b", NODE_B, "failed", [1]),
            ],
            artifact=ARTIFACT,
        ),
        raw,
        ARTIFACT,
    )
    for artifact in (ARTIFACT, SECOND):
        path = import_ok(
            world,
            "before",
            envelope(rec_k, [claim(rec_k, "a", NODE_A, "passed", [last])], artifact=artifact),
            raw_k,
            artifact,
            store=path,
        )
    store = store_of(path)
    seen = observations(world.side("before"))
    pair = (world.snapshot("before"), world.generation("before"))
    report = ranking.evaluate(store, seen, *pair, modulus=5, residue=residue)
    assert report["fold"] == {"keyed_on": "retrieval_record_id", "modulus": 5, "residue": residue}
    assert report["counts"] == {
        "events": 4,
        "active": 4,
        "held_out": 2,
        "training": 2,
        "excluded_by_reason": {
            "path_not_indexed": 0,
            "source_fingerprint_mismatch": 0,
            "generation_mismatch": 0,
        },
    }
    assert {h["record_id"] for h in report["held_out"]} == {record["data"]["retrieval_record_id"]}
    moved = next(h for h in report["held_out"] if h["results"][0]["bundle_id"] == ids[last])
    assert moved["results"] == [
        {"bundle_id": ids[last], "baseline_position": last, "overlay_position": last - 2}
    ]
    failed = next(h for h in report["held_out"] if h["results"][0]["bundle_id"] == ids[1])
    assert (
        failed["results"][0]["baseline_position"] == failed["results"][0]["overlay_position"] == 1
    )
    assert report["order_basis"] == "declared_record_order"
    ranking.validate_evaluation(report, store, seen, *pair)
    # A fold that holds nothing out reports nothing and trains on every active event.
    free = next(r for r in range(5) if r not in {residue, fold(rec_k)})
    empty = ranking.evaluate(store, seen, *pair, modulus=5, residue=free)
    assert empty["counts"]["held_out"] == 0 and empty["counts"]["training"] == 4
    assert empty["held_out"] == []
    # Stale events are excluded from both sides and counted by reason.
    stale = ranking.evaluate(store, seen, "1" * 64, "2" * 64, modulus=5, residue=residue)
    assert stale["counts"]["active"] == 0 and stale["counts"]["held_out"] == 0
    assert stale["counts"]["excluded_by_reason"]["generation_mismatch"] == 4
    for bad in ((5, 5), (5, -1), (0, 0), (True, 0), (5, True)):
        with pytest.raises(ValueError):
            ranking.evaluate(store, seen, *pair, modulus=bad[0], residue=bad[1])

    # Every check of the validator.
    def tamper(edit):
        copy = json.loads(json.dumps(report))
        edit(copy)
        with pytest.raises(ValueError):
            ranking.validate_evaluation(copy, store, seen, *pair)

    tamper(lambda d: d.update(extra=1))
    tamper(lambda d: d["fold"].update(residue=(residue + 1) % 5))
    tamper(lambda d: d["counts"].update(training=3))
    tamper(lambda d: d["held_out"][0]["results"][0].update(overlay_position=0))
    tamper(lambda d: d.update(policy_id="0" * 64))
    tamper(lambda d: d.update(id="0" * 64))
    tamper(lambda d: d["limitations"].pop())
    with pytest.raises(ValueError):
        ranking.validate_evaluation({"format": "x"}, store, seen, *pair)
    with pytest.raises(ValueError):
        ranking.validate_evaluation(report, store, seen, "1" * 64, "2" * 64)


# ---- the sidecar validator ----------------------------------------------------------------------


def reseal(document):
    rest = {k: v for k, v in document.items() if k != "id"}
    document["id"] = domain_digest(ranking.RANKING_FORMAT, rest)
    return document


def test_validate_ranking_checks_every_part(doc):
    ranking.validate_ranking(doc)
    assert doc["counts"]["adjusted"] == 1

    def broken(edit):
        copy = json.loads(json.dumps(doc))
        edit(copy)
        reseal(copy)
        with pytest.raises(ValueError):
            ranking.validate_ranking(copy)

    adjusted = lambda d: next(e for e in d["ranking"] if e["adjustment"])  # noqa: E731
    active = lambda d: d["events"]["active"][0]  # noqa: E731
    broken(lambda d: d.update(extra=1))
    broken(lambda d: d.pop("counts"))
    broken(lambda d: d.update(format="v2"))
    broken(lambda d: d["producer"].update(version="x"))
    broken(lambda d: d["limitations"].reverse())
    broken(lambda d: d["overlay"].update(enabled=1))
    broken(lambda d: d["overlay"].update(policy_id="0" * 64))
    broken(lambda d: d["overlay"]["policy"].update(pinned="none"))
    broken(lambda d: d.update(store_id=None))
    broken(lambda d: d["snapshot"].update(snapshot_id="x"))
    broken(lambda d: d["baseline"]["bundles"][0].update(position=1))
    broken(
        lambda d: d["baseline"]["bundles"][1].update(
            bundle_id=d["baseline"]["bundles"][0]["bundle_id"]
        )
    )
    broken(lambda d: d["baseline"].update(collection_stop=3))
    broken(lambda d: d["ranking"].reverse())
    broken(lambda d: d["ranking"].pop())
    broken(lambda d: adjusted(d)["adjustment"].update(delta=2))
    broken(lambda d: adjusted(d)["adjustment"].update(rule="other"))
    broken(lambda d: adjusted(d)["adjustment"].update(supporting_event_ids=[]))
    broken(lambda d: adjusted(d).update(adjustment=None))
    broken(lambda d: adjusted(d).update(baseline_position=0))
    broken(lambda d: adjusted(d).update(kind="other"))
    broken(
        lambda d: d["ranking"][0].update(
            adjustment={
                "delta": 1,
                "rule": "verified_passed_distinct_artifact_selector",
                "supporting_event_ids": [active(d)["event_id"]],
            }
        )
    )
    broken(lambda d: active(d).update(scored=not active(d)["scored"]))
    broken(lambda d: active(d).update(attestation="verified"))
    broken(lambda d: active(d).update(outcome="skipped"))
    broken(lambda d: active(d).update(matched_bundle_ids=["f" * 64]))
    broken(lambda d: d["events"]["active"].append(d["events"]["active"][0]))
    broken(
        lambda d: d["events"]["excluded"].append(
            {"event_id": active(d)["event_id"], "reason": "generation_mismatch"}
        )
    )
    broken(lambda d: d["events"]["excluded"].append({"event_id": "9" * 64, "reason": "other"}))
    broken(lambda d: d["annotations"].update(scored=1))
    broken(lambda d: d["counts"].update(adjusted=0))
    broken(lambda d: d["counts"].update(excluded=1))
    broken(lambda d: d.update(note="applied"))
    off = json.loads(json.dumps(doc))
    off["id"] = "0" * 64
    with pytest.raises(ValueError):
        ranking.validate_ranking(off)
    for junk in (None, [], "x"):
        with pytest.raises(ValueError):
            ranking.validate_ranking(junk)


def test_a_disabled_sidecar_must_hold_nothing(world):
    document = ranking.rank_feedback(REQUEST, world.side("before"), None, False, LIMITS)
    ranking.validate_ranking(document)

    def broken(edit):
        copy = json.loads(json.dumps(document))
        edit(copy)
        reseal(copy)
        with pytest.raises(ValueError):
            ranking.validate_ranking(copy)

    broken(lambda d: d["overlay"].update(policy_name=ranking.POLICY_NAME))
    broken(lambda d: d.update(store_id="a" * 64))
    broken(
        lambda d: d["events"]["excluded"].append(
            {"event_id": "a" * 64, "reason": "generation_mismatch"}
        )
    )
    broken(lambda d: d["annotations"].update(count=1))
    broken(lambda d: d["ranking"][1].update(bundle_id=d["ranking"][2]["bundle_id"]))
    broken(lambda d: d["ranking"].reverse())
