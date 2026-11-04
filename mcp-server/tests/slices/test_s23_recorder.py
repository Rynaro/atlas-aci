"""ACI-S23 recorder contract: docker classification and the V0 guard use fake `docker` shims (no
daemon); the HEAD smoke drives a real stdio server; run-canaries `--server-json` is end to end."""

from __future__ import annotations

import copy
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from test_s23_support import (
    ROOT,
    agg,
    fixture,
    fixture_report,
    make_records,
    manifest,
    real_cost_builds,
    rec,
)


def shim(directory: Path, script: str) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "docker"
    path.write_text("#!/bin/sh\n" + script)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


IMAGE = "ghcr.io/rynaro/atlas-aci@sha256:" + "e" * 64


def test_docker_failures_are_classified_by_exit_code_and_stderr(tmp_path):
    log = tmp_path / "argv.log"
    record = f'echo "$@" >> {log}\n'
    absent = shim(
        tmp_path / "a", record + 'echo "Error response from daemon: No such image: x" >&2; exit 1\n'
    )
    assert rec.classify_docker(absent, IMAGE)[0] == "image_absent"
    down = shim(
        tmp_path / "b",
        record
        + 'echo "Cannot connect to the Docker daemon at unix:///x. Is the docker daemon running?"'
        + " >&2; exit 1\n",
    )
    assert rec.classify_docker(down, IMAGE)[0] == "docker_unavailable"
    odd = shim(tmp_path / "c", record + 'echo "something unexpected" >&2; exit 125\n')
    assert rec.classify_docker(odd, IMAGE)[0] == "docker_unavailable"
    assert rec.classify_docker(str(tmp_path / "no-such-docker"), IMAGE)[0] == "docker_unavailable"
    fine = shim(tmp_path / "d", record + "exit 0\n")
    assert rec.classify_docker(fine, IMAGE) == ("ok", "")
    calls = [line.split() for line in log.read_text().splitlines()]
    assert len(calls) == 4 and all(c == ["image", "inspect", IMAGE] for c in calls)  # never a pull


def test_v0_not_run_records_aggregate_as_not_run_not_zero(tmp_path):
    m, sha = manifest()
    stubs = rec.v0_not_run(
        m, sha, "s18-useful-spans", {"probe", "retrieval", "cost"}, "image_absent", "No such image"
    )
    assert {(s["kind"], s["status"], s["not_run_reason"]) for s in stubs} == {
        ("probe", "not_run", "image_absent"),
        ("retrieval", "not_run", "image_absent"),
        ("cost", "not_run", "image_absent"),
    }
    records = make_records(m, sha, real_cost_builds(tmp_path))
    for stub in stubs:
        key = {"probe": "V0", "retrieval": "V0.legacy", "cost": "s18-useful-spans.V0"}[stub["kind"]]
        records[(stub["kind"], key)] = stub
    canonical = fixture_report(m, sha, records)["canonical"]
    rows = [r for r in canonical["retrieval"]["span_matrix"] if r["arm"] == "V0.legacy"]
    assert len(rows) == 3 and all(
        r["status"] == "not_run" and r["reason"] == "image_absent" and r["metrics"] is None
        for r in rows
    )
    assert canonical["cost"]["index.cold_full"]["s18-useful-spans"]["V0"]["status"] == "unmeasured"
    assert [a["status"] for a in canonical["pairing"]["arms"]].count("not_run") == 1


V0_EVENT = (
    "2026-11-04T02:29:18.819485Z [info     ] index_done                     edges=4 "
    "files_indexed={n} files_removed=0 files_skipped=0 rationale=0 refs=7 schema_epoch=5 "
    "symbols={s} unsupported_skipped={{}}"
)


def test_parse_index_done_reads_the_structlog_event():
    done = rec.parse_index_done("noise\n" + V0_EVENT.format(n=7, s=11) + "\n")
    assert done == {
        "edges": 4,
        "files_indexed": 7,
        "files_removed": 0,
        "files_skipped": 0,
        "rationale": 0,
        "refs": 7,
        "schema_epoch": 5,
        "symbols": 11,
        "unsupported_skipped": {},
    }
    assert rec.parse_index_done("nothing here") is None


def v0_index(tmp_path, stderr_line, code=0):
    m, _ = manifest()
    variant = next(v for v in m["variants"] if v["id"] == "V0")
    script = f"cat >&2 <<'EOF'\n{stderr_line}\nEOF\nexit {code}\n"
    docker = shim(tmp_path / "bin", script)
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    return rec.run_v0_index(variant, repo, tmp_path / "cache", docker, 7)


def test_an_empty_or_incomplete_v0_index_aborts_instead_of_recording_zero_yield(tmp_path):
    assert v0_index(tmp_path, V0_EVENT.format(n=7, s=11))["files_indexed"] == 7
    for n, s in ((0, 0), (6, 11), (7, 0), (8, 3)):
        with pytest.raises(rec.RecorderAbort) as error:
            v0_index(tmp_path / f"{n}-{s}", V0_EVENT.format(n=n, s=s))
        assert error.value.code == "v0_index_empty"
    with pytest.raises(rec.RecorderAbort) as error:
        v0_index(tmp_path / "missing", "parse_failed error=offline")
    assert error.value.code == "v0_index_event_missing"
    with pytest.raises(rec.RecorderAbort) as error:
        v0_index(tmp_path / "failed", V0_EVENT.format(n=7, s=11), code=1)
    assert error.value.code == "v0_index_failed"


def test_the_v0_grammar_cache_is_a_verified_declared_input(tmp_path):
    cache = tmp_path / "cache"
    (cache / "libs").mkdir(parents=True)
    (cache / "libs" / "a.so").write_bytes(b"binary")
    declared = tmp_path / "declared.json"
    declared.write_text(json.dumps({"tree_sha256": agg.tree_sha256(cache)}))
    assert rec.verify_cache(cache, declared) == agg.tree_sha256(cache)
    (cache / "libs" / "a.so").write_bytes(b"changed")
    with pytest.raises(rec.RecorderAbort) as error:
        rec.verify_cache(cache, declared)
    assert error.value.code == "grammar_cache_mismatch"


def tiny_repo(path: Path) -> str:
    return rec.det_repo(path, {"a.py": b"def alpha():\n    return 1\n"})


@pytest.mark.parametrize(
    ("code", "program", "deadline"),
    [
        ("transport_error", "import sys; sys.exit(3)", 20.0),
        ("timeout", "import time; time.sleep(60)", 2.0),
    ],
)
def test_transport_failures_abort_the_run(tmp_path, code, program, deadline):
    repo = tmp_path / "repo"
    commit = tiny_repo(repo)
    calls = [{"id": "c0", "tool": "search_symbol", "arguments": {"name": "alpha"}}]
    with pytest.raises(rec.RecorderAbort) as error:
        rec.run_calls(
            calls, repo, commit, [sys.executable, "-c", program], deadline, tmp_path / "l"
        )
    assert error.value.code == code


def test_det_repo_commits_are_deterministic_and_independent_of_the_path(tmp_path):
    files = {"a.py": b"x = 1\n", "d/b.py": b"y = 2\n"}
    one = rec.det_repo(tmp_path / "one", files)
    two = rec.det_repo(tmp_path / "two" / "nested", files)
    assert one == two and len(one) == 40
    assert rec.det_repo(tmp_path / "three", {**files, "c.py": b""}) != one


def test_run_p0_maps_junit_to_ids_and_hardens_the_environment(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "test_x.py").write_text(
        "import os, pytest\n"
        "def test_env():\n"
        "    assert not [k for k in os.environ if k.startswith(('PYTEST_FAKE', 'COVERAGE_'))]\n"
        "    assert 'ATLAS_ACI_S08_DOCKER_IMAGE' not in os.environ\n"
        "@pytest.mark.parametrize('n', [1, 2])\n"
        "def test_param(n):\n"
        "    assert n\n"
        "def test_fails():\n"
        "    assert False\n"
        "@pytest.mark.skip(reason='x')\n"
        "def test_skipped():\n"
        "    pass\n"
        "class TestK:\n"
        "    def test_method(self):\n"
        "        pass\n"
    )
    monkeypatch.setenv("PYTEST_FAKE_OPTION", "1")
    monkeypatch.setenv("COVERAGE_PROCESS_START", "x")
    monkeypatch.setenv("ATLAS_ACI_S08_DOCKER_IMAGE", "img")
    ids = [
        "test_x.py::test_env",
        "test_x.py::test_param",
        "test_x.py::test_fails",
        "test_x.py::test_skipped",
        "test_x.py::TestK::test_method",
    ]
    result = rec.run_p0(ids, project, timeout=120)
    outcomes = {r["node_id"]: (r["testcases"], r["outcome"]) for r in result["results"]}
    assert outcomes == {
        "test_x.py::test_env": (1, "passed"),
        "test_x.py::test_param": (2, "passed"),  # params stripped, both counted
        "test_x.py::test_fails": (1, "failed"),
        "test_x.py::test_skipped": (1, "skipped"),
        "test_x.py::TestK::test_method": (1, "passed"),
    }
    assert result["returncode"] != 0
    # An id pytest cannot find aborts the whole run: every id is `missing` (fail closed).
    absent = rec.run_p0([*ids, "test_x.py::test_absent"], project, timeout=120)
    assert {r["outcome"] for r in absent["results"]} == {"missing"}


def test_measure_index_costs_reports_real_build_counters(tmp_path):
    builds = real_cost_builds(tmp_path)
    assert [b["category"] for b in builds] == [
        "index.cold_full",
        "index.warm_full",
        "index.incremental_noop",
        "index.refresh_after_edit",
    ]
    m, _ = manifest()
    assert list(rec.COUNTERS) == m["cost"]["counters"]
    assert all(set(b["counters"]) == set(rec.COUNTERS) for b in builds)
    assert builds[0]["counters"]["files_indexed"] == 2


def head_server(repo: Path) -> list[str]:
    return [sys.executable, "-m", "atlas_aci", "serve", "--repo", str(repo)]


def test_head_stdio_smoke_records_valid_raw_records_for_both_legacy_policies_and_context(tmp_path):
    from atlas_aci.codegraph import CodeGraph

    m, sha = manifest()
    task = next(t for t in m["tasks"] if t["id"] == "unicode")
    small = copy.deepcopy(m)
    small["tasks"] = [task]
    small["budgets"]["tokens"] = [8000]
    small["budgets"]["max_bytes"] = [32000]
    files = rec.corpus_files(m, ROOT, "s18-useful-spans")
    repo = tmp_path / "v1"
    commit = rec.det_repo(repo, files)
    CodeGraph(repo).build()
    arms = {a["id"]: a for a in m["arms"]}
    wanted = ["V1.legacy", "V1.context.inline"] + (["B.search_read"] if shutil.which("rg") else [])
    sessions = 0
    for arm_id in wanted:
        record = rec.record_retrieval(
            small,
            sha,
            arms[arm_id],
            repo=repo,
            commit=commit,
            server=head_server(repo),
            deadline=60.0,
            work=tmp_path / arm_id,
            argv=["test"],
        )
        by_task = agg._validate_retrieval(small, arms[arm_id], record)
        observations = record["observations"]
        sessions += len(observations)
        assert observations and set(by_task) == {"unicode"}
        assert all(
            o["outcome"] in {"success", "tool_error", "application_error"} for o in observations
        )
        assert record["corpus_commit"] == commit and record["repo_root"] == str(repo)
        fx = fixture()
        ctx = agg.SpanContext(fx, record["repo_root"])
        row = agg._task_row(ctx, small["tasks"][0], observations, fx)
        if arm_id != "V1.context.inline":
            assert row["useful_reference_bytes"] > 0 and row["errors"] == 0
            assert [o["tool"] for o in observations][-1] == "view_file"
        else:
            assert row["useful_inline_bytes"] == 132 and row["bundles"] >= 1
    assert sessions <= 6
    for observation in observations:
        text = observation["result_text"]
        assert json.loads(text) and str(repo) not in agg.normalise(text, str(repo))[0]


def test_server_json_flows_through_run_canaries_and_stdio_runner(tmp_path):
    repo = tmp_path / "repo"
    commit = tiny_repo(repo)
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "tasks.json").write_text(
        json.dumps(
            {
                "schema": "atlas/task-manifest.v1",
                "repository": {"revision": commit},
                "model": None,
                "tasks": [
                    {
                        "id": "view",
                        "tool": "view_file",
                        "arguments": {"path": "a.py", "start_line": 1, "end_line": 1},
                        "expect_text": "alpha",
                    }
                ],
            }
        )
    )
    argv = [
        sys.executable,
        "-m",
        "atlas_aci",
        "--log-level",
        "warning",
        "serve",
        "--repo",
        str(repo),
    ]
    env = {**os.environ, "PYTHONPATH": str(ROOT / "mcp-server/src")}
    base = [
        sys.executable,
        str(ROOT / "scripts/run-canaries.py"),
        "--repo",
        str(repo),
        "--canaries-dir",
        str(tasks),
        "--host",
        "stdio",
        "--deadline",
        "60",
    ]
    out = tmp_path / "result.json"
    done = subprocess.run(
        [*base, "--output", str(out), "--server-json", json.dumps(argv)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert done.returncode == 0, done.stderr
    result = json.loads(out.read_bytes())
    assert result["summary"] == {"failed": 0, "passed": 1, "tasks": 1}
    server = result["inputs"]["adapter"]["server"]
    assert "--log-level" in server and "<repo>" in server  # the custom argv ran, not the default
    # The same argv from a file; invalid values are a typed input error (exit 2).
    spec = tmp_path / "server.json"
    spec.write_text(json.dumps(argv))
    again = subprocess.run(
        [*base, "--output", str(tmp_path / "again.json"), "--server-json", str(spec)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert again.returncode == 0, again.stderr
    bad = subprocess.run(
        [*base, "--output", str(tmp_path / "bad.json"), "--server-json", '["", 3]'],
        capture_output=True,
        text=True,
        env=env,
    )
    assert bad.returncode == 2 and "server-json" in bad.stderr
