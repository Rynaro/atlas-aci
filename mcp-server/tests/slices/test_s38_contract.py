"""ACI-S38 acceptance: declared fixture hosts over real stdio server subprocesses.

Two declared fixture hosts: an independent raw JSON-RPC line client and the mcp 1.27.0
``ClientSession``. They are labelled fixtures, never Claude Code, Copilot or Cursor.
"""

import asyncio
import base64
import json
import sys
from pathlib import Path

import anyio
import pytest
import test_s38_support as sup
from jsonschema import Draft202012Validator
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

from atlas_aci.config import Config
from atlas_aci.protocol import revisions as rev
from atlas_aci.protocol.graph import GRAPH_OUTPUT_SCHEMA
from atlas_aci.server import build_server_state, build_tool_manifest, call_tool_result

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "s38"
GOLDEN = FIXTURES / "transcripts"
GRAPH_STEPS = (7, 8, 11, 12, 13, 14, 15)
HEX64 = "0" * 64


@pytest.fixture(scope="module")
def lab(tmp_path_factory):
    return sup.get_lab(tmp_path_factory.mktemp("s38"))


def golden(case):
    return (GOLDEN / f"{case}.v1.json").read_bytes()


def bodies(run):
    return {ex.step: json.loads(ex.raw) for ex in run.exchanges if ex.raw is not None}


def text_payload(body):
    return json.loads(body["result"]["content"][0]["text"])


def dump(model):
    return model.model_dump(by_alias=True, mode="json", exclude_none=True)


# ----------------------------------------------------------------------------- SDK host
async def sdk_lifecycle(lab, revision, plain=False):
    """The same conversation through the mcp ClientSession (the raw-only step 17 is skipped)."""
    paths = lab.fresh()
    params = StdioServerParameters(
        command=sys.executable, args=paths.serve_argv()[1:], env=sup.child_env(0)
    )
    errpath = lab.root / f"sdk-{lab._counter}.stderr"
    rows, models = [], {}
    with errpath.open("w") as errlog, anyio.fail_after(120):
        async with stdio_client(params, errlog=errlog) as streams:
            async with ClientSession(*streams) as session:
                if plain:
                    init = await session.initialize()
                else:
                    init = await session.send_request(
                        types.ClientRequest(
                            types.InitializeRequest(
                                params=types.InitializeRequestParams(
                                    protocolVersion=revision,
                                    capabilities=types.ClientCapabilities(),
                                    clientInfo=types.Implementation(
                                        name="atlas-s38-fixture-host", version="1"
                                    ),
                                )
                            )
                        ),
                        types.InitializeResult,
                    )
                    await session.send_notification(
                        types.ClientNotification(types.InitializedNotification())
                    )
                rows.append((1, "initialize", dump(init)))
                models[1] = init
                rows.append((3, "ping", dump(await session.send_ping())))
                tools = await session.list_tools()
                models[4] = tools
                rows.append((4, "tools/list", dump(tools)))
                rows.append((5, "resources/list", dump(await session.list_resources())))
                templates = await session.list_resource_templates()
                rows.append((6, "resources/templates/list", dump(templates)))
                for step, name, arguments in sup.CONVERSATION:
                    if step in sup.TOOL_STEPS:
                        result = await session.call_tool(name, arguments)
                        models[step] = result
                        rows.append((step, "tools/call", dump(result)))
    return {"rows": rows, "models": models, "paths": paths, "stderr": errpath.read_text()}


def sdk_matches_golden(case, host):
    """Normalised SDK-host responses equal the golden responses for the same steps."""
    expected = {e["step"]: e["response"] for e in json.loads(golden(case))["exchanges"]}
    norm, subs = sup.Normaliser(), sup.n2_substitutions(host["paths"])
    checked = 0
    for step, method, body in host["rows"]:
        actual = sup.normalise_dump(norm, subs, method, body)
        assert actual == expected[step]["result"], f"{case} step {step}"
        checked += 1
    assert checked == len(host["rows"]) and checked >= 15


async def sdk_live(lab, revision):
    paths = lab.fresh(lab.live_paths)
    params = StdioServerParameters(
        command=sys.executable, args=paths.serve_argv()[1:], env=sup.child_env(0)
    )
    errpath = lab.root / f"sdk-live-{lab._counter}.stderr"
    nb = {
        "schema_version": sup.GRAPH,
        "op": "neighborhood",
        "seed": {"name": "A"},
        "max_depth": 1,
        "deadline_ms": 2000,
    }
    with errpath.open("w") as errlog, anyio.fail_after(120):
        async with stdio_client(params, errlog=errlog) as streams:
            async with ClientSession(*streams) as session:
                await session.send_request(
                    types.ClientRequest(
                        types.InitializeRequest(
                            params=types.InitializeRequestParams(
                                protocolVersion=revision,
                                capabilities=types.ClientCapabilities(),
                                clientInfo=types.Implementation(
                                    name="atlas-s38-fixture-host", version="1"
                                ),
                            )
                        )
                    ),
                    types.InitializeResult,
                )
                await session.send_notification(
                    types.ClientNotification(types.InitializedNotification())
                )
                await session.list_tools()
                first = await session.call_tool(
                    "graph_query",
                    {
                        "schema_version": sup.GRAPH,
                        "op": "page",
                        "query": nb,
                        "page_max_bytes": 18000,
                    },
                )
                payload = json.loads(first.content[0].text)
                data = payload["data"]
                descriptor = data["resources"][0]
                resource = await session.read_resource(descriptor["uri"])
                cont = await session.call_tool(
                    "graph_query",
                    {
                        "schema_version": sup.GRAPH,
                        "op": "continue",
                        "cursor": data["next_cursor"],
                        "query_digest": data["query_digest"],
                    },
                )
    return first, payload, descriptor, resource, cont


# --------------------------------------------------------------------------- AC-S38-001
@pytest.mark.parametrize("revision", sup.LEGACY_REVISIONS)
@pytest.mark.parametrize("host", ["raw", "sdk"])
def test_legacy_host_fixture(lab, host, revision):
    case = f"legacy.{revision}"
    negotiation = rev.negotiate(revision)
    assert (negotiation.outcome, negotiation.reason) == ("supported", "listed_revision")
    assert negotiation.family == "legacy" and negotiation.structured_results is False
    if host == "sdk":
        result = asyncio.run(sdk_lifecycle(lab, revision))
        assert result["models"][1].protocolVersion == revision
        assert not any(t.outputSchema for t in result["models"][4].tools)
        steps = sorted(set(sup.TOOL_STEPS) & set(result["models"]))
        assert steps == list(sup.TOOL_STEPS)
        for step in steps:
            assert result["models"][step].structuredContent is None
            assert result["models"][step].isError is sup.EXPECTED_IS_ERROR[step]
        sdk_matches_golden(case, result)
        assert "Traceback" not in result["stderr"]
        return
    run, _ = lab.lifecycle(case)
    responses = bodies(run)
    init = responses[1]["result"]
    assert init["protocolVersion"] == revision
    assert init["serverInfo"] == {"name": "atlas-aci", "version": "1.27.0"}
    assert init["capabilities"] == sup.RECORDED_CAPABILITIES
    assert responses[3]["result"] == {}
    tools = responses[4]["result"]["tools"]
    assert {t["name"] for t in tools} == sup.TOOL_NAMES and len(tools) == 7
    assert not any("outputSchema" in t for t in tools)
    assert {t["name"] for t in tools if "_meta" in t or "annotations" in t} == {"test_dry_run"}
    assert responses[5]["result"] == {"resources": []}
    templates = responses[6]["result"]["resourceTemplates"]
    assert len(templates) == 1 and templates[0]["uriTemplate"].startswith("atlas-source://")
    errors = {step: responses[step]["result"]["isError"] for step in sup.TOOL_STEPS}
    assert errors == sup.EXPECTED_IS_ERROR
    assert all(b"structuredContent" not in ex.raw for ex in run.exchanges if ex.raw)
    first = text_payload(responses[7])
    assert first["definitions"] and first["definitions"][0]["name"] == "target"
    assert responses[17]["error"]["code"] == -32601
    assert run.close == {"exit_code": 0, "killed": False}
    assert "Traceback" not in run.stderr
    assert lab.transcript(case) == golden(case)


# --------------------------------------------------------------------------- AC-S38-002
@pytest.mark.parametrize("revision", sup.MODERN_REVISIONS)
@pytest.mark.parametrize("host", ["raw", "sdk"])
def test_modern_host_fixture(lab, host, revision):
    case = f"modern.{revision}"
    negotiation = rev.negotiate(revision)
    assert negotiation.family == "modern" and negotiation.structured_results is True
    if host == "sdk":
        result = asyncio.run(sdk_lifecycle(lab, revision))
        assert result["models"][1].protocolVersion == revision
        for tool in result["models"][4].tools:
            assert (tool.outputSchema == GRAPH_OUTPUT_SCHEMA) is (tool.name == "graph_query")
            assert (tool.outputSchema is None) is (tool.name != "graph_query")
        for step in sup.TOOL_STEPS:
            model = result["models"][step]  # call_tool completing = client-side validation passed
            if step in GRAPH_STEPS:
                assert model.structuredContent == json.loads(model.content[0].text)
            else:
                assert model.structuredContent is None
        sdk_matches_golden(case, result)
        first, payload, descriptor, resource, cont = asyncio.run(sdk_live(lab, revision))
        blob = resource.contents[0]
        assert not first.isError and not cont.isError
        assert (
            base64.b64decode(blob.blob)
            == sup.LIVE_SOURCE[descriptor["start_byte"] : descriptor["end_byte"]]
        )
        assert blob.meta["generation_id"] == payload["generation_id"]
        assert "Traceback" not in result["stderr"]
        return
    run, _ = lab.lifecycle(case)
    responses = bodies(run)
    assert responses[1]["result"]["protocolVersion"] == revision
    tools = {t["name"]: t for t in responses[4]["result"]["tools"]}
    assert set(tools) == sup.TOOL_NAMES
    assert tools["graph_query"]["outputSchema"] == GRAPH_OUTPUT_SCHEMA
    assert not any("outputSchema" in t for name, t in tools.items() if name != "graph_query")
    for step in sup.TOOL_STEPS:
        result = responses[step]["result"]
        if step in GRAPH_STEPS:
            assert result["structuredContent"] == json.loads(result["content"][0]["text"])
        else:
            assert "structuredContent" not in result
    assert run.close == {"exit_code": 0, "killed": False}
    assert lab.transcript(case) == golden(case)
    live = lab.live(revision)
    assert not live["page"]["isError"] and not live["continue"]["isError"]
    blob = live["resource"]
    start, end = live["descriptor"]["start_byte"], live["descriptor"]["end_byte"]
    assert end > start and base64.b64decode(blob["blob"]) == live["source"][start:end]
    assert blob["_meta"]["generation_id"] == live["payload"]["generation_id"]
    assert (
        json.loads(live["continue"]["content"][0]["text"])["generation_id"]
        == (live["payload"]["generation_id"])
    )


def test_plain_initialize_negotiates_selected_revision(lab):
    result = asyncio.run(sdk_lifecycle(lab, None, plain=True))
    assert result["models"][1].protocolVersion == sup.SELECTED
    assert any(t.outputSchema for t in result["models"][4].tools)
    sdk_matches_golden(f"modern.{sup.SELECTED}", result)


# --------------------------------------------------------------------------- AC-S38-003
def test_schema_conformance(lab):
    schemas = sup.OfficialSchemas(FIXTURES / "schemas")
    provenance = json.loads((FIXTURES / "PROVENANCE.v1.json").read_text())
    assert provenance["files"] and provenance["license"]["spdx"] == "Apache-2.0"
    for entry in provenance["files"]:
        data = (FIXTURES / entry["path"]).read_bytes()
        assert sup.sha256_hex(data) == entry["sha256"] and len(data) == entry["bytes"]
        assert schemas.dialects[entry["revision"]] == entry["dialect"]
        assert schemas.defs_key[entry["revision"]] == entry["definitions_key"]
    licence = (FIXTURES / provenance["license"]["file"]).read_bytes()
    assert sup.sha256_hex(licence) == provenance["license"]["sha256"]
    assert sorted(schemas.schemas) == sorted(sup.ALL_REVISIONS)

    config = Config(repo=lab.paths.repo, memex_root=lab.paths.memex, graph_root=lab.paths.graphs)
    for revision in sup.ALL_REVISIONS:
        for tool in build_tool_manifest(config, revision):
            Draft202012Validator.check_schema(tool["inputSchema"])
            if "outputSchema" in tool:
                Draft202012Validator.check_schema(tool["outputSchema"])

    failures: set = set()
    for revision in sup.ALL_REVISIONS:
        family = "legacy" if revision in sup.LEGACY_REVISIONS else "modern"
        run, _ = lab.lifecycle(f"{family}.{revision}")
        found, covered = sup.validate_run(schemas, revision, run.case, run.exchanges)
        failures |= found
        assert {"InitializeResult", "ListToolsResult", "CallToolResult"} <= covered
        assert covered >= set(sup.ENVELOPES[revision])
        if revision in sup.MODERN_REVISIONS:
            live = lab.live(revision)
            found, covered = sup.validate_run(
                schemas, revision, f"live.{revision}", live["exchanges"]
            )
            failures |= found
            assert "ReadResourceResult" in covered
    assert failures == sup.KNOWN_DEVIATIONS

    # Negative controls: a corrupted frame fails under each schema, nested refs included.
    for revision in sup.ALL_REVISIONS:
        family = "legacy" if revision in sup.LEGACY_REVISIONS else "modern"
        run, _ = lab.lifecycle(f"{family}.{revision}")
        by_step = {ex.step: ex for ex in run.exchanges if ex.raw}
        corrupt = json.loads(by_step[1].raw)
        corrupt["result"]["protocolVersion"] = 5
        assert sup.validate_run(schemas, revision, "x", [_exchange(1, "initialize", corrupt)])[0]
        nested = json.loads(by_step[4].raw)
        nested["result"]["tools"][0]["name"] = 5
        failed, _ = sup.validate_run(schemas, revision, "x", [_exchange(4, "tools/list", nested)])
        assert any(row[3] == "/tools/*/name" for row in failed)
        call = json.loads(by_step[7].raw)
        call["result"]["content"][0]["type"] = 5
        failed, _ = sup.validate_run(schemas, revision, "x", [_exchange(7, "tools/call", call)])
        assert any(row[3].startswith("/content/") for row in failed)

    # structuredContent against the advertised outputSchema (Draft 2020-12), modern conversations.
    checked = 0
    for revision in sup.MODERN_REVISIONS:
        run, _ = lab.lifecycle(f"modern.{revision}")
        responses = bodies(run)
        output_schema = next(
            t["outputSchema"] for t in responses[4]["result"]["tools"] if t["name"] == "graph_query"
        )
        validator = Draft202012Validator(output_schema)
        for step in GRAPH_STEPS:
            structured = responses[step]["result"]["structuredContent"]
            validator.validate(structured)
            assert sup.canon(structured) == sup.canon(text_payload(responses[step]))
            checked += 1
    assert checked == 2 * len(GRAPH_STEPS)
    # Legacy text payloads validate under the same schema.
    legacy_checked = 0
    for revision in sup.LEGACY_REVISIONS:
        run, _ = lab.lifecycle(f"legacy.{revision}")
        for step in GRAPH_STEPS:
            Draft202012Validator(GRAPH_OUTPUT_SCHEMA).validate(text_payload(bodies(run)[step]))
            legacy_checked += 1
    assert legacy_checked == 2 * len(GRAPH_STEPS)

    # In-process results of every Day-1 (g) operation, plus a real page -> continue.
    results = asyncio.run(_inprocess_ops(lab))
    assert sorted(results) == ["continue", "neighborhood", "page", "path", "retrieve", "status"]
    for name, result in results.items():
        assert not result.isError, name
        Draft202012Validator(GRAPH_OUTPUT_SCHEMA).validate(result.structuredContent)
        assert sup.canon(result.structuredContent) == sup.canon(json.loads(result.content[0].text))


def _exchange(step, method, body):
    return sup.Exchange(step, method, method, "request", None, json.dumps(body).encode())


async def _inprocess_ops(lab):
    out = {}
    mini = Config(repo=lab.paths.repo, memex_root=lab.fresh().memex, graph_root=lab.paths.graphs)
    nb = {
        "schema_version": sup.GRAPH,
        "op": "neighborhood",
        "seed": {"name": "target"},
        "max_depth": 1,
        "deadline_ms": 2000,
    }
    calls = {
        "status": {"schema_version": sup.GRAPH, "op": "status"},
        "neighborhood": nb,
        "path": {
            "schema_version": sup.GRAPH,
            "op": "path",
            "seed": {"name": "caller"},
            "target": {"name": "target"},
            "deadline_ms": 2000,
        },
        "retrieve": {"schema_version": sup.GRAPH, "op": "retrieve", "text": "target"},
    }
    enforcement, memex, graph = build_server_state(mini)
    try:
        for name, args in calls.items():
            out[name] = await call_tool_result(
                "graph_query", args, mini, enforcement, memex, graph, protocol_version=sup.SELECTED
            )
    finally:
        graph.close()
    live = Config(
        repo=lab.live_paths.repo,
        memex_root=lab.fresh(lab.live_paths).memex,
        graph_root=lab.live_paths.graphs,
    )
    enforcement, memex, graph = build_server_state(live)
    try:
        page_args = {
            "schema_version": sup.GRAPH,
            "op": "page",
            "query": {**nb, "seed": {"name": "A"}},
            "page_max_bytes": 18000,
        }
        out["page"] = await call_tool_result(
            "graph_query", page_args, live, enforcement, memex, graph, protocol_version=sup.SELECTED
        )
        data = json.loads(out["page"].content[0].text)["data"]
        out["continue"] = await call_tool_result(
            "graph_query",
            {
                "schema_version": sup.GRAPH,
                "op": "continue",
                "cursor": data["next_cursor"],
                "query_digest": data["query_digest"],
            },
            live,
            enforcement,
            memex,
            graph,
            protocol_version=sup.SELECTED,
        )
        registry = getattr(graph, "_lease_registry", None)
        if registry is not None:
            await registry.aclose()
    finally:
        graph.close()
    return out


# --------------------------------------------------------------------------- AC-S38-004
def _ac004_params(case):
    """The exact initialize params the case sends (None when the case sends no initialize)."""
    method, value = sup.AC004_CASES[case][1][0]
    if method != "initialize":
        return None
    return json.loads(json.dumps(sup.ac004_params(value), default=lambda _: None))


UNSUPPORTED_EXPECT = {
    "U1": ("result", "counter_offer", "unknown_revision"),
    "U2": ("result", "counter_offer", "unknown_revision"),
    "U3": ("result", "counter_offer", "unknown_revision"),
    "U4": ("result", "counter_offer", "integer_revision"),
    "M1": ("error", "malformed", "missing_revision"),
    "M2a": ("error", "malformed", "invalid_revision_type"),
    "M2b": ("error", "malformed", "invalid_revision_type"),
    "M3": ("error", "malformed", "invalid_initialize_params"),
}
INVALID_PARAMS = {"code": -32602, "message": "Invalid request parameters", "data": ""}
RECORD_2099 = {
    "format": "atlas-protocol-negotiation.v1",
    "outcome": "counter_offer",
    "reason": "unknown_revision",
    "requested": "2099-01-01",
    "requested_len": 10,
    "requested_truncated": False,
    "requested_type": "string",
    "offered": "2025-11-25",
    "shaping_revision": "2025-11-25",
    "verified": True,
    "family": "modern",
    "structured_results": True,
    "wire": "initialize_result",
    "atlas_revisions": ["2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"],
    "selected": "2025-11-25",
    "sdk_supported": ["2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"],
}
RECORD_MISSING = {
    "format": "atlas-protocol-negotiation.v1",
    "outcome": "malformed",
    "reason": "missing_revision",
    "requested": None,
    "requested_len": None,
    "requested_truncated": False,
    "requested_type": "missing",
    "offered": None,
    "shaping_revision": None,
    "verified": False,
    "family": None,
    "structured_results": False,
    "wire": "invalid_params_error",
    "atlas_revisions": ["2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"],
    "selected": "2025-11-25",
    "sdk_supported": ["2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"],
}


def test_unsupported_version(lab):
    env = sup.child_env(0)
    for case, (kind, outcome, reason) in UNSUPPORTED_EXPECT.items():
        paths = lab.fresh()
        result = sup.run_ac004(case, paths.serve_argv(), env)
        first = result["responses"][0]
        negotiation = rev.classify_initialize(_ac004_params(case))
        assert (negotiation.outcome, negotiation.reason) == (outcome, reason), case
        if kind == "result":
            assert first["result"]["protocolVersion"] == negotiation.offered == sup.SELECTED
            assert negotiation.wire == "initialize_result" and "error" not in first
            assert first["result"]["serverInfo"] == {"name": "atlas-aci", "version": "1.27.0"}
        else:
            assert first["error"] == INVALID_PARAMS and "result" not in first, case
            assert negotiation.wire == "invalid_params_error"
        assert result["close"] == {"exit_code": 0, "killed": False}
        assert "Traceback" not in result["stderr"]
        if case in ("U1", "U4"):  # the unsupported-revision session is shaped as the selected one
            tools = {t["name"]: t for t in result["responses"][1]["result"]["tools"]}
            assert tools["graph_query"]["outputSchema"] == GRAPH_OUTPUT_SCHEMA
    # P1 / P2 lifecycle rows and R1 recovery.
    p1 = sup.run_ac004("P1", lab.fresh().serve_argv(), env)
    assert p1["responses"][0]["error"] == INVALID_PARAMS
    p2 = sup.run_ac004("P2", lab.fresh().serve_argv(), env)
    assert p2["responses"][0]["result"] == {}
    r1 = sup.run_ac004("R1", lab.fresh().serve_argv(), env)
    assert r1["responses"][0]["error"] == INVALID_PARAMS
    assert r1["responses"][1]["result"]["protocolVersion"] == "2024-11-05"
    assert r1["responses"][2]["result"] == {}
    legacy_tools = r1["responses"][3]["result"]["tools"]
    assert legacy_tools and not any("outputSchema" in t for t in legacy_tools)
    assert rev.classify_initialize(
        json.loads(json.dumps(sup.ac004_params("2024-11-05")))
    ).outcome == ("supported")
    for case in ("P1", "P2", "R1"):
        assert "Traceback" not in {"P1": p1, "P2": p2, "R1": r1}[case]["stderr"]

    # Diagnostics: stable bytes and the literal oracle.
    two = rev.negotiate("2099-01-01")
    assert two.record_bytes() == rev.negotiate("2099-01-01").record_bytes()
    assert two.record() == RECORD_2099
    missing = rev.classify_initialize(_ac004_params("M1"))
    assert missing.record_bytes() == rev.classify_initialize(_ac004_params("M1")).record_bytes()
    assert missing.record() == RECORD_MISSING
    assert set(two.record()) == set(rev.RECORD_KEYS)

    # Goldens for the four negotiation cases.
    for case in (c for c, (conv, _) in sup.CASES.items() if conv == "negotiation.v1"):
        assert lab.transcript(case) == golden(case), case

    # A future-only client (the primary C1) and a legacy-only variant disconnect deterministically.
    for supported, requested in (({"2099-01-01"}, "2099-01-01"), ({"2024-11-05"}, "2024-10-07")):
        client = sup.RawLineClient(lab.fresh().serve_argv(), env=env)
        try:
            record = sup.negotiate_as_client(client, supported, requested)
            frames_after_init = len(client.frames)
        finally:
            close = client.close()
        wire = rev.negotiate(requested)
        assert record == {
            "client_outcome": "disconnect",
            "reason": "offered_revision_not_supported_by_client",
            "requested": requested,
            "offered": sup.SELECTED,
        }
        assert wire.offered == record["offered"] and wire.outcome == "counter_offer"
        assert frames_after_init == 1  # the client sent nothing further
        assert close == {"exit_code": 0, "killed": False}
        stderr = client.stderr_text()
        assert "Traceback" not in stderr and "protocol_negotiated" not in stderr

    # C2: the legacy-only client reconnects with 2024-11-05, on the same process and on a fresh one.
    legacy_only = {"2024-11-05"}
    same = sup.RawLineClient(lab.fresh().serve_argv(), env=env)
    try:
        first = sup.negotiate_as_client(same, legacy_only, "2024-10-07")
        second = sup.negotiate_as_client(same, legacy_only, "2024-11-05")
    finally:
        same.close()
    fresh = sup.RawLineClient(lab.fresh().serve_argv(), env=env)
    try:
        third = sup.negotiate_as_client(fresh, legacy_only, "2024-11-05")
    finally:
        fresh.close()
    assert first["client_outcome"] == "disconnect"
    for record in (second, third):
        assert record["client_outcome"] == "proceed" and record["offered"] == "2024-11-05"
        assert record["completed"] == ["initialize", "ping", "tools/list"] and record["ping"] == {}
        assert record["tools"] == 7


# --------------------------------------------------------------------------- AC-S38-005
def test_engine_equivalence(lab):
    steps = [
        row for row in sup.steps_for("legacy.2024-11-05") if row[0] in (1, 2) or 7 <= row[0] <= 13
    ]
    steps.append(
        (
            18,
            "graph_query",
            "tools/call",
            "request",
            {
                "name": "graph_query",
                "arguments": {"schema_version": sup.GRAPH, "op": "retrieve", "text": "target"},
            },
        )
    )
    seen = {}
    for revision in ("2024-11-05", "2025-11-25"):
        rows = [
            (1, "initialize", "initialize", "request", sup.initialize_params(revision)),
            *steps[1:],
        ]
        client = sup.RawLineClient(lab.fresh().serve_argv(), env=sup.child_env(0))
        try:
            exchanges = sup.run_steps(client, rows)
        finally:
            client.close()
        seen[revision] = {ex.step: json.loads(ex.raw) for ex in exchanges if ex.raw is not None}
    legacy, modern = seen["2024-11-05"], seen["2025-11-25"]
    compared = [step for step in legacy if step >= 7]
    assert compared == [7, 8, 9, 10, 11, 12, 13, 18]
    for step in compared:
        old, new = legacy[step]["result"], modern[step]["result"]
        text = old["content"][0]["text"]
        assert text and text == new["content"][0]["text"], step  # byte-identical, nothing masked
        assert old["isError"] is new["isError"] is False
        assert "structuredContent" not in old
        if step in (7, 8, 11, 12, 13, 18):
            assert new["structuredContent"] == json.loads(text)
        else:
            assert "structuredContent" not in new
    payload = text_payload(legacy[7])
    assert payload["definitions"] and payload["references"]
    assert text_payload(legacy[13])["data"]["outcome"] == "complete"
    assert text_payload(legacy[12])["data"]["outcome"] == "complete"
    assert text_payload(legacy[18])["data"]["candidates"]
    # Non-vacuity: the two revisions really are served different manifests.
    old_run, _ = lab.lifecycle("legacy.2024-11-05")
    new_run, _ = lab.lifecycle("modern.2025-11-25")
    old_digest = sup.tools_digest(bodies(old_run)[4]["result"])["tools_digest"]
    new_digest = sup.tools_digest(bodies(new_run)[4]["result"])["tools_digest"]
    assert old_digest["sha256"] != new_digest["sha256"]
    assert old_digest["output_schema"] == [] and new_digest["output_schema"] == ["graph_query"]

    # Cross-check: dispatch_tool_call gives equal payloads for structured False and True.
    asyncio.run(_dispatch_cross_check(lab))


async def _dispatch_cross_check(lab):
    from atlas_aci.server import dispatch_tool_call

    config = Config(repo=lab.paths.repo, memex_root=lab.fresh().memex, graph_root=lab.paths.graphs)
    enforcement, memex, graph = build_server_state(config)
    queries = [
        {"query": "definitions_of:target"},
        {"query": "callers_of:target"},
        {"schema_version": sup.GRAPH, "op": "status"},
        {
            "schema_version": sup.GRAPH,
            "op": "neighborhood",
            "seed": {"name": "target"},
            "max_depth": 1,
            "deadline_ms": 2000,
        },
        {"schema_version": sup.GRAPH, "op": "retrieve", "text": "target"},
    ]
    try:
        for arguments in queries:
            old = await dispatch_tool_call(
                "graph_query", arguments, config, enforcement, memex, graph, structured=False
            )
            new = await dispatch_tool_call(
                "graph_query", arguments, config, enforcement, memex, graph, structured=True
            )
            assert old and old == new
        legacy = await dispatch_tool_call(
            "search_symbol", {"name": "target"}, config, enforcement, memex, graph, structured=False
        )
        assert legacy
    finally:
        graph.close()


# --------------------------------------------------------------------------- AC-S38-006
@pytest.mark.parametrize("revision", sup.ALL_REVISIONS)
def test_adapter_replay(lab, revision):
    family = "legacy" if revision in sup.LEGACY_REVISIONS else "modern"
    case = f"{family}.{revision}"
    (first, paths_a), (second, paths_b) = lab.lifecycle(case, 0), lab.lifecycle(case, 4242)
    assert first.pid and second.pid and first.pid != second.pid
    one, two = lab.transcript(case, 0), lab.transcript(case, 4242)
    assert one and one == two == golden(case)
    # Raw N2-only frames are equal too: any difference would have to be a named N-class.
    raw_a, raw_b = (
        sup.frame_digests(first.frames, paths_a),
        sup.frame_digests(second.frames, paths_b),
    )
    assert len(raw_a) >= 16 and raw_a == raw_b
    other = "modern.2025-11-25" if family == "legacy" else "legacy.2024-11-05"
    assert lab.transcript(other) != one
    manifest = json.loads((FIXTURES / "MANIFEST.v1.json").read_text())
    entry = {row["path"]: row for row in manifest["files"]}[f"transcripts/{case}.v1.json"]
    assert entry["sha256"] == sup.sha256_hex(golden(case)) and entry["bytes"] == len(golden(case))
    assert manifest["base_commit"] == sup.BASE_COMMIT
