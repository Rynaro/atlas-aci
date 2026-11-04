"""ACI-S21: strict parsing, normalization, line mapping, closed shapes, validators, no-exec scan."""

import ast
import json
import random
import sqlite3
import sys
from pathlib import Path

import pytest
from test_s21_support import A, B, report_json

from atlas_aci.providers import coverage_py
from atlas_aci.storage import evidence as ev

SRC = Path(coverage_py.__file__).parent
TEST = "tests/test_x.py::test_a"


def report_bytes(data):
    return json.dumps(data).encode()


def minimal(**files):
    """A format-3 report over ``files`` ({path: (executed_lines, contexts)})."""
    return {
        "meta": {
            "format": 3,
            "version": "7.16.2",
            "timestamp": "2026-01-01T00:00:00.000000",
            "branch_coverage": False,
            "show_contexts": True,
        },
        "files": {
            path: {"executed_lines": lines, "contexts": contexts}
            for path, (lines, contexts) in files.items()
        },
    }


def parse(data, **limits):
    raw = data if isinstance(data, bytes) else report_bytes(data)
    return coverage_py.parse_report(raw, coverage_py.Limits(**limits))


def observe_rejects(code, data):
    with pytest.raises(coverage_py.CoverageImportError) as exc:
        coverage_py.observe(parse(data))
    assert exc.value.code == code, str(exc.value)


def rejects(code, data, **limits):
    with pytest.raises(coverage_py.CoverageImportError) as exc:
        parse(data, **limits)
    assert exc.value.code == code, str(exc.value)


# ---- strict parsing ----
def test_real_reports_parse_and_context_lines_are_not_a_subset_of_executed_lines():
    for run in ("a", "b"):
        report = coverage_py.parse_report((A if run == "a" else B)["report"].read_bytes())
        assert report.version == "7.16.2" and len(report.files) == 11
        extra = {
            (path, line)
            for path, entry in report.files.items()
            for line in entry.contexts
            if line not in entry.executed
        }
        # Condition 3: real reports record docstring/continuation lines `executed_lines` omits.
        assert extra, "the fixture must exercise context lines outside executed_lines"
        observations = coverage_py.observe(report)
        assert observations.counts["test_executes"] == len(observations.executes) == 66
        assert {phase for phases in observations.executes.values() for phase in phases} == {
            "run",
            "setup",
            "teardown",
        }


def test_duplicate_keys_nan_and_non_json_are_rejected():
    rejects("report_invalid", b'{"meta": {}, "meta": {}, "files": {}}')
    rejects("report_invalid", b'{"meta": NaN, "files": {}}')
    rejects("report_invalid", b'{"meta": Infinity, "files": {}}')
    rejects("report_invalid", b"\xff\xfe")
    rejects("report_invalid", b"[]")
    rejects("report_invalid", b'{"meta": 1}')
    data = minimal(**{"a.py": ([1], {"1": [f"{TEST}|run"]})})
    raw = report_bytes(data).replace(b'"files"', b'"files": {}, "files"', 1)
    rejects("report_invalid", raw)
    rejects("report_invalid", {**data, "unexpected": 1})


def test_meta_shape_and_versions():
    data = minimal(**{"a.py": ([1], {"1": [f"{TEST}|run"]})})
    for change, code in (
        ({"format": 2}, "unsupported_report_format"),
        ({"format": True}, "unsupported_report_format"),
        ({"version": "9.9.9"}, "unknown_producer_version"),
        ({"show_contexts": False}, "contexts_absent"),
        ({"show_contexts": 1}, "report_invalid"),
        ({"timestamp": ""}, "report_invalid"),
    ):
        broken = json.loads(json.dumps(data))
        broken["meta"].update(change)
        rejects(code, broken)
    broken = json.loads(json.dumps(data))
    broken["meta"]["extra"] = 1
    rejects("report_invalid", broken)


@pytest.mark.parametrize(
    ("entry", "code"),
    [
        ({"executed_lines": [True], "contexts": {}}, "report_invalid"),
        ({"executed_lines": [1.0], "contexts": {}}, "report_invalid"),
        ({"executed_lines": [2, 1], "contexts": {}}, "report_invalid"),
        ({"executed_lines": [1, 1], "contexts": {}}, "report_invalid"),
        ({"executed_lines": [0], "contexts": {}}, "report_invalid"),
        ({"executed_lines": "1", "contexts": {}}, "report_invalid"),
        ({"contexts": {}}, "report_invalid"),
        ({"executed_lines": [1]}, "contexts_absent"),
        ({"executed_lines": [1], "contexts": []}, "report_invalid"),
        ({"executed_lines": [1], "contexts": {"0": [f"{TEST}|run"]}}, "report_invalid"),
        ({"executed_lines": [1], "contexts": {"01": [f"{TEST}|run"]}}, "report_invalid"),
        ({"executed_lines": [1], "contexts": {"x": [f"{TEST}|run"]}}, "report_invalid"),
        ({"executed_lines": [1], "contexts": {"-1": [f"{TEST}|run"]}}, "report_invalid"),
        ({"executed_lines": [1], "contexts": {"1": f"{TEST}|run"}}, "report_invalid"),
        ({"executed_lines": [1], "contexts": {"1": [7]}}, "report_invalid"),
        (
            {"executed_lines": [1], "contexts": {"99999999999": [f"{TEST}|run"]}},
            "line_out_of_range",
        ),
    ],
)
def test_file_entries_are_strict(entry, code):
    data = minimal()
    data["files"]["a.py"] = entry
    rejects(code, data)


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("/abs/a.py", "absolute_path"),
        ("C:/abs/a.py", "absolute_path"),
        ("\\\\host\\a.py", "absolute_path"),
        ("../a.py", "path_not_normalized"),
        ("a/../b.py", "path_not_normalized"),
        ("./a.py", "path_not_normalized"),
        ("a//b.py", "path_not_normalized"),
        ("a\\b.py", "path_not_normalized"),
        ("", "path_not_normalized"),
    ],
)
def test_paths_must_be_normalized_relative_posix(path, code):
    rejects(code, minimal(**{path: ([1], {"1": [f"{TEST}|run"]})}))


def test_context_lines_outside_executed_lines_are_accepted_and_only_counted():
    data = minimal(**{"a.py": ([5], {"1": [""], "2": [f"{TEST}|run"], "5": [f"{TEST}|setup"]})})
    observations = coverage_py.observe(parse(data))
    assert set(observations.executes) == {(TEST, "a.py", 2), (TEST, "a.py", 5)}
    # `executed_lines` {5} plus context keys {1,2,5}, minus attributed {2,5}: only line 1 is left.
    assert observations.counts["unattributed_lines"] == 1
    assert observations.counts["observed_lines"] == 2


# ---- context normalization ----
def test_context_split_uses_the_last_bar():
    assert coverage_py.split_context("tests/t.py::t[a|b]|run") == ("tests/t.py::t[a|b]", "run")
    assert coverage_py.split_context("tests/t.py::C::t[x|y|z]|teardown")[1] == "teardown"
    assert coverage_py.split_context("tests/t.py::t[\\u65e5]|setup") == (
        "tests/t.py::t[\\u65e5]",
        "setup",
    )


@pytest.mark.parametrize(
    "context",
    [
        "ci",
        "ci|tests/t.py::t|run",
        "tests/t.py::t",
        "tests/t.py::t|call",
        "tests/t.py::t|",
        "|run",
        "tests/t.txt::t|run",
        "tests/t.py|run",
        "tests/t.py::|run",
        "/abs/t.py::t|run",
        "../t.py::t|run",
        "tests/t.py::t|run|extra",
    ],
)
def test_unsupported_contexts(context):
    data = minimal(**{"a.py": ([1], {"1": [f"{TEST}|run", context]})})
    observe_rejects("unsupported_context", data)


def test_unattributed_only_reports_are_refused():
    observe_rejects("no_test_contexts", minimal(**{"a.py": ([1], {"1": [""]})}))
    observe_rejects("no_test_contexts", minimal(**{"a.py": ([1], {})}))


def test_context_list_order_does_not_matter_and_phases_merge():
    one = minimal(**{"a.py": ([1], {"1": [f"{TEST}|run", f"{TEST}|setup", f"{TEST}|teardown"]})})
    two = minimal(**{"a.py": ([1], {"1": [f"{TEST}|teardown", f"{TEST}|run", f"{TEST}|setup"]})})
    first, second = coverage_py.observe(parse(one)), coverage_py.observe(parse(two))
    assert first == second
    assert first.executes == {(TEST, "a.py", 1): frozenset({"run", "setup", "teardown"})}
    assert coverage_py.role_of(first.executes[(TEST, "a.py", 1)]) == (
        "observed_execution:run+setup+teardown"
    )


# ---- bounds ----
def test_bounds_are_enforced_with_stable_codes():
    data = minimal(
        **{
            "a.py": (
                [1, 2],
                {"1": [f"{TEST}|run"], "2": [f"{TEST}|run", "tests/test_x.py::test_b|run"]},
            ),
            "b.py": ([1], {"1": [f"{TEST}|run"]}),
        }
    )
    assert coverage_py.observe(parse(data)).counts["test_executes"] == 4
    rejects("too_many_files", data, max_files=1)
    with pytest.raises(coverage_py.CoverageImportError) as exc:
        coverage_py.observe(parse(data), coverage_py.Limits(max_executes_per_run=3))
    assert exc.value.code == "run_limit"
    with pytest.raises(coverage_py.CoverageImportError) as exc:
        coverage_py.observe(parse(data), coverage_py.Limits(max_tests=1))
    assert exc.value.code == "too_many_tests"
    with pytest.raises(coverage_py.CoverageImportError) as exc:
        coverage_py.observe(parse(data), coverage_py.Limits(max_nodeid_bytes=len(TEST) - 1))
    assert exc.value.code == "nodeid_too_long"
    # Bytes, not characters, are bounded.
    wide = "tests/t.py::t[" + "日" * 5 + "]"
    assert coverage_py.split_context(
        wide + "|run", coverage_py.Limits(max_nodeid_bytes=len(wide.encode()))
    )
    with pytest.raises(coverage_py.CoverageImportError):
        coverage_py.split_context(wide + "|run", coverage_py.Limits(max_nodeid_bytes=len(wide)))


def test_artifact_and_manifest_size_bounds(tmp_path):
    with pytest.raises(coverage_py.CoverageImportError) as exc:
        coverage_py.load_submission(
            A["report"], A["manifest"], coverage_py.Limits(max_artifact_bytes=100)
        )
    assert exc.value.code == "report_too_large"
    with pytest.raises(coverage_py.CoverageImportError) as exc:
        coverage_py.load_submission(
            A["report"], A["manifest"], coverage_py.Limits(max_manifest_bytes=100)
        )
    assert exc.value.code == "manifest_too_large"


def test_generation_bounds_on_merged_runs():
    submission = coverage_py.load_submission(A["report"], A["manifest"])
    other = coverage_py.load_submission(B["report"], B["manifest"])
    entry = coverage_py.run_entry(submission, "1" * 64)
    second = coverage_py.run_entry(other, "2" * 64)
    state, runs = coverage_py.merge_runs(None, entry)
    assert state == "new" and runs == [entry]
    block = coverage_py.block_of("3" * 64, runs)
    assert coverage_py.merge_runs(block, entry) == ("unchanged", runs)
    with pytest.raises(coverage_py.CoverageImportError) as exc:
        coverage_py.merge_runs(block, {**entry, "importer_sha256": "4" * 64})
    assert exc.value.code == "run_conflict"
    with pytest.raises(coverage_py.CoverageImportError) as exc:
        coverage_py.merge_runs(block, second, coverage_py.Limits(max_runs=1))
    assert exc.value.code == "run_limit"
    with pytest.raises(coverage_py.CoverageImportError) as exc:
        coverage_py.merge_runs(block, second, coverage_py.Limits(max_executes_per_generation=100))
    assert exc.value.code == "generation_limit"
    assert coverage_py.merge_runs(block, second)[0] == "new"
    assert coverage_py.MAX_EXECUTES_PER_RUN <= coverage_py.MAX_EXECUTES_PER_GENERATION
    assert coverage_py.MAX_RECORDS_PER_GENERATION >= 3 * coverage_py.MAX_EXECUTES_PER_GENERATION


# ---- the D3 line splitter ----
ALPHABET = [
    b"a",
    b" ",
    b"\r",
    b"\n",
    b"\r\n",
    b"\x0c",
    b"\x0b",
    "\u2028".encode(),
    "é".encode(),
    b"\x00",
]


def test_line_splitter_matches_the_terminator_oracle():
    rng = random.Random(21)
    samples = [b"", b"\n", b"\r", b"\r\n", b"\n\r", b"a\r\nb\rc\nd", b"a\n", b"a\r", b"\r\r\n\n"]
    samples += [
        b"".join(rng.choice(ALPHABET) for _ in range(rng.randrange(1, 40))) for _ in range(400)
    ]
    assert len(samples) > 400
    for source in samples:
        spans = coverage_py.line_spans(source)
        assert [source[a:b] for a, b in spans] == source.splitlines(), source
        assert [(a, b) for a, b in spans if a > b] == []
    # A lone CR is a terminator; FF, VT and U+2028 are not.
    assert coverage_py.line_spans(b"a\rb\x0cc\xe2\x80\xa8d\r\n") == [(0, 1), (2, 9)]
    assert coverage_py.line_spans(b"\xef\xbb\xbfx = 1\n")[0] == (0, 8)  # a BOM belongs to line 1


def test_line_splitter_agrees_with_the_python_tokenizer():
    import io
    import tokenize

    rng = random.Random(7)
    pieces = ["x = 1", "def f():", "    return 2", "# c", "", '"""d"""', "y = (1,", "     2)"]
    for terminator in (b"\n", b"\r\n", b"\r"):
        for _ in range(60):
            lines = [rng.choice(pieces) for _ in range(rng.randrange(1, 12))]
            source = terminator.join(piece.encode() for piece in lines) + terminator
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            spans = coverage_py.line_spans(source)
            texts = [source[a:b].decode() for a, b in spans]
            for node in ast.walk(tree):
                if isinstance(node, ast.stmt):
                    assert texts[node.lineno - 1].lstrip()[:1] != ""
            for token in tokenize.tokenize(
                io.BytesIO(source.replace(b"\r\n", b"\n").replace(b"\r", b"\n")).readline
            ):
                if token.type == tokenize.NAME:
                    assert token.string in texts[token.start[0] - 1]


# ---- sidecar and environment shapes ----
def test_environment_and_sidecar_are_closed_shapes():
    environment = json.loads(A["environment"].read_text())
    assert coverage_py.check_environment(environment) == environment
    for change in (
        lambda e: e.pop("format"),
        lambda e: e.update(extra=1),
        lambda e: e["python"].pop("cache_tag"),
        lambda e: e["packages"].pop("pytest-cov"),
        lambda e: e["platform"].update(machine=""),
        lambda e: e.update(format="atlas-coverage-environment.v2"),
        lambda e: e["packages"].update(pytest=1),
    ):
        broken = json.loads(json.dumps(environment))
        change(broken)
        with pytest.raises(coverage_py.CoverageImportError) as exc:
            coverage_py.check_environment(broken)
        assert exc.value.code == "environment_invalid"
    manifest = json.loads(A["manifest"].read_text())
    assert set(manifest) == {
        "format",
        "artifact_sha256",
        "tool",
        "report_format",
        "context_mode",
        "environment",
        "commit",
        "measured_files",
        "test_files",
    }
    assert manifest["commit"] == {"state": "unavailable"}  # condition 5: capture is cut
    assert set(manifest["test_files"]) <= set(manifest["measured_files"])
    parsed = coverage_py.parse_manifest(A["manifest"].read_bytes())
    assert parsed.measured_files == manifest["measured_files"]
    for change in (
        lambda m: m.update(commit={"state": "recorded", "head": "a" * 40, "dirty": False}),
        lambda m: m.update(format="atlas-coverage-manifest.v2"),
        lambda m: m.update(context_mode="coverage:test_function"),
        lambda m: m["tool"].update(name="other"),
        lambda m: m.update(extra=1),
        lambda m: m["measured_files"].update({"../x.py": "0" * 64}),
        lambda m: m["test_files"].update({"x.py": "short"}),
    ):
        data = json.loads(json.dumps(manifest))
        change(data)
        with pytest.raises(coverage_py.CoverageImportError) as exc:
            coverage_py.parse_manifest(json.dumps(data).encode())
        assert exc.value.code == "manifest_invalid", str(exc.value)
    with pytest.raises(coverage_py.CoverageImportError):
        coverage_py.parse_manifest(b'{"format": 1, "format": 2}')


def test_a_file_hashed_two_ways_is_refused(tmp_path):
    data = json.loads(A["manifest"].read_text())
    data["test_files"]["tests/test_basic.py"] = "0" * 64
    target = tmp_path / "two-ways.json"
    target.write_text(json.dumps(data))
    with pytest.raises(coverage_py.CoverageImportError) as exc:
        coverage_py.load_submission(A["report"], target)
    assert exc.value.code == "manifest_invalid" and "two different ways" in str(exc.value)


def test_run_identity_is_content_derived():
    first = coverage_py.load_submission(A["report"], A["manifest"])
    again = coverage_py.load_submission(A["report"], A["manifest"])
    assert (
        first.run_id
        == again.run_id
        == coverage_py.run_id_of(first.artifact_sha256, first.manifest_sha256)
    )
    assert report_json("a") != report_json("b")
    other = coverage_py.load_submission(B["report"], B["manifest"])
    assert other.run_id != first.run_id
    assert coverage_py.run_fingerprint(first)["coverage"]["commit"] == {"state": "unavailable"}


# ---- the evidence validators (checker condition 7) ----
def tiny():
    """A one-observation database built from the importer's own record builders."""
    producer = ev.producer(
        coverage_py.run_fingerprint(coverage_py.load_submission(A["report"], A["manifest"]))
    )
    file_entity = ev.file_entity("app/m.py", "python")
    test = ev.record(
        "entity",
        path="tests/test_m.py",
        language="python",
        kind="coverage_test",
        name="tests/test_m.py::test_a",
        owners=[],
        ordinal=0,
        identity_basis="parsed",
    )
    occurrence = ev.record(
        "occurrence",
        entity_id=file_entity["id"],
        kind="execution",
        path="app/m.py",
        language="python",
        source_sha256="a" * 64,
        source_size=20,
        start_byte=2,
        end_byte=9,
        line=2,
        column=0,
        end_line=2,
        end_column=7,
        role="observed_execution:run",
        provenance_state="source",
    )
    proof = ev.provenance(occurrence, producer["id"])
    assertion = ev.assertion(
        test["id"], file_entity["id"], "test_executes", proof["id"], "EXTRACTED", "app/m.py:2"
    )
    return {
        "producer": producer,
        "file": file_entity,
        "test": test,
        "occurrence": occurrence,
        "proof": proof,
        "assertion": assertion,
    }


def load(parts):
    conn = sqlite3.connect(":memory:")
    conn.executescript(ev.DDL)
    for item in parts.values():
        ev.put(conn, item)
    return conn


def redo(item, **changes):
    fields = {k: v for k, v in item.items() if k not in {"id", "type"} and k not in changes}
    return ev.record(item["type"], **fields, **changes)


def chain(parts, **by_kind):
    """Re-derive occurrence -> evidence -> assertion after changing an upstream record."""
    parts = dict(parts)
    for key, changes in by_kind.items():
        parts[key] = redo(parts[key], **changes)
    if "occurrence" in by_kind:
        parts["proof"] = ev.provenance(parts["occurrence"], parts["producer"]["id"])
    if "occurrence" in by_kind or "proof" in by_kind:
        parts["assertion"] = redo(parts["assertion"], evidence_id=parts["proof"]["id"])
    return parts


def test_the_baseline_database_validates():
    conn = load(tiny())
    ev.validate_database(conn)
    assert ev.COVERAGE_RELATION not in ev.RELATIONS | ev.REFERENCE_RELATIONS | ev.EDGE_RELATIONS
    assert ev.COVERAGE_RELATION in ev.ASSERTION_RELATIONS


def invalid(parts, match):
    with pytest.raises(ValueError, match=match):
        ev.validate_database(load(parts))


def test_assertion_clauses():
    base = tiny()
    wrong_target = ev.file_entity("app/other.py", "python")
    other_file_occurrence_target = {**base, "other": wrong_target}
    clauses = {
        "source is not a coverage_test": (
            {**base, "assertion": redo(base["assertion"], source_entity_id=base["file"]["id"])},
            "shape",
        ),
        "target is not the occurrence file entity": (
            {
                **other_file_occurrence_target,
                "assertion": redo(base["assertion"], target_entity_id=wrong_target["id"]),
            },
            "shape",
        ),
        "target is a test entity": (
            {**base, "assertion": redo(base["assertion"], target_entity_id=base["test"]["id"])},
            "shape",
        ),
        "raw target names another line": (
            {**base, "assertion": redo(base["assertion"], raw_target="app/m.py:3")},
            "shape",
        ),
        "raw target names another path": (
            {**base, "assertion": redo(base["assertion"], raw_target="app/n.py:2")},
            "shape",
        ),
    }
    for name, (parts, match) in clauses.items():
        with pytest.raises(ValueError, match=match):
            ev.validate_database(load(parts))
        assert name

    # Record-level clauses: resolution, raw target spelling, candidates.
    for changes in (
        {
            "resolution": "AMBIGUOUS",
            "target_entity_id": None,
            "unresolved_reason": "x",
            "candidate_ids": sorted([base["file"]["id"], base["test"]["id"]]),
        },
        {"resolution": "UNRESOLVED", "target_entity_id": None, "unresolved_reason": "x"},
        {"resolution": "INFERRED"},
        {"raw_target": "no-line"},
        {"raw_target": "app/m.py:0"},
        {"raw_target": "app/m.py:02x"},
    ):
        with pytest.raises(ValueError):
            ev.validate_record(redo(base["assertion"], **changes))


def test_a_call_assertion_citing_coverage_evidence_is_rejected():
    base = tiny()
    parts = {**base, "assertion": redo(base["assertion"], relation="call")}
    invalid(parts, "coverage assertion discrimination")
    parts = {**base, "assertion": redo(base["assertion"], relation="reference")}
    invalid(parts, "coverage assertion discrimination")


def test_test_executes_under_a_non_coverage_producer_is_rejected():
    base = tiny()
    build_like = ev.producer(
        {
            "codegraph_sha256": "1" * 64,
            "queries_sha256": "2" * 64,
            "packages": {"tree-sitter": "1", "tree-sitter-language-pack": "1"},
            "modules": {"storage/evidence.py": "3" * 64, "providers/python_imports.py": "4" * 64},
        }
    )
    proof = ev.provenance(base["occurrence"], build_like["id"])
    parts = {
        **base,
        "producer2": build_like,
        "proof": proof,
        "assertion": redo(base["assertion"], evidence_id=proof["id"]),
    }
    del parts["producer"]
    invalid(parts, "coverage assertion discrimination")
    # An execution occurrence cited only by non-coverage evidence is rejected on the evidence.
    parts = {
        **base,
        "producer": build_like,
        "proof": ev.provenance(base["occurrence"], build_like["id"]),
    }
    del parts["assertion"]
    invalid(parts, "coverage evidence/execution occurrence")


def test_coverage_producer_evidence_must_cite_an_execution_occurrence():
    base = tiny()
    definition = ev.record(
        "occurrence",
        entity_id=base["file"]["id"],
        kind="reference",
        path="app/m.py",
        language="python",
        source_sha256="a" * 64,
        source_size=20,
        start_byte=2,
        end_byte=9,
        line=2,
        column=0,
        end_line=2,
        end_column=7,
        role="reference:call",
        provenance_state="source",
    )
    parts = {
        "producer": base["producer"],
        "file": base["file"],
        "occurrence": definition,
        "proof": ev.provenance(definition, base["producer"]["id"]),
    }
    invalid(parts, "coverage evidence/execution occurrence")


@pytest.mark.parametrize(
    "changes",
    [
        {"entity_id": None},
        {"role": "observed_execution:setup+run"},
        {"role": "observed_execution:run+run"},
        {"role": "observed_execution:call"},
        {"role": "observed_execution:"},
        {"role": "definition:function"},
        {"end_line": 3},
        {"column": 1},
        {"end_column": 8},
        {"provenance_state": "legacy_unavailable"},
    ],
)
def test_execution_occurrence_shape(changes):
    base = tiny()
    with pytest.raises(ValueError):
        ev.validate_record(redo(base["occurrence"], **changes))


def test_execution_occurrence_must_hang_off_a_file_entity_in_the_same_path():
    base = tiny()
    function = ev.record(
        "entity",
        path="app/m.py",
        language="python",
        kind="function_definition",
        name="f",
        owners=[],
        ordinal=0,
        identity_basis="parsed",
    )
    parts = chain({**base, "function": function}, occurrence={"entity_id": function["id"]})
    invalid(parts, "test_executes assertion shape")  # the assertion's target is no longer it
    del parts["assertion"]
    invalid(parts, "execution occurrence entity")
    elsewhere = chain(
        {**base, "elsewhere": ev.file_entity("app/zzz.py", "python")},
        occurrence={"entity_id": ev.file_entity("app/zzz.py", "python")["id"]},
    )
    del elsewhere["assertion"]
    invalid(elsewhere, "occurrence/entity location")


@pytest.mark.parametrize(
    "changes",
    [
        {"owners": [{"kind": "class", "name": "C", "ordinal": 0}]},
        {"ordinal": 1},
        {"name": "other.py::test_a"},
        {"name": "tests/test_m.py"},
        {"path": "tests/test_m.txt", "name": "tests/test_m.txt::t"},
        {"identity_basis": "legacy_projection"},
        {"language": "ruby"},
    ],
)
def test_coverage_test_entity_shape(changes):
    with pytest.raises(ValueError):
        ev.validate_record(redo(tiny()["test"], **changes))


def test_coverage_producer_fingerprint_is_closed_and_content_derived():
    producer = tiny()["producer"]
    fingerprint = producer["fingerprint"]["coverage"]
    ev.validate_record(producer)
    for change in (
        lambda f: f.update(run_id="0" * 64),
        lambda f: f.update(extra=1),
        lambda f: f.pop("environment"),
        lambda f: f.update(commit={"state": "recorded", "head": "a" * 40, "dirty": False}),
        lambda f: f.update(artifact_sha256="nope"),
        lambda f: f["environment"].update(format="x"),
        lambda f: f["environment"]["python"].clear(),
        lambda f: f.update(report_format="3"),
    ):
        broken = json.loads(json.dumps(fingerprint))
        change(broken)
        with pytest.raises(ValueError):
            ev.validate_record(
                ev.record("producer", fingerprint={"coverage": broken}, identity_basis="parsed")
            )
    with pytest.raises(ValueError):
        ev.validate_record(
            ev.record(
                "producer", fingerprint={"coverage": fingerprint}, identity_basis="legacy_metadata"
            )
        )


def test_block_validation_is_closed_and_recomputed():
    submission = coverage_py.load_submission(A["report"], A["manifest"])
    producer = ev.producer(coverage_py.run_fingerprint(submission))
    entry = coverage_py.run_entry(submission, producer["id"])
    block = coverage_py.block_of("5" * 64, [entry])
    assert coverage_py.validate_block(block) == block
    assert coverage_py.block_producer_ids(block) == {producer["id"]}
    for change in (
        lambda b: b.update(extra=1),
        lambda b: b.update(runs=[]),
        lambda b: b["runs"][0].update(producer_id="6" * 64),
        lambda b: b["runs"][0].update(run_id="6" * 64),
        lambda b: b["runs"][0]["counts"].update(extra=1),
        lambda b: b["runs"][0]["counts"].update(tests=True),
        lambda b: b["runs"][0]["tool"].update(version="9.9.9"),
        lambda b: b["runs"][0]["environment"]["packages"].update(**{"pytest-cov": "0.0.1"}),
        lambda b: b.update(base_generation_id="short"),
        lambda b: b["runs"].append(dict(b["runs"][0])),
    ):
        broken = json.loads(json.dumps(block))
        change(broken)
        with pytest.raises(ValueError):
            coverage_py.validate_block(broken)


# ---- no execution paths (checker condition 10) ----
DENIED_CALLS = {"eval", "exec", "compile", "__import__"}
DENIED_ATTRIBUTES = {
    ("os", "system"),
    ("os", "popen"),
    ("os", "fork"),
    ("os", "forkpty"),
    ("os", "execv"),
    ("os", "execve"),
    ("os", "execl"),
    ("os", "execlp"),
    ("os", "execvp"),
    ("os", "spawnv"),
    ("os", "spawnl"),
    ("os", "posix_spawn"),
    ("importlib", "import_module"),
    ("subprocess", "run"),
    ("subprocess", "Popen"),
}
DENIED_MODULES = {"subprocess", "ctypes", "runpy", "multiprocessing", "pty", "importlib", "imp"}
PROBE_MODULES = {"importlib.metadata", "json", "platform", "sys"}


def modules(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            yield node.module


def test_coverage_modules_import_only_stdlib_and_the_evidence_model_and_run_nothing():
    files = sorted(SRC.glob("coverage*.py"))
    assert {p.name for p in files} == {"coverage_py.py", "coverage_probe.py"}
    for path in files:
        tree = ast.parse(path.read_text())
        imported = set(modules(tree))
        assert imported
        if path.name == "coverage_probe.py":
            assert imported == PROBE_MODULES, imported
        else:
            for name in imported:
                root = name.split(".")[0]
                assert root in sys.stdlib_module_names or name == "atlas_aci.storage", name
                assert name.split(".")[0] not in DENIED_MODULES, name
            assert ("atlas_aci.storage" in imported) and not any(
                m.startswith("atlas_aci") and m != "atlas_aci.storage" for m in imported
            )
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in DENIED_CALLS, (path.name, node.func.id)
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                assert (node.value.id, node.attr) not in DENIED_ATTRIBUTES, (path.name, node.attr)
            if isinstance(node, ast.Name):
                assert node.id not in DENIED_MODULES - {"importlib"} or path.name == "x", node.id
    # The glob that defines the importer identity sees exactly these files.
    assert coverage_py.importer_sha256() == coverage_py.importer_sha256()
    from_storage = [
        n
        for n in ast.walk(ast.parse((SRC / "coverage_py.py").read_text()))
        if isinstance(n, ast.ImportFrom)
    ]
    assert any(
        n.module == "atlas_aci.storage" and [a.name for a in n.names] == ["evidence"]
        for n in from_storage
    )
