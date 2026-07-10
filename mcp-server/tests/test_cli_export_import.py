"""CLI reachability for A5 (`atlas-aci export` / `atlas-aci import`).

Every AC-A5-* criterion is satisfied by tests calling `CodeGraph.export_jsonl`/
`import_jsonl` directly (see test_export.py) — none of them required the
capability to be reachable by a user. That was the eighteenth defect: the
criteria tested the artefact's properties, never its reachability. These
tests exercise the actual CLI surface (`click.testing.CliRunner`, not a
direct method call) so "the feature exists" and "the feature can be
invoked" are checked separately.

Boundary questions this file asks, per the checker's own pattern ("what
input has the CLI never been shown?"):
- an `import` into a repo whose `schema_epoch` differs from this build's,
- an `export` to a path OUTSIDE the repo,
- an `import` of a file this tool did not write (hand-crafted JSON, and
  plain non-JSON text).

`export`/`import` are deliberately NOT MCP tools (see README.md's "Why
read-only" section for the threat-model argument) — there is no
equivalent "reachable via a served agent" test here, because that
reachability must NOT exist, and `test_server.py`/`enforcement.py`'s
`READ_ONLY_TOOLS` frozenset already pins the complete tool set closed.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from atlas_aci.__main__ import cli
from atlas_aci.codegraph import SCHEMA_EPOCH, CodeGraph


def _write(repo: Path, rel: str, content: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


_FIXTURE = "class Hello\n  def call\n    helper(1)\n  end\n\n  def helper(x)\n    x\n  end\nend\n"


def test_cli_export_creates_a_file_matching_the_direct_api(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "app/hello.rb", _FIXTURE)

    runner = CliRunner()
    result = runner.invoke(cli, ["index", "--repo", str(repo)])
    assert result.exit_code == 0, result.output

    cli_out = tmp_path / "cli-export.jsonl"
    result = runner.invoke(cli, ["export", "--repo", str(repo), str(cli_out)])
    assert result.exit_code == 0, result.output
    assert cli_out.exists()

    # Byte-identical to calling export_jsonl directly against the same DB —
    # the CLI is a thin wrapper, never a second implementation.
    direct_out = tmp_path / "direct-export.jsonl"
    CodeGraph(repo=repo, read_only=True).export_jsonl(direct_out)
    assert cli_out.read_bytes() == direct_out.read_bytes()


def test_cli_export_fails_cleanly_without_an_index(tmp_path: Path) -> None:
    """A user who runs `export` before ever running `index` must get a
    clean, actionable error -- not a raw sqlite3.OperationalError
    traceback for a DB file that doesn't exist yet."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "app/hello.rb", _FIXTURE)

    runner = CliRunner()
    result = runner.invoke(cli, ["export", "--repo", str(repo), str(tmp_path / "out.jsonl")])
    assert result.exit_code != 0
    assert "atlas-aci index" in result.output
    assert not (tmp_path / "out.jsonl").exists()


def test_cli_export_to_a_path_outside_the_repo(tmp_path: Path) -> None:
    """`export` is a CLI-only, operator-invoked command -- unlike an MCP
    tool, it is not subject to `enforcement.assert_path_in_repo`. Writing
    the artefact outside `--repo` (a shared cache dir, a CI artifacts
    directory) is the expected, common case, not an edge case to reject."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "app/hello.rb", _FIXTURE)

    outside = tmp_path / "elsewhere" / "not-under-repo-at-all"

    runner = CliRunner()
    result = runner.invoke(cli, ["index", "--repo", str(repo)])
    assert result.exit_code == 0, result.output
    result = runner.invoke(cli, ["export", "--repo", str(repo), str(outside / "export.jsonl")])
    assert result.exit_code == 0, result.output
    assert (outside / "export.jsonl").exists()


def test_cli_import_reproduces_the_graph_on_a_cold_start(tmp_path: Path) -> None:
    """The cold-start workflow README.md documents: commit the JSONL,
    `import` it on a fresh checkout, skip the re-index entirely."""
    source_repo = tmp_path / "source-repo"
    source_repo.mkdir()
    _write(source_repo, "app/hello.rb", _FIXTURE)

    runner = CliRunner()
    result = runner.invoke(cli, ["index", "--repo", str(source_repo)])
    assert result.exit_code == 0, result.output

    export_path = tmp_path / "export.jsonl"
    result = runner.invoke(cli, ["export", "--repo", str(source_repo), str(export_path)])
    assert result.exit_code == 0, result.output

    cold_start_repo = tmp_path / "cold-start-checkout"
    cold_start_repo.mkdir()
    result = runner.invoke(cli, ["import", "--repo", str(cold_start_repo), str(export_path)])
    assert result.exit_code == 0, result.output

    # No source file exists in cold_start_repo at all -- this proves the
    # graph came from the import, not a hidden re-index.
    graph = CodeGraph(repo=cold_start_repo, read_only=True)
    defs = graph.search_symbol("Hello")["definitions"]
    assert len(defs) == 1
    assert defs[0]["path"] == "app/hello.rb"


def test_cli_import_rejects_a_hand_crafted_file_this_tool_did_not_write(tmp_path: Path) -> None:
    garbage = tmp_path / "garbage.jsonl"
    garbage.write_text(json.dumps({"just": "some json", "not": "an atlas-aci export"}) + "\n")

    target = tmp_path / "target"
    target.mkdir()

    runner = CliRunner()
    result = runner.invoke(cli, ["import", "--repo", str(target), str(garbage)])
    assert result.exit_code != 0
    assert "not a header" in result.output
    # No partial/corrupt DB left behind by a rejected import.
    assert not (target / ".atlas" / f"graph.{SCHEMA_EPOCH}.db").exists()


def test_cli_import_rejects_plain_non_json_text(tmp_path: Path) -> None:
    """The header parse itself must fail cleanly on genuinely non-JSON
    input, not just on well-formed-but-wrong JSON."""
    garbage = tmp_path / "plaintext.jsonl"
    garbage.write_text("this is not json at all\njust plain text\n")

    target = tmp_path / "target"
    target.mkdir()

    runner = CliRunner()
    result = runner.invoke(cli, ["import", "--repo", str(target), str(garbage)])
    assert result.exit_code != 0
    assert "not valid JSON" in result.output


def test_cli_import_rejects_mismatched_schema_epoch(tmp_path: Path) -> None:
    source_repo = tmp_path / "source-repo"
    source_repo.mkdir()
    _write(source_repo, "app/hello.rb", _FIXTURE)

    runner = CliRunner()
    result = runner.invoke(cli, ["index", "--repo", str(source_repo)])
    assert result.exit_code == 0, result.output
    export_path = tmp_path / "export.jsonl"
    result = runner.invoke(cli, ["export", "--repo", str(source_repo), str(export_path)])
    assert result.exit_code == 0, result.output

    lines = export_path.read_text().splitlines()
    header = json.loads(lines[0])
    header["schema_epoch"] = SCHEMA_EPOCH + 1
    lines[0] = json.dumps(header, sort_keys=True, separators=(",", ":"))
    wrong_epoch = tmp_path / "wrong-epoch.jsonl"
    wrong_epoch.write_text("\n".join(lines) + "\n")

    target = tmp_path / "target"
    target.mkdir()
    result = runner.invoke(cli, ["import", "--repo", str(target), str(wrong_epoch)])
    assert result.exit_code != 0
    assert "schema_epoch" in result.output


def test_export_and_import_are_not_mcp_tools() -> None:
    """The threat-model decision (README.md's "Why read-only" section):
    `export`/`import` are CLI-only, operator-invoked commands -- never
    reachable by a served agent. `READ_ONLY_TOOLS` is the complete,
    closed allowlist; this pins that neither name was ever added to it."""
    from atlas_aci.enforcement import READ_ONLY_TOOLS

    assert "export" not in READ_ONLY_TOOLS
    assert "import" not in READ_ONLY_TOOLS
    assert "export_jsonl" not in READ_ONLY_TOOLS
    assert "import_jsonl" not in READ_ONLY_TOOLS


# ---- AC-REL-2's documented bound: warn before `git push` silently rejects ----


def _export_reporting_size(repo: Path, out_path: Path, fake_bytes: int):
    """Invokes the real `export` CLI command but with `export_jsonl`'s
    returned `bytes_written` patched to a synthetic value -- exercises the
    warning branches without actually writing tens/hundreds of MB to disk
    in a test."""
    from unittest import mock

    real_export = CodeGraph.export_jsonl

    def fake_export(self, path):
        stats = real_export(self, path)
        stats["bytes_written"] = fake_bytes
        return stats

    runner = CliRunner()
    with mock.patch.object(CodeGraph, "export_jsonl", fake_export):
        return runner.invoke(cli, ["export", "--repo", str(repo), str(out_path)])


def test_export_warns_approaching_github_soft_limit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "app/hello.rb", _FIXTURE)
    runner = CliRunner()
    assert runner.invoke(cli, ["index", "--repo", str(repo)]).exit_code == 0

    result = _export_reporting_size(repo, tmp_path / "out.jsonl", fake_bytes=60 * 1024 * 1024)
    assert result.exit_code == 0  # advisory only -- the export itself never fails
    assert "export_approaching_github_limit" in result.output
    assert "50 MiB" in result.output


def test_export_warns_at_github_hard_limit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "app/hello.rb", _FIXTURE)
    runner = CliRunner()
    assert runner.invoke(cli, ["index", "--repo", str(repo)]).exit_code == 0

    result = _export_reporting_size(repo, tmp_path / "out.jsonl", fake_bytes=120 * 1024 * 1024)
    assert result.exit_code == 0  # advisory only -- the export itself never fails
    assert "export_exceeds_github_hard_limit" in result.output
    assert "100 MiB" in result.output


def test_export_no_warning_below_soft_limit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "app/hello.rb", _FIXTURE)
    runner = CliRunner()
    assert runner.invoke(cli, ["index", "--repo", str(repo)]).exit_code == 0

    out_path = tmp_path / "out.jsonl"
    result = runner.invoke(cli, ["export", "--repo", str(repo), str(out_path)])
    assert result.exit_code == 0
    assert "export_approaching_github_limit" not in result.output
    assert "export_exceeds_github_hard_limit" not in result.output
