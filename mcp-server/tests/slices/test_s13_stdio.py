"""Actual locked SDK page/resource callbacks, native wire bytes and retained G1."""

import asyncio
import base64
import json
import os
import sys

import anyio
import pytest
from mcp import ClientSession, McpError, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from test_s11_stdio import PROXY, initialize

from atlas_aci.codegraph import CodeGraph
from atlas_aci.protocol.resources import FIXED_RESOURCE_ERROR_CAP, wire_bytes
from atlas_aci.protocol.results import result_bytes

SERVER = """import asyncio,sys,structlog
from pathlib import Path
from atlas_aci.config import Config
from atlas_aci.server import run_stdio
structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
asyncio.run(run_stdio(Config(repo=Path(sys.argv[1]),memex_root=Path(sys.argv[2]),max_response_bytes=int(sys.argv[3]),graph_lease_seconds=int(sys.argv[4]))))
"""


@pytest.mark.parametrize(
    "version", ["2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25", "2099-01-01"]
)
def test_real_sdk_pages_resources_and_g3(tmp_path, version):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = b"def A():\n " + b"B(); " * 20 + b"\ndef B():\n pass\n"
    (repo / "a.py").write_bytes(source)
    writer = CodeGraph(repo)
    writer.build()
    wire = tmp_path / "wire.jsonl"
    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", PROXY, SERVER, str(wire), str(repo), str(tmp_path / "memex"), "1048576", "300"],
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    results = []
    resource_results = []
    errors = []

    async def exercise():
        with anyio.fail_after(30):
            async with stdio_client(params) as streams:
                async with ClientSession(*streams) as session:
                    initialized = await initialize(session, version)
                    actual = "2025-11-25" if version == "2099-01-01" else version
                    assert initialized.protocolVersion == actual
                    assert (await session.list_resources()).resources == []
                    templates = (await session.list_resource_templates()).resourceTemplates
                    assert len(templates) == 1 and templates[0].uriTemplate.startswith(
                        "atlas-source://"
                    )
                    first = await session.call_tool(
                        "graph_query",
                        {
                            "schema_version": "atlas.graph.v1",
                            "op": "page",
                            "query": {
                                "schema_version": "atlas.graph.v1",
                                "op": "neighborhood",
                                "seed": {"name": "A"},
                                "max_depth": 1,
                                "deadline_ms": 2000,
                            },
                            "page_max_bytes": 18000,
                        },
                    )
                    assert not first.isError
                    results.append(first)
                    payload = json.loads(first.content[0].text)
                    data = payload["data"]
                    assert data["next_cursor"]
                    descriptor = data["resources"][0]
                    for index in (2, 3):
                        (repo / "a.py").write_text("def new" + str(index) + "():\n pass\n")
                        writer.build()
                    assert not (
                        writer.atlas_dir
                        / "source-snapshots"
                        / ("manifest." + payload["snapshot_id"] + ".json")
                    ).exists()
                    resource = await session.read_resource(descriptor["uri"])
                    resource_results.append(resource)
                    item = resource.contents[0]
                    assert isinstance(item, types.BlobResourceContents)
                    assert (
                        base64.b64decode(item.blob)
                        == source[descriptor["start_byte"] : descriptor["end_byte"]]
                    )
                    assert item.meta["generation_id"] == payload["generation_id"]
                    args = {
                        "schema_version": "atlas.graph.v1",
                        "op": "continue",
                        "cursor": data["next_cursor"],
                        "query_digest": data["query_digest"],
                    }
                    left, right = await asyncio.gather(
                        session.call_tool("graph_query", args),
                        session.call_tool("graph_query", args),
                    )
                    assert not left.isError and result_bytes(left) == result_bytes(right)
                    results.extend([left, right])
                    assert (
                        json.loads(left.content[0].text)["generation_id"]
                        == payload["generation_id"]
                    )
                    try:
                        await session.read_resource("atlas-source://" + "0" * 64 + "?invalid=1")
                    except McpError as error:
                        errors.append(error.error)
                    else:
                        raise AssertionError("Malformed resource accepted")
                    assert errors[-1].code == -32602
                    assert len(wire_bytes(errors[-1])) <= FIXED_RESOURCE_ERROR_CAP

    try:
        asyncio.run(exercise())
    finally:
        writer.close()
    frames = wire.read_bytes().splitlines()
    for result in results:
        assert any(b'"result":' + result_bytes(result) in frame for frame in frames)
        assert len(result_bytes(result)) <= 18000
    for result in resource_results:
        assert any(b'"result":' + wire_bytes(result) in frame for frame in frames)
        assert len(wire_bytes(result)) <= 8192
    for error in errors:
        assert any(b'"error":' + wire_bytes(error) in frame for frame in frames)
        assert not hasattr(error, "isError")
