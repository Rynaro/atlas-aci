"""ACI-S16: publication lifecycle, leakage, identity and recovery of an imported generation."""

import asyncio
import json

import pytest
from test_s16_support import (
    INDEX,
    LABELS,
    MANIFEST,
    built,
    craft,
    current_id,
    imported,
    manifest_for,
    official,
    published_bytes,
    records,
    rows,
    scip_records,
    stage_leftovers,
    workspace,
)

from atlas_aci.codegraph import CodeGraph
from atlas_aci.config import Config
from atlas_aci.enforcement import READ_ONLY_TOOLS
from atlas_aci.protocol.resources import read_resource
from atlas_aci.providers import scip
from atlas_aci.resolvers import dependencies as dep
from atlas_aci.server import build_server_state, call_tool_result
from atlas_aci.storage import evidence as ev

SAUDACAO = "scip-typescript npm s16-fixture 1.0.0 src/`greeting.ts`/`saudação`()."
CAFE = "scip-typescript npm s16-fixture 1.0.0 src/`greeting.ts`/`Café`#"


def find(index, path, rng):
    document = next(d for d in index.documents if d.relative_path == path)
    return document, next(o for o in document.occurrences if list(o.range) == rng)


def edited(tmp_path, mutate, name):
    path = craft(tmp_path, mutate, name + ".scip")
    return path, manifest_for(tmp_path, path.read_bytes(), name=name + ".json")


async def graph_call(state, **arguments):
    _w, config, enforcement, memex, factory = state
    return await call_tool_result(
        "graph_query",
        {"schema_version": "atlas.graph.v1", **arguments},
        config,
        enforcement,
        memex,
        factory,
    )


def server(tmp_path, repo):
    writer = CodeGraph(repo)
    config = Config(repo=repo, memex_root=tmp_path / "memex", max_calls_per_minute=0)
    enforcement, memex, factory = build_server_state(config)
    return writer, config, enforcement, memex, factory


# ---- status, lease, epoch ----
def test_status_lease_and_epoch_on_scip_import_generation(tmp_path):
    repo, graph, result = imported(tmp_path)
    reader = CodeGraph(repo, read_only=True)
    try:
        assert reader.epoch_ok() and graph.epoch_ok()
        status = reader.status()
        assert status["generation_id"] == result["generation_id"]
        assert status["completeness"]["state"] == "complete"
        assert status["completeness"]["reason"] == "observed_complete"
        (block,) = status["semantic_imports"]
        assert block["producer"] == {"name": "scip-typescript", "version": "0.4.0"}
        assert block["index_sha256"] == INDEX_SHA() and block["position_encoding"] == "utf-16"
        assert block["base_generation_id"] == result["base_generation_id"]
        assert block["counts"] == {
            "documents": 3,
            "definitions": 17,
            "references": 24,
            "resolved_references": 22,
            "ambiguous_references": 0,
            "unresolved_references": 2,
        }
        assert {item["name"] for item in block["limitations"]} == set(scip.LIMITATIONS)
        # Runtime capabilities stay unsupported: nothing runs at query time.
        capabilities = status["indexed"]["capabilities"]["languages"]
        assert all(lang["semantic_binding"]["state"] == "unsupported" for lang in capabilities)
        with reader.reader_lease() as lease:
            facts = lease.published_status()["facts"]
        # `facts` counts include the SCIP rows (documented).
        assert facts["definitions"] == 5 + 17 and facts["references"] == 5 + 24
    finally:
        reader.close()


def INDEX_SHA():
    import hashlib

    return hashlib.sha256(INDEX.read_bytes()).hexdigest()


def test_mcp_status_and_pinned_reader_survive_a_publication(tmp_path):
    repo, graph = built(tmp_path)
    with graph.reader_lease() as old:
        base_id = old.generation_id
        graph.import_scip(INDEX, MANIFEST)
        assert old.generation_id == base_id
        assert (
            old.db.execute("SELECT COUNT(*) FROM refs WHERE relation='reference'").fetchone()[0]
            == 0
        )
    state = server(tmp_path, repo)

    async def run():
        try:
            result = await graph_call(state, op="status")
            assert not result.isError, result
            data = json.loads(result.content[0].text)["data"]
            assert data["completeness"]["state"] == "complete"
        finally:
            state[-1].close()

    asyncio.run(run())


# ---- lifecycle ----
def test_index_after_import_is_a_full_rebuild(tmp_path):
    repo, graph, imported_result = imported(tmp_path)
    stats = graph.build(since="incremental")
    assert stats["binding_resolution"]["mode"] == "full"
    assert stats["files_skipped"] == 0 and stats["files_indexed"] == 3
    assert current_id(graph) == imported_result["base_generation_id"]
    assert scip_records(graph) == []
    assert rows(graph, "SELECT COUNT(*) AS n FROM refs WHERE relation='reference'")[0]["n"] == 0
    assert CodeGraph(repo, read_only=True).status()["semantic_imports"] == []
    assert graph.build()["files_indexed"] == 3  # without --since too


def test_later_parse_failure_does_not_retain_scip_history(tmp_path):
    repo, graph, _ = imported(tmp_path)
    (repo / "src/main.ts").write_text(
        (repo / "src/main.ts").read_text(encoding="utf-8") + "\nconst broken = (;\n",
        encoding="utf-8",
    )
    stats = graph.build()
    assert stats["files_failed"] == 1 and stats["extraction_state"] == "partial"
    assert scip_records(graph) == []
    histories = [r for r in graph.observation_records() if r["type"] == "history"]
    assert histories and all(h["path"] == "src/main.ts" for h in histories)
    for history in histories:
        for item in history["records"]:
            assert not str(item.get("kind", "")).startswith("scip_")
            assert "scip" not in json.dumps(item.get("fingerprint") or {})
            assert not str(item.get("role", "")).endswith(("scip_term", "scip_method"))
            assert item.get("relation") != "reference"
            if item["type"] == "projection":
                assert item["table"] in ev.COLUMNS and "scip-typescript" not in json.dumps(
                    item["row"]
                )
        assert any(
            item["type"] == "entity" for item in history["records"]
        )  # tree-sitter facts kept
    status = CodeGraph(repo, read_only=True).status()
    assert status["completeness"]["state"] == "partial" and status["semantic_imports"] == []


def test_dependency_witness_is_recomputed(tmp_path):
    _repo, graph, _ = imported(tmp_path)
    with graph.reader_lease() as lease:
        stored = dep.read(lease.db)
        inputs = json.loads(
            lease.db.execute("SELECT value FROM manifest WHERE key='generation_inputs'").fetchone()[
                0
            ]
        )
        assert stored == dep.witness(lease.db, inputs)
        assert {unit["path"] for unit in stored["units"]} == {
            "src/greeting.ts",
            "src/main.ts",
            "src/windows.ts",
        }
        dep.validate_outputs(lease.db)
    before = stored
    graph.build()
    with graph.reader_lease() as lease:
        assert dep.read(lease.db) != before


def test_base_must_be_a_current_build_generation(tmp_path):
    repo, graph = built(tmp_path)
    export = tmp_path / "export.jsonl"
    graph.export_jsonl(export)
    other = workspace(tmp_path, "from-import")
    importer = CodeGraph(other)
    importer.import_jsonl(export)
    before = published_bytes(importer)
    with pytest.raises(ValueError, match="needs a `build` generation"):
        importer.import_scip(INDEX, MANIFEST)
    assert published_bytes(importer) == before and stage_leftovers(importer) == []
    empty = workspace(tmp_path, "no-index")
    with pytest.raises(ValueError, match="atlas-aci index"):
        CodeGraph(empty).import_scip(INDEX, MANIFEST)
    assert [p.name for p in (empty / ".atlas").iterdir() if p.name != ".index.lock"] == []
    disabled = workspace(tmp_path, "disabled")
    CodeGraph(disabled, langs=["python"]).build()
    with pytest.raises(ValueError, match="document_not_indexed"):
        CodeGraph(disabled).import_scip(INDEX, MANIFEST)
    with pytest.raises(RuntimeError, match="writable"):
        CodeGraph(repo, read_only=True).import_scip(INDEX, MANIFEST)


def test_base_change_between_validation_and_lock_aborts(tmp_path, monkeypatch):
    repo, graph = built(tmp_path)
    real = scip.plan_import
    calls = []

    def plan_then_concurrent_publication(*args, **kwargs):
        plan = real(*args, **kwargs)
        calls.append(1)
        (repo / "src/extra.ts").write_text("export const extra = 1;\n")
        CodeGraph(repo).build()  # another operator publishes after validation, before our lock
        return plan

    monkeypatch.setattr(scip, "plan_import", plan_then_concurrent_publication)
    with pytest.raises(ValueError, match="changed during import"):
        graph.import_scip(INDEX, MANIFEST)
    assert calls == [1]
    assert stage_leftovers(graph) == []
    assert scip_records(graph) == []  # the concurrent build stands; nothing was overwritten
    assert rows(graph, "SELECT COUNT(*) AS n FROM files")[0]["n"] == 4


# ---- identity, roles, ambiguity, duplicates ----
def test_same_name_parameters_and_scip_entities_are_distinct(tmp_path):
    _repo, graph, _ = imported(tmp_path)
    entities = [
        r for r in records(graph) if r["type"] == "entity" and r["kind"].startswith("scip_")
    ]
    assert len(entities) == len({e["id"] for e in entities}) == 17
    params = [e for e in entities if e["kind"] == "scip_parameter" and e["name"] == "p"]
    assert len(params) == 2 and params[0]["id"] != params[1]["id"]
    assert {e["owners"][-1]["name"] for e in params} == {"saudação", "despedida"}
    assert all(e["owners"][0]["kind"] == "scip_package" for e in params)
    # Disjoint from tree-sitter kinds; deterministic across an independent import.
    structural = {e["kind"] for e in records(graph) if e["type"] == "entity"} - {
        e["kind"] for e in entities
    }
    assert structural and not any(k.startswith("scip_") for k in structural)
    _other_repo, other_graph, _ = imported(tmp_path, "twin")
    twin = {
        e["name"] + e["kind"]
        for e in records(other_graph)
        if e["type"] == "entity" and e["kind"].startswith("scip_")
    }
    assert twin == {e["name"] + e["kind"] for e in entities}


def test_multiple_definition_ranges_are_distinct_entities_and_references_ambiguous(tmp_path):
    _repo, graph = built(tmp_path)
    before = graph.confident_edges(), graph.god_nodes(), graph.communities()

    def declare_twice(index):
        document = next(d for d in index.documents if d.relative_path == "src/main.ts")
        extra = document.occurrences.add()
        extra.range.extend([3, 12, 20])  # the call-site spelling of saudação
        extra.symbol = SAUDACAO
        extra.symbol_roles = 1

    index, manifest = edited(tmp_path, declare_twice, "twice")
    result = graph.import_scip(index, manifest)
    assert result["definitions"] == 18 and result["ambiguous_references"] >= 2
    entities = [
        r
        for r in records(graph)
        if r["type"] == "entity" and r["kind"] == "scip_method" and r["name"] == "saudação"
    ]
    assert len(entities) == 2 and {e["path"] for e in entities} == {
        "src/greeting.ts",
        "src/main.ts",
    }
    ambiguous = rows(
        graph, "SELECT * FROM edges WHERE relation='reference' AND confidence='AMBIGUOUS'"
    )
    assert ambiguous and all(len(json.loads(e["candidates"])) == 2 for e in ambiguous)
    assert all(e["target_path"] is None for e in ambiguous)
    # Ambiguous SCIP edges stay out of structural analytics.
    after = graph.confident_edges(), graph.god_nodes(), graph.communities()
    assert after == before
    # The same symbol twice in ONE document yields ordinals, not a merge.
    graph2 = built(tmp_path, "again")[1]

    def same_document(index):
        document = next(d for d in index.documents if d.relative_path == "src/greeting.ts")
        extra = document.occurrences.add()
        extra.range.extend([3, 44, 52])
        extra.symbol = SAUDACAO
        extra.symbol_roles = 1

    index2, manifest2 = edited(tmp_path, same_document, "same-document")
    graph2.import_scip(index2, manifest2)
    twins = [
        r
        for r in records(graph2)
        if r["type"] == "entity" and r["kind"] == "scip_method" and r["name"] == "saudação"
    ]
    assert sorted(e["ordinal"] for e in twins) == [0, 1]


def test_duplicate_same_range_references_validate(tmp_path):
    _repo, graph = built(tmp_path)

    def stack(index):
        document, original = find(index, "src/main.ts", [0, 9, 17])
        second = document.occurrences.add()
        second.CopyFrom(original)
        second.symbol = CAFE  # a different symbol on exactly the same range
        clone = document.occurrences.add()
        clone.CopyFrom(original)  # an exact duplicate: collapses

    index, manifest = edited(tmp_path, stack, "stacked")
    result = graph.import_scip(index, manifest)
    assert result["references"] == 25 and result["definitions"] == 17
    same_range = rows(
        graph,
        "SELECT * FROM refs WHERE relation='reference' AND path='src/main.ts' AND line=1 AND col=9",
    )
    assert len(same_range) == 2 and len({r["callee_name"] for r in same_range}) == 2
    assert len({r["occurrence_id"] for r in same_range}) == 1  # one canonical occurrence
    with graph.reader_lease() as lease:
        dep.validate_outputs(lease.db)  # exactly one assertion per raw reference
        graph._validate_evidence_projections(lease.db)
        by_evidence = {}
        for (payload,) in lease.db.execute("SELECT payload FROM assertions"):
            item = json.loads(payload)
            by_evidence.setdefault(item["evidence_id"], []).append(item["raw_target"])
        shared = next(v for v in by_evidence.values() if len(v) == 2)
        assert len(set(shared)) == 2


def test_role_bits_are_recorded_and_never_become_imports(tmp_path):
    _repo, graph = built(tmp_path)

    def roles(index):
        _doc, occurrence = find(index, "src/main.ts", [0, 30, 36])
        occurrence.symbol_roles = 2 | 8 | 0x80  # Import | ReadAccess | unknown bit

    index, manifest = edited(tmp_path, roles, "roles")
    graph.import_scip(index, manifest)
    row = rows(
        graph,
        "SELECT * FROM refs WHERE relation='reference' AND path='src/main.ts' "
        "AND line=1 AND col=33",
    )[0]
    recorded = json.loads(row["binding"])["scip"]
    assert recorded["roles"] == 138 and recorded["role_names"] == [
        "Import",
        "ReadAccess",
        "Unknown:128",
    ]
    assert not [
        r for r in scip_records(graph) if r["type"] == "assertion" and r["relation"] == "import"
    ]
    assert rows(graph, "SELECT COUNT(*) AS n FROM import_bindings")[0]["n"] == 0


# ---- leakage into navigation, retrieval and context ----
def test_name_seeds_stay_unique_and_scip_is_addressable_by_id(tmp_path):
    repo, graph = built(tmp_path)
    state = server(tmp_path, repo)

    async def seed(name):
        result = await graph_call(
            state, op="neighborhood", seed={"name": name}, max_depth=1, deadline_ms=2000
        )
        assert not result.isError, result
        return json.loads(result.content[0].text)["data"]["seed"]

    async def run():
        try:
            before = {n: await seed(n) for n in ("saudação", "Café", "Pessoa", "brew", "nope")}
            assert before["saudação"]["state"] == before["Café"]["state"] == "unique"
            assert before["nope"]["state"] == "absent"
            graph.import_scip(INDEX, MANIFEST)
            after = {n: await seed(n) for n in before}
            assert after == before
            scip_entity = next(
                r
                for r in records(graph)
                if r["type"] == "entity" and r["kind"] == "scip_type" and r["name"] == "Café"
            )
            result = await graph_call(
                state,
                op="neighborhood",
                seed={"entity_id": scip_entity["id"]},
                max_depth=1,
                deadline_ms=2000,
            )
            assert not result.isError, result
            data = json.loads(result.content[0].text)["data"]
            assert (
                data["seed"]["state"] == "unique"
                and data["seed"]["candidates"][0]["kind"] == "scip_type"
            )
            assert data["edges"] == []  # reference is not traversable in this slice
        finally:
            state[-1].close()

    asyncio.run(run())


def test_retrieve_and_context_over_scip_defined_names(tmp_path):
    repo, _graph, _ = imported(tmp_path)
    state = server(tmp_path, repo)

    async def run():
        try:
            retrieved = await graph_call(state, op="retrieve", text="Café")
            assert not retrieved.isError, retrieved
            kinds = {
                c["match_tier"] + ":" + c.get("kind", "")
                for c in retrieved.structuredContent["data"]["candidates"]
            }
            assert any(k.startswith("exact_name") for k in kinds)
            names = [
                c
                for c in retrieved.structuredContent["data"]["candidates"]
                if c["match_tier"] == "exact_name"
            ]
            assert len(names) == 2  # tree-sitter class and SCIP type
            context = await graph_call(
                state, op="context", text="saudação", source_policy="references"
            )
            assert not context.isError, context
            bundles = json.loads(context.content[0].text)["data"]["bundles"]
            producers = {
                b["records"]["producer"]["id"]: b["records"]["producer"]["fingerprint"]
                for b in bundles
                if b["kind"] == "definition" and b["records"]["producer"]
            }
            assert any("scip" in (f or {}) for f in producers.values())
            assert any("codegraph_sha256" in (f or {}) for f in producers.values())
        finally:
            state[-1].close()

    asyncio.run(run())


def test_zero_width_module_span_is_a_readable_resource(tmp_path):
    repo, _graph, _ = imported(tmp_path)
    state = server(tmp_path, repo)

    async def run():
        try:
            result = await graph_call(
                state,
                op="context",
                text="greeting.ts",
                continuation=True,
                source_policy="references",
            )
            assert not result.isError, result
            resources = json.loads(result.content[0].text)["data"]["resources"]
            empty = [r for r in resources if r["start_byte"] == r["end_byte"] == 0]
            assert empty and empty[0]["path"] == "src/greeting.ts"
            content = (await read_resource(state[-1]._lease_registry, empty[0]["uri"], state[2]))[0]
            assert content.content == b"" and content.meta["complete"] is True
            word = next(
                r
                for r in resources
                if (r["start_byte"], r["end_byte"])
                == (LABEL("saudação")["start_byte"], LABEL("saudação")["end_byte"])
            )
            spelled = (await read_resource(state[-1]._lease_registry, word["uri"], state[2]))[0]
            assert spelled.content.decode() == "saudação"
        finally:
            await state[-1]._lease_registry.aclose()
            state[-1].close()

    asyncio.run(run())


def LABEL(text):
    return next(r for r in LABELS["definitions"] if r["text"] == text)


# ---- export/import round trip ----
def test_export_import_round_trip_yields_import_kind_with_same_records(tmp_path):
    _repo, graph, _ = imported(tmp_path)
    export = tmp_path / "scip-export.jsonl"
    graph.export_jsonl(export)
    twin = workspace(tmp_path, "twin")
    importer = CodeGraph(twin)
    importer.import_jsonl(export)
    assert sorted(r["id"] for r in records(importer)) == sorted(r["id"] for r in records(graph))
    assert sorted(r["id"] for r in scip_records(importer)) == sorted(
        r["id"] for r in scip_records(graph)
    )
    with importer.reader_lease() as lease:
        inputs = json.loads(
            lease.db.execute("SELECT value FROM manifest WHERE key='generation_inputs'").fetchone()[
                0
            ]
        )
    assert inputs["kind"] == "import" and "scip" not in inputs
    status = CodeGraph(twin, read_only=True).status()
    assert status["semantic_imports"] == []
    # An imported (not scip_import) generation cannot take a SCIP layer.
    with pytest.raises(ValueError, match="needs a `build` generation"):
        importer.import_scip(INDEX, MANIFEST)


def test_surface_is_unchanged_and_read_only():
    assert len(READ_ONLY_TOOLS) == 7
    assert not any("scip" in name or "import" in name for name in READ_ONLY_TOOLS)
    assert "reference" not in ev.RELATIONS and "reference" in ev.REFERENCE_RELATIONS
    assert "reference" not in __import__("atlas_aci.codegraph", fromlist=["x"]).KNOWN_EDGE_RELATIONS


def test_publication_leaves_no_staging_files_and_keeps_two_snapshots(tmp_path):
    _repo, graph, _ = imported(tmp_path)
    assert stage_leftovers(graph) == []
    store = graph.atlas_dir / "source-snapshots"
    manifests = [p for p in store.iterdir() if p.name.startswith("manifest.")]
    assert len(manifests) == 2  # the new snapshot and its build predecessor
    assert official() is not None
