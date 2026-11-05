"""ACI-S27: import one local OTLP JSON trace artifact into a separate runtime overlay.

Every repository is indexed for real (git repository, external graph root); nothing about the
behaviour under test is mocked. The only instrumentation is a delegating counter on
`SourceLease.read` and a sqlite trace callback.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from contextlib import ExitStack

import pytest
from test_s27_support import (
    EDGES,
    EXPECTED,
    FX,
    ROOT,
    SRC,
    T1,
    basic,
    example,
    key_walk,
    make_world,
    manifest_for,
    observation,
    raw_of,
    run_cli,
    sentinels,
    sid,
    write_inputs,
)

from atlas_aci.importers import otel_json as otel
from atlas_aci.overlays import runtime
from atlas_aci.storage import evidence as ev
from atlas_aci.storage.snapshots import SourceLease


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return make_world(tmp_path_factory.mktemp("s27"))


@pytest.fixture(scope="module")
def matched(world):
    raw = raw_of(manifest_for(basic(), world.snapshot_id))
    return raw, runtime.import_otel(world.side, raw, basic())


@pytest.fixture(scope="module")
def mismatched(world):
    raw = (FX / "manifest-example.json").read_bytes()
    return raw, runtime.import_otel(world.side, raw, basic())


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_otel_observation_has_snapshot_provenance(world, matched, tmp_path):
    raw, overlay = matched
    artifact = basic()
    assert T1.upper() in artifact.decode() and sid(1).upper() in artifact.decode()
    s1 = observation(overlay, T1, 1)
    assert s1["run_id"] == "run-2026-11-05-001"
    assert (s1["trace_id"], s1["span_id"]) == (T1, sid(1))  # lowercased from the input
    assert s1["artifact_sha256"] == digest(artifact)
    with ExitStack() as stack:
        leased = runtime.open_active(stack, world.side).snapshot_id
    assert s1["snapshot_id"] == leased == world.snapshot_id
    assert s1["id"] == otel.observation_id(s1["run_id"], T1, sid(1))
    assert len(overlay["observations"]) == 13
    for item in overlay["observations"]:
        assert item["run_id"] == "run-2026-11-05-001"
        assert item["artifact_sha256"] == digest(artifact)
        assert item["snapshot_id"] == world.snapshot_id
        assert item["id"] == otel.observation_id(item["run_id"], item["trace_id"], item["span_id"])
    assert overlay["snapshot"] == {
        "declared_id": world.snapshot_id,
        "supplied_id": world.snapshot_id,
        "matched": True,
    }
    assert overlay["importer"]["version"] == "atlas-otel-json-importer.v1"
    assert overlay["manifest"]["sha256"] == digest(raw)
    assert overlay["artifact"] == {"sha256": digest(artifact), "bytes": len(artifact)}
    assert s1["linkage"]["classification"] == "linked_symbol"
    symbol = s1["linkage"]["symbol"]
    assert (symbol["name"], symbol["kind"], symbol["line_start"], symbol["line_end"]) == (
        "handle",
        "function",
        2,
        3,
    )
    assert ev.hash_value(symbol["entity_id"])
    assert s1["linkage"]["path"] == "app/service.py"
    assert s1["linkage"]["file_entity_id"] == ev.file_entity("app/service.py", "python")["id"]
    assert observation(overlay, T1, 3)["linkage"]["classification"] == "linked_file"
    got = {}
    for item in overlay["observations"]:
        symbol = item["linkage"]["symbol"]
        got[(item["trace_id"], int(item["span_id"][-2:], 16))] = (
            item["linkage"]["classification"],
            symbol["name"] if symbol else None,
        )
    assert got == EXPECTED
    # The CLI writes the same bytes and prints the summary document.
    manifest, artifact_path = write_inputs(tmp_path, artifact, raw)
    output = tmp_path / "overlay.json"
    result = run_cli(world, manifest, artifact_path, output)
    assert result.exit_code == 0, result.stderr
    assert output.read_bytes() == runtime.canonical_bytes(overlay)
    printed = json.loads(result.stdout_bytes)
    assert printed["overlay_id"] == overlay["overlay_id"] and printed["run_id"] == s1["run_id"]
    assert printed["snapshot"]["matched"] is True and printed["summary"] == overlay["summary"]
    assert result.stdout_bytes == runtime.canonical_bytes(runtime.summary_document(overlay))


def test_otel_parent_relation_preserves_observed_semantics(matched, mismatched):
    for _raw, overlay in (matched, mismatched):
        relations = overlay["relations"]
        assert len(relations) == len(EDGES) == 9
        assert {(r["parent_span_id"], r["child_span_id"]) for r in relations} == {
            (sid(a), sid(b)) for a, b in EDGES
        }
        by_id = {o["id"]: o for o in overlay["observations"]}
        assert by_id
        for relation in relations:
            assert relation["relation"] == "observed_parent_of"
            assert relation["semantics"] == "observed_span_nesting"
            assert relation["type"] == "runtime_relation"
            parent, child = by_id[relation["parent_id"]], by_id[relation["child_id"]]
            assert parent["trace_id"] == child["trace_id"] == relation["trace_id"] == T1
            assert parent["span_id"] == relation["parent_span_id"]
            assert child["span_id"] == relation["child_span_id"]
            assert relation["id"] == otel.relation_id(relation["parent_id"], relation["child_id"])
        children = {r["child_span_id"] for r in relations}
        assert sid(10) not in children and sid(13) not in children
        assert overlay["summary"]["parent_not_in_artifact"] == 2
        assert overlay["summary"]["root_spans"] == 2
        assert overlay["summary"]["relations_total"] == 9
        assert "call" not in json.dumps(relations).lower()
    assert "observed_parent_of" not in ev.RELATIONS | ev.REFERENCE_RELATIONS
    assert matched[1]["relations"] == mismatched[1]["relations"]


def test_otel_build_mismatch_is_unlinked(world, matched, mismatched, monkeypatch):
    raw, overlay = mismatched
    declared = example()["build"]["snapshot_id"]
    assert overlay["snapshot"] == {
        "declared_id": declared,
        "supplied_id": world.snapshot_id,
        "matched": False,
    }
    assert declared != world.snapshot_id
    broken = {(T1, 4), (T1, 5)}
    malformed = {(T1, 11)}
    seen = 0
    for (trace, number), (_, _name) in EXPECTED.items():
        item = observation(overlay, trace, number)
        if (trace, number) in broken:
            assert item["linkage"]["classification"] == "unlinked_missing_source_location"
        elif (trace, number) in malformed:
            assert item["linkage"]["classification"] == "unlinked_malformed_source_location"
        else:
            assert item["linkage"]["classification"] == "unlinked_build_mismatch"
            seen += 1
        assert item["snapshot_id"] is None
        assert item["linkage"]["path"] is None and item["linkage"]["file_entity_id"] is None
        assert item["linkage"]["symbol"] is None
    assert seen == 10
    reasons = overlay["summary"]["unlinked_by_reason"]
    assert reasons["build_mismatch"] == 10
    assert reasons["missing_source_location"] == 2 and reasons["malformed_source_location"] == 1
    assert overlay["summary"]["linked_symbol"] == overlay["summary"]["linked_file"] == 0

    reads: list[tuple] = []
    original = SourceLease.read

    def counting(self, *args, **kwargs):
        reads.append(args)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(SourceLease, "read", counting)

    def execute(manifest_raw):
        manifest = otel.parse_manifest(manifest_raw)
        spans = otel.parse_artifact(basic(), manifest, otel.normalize_limits())
        meta = {"sha256": digest(basic()), "bytes": len(basic())}
        statements: list[str] = []
        with ExitStack() as stack:
            reader = runtime.open_active(stack, world.side)
            before = len(reads)  # lease acquisition itself authenticates the packed sources
            reader.db.set_trace_callback(statements.append)
            built = runtime.build_overlay(manifest, digest(manifest_raw), meta, spans, reader)
            reader.db.set_trace_callback(None)
        return built, statements, len(reads) - before

    built, statements, count = execute(raw)
    assert built == overlay and statements == [] and count == 0
    control, statements, count = execute(matched[0])
    assert control == matched[1]
    assert statements and count > 0
    assert all(s.lstrip().upper().startswith("SELECT") for s in statements)


def test_otel_missing_source_is_counted(world, matched, mismatched, tmp_path):
    for _raw, overlay in (matched, mismatched):
        summary = overlay["summary"]
        assert summary["unlinked_by_reason"]["missing_source_location"] == 2
        assert summary["unlinked_by_reason"]["malformed_source_location"] == 1
        assert summary["source_locations"] == {
            "present": 10,
            "missing_source_location": 2,
            "malformed_source_location": 1,
        }
        assert summary["legacy_source_attributes_ignored"] == 1
        assert summary["linked_symbol"] + summary["linked_file"] + summary["unlinked_total"] == 13
        assert sum(summary["unlinked_by_reason"].values()) == summary["unlinked_total"]
        for number in (4, 5):
            item = observation(overlay, T1, number)
            assert item["source"] == {"declared_path": None, "line": None}
            assert item["linkage"]["classification"] == "unlinked_missing_source_location"
    manifest, artifact = write_inputs(tmp_path, basic(), matched[0])
    result = run_cli(world, manifest, artifact, tmp_path / "o.json")
    assert result.exit_code == 0, result.stderr
    printed = json.loads(result.stdout_bytes)["summary"]
    assert printed["unlinked_by_reason"]["missing_source_location"] == 2
    assert printed["source_locations"]["missing_source_location"] == 2
    # Moving s1's `code.file.path` to the legacy spelling adds a missing location.
    document = json.loads(basic())
    first = document["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert first["attributes"][0]["key"] == "code.file.path"
    first["attributes"][0]["key"] = "code.filepath"
    variant = json.dumps(document).encode()
    overlay = runtime.import_otel(
        world.side, raw_of(manifest_for(variant, world.snapshot_id)), variant
    )
    assert overlay["summary"]["unlinked_by_reason"]["missing_source_location"] == 3
    assert overlay["summary"]["legacy_source_attributes_ignored"] == 2
    assert overlay["summary"]["source_locations"]["missing_source_location"] == 3
    assert observation(overlay, T1, 1)["linkage"]["classification"] == (
        "unlinked_missing_source_location"
    )


def run_module(args, seed, cwd):
    env = {**os.environ, "PYTHONHASHSEED": str(seed)}
    return subprocess.run(
        [sys.executable, "-m", "atlas_aci", *args],
        capture_output=True,
        env=env,
        cwd=cwd,
        check=False,
    )


def test_otel_reimport_is_idempotent(world, matched, tmp_path):
    raw, overlay = matched
    again = runtime.import_otel(world.side, raw, basic())
    assert runtime.canonical_bytes(again) == runtime.canonical_bytes(overlay)
    manifest, artifact = write_inputs(tmp_path / "a", basic(), raw)
    first = run_cli(world, manifest, artifact, tmp_path / "out1.json")
    second = run_cli(world, manifest, artifact, tmp_path / "out2.json")
    rerun = run_cli(world, manifest, artifact, tmp_path / "out1.json")
    other_manifest, other_artifact = write_inputs(tmp_path / "elsewhere", basic(), raw, stem="x")
    moved = run_cli(world, other_manifest, other_artifact, tmp_path / "out3.json")
    results = [first, second, rerun, moved]
    assert all(r.exit_code == 0 for r in results), [r.stderr for r in results]
    expected = runtime.canonical_bytes(overlay)
    assert [tmp_path / n for n in ("out1.json", "out2.json", "out3.json")]
    for name in ("out1.json", "out2.json", "out3.json"):
        assert (tmp_path / name).read_bytes() == expected
    assert len({r.stdout_bytes for r in results}) == 1
    parsed = json.loads((tmp_path / "out1.json").read_bytes())
    assert parsed["observations"] == overlay["observations"]
    assert parsed["overlay_id"] == overlay["overlay_id"]
    for item in parsed["observations"]:
        assert item["id"] == otel.observation_id(item["run_id"], item["trace_id"], item["span_id"])
    runtime.validate_overlay(parsed)
    # Two processes with different hash seeds produce the same bytes.
    args = [
        "import-otel",
        "--repo",
        str(world.repo),
        "--graph-root",
        str(world.root),
        "--manifest",
        str(manifest),
        "--output",
        str(tmp_path / "seeded.json"),
        str(artifact),
    ]
    one = run_module(args, 1, tmp_path)
    assert one.returncode == 0, one.stderr
    seeded_one = (tmp_path / "seeded.json").read_bytes()
    two = run_module(args, 2, tmp_path)
    assert two.returncode == 0, two.stderr
    assert (tmp_path / "seeded.json").read_bytes() == seeded_one == expected
    assert one.stdout == two.stdout == first.stdout_bytes
    # Decided: what the overlay id binds.
    pretty = json.dumps(json.loads(raw), indent=4).encode()
    assert pretty != raw
    reformatted = runtime.import_otel(world.side, pretty, basic())
    assert reformatted["observations"] == overlay["observations"]
    assert reformatted["relations"] == overlay["relations"]
    assert reformatted["manifest"]["sha256"] == digest(pretty) != overlay["manifest"]["sha256"]
    assert reformatted["overlay_id"] != overlay["overlay_id"]
    renamed = runtime.import_otel(
        world.side,
        raw_of(manifest_for(basic(), world.snapshot_id, run={"id": "run-other"})),
        basic(),
    )
    assert {(o["trace_id"], o["span_id"]) for o in renamed["observations"]} == {
        (o["trace_id"], o["span_id"]) for o in overlay["observations"]
    }
    assert {o["id"] for o in renamed["observations"]}.isdisjoint(
        {o["id"] for o in overlay["observations"]}
    )
    roots = [*example()["source_roots"], {"from": "/other", "to": "elsewhere"}]
    rooted = runtime.import_otel(
        world.side, raw_of(manifest_for(basic(), world.snapshot_id, source_roots=roots)), basic()
    )
    assert rooted["overlay_id"] != overlay["overlay_id"]
    assert rooted["observations"] == overlay["observations"]


ALLOWED_OBSERVATION_KEYS = {
    "id",
    "type",
    "run_id",
    "trace_id",
    "span_id",
    "parent_span_id",
    "artifact_sha256",
    "snapshot_id",
    "start_time_unix_nano",
    "end_time_unix_nano",
    "status_code",
    "source",
    "linkage",
    "evidence_coverage",
}
FORBIDDEN_KEYS = {"name", "kind", "attributes", "events", "links", "resource", "scope"}


def test_otel_attributes_are_allowlisted(world, tmp_path):
    artifact = sentinels()
    for number in range(1, 23):
        assert f"S27SENTINEL{number:02d}".encode() in artifact
    raw = raw_of(manifest_for(artifact, world.snapshot_id))
    overlay = runtime.import_otel(world.side, raw, artifact)
    data = runtime.canonical_bytes(overlay)
    assert len(overlay["observations"]) == 1
    manifest, artifact_path = write_inputs(tmp_path, artifact, raw)
    result = run_cli(world, manifest, artifact_path, tmp_path / "o.json")
    assert result.exit_code == 0, result.stderr
    paths = runtime.canonical_bytes(runtime.runtime_paths(overlay))
    assert b"S27SENTINEL" not in data
    assert b"S27SENTINEL" not in result.stdout_bytes and b"S27SENTINEL" not in paths
    assert b"S27SENTINEL" not in (tmp_path / "o.json").read_bytes()
    assert b"S27ALLOWED" in data  # the positive control: the allowlisted path is kept
    item = overlay["observations"][0]
    assert item["source"] == {"declared_path": "/srv/app/S27ALLOWED/x.py", "line": 3}
    assert overlay["summary"]["legacy_source_attributes_ignored"] == 1
    assert set(item) == ALLOWED_OBSERVATION_KEYS
    assert set(item["source"]) == {"declared_path", "line"}
    assert set(item["linkage"]) == {"classification", "path", "file_entity_id", "symbol"}
    assert set(overlay) == {
        "format",
        "overlay_id",
        "importer",
        "run",
        "artifact",
        "manifest",
        "producer",
        "build",
        "source_roots",
        "snapshot",
        "coverage",
        "summary",
        "limitations",
        "observations",
        "relations",
    }
    assert set(overlay["importer"]) == {"name", "version", "profile"}
    assert set(overlay["producer"]) == {"name", "version", "attestation"}
    assert set(overlay["run"]) == {"id"} and set(overlay["build"]) == {"id"}
    assert set(overlay["artifact"]) == {"sha256", "bytes"}
    assert set(overlay["manifest"]) == {"format", "sha256"}
    # Forbidden keys: every observation except the symbol definition, and every relation.
    full = runtime.import_otel(
        world.side,
        raw_of(manifest_for(basic(), world.snapshot_id)),
        basic(),
    )
    symbols = [o["linkage"]["symbol"] for o in full["observations"] if o["linkage"]["symbol"]]
    assert symbols
    for symbol in symbols:
        assert set(symbol) == {"name", "kind", "line_start", "line_end", "entity_id"}
    walked = 0
    for source in (overlay, full):
        for observation_ in source["observations"]:
            trimmed = json.loads(json.dumps(observation_))
            trimmed["linkage"]["symbol"] = None
            keys = {key for _where, key in key_walk(trimmed)}
            assert not keys & FORBIDDEN_KEYS, keys
            walked += 1
        for relation in source["relations"]:
            assert not {key for _w, key in key_walk(relation)} & FORBIDDEN_KEYS
    assert walked == 14
    assert full["relations"]
    assert {"name", "kind", "attributes", "events", "links", "resource", "scope"} == FORBIDDEN_KEYS


def paths_of(result):
    return {tuple(int(s[-2:], 16) for s in p["span_ids"]): p for p in result["paths"]}


def test_otel_sampling_limits_are_explicit(world, matched, mismatched, tmp_path):
    expected_coverage = {
        "evidence_coverage": "observed_sample",
        "completeness": "unknown",
        "sampling": "not_declared",
        "relation_semantics": "observed_span_nesting",
    }
    for _raw, overlay in (matched, mismatched):
        assert overlay["coverage"] == expected_coverage
        assert overlay["observations"] and overlay["relations"]
        assert all(o["evidence_coverage"] == "observed_sample" for o in overlay["observations"])
        assert all(r["evidence_coverage"] == "observed_sample" for r in overlay["relations"])
        assert overlay["summary"]["evidence_coverage"] == "observed_sample"
        for limitation in runtime.LIMITATIONS[:3]:
            assert limitation in overlay["limitations"]
        assert overlay["limitations"][:3] == [
            "span_nesting_is_observed_not_a_call_relation",
            "observations_are_a_sample_and_completeness_is_unknown",
            "one_declared_build_per_artifact",
        ]
        result = runtime.runtime_paths(overlay)
        assert result["evidence_coverage"] == "observed_sample"
        assert result["completeness"] == "unknown"
        assert result["semantics"] == "observed_span_nesting"
        assert result["paths"] and all(
            p["evidence_coverage"] == "observed_sample" for p in result["paths"]
        )
        assert result["limitations"][:3] == overlay["limitations"][:3]
    manifest, artifact = write_inputs(tmp_path, basic(), matched[0])
    cli_result = run_cli(world, manifest, artifact, tmp_path / "o.json")
    printed = json.loads(cli_result.stdout_bytes)
    assert printed["evidence_coverage"] == printed["summary"]["evidence_coverage"]
    assert printed["evidence_coverage"] == "observed_sample"
    result = runtime.runtime_paths(matched[1])
    chains = paths_of(result)
    assert set(chains) == {
        (1, 2, 3),
        (1, 4),
        (1, 5),
        (1, 7),
        (1, 8),
        (1, 9),
        (1, 11),
        (1, 12),
        (6,),
        (10,),
        (13,),
    }
    assert result["paths_total"] == 11 and result["paths_truncated"] is False
    assert [chains[k]["starts_at"] for k in ((1, 2, 3), (6,), (10,), (13,))] == [
        "trace_root",
        "trace_root",
        "parent_not_in_artifact",
        "parent_not_in_artifact",
    ]
    assert all(p["truncated"] is False for p in result["paths"])
    by_id = {o["id"]: o for o in matched[1]["observations"]}
    for entry in result["paths"]:
        assert [by_id[i]["span_id"] for i in entry["observation_ids"]] == entry["span_ids"]
    assert result["limits"] == {"max_paths": 1000, "max_path_length": 256}
    one = runtime.runtime_paths(matched[1], max_paths=1)
    assert len(one["paths"]) == 1 and one["paths_total"] == 11 and one["paths_truncated"] is True
    short = runtime.runtime_paths(matched[1], max_path_length=1)
    assert short["paths_total"] == 11 and short["paths_truncated"] is True
    assert {
        tuple(int(s[-2:], 16) for s in p["span_ids"]): p["truncated"] for p in short["paths"]
    } == {
        (1,): True,
        (6,): False,
        (10,): False,
        (13,): False,
    }
    two = runtime.runtime_paths(matched[1], max_path_length=2)
    assert (1, 2) in paths_of(two) and paths_of(two)[(1, 2)]["truncated"] is True
    assert (1, 4) in paths_of(two) and paths_of(two)[(1, 4)]["truncated"] is False
    assert two["paths_total"] == 11
    for module in ("query/impact.py", "protocol/impact.py"):
        text = (SRC / module).read_text()
        assert "runtime_observation" in text and "not_performed" in text
    assert ROOT.exists()
