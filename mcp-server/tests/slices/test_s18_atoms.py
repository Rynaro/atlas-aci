"""Actual canonical bundle integrity and final negotiated SDK token bytes."""

import asyncio
import copy
import json

import pytest

from atlas_aci.codegraph import CodeGraph
from atlas_aci.enforcement import ToolError
from atlas_aci.protocol.results import result_bytes, sdk_result
from atlas_aci.query import context_bundles as bundles
from atlas_aci.query.context_admission import prefix, reserved_payload
from atlas_aci.query.context_tokens import account
from atlas_aci.query.context_work import ContextWork
from atlas_aci.query.retrieval import retrieve
from atlas_aci.query.traversal import navigate


@pytest.fixture
def graph(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text(
        'def A():\n B(); B()\ndef B():\n return "café ☃"\n# unique task phrase\n'
    )
    graph = CodeGraph(repo)
    graph.build()
    try:
        yield graph
    finally:
        graph.close()


def atoms(reader):
    work = ContextWork()
    candidate = asyncio.run(retrieve(reader, text="A", accounting=work))["candidates"][0]
    definition = bundles.definition(reader, candidate, work, 0)
    nav = asyncio.run(
        navigate(reader, seed={"name": "A"}, max_depth=1, deadline_ms=2000, accounting=work)
    )
    nodes = {n["id"]: n for n in nav["nodes"]}
    relationships = [
        bundles.relationship(
            reader,
            hop,
            [nodes[hop["assertion"][key]] for key in ("source_entity_id", "target_entity_id")],
            work,
            tier="exact_name",
            seed_rank=0,
        )
        for hop in nav["edges"]
    ]
    path = asyncio.run(
        navigate(
            reader,
            operation="path",
            seed={"name": "A"},
            target={"name": "B"},
            deadline_ms=2000,
            accounting=work,
        )
    )["path"]
    whole = bundles.whole_path(reader, path, work, tier="exact_name", seed_rank=0)
    file = asyncio.run(retrieve(reader, text="unique task phrase", accounting=work))["candidates"][
        0
    ]
    lexical = bundles.lexical_file(reader, file, work, 1)
    return [whole, definition, *relationships, lexical], work


def payload(items, more=False):
    return {
        "schema_version": "atlas.graph.v1",
        "operation": "context",
        "status": "ok",
        "data": {"bundles": items, "has_more": more, "next_cursor": None, "resources": []},
    }


def test_real_complete_definition_path_parallel_and_file_membership(graph):
    with graph.reader_lease() as reader:
        values, work = atoms(reader)
        path, definition, first, second, file = values
        assert (
            definition["records"]["producer"]
            and definition["sources"][0]["entity_id"] == definition["records"]["entity"]["id"]
        )
        assert len(path["records"]["nodes"]) == 2 and len(path["records"]["hops"]) == 1
        assert first["id"] != second["id"]
        assert first["sources"][0]["evidence_id"] == first["records"]["proof"]["evidence"]["id"]
        assert file["kind"] == "lexical_file" and "evidence_id" not in file["sources"][0]
        assert file["records"]["document_id"] == file["sources"][0]["document_id"]
        inline = bundles.inline_sources(
            [definition], reader, work, policy="inline_if_complete", max_inline_bytes=8192
        )
        source = inline[0]["sources"][0]
        raw = reader.read_source(source["path"], source["source_sha256"])
        assert source["inline"]["data"].encode() == raw[source["start_byte"] : source["end_byte"]]
        refs = bundles.inline_sources(
            [definition], reader, work, policy="references", max_inline_bytes=8192
        )
        assert (
            refs[0]["sources"][0]["inline"] is None
            and refs[0]["sources"][0]["inline_reason"] == "references_policy"
        )


def test_whole_path_prefix_and_list_caps_never_clip_or_skip(graph):
    with graph.reader_lease() as reader:
        values, _ = atoms(reader)
        path, definition = values[:2]
        size = len(
            result_bytes(
                sdk_result(
                    reserved_payload(payload([path], True), None), error=False, structured=True
                )
            )
        )
        selected, reason = prefix(
            [path, definition],
            payload,
            budget=size - 1,
            structured=True,
            element_limit=100,
            tokenizer=None,
        )
        assert selected == [] and reason == "byte_limit"
        selected, reason = prefix(
            [path], payload, budget=100000, structured=True, element_limit=1, tokenizer=None
        )
        assert selected == [] and reason == "element_limit"
        assert len(path["records"]["hops"]) == 1 and path["records"]["hops"][0]["producer"]


class BytesTokenizer:
    algorithm_id = "local_utf8_bytes"
    version = "1"

    def encode(self, text):
        return text.encode("utf-8")


class WordsTokenizer:
    algorithm_id = "local_whitespace"
    version = "1"

    def encode(self, text):
        return text.split()


@pytest.mark.parametrize("structured", [False, True])
def test_actual_emitted_sdk_fixed_point_two_declared_algorithms_and_estimate(graph, structured):
    with graph.reader_lease() as reader:
        values, _ = atoms(reader)
        original = payload(values[:1])
        before = copy.deepcopy(original)
        for tokenizer in (BytesTokenizer(), WordsTokenizer(), None):
            work = ContextWork()
            counted = account(original, tokenizer=tokenizer, structured=structured, work=work)
            raw = result_bytes(sdk_result(counted, error=False, structured=structured))
            usage = counted["data"]["token_usage"]
            expected = (
                (len(raw) + 3) // 4 if tokenizer is None else len(tokenizer.encode(raw.decode()))
            )
            assert usage["count"] == expected
            assert usage["mode"] == ("estimated" if tokenizer is None else "exact")
            assert 1 <= work.counts["tokenizer_calls"] <= 16
        assert original == before and "token_usage" not in original["data"]


def test_real_configured_self_reference_cycle_and_exception_fail_closed():
    class Cycle(BytesTokenizer):
        def encode(self, text):
            reported = json.loads(json.loads(text)["content"][0]["text"])["data"]["token_usage"][
                "count"
            ]
            return range(1 if reported == 0 else 0)

    class Never(BytesTokenizer):
        def encode(self, text):
            reported = json.loads(json.loads(text)["content"][0]["text"])["data"]["token_usage"][
                "count"
            ]
            return range(reported + 1)

    class Broken(BytesTokenizer):
        def encode(self, text):
            raise RuntimeError("controlled local tokenizer failure")

    for tokenizer in (Cycle(), Never(), Broken()):
        work = ContextWork()
        with pytest.raises(ToolError, match="Declared token accounting failed"):
            account(payload([]), tokenizer=tokenizer, structured=True, work=work)
        assert work.counts["tokenizer_calls"] <= 16


def test_frozen_exact_definition_anchor_even_when_file_candidate_ranks_first(tmp_path):
    from atlas_aci.query.context_admission import ranked

    repo = tmp_path / "repo"
    repo.mkdir()
    for index in range(8):
        (repo / f"a{index}.py").write_text(f"def A{index}():\n pass\n")
    graph = CodeGraph(repo)
    graph.build()
    try:
        with graph.reader_lease() as reader:
            choices = None
            for index in range(8):
                candidates = asyncio.run(retrieve(reader, text=f"a{index}.py"))["candidates"]
                if candidates[0]["kind"] == "source_file":
                    choices = candidates
                    break
            assert choices is not None, "fixture must expose a file-first exact-path tier"
            work = ContextWork()
            values = [
                bundles.lexical_file(reader, candidate, work, position)
                if candidate["kind"] == "source_file"
                else bundles.definition(reader, candidate, work, position)
                for position, candidate in enumerate(choices)
            ]
            assert values[0]["kind"] == "lexical_file" and values[0]["rank"]["tier"] == "exact_path"
            assert any(a["kind"] == "definition" for a in values)
            assert ranked(values)[0]["kind"] == "definition"
    finally:
        graph.close()


def test_final_canonical_saved_page_key_order_is_in_the_token_domain(graph):
    from atlas_aci.query.leases import canonical_bytes

    class Position(BytesTokenizer):
        algorithm_id = "local_last_usage_position"

        def encode(self, text):
            return range(text.rfind('"token_usage"'))

    with graph.reader_lease() as reader:
        values, _ = atoms(reader)
        original = payload(values[:1])
        original["data"]["z_final_metadata"] = "escaped café metadata"
        tokenizer = Position()
        counted = account(original, tokenizer=tokenizer, structured=True)
        saved = json.loads(canonical_bytes(counted))
        emitted = result_bytes(sdk_result(saved, error=False, structured=True))
        assert saved["data"]["token_usage"]["count"] == len(tokenizer.encode(emitted.decode()))


def test_outer_bundle_array_cap_is_admitted_whole(graph):
    with graph.reader_lease() as reader:
        values, _ = atoms(reader)
        definition = values[1]
        # A definition has no multi-element proof arrays; the outer list is the limit.
        selected, reason = prefix(
            [definition, copy.deepcopy(definition)],
            payload,
            budget=100000,
            structured=True,
            element_limit=1,
            tokenizer=None,
        )
        assert len(selected) == 1 and reason == "element_limit"


def test_disconnected_supplied_definition_occurrence_cannot_hide_as_unavailable(graph):
    with graph.reader_lease() as reader:
        a = asyncio.run(retrieve(reader, text="A"))["candidates"][0]
        b = asyncio.run(retrieve(reader, text="B"))["candidates"][0]
        assert a["entity"]["id"] != b["entity"]["id"]
        forged = copy.deepcopy(a)
        forged["occurrence"] = b["occurrence"]
        forged["evidence"] = None
        with pytest.raises(ValueError):
            bundles.definition(reader, forged, ContextWork(), 0)
