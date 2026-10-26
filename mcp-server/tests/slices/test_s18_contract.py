"""Seven frozen context criteria through actual parser, SQLite and MCP SDK adapter."""

import asyncio
import json
from pathlib import Path

import pytest
from test_s18_atoms import BytesTokenizer, WordsTokenizer

from atlas_aci.codegraph import CodeGraph
from atlas_aci.config import Config
from atlas_aci.protocol.results import FIXED_ERROR_ENVELOPE_CAP, result_bytes
from atlas_aci.server import build_server_state, call_tool_result

ROOT = Path(__file__).resolve().parents[3]


def setup(tmp_path, tokenizer=None):
    corpus = json.loads((ROOT / "mcp-server/tests/fixtures/s18/useful-spans.v1.json").read_text())
    repo = tmp_path / "repo"
    repo.mkdir()
    for name, text in corpus["source_files"].items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    writer = CodeGraph(repo)
    writer.build()
    config = Config(
        repo=repo,
        memex_root=tmp_path / "memex",
        max_calls_per_minute=0,
        context_tokenizer=tokenizer,
    )
    enforcement, memex, factory = build_server_state(config)
    return writer, config, enforcement, memex, factory


async def invoke(state, *, version="2025-11-25", **options):
    _, config, enforcement, memex, factory = state
    return await call_tool_result(
        "graph_query",
        {"schema_version": "atlas.graph.v1", "op": "context", "text": "process_payment", **options},
        config,
        enforcement,
        memex,
        factory,
        protocol_version=version,
    )


def decoded(result):
    return json.loads(result.content[0].text)


def close(state):
    state[0].close()
    state[-1].close()


def test_complete_bundle(tmp_path):
    state = setup(tmp_path)
    try:
        result = asyncio.run(invoke(state))
        assert not result.isError, result
        data = decoded(result)["data"]
        relationships = [a for a in data["bundles"] if a["kind"] == "relationship"]
        assert relationships
        for atom in relationships:
            records = atom["records"]
            proof = records["proof"]
            assert len(records["nodes"]) == 2
            assert atom["sources"][0]["evidence_id"] == proof["evidence"]["id"]
            assert proof["assertion"]["evidence_id"] == proof["evidence"]["id"]
            assert proof["evidence"]["occurrence_id"] == proof["occurrence"]["id"]
            assert proof["evidence"]["producer_id"] == proof["producer"]["id"]
        assert data["bundles"][0]["kind"] == "definition"
    finally:
        close(state)


def test_full_envelope_budget(tmp_path):
    state = setup(tmp_path)
    try:
        for version in ("2024-11-05", "2025-11-25"):
            for budget in (1, 2000, 8000, 16000, 32000):
                result = asyncio.run(
                    invoke(state, version=version, max_bytes=budget, text="render_text")
                )
                body = decoded(result)
                if result.isError:
                    assert body["error"]["code"] == "INVALID_BUDGET", body
                    assert len(result_bytes(result)) <= FIXED_ERROR_ENVELOPE_CAP
                else:
                    assert len(result_bytes(result)) <= budget
                    assert (result.structuredContent is not None) == (version == "2025-11-25")
    finally:
        close(state)


@pytest.mark.parametrize("tokenizer", [BytesTokenizer(), WordsTokenizer()])
def test_declared_tokenizer(tmp_path, tokenizer):
    state = setup(tmp_path, tokenizer)
    try:
        for version in ("2024-11-05", "2025-11-25"):
            result = asyncio.run(invoke(state, version=version))
            assert not result.isError, result
            usage = decoded(result)["data"]["token_usage"]
            assert usage["mode"] == "exact" and usage["algorithm"] == tokenizer.algorithm_id
            assert usage["count"] == len(tokenizer.encode(result_bytes(result).decode()))
    finally:
        close(state)


def test_estimate_labeled(tmp_path):
    state = setup(tmp_path)
    try:
        result = asyncio.run(invoke(state))
        assert not result.isError, result
        usage = decoded(result)["data"]["token_usage"]
        assert usage["mode"] == "estimated" and usage["algorithm"] == "byte_quarter"
        assert usage["count"] == (len(result_bytes(result)) + 3) // 4
    finally:
        close(state)


def test_budget_too_small(tmp_path):
    state = setup(tmp_path)
    try:
        for budget in (0, 1):
            result = asyncio.run(invoke(state, max_bytes=budget))
            assert result.isError and decoded(result)["error"]["code"] == "INVALID_BUDGET"
            assert len(result_bytes(result)) <= FIXED_ERROR_ENVELOPE_CAP
    finally:
        close(state)


def test_context_replay(tmp_path):
    state = setup(tmp_path)
    try:
        first = asyncio.run(invoke(state))
        assert not first.isError, first
        path = state[0].repo / "app/payments.py"
        path.write_text(path.read_text() + "# live-only bytes\n")
        second = asyncio.run(invoke(state))
        assert not second.isError, second
        assert decoded(first) == decoded(second)
    finally:
        close(state)


def test_retrieval_record_identity(tmp_path):
    state = setup(tmp_path)
    try:
        first, same = asyncio.run(invoke(state)), asyncio.run(invoke(state))
        assert not first.isError and not same.isError
        before = decoded(first)["data"]["retrieval_record_id"]
        assert decoded(same)["data"]["retrieval_record_id"] == before
        different = asyncio.run(invoke(state, source_policy="references"))
        assert not different.isError and decoded(different)["data"]["retrieval_record_id"] != before
        path = state[0].repo / "app/payments.py"
        path.write_text(path.read_text() + "# new indexed source bytes\n")
        state[0].build()
        changed = asyncio.run(invoke(state))
        assert not changed.isError and decoded(changed)["data"]["retrieval_record_id"] != before
    finally:
        close(state)


def test_independent_context_membership_and_shared_work_contradictions(tmp_path):
    import copy

    from atlas_aci.protocol.context import validate_context
    from atlas_aci.query.leases import digest

    state = setup(tmp_path)
    try:
        result = asyncio.run(invoke(state))
        assert not result.isError
        original = decoded(result)
        validate_context(original, "context", 200)
        for mutation in ("source", "work", "disconnected_path"):
            forged = copy.deepcopy(original)
            data = forged["data"]
            if mutation == "source":
                atom = data["bundles"][0]
                atom["sources"][0]["source_sha256"] = "0" * 64
                source = atom["sources"][0]
                source["id"] = digest(
                    {k: v for k, v in source.items() if k not in {"id", "inline", "inline_reason"}}
                )
                atom["id"] = digest({k: v for k, v in atom.items() if k not in {"id", "rank"}})
            elif mutation == "work":
                data["work"]["examined_edges"] = data["effective_limits"]["max_examined_edges"] + 1
            else:
                # A whole path orders its nodes; hashes cannot repair reversal.
                path_result = asyncio.run(invoke(state, target={"name": "validate_payment"}))
                assert not path_result.isError
                forged = copy.deepcopy(decoded(path_result))
                data = forged["data"]
                atom = next(a for a in data["bundles"] if a["kind"] == "path")
                atom["records"]["nodes"].reverse()
                atom["id"] = digest({k: v for k, v in atom.items() if k not in {"id", "rank"}})
            domain = {
                k: v
                for k, v in data.items()
                if k
                in {
                    "format",
                    "request",
                    "repository",
                    "generation_id",
                    "snapshot_id",
                    "ranker",
                    "tokenizer",
                    "bundles",
                    "effective_limits",
                    "coverage",
                    "retrieval",
                    "frontiers",
                    "work",
                    "collection_stop",
                    "selected_range",
                    "selection",
                    "source_resource_ids",
                }
            }
            data["retrieval_record_id"] = digest(domain)
            with pytest.raises(ValueError):
                validate_context(forged, "context", 200)
    finally:
        close(state)


@pytest.mark.parametrize("continuation", [False, True])
def test_actual_recursive_call_keeps_two_endpoint_slots_of_one_identity(tmp_path, continuation):
    state = setup(tmp_path)
    path = state[0].repo / "app/recursive.py"
    path.write_text("def Recursive():\n Recursive()\n")
    state[0].build()
    try:
        result = asyncio.run(
            invoke(
                state,
                text="Recursive",
                source_policy="references",
                max_bytes=100000,
                continuation=continuation,
            )
        )
        assert not result.isError, result
        relationships = [
            atom for atom in decoded(result)["data"]["bundles"] if atom["kind"] == "relationship"
        ]
        assert len(relationships) == 1
        pair = relationships[0]["records"]["nodes"]
        assert len(pair) == 2 and pair[0]["id"] == pair[1]["id"]
    finally:
        close(state)


def test_same_length_forged_whole_file_inline_cannot_hide_behind_record_rehash(tmp_path):
    import copy

    from atlas_aci.protocol.context import validate_context
    from atlas_aci.query.leases import digest

    state = setup(tmp_path)
    try:
        result = asyncio.run(invoke(state, text="retry transport timeout", max_bytes=100000))
        assert not result.isError
        forged = copy.deepcopy(decoded(result))
        data = forged["data"]
        changed = 0
        for atom in data["bundles"]:
            for source in atom["sources"]:
                if (
                    source["inline"] is not None
                    and source["start_byte"] == 0
                    and source["end_byte"] == source["source_size"]
                ):
                    assert source["inline"]["encoding"] == "utf8"
                    source["inline"]["data"] = "X" * len(source["inline"]["data"].encode())
                    changed += 1
            atom["id"] = digest({k: v for k, v in atom.items() if k not in {"id", "rank"}})
        assert changed > 0
        domain = {
            k: v
            for k, v in data.items()
            if k
            in {
                "format",
                "request",
                "repository",
                "generation_id",
                "snapshot_id",
                "ranker",
                "tokenizer",
                "bundles",
                "effective_limits",
                "coverage",
                "retrieval",
                "frontiers",
                "work",
                "collection_stop",
                "selected_range",
                "selection",
                "source_resource_ids",
            }
        }
        data["retrieval_record_id"] = digest(domain)
        with pytest.raises(ValueError, match="inline source hash"):
            validate_context(forged, "context", 200)
    finally:
        close(state)


def test_real_non_utf8_source_uses_complete_base64_and_full_file_hash(tmp_path):
    import base64
    import hashlib

    state = setup(tmp_path)
    original = b"def Binary():\n # comment \xff\n pass"
    (state[0].repo / "app/binary.py").write_bytes(original)
    state[0].build()
    try:
        result = asyncio.run(invoke(state, text="Binary", max_bytes=100000))
        assert not result.isError, result
        sources = [s for atom in decoded(result)["data"]["bundles"] for s in atom["sources"]]
        binary = [
            s for s in sources if s.get("path") == "app/binary.py" and s["inline"] is not None
        ]
        assert binary
        whole = next(s for s in binary if s["start_byte"] == 0 and s["end_byte"] == len(original))
        assert whole["inline"]["encoding"] == "base64"
        assert base64.b64decode(whole["inline"]["data"], validate=True) == original
        assert whole["source_sha256"] == hashlib.sha256(original).hexdigest()
    finally:
        close(state)


def test_genuine_old8_and_absent_index_never_reacquire_or_upgrade(tmp_path):
    import hashlib

    from test_s17_storage import old8

    graph, fixture = old8(tmp_path / "legacy")
    config = Config(
        repo=graph.repo,
        graph_root=tmp_path / "legacy/graphs",
        memex_root=tmp_path / "legacy-memex",
        max_calls_per_minute=0,
    )
    enforcement, memex, factory = build_server_state(config)
    original = {
        str(path.relative_to(graph.atlas_dir)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in graph.atlas_dir.rglob("*")
        if path.is_file()
    }
    legacy = graph, config, enforcement, memex, factory

    async def run():
        for continuation in (False, True):
            result = await invoke(legacy, text="A", continuation=continuation, max_bytes=100000)
            assert result.isError and decoded(result)["error"]["code"] == "INDEX_UPGRADE_REQUIRED"
        if hasattr(factory, "_lease_registry"):
            assert (
                factory._lease_registry.usage()["pending"]
                == factory._lease_registry.usage()["active"]
                == 0
            )
            await factory._lease_registry.aclose()
        assert not factory.db_path.exists()
        with graph.reader_lease() as reader:
            assert (
                reader.generation_id == fixture["generation_id"] and reader._lease_schema_epoch == 8
            )

    try:
        asyncio.run(run())
        assert original == {
            str(path.relative_to(graph.atlas_dir)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in graph.atlas_dir.rglob("*")
            if path.is_file()
        }
    finally:
        close(legacy)
    empty = tmp_path / "empty"
    empty.mkdir()
    config = Config(repo=empty, memex_root=tmp_path / "empty-memex", max_calls_per_minute=0)
    enforcement, memex, factory = build_server_state(config)
    try:
        result = asyncio.run(
            call_tool_result(
                "graph_query",
                {"schema_version": "atlas.graph.v1", "op": "context", "text": "A"},
                config,
                enforcement,
                memex,
                factory,
            )
        )
        assert result.isError and decoded(result)["error"]["code"] == "INDEX_UNAVAILABLE"
        assert not (empty / ".atlas").exists()
    finally:
        factory.close()


def test_preexisting_status_four_argument_dispatch_remains_unchanged(tmp_path, monkeypatch):
    from atlas_aci import server

    state = setup(tmp_path)
    original = server.graph_query
    entered = []

    async def status_boundary(arguments, config, enforcement, reader):
        entered.append(reader)
        return await original(arguments, config, enforcement, reader)

    monkeypatch.setattr(server, "graph_query", status_boundary)
    try:
        result = asyncio.run(
            call_tool_result(
                "graph_query",
                {"schema_version": "atlas.graph.v1", "op": "status"},
                state[1],
                state[2],
                state[3],
                state[4],
            )
        )
        assert not result.isError and len(entered) == 1
        assert entered[0]._closed
    finally:
        close(state)
