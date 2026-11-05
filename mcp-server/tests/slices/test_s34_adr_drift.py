"""ACI-S34 acceptance: exact ADR evidence, review signals, unresolved and unknown, replay."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from contextlib import ExitStack

import pytest
from test_s19_support import build, write_tree
from test_s34_support import (
    ADR,
    EXPECTED,
    WORLD_TEXTS,
    World,
    build_world,
    by_mapping,
    cli_run,
    drift_args,
    encode,
    generation_of,
    map_args,
    sha,
    stat_inventory,
    temp_leftovers,
)

from atlas_aci.overlays import runtime
from atlas_aci.query import diff as symbol_diff
from atlas_aci.rationale import adr_mapping as am
from atlas_aci.rationale import drift
from atlas_aci.storage import evidence as ev


@pytest.fixture(scope="module")
def world(tmp_path_factory) -> World:
    return build_world(tmp_path_factory.mktemp("s34"))


@pytest.fixture(scope="module")
def overlay(world):
    return am.map_rationale(world.raw(), world.before)


@pytest.fixture(scope="module")
def mixed_drift(world):
    return drift.rationale_drift(world.mixed(), world.before, world.after)


@pytest.fixture(scope="module")
def change_set(world):
    return symbol_diff.compare(world.before, world.after)


def _record_id(record):
    body = {k: v for k, v in record.items() if k != "id"}
    return sha(("atlas-rationale-record.v1:" + ev.canonical(body)).encode())


def _ids_in(text: str, *ids: str) -> list[str]:
    return [item for item in ids if item in text]


def test_adr_mapping_has_exact_evidence(world, overlay, tmp_path):
    # The checkout holds the AFTER state: ADR-0003 is gone from it, the snapshot still has it.
    assert not (world.repo / "docs/adr/0003-legacy.md").exists()
    assert (world.repo / "src/pricing.py").read_text() == WORLD_TEXTS["after"]["src/pricing.py"]
    entries = {e["mapping_id"]: e for e in world.document()["mappings"]}
    assert overlay["counts"] == {"mappings": 22, "resolved": 22, "unresolved": 0}
    assert overlay["link_kind"] == "operator_declared_rationale_link"
    assert overlay["snapshot"] == {
        "snapshot_id": world.snapshot_id,
        "generation_id": generation_of(world.before),
    }
    assert {r["mapping_id"] for r in overlay["resolved"]} == set(entries)
    for record in overlay["resolved"]:
        entry = entries[record["mapping_id"]]
        path = entry["document"]["path"]
        text = WORLD_TEXTS["before"][path].encode()
        start, end = entry["span"]["start_byte"], entry["span"]["end_byte"]
        assert record["type"] == "rationale_mapping"
        assert record["decision_id"] == entry["decision_id"]
        assert record["document"] == {"path": path, "sha256": sha(text)}
        assert record["span"] == {
            "start_byte": start,
            "end_byte": end,
            "start_line": text.count(b"\n", 0, start) + 1,
            "end_line": text.count(b"\n", 0, end - 1) + 1,
            "sha256": sha(text[start:end]),
        }
        target = record["target"]
        assert target["entity_id"] == entry["target"]["entity_id"]
        assert (target["path"], target["kind"], target["name"]) == (
            entry["target"]["path"],
            entry["target"]["kind"],
            entry["target"]["name"],
        )
        assert record["snapshot_id"] == world.snapshot_id
        assert record["generation_id"] == overlay["snapshot"]["generation_id"]
        assert target["source_sha256"] == sha(WORLD_TEXTS["before"][target["path"]].encode())
        assert target["target_class"] == ("file" if target["kind"] == "file" else "symbol")
        assert record["id"] == _record_id(record)
    assert overlay["id"] == sha(
        (
            "atlas-rationale-overlay.v1:"
            + ev.canonical({k: v for k, v in overlay.items() if k != "id"})
        ).encode()
    )
    am.validate_overlay(overlay)
    # The span of the deleted ADR comes from the snapshot, not from the checkout.
    legacy = next(r for r in overlay["resolved"] if r["mapping_id"] == "summary-legacy-doc")
    assert legacy["document"]["path"] == "docs/adr/0003-legacy.md"
    # The CLI writes the library's bytes and prints the summary.
    out = tmp_path / "overlay.json"
    result = cli_run(*map_args(world, world.mapping_file("m.json"), out))
    assert result.exit_code == 0, result.stderr
    assert out.read_bytes() == am.canonical_bytes(overlay)
    assert result.stdout_bytes == am.canonical_bytes(am.summary_document(overlay))
    am.validate_summary(json.loads(result.stdout_bytes))
    assert temp_leftovers(tmp_path) == []


def test_adr_source_change_requests_review(world, change_set, mixed_drift):
    results = by_mapping(mixed_drift)
    records = {r["entity_id"]: r for r in change_set["changes"]}
    entries = {e["mapping_id"]: e for e in world.document()["mappings"]}
    for name, state in (("charge", "body"), ("refund", "signature")):
        result = results[name]
        assert result["outcome"] == "rationale_review_needed"
        assert result["decision_state"] == "document_unchanged"
        assert result["document_sha256_before"] == result["document_sha256_after"]
        assert result["document_sha256_before"] == sha(WORLD_TEXTS["before"][ADR].encode())
        record = records[entries[name]["target"]["entity_id"]]
        assert record["category"] == state
        assert result["change_record_id"] == record["id"]
    settings = results["settings-file"]
    assert settings["outcome"] == "rationale_review_needed"
    assert settings["source_state"] == "content_changed"
    assert settings["change_record_id"] is None  # S19 emits no file records
    assert settings["target_source_sha256_before"] != settings["target_source_sha256_after"]
    named = {
        f["mapping_id"]: f
        for f in mixed_drift["findings"]
        if f["kind"] == "rationale_review_needed"
    }
    assert set(named) == {"charge", "refund", "settings-file", "dual-second"}
    assert named["charge"]["change_record_id"] == results["charge"]["change_record_id"]
    # Control: the same source change with an edited document raises no finding.
    control = results["charge-doc-edited"]
    assert control["outcome"] == "no_finding" and control["decision_state"] == "document_changed"
    assert control["source_state"] == "body_changed"
    assert "charge-doc-edited" not in {f["mapping_id"] for f in mixed_drift["findings"]}
    drift.validate_drift(mixed_drift)


def test_adr_unchanged_source_has_no_drift_signal(world, change_set, mixed_drift):
    results = by_mapping(mixed_drift)
    named = {f["mapping_id"] for f in mixed_drift["findings"]}
    entries = {e["mapping_id"]: e for e in world.document()["mappings"]}
    records = {r["entity_id"] for r in change_set["changes"]}
    for name in ("summary", "constants-file", "summary-doc-edited"):
        result = results[name]
        assert result["outcome"] == "no_finding" and result["source_state"] == "unchanged"
        assert result["reason"] is None and result["change_record_id"] is None
        assert name not in named
        assert entries[name]["target"]["entity_id"] not in records
    summary = results["summary"]
    assert summary["decision_state"] == "document_unchanged"
    assert summary["target_source_sha256_before"] == summary["target_source_sha256_after"]
    assert summary["target_source_sha256_before"] == sha(
        WORLD_TEXTS["before"]["src/reports.py"].encode()
    )
    assert results["summary-doc-edited"]["decision_state"] == "document_changed"
    # The target is confirmed on the after lease: the entity exists with one definition.
    target = entries["summary"]["target"]["entity_id"]
    with ExitStack() as stack:
        reader = runtime.open_active(stack, world.after)
        assert reader.db.execute(runtime.ENTITY_SQL, (target,)).fetchone() is not None
        occurrences = [
            json.loads(p)
            for (p,) in reader.db.execute(
                "SELECT payload FROM occurrences WHERE path=?", ("src/reports.py",)
            )
        ]
    definitions = [o for o in occurrences if o["entity_id"] == target and o["kind"] == "definition"]
    assert len(definitions) == 1
    constants = results["constants-file"]
    assert constants["target_source_sha256_before"] == constants["target_source_sha256_after"]


def test_adr_missing_target_is_unresolved(world, change_set, mixed_drift, tmp_path):
    raw = world.raw("mapping-unresolved.json")
    overlay = am.map_rationale(raw, world.before)
    assert overlay["counts"] == {"mappings": 8, "resolved": 0, "unresolved": 8}
    found = {u["mapping_id"]: u for u in overlay["unresolved"]}
    assert len(found) == 8
    inputs = {e["mapping_id"]: e for e in world.document("mapping-unresolved.json")["mappings"]}
    for name, reason in EXPECTED["import"].items():
        record = found[name]
        assert record["type"] == "unresolved_mapping" and record["stage"] == "mapping_import"
        assert record["reason"] == reason
        entry = inputs[name]
        assert record["document"] == entry["document"]
        assert record["span"] == entry["span"]
        assert record["target"] == entry["target"]
        assert record["decision_id"] == entry["decision_id"]
    reachable = set(am.IMPORT_REASONS) - {"target_kind_unsupported", "target_definition_ambiguous"}
    assert {u["reason"] for u in overlay["unresolved"]} == reachable
    # A same-name entity exists, yet the descriptor mismatch is never rebound to it.
    with ExitStack() as stack:
        reader = runtime.open_active(stack, world.before)
        named = [
            json.loads(p)
            for (p,) in reader.db.execute(
                "SELECT payload FROM entities WHERE path=?", ("src/pricing.py",)
            )
        ]
    assert any(e["name"] == "charge" for e in named)
    assert found["path-mismatch"]["reason"] == "target_descriptor_mismatch"
    out = tmp_path / "unresolved.json"
    result = cli_run(*map_args(world, world.mapping_file("u.json", raw), out))
    assert result.exit_code == 0, result.stderr
    assert json.loads(out.read_bytes())["counts"]["unresolved"] == 8
    # Drift: removed endpoints stay unresolved and are never rebound to a candidate.
    results = by_mapping(mixed_drift)
    for name in ("retire", "legacy-file", "rename"):
        assert results[name]["outcome"] == "unresolved_mapping"
        assert results[name]["reason"] == "target_removed"
    entries = {e["mapping_id"]: e for e in world.document()["mappings"]}
    candidate = next(
        c
        for c in change_set["candidates"]
        if c["before_entity_id"] == entries["rename"]["target"]["entity_id"]
    )
    assert results["rename"]["candidate_ids"] == [candidate["id"]]
    assert results["retire"]["candidate_ids"] == []
    with ExitStack() as stack:
        after_rows = [
            json.loads(p)
            for (p,) in runtime.open_active(stack, world.after).db.execute(
                "SELECT payload FROM entities"
            )
        ]
    replacements = [
        candidate["after_entity_id"],
        *(e["id"] for e in after_rows if e["path"] == "src/legacy_v2.py"),
    ]
    assert len(replacements) == 3
    overlay_text = am.canonical_bytes(am.map_rationale(world.raw(), world.before)).decode()
    drift_text = drift.canonical_bytes(mixed_drift).decode()
    assert _ids_in(overlay_text + drift_text, *replacements) == []
    assert results["summary-legacy-doc"]["reason"] == "document_not_in_snapshot"
    assert results["summary-legacy-doc"]["decision_state"] == "document_missing"
    # Import-unresolved mappings appear unchanged in the drift document.
    assert {n for n, r in results.items() if r["stage"] == "mapping_import"} == set(
        EXPECTED["import"]
    )


def _runs(world, tmp_path):
    first, second = tmp_path / "one.json", tmp_path / "two.json"
    mapping = world.mapping_file("replay.json", world.mixed())
    for out in (first, second):
        assert cli_run(*map_args(world, mapping, out)).exit_code == 0
    drift_first, drift_second = tmp_path / "d1.json", tmp_path / "d2.json"
    assert cli_run(*drift_args(world, mapping, drift_first)).exit_code == 0
    permuted = [
        "rationale-drift",
        "--output",
        str(drift_second),
        "--after-graph-root",
        str(world.after.graph_root),
        "--before-graph-root",
        str(world.before.graph_root),
        "--repo",
        str(world.repo),
        "--mapping",
        str(mapping),
    ]
    assert cli_run(*permuted).exit_code == 0
    return first, second, drift_first, drift_second


def test_adr_reimport_is_idempotent(world, tmp_path):
    raw = world.mixed()
    one, two = am.map_rationale(raw, world.before), am.map_rationale(raw, world.before)
    assert am.canonical_bytes(one) == am.canonical_bytes(two)
    first = drift.rationale_drift(raw, world.before, world.after)
    second = drift.rationale_drift(raw, world.before, world.after)
    assert drift.canonical_bytes(first) == drift.canonical_bytes(second)
    assert first["overlay_id"] == one["id"]
    map_one, map_two, drift_one, drift_two = _runs(world, tmp_path)
    assert map_one.read_bytes() == map_two.read_bytes() == am.canonical_bytes(one)
    assert drift_one.read_bytes() == drift_two.read_bytes() == drift.canonical_bytes(first)
    # A different process (and hash seed) writes the same bytes.
    mapping = world.mapping_file("seed.json", raw)
    for seed in ("1", "2"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        for command, expected in (
            (map_args(world, mapping, tmp_path / ("m" + seed)), am.canonical_bytes(one)),
            (drift_args(world, mapping, tmp_path / ("d" + seed)), drift.canonical_bytes(first)),
        ):
            done = subprocess.run(
                [sys.executable, "-m", "atlas_aci", *command], capture_output=True, env=env
            )
            assert done.returncode == 0, done.stderr
            assert (tmp_path / command[-1].rsplit("/", 1)[1]).read_bytes() == expected
    # A reordered mapping file changes only the digest and the document ids.
    document = world.document()
    reordered = encode({**document, "mappings": list(reversed(world.document()["mappings"]))})
    again = am.map_rationale(reordered, world.before)
    plain = am.map_rationale(world.raw(), world.before)
    for key in ("resolved", "unresolved"):
        assert again[key] == plain[key]
    assert again["mapping_digest"] != plain["mapping_digest"] and again["id"] != plain["id"]
    swapped = drift.rationale_drift(reordered, world.before, world.after)
    original = drift.rationale_drift(world.raw(), world.before, world.after)
    assert swapped["results"] == original["results"]
    assert swapped["findings"] == original["findings"]
    assert swapped["id"] != original["id"] and swapped["overlay_id"] == again["id"]


def test_adr_reimport_survives_a_rebuild_at_the_same_paths(tmp_path):
    world = build_world(tmp_path / "w", extra_roots=False)
    first = drift.canonical_bytes(drift.rationale_drift(world.raw(), world.before, world.after))
    first_map = am.canonical_bytes(am.map_rationale(world.raw(), world.before))
    for root in (world.before.graph_root, world.after.graph_root):
        shutil.rmtree(root)
    clean = {k: None for k in WORLD_TEXTS["after"] if k not in WORLD_TEXTS["before"]}
    write_tree(world.repo, {**clean, **WORLD_TEXTS["before"]})
    build(world.repo, world.before.graph_root)
    write_tree(world.repo, WORLD_TEXTS["after"])
    build(world.repo, world.after.graph_root)
    assert (
        drift.canonical_bytes(drift.rationale_drift(world.raw(), world.before, world.after))
        == first
    )
    assert am.canonical_bytes(am.map_rationale(world.raw(), world.before)) == first_map


def test_adr_incomparable_snapshot_is_unknown(world, mixed_drift, tmp_path):
    results = by_mapping(mixed_drift)
    for name in ("fragile", "fragile-file"):
        assert results[name]["outcome"] == "unknown_target_history"
        assert results[name]["reason"] == "extraction_failed_after"
        assert results[name]["outcome"] != "unresolved_mapping"
    assert results["limits"]["reason"] == "fingerprint_unsupported_kind"
    mapping = world.mapping_file("mixed.json", world.mixed())
    for after, reason in (
        (world.config, "configuration_mismatch"),
        (world.nongit, "identity_kind_mismatch"),
    ):
        out = tmp_path / (reason + ".json")
        result = cli_run(*drift_args(world, mapping, out, after))
        assert result.exit_code == 0, result.stderr
        document = json.loads(out.read_bytes())
        assert document["comparison"] == {
            "state": "rejected",
            "change_set_id": None,
            "reason": reason,
        }
        drift.validate_drift(document)
        found = by_mapping(document)
        compared = [r for r in found.values() if r["stage"] == "comparison"]
        assert len(compared) == 22
        for item in compared:
            assert item["outcome"] == "unknown_target_history"
            assert item["reason"] == "comparison_rejected" and item["detail"] == reason
            assert item["decision_state"] == "not_compared"
            assert item["source_state"] == "unknown"
        imported = {n: r for n, r in found.items() if r["stage"] == "mapping_import"}
        assert imported == {n: r for n, r in results.items() if r["stage"] == "mapping_import"}
        assert result.stdout_bytes == am.canonical_bytes(am.summary_document(document))


def test_adr_analysis_never_edits_decisions(world, tmp_path):
    out_dir = world.outputs("edits")
    mapping = world.mapping_file("inventory.json", world.mixed())
    wrong = world.mapping_file("wrong.json", world.raw(snapshot_id="f" * 64))
    watched = [*world.roots, mapping.parent]
    adr_files = {
        path: (world.repo / path).read_bytes()
        for path in WORLD_TEXTS["after"]
        if path.startswith("docs/") and WORLD_TEXTS["after"][path] is not None
    }
    cases = [
        (map_args(world, mapping, out_dir / "a.json"), 0),
        (map_args(world, wrong, out_dir / "b.json"), 1),
        (drift_args(world, mapping, out_dir / "c.json"), 0),
        (drift_args(world, mapping, out_dir / "d.json", world.config), 0),
        (
            [
                "rationale-drift",
                "--mapping",
                str(mapping),
                "--repo",
                str(world.repo),
                "--before-graph-root",
                str(world.after.graph_root),
                "--after-graph-root",
                str(world.before.graph_root),
                "--output",
                str(out_dir / "e.json"),
            ],
            1,
        ),
        (map_args(world, mapping, world.repo / "inside.json"), 2),
    ]
    for args, code in cases:
        before = stat_inventory(*watched)
        result = cli_run(*args)
        assert result.exit_code == code, (args, result.stderr)
        assert stat_inventory(*watched) == before
    produced = sorted(p.name for p in out_dir.iterdir())
    assert produced == ["a.json", "c.json", "d.json"]
    assert temp_leftovers(out_dir) == []
    assert not (world.repo / "inside.json").exists()
    assert not (world.repo / ".atlas").exists()
    for path, data in adr_files.items():
        assert (world.repo / path).read_bytes() == data
    assert (world.repo / ADR).read_bytes() == WORLD_TEXTS["before"][ADR].encode()
