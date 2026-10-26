"""Actual returned proof resources, SDK metadata/base64 and bounded verified reads."""

import asyncio
import base64
import os
from urllib.parse import parse_qs, urlsplit

import pytest
from mcp import McpError, types

from atlas_aci.codegraph import CodeGraph
from atlas_aci.config import Config
from atlas_aci.enforcement import Enforcement
from atlas_aci.protocol.resources import next_uri, read_resource, wire_bytes
from atlas_aci.query.leases import LeaseRegistry
from atlas_aci.query.paging import issue_pages


def test_actual_long_comment_resource_bytes_budget_and_metadata(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    data = b"def A():\n # NOTE: " + b"x" * 150000 + b"\xc3\xa9Z\n pass\n"
    (repo / "a.py").write_bytes(data)
    graph = CodeGraph(repo)
    graph.build()
    config = Config(repo=repo, memex_root=tmp_path / "memex", max_calls_per_minute=0)
    registry = LeaseRegistry(config, graph)
    enforcement = Enforcement(config)

    async def exercise():
        page = await issue_pages(
            registry,
            graph,
            {
                "query": {
                    "op": "neighborhood",
                    "seed": {"name": "A"},
                    "relations": ["rationale_for"],
                    "direction": "incoming",
                    "deadline_ms": 2000,
                },
                "page_max_bytes": 1024 * 1024,
            },
        )
        descriptor = page["data"]["resources"][0]
        parts = urlsplit(descriptor["uri"])
        token = parse_qs(parts.query)["token"][0]
        budget = 6000
        uri = next_uri(descriptor["id"], token, 0, budget)
        calls = []
        pread = os.pread

        def observed(fd, count, offset):
            calls.append(count)
            return pread(fd, count, offset)

        monkeypatch.setattr(os, "pread", observed)
        content = (await read_resource(registry, uri, enforcement))[0]
        assert content.meta is not None
        actual = types.ReadResourceResult(
            contents=[
                types.BlobResourceContents(
                    uri=uri,
                    mimeType=content.mime_type,
                    blob=base64.b64encode(content.content).decode(),
                    _meta=content.meta,
                )
            ]
        )
        assert len(wire_bytes(actual)) <= budget
        start = descriptor["start_byte"]
        assert content.content == data[start : start + len(content.content)]
        assert content.meta["next_uri"] and not content.meta["complete"]
        assert sum(calls) <= len(content.content) + 2 * 65536
        assert descriptor["end_byte"] - start > budget
        for tiny in (0, 1):
            with pytest.raises(McpError):
                await read_resource(
                    registry, next_uri(descriptor["id"], token, 0, tiny), enforcement
                )
        await registry.aclose()

    try:
        asyncio.run(exercise())
    finally:
        graph.close()
