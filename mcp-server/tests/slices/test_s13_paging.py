"""Real bounded result spooling, whole atoms and retained source membership."""

import asyncio
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from atlas_aci.codegraph import CodeGraph
from atlas_aci.config import Config
from atlas_aci.enforcement import ToolError
from atlas_aci.protocol.results import result_bytes, sdk_result
from atlas_aci.query.leases import LeaseRegistry
from atlas_aci.query.paging import issue_pages


def setup(tmp_path, **policy):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = b"def A():\n " + b"B(); " * 20 + b"\ndef B():\n pass\n"
    (repo / "a.py").write_bytes(source)
    graph = CodeGraph(repo)
    graph.build()
    config = Config(repo=repo, memex_root=tmp_path / "memex", **policy)
    return graph, source, LeaseRegistry(config, graph)


def test_pages_deliver_all_captured_proofs_g1_after_g3(tmp_path):
    graph, source, registry = setup(tmp_path)

    async def exercise():
        first = await issue_pages(
            registry,
            graph,
            {
                "query": {
                    "op": "neighborhood",
                    "seed": {"name": "A"},
                    "max_depth": 1,
                    "deadline_ms": 2000,
                },
                "page_max_bytes": 18000,
            },
        )
        assert first["data"]["next_cursor"] and first["data"]["total_count"] == 20
        generation = first["generation_id"]
        snapshot = first["snapshot_id"]
        for version in (2, 3):
            (graph.repo / "a.py").write_text("def other" + str(version) + "():\n pass\n")
            graph.build()
        assert not (
            graph.atlas_dir / "source-snapshots" / ("manifest." + snapshot + ".json")
        ).exists()
        pages = [first]
        while pages[-1]["data"]["next_cursor"]:
            prior = pages[-1]["data"]
            token = prior["next_cursor"]
            left, right = await asyncio.gather(
                registry.page(token, prior["query_digest"]),
                registry.page(token, prior["query_digest"]),
            )
            assert left == right
            pages.append(json.loads(left))
        atoms = [a for page in pages for a in page["data"]["items"]]
        assert len(atoms) == 20 and len({a["proof"]["assertion"]["id"] for a in atoms}) == 20
        assert all(p["generation_id"] == generation for p in pages)
        assert all(p["data"]["captured"] == first["data"]["captured"] for p in pages)
        assert first["data"]["captured"]["stop_reason"] == "depth_limit"
        assert all(
            len(result_bytes(sdk_result(p, error=False, structured=True))) <= 18000 for p in pages
        )
        descriptor = pages[-1]["data"]["resources"][0]
        token = parse_qs(urlsplit(descriptor["uri"]).query)["token"][0]
        lease, identifier = registry.authenticate(token, "resource")
        stored = json.loads(lease.resources[identifier])
        actual = lease.reader._source_lease.read_span(
            stored["path"],
            stored["source_sha256"],
            stored["start_byte"],
            stored["end_byte"],
            0,
            10000,
        )
        assert actual == source[stored["start_byte"] : stored["end_byte"]]
        await registry.aclose()

    try:
        asyncio.run(exercise())
    finally:
        graph.close()


def test_indivisible_path_or_metadata_rejects_before_issuance(tmp_path):
    graph, _source, registry = setup(tmp_path)

    async def exercise():
        with pytest.raises(ToolError, match="Page"):
            await issue_pages(
                registry,
                graph,
                {
                    "query": {
                        "op": "path",
                        "seed": {"name": "A"},
                        "target": {"name": "B"},
                        "deadline_ms": 2000,
                    },
                    "page_max_bytes": 1000,
                },
            )
        assert registry.usage()["active"] == registry.usage()["pending"] == 0
        await registry.aclose()

    try:
        asyncio.run(exercise())
    finally:
        graph.close()


def test_terminal_collection_limit_is_not_page_exhaustion(tmp_path):
    graph, _source, registry = setup(tmp_path)

    async def exercise():
        first = await issue_pages(
            registry,
            graph,
            {
                "query": {
                    "op": "neighborhood",
                    "seed": {"name": "A"},
                    "max_bytes": 10000,
                    "deadline_ms": 2000,
                },
                "page_max_bytes": 18000,
            },
        )
        data = first["data"]
        assert data["captured"]["outcome"] == "partial"
        assert data["captured"]["stop_reason"] == "retained_collection_limit"
        assert data["total_count"] > 0 and data["total_count"] < 20
        seen = len(data["items"])
        while data["next_cursor"]:
            data = json.loads(await registry.page(data["next_cursor"], data["query_digest"]))[
                "data"
            ]
            assert data["captured"] == first["data"]["captured"]
            seen += len(data["items"])
        assert seen == first["data"]["total_count"] and not data["has_more"]
        await registry.aclose()

    try:
        asyncio.run(exercise())
    finally:
        graph.close()


def test_concatenation_matches_independent_full_captured_oracle(tmp_path):
    from atlas_aci.query.traversal import navigate

    graph, _source, registry = setup(tmp_path)

    async def exercise():
        with graph.reader_lease() as reader:
            oracle = await navigate(reader, seed={"name": "A"}, max_depth=1, deadline_ms=2000)
        first = await issue_pages(
            registry,
            graph,
            {
                "query": {
                    "op": "neighborhood",
                    "seed": {"name": "A"},
                    "max_depth": 1,
                    "deadline_ms": 2000,
                },
                "page_max_bytes": 18000,
            },
        )
        data = first["data"]
        atoms = list(data["items"])
        while data["next_cursor"]:
            data = json.loads(await registry.page(data["next_cursor"], data["query_digest"]))[
                "data"
            ]
            atoms.extend(data["items"])
        assert [a["proof"] for a in atoms] == oracle["edges"]
        expected = {k: v for k, v in oracle.items() if k not in {"nodes", "edges"}}
        assert {k: v for k, v in data["captured"].items() if k != "query_operation"} == expected
        nodes = {n["id"]: n for n in oracle["nodes"]}
        for atom in atoms:
            assert atom["nodes"] == [
                nodes[atom["proof"]["assertion"][k]]
                for k in ("source_entity_id", "target_entity_id")
            ]
        await registry.aclose()

    try:
        asyncio.run(exercise())
    finally:
        graph.close()
