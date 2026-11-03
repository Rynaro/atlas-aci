"""ACI-S20 MCP surface: typed errors, operator-only before side, budgets, additive schema."""

from __future__ import annotations

import copy
import hashlib
import json

import pytest
from click.testing import CliRunner
from test_s20_support import (
    V1,
    V2,
    Served,
    build,
    init_git,
    inventory,
    same_path_pair,
    served_pair,
    write_tree,
)

from atlas_aci.__main__ import cli
from atlas_aci.config import Config
from atlas_aci.enforcement import READ_ONLY_TOOLS
from atlas_aci.protocol import graph
from atlas_aci.protocol.impact import IMPACT_DATA, IMPACT_INPUT
from atlas_aci.protocol.results import FIXED_ERROR_ENVELOPE_CAP, FIXED_MESSAGES, result_bytes
from atlas_aci.query.admission import MINIMUM_BUDGET
from atlas_aci.query.impact import validate_configuration
from atlas_aci.query.impact_admission import IMPACT_MINIMUM_BUDGET
from atlas_aci.query.paging import COLLECTION_MINIMUM_BUDGET
from atlas_aci.query.traversal import LIMITS
from atlas_aci.server import build_tool_manifest
from atlas_aci.storage import evidence as ev


def error_of(result):
    assert result.isError
    payload = result.structuredContent
    assert payload["status"] == "error" and payload["data"] is None
    assert payload["operation"] == "impact"
    return payload["error"]


def no_path_leak(result, *roots):
    text = result.content[0].text + json.dumps(result.structuredContent)
    for root in roots:
        assert str(root) not in text


def test_unconfigured_is_typed_and_precedes_any_lease(tmp_path):
    repo, before, after = same_path_pair(tmp_path, V1, V2)
    served = Served(tmp_path, repo, before, after, unconfigured=True)
    try:
        error = error_of(served.call({}))
        assert error["code"] == "IMPACT_UNCONFIGURED" and error["retry_hint"] == "different_tool"
        no_path_leak(served.call({}), tmp_path)
    finally:
        served.close()
    # Deterministic even with no index at all: it is decided before any lease.
    bare = tmp_path / "bare"
    bare.mkdir()
    config = Config(repo=bare, memex_root=tmp_path / "bare-memex", max_calls_per_minute=0)
    served = Served.__new__(Served)
    served.repo, served.before, served.after, served.config = bare, before, after, config
    from atlas_aci.server import build_server_state

    served.enforcement, served.memex, served.graph = build_server_state(config)
    try:
        assert error_of(served.call({}))["code"] == "IMPACT_UNCONFIGURED"
    finally:
        served.close()


def test_diff_rejections_are_closed_reasons_without_paths(tmp_path):
    repo, before, after = same_path_pair(tmp_path, V1, V2)
    # An unrelated repository identity.
    other = tmp_path / "other"
    other.mkdir()
    init_git(other)
    write_tree(other, V1)
    build(other, tmp_path / "other-root")
    unrelated = Served(
        tmp_path, repo, before, after, before_repo=other, before_graph_root=tmp_path / "other-root"
    )
    try:
        result = unrelated.call({})
        error = error_of(result)
        assert error["code"] == "SNAPSHOT_DIFF_REJECTED"
        assert error["message"] == "Snapshot diff rejected: repository_identity_mismatch"
        no_path_leak(result, tmp_path)
    finally:
        unrelated.close()
    # A before root with no index at all, and a root that is missing entirely.
    for name in ("empty-root", "missing/deeper"):
        served = Served(tmp_path, repo, before, after, before_graph_root=tmp_path / name)
        try:
            result = served.call({})
            error = error_of(result)
            assert error["code"] == "SNAPSHOT_DIFF_REJECTED"
            assert error["message"] == "Snapshot diff rejected: index_unavailable"
            no_path_leak(result, tmp_path)
        finally:
            served.close()
    # A before root INSIDE the checkout is refused by the graph layer: fixed, path-free.
    inside = Served(tmp_path, repo, before, after, before_graph_root=repo / "inside-root")
    try:
        result = inside.call({})
        assert error_of(result)["message"] == "Snapshot diff rejected: index_unavailable"
        no_path_leak(result, tmp_path, repo)
    finally:
        inside.close()


def test_same_namespace_is_rejected(tmp_path):
    repo, before, after = same_path_pair(tmp_path, V1, V2)
    served = Served(tmp_path, repo, before, after, before_graph_root=after.graph_root)
    try:
        error = error_of(served.call({}))
        assert error["message"] == "Snapshot diff rejected: same_namespace"
    finally:
        served.close()
    # Startup rejection (library + CLI `serve`), before any server state exists.
    config = Config(
        repo=repo,
        memex_root=tmp_path / "m",
        graph_root=after.graph_root,
        before_graph_root=after.graph_root,
    )
    with pytest.raises(ValueError, match="same graph namespace"):
        validate_configuration(config)
    alone = Config(repo=repo, memex_root=tmp_path / "m", before_repo=repo)  # default == default
    with pytest.raises(ValueError, match="same graph namespace"):
        validate_configuration(alone)
    run = CliRunner().invoke(
        cli,
        [
            "serve",
            "--repo",
            str(repo),
            "--graph-root",
            str(after.graph_root),
            "--before-graph-root",
            str(after.graph_root),
        ],
    )
    assert run.exit_code == 1 and "same graph namespace" in run.output
    ok = Config(
        repo=repo,
        memex_root=tmp_path / "m",
        graph_root=after.graph_root,
        before_graph_root=before.graph_root,
    )
    validate_configuration(ok)  # distinct namespaces validate cleanly


def test_generation_guards(tmp_path):
    served = served_pair(tmp_path, V1, V2)
    try:
        data = served.impact()["data"]
        wrong = "0" * 64
        assert wrong not in (data["before"]["generation_id"], data["after"]["generation_id"])
        for key in ("expected_generation_id", "expected_before_generation_id"):
            error = error_of(served.call({key: wrong}))
            assert error["code"] == "SNAPSHOT_MISMATCH"
        right = served.impact(
            expected_generation_id=data["after"]["generation_id"],
            expected_before_generation_id=data["before"]["generation_id"],
        )
        assert right["data"]["impact_id"] == data["impact_id"]
        # The guards are not interchangeable.
        swapped = served.call(
            {
                "expected_generation_id": data["before"]["generation_id"],
                "expected_before_generation_id": data["after"]["generation_id"],
            }
        )
        assert error_of(swapped)["code"] == "SNAPSHOT_MISMATCH"
    finally:
        served.close()


def test_no_client_supplied_paths(tmp_path):
    served = served_pair(tmp_path, V1, V2)
    try:
        for extra in (
            {"before_graph_root": str(tmp_path / "before")},
            {"repo": str(tmp_path)},
            {"before_repo": str(tmp_path)},
            {"graph_root": str(tmp_path)},
            {"path": "/etc/passwd"},
        ):
            result = served.call(extra)
            assert error_of(result)["code"] == "INVALID_INPUT"
            no_path_leak(result, tmp_path)
        ok = served.call({})
        assert not ok.isError
        assert str(tmp_path) not in ok.content[0].text
    finally:
        served.close()
    # INVALID_INPUT is decided before any lease: it holds even with no index at all.
    bare = tmp_path / "bare"
    bare.mkdir()
    config = Config(repo=bare, memex_root=tmp_path / "bm", max_calls_per_minute=0)
    state = Served.__new__(Served)
    state.repo, state.config = bare, config
    from atlas_aci.server import build_server_state

    state.enforcement, state.memex, state.graph = build_server_state(config)
    try:
        assert error_of(state.call({"before_graph_root": str(tmp_path)}))["code"] == "INVALID_INPUT"
    finally:
        state.close()


@pytest.mark.parametrize(
    "bad",
    [
        {"relations": ["rationale_for"]},
        {"relations": []},
        {"relations": ["call", "call"]},
        {"confidence": ["AMBIGUOUS"]},
        {"max_depth": 0},
        {"max_depth": 33},
        {"max_seeds": 0},
        {"max_visited_nodes": 0},
        {"max_examined_edges": 10001},
        {"deadline_ms": 2001},
        {"max_boundary_probes": -1},
        {"max_vm_operations": 10_000_001},
        {"max_depth": True},
        {"max_depth": 2.0},
        {"direction": "outgoing"},
    ],
)
def test_invalid_limits_are_rejected(tmp_path, bad):
    served = served_pair(tmp_path, V1, V2)
    try:
        assert error_of(served.call(bad))["code"] == "INVALID_INPUT"
    finally:
        served.close()


def test_budget_minimum_and_tool_raised_budget(tmp_path):
    served = served_pair(tmp_path, V1, V1)  # identical snapshots: zero seeds
    try:
        full = served.impact()["data"]
        assert full["seeds"] == [] and full["impacted"] == []
        assert full["coverage"]["state"] == "complete"
        exact = served.call({"max_bytes": IMPACT_MINIMUM_BUDGET})
        assert not exact.isError
        assert len(result_bytes(exact)) <= IMPACT_MINIMUM_BUDGET
        below = served.call({"max_bytes": IMPACT_MINIMUM_BUDGET - 1})
        error = error_of(below)
        assert error["code"] == "INVALID_BUDGET"
        assert error["message"] == FIXED_MESSAGES["INVALID_BUDGET"]
        assert len(result_bytes(below)) <= FIXED_ERROR_ENVELOPE_CAP
    finally:
        served.close()
    changed_root = tmp_path / "changed"
    changed_root.mkdir()
    changed = served_pair(changed_root, V1, V2)
    try:
        # Exactly the minimum, but the real metadata (three seeds) does not fit.
        tool = changed.call({"max_bytes": IMPACT_MINIMUM_BUDGET})
        error = error_of(tool)
        assert error["code"] == "INVALID_BUDGET"
        assert error["message"] == "Impact metadata cannot fit the result budget."
        assert error["message"] != FIXED_MESSAGES["INVALID_BUDGET"]
        # Operator ceiling is honoured too.
        assert error_of(changed.call({"max_bytes": 10**9}))["code"] == "INVALID_BUDGET"
    finally:
        changed.close()


def test_whole_atom_prefix_never_reorders_and_totals_are_honest(tmp_path):
    served = served_pair(tmp_path, V1, V2)
    try:
        full = served.impact()["data"]
        ids = [i["entity"]["id"] for i in full["impacted"]]
        assert len(ids) >= 3 and full["coverage"]["boundaries_total"] >= 1
        full_size = len(result_bytes(served.call({})))
        seen_counts = set()
        for budget in range(IMPACT_MINIMUM_BUDGET, full_size + 1, 350):
            result = served.call({"max_bytes": budget})
            if result.isError:
                continue
            assert len(result_bytes(result)) <= budget
            data = result.structuredContent["data"]
            got = [i["entity"]["id"] for i in data["impacted"]]
            assert got == ids[: len(got)]  # strict prefix, ranked order
            assert data["impacted_total"] == len(ids) == full["impacted_total"]
            assert data["coverage"]["boundaries_total"] == full["coverage"]["boundaries_total"]
            assert data["coverage"]["reasons"] == full["coverage"]["reasons"]
            assert data["seeds"] == full["seeds"]
            if data["coverage"]["boundaries"]:
                assert len(got) == len(ids)  # boundaries come after ALL impacted atoms
            assert data["selection"]["truncated"] == (
                len(got) < len(ids)
                or len(data["coverage"]["boundaries"]) < data["coverage"]["boundaries_total"]
            )
            seen_counts.add(len(got))
        # Strictly partial prefixes and the complete list were both observed.
        assert {1, 2} <= seen_counts and max(seen_counts) == len(ids)
    finally:
        served.close()


def test_element_limit_stops_admission_and_caps_seeds(tmp_path):
    repo, before, after = same_path_pair(tmp_path, V1, V2)
    served = Served(tmp_path, repo, before, after, max_bound_field_elements=1)
    try:
        data = served.impact()["data"]
        assert len(data["seeds"]) == 1 and data["seeds_total"] == 3
        assert data["coverage"]["reasons"]["seed_limit"] is True
        assert data["effective_limits"]["element_limit"] == 1
        # Every path has two nodes, which exceeds a one-element cap: nothing is admitted.
        assert data["impacted"] == [] and data["impacted_total"] >= 1
        assert data["selection"] == {"truncated": True, "reason": "element_limit"}
        assert data["coverage"]["state"] == "incomplete"
    finally:
        served.close()


def test_read_only_inventory_across_every_path(tmp_path):
    repo, before, after = same_path_pair(tmp_path, V1, V2)
    served = Served(tmp_path, repo, before, after)
    strict = Served(tmp_path, repo, before, after, impact_max_changed_files=0)
    memex = tmp_path / "memex"
    watched = (repo, before.graph_root, after.graph_root, memex)
    try:
        baseline = inventory(*watched)
        data = served.impact()["data"]
        assert inventory(*watched) == baseline
        calls = [
            (served, {"expected_generation_id": "0" * 64}),
            (served, {"expected_before_generation_id": "0" * 64}),
            (served, {"before_graph_root": "/"}),
            (served, {"max_bytes": 10}),
            (served, {"max_depth": 0}),
            (strict, {}),
            (served, {"relations": ["call"]}),
        ]
        for state, arguments in calls:
            state.call(arguments)
            assert inventory(*watched) == baseline
        assert data["impacted"]
    finally:
        served.close()
        strict.close()


# ---- G-SCHEMA-ADDITIVE / G-ENVELOPE / G-MCP pins (recorded on base 59a4819) --------


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


BASE_INPUT = [
    "b001a8bce7cd795e573254827a2f26911fcd35641af763348a44224ddd1bf191",
    "562ac034ac911bde25331921bcac32656356d22a52dc65f34c423e61827257f6",
    "6ec8b579fdcffec252b9cc9f2aafe6b12b87b64cf6e4ac772ad502b48df407ef",
    "ec88f473ba826ff344bc19227ad2de947341512b0994599ac5d4524785e6d921",
    "53d944868f322031cf9870256598f4b8a43997925b40a94539fc30ea0fe1b226",
    "6387ac0ddd392bcf03a172a66b95450a8e1b84d1116483edd9a0124b63a9a66e",
    "7f9123885be481ec2cbcd1fe09c53ae4db412ddb1a31fabcb3dc8a32764c4ae5",
    "56761ef227de39b82babe79f041759071feb1d92818726ba3047b959fd06c418",
]
BASE_DATA = [
    "348bb83c519afcf6ab2fb6bddcf81f74ce7ddecdfc7940fc00a5d182c4b77a8e",
    "5692331f381de942e225e5fa83112b616b0cc4fb8c729e97ac0786c4309c459d",
    "20794230d0768f9e82678486a2b728c08a119679412520f081cf185ddef556c6",
    "a492ddb25af569a506255e7cebbaa06f3f5fba7340b330c31dfa8d2ab16c6216",
    "0559d4c28778b408aea13f4bc02cab1eca85cb9e50caec370393295009694a5f",
    "bcde375ebd4cbacf651311181173836b169d5a360c6ac158c6a2cdaf49be3f61",
    "8bd4c019da81b2f89b5390c6b903b5331739275eda0d2568e803a57f71a79f64",
]
BASE_ENVELOPE = [
    "5affb52a6262f3f0d896df4f3059ce0463462f1428069e62323111a15e56772d",
    "5ecfe53b627685e32325663b8a6978156399e714e5eeb4119f4f6ed8eeab204f",
    "d5e10bc55ed798b4c13bed1b50ca9a5352469c8d5f2d1cd559176148975c84ae",
    "79f960068225cdc7fb71c4205883fe5d2a543d8137630402e74939f9ea580a62",
    "4389a037b235da77a0d950ece643a72dc999203b29b85135000d6a405d14d4bb",
    "591995b48d24222b1454480334c5fc9772a678af1787be83ba761a40e98e5ead",
    "00b2ef2ec343ff6fc718021eef5bf6deba5d1d5a35c0ff89b443fe14736df89a",
    "6a371051211906d11115be4378cd3b4d62ce41088448bb9ec81dd1dd6469fc9e",
    "817be42a05668eaa98ae9badc8963ea4c4d78e7d26c60f916ba730b892bef038",
]
BASE_OPERATIONS = ["status", "neighborhood", "path", "page", "continue", "retrieve", "context"]
BASE_LEGACY = "e9cbbdfacb0097792a29c35a692f87dd00a8c709be4842c7aa1cde3afcef5bd7"
BASE_TOOLS = {
    "list_dir": "538fc1f24298c3ebc368b5c4d9f0f70742c52a6055e72a0bbe97c4c30fb54c3f",
    "memex_read": "06b333c55c7f1124fde72aff8ddbc2ff6b271891a1fae77ae23a5b18bb8b805a",
    "search_symbol": "248c29557bcd1db24b8f67097c6c7c3f749fd2b8971a56f497ef0f4c7ce9d95a",
    "search_text": "a1bdb103f2db5a2c445a5ace23153bbbdd952fc54a8d33dc9fa2461177a0601d",
    "test_dry_run": "52c6b3a91c44e9fbb2bbdc9c717849b0c38a4e9563d33bb923ebd59595340437",
    "view_file": "5334b4c033e2f7431f9c857ccf96b5013026eef26e92038a64963cb0d8854d2d",
}
BASE_TOOL_DESCRIPTORS = {
    "list_dir": "bdecc05da7de25c4bb205e439646d7fc74ed5f4ac8784354ec7af2c065cccf39",
    "memex_read": "d44534d733d4410d0cdb1ecf8f3bc2e270c7e0071aa5b8d9de6448b1676c9371",
    "search_symbol": "2c4b4485e048ed8b6100a60db0cd85f41613933dc692aa32601b655d22c28d51",
    "search_text": "91c682cc3ba40df7e923b63ec5c804cf05c9353a2d44a6696400a5dd400d90a1",
    "test_dry_run": "e7cc167b20c09a245c89b9a66192aaad77e888265f822ec1dbea63b48c8ad0bf",
    "view_file": "2f780c677962b3727fe33beacbcd2980b60265160388289c3d84e55d2d8041fd",
}


def test_schema_registration_is_strictly_additive(tmp_path):
    inputs = graph.GRAPH_INPUT_SCHEMA["oneOf"]
    # Every pre-existing member is byte-identical (canonical sha256 recorded on the base).
    assert [digest(m) for m in inputs[: len(BASE_INPUT)]] == BASE_INPUT
    assert len(inputs) == len(BASE_INPUT) + 1  # exactly one appended member
    assert inputs[-1] is IMPACT_INPUT
    assert digest(inputs[-2]) != digest(IMPACT_INPUT)
    anyof = graph.ENVELOPE_SCHEMA["properties"]["data"]["anyOf"]
    assert [digest(m) for m in anyof[: len(BASE_DATA)]] == BASE_DATA
    assert len(anyof) == len(BASE_DATA) + 1 and anyof[-1] is IMPACT_DATA
    one_of = graph.ENVELOPE_SCHEMA["oneOf"]
    assert [digest(m) for m in one_of[: len(BASE_ENVELOPE)]] == BASE_ENVELOPE
    assert len(one_of) == len(BASE_ENVELOPE) + 1
    assert one_of[-1]["properties"]["operation"] == {"const": "impact"}
    operations = graph.ENVELOPE_SCHEMA["properties"]["operation"]["enum"]
    assert operations == [*BASE_OPERATIONS, "impact"]
    assert digest(graph.LEGACY_OUTPUT_SCHEMA) == BASE_LEGACY
    # The first '"relation"' in the dumped output schema stays in the NAV hop.
    assert "jsx" not in json.dumps(graph.GRAPH_OUTPUT_SCHEMA).split('"relation"')[1][:200]


def test_envelope_constants_and_relations_are_unchanged():
    assert MINIMUM_BUDGET == 3436
    assert COLLECTION_MINIMUM_BUDGET == 3448
    assert FIXED_ERROR_ENVELOPE_CAP == 797
    assert (
        frozenset(
            {
                "call",
                "construct",
                "import",
                "superclass",
                "include",
                "extend",
                "prepend",
                "rationale_for",
            }
        )
        == ev.RELATIONS
    )
    assert LIMITS == {
        "max_visited_nodes": (100, 1, 1000),
        "max_examined_edges": (1000, 0, 10000),
        "max_depth": (3, 0, 32),
        "deadline_ms": (100, 0, 2000),
    }
    # The impact minimum is measured, deterministic and pinned.
    assert IMPACT_MINIMUM_BUDGET == 7730


def test_exactly_seven_read_only_tools_and_only_graph_query_changed(tmp_path):
    manifest = build_tool_manifest(Config(repo=tmp_path, memex_root=tmp_path / "memex"))
    names = {tool["name"] for tool in manifest}
    assert names == {
        "view_file",
        "list_dir",
        "search_text",
        "search_symbol",
        "graph_query",
        "test_dry_run",
        "memex_read",
    }
    assert len(READ_ONLY_TOOLS) == 7 and names == set(READ_ONLY_TOOLS)
    by_name = {tool["name"]: tool for tool in manifest}
    for name, expected in BASE_TOOL_DESCRIPTORS.items():
        assert digest(by_name[name]) == expected
        assert digest(by_name[name]["inputSchema"]) == BASE_TOOLS[name]
    # graph_query: the published input schema is the base list plus exactly one member.
    published = by_name["graph_query"]["inputSchema"]
    assert digest(published) == digest(graph.GRAPH_INPUT_SCHEMA)
    base_published = copy.deepcopy(published)
    appended = base_published["oneOf"].pop()
    assert digest(appended) == digest(IMPACT_INPUT)
    assert [digest(m) for m in base_published["oneOf"][: len(BASE_INPUT)]] == BASE_INPUT


def test_request_schema_names_no_path_field():
    properties = IMPACT_INPUT["properties"]
    assert not [k for k in properties if "path" in k or "root" in k or k in {"repo"}]
    assert IMPACT_INPUT["additionalProperties"] is False
    assert set(properties) >= {"expected_generation_id", "expected_before_generation_id"}
