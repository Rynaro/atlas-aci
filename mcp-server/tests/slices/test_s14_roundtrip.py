"""TSX cache packets, owner invariants, incremental resolution and portable round trips."""

import copy
import json

import pytest
from test_s14_contract import build, canonical, rows, workspace

from atlas_aci.codegraph import CodeGraph
from atlas_aci.indexing import extraction_cache as xc


def edge_owners(graph):
    return {
        (e["relation"], e["callee_name"], e["source_name"], e["source_kind"], e["target_name"])
        for e in rows(graph, "SELECT * FROM edges")
    }


def test_arrow_component_edge_owner_through_since_and_round_trip(tmp_path):
    repo = workspace(tmp_path)
    graph, _ = build(repo)
    # A resolved edge from inside an arrow component owns `function` Badge.
    assert ("call", "renderLabel", "Badge", "function", "renderLabel") in edge_owners(graph)
    components = repo / "components.tsx"
    components.write_text(components.read_text().replace("renderLabel", "renderTitle"))
    graph.build(since="incremental")
    assert ("call", "renderTitle", "Badge", "function", "renderTitle") in edge_owners(graph)
    assert not [e for e in edge_owners(graph) if e[1] == "renderLabel"]
    fresh_repo = workspace(tmp_path, name="fresh")
    (fresh_repo / "components.tsx").write_text(components.read_text())
    fresh, _ = build(fresh_repo)
    assert canonical(graph) == canonical(fresh)
    # Export -> source-free import re-runs projection validation, including the
    # edge-owner and TSX import-description checks.
    artifact = tmp_path / "export.jsonl"
    graph.export_jsonl(artifact)
    destination = tmp_path / "source-free"
    destination.mkdir()
    imported = CodeGraph(destination)
    imported.import_jsonl(artifact)
    assert imported.evidence_records() == graph.evidence_records()
    replay = tmp_path / "replay.jsonl"
    imported.export_jsonl(replay)
    assert replay.read_bytes() == artifact.read_bytes()
    assert edge_owners(imported) == edge_owners(graph)
    assert {
        r["description"] for r in rows(imported, "SELECT description FROM import_bindings")
    } == {r["description"] for r in rows(graph, "SELECT description FROM import_bindings")}
    # A function-expression declarator is a function owner too.
    imported.close()
    graph.close()
    fresh.close()


def test_function_expression_declarator_owner(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.tsx").write_text(
        "function helper() { return 1; }\n"
        "export const Widget = function () {\n"
        "  return helper();\n"
        "};\n"
        "const Nested = () => {\n"
        "  const Inner = () => helper();\n"
        "  return Inner();\n"
        "};\n"
    )
    graph, _ = build(repo)
    owners = {(e[2], e[3]) for e in edge_owners(graph) if e[1] == "helper"}
    assert owners == {("Widget", "function"), ("Inner", "function")}
    artifact = tmp_path / "a.jsonl"
    graph.export_jsonl(artifact)
    target = tmp_path / "dest"
    target.mkdir()
    CodeGraph(target).import_jsonl(artifact)
    graph.close()


def test_rationale_comments_on_tsx(tmp_path):
    repo = workspace(tmp_path)
    graph, _ = build(repo)
    (repo / "notes.tsx").write_text(
        "// HACK: top-level, no enclosing definition\n"
        "export function Noted() {\n"
        "  // NOTE: see ADR-0007 for the reasoning\n"
        "  return <div>{/* ADR-0009 explains this tag */}</div>;\n"
        "}\n"
        "// just prose, not a rationale comment\n"
    )
    graph.build(since="incremental")
    rationale = rows(graph, "SELECT * FROM rationale WHERE path='notes.tsx' ORDER BY line")
    assert [(r["line"], r["label"], r["target_name"]) for r in rationale] == [
        (1, None, None),
        (3, "ADR-0007", "Noted"),
        (4, "ADR-0009", "Noted"),
    ]
    records = [
        r
        for r in graph.evidence_records()
        if r["type"] == "assertion" and r["relation"] == "rationale_for"
    ]
    by_text = {r["raw_target"]: r for r in records}
    assert "// just prose, not a rationale comment" not in by_text
    attached = by_text["// NOTE: see ADR-0007 for the reasoning"]
    assert attached["resolution"] == "EXTRACTED" and attached["target_entity_id"]
    top = by_text["// HACK: top-level, no enclosing definition"]
    assert top["resolution"] == "UNRESOLVED"
    assert top["unresolved_reason"] == "no_enclosing_definition"
    graph.close()


def packet_path(graph, rel):
    for entry in (graph.atlas_dir / "extraction-cache/v1").glob("*/*.json"):
        if json.loads(entry.read_text())["inputs"]["path"] == rel:
            return entry
    raise AssertionError(rel)


def store(path, item):
    item["checksum"] = xc.digest({"packet": item["packet"], "failure": item["failure"]})
    path.write_text(json.dumps(item))


def mutate_import(field, value):
    def apply(packet):
        row = packet["imports"][0]
        description = json.loads(row["description"])
        if value is None:
            del description[field]
        else:
            description[field] = value
        row["description"] = json.dumps(description, sort_keys=True)

    return apply


def python_shaped_import(packet):
    row = packet["imports"][0]
    row["description"] = json.dumps(
        {"level": 1, "module": [], "name": "x", "local_name": row["local_name"], "reason": None}
    )


MUTATIONS = {
    "missing_type_only": mutate_import("type_only", None),
    "type_only_not_bool": mutate_import("type_only", 1),
    "bad_kind": mutate_import("kind", "aliased"),
    "resolved_reason": mutate_import("reason", None),
    "wrong_reason": mutate_import("reason", "relative_import_missing_module"),
    "source_not_string": mutate_import("source", 7),
    "python_shape": python_shaped_import,
    "jsx_role": lambda p: next(r for r in p["refs"] if r["relation"] == "jsx").update(
        relation="call"
    ),
    "unknown_relation": lambda p: p["refs"][0].update(relation="include_x"),
    "export_status": lambda p: (
        json.loads(p["symbols"][0]["binding"])
        and p["symbols"][0].update(
            binding=json.dumps(
                {**json.loads(p["symbols"][0]["binding"]), "export_status": "private"}
            )
        )
    ),
}


@pytest.mark.parametrize("mutation", sorted(MUTATIONS))
def test_invalid_tsx_packet_is_a_miss_and_reparsed(tmp_path, mutation):
    repo = workspace(tmp_path)
    graph, _ = build(repo)
    before = canonical(graph)
    path = packet_path(graph, "components.tsx")
    item = json.loads(path.read_text())
    original = copy.deepcopy(item["packet"])
    MUTATIONS[mutation](item["packet"])
    assert item["packet"] != original, "mutation must be non-vacuous"
    store(path, item)
    stats = graph.build()
    assert stats["extraction_cache"]["miss_reasons"] == {"invalid": 1}
    assert canonical(graph) == before
    assert graph.build()["extraction_cache"]["disk_hits"] == 5
    graph.close()


def test_valid_tsx_packet_is_a_hit_with_same_graph(tmp_path):
    repo = workspace(tmp_path)
    cold, _ = build(repo)
    before = canonical(cold)
    path = packet_path(cold, "components.tsx")
    packet = json.loads(path.read_text())["packet"]
    assert any(json.loads(r["description"])["kind"] == "namespace" for r in packet["imports"])
    assert {r["relation"] for r in packet["refs"]} >= {"jsx", "call", "superclass"}
    assert any(json.loads(r["binding"]).get("export_status") == "named" for r in packet["symbols"])
    stats = cold.build()
    assert stats["extraction_cache"]["disk_hits"] == 5
    assert canonical(cold) == before
    cold.close()
