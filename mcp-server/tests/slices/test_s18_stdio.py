"""Real SDK negotiation and exact final context bytes, not a simulated transport."""

import asyncio
import base64
import hashlib
import json
import os
import sys

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_s11_stdio import PROXY, initialize
from test_s18_contract import close, setup

from atlas_aci.protocol.results import result_bytes

SERVER = """import asyncio,sys,structlog
from pathlib import Path
from atlas_aci.config import Config
from atlas_aci.server import run_stdio
structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
class LocalBytes:
 algorithm_id='local_utf8_bytes'
 version='1'
 def encode(self,text): return text.encode('utf-8')
asyncio.run(run_stdio(Config(repo=Path(sys.argv[1]),memex_root=Path(sys.argv[2]),max_calls_per_minute=0,context_tokenizer=LocalBytes())))
"""


@pytest.mark.parametrize(
    "version", ["2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25", "2099-01-01"]
)
def test_real_context_sdk_versions_owned_replay_and_changed_publication(tmp_path, version):
    state = setup(tmp_path)
    repo = state[0].repo
    wire = tmp_path / "wire.jsonl"
    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", PROXY, SERVER, str(wire), str(repo), str(tmp_path / "native-memex")],
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    results = []

    async def run():
        with anyio.fail_after(30):
            async with stdio_client(params) as streams:
                async with ClientSession(*streams) as session:
                    negotiated = await initialize(session, version)
                    actual = "2025-11-25" if version == "2099-01-01" else version
                    assert negotiated.protocolVersion == actual
                    first = await session.call_tool(
                        "graph_query",
                        {
                            "schema_version": "atlas.graph.v1",
                            "op": "context",
                            "text": "process_payment",
                            "continuation": True,
                            "max_bytes": 40000,
                        },
                    )
                    assert not first.isError, first
                    results.append(first)
                    data = json.loads(first.content[0].text)["data"]
                    assert data["bundles"] and data["has_more"] and data["resources"]
                    assert (first.structuredContent is not None) == (
                        actual in {"2025-06-18", "2025-11-25"}
                    )
                    resource = data["resources"][0]
                    old = await session.read_resource(resource["uri"])
                    raw = base64.b64decode(old.contents[0].blob)
                    assert (
                        raw
                        == (repo / resource["path"]).read_bytes()[
                            resource["start_byte"] : resource["end_byte"]
                        ]
                    )
                    for generation in ("G2", "G3"):
                        path = repo / "app/payments.py"
                        path.write_text("def " + generation + "():\n pass\n")
                        state[0].build()
                    latest = await session.call_tool(
                        "graph_query",
                        {
                            "schema_version": "atlas.graph.v1",
                            "op": "context",
                            "text": "G3",
                            "max_bytes": 40000,
                        },
                    )
                    assert not latest.isError, latest
                    results.append(latest)
                    assert (
                        json.loads(latest.content[0].text)["generation_id"] != data["generation_id"]
                    )
                    again = await session.read_resource(resource["uri"])
                    assert base64.b64decode(again.contents[0].blob) == raw
                    continuation = {
                        "schema_version": "atlas.graph.v1",
                        "op": "continue",
                        "cursor": data["next_cursor"],
                        "query_digest": data["query_digest"],
                    }
                    second = await session.call_tool("graph_query", continuation)
                    replay = await session.call_tool("graph_query", continuation)
                    assert not second.isError and result_bytes(second) == result_bytes(replay)
                    results.extend([second, replay])
                    for result in results:
                        assert len(result_bytes(result)) <= 40000
                        body = json.loads(result.content[0].text)
                        assert body["data"]["token_usage"]["count"] == len(result_bytes(result))
                    bad = await session.call_tool(
                        "graph_query",
                        {
                            "schema_version": "atlas.graph.v1",
                            "op": "context",
                            "text": "G3",
                            "max_bytes": 1,
                        },
                    )
                    assert (
                        bad.isError
                        and json.loads(bad.content[0].text)["error"]["code"] == "INVALID_BUDGET"
                    )

    try:
        asyncio.run(run())
        frames = wire.read_bytes().splitlines()
        for result in results:
            assert any(b'"result":' + result_bytes(result) in frame for frame in frames)
        print(
            json.dumps(
                {
                    "protocol": version,
                    "wire_sha256": hashlib.sha256(wire.read_bytes()).hexdigest(),
                    "actual_sdk_result_bytes": [len(result_bytes(r)) for r in results],
                }
            )
        )
    finally:
        close(state)
