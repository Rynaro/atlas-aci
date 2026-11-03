"""ACI-S16: the operator CLI surface (`scip-manifest`, `import-scip`, `status`)."""

import json

from click.testing import CliRunner
from test_s16_support import (
    FIXTURE,
    INDEX,
    MANIFEST,
    built,
    published_bytes,
    stage_leftovers,
    workspace,
)

from atlas_aci.__main__ import cli
from atlas_aci.codegraph import CodeGraph


def out(result):
    return getattr(result, "stdout", result.output)


def test_scip_manifest_reproduces_the_committed_sidecar(tmp_path):
    target = tmp_path / "nested/out.json"
    result = CliRunner().invoke(
        cli,
        [
            "scip-manifest",
            "--repo",
            str(FIXTURE / "project"),
            "--position-encoding",
            "utf-16",
            "--output",
            str(target),
            str(INDEX),
        ],
    )
    assert result.exit_code == 0, result.output
    assert target.read_bytes() == MANIFEST.read_bytes()


def test_scip_manifest_requires_a_declared_permitted_encoding(tmp_path):
    runner = CliRunner()
    base = [
        "scip-manifest",
        "--repo",
        str(FIXTURE / "project"),
        "--output",
        str(tmp_path / "m.json"),
    ]
    missing = runner.invoke(cli, [*base, str(INDEX)])
    assert missing.exit_code == 2 and "position-encoding" in missing.output
    for wrong in ("utf-8", "utf-32"):
        rejected = runner.invoke(cli, [*base, "--position-encoding", wrong, str(INDEX)])
        assert rejected.exit_code == 1 and "encoding_not_permitted" in rejected.output
    bogus = runner.invoke(cli, [*base, "--position-encoding", "ebcdic", str(INDEX)])
    assert bogus.exit_code == 2
    assert not (tmp_path / "m.json").exists()
    # The producer's working tree must contain every indexed document.
    empty = tmp_path / "empty"
    empty.mkdir()
    gone = runner.invoke(
        cli, [*base[:2], str(empty), *base[3:], "--position-encoding", "utf-16", str(INDEX)]
    )
    assert gone.exit_code == 1 and "document_source_unavailable" in gone.output


def test_import_scip_cli_publishes_then_reports_status(tmp_path):
    repo, _graph = built(tmp_path)
    runner = CliRunner()
    imported = runner.invoke(
        cli, ["import-scip", "--repo", str(repo), "--manifest", str(MANIFEST), str(INDEX)]
    )
    assert imported.exit_code == 0, imported.output
    summary = json.loads(out(imported).strip().splitlines()[-1])
    assert summary["status"] == "imported" and summary["references"] == 24
    again = runner.invoke(
        cli, ["import-scip", "--repo", str(repo), "--manifest", str(MANIFEST), str(INDEX)]
    )
    assert (
        again.exit_code == 0
        and json.loads(out(again).strip().splitlines()[-1])["status"] == "unchanged"
    )
    status = json.loads(
        out(runner.invoke(cli, ["status", "--repo", str(repo)])).strip().splitlines()[-1]
    )
    assert status["semantic_imports"][0]["counts"]["definitions"] == 17
    assert status["generation_id"] == summary["generation_id"]


def test_import_scip_cli_rejection_is_a_clean_error_and_changes_nothing(tmp_path):
    repo, graph = built(tmp_path)
    (repo / "src/main.ts").write_text("export const changed = true;\n")
    graph.build()
    before = published_bytes(graph)
    result = CliRunner().invoke(
        cli, ["import-scip", "--repo", str(repo), "--manifest", str(MANIFEST), str(INDEX)]
    )
    assert result.exit_code == 1 and "source_mismatch" in result.output
    assert "Traceback" not in result.output
    assert published_bytes(graph) == before and stage_leftovers(graph) == []


def test_import_scip_cli_honors_the_external_graph_root(tmp_path):
    repo = workspace(tmp_path)
    root = tmp_path / "graphs"
    CodeGraph(repo, graph_root=root).build()
    result = CliRunner().invoke(
        cli,
        [
            "import-scip",
            "--repo",
            str(repo),
            "--graph-root",
            str(root),
            "--manifest",
            str(MANIFEST),
            str(INDEX),
        ],
    )
    assert result.exit_code == 0, result.output
    assert not (repo / ".atlas").exists()
    status = CodeGraph(repo, read_only=True, graph_root=root).status()
    assert status["semantic_imports"][0]["producer"]["version"] == "0.4.0"
