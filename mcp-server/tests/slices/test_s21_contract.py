"""ACI-S21 acceptance criteria: per-test Python coverage observations, real artifacts only.

Every repository is a tmp copy of ``tests/fixtures/coverage/project`` built by the real
indexer with an external graph root; imports use the committed coverage.py 7.16.2 reports
and the sidecars `atlas-aci coverage-manifest` wrote for them. No mocks of behavior.
"""

import asyncio
import json
import re

import pytest
from click.testing import CliRunner
from test_s16_support import INDEX, MANIFEST
from test_s16_support import imported as scip_imported
from test_s21_support import (
    LABELS,
    PROVENANCE,
    A,
    built,
    canonical_set,
    coverage_producers,
    coverage_records,
    current_id,
    edited_manifest,
    edited_report,
    import_run,
    imported,
    inputs_of,
    observed,
    published_bytes,
    records,
    remade_manifest,
    report_json,
    run_id,
    sha,
    stage_leftovers,
    status_of,
    workspace,
    write_json,
)

from atlas_aci.__main__ import cli
from atlas_aci.codegraph import CodeGraph
from atlas_aci.config import Config
from atlas_aci.providers import coverage_py
from atlas_aci.server import build_server_state, call_tool_result
from atlas_aci.storage import evidence as ev

RUN_A = LABELS["a"]


def rows_of(run):
    rows = LABELS[run]["rows"]
    assert rows, "the labels must not be empty"
    return rows


# ---- AC-S21-001 ----
def test_test_execution_mapping(tmp_path):
    repo, graph, result = imported(tmp_path)
    assert result["status"] == "imported" and result["test_executes"] == len(RUN_A["rows"])
    by_id = {r["id"]: r for r in records(graph)}
    found = observed(graph)
    labels = rows_of("a")
    for label in labels:
        key = (label["nodeid"], label["path"], label["line"])
        assert len(found.get(key, [])) == 1, key
        item, occurrence, _producer = found[key][0]
        source, target = by_id[item["source_entity_id"]], by_id[item["target_entity_id"]]
        assert item["relation"] == "test_executes" and item["resolution"] == "EXTRACTED"
        assert source["kind"] == "coverage_test" and source["name"] == label["nodeid"]
        assert source["path"] == label["nodeid"].partition("::")[0]
        assert target == ev.file_entity(label["path"], "python")
        assert occurrence["kind"] == "execution" and occurrence["entity_id"] == target["id"]
        assert occurrence["line"] == occurrence["end_line"] == label["line"]
        assert (occurrence["start_byte"], occurrence["end_byte"]) == (
            label["start_byte"],
            label["end_byte"],
        )
        snapshot = (repo / label["path"]).read_bytes()
        assert snapshot[occurrence["start_byte"] : occurrence["end_byte"]] == label["text"].encode()
        assert occurrence["role"] == "observed_execution:" + "+".join(label["phases"])
        assert (occurrence["column"], occurrence["end_column"]) == (
            0,
            label["end_byte"] - label["start_byte"],
        )
        assert item["raw_target"] == f"{label['path']}:{label['line']}"
    assert set(found) == {(r["nodeid"], r["path"], r["line"]) for r in labels}
    assert sum(len(v) for v in found.values()) == len(labels)

    # Lines whose only context is the default `""` create no observation at all.
    unattributed = {
        (path, line) for path, lines in RUN_A["unattributed_lines"].items() for line in lines
    }
    assert unattributed
    assert not unattributed & {(path, line) for _nodeid, path, line in found}
    occurrences = [
        r for r in by_id.values() if r["type"] == "occurrence" and r["kind"] == "execution"
    ]
    assert occurrences and not unattributed & {(o["path"], o["line"]) for o in occurrences}

    # A line executed in the setup AND run phase of one test is ONE assertion.
    both = [r for r in labels if r["phases"] == ["run", "setup"]]
    assert both
    for label in both:
        key = (label["nodeid"], label["path"], label["line"])
        assert len(found[key]) == 1
        assert found[key][0][1]["role"] == "observed_execution:run+setup"
    assert {r["phases"].__str__() for r in labels} >= {"['setup']", "['teardown']"}

    # Terminator-awareness, hand-checked against bytes the importer never saw.
    crlf = (repo / "app/crlf_mod.py").read_bytes()
    crlf_lines = crlf.split(b"\r\n")
    assert b"\r" not in b"".join(crlf_lines) and crlf.count(b"\r\n") == 12
    row = next(r for r in labels if r["path"] == "app/crlf_mod.py" and r["line"] == 5)
    assert crlf_lines[4] == b"    return x * 2" == row["text"].encode()
    assert (row["start_byte"], row["end_byte"]) == (51, 67) and crlf[67:69] == b"\r\n"
    row = next(r for r in labels if r["path"] == "app/ff_mod.py" and r["line"] == 12)
    ff = (repo / "app/ff_mod.py").read_bytes()
    assert ff.count(b"\x0c") == 3 and ff.split(b"\n")[11] == b"    return 2"
    assert (row["start_byte"], row["end_byte"]) == (127, 139)
    sep = (repo / "app/sep.py").read_bytes()
    assert sep.count("\u2028".encode()) == 2 and len(sep.splitlines()) == 10
    assert len(sep.decode().splitlines()) == 12  # str.splitlines would shift every later line
    row = next(r for r in labels if r["path"] == "app/sep.py" and r["line"] == 6)
    assert sep.split(b"\n")[5] == b'    return SEPARATOR.split("\\u2028")' == row["text"].encode()
    assert (row["start_byte"], row["end_byte"]) == (56, 92)
    uni = (repo / "app/uni.py").read_bytes()
    row = next(r for r in labels if r["path"] == "app/uni.py" and r["line"] == 7)
    assert uni.split(b"\n")[6].decode("utf-8") == '    return text.upper() + "!"  # naïve'
    assert (row["start_byte"], row["end_byte"]) == (133, 172)
    assert row["end_byte"] - row["start_byte"] == 39 != len(row["text"])  # bytes, not characters
    row = next(r for r in labels if r["path"] == "app/uni.py" and r["line"] == 11)
    assert row["text"] == '    return len("日本語")' and (row["start_byte"], row["end_byte"]) == (
        188,
        215,
    )

    # Each parametrized variant is a distinct test; the last `|` is the phase separator.
    names = {
        r["name"] for r in records(graph) if r["type"] == "entity" and r["kind"] == "coverage_test"
    }
    assert "tests/test_params.py::test_pipe[a|b]" in names
    assert "tests/test_params.py::test_pipe[plain]" in names
    assert len({n for n in names if n.startswith("tests/test_params.py::test_clamp_param[")}) == 2


# ---- AC-S21-002 ----
def test_run_provenance(tmp_path):
    repo, graph, result = imported(tmp_path)
    expected_run = run_id("a")
    assert result["run_id"] == expected_run
    environment = json.loads(A["environment"].read_text())
    by_id = {r["id"]: r for r in records(graph)}
    producers = coverage_producers(graph)
    assert len(producers) == 1
    (producer,) = producers
    fingerprint = producer["fingerprint"]["coverage"]
    assert fingerprint["run_id"] == expected_run
    assert fingerprint["tool"] == {"name": "coverage.py", "version": "7.16.2"}
    assert (fingerprint["report_format"], fingerprint["context_mode"]) == (3, "pytest-cov:test")
    assert fingerprint["environment"] == environment
    assert environment["python"]["cache_tag"] == "cpython-312"
    assert fingerprint["artifact_sha256"] == PROVENANCE["runs"]["a"]["artifact_sha256"]
    assert fingerprint["manifest_sha256"] == PROVENANCE["runs"]["a"]["manifest_sha256"]
    assert fingerprint["environment_sha256" if False else "importer_sha256"] == (
        coverage_py.importer_sha256()
    )
    assert fingerprint["report_timestamp"] == report_json("a")["meta"]["timestamp"]
    assert fingerprint["commit"] == {"state": "unavailable"}
    assert (
        fingerprint
        == coverage_py.run_fingerprint_of(inputs_of(graph)["coverage"]["runs"][0])["coverage"]
    )
    assert (
        ev.coverage_run_id(
            PROVENANCE["runs"]["a"]["artifact_sha256"], PROVENANCE["runs"]["a"]["manifest_sha256"]
        )
        == expected_run
    )

    assertions = [
        r for r in by_id.values() if r["type"] == "assertion" and r["relation"] == "test_executes"
    ]
    assert assertions
    assert {by_id[by_id[a["evidence_id"]]["producer_id"]]["id"] for a in assertions} == {
        producer["id"]
    }
    for item in assertions:
        proof = by_id[item["evidence_id"]]
        assert proof["producer_id"] == producer["id"]
        assert by_id[proof["occurrence_id"]]["kind"] == "execution"
    block = inputs_of(graph)["coverage"]
    assert [r["producer_id"] for r in block["runs"]] == [producer["id"]]
    status = status_of(repo, graph.graph_root)
    (shown,) = status["execution_observations"]
    assert shown["runs"][0]["producer_id"] == producer["id"]
    assert shown["runs"][0]["run_id"] == expected_run
    assert shown["runs"][0]["environment"] == environment
    # The run identity is content-derived: the build producer is untouched and distinct.
    assert block["base_generation_id"] != current_id(graph)
    assert producer["id"] == ev.producer(producer["fingerprint"])["id"]


# ---- AC-S21-003 ----
def clean(graph, before_bytes, before_id, repo):
    assert published_bytes(graph) == before_bytes
    assert current_id(graph) == before_id
    assert stage_leftovers(graph) == []
    assert coverage_producers(graph) == []
    assert status_of(repo, graph.graph_root)["execution_observations"] == []


def test_coverage_source_mismatch(tmp_path):
    cases = 0

    # (a) a measured file edited before the build
    repo = workspace(tmp_path, "edit-measured")
    (repo / "app/uni.py").write_bytes((repo / "app/uni.py").read_bytes() + b"# drift\n")
    graph = CodeGraph(repo, graph_root=tmp_path / "root-a")
    graph.build()
    before, gen = published_bytes(graph), current_id(graph)
    with pytest.raises(coverage_py.CoverageImportError) as exc:
        import_run(graph, "a")
    assert exc.value.code == "source_mismatch" and "app/uni.py" in str(exc.value)
    clean(graph, before, gen, repo)
    cases += 1

    # (b) a test file edited before the build
    repo = workspace(tmp_path, "edit-test")
    (repo / "tests/test_basic.py").write_bytes(
        (repo / "tests/test_basic.py").read_bytes() + b"# drift\n"
    )
    graph = CodeGraph(repo, graph_root=tmp_path / "root-b")
    graph.build()
    before, gen = published_bytes(graph), current_id(graph)
    with pytest.raises(coverage_py.CoverageImportError) as exc:
        import_run(graph, "a")
    assert exc.value.code == "test_source_mismatch" and "tests/test_basic.py" in str(exc.value)
    clean(graph, before, gen, repo)
    cases += 1

    # (c) a measured path whose observation is `failed`; the sidecar is re-made over the tree
    repo = workspace(tmp_path, "failed")
    (repo / "app/uni.py").write_text("def broken(:\n")
    graph = CodeGraph(repo, graph_root=tmp_path / "root-c")
    stats = graph.build()
    assert stats["files_failed"] == 1
    remade = remade_manifest(tmp_path, repo, A["report"], name="remade-c.json")
    before, gen = published_bytes(graph), current_id(graph)
    with pytest.raises(coverage_py.CoverageImportError) as exc:
        graph.import_coverage(A["report"], remade)
    assert exc.value.code == "document_not_indexed" and "app/uni.py" in str(exc.value)
    clean(graph, before, gen, repo)
    cases += 1

    # (d) a line beyond EOF: synthetic report + a sidecar re-made by the real code
    repo, graph = built(tmp_path, "eof", root=tmp_path / "root-d")
    lines = len((repo / "app/aio.py").read_bytes().splitlines())
    report = edited_report(
        tmp_path,
        lambda d: d["files"]["app/aio.py"]["contexts"].update(
            {str(lines + 1): ["tests/test_forms.py::test_async|run"]}
        ),
        "eof.json",
    )
    remade = remade_manifest(tmp_path, repo, report, name="remade-d.json")
    before, gen = published_bytes(graph), current_id(graph)
    with pytest.raises(coverage_py.CoverageImportError) as exc:
        graph.import_coverage(report, remade)
    assert exc.value.code == "line_out_of_range" and f"app/aio.py:{lines + 1}" in str(exc.value)
    clean(graph, before, gen, repo)
    cases += 1
    assert cases == 4


# ---- AC-S21-004 ----
FORBIDDEN = re.compile(
    r"covers|covered|verified|verifies|asserts|asserted|passed|passes|tested_by|proves",
    re.IGNORECASE,
)


def walk(value, path=()):
    if isinstance(value, dict):
        for key, child in value.items():
            yield (*path, str(key)), key
            yield from walk(child, (*path, str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, (*path, str(index)))
    else:
        yield path, value


def test_no_assertion_inference(tmp_path):
    repo, graph, _ = imported(tmp_path)
    status = status_of(repo, graph.graph_root)
    (block,) = status["execution_observations"]
    assert block["semantics"] == "observed_execution"
    assert block["assertion_inference"] == "not_performed"
    assert block["test_outcome"] == "not_recorded"
    assert block["runs"] and all(run["semantics"] == "observed_execution" for run in block["runs"])
    assert block["counts"] == {
        "runs": 1,
        "tests": 14,
        "observed_lines": 48,
        "test_executes": len(RUN_A["rows"]),
    }
    scanned = list(walk(block))
    assert scanned
    for location, leaf in scanned:
        assert not (isinstance(leaf, str) and FORBIDDEN.search(leaf)), (location, leaf)
    assert FORBIDDEN.search(json.dumps(inputs_of(graph)["coverage"])) is None
    assert {item["name"] for item in block["limitations"]} == set(coverage_py.LIMITATIONS)
    assert "test_outcome" in {item["name"] for item in block["limitations"]}

    # Everything a coverage producer cites is an observed execution, nothing else.
    everything = {r["id"]: r for r in records(graph)}
    producer_ids = {p["id"] for p in coverage_producers(graph)}
    cited = [
        r
        for r in everything.values()
        if r["type"] == "evidence" and r["producer_id"] in producer_ids
    ]
    assert cited
    for proof in cited:
        occurrence = everything[proof["occurrence_id"]]
        assert occurrence["kind"] == "execution"
        assert occurrence["role"].startswith("observed_execution:")
    claims = [
        r
        for r in everything.values()
        if r["type"] == "assertion" and r["evidence_id"] in {c["id"] for c in cited}
    ]
    assert claims and {c["relation"] for c in claims} == {"test_executes"}

    # A line coverage.py reports as `missing` has no record and no status claim.
    missing = {
        (path, line) for path, entry in RUN_A["files"].items() for line in entry["missing_lines"]
    }
    assert missing
    recorded = {
        (o["path"], o["line"])
        for o in everything.values()
        if o["type"] == "occurrence" and o["kind"] == "execution"
    }
    assert not missing & recorded
    assert "missing" not in json.dumps(block)

    # Not reachable over MCP: search_symbol for a test id finds nothing.
    config = Config(repo=repo, memex_root=tmp_path / "memex", max_calls_per_minute=0)
    enforcement, memex, factory = build_server_state(config)
    nodeid = "tests/test_basic.py::test_add"
    assert any(r["type"] == "entity" and r["name"] == nodeid for r in everything.values())

    async def run():
        try:
            for name in (nodeid, "test_add", "tests/test_basic.py"):
                result = await call_tool_result(
                    "search_symbol", {"name": name}, config, enforcement, memex, factory
                )
                assert not result.isError, result
                text = result.content[0].text
                assert "coverage_test" not in text and nodeid not in text
        finally:
            factory.close()

    asyncio.run(run())


# ---- AC-S21-005 ----
def test_coverage_reimport(tmp_path):
    repo, graph, first = imported(tmp_path, root=tmp_path / "root-1")
    g1 = first["generation_id"]
    assert first["status"] == "imported" and g1 == current_id(graph)
    before = published_bytes(graph)
    reference = canonical_set(graph)
    assert reference[0] and reference[1] and reference[2]
    again = import_run(graph, "a")
    assert again["status"] == "unchanged" and again["generation_id"] == g1
    assert published_bytes(graph) == before and canonical_set(graph) == reference
    assert stage_leftovers(graph) == []

    # A fresh graph root over the same repository path reproduces the same generation.
    fresh = CodeGraph(repo, graph_root=tmp_path / "root-2")
    fresh.build()
    second = import_run(fresh, "a")
    assert second["status"] == "imported" and second["generation_id"] == g1
    assert canonical_set(fresh) == reference

    # A -> B -> A: re-importing a run that is already part of the generation is a no-op.
    accumulated = import_run(graph, "b")
    assert accumulated["status"] == "imported" and accumulated["generation_id"] != g1
    before_ab = published_bytes(graph)
    back = import_run(graph, "a")
    assert back["status"] == "unchanged" and back["generation_id"] == accumulated["generation_id"]
    assert published_bytes(graph) == before_ab


# ---- AC-S21-006 ----
def test_distinct_run_preserved(tmp_path):
    repo, graph, only_a = imported(tmp_path, root=tmp_path / "root-ab")
    both = import_run(graph, "b")
    assert both["status"] == "imported" and both["runs"] == 2
    g_ab = both["generation_id"]
    assert g_ab != only_a["generation_id"]
    block = inputs_of(graph)["coverage"]
    assert len(block["runs"]) == 2
    ids = [run["run_id"] for run in block["runs"]]
    assert ids == sorted(ids) == sorted([run_id("a"), run_id("b")])
    assert len({run["producer_id"] for run in block["runs"]}) == 2
    environments = {run["run_id"]: run["environment"] for run in block["runs"]}
    assert environments[run_id("a")] != environments[run_id("b")]
    assert environments[run_id("a")]["python"]["cache_tag"] == "cpython-312"
    assert environments[run_id("b")]["python"]["cache_tag"] == "cpython-313"
    assert block["base_generation_id"] == only_a["base_generation_id"]

    producers = {p["fingerprint"]["coverage"]["run_id"]: p["id"] for p in coverage_producers(graph)}
    assert set(producers) == {run_id("a"), run_id("b")}
    found = observed(graph)
    shared = [r for r in rows_of("a") if (r["nodeid"], r["path"], r["line"]) in found]
    assert shared and len(shared) == len(rows_of("b"))
    for label in shared:
        entries = found[(label["nodeid"], label["path"], label["line"])]
        assert len(entries) == 2
        assert {producer for _i, _o, producer in entries} == set(producers.values())
        assert len({item["id"] for item, _o, _p in entries}) == 2

    status = status_of(repo, graph.graph_root)
    (shown,) = status["execution_observations"]
    assert [run["run_id"] for run in shown["runs"]] == ids
    assert shown["counts"]["runs"] == 2
    assert shown["counts"]["test_executes"] == 2 * len(rows_of("a"))
    export = tmp_path / "ab.jsonl"
    graph.export_jsonl(export)
    exported = [json.loads(line) for line in export.read_text().splitlines()][1:]
    exported_producers = [
        r["id"]
        for r in exported
        if r["type"] == "producer" and "coverage" in (r["fingerprint"] or {})
    ]
    assert sorted(exported_producers) == sorted(producers.values())

    # B then A, in another root, is the same generation with the same canonical records.
    other, other_graph = built(tmp_path, "repo-ba", root=tmp_path / "root-ba")
    assert other != repo
    reverse = CodeGraph(repo, graph_root=tmp_path / "root-ba")
    reverse.build()
    import_run(reverse, "b")
    last = import_run(reverse, "a")
    assert last["generation_id"] == g_ab
    assert canonical_set(reverse) == canonical_set(graph)
    assert other_graph is not None


# ---- AC-S21-007 ----
def _rebound(tmp_path, report, name="rebound.json"):
    """The committed sidecar re-pointed at an edited report (isolates the report defect)."""
    return edited_manifest(
        tmp_path, lambda d: d.update(artifact_sha256=sha(report.read_bytes())), name=name
    )


def _manifest_case(tmp_path, repo, mutate):
    return A["report"], edited_manifest(tmp_path, mutate)


def _drop(key, path):
    return lambda d: d[key].pop(path)


def _case_missing_manifest(tmp_path, repo):
    return A["report"], tmp_path / "nowhere.json"


def _case_missing_measured(tmp_path, repo):
    return _manifest_case(tmp_path, repo, _drop("measured_files", "app/uni.py"))


def _case_extra_measured(tmp_path, repo):
    return _manifest_case(
        tmp_path, repo, lambda d: d["measured_files"].update({"app/extra.py": "0" * 64})
    )


def _case_missing_test_file(tmp_path, repo):
    return _manifest_case(tmp_path, repo, _drop("test_files", "tests/test_forms.py"))


def _case_artifact_hash(tmp_path, repo):
    return _manifest_case(tmp_path, repo, lambda d: d.update(artifact_sha256="f" * 64))


def _case_no_environment(tmp_path, repo):
    return _manifest_case(tmp_path, repo, lambda d: d.pop("environment"))


def _case_bad_environment(tmp_path, repo):
    return _manifest_case(
        tmp_path, repo, lambda d: d["environment"]["packages"].update(unexpected="1")
    )


def _case_show_contexts_false(tmp_path, repo):
    report = edited_report(tmp_path, lambda d: d["meta"].update(show_contexts=False), "nosc.json")
    return report, _rebound(tmp_path, report)


def _case_contexts_absent(tmp_path, repo):
    def mutate(data):
        for entry in data["files"].values():
            entry.pop("contexts")

    report = edited_report(tmp_path, mutate, "noctx.json")
    return report, _rebound(tmp_path, report)


def _case_absolute_path(tmp_path, repo):
    def mutate(data):
        data["files"]["/abs/app/calc.py"] = data["files"].pop("app/calc.py")

    report = edited_report(tmp_path, mutate, "abs.json")
    return report, A["manifest"]


def _case_format(tmp_path, repo):
    report = edited_report(tmp_path, lambda d: d["meta"].update(format=2), "fmt.json")
    return report, _rebound(tmp_path, report)


def _case_unknown_version(tmp_path, repo):
    report = edited_report(tmp_path, lambda d: d["meta"].update(version="9.9.9"), "ver.json")
    return report, A["manifest"]


def _case_env_version(tmp_path, repo):
    return _manifest_case(
        tmp_path, repo, lambda d: d["environment"]["packages"].update(coverage="7.99.0")
    )


def _case_static_context(tmp_path, repo):
    def mutate(data):
        for entry in data["files"].values():
            entry["contexts"] = {
                line: [f"ci|{c}" if c else "ci" for c in values]
                for line, values in entry["contexts"].items()
            }

    report = edited_report(tmp_path, mutate, "static.json")
    return report, _rebound(tmp_path, report)


def _case_no_test_contexts(tmp_path, repo):
    def mutate(data):
        for entry in data["files"].values():
            entry["contexts"] = {line: [""] for line in entry["contexts"]}

    report = edited_report(tmp_path, mutate, "flat.json")
    return report, _rebound(tmp_path, report)


def _case_rootdir(tmp_path, repo):
    def mutate(data):
        for entry in data["files"].values():
            entry["contexts"] = {
                line: [f"proj/{c}" if c else c for c in values]
                for line, values in entry["contexts"].items()
            }

    report = edited_report(tmp_path, mutate, "rootdir.json")
    return report, _rebound(tmp_path, report)


CASES = {
    "nonexistent_manifest": ("manifest_unreadable", _case_missing_manifest),
    "missing_measured_file": ("manifest_incomplete", _case_missing_measured),
    "extra_measured_file": ("manifest_extra", _case_extra_measured),
    "missing_test_file": ("manifest_incomplete", _case_missing_test_file),
    "artifact_sha_mismatch": ("artifact_hash_mismatch", _case_artifact_hash),
    "no_environment": ("environment_invalid", _case_no_environment),
    "malformed_environment": ("environment_invalid", _case_bad_environment),
    "show_contexts_false": ("contexts_absent", _case_show_contexts_false),
    "contexts_absent": ("contexts_absent", _case_contexts_absent),
    "absolute_path": ("absolute_path", _case_absolute_path),
    "report_format": ("unsupported_report_format", _case_format),
    "unknown_version": ("unknown_producer_version", _case_unknown_version),
    "environment_version": ("environment_tool_mismatch", _case_env_version),
    "static_context": ("unsupported_context", _case_static_context),
    "no_test_contexts": ("no_test_contexts", _case_no_test_contexts),
    "wrong_rootdir": ("manifest_incomplete", _case_rootdir),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_missing_coverage_identity(tmp_path, name):
    code, case = CASES[name]
    repo, graph = built(tmp_path, root=tmp_path / "root")
    report, manifest = case(tmp_path, repo)
    before, gen = published_bytes(graph), current_id(graph)
    with pytest.raises(coverage_py.CoverageImportError) as exc:
        graph.import_coverage(report, manifest)
    assert exc.value.code == code, str(exc.value)
    if name == "wrong_rootdir":
        assert "rootdir" in str(exc.value)
    clean(graph, before, gen, repo)
    # The same rejection through the operator CLI: exit 1 with nothing on stdout (exit 2 when
    # click itself refuses a missing file).
    result = CliRunner().invoke(
        cli,
        [
            "import-coverage",
            "--repo",
            str(repo),
            "--graph-root",
            str(tmp_path / "root"),
            "--manifest",
            str(manifest),
            str(report),
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == (1 if manifest.exists() else 2) and result.stdout == ""
    clean(graph, before, gen, repo)


def test_missing_coverage_identity_base_scip_and_usage(tmp_path):
    repo, graph, _result = scip_imported(tmp_path)
    before, gen = published_bytes(graph), current_id(graph)
    with pytest.raises(coverage_py.CoverageImportError) as exc:
        import_run(graph, "a")
    assert exc.value.code == "base_kind_unsupported"
    assert published_bytes(graph) == before and current_id(graph) == gen
    assert stage_leftovers(graph) == [] and coverage_producers(graph) == []
    assert status_of(repo)["execution_observations"] == []
    assert INDEX.exists() and MANIFEST.exists()

    # `import-coverage` without --manifest is a usage error (exit 2), not a rejection.
    runner = CliRunner()
    result = runner.invoke(cli, ["import-coverage", "--repo", str(repo), str(A["report"])])
    assert result.exit_code == 2 and "manifest" in result.output.lower()
    assert write_json is not None and canonical_set is not None
    assert coverage_records is not None
