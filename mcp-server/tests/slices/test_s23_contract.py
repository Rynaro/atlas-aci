"""ACI-S23 acceptance contract: one test per product criterion.

Committed evidence (docs/evidence/v3) is checked against the frozen manifest; negative controls
and the cost/legacy-span facts use fixture records built in-test from the real S18 buffers.
"""

from __future__ import annotations

import copy
import gzip
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_s23_support import (
    EVIDENCE,
    MANIFEST,
    RAW,
    ROOT,
    agg,
    committed_records,
    committed_report,
    fixture,
    fixture_report,
    legacy_observations,
    lines_of,
    make_records,
    manifest,
    real_cost_builds,
    rec,
    retrieval_record,
)

TOKENS = [2000, 8000, 16000]


def _gz(value):
    return agg.gzip_canonical(value)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    m, sha = manifest()
    return m, sha, real_cost_builds(tmp_path_factory.mktemp("cost"))


# ---------------------------------------------------------------- AC-S23-001


def test_paired_inputs(built):
    m, sha, builds = built
    fx = fixture()
    # Manifest: one frozen task input shared by every variant, equal to the S18 fixture tasks.
    assert m["task_input_sha256"] == agg.digest(m["tasks"])
    assert m["variants"] and all(
        v["task_input_sha256"] == m["task_input_sha256"] for v in m["variants"]
    )
    assert {v["id"] for v in m["variants"]} == {"V0", "V1", "B"}
    assert [t["text"] for t in m["tasks"]] == [t["text"] for t in fx["tasks"]]
    assert [t["id"] for t in m["tasks"]] == [t["id"] for t in fx["tasks"]] and m["tasks"]
    assert agg.sha256_bytes((ROOT / m["corpus"][0]["path"]).read_bytes()) == (
        "07553bd3d51be01a1d522fc59c240bf41ecf312a051c413c609585c60999b507"
    )
    agg.verify_corpus(m, ROOT)  # every corpus sha / tree sha matches the bytes on disk
    # Committed records: one digest, one corpus commit, the manifest's task set per arm/budget.
    records = committed_records()
    retrieval = [r for r in records if r["kind"] == "retrieval"]
    assert {r["arm"] for r in retrieval} == {a["id"] for a in m["arms"]} and len(retrieval) == 5
    assert {r["task_input_sha256"] for r in retrieval} == {m["task_input_sha256"]}
    assert len({r["corpus_commit"] for r in retrieval}) == 1
    task_ids = {t["id"] for t in m["tasks"]}
    for r in retrieval:
        arm = next(a for a in m["arms"] if a["id"] == r["arm"])
        by_budget: dict = {}
        for o in r["observations"]:
            by_budget.setdefault(o["budget_tokens"], set()).add(o["task_id"])
        if arm["kind"] == "context":
            assert set(by_budget) == set(TOKENS)
        else:
            assert set(by_budget) == {None}
        assert by_budget and all(tasks == task_ids for tasks in by_budget.values())
    pairing = committed_report()["canonical"]["pairing"]
    assert pairing["paired"] is True and pairing["task_input_sha256"] == m["task_input_sha256"]
    assert {a["status"] for a in pairing["arms"]} == {"complete"}
    # Negative controls on fixture records.
    records_ = make_records(m, sha, builds)
    assert fixture_report(m, sha, records_)["canonical"]["pairing"]["paired"] is True

    altered = copy.deepcopy(records_)
    altered[("retrieval", "B.search_read")]["task_input_sha256"] = "0" * 64
    with pytest.raises(agg.EvalInputError) as error:
        fixture_report(m, sha, altered)
    assert error.value.code == "unpaired_inputs"

    missing = copy.deepcopy(records_)
    record = missing[("retrieval", "V1.context.inline")]
    record["observations"] = [o for o in record["observations"] if o["task_id"] != "unicode"]
    with pytest.raises(agg.EvalInputError) as error:
        fixture_report(m, sha, missing)
    assert error.value.code == "unpaired_inputs"

    other_commit = copy.deepcopy(records_)
    other_commit[("retrieval", "V1.legacy")]["corpus_commit"] = "d" * 40
    with pytest.raises(agg.EvalInputError) as error:
        fixture_report(m, sha, other_commit)
    assert error.value.code == "unpaired_inputs"

    text_changed = copy.deepcopy(records_)
    record = text_changed[("retrieval", "V1.context.inline")]
    first = record["observations"][0]
    first["arguments"] = {**first["arguments"], "text": "something else"}
    first["observation_key"] = agg.observation_key(
        record["arm"],
        first["task_id"],
        m["task_input_sha256"],
        first["budget_tokens"],
        first["step"],
        first["tool"],
        first["arguments"],
    )
    with pytest.raises(agg.EvalInputError) as error:
        fixture_report(m, sha, text_changed)
    assert error.value.code == "unpaired_inputs"


# ---------------------------------------------------------------- AC-S23-002


def test_release_integrity_gate(built):
    m, sha, builds = built
    ids = [n["id"] for n in m["p0"]["node_ids"]]
    assert len(ids) == m["p0"]["count"] == 30 and len(set(ids)) == 30
    assert {n["category"] for n in m["p0"]["node_ids"]} == {"binding", "snapshot"}
    excluded = {e["id"] for e in m["p0"]["excluded"]}
    assert excluded and not excluded & set(ids)
    assert not any(i.split("::")[0].endswith("_docker.py") for i in ids)
    # Run: the declared ids pass in a subprocess with this interpreter.
    run = rec.run_p0(ids, ROOT / "mcp-server")
    assert run["returncode"] == 0 and run["results"]
    assert [r["node_id"] for r in run["results"]] == ids
    assert all(r["testcases"] >= 1 and r["outcome"] == "passed" for r in run["results"])
    assert sum(r["testcases"] for r in run["results"]) >= len(ids)
    # A misspelt id maps to `missing` (the id mapping is not vacuous).
    bad = rec.run_p0(
        ["tests/slices/test_s02_contract.py::test_no_such_function"], ROOT / "mcp-server"
    )
    assert bad["results"] == [
        {
            "node_id": "tests/slices/test_s02_contract.py::test_no_such_function",
            "testcases": 0,
            "outcome": "missing",
        }
    ]
    # Committed report: pass, ids equal to the manifest's.
    integrity = committed_report()["canonical"]["release_integrity"]
    assert integrity["status"] == "pass" and integrity["total"] == integrity["passed"] == 30
    assert [r["node_id"] for r in integrity["results"]] == sorted(ids)
    assert all(r["outcome"] == "passed" for r in integrity["results"])
    # Fixture: one failed id makes the report and the gate fail.
    records = make_records(m, sha, builds)
    records[("p0", "all")]["results"][0]["outcome"] = "failed"
    report = fixture_report(m, sha, records)["canonical"]
    assert report["release_integrity"]["status"] == "fail"
    assert report["gates"]["release_integrity"] == "fail"
    records = make_records(m, sha, builds)
    del records[("p0", "all")]
    with pytest.raises(agg.EvalInputError) as error:
        fixture_report(m, sha, records)
    assert error.value.code == "missing_input"


# ---------------------------------------------------------------- AC-S23-003


def test_span_budget_matrix(built):
    m, sha, builds = built
    fx = fixture()
    # Fixture records: V1.legacy reads payments.py lines 1..4; a padded search result makes the
    # smallest budget admit fewer calls (the prefix rule is visible).
    arm = next(a for a in m["arms"] if a["id"] == "V1.legacy")
    records = make_records(m, sha, builds)
    observations = legacy_observations(m, arm, pad_definitions={"process_payment": 60})
    records[("retrieval", "V1.legacy")] = retrieval_record(m, sha, arm, observations, "/repo")
    big = next(o for o in observations if o["tool"] == "search_symbol" and o["step"] == 0)
    big_bytes = len(big["result_text"].encode())
    assert 8000 < big_bytes < 32000
    view = next(o for o in observations if o["tool"] == "view_file")
    assert view["arguments"]["path"] == "app/payments.py"
    first, last = view["arguments"]["start_line"], view["arguments"]["end_line"]
    assert first == 1
    lines = lines_of("app/payments.py")
    start = 0
    end = len("".join(lines[:last]).encode())
    expected = 0  # hand overlap of the viewed bytes with the labelled spans
    for span in next(t for t in fx["tasks"] if t["id"] == "payment")["useful_spans"]:
        expected += max(0, min(end, span["end_byte"]) - max(start, span["start_byte"]))
    assert expected > 0
    report = fixture_report(m, sha, records)["canonical"]
    rows = {(r["arm"], r["budget_tokens"]): r for r in report["retrieval"]["span_matrix"]}
    # Other tasks add their own labelled bytes; isolate the payment task from per_task rows.
    per_task = {
        (r["arm"], r["budget_tokens"], r["task_id"]): r for r in report["retrieval"]["per_task"]
    }
    assert per_task[("V1.legacy", 2000, "payment")]["calls"] == 0  # first call exceeds 8000 B
    assert per_task[("V1.legacy", 2000, "payment")]["useful_inline_bytes"] == 0
    for budget in (8000, 16000):
        row = per_task[("V1.legacy", budget, "payment")]
        # search + view(app/payments.py) + view(other/payments.py, no labelled bytes)
        assert row["calls"] == 3 and row["useful_inline_bytes"] == expected
    assert (
        rows[("V1.legacy", 2000)]["metrics"]["calls"]
        < rows[("V1.legacy", 8000)]["metrics"]["calls"]
    )
    # Report: exactly one row per (arm, budget); all metric keys; V0 context n/a, never 0.
    matrix = report["retrieval"]["span_matrix"]
    assert len(matrix) == len(rows) == (5 + 2) * 3
    keys = {
        "calls",
        "errors",
        "result_bytes",
        "result_bytes_repo_normalized",
        "byte_quarter_tokens",
        "useful_label_bytes",
        "useful_reference_bytes",
        "useful_inline_bytes",
        "unique_reference_bytes",
        "unique_inline_bytes",
        "redundant_reference_bytes",
        "redundant_inline_bytes",
        "inline_recall",
        "reference_recall",
        "inline_precision",
        "bundles",
    }
    measured = [r for r in matrix if r["status"] == "measured"]
    assert len(measured) == 15 and all(keys <= set(r["metrics"]) for r in measured)
    assert sorted({r["budget_tokens"] for r in matrix}) == TOKENS
    not_applicable = [r for r in matrix if r["status"] == "not_applicable"]
    assert len(not_applicable) == 6
    assert {r["arm"] for r in not_applicable} == {"V0.context.references", "V0.context.inline"}
    assert all(
        r["reason"] == "op_unavailable_pre_v3" and r["metrics"] is None for r in not_applicable
    )
    # Committed report: 5 measured arms x 3 budgets, md has a table row per budget.
    committed = committed_report()["canonical"]["retrieval"]["span_matrix"]
    done = [r for r in committed if r["status"] == "measured"]
    assert len(done) == 15 and {r["arm"] for r in done} == {a["id"] for a in m["arms"]}
    assert all(keys <= set(r["metrics"]) for r in done)
    text = (EVIDENCE / "release-report.md").read_text()
    for arm_id in {r["arm"] for r in done}:
        for budget in TOKENS:
            assert any(line.startswith(f"| {arm_id} | {budget} |") for line in text.splitlines()), (
                arm_id,
                budget,
            )
    # Removing one budget's records is an incomplete matrix.
    broken = make_records(m, sha, builds)
    record = broken[("retrieval", "V1.context.inline")]
    record["observations"] = [o for o in record["observations"] if o["budget_tokens"] != 8000]
    with pytest.raises(agg.EvalInputError) as error:
        fixture_report(m, sha, broken)
    assert error.value.code == "incomplete_matrix"
    absent = make_records(m, sha, builds)
    del absent[("retrieval", "B.search_read")]
    with pytest.raises(agg.EvalInputError) as error:
        fixture_report(m, sha, absent)
    assert error.value.code == "incomplete_matrix"


# ---------------------------------------------------------------- AC-S23-004


def _walk_keys(value, out):
    if isinstance(value, dict):
        for key, item in value.items():
            out.append(key)
            _walk_keys(item, out)
    elif isinstance(value, list):
        for item in value:
            _walk_keys(item, out)


def test_cost_accounting(built):
    m, sha, builds = built
    spec = m["cost"]
    # Real counters on a 2-file repo.
    by_category = {b["category"]: b["counters"]["extraction_cache"] for b in builds}
    assert list(by_category) == spec["categories"][:4]
    assert by_category["index.cold_full"]["parser_invocations"] == 2
    assert by_category["index.warm_full"]["parser_invocations"] == 0
    assert by_category["index.incremental_noop"]["parser_invocations"] == 0
    assert by_category["index.refresh_after_edit"]["parser_invocations"] == 1
    assert all(set(spec["counters"]) <= set(b["counters"]) for b in builds)
    records = make_records(m, sha, builds)
    canonical = fixture_report(m, sha, records)["canonical"]
    assert list(canonical["cost"]) == spec["categories"]
    for category in spec["categories"][:4]:
        per_corpus = canonical["cost"][category]
        assert set(per_corpus) == set(spec["corpora"]) and per_corpus
        for variants in per_corpus.values():
            assert variants["V1"]["status"] == "measured"
            assert set(variants["V1"]["counters"]) == set(spec["counters"])
    cold = canonical["cost"]["index.cold_full"]["s18-useful-spans"]
    assert cold["V0"]["status"] == "measured"
    assert (
        canonical["cost"]["index.warm_full"]["s18-useful-spans"]["V0"]["status"] == "not_applicable"
    )
    retrieval = canonical["cost"]["query.retrieval"]
    assert len(retrieval) == 15 and all(
        {"calls", "result_bytes", "byte_quarter_tokens"} <= set(q) for q in retrieval
    )
    assert canonical["cost"]["query.model"] == {
        "status": "unmeasured",
        "reason": "no_operator_budget",
    }
    names: list[str] = []
    _walk_keys(canonical, names)
    assert names and not [
        n for n in names if any(w in n for w in ("duration", "seconds", "elapsed"))
    ]
    # Committed report: every index category per cost corpus for V1 (V0 per D6).
    committed = committed_report()["canonical"]["cost"]
    assert set(committed) == set(spec["categories"])
    for category in spec["categories"][:4]:
        assert set(committed[category]) == set(spec["corpora"])
        for corpus, variants in committed[category].items():
            assert variants["V1"]["status"] == "measured", (category, corpus)
            assert set(variants["V0"]) and variants["V0"]["status"] in {
                "measured",
                "unmeasured",
                "not_applicable",
            }
    assert committed["index.cold_full"]["s18-useful-spans"]["V0"]["status"] == "measured"
    # Dropping a category fails closed.
    broken = make_records(m, sha, builds)
    broken[("cost", "coverage-project.V1")]["builds"] = [
        b for b in builds if b["category"] != "index.warm_full"
    ]
    with pytest.raises(agg.EvalInputError) as error:
        fixture_report(m, sha, broken)
    assert error.value.code == "missing_cost_category"


# ---------------------------------------------------------------- AC-S23-005


def _live_values(value, names, found):
    if isinstance(value, dict):
        for key, item in value.items():
            unmeasured = isinstance(item, dict) and item.get("status") == "unmeasured"
            if key in names and item is not None and not unmeasured:
                found.append(key)
            if unmeasured and item.get("value") is not None:
                found.append(key)
            _live_values(item, names, found)
    elif isinstance(value, list):
        for item in value:
            _live_values(item, names, found)


def test_incomplete_live_gate(built):
    m, sha, builds = built
    live = m["engineering_outcomes"]["live"]
    assert live and m["engineering_outcomes"]["live_budget"] is None
    reports = [
        fixture_report(m, sha, make_records(m, sha, builds)),
        committed_report(),
    ]
    for report in reports:
        canonical = report["canonical"]
        assert set(canonical["engineering_outcomes"]["live"]) == set(live)
        for item in canonical["engineering_outcomes"]["live"].values():
            assert item == {"status": "unmeasured", "reason": "no_operator_budget", "value": None}
        assert canonical["gates"]["live_agent_evaluation"] == "incomplete"
        found: list[str] = []
        _live_values(canonical, set(live), found)
        assert found == []
        offline = canonical["engineering_outcomes"]["offline"]["validation_plan_comparison"]
        assert offline["label"] == "offline_fixture_proxy"
    text = (EVIDENCE / "release-report.md").read_text()
    assert text.count("unmeasured (no_operator_budget)") >= len(live)
    for name in live:
        assert f"- {name}: unmeasured (no_operator_budget)" in text
    # A planted live record or value fails closed.
    planted = make_records(m, sha, builds)
    planted[("live", "x")] = {
        "kind": "live",
        "format": agg.RAW_FORMAT,
        "status": "complete",
        "manifest_sha256": sha,
    }
    with pytest.raises(agg.EvalInputError) as error:
        fixture_report(m, sha, planted)
    assert error.value.code == "live_without_budget"
    valued = make_records(m, sha, builds)
    valued[("offline", "validation_plan_comparison")]["result"]["model_tokens"] = 12
    with pytest.raises(agg.EvalInputError) as error:
        fixture_report(m, sha, valued)
    assert error.value.code == "live_without_budget"
    budgeted = copy.deepcopy(m)
    budgeted["engineering_outcomes"]["live_budget"] = 5
    with pytest.raises(agg.EvalInputError) as error:
        agg.validate_manifest(budgeted)
    assert error.value.code == "live_without_budget"


# ---------------------------------------------------------------- AC-S23-006


def _write_raw(directory: Path, records, *, level=9, reverse=False, rename=False):
    directory.mkdir(parents=True)
    items = list(records)
    if reverse:
        items.reverse()
    for index, record in enumerate(items):
        name = f"x{index:02d}.json.gz" if rename else f"{record['kind']}.{index}.json.gz"
        (directory / name).write_bytes(
            gzip.compress(agg.canonical(record), compresslevel=level, mtime=0)
        )


def test_report_replay(tmp_path):
    m, _ = manifest()
    fx = fixture()
    first = agg.run(MANIFEST, RAW, ROOT)
    second = agg.run(MANIFEST, RAW, ROOT)
    assert first["canonical"] and agg.canonical(first["canonical"]) == agg.canonical(
        second["canonical"]
    )
    assert agg.render_md(first) == agg.render_md(second)
    assert first["canonical_sha256"] == second["canonical_sha256"]
    committed = committed_report()
    assert committed["canonical"] == first["canonical"]
    assert committed["canonical_sha256"] == first["canonical_sha256"]
    assert (EVIDENCE / "release-report.md").read_text() == agg.render_md(first)
    # Two CLI aggregations into two directories are byte-identical to each other and to the repo.
    outs = []
    for name in ("a", "b"):
        target = tmp_path / name
        done = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/eval/v3_aggregate.py"),
                "--manifest",
                str(MANIFEST),
                "--raw",
                str(RAW),
                "--out",
                str(target),
            ],
            capture_output=True,
            text=True,
        )
        assert done.returncode == 0, done.stderr
        outs.append(target)
    for name in ("release-report.md",):
        assert (outs[0] / name).read_bytes() == (outs[1] / name).read_bytes()
        assert (outs[0] / name).read_bytes() == (EVIDENCE / name).read_bytes()
    one = json.loads((outs[0] / "release-report.json").read_bytes())
    two = json.loads((outs[1] / "release-report.json").read_bytes())
    assert one["canonical"] == two["canonical"] == committed["canonical"]
    # Diagnostic-only perturbations of a copy leave the canonical section unchanged.
    originals = committed_records()
    assert originals
    perturbed = copy.deepcopy(originals)
    touched = 0
    for record in perturbed:
        diagnostic = record.get("diagnostic")
        if diagnostic is None:
            continue
        touched += 1
        diagnostic["durations_seconds"] = {
            k: v * 2 for k, v in diagnostic.get("durations_seconds", {}).items()
        }
        diagnostic["run_ids"] = ["perturbed"]
        diagnostic["producer"] = {"perturbed": True}
        diagnostic["platform"] = "elsewhere"
    assert touched == len(originals)
    _write_raw(tmp_path / "perturbed", perturbed, level=1, reverse=True, rename=True)
    again = agg.run(MANIFEST, tmp_path / "perturbed", ROOT)
    assert agg.canonical(again["canonical"]) == agg.canonical(first["canonical"])
    assert again["canonical_sha256"] == first["canonical_sha256"]
    assert again["diagnostic"] != first["diagnostic"]
    # Non-vacuity: one result byte changed (sha recomputed) moves the canonical hash.
    altered = copy.deepcopy(originals)
    target = next(
        r for r in altered if r["kind"] == "retrieval" and r["arm"] == "V1.context.inline"
    )
    observation_ = target["observations"][-1]
    result = json.loads(observation_["result_text"])
    body = json.loads(result["content"][0]["text"])
    body["replay_probe"] = "x"
    result["content"][0]["text"] = json.dumps(body, indent=2)
    observation_["result_text"] = json.dumps(
        result, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    observation_["result_sha256"] = hashlib.sha256(observation_["result_text"].encode()).hexdigest()
    changed = agg.aggregate(m, altered, fx)
    assert changed["canonical_sha256"] != first["canonical_sha256"]
    stale = copy.deepcopy(altered)
    next(r for r in stale if r["kind"] == "retrieval" and r["arm"] == "V1.context.inline")[
        "observations"
    ][-1]["result_sha256"] = "0" * 64
    with pytest.raises(agg.EvalInputError) as error:
        agg.aggregate(m, stale, fx)
    assert error.value.code == "hash_mismatch"
    duplicate = [*copy.deepcopy(originals), copy.deepcopy(originals[0])]
    with pytest.raises(agg.EvalInputError) as error:
        agg.aggregate(m, duplicate, fx)
    assert error.value.code == "duplicate_input"
    foreign = copy.deepcopy(originals)
    foreign[0]["manifest_sha256"] = "1" * 64
    with pytest.raises(agg.EvalInputError) as error:
        agg.aggregate(m, foreign, fx)
    assert error.value.code == "manifest_mismatch"
