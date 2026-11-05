"""ACI-S34 states: the golden outcomes, the precedence rows, the race checks, pure branches."""

from __future__ import annotations

import json
from contextlib import ExitStack
from typing import Any

import pytest
from test_s34_support import (
    EXPECTED,
    WORLD_TEXTS,
    build_world,
    by_mapping,
    reindex,
    sha,
)

from atlas_aci.overlays import runtime
from atlas_aci.query import diff as symbol_diff
from atlas_aci.rationale import adr_mapping as am
from atlas_aci.rationale import drift
from atlas_aci.storage import evidence as ev


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return build_world(tmp_path_factory.mktemp("s34s"))


@pytest.fixture(scope="module")
def mixed(world):
    return drift.rationale_drift(world.mixed(), world.before, world.after)


@pytest.fixture(scope="module")
def change_set(world):
    return symbol_diff.compare(world.before, world.after)


def test_the_fixture_literals_match_an_independent_read(world):
    entries = world.document()["mappings"] + world.document("mapping-unresolved.json")["mappings"]
    with ExitStack() as stack:
        reader = runtime.open_active(stack, world.before)
        for entry in entries:
            data = WORLD_TEXTS["before"].get(entry["document"]["path"])
            if data is not None and entry["mapping_id"] not in {"bad-doc-digest"}:
                raw = data.encode()
                assert entry["document"]["sha256"] == sha(raw)
            target = entry["target"]
            row = reader.db.execute(
                "SELECT payload FROM entities WHERE id=?", (target["entity_id"],)
            ).fetchone()
            if entry["mapping_id"] == "absent-id":
                assert row is None
                continue
            stored = json.loads(row[0])
            if entry["mapping_id"] in {"name-mismatch", "path-mismatch"}:
                assert (stored["path"], stored["name"]) == ("src/pricing.py", "charge")
                continue
            assert (stored["path"], stored["kind"], stored["name"]) == (
                target["path"],
                target["kind"],
                target["name"],
            )
            if target["kind"] == "file":
                assert stored["id"] == ev.file_entity(target["path"], stored["language"])["id"]


def test_every_expected_row_holds(mixed, change_set):
    results = by_mapping(mixed)
    imported = {n for n, r in results.items() if r["stage"] == "mapping_import"}
    assert imported == set(EXPECTED["import"])
    for name, reason in EXPECTED["import"].items():
        assert results[name]["reason"] == reason
        assert results[name]["target_class"] is None
    compared = {n for n, r in results.items() if r["stage"] == "comparison"}
    assert compared == set(EXPECTED["drift"])
    for name, (outcome, source, decision, reason) in EXPECTED["drift"].items():
        found = results[name]
        assert (
            found["outcome"],
            found["source_state"],
            found["decision_state"],
            found["reason"],
        ) == (outcome, source, decision, reason), name
    for name, count in EXPECTED["candidates"].items():
        assert len(results[name]["candidate_ids"]) == count
    counts = mixed["counts"]
    assert counts["mappings"] == 30 and counts["unresolved_at_import"] == 8
    assert counts["resolved"] == 22
    assert len(mixed["findings"]) == counts["mappings"] - counts["no_finding"]
    # The formatting-only and location-only controls match S19's own categories.
    categories = {(r["name"], r["category"]) for r in change_set["changes"]}
    assert {("audit", "format"), ("post", "location")} <= categories
    drift.validate_drift(mixed)


def test_precedence_rows(world, mixed, change_set):
    results = by_mapping(mixed)
    entries = {e["mapping_id"]: e for e in world.document()["mappings"]}
    records = {r["entity_id"]: r for r in change_set["changes"]}
    # An unknown correspondence is never reported as a removal.
    fragile = records[entries["fragile"]["target"]["entity_id"]]
    assert fragile["category"] == "unknown"
    assert results["fragile"]["outcome"] == "unknown_target_history"
    # A removed target wins over a missing document; a missing document still shows.
    assert results["retire-legacy-doc"]["reason"] == "target_removed"
    assert results["retire-legacy-doc"]["decision_state"] == "document_missing"
    assert results["charge-legacy-doc"]["reason"] == "document_not_in_snapshot"
    assert results["charge-legacy-doc"]["source_state"] == "body_changed"
    assert results["charge-legacy-doc"]["change_record_id"] is not None
    # An edited document turns a source change into no finding, never the reverse.
    assert results["charge-doc-edited"]["outcome"] == "no_finding"
    assert results["summary-doc-edited"]["outcome"] == "no_finding"
    assert results["summary-doc-edited"]["decision_state"] == "document_changed"
    ids = {f["mapping_id"] for f in mixed["findings"]}
    assert {"charge-doc-edited", "summary-doc-edited", "summary", "audit", "post"}.isdisjoint(ids)


def test_ordinal_renumbering_is_detected(world, mixed, change_set):
    """Deleting the first of two same-name definitions renumbers the survivor's id."""
    entries = {e["mapping_id"]: e for e in world.document()["mappings"]}
    records = {r["entity_id"]: r for r in change_set["changes"]}
    first = records[entries["twin-first"]["target"]["entity_id"]]
    second = records[entries["twin-second"]["target"]["entity_id"]]
    # The premise: S19 compares the survivor under the old first id (a false body change).
    assert (first["category"], first["ordinal"]) == ("body", 0)
    assert (second["category"], second["ordinal"]) == ("removed", 1)
    results = by_mapping(mixed)
    for name, record in (("twin-first", first), ("twin-second", second)):
        assert results[name]["outcome"] == "unknown_target_history"
        assert results[name]["reason"] == "sibling_ordinal_shift"
        assert results[name]["source_state"] == "unknown"
        assert results[name]["change_record_id"] == record["id"]
    # A same-name sibling edit that keeps the id set is an ordinary body change.
    assert results["dual-second"]["outcome"] == "rationale_review_needed"
    assert results["dual-second"]["reason"] is None
    # A container is compared by its own declaration: a member edit is its own entity.
    assert results["account-class"]["source_state"] == "unchanged"
    assert "sibling_ordinal_shift" in drift.HISTORY_REASONS


# ---- race checks with real re-indexes -----------------------------------------------------------

EDIT = {"src/reports.py": "def summary(rows):\n    return 1\n"}


def _race(tmp_path, monkeypatch, root, langs=None):
    world = build_world(tmp_path / "w", extra_roots=False)
    real = symbol_diff.compare

    def spy(before, after, **kwargs):
        reindex(world, EDIT, root, langs)
        return real(before, after, **kwargs)

    monkeypatch.setattr(symbol_diff, "compare", spy)
    return world


def test_a_reindex_before_the_comparison_is_snapshot_changed(tmp_path, monkeypatch):
    world = _race(tmp_path, monkeypatch, "root-after")
    with pytest.raises(am.RationaleRejected) as info:
        drift.rationale_drift(world.raw(), world.before, world.after)
    assert info.value.reason == "snapshot_changed"


def test_a_reindex_of_the_mapped_side_is_snapshot_changed(tmp_path, monkeypatch):
    world = _race(tmp_path, monkeypatch, "root-before")
    with pytest.raises(am.RationaleRejected) as info:
        drift.rationale_drift(world.raw(), world.before, world.after)
    assert info.value.reason == "snapshot_changed"


def test_a_reindex_behind_a_rejected_comparison_is_snapshot_changed(tmp_path, monkeypatch):
    world = _race(tmp_path, monkeypatch, "root-after", ["python", "markdown"])
    with pytest.raises(am.RationaleRejected) as info:
        drift.rationale_drift(world.raw(), world.before, world.after)
    assert info.value.reason == "snapshot_changed"


def test_a_rejected_comparison_without_a_race_stays_rejected(tmp_path):
    world = build_world(tmp_path / "w")
    document = drift.rationale_drift(world.raw(), world.before, world.config)
    assert document["comparison"]["reason"] == "configuration_mismatch"


# ---- pure branches (fabricated facts) -----------------------------------------------------------


class Facts:
    """Stands in for `Snapshot`: only the reads `evaluate` makes."""

    def __init__(self, prefix="after_", observations=None, entities=None, siblings=None):
        self.prefix = prefix
        self._observations = observations or {}
        self._entities = entities or {}
        self._siblings = siblings or []

    def observations(self):
        return self._observations

    def entity(self, entity_id):
        return self._entities.get(entity_id)

    def definition(self, row):
        return {"id": "occurrence"} if row.get("defined", True) else None

    def siblings(self, row):
        return self._siblings


ENTITY = "a" * 64
DOC = "d" * 64
OLD = "1" * 64
NEW = "2" * 64


def _record(target_class="symbol", path="src/x.py"):
    return {
        "mapping_id": "m",
        "decision_id": "d",
        "document": {"path": "docs/a.md", "sha256": DOC},
        "target": {
            "entity_id": ENTITY,
            "target_class": target_class,
            "path": path,
            "source_sha256": OLD,
        },
    }


def _observation(state="indexed", digest=DOC):
    return {"state": state, "observed_source_sha256": digest}


def _view(after, change=None, partial=(), before=None, rejection=None):
    records = {} if change is None else {ENTITY: change}
    return drift.View(
        rejection=rejection,
        records=records,
        candidates={},
        partial=frozenset(partial),
        before=before or Facts("before_", entities={ENTITY: {"path": "src/x.py"}}),
        after=after,
    )


def _after(entities=True, **observations):
    seen = {"docs/a.md": _observation(), "src/x.py": _observation(digest=NEW)}
    seen.update(observations)
    return Facts("after_", seen, {ENTITY: {"path": "src/x.py"}} if entities else {})


def test_symbol_branches_without_a_record():
    gone = drift.evaluate(_view(_after(entities=False)), _record())
    assert (gone["outcome"], gone["reason"]) == (
        "unknown_target_history",
        "correspondence_not_confirmed",
    )
    kept = drift.evaluate(_view(_after()), _record())
    assert (kept["outcome"], kept["source_state"]) == ("no_finding", "unchanged")
    added = drift.evaluate(_view(_after(), {"id": "c" * 64, "category": "added"}), _record())
    assert added["reason"] == "correspondence_not_confirmed"
    partial = drift.evaluate(_view(_after(), partial=["src/x.py"]), _record())
    assert partial["reason"] == "path_partial"
    unknown = {"id": "c" * 64, "category": "unknown", "reason": "not_a_reason"}
    with pytest.raises(am.RationaleRejected) as info:
        drift.evaluate(_view(_after(), unknown), _record())
    assert info.value.reason == "comparison_failed"


def test_file_branches():
    file = _record("file")
    unsupported = _after(**{"src/x.py": _observation("unsupported")})
    partial = drift.evaluate(_view(unsupported, partial=["src/x.py"]), file)
    assert partial["reason"] == "path_partial"
    failed = drift.evaluate(_view(_after(**{"src/x.py": _observation("failed")})), file)
    assert failed["reason"] == "extraction_failed_after"
    half = drift.evaluate(_view(_after(entities=False)), file)
    assert half["reason"] == "correspondence_not_confirmed"
    no_observation = _after()
    del no_observation._observations["src/x.py"]
    half = drift.evaluate(_view(no_observation), file)
    assert half["reason"] == "correspondence_not_confirmed"
    removed = _after(entities=False)
    del removed._observations["src/x.py"]
    assert drift.evaluate(_view(removed), file)["reason"] == "target_removed"
    same = _after(**{"src/x.py": _observation(digest=OLD)})
    assert drift.evaluate(_view(same), file)["source_state"] == "unchanged"
    assert drift.evaluate(_view(_after()), file)["source_state"] == "content_changed"


def test_the_document_state_comes_from_the_observation_in_any_state():
    change = {"id": "c" * 64, "category": "body"}
    edited = _after(**{"docs/a.md": _observation("failed", "e" * 64)})
    found = drift.evaluate(_view(edited, change), _record())
    assert found["decision_state"] == "document_changed" and found["outcome"] == "no_finding"
    same = _after(**{"docs/a.md": _observation("failed", DOC)})
    assert drift.evaluate(_view(same, change), _record())["outcome"] == "rationale_review_needed"
    missing = _after()
    del missing._observations["docs/a.md"]
    gone = drift.evaluate(_view(missing, change), _record())
    assert gone["decision_state"] == "document_missing"
    assert (gone["outcome"], gone["reason"]) == ("unresolved_mapping", "document_not_in_snapshot")
    broken = _after(**{"docs/a.md": {"state": "indexed", "observed_source_sha256": "nope"}})
    with pytest.raises(am.RationaleRejected) as info:
        drift.evaluate(_view(broken, change), _record())
    assert info.value.reason == "after_index_unavailable"


def test_the_sibling_guard_compares_id_sets():
    change = {"id": "c" * 64, "category": "body"}
    other = "b" * 64

    def run(before_ids, after_ids):
        before = Facts("before_", entities={ENTITY: {"path": "src/x.py"}}, siblings=before_ids)
        after = _after()
        after._siblings = after_ids
        return drift.evaluate(_view(after, change, before=before), _record())

    assert run([ENTITY, other], [ENTITY])["reason"] == "sibling_ordinal_shift"
    assert run([ENTITY], [ENTITY, other])["reason"] == "sibling_ordinal_shift"
    assert run([ENTITY, other], [ENTITY, "c" * 64])["reason"] == "sibling_ordinal_shift"
    assert run([ENTITY, other], [ENTITY, other])["outcome"] == "rationale_review_needed"
    assert run([ENTITY], [ENTITY])["outcome"] == "rationale_review_needed"


def test_pure_target_helpers():
    assert am._target_class({"kind": "file"}) == "file"
    assert am._target_class({"kind": "function"}) == "symbol"
    assert am._target_class({"kind": "scip_symbol"}) is None
    assert am._target_class({"kind": ev.COVERAGE_TEST_KIND}) is None
    entity = {"id": "e", "kind": "function"}
    definition = {
        "entity_id": "e",
        "kind": "definition",
        "provenance_state": "source",
        "role": "definition:def.function",
    }
    assert am._definition([definition], entity) == definition
    assert am._definition([], entity) is None
    assert am._definition([definition, dict(definition)], entity) is None
    assert am._definition([{**definition, "provenance_state": "scip"}], entity) is None
    assert am._definition([{**definition, "role": "definition:def.class"}], entity) is None
    one = {
        "path": "a",
        "kind": "function",
        "name": "f",
        "owners": [{"kind": "c", "name": "A", "ordinal": 0}],
    }
    two = {**one, "owners": [{"kind": "c", "name": "A", "ordinal": 1}]}
    assert am._sibling_key(one) == am._sibling_key(two)
    assert am._sibling_key(one) != am._sibling_key({**one, "name": "g"})


def test_the_definition_predicate_matches_the_diff_loader(world):
    """R10 agrees with `_Loaded.definition` for every symbol of both snapshots."""
    checked = 0
    for side in (world.before, world.after):
        with ExitStack() as stack:
            reader = runtime.open_active(stack, side)
            loaded = symbol_diff._Loaded(reader, reader.snapshot_id, reader.generation_id, {})
            loaded.load()
            facts = am.Snapshot(reader)
            rows: list[dict[str, Any]] = [
                json.loads(p) for (p,) in reader.db.execute("SELECT payload FROM entities")
            ]
            for row in rows:
                if am._target_class(row) != "symbol":
                    continue
                mine, theirs = facts.definition(row), loaded.definition(row["id"])
                assert (mine or {}).get("id") == (theirs or {}).get("id"), row["name"]
                checked += 1
    assert checked > 40
