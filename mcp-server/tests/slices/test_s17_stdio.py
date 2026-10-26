"""Real locked SDK version negotiation/result wire bounds and held S13 resources."""

import asyncio
import base64
import json
import os
import sys

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_s11_stdio import PROXY, initialize

from atlas_aci.codegraph import CodeGraph
from atlas_aci.protocol.results import FIXED_ERROR_ENVELOPE_CAP, result_bytes

SERVER = """import asyncio,sys,structlog
from pathlib import Path
from atlas_aci.config import Config
from atlas_aci.server import run_stdio
structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
asyncio.run(run_stdio(Config(repo=Path(sys.argv[1]),memex_root=Path(sys.argv[2]),max_calls_per_minute=0)))
"""


@pytest.mark.parametrize(
    "version", ["2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25", "2099-01-01"]
)
def test_native_retrieval_versions_and_old_page_resource(tmp_path, version):
    repo = tmp_path / "repo"
    repo.mkdir()
    original = (
        b"def A():\n "
        + b"B(); " * 20
        + b"\ndef B():\n pass\n# nightingale\n# task phrase caf\xc3\xa9\n"
    )
    (repo / "a.py").write_bytes(original)
    writer = CodeGraph(repo)
    writer.build()
    wire = tmp_path / "wire.jsonl"
    results = []
    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", PROXY, SERVER, str(wire), str(repo), str(tmp_path / "memex")],
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )

    async def exercise():
        with anyio.fail_after(30):
            async with stdio_client(params) as streams:
                async with ClientSession(*streams) as session:
                    initialized = await initialize(session, version)
                    actual = "2025-11-25" if version == "2099-01-01" else version
                    assert initialized.protocolVersion == actual
                    tools = await session.list_tools()
                    assert len(tools.tools) == 7
                    args = {
                        "schema_version": "atlas.graph.v1",
                        "op": "retrieve",
                        "text": "nightingale task",
                        "max_bytes": 10000,
                    }
                    first = await session.call_tool("graph_query", args)
                    assert not first.isError, first
                    results.append(first)
                    data = json.loads(first.content[0].text)
                    assert data["operation"] == "retrieve"
                    assert (first.structuredContent is not None) == (
                        actual in {"2025-06-18", "2025-11-25"}
                    )
                    candidate = data["data"]["candidates"][0]
                    assert (
                        candidate["source"]["source_sha256"]
                        == __import__("hashlib").sha256(original).hexdigest()
                    )
                    assert candidate["source"]["granularity"] == "file" and candidate["source"][
                        "end_byte"
                    ] == len(original)
                    page = await session.call_tool(
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
                    assert not page.isError, page
                    saved = json.loads(page.content[0].text)
                    assert saved["data"]["next_cursor"]
                    reference = saved["data"]["resources"][0]
                    (repo / "a.py").write_text("def Fresh():\n pass\n# new generation phrase\n")
                    writer.build()
                    resource = await session.read_resource(reference["uri"])
                    assert (
                        base64.b64decode(resource.contents[0].blob)
                        == original[reference["start_byte"] : reference["end_byte"]]
                    )
                    continued = await session.call_tool(
                        "graph_query",
                        {
                            "schema_version": "atlas.graph.v1",
                            "op": "continue",
                            "cursor": saved["data"]["next_cursor"],
                            "query_digest": saved["data"]["query_digest"],
                        },
                    )
                    assert (
                        not continued.isError
                        and json.loads(continued.content[0].text)["generation_id"]
                        == saved["generation_id"]
                    )
                    new = await session.call_tool(
                        "graph_query", {**args, "text": "new generation phrase"}
                    )
                    assert not new.isError
                    results.append(new)
                    assert json.loads(new.content[0].text)["generation_id"] != data["generation_id"]
                    error = await session.call_tool("graph_query", {**args, "max_vm_operations": 0})
                    results.append(error)
                    assert (
                        error.isError
                        and json.loads(error.content[0].text)["error"]["code"]
                        == "RETRIEVAL_BUDGET_EXCEEDED"
                    )
                    tiny = await session.call_tool("graph_query", {**args, "max_bytes": 1})
                    results.append(tiny)
                    assert (
                        tiny.isError
                        and json.loads(tiny.content[0].text)["error"]["code"] == "INVALID_BUDGET"
                    )
                    assert len(result_bytes(tiny)) <= FIXED_ERROR_ENVELOPE_CAP

    try:
        asyncio.run(exercise())
    finally:
        writer.close()
    frames = wire.read_bytes().splitlines()
    for result in results:
        assert any(b'"result":' + result_bytes(result) in frame for frame in frames)
        assert len(result_bytes(result)) <= max(10000, FIXED_ERROR_ENVELOPE_CAP)
