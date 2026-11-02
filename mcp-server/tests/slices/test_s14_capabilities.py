"""Capability format v4: explicit TSX support, readable v1-v3 history, closed validation."""

import asyncio
import copy
import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from test_s14_contract import build, workspace

import atlas_aci.server as server
from atlas_aci.codegraph import CodeGraph
from atlas_aci.config import Config
from atlas_aci.extractors import rust, tsx
from atlas_aci.protocol.graph import GRAPH_OUTPUT_SCHEMA
from atlas_aci.storage import evidence as ev

ROOT = Path(__file__).resolve().parents[3]
V3 = json.loads(
    (ROOT / "mcp-server/tests/fixtures/s14/historical-v3-capabilities.json").read_text()
)
HISTORICAL = json.loads(
    (ROOT / ".spectra/evidence/aci-s15/qualification/historical-capabilities.json").read_text()
)
BASE_LIMITS = {
    "python_imports": "package_local_unaliased_relative_only",
    "control_flow": "unsupported",
    "semantic_binding": "unsupported",
}


def tsx_row(caps):
    return next(r for r in caps["languages"] if r["language"] == "tsx")


def current(tmp_path):
    graph = CodeGraph(workspace(tmp_path))
    inputs = graph._build_inputs()
    caps = graph._capabilities(inputs["configuration"]["languages"])
    return graph, caps, inputs


def test_v4_snapshot_is_explicit(tmp_path):
    graph, caps, inputs = current(tmp_path)
    assert caps["format"] == "atlas-capabilities.v4"
    assert caps["limitations"] == {**BASE_LIMITS, **rust.LIMITATIONS, **tsx.LIMITATIONS}
    assert set(tsx.LIMITATIONS) == {
        "tsx_module_resolution",
        "tsx_jsx_binding",
        "tsx_intrinsic_jsx",
        "tsx_export_specifiers",
        "tsx_default_export_identifier",
        "tsx_anonymous_default_export",
        "tsx_dynamic_binding",
    }
    row = tsx_row(caps)
    assert row == {
        "language": "tsx",
        "extensions": [".tsx"],
        "extraction": {"state": "partial", "reason": "bounded_structural_queries"},
        "definitions": ["class", "function", "method"],
        "relations": ["call", "construct", "import", "jsx", "rationale_for", "superclass"],
        "semantic_binding": {"state": "unsupported", "reason": "no_compiler_provider"},
    }
    assert "extractors/tsx.py" in caps["producer"]["modules"]
    # tsx.py is the last module so ordered historical prefixes remain valid.
    assert list(caps["producer"]["modules"])[-1] == "extractors/tsx.py"
    assert CodeGraph._valid_capabilities(caps, inputs["producer"], inputs["configuration"])
    graph.close()


def test_advertised_relations_are_exactly_the_persisted_relations(tmp_path):
    repo = workspace(tmp_path)
    (repo / "notes.tsx").write_text(
        "// NOTE: top\nexport function Noted() {\n  // NOTE: inner\n}\n"
    )
    graph, _ = build(repo)
    persisted = {r["relation"] for r in graph.evidence_records() if r["type"] == "assertion"}
    assert persisted == set(tsx.RELATIONS)
    kinds = {r["kind"] for r in graph.evidence_records() if r["type"] == "entity"} - {"file"}
    assert kinds == set(tsx.KINDS)
    assert "jsx" not in ev.RELATIONS and "jsx" in ev.REFERENCE_RELATIONS
    graph.close()


def test_historical_v3_snapshot_still_validates_and_keeps_tsx_unsupported():
    caps, producer, configuration = V3["capabilities"], V3["producer"], V3["configuration"]
    assert caps["format"] == "atlas-capabilities.v3"
    assert "extractors/tsx.py" not in producer["modules"]
    assert CodeGraph._valid_capabilities(caps, producer, configuration)
    assert tsx_row(caps)["extraction"] == {"state": "unsupported", "reason": "no_extraction_query"}
    assert tsx_row(caps)["definitions"] == tsx_row(caps)["relations"] == []
    assert "tsx_module_resolution" not in caps["limitations"]


def test_v1_and_v2_history_still_validates_with_tsx_unsupported():
    assert {row["capabilities"]["format"] for row in HISTORICAL} == {
        "atlas-capabilities.v1",
        "atlas-capabilities.v2",
    }
    for row in HISTORICAL:
        assert CodeGraph._valid_capabilities(
            row["capabilities"], row["producer"], row["configuration"]
        )
        assert tsx_row(row["capabilities"])["extraction"] == {
            "state": "unsupported",
            "reason": "no_extraction_query",
        }


@pytest.mark.parametrize("with_enabled_language", [False, True])
def test_forged_v3_claiming_tsx_support_is_rejected(with_enabled_language):
    caps = copy.deepcopy(V3["capabilities"])
    configuration = copy.deepcopy(V3["configuration"])
    row = tsx_row(caps)
    row["extraction"] = {"state": "partial", "reason": "bounded_structural_queries"}
    row["definitions"] = list(tsx.KINDS)
    row["relations"] = list(tsx.RELATIONS)
    if with_enabled_language:
        configuration["languages"] = sorted([*configuration["languages"], "tsx"])
    assert not CodeGraph._valid_capabilities(caps, V3["producer"], configuration)


@pytest.mark.parametrize("old", range(len(HISTORICAL)))
def test_forged_pre_v3_claiming_tsx_support_is_rejected(old):
    item = copy.deepcopy(HISTORICAL[old])
    caps, configuration = item["capabilities"], item["configuration"]
    row = tsx_row(caps)
    row["extraction"] = {"state": "partial", "reason": "bounded_structural_queries"}
    row["definitions"], row["relations"] = ["class"], ["call"]
    configuration["languages"] = sorted([*configuration["languages"], "tsx"])
    assert not CodeGraph._valid_capabilities(caps, item["producer"], configuration)


def test_format_is_derived_from_producer_modules(tmp_path):
    graph, caps, inputs = current(tmp_path)
    producer, configuration = inputs["producer"], inputs["configuration"]
    # v4 capabilities cannot ride a producer that lacks tsx.py (nor v3 one with it).
    legacy_producer = copy.deepcopy(producer)
    del legacy_producer["modules"]["extractors/tsx.py"]
    claimed = copy.deepcopy(caps)
    claimed["producer"] = legacy_producer
    assert not CodeGraph._valid_capabilities(claimed, legacy_producer, configuration)
    downgraded = copy.deepcopy(caps)
    downgraded["format"] = "atlas-capabilities.v3"
    assert not CodeGraph._valid_capabilities(downgraded, producer, configuration)
    for stale_format in ("atlas-capabilities.v1", "atlas-capabilities.v2"):
        stale = copy.deepcopy(caps)
        stale["format"] = stale_format
        assert not CodeGraph._valid_capabilities(stale, producer, configuration)
    graph.close()


MUTATIONS = {
    "missing_limitation": lambda c: c["limitations"].pop("tsx_dynamic_binding"),
    "extra_limitation": lambda c: c["limitations"].update(tsx_fictional="yes"),
    "changed_limitation": lambda c: c["limitations"].update(tsx_jsx_binding="resolved"),
    "no_rust_limitation": lambda c: c["limitations"].pop("rust_cfg"),
    "drop_jsx": lambda c: tsx_row(c).update(
        relations=["call", "construct", "import", "rationale_for", "superclass"]
    ),
    "add_include": lambda c: tsx_row(c).update(relations=sorted([*tsx.RELATIONS, "include"])),
    "add_kind": lambda c: tsx_row(c).update(
        definitions=["class", "function", "interface", "method"]
    ),
    "wrong_extension": lambda c: tsx_row(c).update(extensions=[".tsx", ".jsx"]),
    "semantic": lambda c: tsx_row(c).update(semantic_binding={"state": "complete", "reason": "x"}),
    "claim_unsupported": lambda c: tsx_row(c).update(
        extraction={"state": "unsupported", "reason": "no_extraction_query"}
    ),
    "claim_rust_construct": lambda c: next(
        r for r in c["languages"] if r["language"] == "rust"
    ).update(relations=["call", "construct"]),
}


@pytest.mark.parametrize("mutation", sorted(MUTATIONS))
def test_v4_tsx_capabilities_are_closed_and_truthful(tmp_path, mutation):
    graph, caps, inputs = current(tmp_path)
    forged = copy.deepcopy(caps)
    MUTATIONS[mutation](forged)
    assert forged != caps
    assert not CodeGraph._valid_capabilities(forged, inputs["producer"], inputs["configuration"])
    graph.close()


def test_tsx_disabled_generation_is_reported_unsupported(tmp_path):
    graph = CodeGraph(workspace(tmp_path), langs=["python"])
    graph.build()
    payload = graph.status()
    runtime = tsx_row(payload["runtime"])
    indexed = tsx_row(payload["indexed"]["capabilities"])
    assert runtime["extraction"]["state"] == "partial"
    assert indexed["extraction"] == {"state": "unsupported", "reason": "language_disabled"}
    assert all(row["state"] == "disabled" for row in graph.observation_records())
    graph.close()


def test_real_status_payload_validates_against_protocol_schema(tmp_path):
    repo = workspace(tmp_path)
    graph, _ = build(repo)
    config = Config(repo=repo, memex_root=tmp_path / "memex")
    enforcement, memex, factory = server.build_server_state(config)
    try:
        result = asyncio.run(
            server.call_tool_result(
                "graph_query",
                {"schema_version": "atlas.graph.v1", "op": "status"},
                config,
                enforcement,
                memex,
                factory,
            )
        )
        assert not result.isError
        payload = result.structuredContent
        Draft202012Validator(GRAPH_OUTPUT_SCHEMA).validate(payload)
        data = payload["data"]
        modules = data["indexed"]["producer"]["modules"]
        assert "extractors/tsx.py" in modules
        capabilities = data["indexed"]["capabilities"]
        assert capabilities["stored_format"] == "atlas-capabilities.v4"
        row = tsx_row(capabilities)
        assert row["extraction"] == {"state": "partial", "reason": "bounded_structural_queries"}
        assert row["relations"] == list(tsx.RELATIONS)
        names = {entry["name"] for entry in capabilities["limitations"]}
        assert set(tsx.LIMITATIONS) <= names
        # The protocol never exposes a jsx relation: no traversal enum accepts it.
        assert "jsx" not in json.dumps(GRAPH_OUTPUT_SCHEMA).split('"relation"')[1][:200]
    finally:
        graph.close()
        factory.close()


def test_current_and_historical_producer_prefixes_validate(tmp_path):
    graph = CodeGraph(tmp_path)
    current_inputs = graph._producer_inputs()
    names = list(current_inputs["modules"])
    assert names[:7] == [
        "storage/evidence.py",
        "providers/python_imports.py",
        "indexing/extraction_cache.py",
        "resolvers/dependencies.py",
        "extractors/rust.py",
        "storage/snapshots.py",
        "indexing/retrieval.py",
    ]
    for count in range(2, len(names) + 1):
        old = copy.deepcopy(current_inputs)
        old["modules"] = {name: current_inputs["modules"][name] for name in names[:count]}
        ev.validate_record(ev.producer(old))
    # Skipping a predecessor is not a recorded historical set.
    skipped = copy.deepcopy(current_inputs)
    del skipped["modules"]["extractors/rust.py"]
    with pytest.raises(ValueError, match="module set"):
        ev.validate_record(ev.producer(skipped))
    graph.close()


def readme_matrix():
    text = (ROOT / "README.md").read_text()
    section = text.split("### TSX structural navigation", 1)[1].split("\n### ", 1)[0]
    rows = {}
    for line in section.splitlines():
        cells = [c.strip().strip("`") for c in line.strip().strip("|").split("|")]
        if (
            line.startswith("|")
            and len(cells) == 4
            and cells[0] not in {"Relation", "---"}
            and "---" not in cells[0]
        ):
            rows[cells[0]] = cells
    return rows


def test_readme_matrix_matches_capabilities_and_names_real_tests(tmp_path):
    graph, caps, _ = current(tmp_path)
    graph.close()
    matrix = readme_matrix()
    row = tsx_row(caps)
    relations = {
        name for name in matrix if name in {*row["relations"], "jsx"} or name in tsx.RELATIONS
    }
    assert relations == set(row["relations"])
    for name in row["definitions"]:
        assert ("definition: " + name) in matrix
    for key in tsx.LIMITATIONS:
        assert ("limitation: " + key) in matrix, key
    # Each row names a backing test that exists, by file and function.
    for label, (_, support, reason, backing) in matrix.items():
        assert support and reason and backing, label
        path, _, function = backing.partition("::")
        source = (ROOT / "mcp-server/tests/slices" / path).read_text()
        assert re.search(rf"^(async )?def {re.escape(function)}\(", source, re.M), backing
    for key, value in tsx.LIMITATIONS.items():
        assert matrix["limitation: " + key][1] == value
