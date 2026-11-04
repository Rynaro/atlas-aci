"""ACI-S21: the operator CLI (coverage-probe, coverage-manifest, import-coverage, status)."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner
from test_s21_support import (
    FIXTURE,
    A,
    B,
    built,
    current_id,
    edited_report,
    inputs_of,
    published_bytes,
    stage_leftovers,
    workspace,
)

from atlas_aci.__main__ import cli
from atlas_aci.providers import coverage_py


def invoke(*args, **kwargs):
    return CliRunner().invoke(cli, [str(a) for a in args], catch_exceptions=False, **kwargs)


def inventory(root):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(Path(root).rglob("*"))
        if p.is_file() and not p.is_symlink()
    }


# ---- coverage-probe ----
def test_probe_prints_its_own_source_and_nothing_else():
    result = invoke("coverage-probe")
    assert result.exit_code == 0
    assert result.output == (Path(coverage_py.__file__).parent / "coverage_probe.py").read_text()
    compile(result.output, "probe", "exec")


def site_with_fake_distributions(tmp_path, **versions):
    """Real installed-metadata directories (``*.dist-info/METADATA``) for the probe to read."""
    site = tmp_path / "site"
    site.mkdir()
    for name, version in versions.items():
        dist = site / f"{name.replace('-', '_')}-{version}.dist-info"
        dist.mkdir()
        (dist / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
    return site


WRAPPER = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "exec(compile(sys.stdin.read(), 'coverage_probe', 'exec'), {'__name__': '__main__'})"
)


def test_probe_output_runs_isolated_and_is_a_valid_environment(tmp_path):
    probe = invoke("coverage-probe").output
    site = site_with_fake_distributions(tmp_path, **{"coverage": "7.16.2", "pytest-cov": "7.1.0"})
    done = subprocess.run(
        [sys.executable, "-I", "-c", WRAPPER, str(site)],
        input=probe,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.endswith("\n") and done.stdout.count("\n") == 1
    environment = json.loads(done.stdout)
    assert coverage_py.check_environment(environment) == environment
    assert environment["packages"]["coverage"] == "7.16.2"
    assert environment["packages"]["pytest-cov"] == "7.1.0"
    assert environment["python"]["implementation"] == sys.implementation.name
    assert environment["python"]["cache_tag"] == sys.implementation.cache_tag
    assert (
        done.stdout
        == json.dumps(environment, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    )


def test_probe_fails_closed_when_a_package_is_missing(tmp_path):
    probe = invoke("coverage-probe").output
    # `python -I -` exactly as documented; a bare site has no coverage / pytest-cov installed.
    done = subprocess.run(
        [sys.executable, "-I", "-S", "-"], input=probe, capture_output=True, text=True, check=False
    )
    assert done.returncode == 1 and done.stdout == ""
    assert "is not installed" in done.stderr


# ---- coverage-manifest ----
def test_manifest_is_byte_deterministic_and_reproduces_the_committed_sidecar(tmp_path):
    repo = workspace(tmp_path)
    outputs = []
    for run in (A, B):
        first, second = (
            tmp_path / f"{run['report'].parent.name}-1.json",
            tmp_path / f"{run['report'].parent.name}-2.json",
        )
        for target in (first, second):
            result = invoke(
                "coverage-manifest",
                "--repo",
                repo,
                "--environment",
                run["environment"],
                "--output",
                target,
                run["report"],
            )
            assert result.exit_code == 0, result.output
        assert first.read_bytes() == second.read_bytes() == run["manifest"].read_bytes()
        outputs.append(first.read_bytes())
    assert outputs[0] != outputs[1] and outputs[0].endswith(b"}\n")


def test_manifest_reads_only_and_never_writes_inside_the_repo(tmp_path):
    repo = workspace(tmp_path)
    before = inventory(repo)
    inside = repo / "sidecar.json"
    result = invoke(
        "coverage-manifest",
        "--repo",
        repo,
        "--environment",
        A["environment"],
        "--output",
        inside,
        A["report"],
    )
    assert result.exit_code == 1 and not inside.exists()
    nested = repo / "deeper" / "sidecar.json"
    result = invoke(
        "coverage-manifest",
        "--repo",
        repo,
        "--environment",
        A["environment"],
        "--output",
        nested,
        A["report"],
    )
    assert result.exit_code == 1 and not nested.exists() and not nested.parent.exists()
    assert inventory(repo) == before
    ok = tmp_path / "ok.json"
    result = invoke(
        "coverage-manifest",
        "--repo",
        repo,
        "--environment",
        A["environment"],
        "--output",
        ok,
        A["report"],
    )
    assert result.exit_code == 0 and inventory(repo) == before and ok.exists()


def test_manifest_rejects_symlinks_and_paths_outside_the_repo(tmp_path):
    repo = workspace(tmp_path)
    target = repo / "app/uni.py"
    outside = tmp_path / "outside.py"
    outside.write_bytes(target.read_bytes())
    target.unlink()
    target.symlink_to(outside)
    out = tmp_path / "m.json"
    result = invoke(
        "coverage-manifest",
        "--repo",
        repo,
        "--environment",
        A["environment"],
        "--output",
        out,
        A["report"],
    )
    assert (
        result.exit_code == 1
        and not out.exists()
        and "symlink" in (result.output + result.stderr).lower()
    )
    # A directory symlink in the middle of a measured path is refused as well.
    repo2 = workspace(tmp_path, "repo2")
    moved = tmp_path / "moved-app"
    (repo2 / "app").rename(moved)
    (repo2 / "app").symlink_to(moved, target_is_directory=True)
    result = invoke(
        "coverage-manifest",
        "--repo",
        repo2,
        "--environment",
        A["environment"],
        "--output",
        out,
        A["report"],
    )
    assert result.exit_code == 1 and not out.exists()
    # A report naming a path outside the repository never gets a hash.
    escaping = edited_report(
        tmp_path,
        lambda d: d["files"].update({"../outside.py": d["files"].pop("app/uni.py")}),
        "escape.json",
    )
    result = invoke(
        "coverage-manifest",
        "--repo",
        workspace(tmp_path, "repo3"),
        "--environment",
        A["environment"],
        "--output",
        out,
        escaping,
    )
    assert result.exit_code == 1 and not out.exists()


def test_manifest_names_a_missing_test_file_with_a_rootdir_hint(tmp_path):
    repo = workspace(tmp_path)
    (repo / "tests/test_forms.py").unlink()
    out = tmp_path / "m.json"
    with pytest.raises(coverage_py.CoverageImportError) as exc:
        coverage_py.build_manifest(repo, A["report"], A["environment"])
    assert exc.value.code in {"manifest_incomplete", "source_unavailable"}
    result = invoke(
        "coverage-manifest",
        "--repo",
        repo,
        "--environment",
        A["environment"],
        "--output",
        out,
        A["report"],
    )
    assert result.exit_code == 1 and not out.exists()


def test_manifest_requires_a_matching_environment(tmp_path):
    repo = workspace(tmp_path)
    environment = json.loads(A["environment"].read_text())
    environment["packages"]["coverage"] = "7.99.0"
    wrong = tmp_path / "env.json"
    wrong.write_text(json.dumps(environment))
    out = tmp_path / "m.json"
    result = invoke(
        "coverage-manifest", "--repo", repo, "--environment", wrong, "--output", out, A["report"]
    )
    assert result.exit_code == 1 and not out.exists()
    wrong.write_text("{}")
    result = invoke(
        "coverage-manifest", "--repo", repo, "--environment", wrong, "--output", out, A["report"]
    )
    assert result.exit_code == 1 and not out.exists()


# ---- import-coverage and status ----
def test_import_coverage_prints_one_json_line_and_status_shows_the_block(tmp_path):
    repo, graph = built(tmp_path, root=tmp_path / "root")
    result = invoke(
        "import-coverage",
        "--repo",
        repo,
        "--graph-root",
        tmp_path / "root",
        "--manifest",
        A["manifest"],
        A["report"],
    )
    assert result.exit_code == 0
    assert result.stdout.endswith("\n") and result.stdout.count("\n") == 1
    stats = json.loads(result.stdout)
    assert stats["status"] == "imported" and stats["runs"] == 1
    assert stats["generation_id"] == current_id(graph) and stats["test_executes"] == 66
    status = json.loads(invoke("status", "--repo", repo, "--graph-root", tmp_path / "root").stdout)
    (block,) = status["execution_observations"]
    assert block["format"] == "atlas-execution-observations.v1"
    assert block["semantics"] == "observed_execution"
    assert block["base_generation_id"] == stats["base_generation_id"]
    assert [r["run_id"] for r in block["runs"]] == [stats["run_id"]]
    assert status["generation_id"] == stats["generation_id"]
    assert inputs_of(graph)["coverage"]["runs"][0]["run_id"] == stats["run_id"]
    # Re-running is idempotent and says so.
    again = json.loads(
        invoke(
            "import-coverage",
            "--repo",
            repo,
            "--graph-root",
            tmp_path / "root",
            "--manifest",
            A["manifest"],
            A["report"],
        ).stdout
    )
    assert again["status"] == "unchanged" and again["generation_id"] == stats["generation_id"]
    # A build generation reports an empty list, never the block.
    other, _ = built(tmp_path, "other", root=tmp_path / "other-root")
    plain = json.loads(
        invoke("status", "--repo", other, "--graph-root", tmp_path / "other-root").stdout
    )
    assert plain["execution_observations"] == []


def test_import_coverage_rejection_is_exit_one_with_empty_stdout(tmp_path):
    repo, graph = built(tmp_path, root=tmp_path / "root")
    before = published_bytes(graph)
    broken = tmp_path / "broken.json"
    broken.write_text("{")
    result = CliRunner().invoke(
        cli,
        [
            "import-coverage",
            "--repo",
            str(repo),
            "--graph-root",
            str(tmp_path / "root"),
            "--manifest",
            str(A["manifest"]),
            str(broken),
        ],
    )
    assert result.exit_code == 1 and result.stdout == ""
    assert "report_invalid" in result.stderr or "report_invalid" in result.output
    assert published_bytes(graph) == before and stage_leftovers(graph) == []


def test_usage_errors_are_exit_two():
    runner = CliRunner()
    assert (
        runner.invoke(cli, ["import-coverage", "--repo", str(FIXTURE), str(A["report"])]).exit_code
        == 2
    )
    assert (
        runner.invoke(
            cli, ["coverage-manifest", "--repo", str(FIXTURE), str(A["report"])]
        ).exit_code
        == 2
    )
    assert (
        runner.invoke(cli, ["coverage-manifest", "--environment", str(A["environment"])]).exit_code
        == 2
    )
    assert (
        runner.invoke(
            cli, ["import-coverage", "--repo", str(FIXTURE), "--manifest", str(A["manifest"])]
        ).exit_code
        == 2
    )
    assert os.path.exists(A["report"])
