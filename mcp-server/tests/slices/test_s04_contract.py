"""S04 anchors use actual parsers, persisted facts and the operator CLI."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from atlas_aci.__main__ import cli
from atlas_aci.codegraph import CodeGraph


@contextmanager
def fatal_recovered_parser():
    """Inject a fatal parser-provider exception after a real recovered parse.

    Recovered syntax alone now publishes partial observations. These existing
    fatal/sidecar controls must still exercise an actual aborted replacement.
    """
    import tree_sitter_language_pack as pack

    original = pack.get_parser

    def get_parser(language):
        parser = original(language)

        class ThrowingParser:
            def parse(self, source):
                tree = parser.parse(source)
                if tree.root_node.has_error:
                    raise LookupError("controlled fatal parser-provider failure")
                return tree

        return ThrowingParser()

    with patch.object(pack, "get_parser", get_parser):
        yield


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def status(repo: Path) -> dict:
    result = CliRunner().invoke(cli, ["status", "--repo", str(repo)])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def build_repo(tmp_path: Path, langs=None):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "a.py"
    source.write_text("def old():\n    pass\n")
    graph = CodeGraph(repo, langs=langs)
    graph.build()
    return repo, source, graph


def test_unsupported_extension(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.go").write_text("package main\nfunc main() {}")
    (repo / "a.py").write_text("def target():\n    pass\n")
    (repo / "a.js").write_text("function target() {}")
    graph = CodeGraph(repo, langs=["python"])
    graph.build()
    payload = status(repo)
    languages = {x["language"]: x for x in payload["runtime"]["languages"]}
    assert languages["go"]["extraction"]["state"] == "unsupported"
    indexed = {x["language"]: x for x in payload["indexed"]["capabilities"]["languages"]}
    assert languages["javascript"]["extraction"]["state"] == "partial"
    assert indexed["javascript"]["extraction"] == {
        "state": "unsupported",
        "reason": "language_disabled",
    }
    assert payload["completeness"]["counts"] == {
        "indexed": 1,
        "unsupported": 1,
        "disabled": 1,
        "failed": 0,
    }
    graph.close()


def test_producer_specific_capability(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.html").write_text('<div id="anchor"></div>')
    (repo / "a.scss").write_text("@mixin demo { color: red; }\n@include demo;\n")
    graph = CodeGraph(repo)
    graph.build()
    assert graph.db.execute("SELECT relation FROM refs WHERE lang='scss'").fetchone()[0] == "call"
    assert graph.db.execute("SELECT count(*) FROM symbols WHERE lang='html'").fetchone()[0] == 1
    payload = status(repo)
    languages = {x["language"]: x for x in payload["indexed"]["capabilities"]["languages"]}
    assert languages["html"]["definitions"] == ["id"]
    assert languages["html"]["relations"] == []
    assert languages["scss"]["relations"] == ["call"]
    assert all(
        p["semantic_binding"]["state"] == "unsupported" for p in payload["runtime"]["producers"]
    )
    assert all(x["semantic_binding"]["state"] == "unsupported" for x in languages.values())
    graph.close()


@pytest.mark.parametrize(
    "lang,extension,malformed",
    [
        ("python", ".py", "def broken(:\n    pass\n"),
        ("javascript", ".js", "function broken( {\n"),
        ("ruby", ".rb", "class Broken\n def broken(\n"),
    ],
)
def test_parse_failure_visible(tmp_path: Path, lang: str, extension: str, malformed: str):
    from tree_sitter_language_pack import get_parser

    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / ("broken" + extension)
    source.write_text(malformed)
    assert get_parser(lang).parse(source.read_bytes()).root_node.has_error
    graph = CodeGraph(repo)
    stats = graph.build()
    assert stats["extraction_state"] == "partial" and stats["files_failed"] == 1
    payload = status(repo)
    assert payload["generation_id"] is not None
    assert payload["completeness"]["state"] == "partial"
    assert payload["completeness"]["enumeration"] == "complete"
    assert payload["attempt"]["applicability"] == "none"
    failed = payload["completeness"]["files"][-1]
    assert failed["path"] == source.name
    assert failed["reason"] == "parse_error"
    assert failed["failed_source_sha256"] == digest(source.read_bytes())
    assert failed["location"]["line"] >= 1
    assert graph.search_symbol("broken")["definitions"] == []
    graph.close()


def test_retained_stale_fact(tmp_path: Path):
    repo, source, graph = build_repo(tmp_path)
    later = repo / "z.py"
    later.write_text("def untouched():\n    pass\n")
    graph.build()
    old = source.read_bytes()
    generation = graph.generation_id
    before = graph.db_path.read_bytes()
    source.write_text("def broken(:\n    pass\n")
    # Preserve the fatal/ABA obligation with a real provider exception;
    # ordinary recovered trees below publish a separate partial generation.
    with fatal_recovered_parser(), pytest.raises(RuntimeError):
        graph.build()
    saved_attempt = (repo / ".atlas/index-attempt.v1.json").read_bytes()
    assert graph.db_path.read_bytes() == before and graph.generation_id == generation
    for _ in range(2):
        graph.build(since="HEAD")
    assert graph.db_path.read_bytes() != before and graph.generation_id != generation
    assert not graph.search_symbol("old")["definitions"]
    assert graph.search_symbol("old")["historical_definitions"]
    failed_status = status(repo)
    assert failed_status["completeness"]["state"] == "partial"
    files = {x["path"]: x for x in failed_status["completeness"]["files"]}
    assert files["a.py"]["stale"] is True
    assert files["a.py"]["fact_source_sha256"] == digest(old)
    assert files["a.py"]["failed_source_sha256"] == digest(source.read_bytes())
    assert files["z.py"]["refresh_verification"] == "verified_unchanged"
    source.write_bytes(old)
    graph.build(since="HEAD")
    assert graph.generation_id == generation
    # Reappearance of an old atomic sidecar must not revive its overlay (ABA).
    (repo / ".atlas/index-attempt.v1.json").write_bytes(saved_attempt)
    recovered = status(repo)
    assert recovered["attempt"]["applicability"] == "mismatch"
    assert recovered["completeness"]["state"] == "complete"
    assert all(x["stale"] is False for x in recovered["completeness"]["files"])
    graph.close()


def test_status_replay(tmp_path: Path):
    repo, source, graph = build_repo(tmp_path)
    first = CliRunner().invoke(cli, ["status", "--repo", str(repo)])
    second = CliRunner().invoke(cli, ["status", "--repo", str(repo)])
    assert first.exit_code == second.exit_code == 0
    assert first.stdout == second.stdout
    parsed = json.loads(first.stdout)
    token = graph.db.execute(
        "SELECT value FROM manifest WHERE key='diagnostic_publication_token'"
    ).fetchone()[0]
    assert token not in first.stdout
    source.write_text("def broken(:\n    pass\n")
    graph.build()
    failed = status(repo)
    assert failed["generation_id"] != parsed["generation_id"]
    assert failed["status_id"] != parsed["status_id"]
    assert failed["completeness"]["state"] == "partial"
    graph.build()
    assert status(repo) == failed
    graph.close()


def test_read_only_missing_and_legacy(tmp_path: Path):
    empty = tmp_path / "empty"
    empty.mkdir()
    before = list(empty.iterdir())
    assert status(empty)["completeness"]["state"] == "unavailable"
    assert list(empty.iterdir()) == before
    repo, source, graph = build_repo(tmp_path)
    graph.db.execute(
        "DELETE FROM manifest WHERE key IN ('coverage_snapshot','diagnostic_publication_token')"
    )
    graph.db.commit()
    legacy = status(repo)
    assert legacy["indexed"]["capabilities"] is None
    assert legacy["completeness"]["state"] == "unknown"
    assert legacy["completeness"]["counts"] is None
    source.write_text("def broken(:\n    pass\n")
    with fatal_recovered_parser(), pytest.raises(RuntimeError):
        graph.build()
    failed = status(repo)
    assert failed["attempt"]["applicability"] == "uncorrelated_missing_token"
    assert failed["completeness"]["files"][0]["stale"] is None
    assert failed["completeness"]["files"][0]["fact_source_sha256"]
    before = {
        str(p.relative_to(repo)): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in repo.rglob("*")
        if p.is_file()
    }
    assert status(repo) == failed
    after = {
        str(p.relative_to(repo)): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in repo.rglob("*")
        if p.is_file()
    }
    assert before == after
    graph.close()


def test_source_free_import_unknown(tmp_path: Path):
    _repo, _, graph = build_repo(tmp_path)
    portable = tmp_path / "graph.jsonl"
    graph.export_jsonl(portable)
    target = tmp_path / "target"
    target.mkdir()
    imported = CodeGraph(target)
    imported.import_jsonl(portable)
    payload = status(target)
    assert payload["generation_id"]
    assert payload["completeness"]["state"] == "unknown"
    assert payload["completeness"]["counts"] is None
    assert payload["indexed"]["capabilities"] is None
    assert list(target.glob("*.py")) == []
    assert imported.search_symbol("old")["definitions"]
    graph.close()
    imported.close()


@pytest.mark.parametrize("mutation", ["json", "producer", "id", "foreign", "token"])
def test_bad_attempt_never_claims_fresh(tmp_path: Path, mutation: str):
    repo, source, graph = build_repo(tmp_path)
    source.write_text("def broken(:\n    pass\n")
    with fatal_recovered_parser(), pytest.raises(RuntimeError):
        graph.build()
    path = repo / ".atlas/index-attempt.v1.json"
    data = json.loads(path.read_text())
    if mutation == "json":
        path.write_text("{")
    else:
        if mutation == "producer":
            data["observation"]["producer"] = {}
            data["attempt_id"] = graph._status_hash(data["observation"])
        if mutation == "id":
            data["attempt_id"] = "0" * 64
        if mutation == "foreign":
            data["format"] = "foreign.v1"
        if mutation == "token":
            data["base_publication_token"] = "0" * 64
        path.write_text(json.dumps(data))
    result = status(repo)
    assert result["attempt"]["applicability"] == (
        "mismatch" if mutation == "token" else "unavailable"
    )
    if mutation != "token":
        assert result["completeness"]["state"] == "unknown"
        assert result["completeness"]["files"][0]["stale"] is None
    graph.close()


@pytest.mark.parametrize("failure", ["parser", "read", "diagnostic_write"])
def test_failure_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str):
    import tree_sitter_language_pack

    repo, source, graph = build_repo(tmp_path)
    old = graph.db_path.read_bytes()
    source.write_text("def replacement():\n    pass\n")
    if failure == "parser":

        def throwing(_language):
            raise LookupError("unstable external details")

        monkeypatch.setattr(tree_sitter_language_pack, "get_parser", throwing)
    elif failure == "read":
        original = Path.read_bytes

        def unreadable(path):
            if path == source:
                raise PermissionError("external path and message")
            return original(path)

        monkeypatch.setattr(Path, "read_bytes", unreadable)
    else:
        source.write_text("def broken(:\n    pass\n")

        def cannot_write(_base):
            raise OSError("no space")

        monkeypatch.setattr(graph, "_write_attempt", cannot_write)
    with fatal_recovered_parser(), pytest.raises((RuntimeError, PermissionError)) as error:
        graph.build(since="HEAD")
    assert "no space" not in str(error.value)
    assert graph.db_path.read_bytes() == old
    payload = status(repo)
    if failure != "diagnostic_write":
        entry = payload["attempt"]["observation"]["files"][0]
        assert entry["reason"] == ("read_error" if failure == "read" else "parser_exception")
        assert entry["source_sha256"] is None if failure == "read" else entry["source_sha256"]
    graph.close()


def test_status_atomic_lease_race(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import concurrent.futures
    import threading

    repo, source, graph = build_repo(tmp_path)
    generation = graph.generation_id
    before_attempt = threading.Event()
    published = threading.Event()
    original = CodeGraph._read_attempt

    def paused(reader):
        before_attempt.set()
        assert published.wait(5)
        return original(reader)

    monkeypatch.setattr(CodeGraph, "_read_attempt", paused)

    def threaded_status():
        reader = CodeGraph(repo, read_only=True)
        try:
            return reader.status()
        finally:
            reader.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(threaded_status)
        assert before_attempt.wait(5)
        source.write_text("def replacement():\n    pass\n")
        graph.build()
        source.write_text("def broken(:\n    pass\n")
        with fatal_recovered_parser(), pytest.raises(RuntimeError):
            graph.build()
        published.set()
        payload = result.result(5)
    assert payload["generation_id"] == generation
    assert payload["attempt"]["applicability"] == "mismatch"
    assert payload["completeness"]["state"] == "complete"
    graph.close()


def test_diagnostic_token_excluded_but_coverage_consumed(tmp_path: Path):
    import importlib.util

    runner_path = Path(__file__).resolve().parents[3] / "atlas/evals/stdio_runner.py"
    spec = importlib.util.spec_from_file_location("s04_runner", runner_path)
    assert spec and spec.loader
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    repo, _source, graph = build_repo(tmp_path)
    first = runner.index_identity(graph.db_path)
    first_status = status(repo)
    graph.build()
    assert status(repo) == first_status
    assert runner.index_identity(graph.db_path) == first
    # Unsupported source changes measured coverage without changing graph facts.
    (repo / "observed.rs").write_text("fn main() {}\n")
    graph.build()
    changed = status(repo)
    assert changed["generation_id"] != first_status["generation_id"]
    assert changed["indexed"]["coverage_id"] != first_status["indexed"]["coverage_id"]
    assert runner.index_identity(graph.db_path) != first
    graph.close()


def test_unprocessed_and_empty_files_are_not_verified_facts(tmp_path: Path):
    repo, source, graph = build_repo(tmp_path)
    source.write_text("")
    (repo / "z.py").write_text("def later():\n    pass\n")
    graph.build()
    source.write_text("def broken(:\n    pass\n")
    with fatal_recovered_parser(), pytest.raises(RuntimeError):
        graph.build()
    files = {f["path"]: f for f in status(repo)["completeness"]["files"]}
    assert files["a.py"]["stale"] is False  # no retained facts for the empty file
    assert files["z.py"]["stale"] is None  # unprocessed, not known fresh
    assert files["z.py"]["refresh_verification"] == "not_processed"
    graph.close()


@pytest.mark.parametrize("mutation", ["invalid_json", "producer", "capabilities", "missing_token"])
def test_invalid_coverage_is_unknown(tmp_path: Path, mutation: str):
    repo, _source, graph = build_repo(tmp_path)
    row = graph.db.execute("SELECT value FROM manifest WHERE key='coverage_snapshot'").fetchone()[0]
    coverage = json.loads(row)
    if mutation == "producer":
        coverage["producer"] = {}
    if mutation == "capabilities":
        coverage["capabilities"]["languages"] = []
    if mutation == "missing_token":
        graph.db.execute("DELETE FROM manifest WHERE key='diagnostic_publication_token'")
    else:
        graph.db.execute(
            "UPDATE manifest SET value=? WHERE key='coverage_snapshot'",
            ("{" if mutation == "invalid_json" else json.dumps(coverage),),
        )
    graph.db.commit()
    result = status(repo)
    assert result["generation_id"] == graph.generation_id
    assert result["completeness"]["state"] == "unknown"
    assert result["indexed"]["capabilities"] is None
    graph.close()


def test_indexed_producer_not_replaced_with_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo, _source, graph = build_repo(tmp_path)
    prior = graph._producer_inputs()
    prior["codegraph_sha256"] = "0" * 64
    monkeypatch.setattr(graph, "_producer_inputs", lambda: prior)
    graph.build()
    result = status(repo)
    assert result["indexed"]["producer"] == prior
    assert result["indexed"]["capabilities"]["producer"] == prior
    assert result["runtime"]["producer"] != prior
    assert result["completeness"]["state"] == "complete"
    graph.close()


def test_aborted_changed_prefix_not_claimed_published(tmp_path: Path):
    repo, source, graph = build_repo(tmp_path)
    (repo / "z.py").write_text("def later():\n    pass\n")
    graph.build()
    original = source.read_bytes()
    source.write_text("def replacement():\n    pass\n")
    (repo / "z.py").write_text("def broken(:\n    pass\n")
    with fatal_recovered_parser(), pytest.raises(RuntimeError):
        graph.build()
    files = {f["path"]: f for f in status(repo)["completeness"]["files"]}
    assert files["a.py"]["stale"] is True
    assert files["a.py"]["fact_source_sha256"] == digest(original)
    assert files["a.py"]["observed_source_sha256"] == digest(source.read_bytes())
    assert files["a.py"]["failed_source_sha256"] is None
    assert files["a.py"]["refresh_verification"] == "observed_not_published"
    assert graph.search_symbol("old")["definitions"]
    assert not graph.search_symbol("replacement")["definitions"]
    graph.close()


def test_real_query_failure_is_unverified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from tree_sitter import QueryError

    from atlas_aci.codegraph import QUERIES

    repo, _source, graph = build_repo(tmp_path)
    generation = graph.generation_id
    before = graph.db_path.read_bytes()
    with monkeypatch.context() as changed:
        changed.setitem(QUERIES, "python", "(nonexistent_node_type) @def.function")
        with pytest.raises(QueryError):
            graph.build()
    result = status(repo)
    assert result["generation_id"] == generation
    assert graph.db_path.read_bytes() == before
    assert result["attempt"]["observed_failed_count"] == 1
    assert result["attempt"]["observation"]["files"][0]["reason"] == "extraction_error"
    file = result["completeness"]["files"][0]
    assert file["refresh_verification"] == "incompatible_provenance"
    assert file["stale"] is None
    graph.close()


def test_persisted_capability_must_match_enabled_configuration(tmp_path: Path):
    repo, _source, graph = build_repo(tmp_path, langs=["python"])
    raw = graph.db.execute("SELECT value FROM manifest WHERE key='coverage_snapshot'").fetchone()[0]
    coverage = json.loads(raw)
    javascript = next(
        item for item in coverage["capabilities"]["languages"] if item["language"] == "javascript"
    )
    javascript["extraction"] = {"state": "partial", "reason": "bounded_structural_queries"}
    graph.db.execute(
        "UPDATE manifest SET value=? WHERE key='coverage_snapshot'", (json.dumps(coverage),)
    )
    graph.db.commit()
    result = status(repo)
    assert result["completeness"]["state"] == "unknown"
    assert result["indexed"]["capabilities"] is None
    graph.close()
