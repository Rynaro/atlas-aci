"""ACI-S42 acceptance tests: shared-operation isolation and deployment evidence.

A module fixture runs the real recorder once: one ``serve-shared`` process driven by raw HTTP
clients, the interruption matrix over real ``atlas-aci jobs`` processes, the client and transport
cells (raw JSON-RPC and the mcp SDK) and the bounded workload. Nothing under test is mocked. Every
check that counts a zero is paired with a positive control, and each judgement has a teeth case
(an over-granted policy, a corrupted retained farm, a wrong expectation, a mutated record).
"""

from __future__ import annotations

import copy
import gzip
import importlib.metadata
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import test_s42_support as s
from test_s41_support import (
    lock_canary,  # noqa: F401  (session canary, autouse; fails, never skips)
)

CHECKS = (
    "records.complete",
    "environment.sdk_pinned",
    "isolation.unauthorized_zero",
    "isolation.positive_controls",
    "isolation.typed_denials",
    "isolation.concurrency",
    "isolation.ledger_teeth",
    "interrupt.all_recovered",
    "interrupt.teeth_detected",
    "retention.lease_respected",
    "compat.cells_recorded",
    "capacity.limits_held",
    "capacity.saturation_typed",
)
# Literal oracles (not computed by the atlas negotiation code the recorder also uses).
LISTED_REVISIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
UNLISTED = "2099-01-01"
COUNTER_OFFER = "2025-11-25"
EXPECTED_FACTS: dict[str, dict[str, Any]] = {
    "cancel_marker": {"exit": 4, "state": "cancelled", "cancel": "honoured", "published": False},
    "cancel_sigterm": {"exit": 4, "state": "cancelled", "cancel": "honoured", "published": False},
    "kill_checkpoint": {
        "killed": True,
        "interrupted": True,
        "interrupted_state": "running",
        "resumed_from": 2,
        "outcome": "built",
        "state": "completed",
    },
    "kill_mid_publish": {
        "killed": True,
        "interrupted": True,
        "interrupted_state": "running",
        "outcome": "built",
        "state": "completed",
    },
    "kill_post_replace": {
        "killed": True,
        "interrupted": True,
        "interrupted_state": "published",
        "outcome": "recovered",
        "state": "completed",
    },
    "kill_pre_receipt": {
        "killed": True,
        "interrupted": True,
        "interrupted_state": "captured",
        "outcome": "recovered",
        "state": "completed",
    },
    "gc_vs_lease": {
        "leased_retained": True,
        "holder_killed": True,
        "retired_after_release": True,
        "acquire_exit": 3,
        "acquire_code": "EXPIRED_REFERENCE",
    },
    "gc_vs_build": {"gc_exit": 75, "gc_code": "WRITER_BUSY", "state": "completed"},
}
VALIDATION_KEYS = (
    "status_current_matches",
    "no_invalid_jobs",
    "server_gid_matches",
    "server_expected_canary",
    "server_other_canary_absent",
    "acquire_ok",
    "control_gid_matches",
)


@dataclass
class Fresh:
    records: list[dict[str, Any]]
    doc: dict[str, Any]
    md: str
    work: Path

    @property
    def report(self) -> dict[str, Any]:
        report: dict[str, Any] = self.doc["report"]
        return report


@pytest.fixture(scope="module")
def fresh(tmp_path_factory: pytest.TempPathFactory) -> Fresh:
    """One real recorder run (every unit) plus its aggregation."""
    base = tmp_path_factory.mktemp("s42-fresh")
    code, tail = s.run_recorder(base / "raw", base / "work")
    assert code == 0, f"recorder exit {code}: {tail}"
    records = s.records_from(base / "raw")
    doc = s.aggregate(records)
    return Fresh(records, doc, s.aggregator().render_md(doc), base / "work")


@pytest.fixture(scope="module")
def committed() -> list[dict[str, Any]]:
    return s.committed_records()


def failed_line(md: str) -> str:
    lines = [line for line in md.splitlines() if line.startswith("Failed checks:")]
    assert len(lines) == 1, lines
    return lines[0]


def assert_blocked(records: list[dict[str, Any]], check: str) -> dict[str, Any]:
    """The aggregation is blocked and names ``check`` in json, in md and on the verdict line."""
    doc = s.aggregate(records)
    assert doc["report"]["verdict"] == "blocked"
    assert check in doc["report"]["failed_checks"], doc["report"]["failed_checks"]
    assert doc["report"]["checks"][check] in ("fail", "missing")
    md = s.aggregator().render_md(doc)
    assert check in failed_line(md)
    assert "Verdict: blocked" in md.splitlines()
    assert "Verdict: ready_for_operator_review" not in md
    result: dict[str, Any] = doc
    return result


# ------------------------------------------------------------------------------------ AC-001
def test_negative_isolation_suite(fresh: Fresh) -> None:
    """AC-S42-001: the unauthorized evidence access count is zero over the declared fixture."""
    manifest = s.manifest()
    main = s.find(fresh.records, "isolation", "main")
    revocation = s.find(fresh.records, "isolation", "revocation")
    teeth = s.find(fresh.records, "isolation", "teeth")
    assert {r["status"] for r in (main, revocation, teeth)} == {"complete"}
    rows = main["body"]["rows"]
    rev_rows = revocation["body"]["rows"]
    cases = {(c["phase"], c["id"]): c for c in manifest["isolation"]["cases"]}

    # The aggregator's count equals an independently coded count, and both are zero.
    report = fresh.report["isolation"]
    assert s.independent_unauthorized(manifest, rows) == []
    assert s.independent_unauthorized(manifest, rev_rows) == []
    assert report["unauthorized"]["main"] == 0 and report["unauthorized"]["revocation"] == 0
    assert report["unauthorized_rows"] == []
    assert len(rows) + len(rev_rows) >= 30 and report["rows_total"] == len(rows) + len(rev_rows)
    main_rows = [r for r in rows if r["phase"] == "main"]
    assert len({r["principal"] for r in main_rows if r["principal"]}) >= 4
    assert len({r["route"] for r in main_rows}) >= 2
    assert report["deny_rows"] == 18 and report["allow_rows"] == 22  # non-vacuous both ways

    # Every class has deny cases; every deny case has a control that is ok and hit its target.
    by_id = {r["case_id"]: r for r in [*rows, *rev_rows]}
    classes = {c["class"] for c in cases.values() if c["expected"] == "deny"}
    assert classes == set(manifest["isolation"]["classes"]) - {"control"}
    seen_classes = set()
    for (phase, cid), case in cases.items():
        if case["expected"] != "deny" or phase == "teeth":
            continue
        row, control = by_id[cid], by_id[case["control"]]
        seen_classes.add(row["class"])
        assert control["outcome"] == "ok", (cid, control["outcome"])
        assert case["target_domain"] in control["canary_hits"], (cid, control["canary_hits"])
        assert row["canary_hits"] == [] and row["witness_in_request"] is False
        # typed denials: the frozen outcomes, never an untyped or catch-all code
        assert row["outcome"] in case["expected_outcomes"], (cid, row["outcome"])
        assert not row["outcome"].endswith(":OTHER") and row["outcome"] != "http:other"
        assert row["outcome"] != "transport_error"
    assert seen_classes == classes
    assert "C" in by_id["M07"]["canary_hits"]  # the before-snapshot expansion is real

    # Server-side corroboration: allow carriers reach a tool_call line, deny carriers never do.
    carriers = [c for c in cases.values() if c["nonce_carrier"] and c["phase"] != "teeth"]
    assert carriers
    for case in carriers:
        row = by_id[case["id"]]
        assert row["server_seen"] is (case["expected"] == "allow"), case["id"]

    # Concurrency: client intervals overlapped, the server gate peak reached 3, and both held
    # requests spanned every main row (observed times; recorder-attested boolean).
    assert main["body"]["holds_spanned"] is True and main["body"]["server"]["peak_ge_3"] is True
    holds = [r for r in rows if r["phase"] == "hold"]
    assert len(holds) == 2 and main_rows
    first = min(r["observed"]["t_start_ms"] for r in main_rows)
    last = max(r["observed"]["t_end_ms"] for r in main_rows)
    assert all(
        h["observed"]["t_start_ms"] <= first and h["observed"]["t_end_ms"] >= last for h in holds
    )
    assert main["observed"]["max_client_overlap"] >= 3 and main["observed"]["server"]["peak"] >= 3

    # Revocation: own page and continue ok, then 403, another grant ok, restored policy still
    # refuses the old cursor, a fresh page is ok again.
    outcomes = {cid: by_id[cid]["outcome"] for cid in ("R1", "R2", "R3", "R4", "R5", "R6")}
    assert outcomes == {
        "R1": "ok",
        "R2": "ok",
        "R3": "http:403:repository_forbidden",
        "R4": "ok",
        "R5": "tool_error:EXPIRED_REFERENCE",
        "R6": "ok",
    }

    # Teeth: an over-granted policy makes the same ledger fire, by two independent clauses.
    teeth_rows = teeth["body"]["rows"]
    flagged = s.independent_unauthorized(manifest, teeth_rows)
    assert "D01" in flagged and report["unauthorized"]["teeth"] >= 1
    first_deny = s.row_of(teeth, "D01")
    assert first_deny["outcome"] == "ok" and first_deny["canary_hits"] == ["A"]
    assert first_deny["server_seen"] is True
    swapped = [
        r
        for r in fresh.records
        if (r["kind"], r["unit"]) not in (("isolation", "main"), ("isolation", "teeth"))
    ]
    renamed = copy.deepcopy(teeth)
    renamed["unit"] = "main"
    blocked = assert_blocked([*swapped, renamed], "isolation.unauthorized_zero")
    assert blocked["report"]["isolation"]["unauthorized"]["main"] >= 1

    # The four honesty statements are in the rendered report.
    for statement in manifest["honesty"]:
        assert statement in fresh.md
    assert "zero within the enumerated cases" in fresh.md
    assert fresh.report["checks"]["isolation.unauthorized_zero"] == "pass"


# ------------------------------------------------------------------------------------ AC-002
def test_failure_recovery_suite(fresh: Fresh) -> None:
    """AC-S42-002: every recovered server exposes a valid completed generation."""
    environment = s.find(fresh.records, "environment", "main")["body"]
    assert environment["mcp_version"] == "1.30.0"
    canary = environment["lock_canary"]
    assert canary["second_fd_blocks"] and canary["process_blocks"] and canary["hardlink_same_inode"]
    assert canary["shared_folder_fs"] is False

    for case_id, facts in EXPECTED_FACTS.items():
        body = s.find(fresh.records, "interrupt", case_id)["body"]
        assert body["facts"] == facts, (case_id, body["facts"])
        assert body["valid"] is True, case_id
        validation = body["validation"]
        assert set(validation) == set(VALIDATION_KEYS)
        for key in VALIDATION_KEYS:  # all five conditions (the server one is not null: Day-1 f)
            assert validation[key] is True, (case_id, key, validation)
        rendered = json.dumps(body["facts"])
        assert "-9" not in rendered and "137" not in rendered  # normalized, never a raw exit code

    # Teeth 1: a farm corrupted by replace-by-copy is recorded invalid; the live store is intact.
    copied = s.find(fresh.records, "interrupt", "teeth_replace_by_copy")["body"]
    assert copied["valid"] is False and copied["validation"]["acquire_ok"] is False
    assert copied["live_unchanged"] is True
    assert copied["validation"]["server_expected_canary"] is True  # the live server is fine
    assert copied["facts"] == {"corrupted_farm_acquire_refused": True}
    # Teeth 2: a healthy namespace validated against the other tree trips the three server-side
    # conditions, so none of them can be an always-true placeholder.
    wrong = s.find(fresh.records, "interrupt", "teeth_wrong_expectation")["body"]
    assert wrong["valid"] is False
    for key in ("status_current_matches", "server_expected_canary", "control_gid_matches"):
        assert wrong["validation"][key] is False, key
    assert wrong["facts"] == {"validated_against": "T1"}

    for check in (
        "interrupt.all_recovered",
        "interrupt.teeth_detected",
        "retention.lease_respected",
    ):
        assert fresh.report["checks"][check] == "pass", check
    assert len(fresh.report["interruption"]["cases"]) == 10

    # The aggregator is not a rubber stamp: a flipped condition or fact fails the right check.
    records = s.mutated(
        fresh.records,
        "interrupt",
        "kill_checkpoint",
        lambda r: r["body"]["validation"].update(server_expected_canary=False),
    )
    assert_blocked(records, "interrupt.all_recovered")
    records = s.mutated(
        fresh.records,
        "interrupt",
        "gc_vs_build",
        lambda r: r["body"]["facts"].update(gc_exit=0),
    )
    assert_blocked(records, "retention.lease_respected")
    records = s.mutated(
        fresh.records,
        "interrupt",
        "teeth_wrong_expectation",
        lambda r: r["body"]["validation"].update(control_gid_matches=True),
    )
    assert_blocked(records, "interrupt.teeth_detected")


# ------------------------------------------------------------------------------------ AC-003
def test_compatibility_matrix(fresh: Fresh) -> None:
    """AC-S42-003: the report identifies each actually tested client and transport combination."""
    from atlas_aci.protocol.revisions import VERIFIED_SDK, negotiate

    manifest = s.manifest()
    cells = fresh.report["compatibility"]["cells"]
    units = sorted(r["unit"] for r in fresh.records if r["kind"] == "compat")
    assert sorted(c["id"] for c in cells) == units and len(units) >= 9
    installed = importlib.metadata.version("mcp")
    assert VERIFIED_SDK[1] == installed == "1.30.0"
    shared = {c["id"] for c in cells if c["shared"]}
    assert shared == {c["id"] for c in cells if c["transport"] == "shared-http"} and shared
    for cell in cells:
        requested = cell["requested"]
        assert cell["mcp_version"] == installed and cell["tools"]["count"] == 7, cell["id"]
        assert cell["call"]["outcome"] == "ok" and cell["call"]["canary_hit"] is True, cell["id"]
        if requested in LISTED_REVISIONS:  # literal oracle: a listed revision is answered as is
            assert cell["negotiated"] == requested and cell["expected"]["outcome"] == "supported"
            assert cell["sent_revision"] == requested
        elif requested == UNLISTED:  # the counter-offer; later requests send the negotiated one
            assert cell["negotiated"] == COUNTER_OFFER
            assert cell["expected"] == {"outcome": "counter_offer", "offered": COUNTER_OFFER}
            assert cell["sent_revision"] == COUNTER_OFFER
        else:
            assert requested == "sdk_default" and cell["client"] == "mcp-sdk"
            assert cell["negotiated"] == COUNTER_OFFER  # the SDK latest revision
        negotiation = negotiate(UNLISTED if requested == "sdk_default" else requested)
        if requested != "sdk_default":
            assert cell["negotiated"] == negotiation.offered  # the atlas code agrees too
    by_id = {c["id"]: c for c in cells}
    assert by_id[f"raw.shared-http.{UNLISTED}"]["requested"] == UNLISTED
    assert {c["id"] for c in cells if c["client"] == "mcp-sdk"} == {
        "sdk.stdio.sdk_default",
        "sdk.http.sdk_default",
        "sdk.shared-http.sdk_default",
    }
    assert {c["transport"] for c in cells if not c["shared"]} == {"stdio", "http"}

    # Hosts are not cells: each is listed untested with its reason.
    hosts = fresh.report["compatibility"]["hosts"]
    assert [h["id"] for h in hosts] == ["claude-code", "github-copilot", "cursor"]
    assert {h["status"] for h in hosts} == {"untested"}
    assert {h["reason"] for h in hosts} == {"no_real_host_run"}
    assert not {h["id"] for h in hosts} & set(by_id)
    assert (
        "untested" in fresh.md and "No real host (Claude Code, GitHub Copilot, Cursor)" in fresh.md
    )
    assert manifest["compatibility"]["hosts"] == hosts

    # Negative control on a never-cut unit: dropping a recorded cell removes it and blocks.
    dropped = s.without(fresh.records, "compat", "raw.shared-http.2025-11-25")
    doc = assert_blocked(dropped, "compat.cells_recorded")
    assert "records.complete" in doc["report"]["failed_checks"]
    assert "raw.shared-http.2025-11-25" not in {
        c["id"] for c in doc["report"]["compatibility"]["cells"]
    }
    assert {"id": "raw.shared-http.2025-11-25", "reason": "no_record"} in doc["report"][
        "compatibility"
    ]["untested_cells"]
    wrong = s.mutated(
        fresh.records,
        "compat",
        "raw.shared-http.2025-06-18",
        lambda r: r["body"].update(negotiated="2024-11-05"),
    )
    assert_blocked(wrong, "compat.cells_recorded")


# ------------------------------------------------------------------------------------ AC-004
def test_capacity_record(fresh: Fresh) -> None:
    """AC-S42-004: one workload record holds the configured limits and the observed use."""
    manifest = s.manifest()
    capacity = fresh.report["capacity"]
    assert list(capacity) == ["workload"]
    workload = capacity["workload"]
    assert workload["limits"] == {
        "listen": manifest["capacity"]["listen"],
        "limits": manifest["capacity"]["limits"],
    }
    assert workload["workload"] == manifest["capacity"]["workload"]
    assert all(workload["invariants"].values()) and len(workload["invariants"]) == 7
    assert all(workload["saturation"].values()) and len(workload["saturation"]) == 5
    observed = workload["observed"]
    latency = observed["latency_ms"]
    assert latency["n"] == 72 == 6 * 12
    assert 0 <= latency["p50"] <= latency["p95"] <= latency["max"]
    assert observed["server"]["peak"] >= 1 and observed["server"]["rejected"] >= 1
    assert observed["attempts"] >= 72
    sources = {k: observed[k]["source"] for k in ("memory", "fds", "rusage", "server")}
    assert set(sources.values()) <= {
        "proc_status",
        "proc_fd",
        "wait4",
        "stopped_line",
        "unavailable",
    }
    assert sources["server"] == "stopped_line"
    if sys.platform.startswith("linux"):
        assert sources["memory"] == "proc_status" and sources["fds"] == "proc_fd"
        assert sources["rusage"] == "wait4" and observed["rusage"]["unit"] == "kib"
        assert observed["memory"]["vm_hwm_kib"] > 0 and observed["fds"]["max"] > 0
    assert fresh.report["checks"]["capacity.limits_held"] == "pass"
    assert fresh.report["checks"]["capacity.saturation_typed"] == "pass"
    assert "Observed (excluded from canonical_sha256)" in fresh.md

    # Teeth: a failed invariant, a changed limit and a changed workload each fail the check.
    for change in (
        lambda r: r["body"]["invariants"].update(server_peak_within_limit=False),
        lambda r: r["body"]["invariants"].update(all_calls_completed=False),
        lambda r: r["body"]["limits"]["listen"].update(max_inflight=5),
        lambda r: r["body"]["workload"].update(calls_per_client=11),
    ):
        assert_blocked(s.mutated(fresh.records, "capacity", "main", change), "capacity.limits_held")
    # Exactly one record: a duplicate is refused with a typed error, never merged.
    aggregator = s.aggregator()
    duplicate = [*fresh.records, copy.deepcopy(s.find(fresh.records, "capacity", "main"))]
    with pytest.raises(aggregator.ReadinessInputError) as caught:
        s.aggregate(duplicate)
    assert caught.value.code == "duplicate_record"


# ------------------------------------------------------------------------------------ AC-005
def test_failed_gate_visible(committed: list[dict[str, Any]]) -> None:
    """AC-S42-005: one failed required check makes the shared-readiness verdict blocked."""
    aggregator = s.aggregator()
    control = s.aggregate(committed)
    assert control["report"]["verdict"] == "ready_for_operator_review"
    assert control["report"]["failed_checks"] == []
    assert set(control["report"]["checks"]) == set(CHECKS)
    assert all(state == "pass" for state in control["report"]["checks"].values())
    md = aggregator.render_md(control)
    assert "Verdict: ready_for_operator_review (evidence for an operator decision" in md
    assert failed_line(md) == "Failed checks: none"

    cases = [
        (  # a never-cut unit is missing
            s.without(committed, "interrupt", "kill_checkpoint"),
            ("records.complete", "interrupt.all_recovered"),
        ),
        (  # a deny case that succeeded
            s.mutated(
                committed,
                "isolation",
                "main",
                lambda r: s.row_of(r, "D01").update(outcome="ok"),
            ),
            ("isolation.unauthorized_zero",),
        ),
        (  # a foreign canary hit on a deny row, whatever its outcome
            s.mutated(
                committed,
                "isolation",
                "main",
                lambda r: s.row_of(r, "D03").update(canary_hits=["B"]),
            ),
            ("isolation.unauthorized_zero",),
        ),
        (  # a deny case answered with an untyped outcome
            s.mutated(
                committed,
                "isolation",
                "main",
                lambda r: s.row_of(r, "D05").update(outcome="http:other"),
            ),
            ("isolation.typed_denials",),
        ),
        (  # a control that no longer proves the witness is reachable
            s.mutated(
                committed,
                "isolation",
                "main",
                lambda r: s.row_of(r, "M01").update(canary_hits=[]),
            ),
            ("isolation.positive_controls",),
        ),
        (
            s.mutated(
                committed,
                "compat",
                "raw.shared-http.2025-03-26",
                lambda r: r["body"].update(negotiated="2025-06-18"),
            ),
            ("compat.cells_recorded",),
        ),
        (
            s.mutated(
                committed,
                "capacity",
                "main",
                lambda r: r["body"]["saturation"].update(gate_503_typed=False),
            ),
            ("capacity.saturation_typed",),
        ),
    ]
    for records, names in cases:
        doc = s.aggregate(records)
        assert doc["report"]["verdict"] == "blocked", names
        for name in names:
            assert name in doc["report"]["failed_checks"], (name, doc["report"]["failed_checks"])
            assert name in failed_line(aggregator.render_md(doc))
        assert "Verdict: blocked" in aggregator.render_md(doc).splitlines()

    # A failed record (no body) is a failed or missing check, never a KeyError.
    failed = s.mutated(
        committed,
        "capacity",
        "main",
        lambda r: (r.pop("body"), r.update(status="failed", failure={"code": "request_deadline"})),
    )
    doc = assert_blocked(failed, "capacity.limits_held")
    assert doc["report"]["checks"]["capacity.limits_held"] == "fail"
    assert doc["report"]["capacity"]["workload"] is None

    # Typed errors; the CLI exits 2 and writes no report.
    base = committed
    other_manifest = s.mutated(
        base, "environment", "main", lambda r: r.update(manifest_sha256="0" * 64)
    )
    unknown_kind = s.mutated(base, "environment", "main", lambda r: r.update(kind="mystery"))
    unknown_outcome = s.mutated(
        base, "isolation", "main", lambda r: s.row_of(r, "D01").update(outcome="shrug")
    )
    for records, code in (
        (other_manifest, "manifest_mismatch"),
        (unknown_kind, "unknown_kind"),
        (unknown_outcome, "vocabulary"),
    ):
        with pytest.raises(aggregator.ReadinessInputError) as caught:
            s.aggregate(records)
        assert caught.value.code == code


def test_cli_rejects_bad_input_without_writing(
    tmp_path: Path, committed: list[dict[str, Any]]
) -> None:
    """AC-S42-005 (typed errors at the CLI): exit 2, a typed code on stderr, nothing written."""
    foreign = s.mutated(
        committed, "environment", "main", lambda r: r.update(manifest_sha256="1" * 64)
    )
    env_record = s.find(committed, "environment", "main")
    bad_inputs = {
        "manifest_mismatch": lambda raw: s.write_dir(raw, foreign),
        "not_canonical": lambda raw: (
            s.write_dir(raw, committed),
            (raw / "environment.main.json.gz").write_bytes(
                gzip.compress(json.dumps(env_record, indent=2).encode(), mtime=0)
            ),
        ),
        "unknown_shape": lambda raw: (
            s.write_dir(raw, committed),
            (raw / "environment.main.json.gz").write_bytes(gzip.compress(b"[1, 2]", mtime=0)),
        ),
    }
    for code, build in bad_inputs.items():
        raw, out = tmp_path / f"raw-{code}", tmp_path / f"out-{code}"
        build(raw)
        done = s.run_aggregator(raw, out)
        assert done.returncode == 2, (code, done.returncode, done.stderr)
        assert f"s42_aggregate: {code}:" in done.stderr
        assert not out.exists() or list(out.iterdir()) == []
    garbage = tmp_path / "garbage"
    s.write_dir(garbage, committed)
    (garbage / "environment.main.json.gz").write_bytes(b"this is not gzip")
    done = s.run_aggregator(garbage, tmp_path / "out-garbage")
    assert done.returncode == 2 and "not_canonical" in done.stderr
    empty = tmp_path / "empty"
    empty.mkdir()
    done = s.run_aggregator(empty, tmp_path / "out-empty")
    assert done.returncode == 2 and "missing_input" in done.stderr


# ------------------------------------------------------------------------------------ AC-006
def digest_scan_text(doc: dict[str, Any]) -> str:
    aggregator = s.aggregator()
    return aggregator.canonical(aggregator.strip(doc["report"])).decode()


def test_shared_report_replay(
    fresh: Fresh, committed: list[dict[str, Any]], tmp_path: Path
) -> None:
    """AC-S42-006: reaggregating the same immutable records gives identical canonical metrics."""
    aggregator = s.aggregator()
    first = s.aggregate(committed)
    second = s.aggregate(committed)
    assert first == second and first["canonical_sha256"] == second["canonical_sha256"]
    json_text, md_text = aggregator.render_json(first), aggregator.render_md(first)
    assert json_text.encode() == s.REPORT_JSON.read_bytes()
    assert md_text.encode() == s.REPORT_MD.read_bytes()
    assert json.loads(s.REPORT_JSON.read_text())["canonical_sha256"] == first["canonical_sha256"]

    # The CLI into two directories: byte-identical to each other and to the committed files.
    one, two = tmp_path / "one", tmp_path / "two"
    for out in (one, two):
        done = s.run_aggregator(s.COMMITTED_RAW, out)
        assert done.returncode == 0, done.stderr
        assert done.stdout.split() == [first["canonical_sha256"], "ready_for_operator_review"]
    for name in ("readiness-report.json", "readiness-report.md"):
        assert (one / name).read_bytes() == (two / name).read_bytes()
        assert (one / name).read_bytes() == (s.EVIDENCE / name).read_bytes()
    assert s.run_aggregator(s.COMMITTED_RAW, s.EVIDENCE, "--verify").returncode == 0
    edited = tmp_path / "edited"
    edited.mkdir()
    for name in ("readiness-report.json", "readiness-report.md"):
        (edited / name).write_bytes((one / name).read_bytes())
    (edited / "readiness-report.md").write_bytes(
        (one / "readiness-report.md")
        .read_bytes()
        .replace(b"Failed checks: none", b"Failed checks: x")
    )
    assert s.run_aggregator(s.COMMITTED_RAW, edited, "--verify").returncode == 1
    assert (edited / "readiness-report.md").read_bytes().count(
        b"Failed checks: x"
    ) == 1  # untouched

    # Order does not matter; neither do observed and diagnostic values (at any depth).
    permuted = list(reversed(committed))
    assert s.aggregate(permuted)["canonical_sha256"] == first["canonical_sha256"]

    def scramble(value: Any) -> None:
        if isinstance(value, dict):
            for key in list(value):
                if key in ("observed", "diagnostic"):
                    value[key] = {"scrambled": [1, 2, 3]}
                else:
                    scramble(value[key])
        elif isinstance(value, list):
            for item in value:
                scramble(item)

    scrambled = copy.deepcopy(committed)
    scramble(scrambled)
    assert scrambled != committed
    assert s.aggregate(scrambled)["canonical_sha256"] == first["canonical_sha256"]
    # Control: a changed capacity limit changes the digest and blocks.
    limit = s.mutated(
        committed, "capacity", "main", lambda r: r["body"]["limits"]["limits"].update(max_records=1)
    )
    changed = s.aggregate(limit)
    assert changed["canonical_sha256"] != first["canonical_sha256"]
    assert changed["report"]["verdict"] == "blocked"
    # Compare canonical bytes, not gzip bytes: another deflate level reads identically.
    recompressed = tmp_path / "recompressed"
    recompressed.mkdir()
    for record in committed:
        name = f"{record['kind']}.{record['unit']}.json.gz"
        (recompressed / name).write_bytes(s.gz(record, level=1))
    assert (
        s.aggregate(s.records_from(recompressed))["canonical_sha256"] == first["canonical_sha256"]
    )

    # A fresh run is compared by invariants, not by digest equality with the committed one.
    assert fresh.report["verdict"] == "ready_for_operator_review"
    assert fresh.report["failed_checks"] == [] and set(fresh.report["checks"]) == set(CHECKS)
    assert fresh.report["manifest_sha256"] == first["report"]["manifest_sha256"]
    assert [i["name"] for i in fresh.report["inputs"]] == [
        i["name"] for i in first["report"]["inputs"]
    ]

    # Determinism scan of the canonical section: nothing path-, run- or secret-dependent.
    for doc, records in ((first, committed), (fresh.doc, fresh.records)):
        text = digest_scan_text(doc)
        assert "eyJ" not in text and "kid-" not in text and "-----BEGIN" not in text
        assert "/tmp/" not in text and "/var/" not in text and str(fresh.work) not in text
        assert not re.search(r"127\.0\.0\.1:\d+|port=\d+|\"pid\"", text)
        gids = {
            g
            for r in records
            if r["kind"] == "interrupt"
            for g in (r.get("observed", {}).get("gids", {}) or {}).values()
            if g
        }
        assert len(gids) >= 6 and not any(g in text for g in gids)  # observed gids stay observed
