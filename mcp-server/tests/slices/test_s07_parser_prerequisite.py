"""First supporting proof: real extraction, one-predecessor closure, portability."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from atlas_aci.codegraph import CodeGraph
from atlas_aci.storage import evidence as ev


def make(tmp_path):
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg/__init__.py").write_text("")
    (repo / "pkg/a.py").write_text("from .b import B\ndef A():\n B()\n")
    (repo / "pkg/b.py").write_text("def B():\n pass\n")
    graph = CodeGraph(repo)
    graph.build()
    assert graph.query("callers_of:B")["edges"]
    return repo, graph


def test_first_proof_historical_target_closure_and_fresh_isolation(tmp_path: Path):
    repo, graph = make(tmp_path)
    original = graph.evidence_records()
    old_b = (repo / "pkg/b.py").read_bytes()
    old_call = next(r for r in original if r["type"] == "assertion" and r["relation"] == "call")
    (repo / "pkg/a.py").write_text("def A(:\n B()\n")
    (repo / "pkg/b.py").write_bytes(b"\n\n" + old_b)
    (repo / "pkg/c.py").write_text("from .a import A\ndef C():\n A()\n")
    stats = graph.build()
    assert stats["extraction_state"] == "partial" and stats["files_failed"] == 1
    observations = graph.observation_records()
    failed = next(r for r in observations if r["type"] == "observation" and r["path"] == "pkg/a.py")
    assert failed["state"] == "failed" and failed["reason"] == "parse_error"
    assert (
        failed["observed_source_sha256"]
        == hashlib.sha256((repo / "pkg/a.py").read_bytes()).hexdigest()
    )
    history = next(
        r for r in observations if r["type"] == "history" and r["id"] == failed["historical_id"]
    )
    assert old_call in history["records"]
    old_target = next(
        r
        for r in history["records"]
        if r["type"] == "projection" and r["table"] == "symbols" and r["row"]["name"] == "B"
    )
    old_occurrence = next(
        r
        for r in history["records"]
        if r["type"] == "occurrence" and r["id"] == old_target["links"]["occurrence_id"]
    )
    assert (
        old_occurrence["line"] == 1
        and old_occurrence["source_sha256"] == hashlib.sha256(old_b).hexdigest()
    )
    assert any(
        r["type"] == "evidence" and r["occurrence_id"] == old_occurrence["id"]
        for r in history["records"]
    )
    assert graph.search_symbol("B")["definitions"][0]["line_start"] == 3
    assert not graph.query("callers_of:A")["edges"]
    assert graph.search_symbol("A")["references"][0]["unresolved_reason"] == "stale_target"
    assert not graph.confident_edges()
    assert graph.communities()["communities"] == []
    assert graph.god_nodes()["god_nodes"] == []
    artifact = tmp_path / "partial.jsonl"
    graph.export_jsonl(artifact)
    destination = tmp_path / "no-source"
    destination.mkdir()
    imported = CodeGraph(destination)
    imported.import_jsonl(artifact)
    assert imported.observation_records() == observations
    replay = tmp_path / "replay.jsonl"
    imported.export_jsonl(replay)
    assert replay.read_bytes() == artifact.read_bytes()
    imported.import_jsonl(replay)
    assert imported.observation_records() == observations and not imported.confident_edges()
    assert not list(destination.glob("*.py"))
    imported.close()
    graph.close()


def test_first_failure_has_no_invented_history_and_recovery(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    bad = repo / "bad.py"
    bad.write_text("def bad(:\n pass\n")
    (repo / "good.py").write_text("def good():\n pass\n")
    graph = CodeGraph(repo)
    graph.build()
    first = graph.observation_records()
    failed = next(r for r in first if r["type"] == "observation" and r["path"] == "bad.py")
    assert failed["historical_id"] is None and failed["retained_record_ids"] == []
    assert not any(
        r["type"] == "occurrence" and r["path"] == "bad.py" for r in graph.evidence_records()
    )
    graph.build()
    assert graph.observation_records() == first
    bad.write_text("def bad():\n pass\n")
    graph.build()
    assert not any(r["type"] == "history" for r in graph.observation_records())
    assert all(r["state"] == "indexed" for r in graph.observation_records())
    assert graph.search_symbol("bad")["definitions"]
    graph.close()


def test_contradictory_portable_stale_observation_rejected(tmp_path: Path):
    repo, graph = make(tmp_path)
    (repo / "pkg/a.py").write_text("def broken(:\n pass\n")
    graph.build()
    artifact = tmp_path / "bad.jsonl"
    graph.export_jsonl(artifact)
    rows = [json.loads(line) for line in artifact.read_text().splitlines()]
    header = rows.pop(0)
    observed = next(r for r in rows if r["type"] == "observation" and r["historical_id"])
    observed["state"] = "indexed"
    observed["reason"] = None
    observed["location"] = None
    observed["id"] = ev.identifier(observed)
    rows.sort(key=lambda r: (r["type"], r["id"]))
    body = "".join(ev.canonical(r) + "\n" for r in rows)
    header["content_hash"] = hashlib.sha256(body.encode()).hexdigest()
    artifact.write_text(ev.canonical(header) + "\n" + body)
    generation = graph.generation_id
    before = graph.db_path.read_bytes()
    with pytest.raises(ValueError):
        graph.import_jsonl(artifact)
    assert graph.generation_id == generation and graph.db_path.read_bytes() == before
    assert not list(graph.atlas_dir.glob("*.tmp.*"))
    graph.close()


@pytest.mark.parametrize(
    "mutation", ["indexed_hash", "failed_active_facts", "missing_indexed_observation"]
)
def test_current_observations_must_match_active_source_evidence(tmp_path: Path, mutation: str):
    repo, graph = make(tmp_path)
    (repo / "pkg/a.py").write_text("def A(:\n B()\n")
    (repo / "pkg/b.py").write_text("\n\ndef B():\n pass\n")
    graph.build()
    artifact = tmp_path / "contradiction.jsonl"
    graph.export_jsonl(artifact)
    rows = [json.loads(line) for line in artifact.read_text().splitlines()]
    header = rows.pop(0)
    observation = next(r for r in rows if r["type"] == "observation" and r["path"] == "pkg/b.py")
    assert observation["state"] == "indexed"
    assert any(
        r["type"] == "evidence" and r["source_sha256"] == observation["observed_source_sha256"]
        for r in rows
    )
    if mutation == "indexed_hash":
        observation["observed_source_sha256"] = "0" * 64
    elif mutation == "failed_active_facts":
        observation.update(
            state="failed",
            reason="parse_error",
            location={"line": 1, "column": 0, "end_line": 1, "end_column": 1},
        )
    else:
        rows.remove(observation)
    observation["id"] = ev.identifier(observation)
    rows.sort(key=lambda r: (r["type"], r["id"]))
    body = "".join(ev.canonical(r) + "\n" for r in rows)
    header.update(content_hash=hashlib.sha256(body.encode()).hexdigest(), record_count=len(rows))
    artifact.write_text(ev.canonical(header) + "\n" + body)
    before = graph.db_path.read_bytes()
    generation = graph.generation_id
    with pytest.raises(ValueError):
        graph.import_jsonl(artifact)
    assert graph.db_path.read_bytes() == before and graph.generation_id == generation
    assert not list(graph.atlas_dir.glob("*.tmp.*"))
    graph.close()


def test_explicit_v1_adapter_keeps_observation_verification_unknown(tmp_path: Path):
    _repo, graph = make(tmp_path)
    artifact = tmp_path / "v1.jsonl"
    graph.export_jsonl(artifact)
    rows = [json.loads(line) for line in artifact.read_text().splitlines()]
    header = rows.pop(0)
    rows = [r for r in rows if r["type"] not in {"observation", "history"}]
    body = "".join(ev.canonical(r) + "\n" for r in rows)
    header.update(
        format=ev.FORMAT,
        content_hash=hashlib.sha256(body.encode()).hexdigest(),
        record_count=len(rows),
    )
    artifact.write_text(ev.canonical(header) + "\n" + body)
    destination = tmp_path / "v1-source-free"
    destination.mkdir()
    imported = CodeGraph(destination)
    imported.import_jsonl(artifact)
    assert imported.observation_records() == []
    assert imported.query("callers_of:B")["edges"]
    assert imported._generation_data(imported.db)[0]["source_provenance"] == "unavailable"
    imported.close()
    graph.close()


def test_observation_coverage_history_recovery_and_cache(tmp_path: Path):
    repo, graph = make(tmp_path)
    (repo / "unknown.go").write_text("package main\nfunc main() {}\n")
    original = (repo / "pkg/a.py").read_bytes()
    (repo / "pkg/a.py").write_text("def bad(:\n pass\n")
    graph.build()
    payload = graph.status()
    assert payload["completeness"]["state"] == "partial"
    assert payload["completeness"]["enumeration"] == "complete"
    assert payload["completeness"]["counts"]["failed"] == 1
    assert payload["completeness"]["counts"]["unsupported"] == 1
    assert graph.search_symbol("A")["historical_definitions"][0]["freshness"]["state"] == "stale"
    historical = graph.query("callers_of:B")["historical_edges"]
    assert (
        historical
        and historical[0]["freshness"]["fact_source_sha256"] == hashlib.sha256(original).hexdigest()
    )
    first = graph.generation_id
    graph.build(since="HEAD")
    assert graph.generation_id == first
    assert graph.status() == payload
    (repo / "pkg/a.py").write_bytes(original)
    graph.build(since="HEAD")
    assert not any(r["type"] == "history" for r in graph.observation_records())
    assert graph.query("callers_of:B")["edges"] and not graph.query("callers_of:B").get(
        "historical_edges", []
    )
    assert graph.status()["completeness"]["counts"]["failed"] == 0
    generation = graph.generation_id
    graph.build()
    assert graph.generation_id == generation
    graph.close()


def test_deleted_target_and_disabled_source_do_not_rebind_history(tmp_path: Path):
    repo, graph = make(tmp_path)
    (repo / "pkg/a.py").write_text("def bad(:\n pass\n")
    (repo / "pkg/b.py").unlink()
    graph.build()
    assert graph.query("callers_of:B")["historical_edges"][0]["target"]["line"] == 1
    assert not graph.confident_edges()
    graph.langs = {"ruby"}
    graph.build()
    assert not any(r["type"] == "history" for r in graph.observation_records())
    assert all(r["state"] == "disabled" for r in graph.observation_records())
    assert not graph.search_symbol("A")["definitions"] and not graph.search_symbol("A").get(
        "historical_definitions", []
    )
    graph.close()


def test_unresolved_predecessor_references_retain_closed_history(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "a.py"
    source.write_text("def A():\n missing()\n")
    graph = CodeGraph(repo)
    graph.build()
    old = next(
        r
        for r in graph.evidence_records()
        if r["type"] == "assertion" and r["resolution"] == "UNRESOLVED"
    )
    source.write_text("def A(:\n missing()\n")
    graph.build()
    history = next(r for r in graph.observation_records() if r["type"] == "history")
    assert old in history["records"]
    assert not graph.confident_edges()
    artifact = tmp_path / "history.jsonl"
    graph.export_jsonl(artifact)
    graph.import_jsonl(artifact)
    assert old in next(r for r in graph.observation_records() if r["type"] == "history")["records"]
    graph.close()


def test_pinned_dialect_files_remain_failed_whole_file_observations(tmp_path: Path):
    fixtures = Path(__file__).resolve().parents[1] / "fixtures/s07/parser_prerequisite"
    provenance = json.loads((fixtures / "provenance.json").read_text())
    repo = tmp_path / "corpus"
    repo.mkdir()
    for index, item in enumerate(provenance):
        raw = (fixtures / item["fixture"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == item["source_sha256"]
        suffix = {"yaml": ".yml", "ruby": ".rb", "scss": ".scss"}[item["language"]]
        (repo / (str(index) + suffix)).write_bytes(raw)
    (repo / "clean.py").write_text("def clean():\n pass\n")
    graph = CodeGraph(repo)
    stats = graph.build()
    assert stats["files_failed"] == len(provenance) and stats["extraction_state"] == "partial"
    observations = [r for r in graph.observation_records() if r["type"] == "observation"]
    assert len(observations) == len(provenance) + 1
    assert sum(r["state"] == "failed" and r["location"] is not None for r in observations) == len(
        provenance
    )
    assert {r["path"] for r in graph.evidence_records() if r["type"] == "occurrence"} == {
        "clean.py"
    }
    assert graph.search_symbol("clean")["definitions"]
    graph.close()


@pytest.mark.parametrize(
    "tool,arguments,fields",
    [
        ("search_symbol", {"name": "A"}, ["historical_definitions", "historical_references"]),
        (
            "graph_query",
            {"query": "definitions_of:A"},
            ["historical_definitions", "historical_references"],
        ),
        ("graph_query", {"query": "callers_of:A"}, ["historical_edges"]),
        ("graph_query", {"query": "rationale:"}, ["historical_rationale"]),
    ],
)
async def test_historical_results_obey_production_central_bounds(
    tmp_path: Path, tool: str, arguments: dict, fields: list[str]
):
    from atlas_aci.config import Config
    from atlas_aci.server import build_server_state, dispatch_tool_call

    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "a.py"
    source.write_text("def A():\n # NOTE: old\n A()\n" * 4)
    writer = CodeGraph(repo)
    writer.build()
    source.write_text("def bad(:\n pass\n")
    writer.build()
    config = Config(repo=repo, memex_root=tmp_path / "memex", max_bound_field_elements=2)
    enforcement, memex, reader = build_server_state(config)
    result = await dispatch_tool_call(tool, arguments, config, enforcement, memex, reader)
    for field in fields:
        assert len(result[field]) == 2
        assert field in result["truncated_fields"]
        assert result["returned_count"][field] == 2
    assert result["truncated"] is True and result["more_available"] is True
    if "historical_edges" in fields:
        assert all(len(edge["candidates"]) == 2 for edge in result["historical_edges"])
        assert "historical_edges.candidates" in result["truncated_fields"]
    reader.close()
    writer.close()


async def test_historical_subclasses_and_nested_candidates_are_bounded(tmp_path: Path):
    from atlas_aci.config import Config
    from atlas_aci.server import build_server_state, dispatch_tool_call

    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "a.py"
    source.write_text(
        "class Base:\n pass\n" * 4 + "".join(f"class Child{i}(Base):\n pass\n" for i in range(4))
    )
    writer = CodeGraph(repo)
    writer.build()
    source.write_text("class bad(:\n pass\n")
    writer.build()
    real = writer.query("subclasses_of:Base")["historical_edges"]
    assert len(real) == 4 and all(
        edge["confidence"] == "AMBIGUOUS" and len(edge["candidates"]) == 4 for edge in real
    )
    config = Config(repo=repo, memex_root=tmp_path / "memex", max_bound_field_elements=2)
    enforcement, memex, reader = build_server_state(config)
    response = await dispatch_tool_call(
        "graph_query", {"query": "subclasses_of:Base"}, config, enforcement, memex, reader
    )
    assert len(response["historical_edges"]) == 2
    assert all(len(edge["candidates"]) == 2 for edge in response["historical_edges"])
    assert response["truncated_fields"] == ["historical_edges", "historical_edges.candidates"]
    assert response["returned_count"]["historical_edges"] == 2
    assert response["returned_count"]["historical_edges.candidates"] == 4
    reader.close()
    writer.close()


def test_failed_retry_read_once_and_changed_producer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import tree_sitter_language_pack as pack

    from atlas_aci.codegraph import QUERIES

    repo, graph = make(tmp_path)
    bad = repo / "pkg/a.py"
    bad.write_text("def bad(:\n pass\n")
    graph.build()
    observed = graph.observation_records()
    old_producers = next(
        r for r in observed if r["type"] == "observation" and r["path"] == "pkg/a.py"
    )["retained_producer_ids"]
    parse = pack.get_parser
    read = Path.read_bytes
    counts = {"bad": 0, "reads": {}}

    bad_bytes = bad.read_bytes()

    def get_parser(language):
        original = parse(language)

        class CountingParser:
            def parse(self, buffer):
                if buffer == bad_bytes:
                    counts["bad"] += 1
                return original.parse(buffer)

        return CountingParser()

    def read_once(path):
        if path.is_relative_to(repo):
            counts["reads"][str(path)] = counts["reads"].get(str(path), 0) + 1
        return read(path)

    with monkeypatch.context() as patch:
        patch.setattr(pack, "get_parser", get_parser)
        patch.setattr(Path, "read_bytes", read_once)
        graph.build(since="HEAD")
    assert counts["bad"] == 1 and set(counts["reads"].values()) == {1}
    assert graph.observation_records() == observed
    before = graph.generation_id
    with monkeypatch.context() as patch:
        patch.setitem(QUERIES, "python", QUERIES["python"] + "\n")
        stats = graph.build(since="HEAD")
    assert stats["files_skipped"] == 0 and graph.generation_id != before
    assert (
        next(
            r
            for r in graph.observation_records()
            if r["type"] == "observation" and r["path"] == "pkg/a.py"
        )["retained_producer_ids"]
        == old_producers
    )
    graph.close()


def test_partial_publication_and_fatal_failure_pin_real_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import os

    repo, graph = make(tmp_path)
    before = graph.db_path.read_bytes()
    with graph.reader_lease() as held:
        original = held.generation_id
        (repo / "pkg/a.py").write_text("def bad(:\n pass\n")
        graph.build()
        assert held.generation_id == original and held.query("callers_of:B")["edges"]
        assert graph.generation_id != original and not graph.confident_edges()
    partial = graph.db_path.read_bytes()
    generation = graph.generation_id
    replace = os.replace

    def cannot_publish(source, destination):
        if Path(destination) == graph.db_path:
            raise OSError("controlled final publication failure")
        return replace(source, destination)

    (repo / "pkg/b.py").write_text("def B():\n return 42\n")
    with graph.reader_lease() as held, monkeypatch.context() as patch:
        patch.setattr(os, "replace", cannot_publish)
        with pytest.raises(OSError, match="controlled final publication"):
            graph.build()
        assert held.generation_id == generation
        assert held.query("callers_of:B")["historical_edges"]
    assert (
        graph.db_path.read_bytes() == partial
        and partial != before
        and graph.generation_id == generation
    )
    assert not list(graph.atlas_dir.glob("*.tmp.*"))
    assert graph.status()["completeness"]["state"] == "failed"
    graph.close()


def test_empty_observed_generation_keeps_v2_portable_contract(tmp_path: Path):
    repo = tmp_path / "empty"
    repo.mkdir()
    graph = CodeGraph(repo)
    graph.build()
    artifact = tmp_path / "empty.jsonl"
    graph.export_jsonl(artifact)
    assert json.loads(artifact.read_text().splitlines()[0])["format"] == ev.OBSERVATION_FORMAT
    destination = tmp_path / "destination"
    destination.mkdir()
    imported = CodeGraph(destination)
    imported.import_jsonl(artifact)
    replay = tmp_path / "replay.jsonl"
    imported.export_jsonl(replay)
    assert replay.read_bytes() == artifact.read_bytes()
    assert imported.status()["completeness"]["state"] == "unknown"
    imported.close()
    graph.close()


def rewrite_history_artifact(artifact: Path, mutate):
    lines = artifact.read_text().splitlines()
    header = json.loads(lines[0])
    records = [json.loads(line) for line in lines[1:]]
    history = next(r for r in records if r["type"] == "history")
    original = history["id"]
    mutate(history["records"])
    replacements = {}

    def relink(value):
        if isinstance(value, str):
            return replacements.get(value, value)
        if isinstance(value, list):
            return [relink(v) for v in value]
        if isinstance(value, dict):
            return {k: relink(v) for k, v in value.items()}
        return value

    for _ in range(12):
        changed = False
        for index, record in enumerate(history["records"]):
            old = record["id"]
            updated = relink(record)
            updated["id"] = ev.identifier(updated)
            if old != updated["id"]:
                replacements[old] = updated["id"]
                changed = True
            history["records"][index] = updated
        if not changed:
            break
    history["records"].sort(key=lambda r: (r["type"], r["id"]))
    history["id"] = ev.identifier(history)
    for index, item in enumerate(records):
        if item["type"] == "observation" and item["historical_id"] == original:
            records[index] = ev.observation(
                item["path"],
                item["language"],
                item["observed_source_sha256"],
                item["state"],
                item["reason"],
                item["location"],
                history,
            )
    records.sort(key=lambda r: (r["type"], r["id"]))
    body = "".join(ev.canonical(r) + "\n" for r in records)
    header.update(content_hash=hashlib.sha256(body.encode()).hexdigest(), record_count=len(records))
    artifact.write_text(ev.canonical(header) + "\n" + body)


@pytest.mark.parametrize(
    "mutation", ["missing_target_occurrence", "missing_producer", "symlink_target"]
)
def test_portable_history_is_closed_and_contained(tmp_path: Path, mutation: str):
    repo, graph = make(tmp_path)
    (repo / "pkg/a.py").write_text("def A(:\n B()\n")
    graph.build()
    artifact = tmp_path / "forged.jsonl"
    graph.export_jsonl(artifact)
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / "escape").symlink_to(outside, target_is_directory=True)

    def mutate(records):
        if mutation == "missing_target_occurrence":
            target = next(
                r
                for r in records
                if r["type"] == "projection" and r["table"] == "symbols" and r["row"]["name"] == "B"
            )
            records.remove(next(r for r in records if r["id"] == target["links"]["occurrence_id"]))
        elif mutation == "missing_producer":
            records.remove(next(r for r in records if r["type"] == "producer"))
        else:
            for item in records:
                if item.get("path") == "pkg/b.py":
                    item["path"] = "escape/b.py"
                    if item["type"] == "entity" and item["kind"] == "file":
                        item["name"] = "escape/b.py"
                if item["type"] == "projection":
                    for key, value in list(item["row"].items()):
                        if "path" in key and value == "pkg/b.py":
                            item["row"][key] = "escape/b.py"

    rewrite_history_artifact(artifact, mutate)
    before = graph.db_path.read_bytes()
    generation = graph.generation_id
    with pytest.raises(ValueError):
        graph.import_jsonl(artifact)
    assert graph.db_path.read_bytes() == before and graph.generation_id == generation
    assert not list(graph.atlas_dir.glob("*.tmp.*"))
    graph.close()


def test_edge_projection_uses_actual_captured_owner_not_line_containment(tmp_path: Path):
    from tree_sitter_language_pack import get_parser

    repo = tmp_path / "repo"
    repo.mkdir()
    source = b"@mixin target {}\n@mixin caller {\n $x: 0; @include target;\n}\n"
    assert not get_parser("scss").parse(source).root_node.has_error
    (repo / "a.scss").write_bytes(source)
    graph = CodeGraph(repo)
    graph.build()
    edge = graph.query("callers_of:target")["edges"][0]
    assert edge["source"] == {"path": "a.scss", "line": 3, "name": "caller", "kind": "mixin"}
    records = graph.evidence_records()
    assertion = next(r for r in records if r["type"] == "assertion" and r["relation"] == "call")
    owner = next(
        r for r in records if r["type"] == "entity" and r["id"] == assertion["source_entity_id"]
    )
    assert (owner["name"], owner["kind"]) == ("caller", "mixin")
    graph.close()


def test_literal_scoped_class_has_exact_declaring_owner(tmp_path: Path):
    from tree_sitter_language_pack import get_parser

    repo = tmp_path / "repo"
    repo.mkdir()
    source = b"module M\n class Base; end\n class Base::Child < Base; end\nend\n"
    assert not get_parser("ruby").parse(source).root_node.has_error
    (repo / "a.rb").write_bytes(source)
    graph = CodeGraph(repo)
    graph.build()
    result = graph.query("subclasses_of:Base")
    assert result["unresolved_refs"] == 0
    assert result["edges"][0]["source"]["name"] == "Base::Child"
    assert result["edges"][0]["source"]["kind"] == "class"
    records = graph.evidence_records()
    assertion = next(
        r for r in records if r["type"] == "assertion" and r["relation"] == "superclass"
    )
    owner = next(
        r for r in records if r["type"] == "entity" and r["id"] == assertion["source_entity_id"]
    )
    assert (owner["kind"], owner["name"]) == ("class", "Base::Child")
    graph.close()


@pytest.mark.parametrize(
    "language,suffix,source,caller,kind",
    [
        ("scss", ".scss", "@mixin alpha {} @mixin beta { @include alpha; }\n", "beta", "mixin"),
        ("scss", ".scss", "@mixin alpha {} @include alpha;\n", None, None),
        ("bash", ".sh", "alpha() { :; }; beta() { alpha; }\n", "beta", "function"),
        ("bash", ".sh", "alpha() { :; }; alpha;\n", None, None),
    ],
)
def test_same_line_callers_and_file_owned_nulls(
    tmp_path: Path, language: str, suffix: str, source: str, caller: str | None, kind: str | None
):
    from tree_sitter_language_pack import get_parser

    repo = tmp_path / "repo"
    repo.mkdir()
    raw = source.encode()
    assert not get_parser(language).parse(raw).root_node.has_error
    (repo / ("a" + suffix)).write_bytes(raw)
    graph = CodeGraph(repo)
    graph.build()
    edge = graph.query("callers_of:alpha")["edges"][0]
    assert edge["source"]["name"] == caller and edge["source"]["kind"] == kind
    if caller is None:
        assert graph._resolve_source_node(edge["source"]) == (None, None)
    graph.close()


@pytest.mark.parametrize("class_name", ["::Top", "self::Child"])
def test_unmodeled_absolute_or_dynamic_class_owner_is_unresolved(tmp_path: Path, class_name: str):
    from tree_sitter_language_pack import get_parser

    repo = tmp_path / "repo"
    repo.mkdir()
    raw = f"module M\n class Base; end\n class {class_name} < Base; end\nend\n".encode()
    assert not get_parser("ruby").parse(raw).root_node.has_error
    (repo / "a.rb").write_bytes(raw)
    graph = CodeGraph(repo)
    graph.build()
    result = graph.query("subclasses_of:Base")
    assert result["edges"] == [] and result["unresolved_refs"] == 1
    assert (
        graph.search_symbol("Base")["references"][0]["unresolved_reason"]
        == "unsupported_source_owner"
    )
    assert graph.search_symbol(class_name)["definitions"] == []
    graph.close()


def test_literal_scoped_module_has_exact_declaring_owner(tmp_path: Path):
    from tree_sitter_language_pack import get_parser

    repo = tmp_path / "repo"
    repo.mkdir()
    raw = b"module M\n module Base; end\n module Base::Nested\n  include Base\n end\nend\n"
    assert not get_parser("ruby").parse(raw).root_node.has_error
    (repo / "a.rb").write_bytes(raw)
    graph = CodeGraph(repo)
    graph.build()
    result = graph.query("subclasses_of:Base")
    assert result["unresolved_refs"] == 0
    assert result["edges"][0]["source"]["name"] == "Base::Nested"
    assert result["edges"][0]["source"]["kind"] == "module"
    records = graph.evidence_records()
    assertion = next(r for r in records if r["type"] == "assertion" and r["relation"] == "include")
    owner = next(
        r for r in records if r["type"] == "entity" and r["id"] == assertion["source_entity_id"]
    )
    assert (owner["kind"], owner["name"]) == ("module", "Base::Nested")
    graph.close()


@pytest.mark.parametrize("module_name", ["::Top", "self::Nested"])
def test_unmodeled_absolute_or_dynamic_module_owner_is_unresolved(tmp_path: Path, module_name: str):
    from tree_sitter_language_pack import get_parser

    repo = tmp_path / "repo"
    repo.mkdir()
    raw = f"module M\n module Base; end\n module {module_name}\n include Base\n end\nend\n".encode()
    assert not get_parser("ruby").parse(raw).root_node.has_error
    (repo / "a.rb").write_bytes(raw)
    graph = CodeGraph(repo)
    graph.build()
    result = graph.query("subclasses_of:Base")
    assert result["edges"] == [] and result["unresolved_refs"] == 1
    assert (
        graph.search_symbol("Base")["references"][0]["unresolved_reason"]
        == "unsupported_source_owner"
    )
    assert graph.search_symbol(module_name)["definitions"] == []
    graph.close()
