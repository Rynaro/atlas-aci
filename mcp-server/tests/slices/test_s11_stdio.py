"""Actual locked SDK sessions and exact stdout tool-result byte measurements."""

import asyncio
import json
import os
import sys

import anyio
import pytest
from jsonschema import Draft202012Validator
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

from atlas_aci.codegraph import CodeGraph
from atlas_aci.protocol.graph import GRAPH_OUTPUT_SCHEMA
from atlas_aci.protocol.results import FIXED_ERROR_ENVELOPE_CAP, result_bytes

SERVER = """import asyncio,sys,structlog
from pathlib import Path
from atlas_aci.config import Config
from atlas_aci.server import run_stdio
structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
asyncio.run(run_stdio(Config(repo=Path(sys.argv[1]),memex_root=Path(sys.argv[2]),max_response_bytes=int(sys.argv[3]))))
"""
PROXY = """import os,signal,subprocess,sys,threading
p=subprocess.Popen([sys.executable,'-c',sys.argv[1],*sys.argv[3:]],stdin=subprocess.PIPE,stdout=subprocess.PIPE)
def shutdown(signum,frame):
 p.terminate()
 try:p.wait(timeout=3)
 except subprocess.TimeoutExpired:p.kill();p.wait()
 raise SystemExit(128+signum)
signal.signal(signal.SIGTERM,shutdown)
def forward():
 try:
  while data:=os.read(sys.stdin.fileno(),65536):p.stdin.write(data);p.stdin.flush()
 finally:p.stdin.close()
threading.Thread(target=forward,daemon=True).start()
with open(sys.argv[2],'wb') as log:
 for line in p.stdout:
  log.write(line);log.flush();sys.stdout.buffer.write(line);sys.stdout.buffer.flush()
raise SystemExit(p.wait())
"""


async def initialize(session, requested):
    result = await session.send_request(
        types.ClientRequest(
            types.InitializeRequest(
                params=types.InitializeRequestParams(
                    protocolVersion=requested,
                    capabilities=types.ClientCapabilities(),
                    clientInfo=types.Implementation(name="same-client-every-version", version="1"),
                )
            )
        ),
        types.InitializeResult,
    )
    await session.send_notification(types.ClientNotification(types.InitializedNotification()))
    return result


def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    (path / "a.py").write_text("def target():\n return 1\ndef caller():\n target()\n")
    g = CodeGraph(path)
    g.build()
    g.close()
    return path


async def session_calls(tmp_path, path, version, budget, arguments):
    wire = tmp_path / "wire.jsonl"
    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", PROXY, SERVER, str(wire), str(path), str(tmp_path / "memex"), str(budget)],
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    with anyio.fail_after(30):
        async with stdio_client(params) as streams:
            async with ClientSession(*streams) as session:
                initialized = await initialize(session, version)
                manifest = await session.list_tools()
                results = []
                for name, args in arguments:
                    results.append(await session.call_tool(name, args))
    return initialized, manifest, results, wire.read_bytes().splitlines()


@pytest.mark.parametrize(
    "version", ["2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25", "2099-01-01"]
)
def test_native_protocol_negotiation(tmp_path, version):
    path = repo(tmp_path)
    init, tools, results, frames = asyncio.run(
        session_calls(
            tmp_path,
            path,
            version,
            1024 * 1024,
            [
                ("graph_query", {"schema_version": "atlas.graph.v1", "op": "status"}),
                ("graph_query", {"query": "definitions_of:Absent"}),
                ("graph_query", {"query": "not-a-query"}),
                ("graph_query", {"query": False}),
            ],
        )
    )
    actual = "2025-11-25" if version == "2099-01-01" else version
    assert init.protocolVersion == actual
    names = {tool.name for tool in tools.tools}
    assert names == {
        "view_file",
        "list_dir",
        "search_text",
        "search_symbol",
        "graph_query",
        "test_dry_run",
        "memex_read",
    }
    graph = next(tool for tool in tools.tools if tool.name == "graph_query")
    structured = actual in {"2025-06-18", "2025-11-25"}
    assert (graph.outputSchema is not None) == structured
    for result in results:
        payload = json.loads(result.content[0].text)
        assert result.structuredContent == (payload if structured else None)
        Draft202012Validator(GRAPH_OUTPUT_SCHEMA).validate(payload)
    assert [result.isError for result in results] == [False, False, True, True]
    assert json.loads(results[0].content[0].text)["data"]["source"]["references"]
    calls = [line for line in frames if "content" in json.loads(line).get("result", {})]
    assert len(calls) == len(results)
    for frame, result in zip(calls, results, strict=True):
        body = result_bytes(result)
        assert b'"result":' + body in frame
        assert len(body) <= 1024 * 1024 and len(frame) > len(body)


@pytest.mark.parametrize("budget", [1, 728, 729, 1000])
def test_native_complete_result_budget(tmp_path, budget):
    path = repo(tmp_path)
    _init, _tools, results, frames = asyncio.run(
        session_calls(
            tmp_path,
            path,
            "2025-11-25",
            budget,
            [
                (
                    "graph_query",
                    {"schema_version": "atlas.graph.v1", "op": "unknown" + 'é"\\' * 2000},
                ),
                ("graph_query", {"schema_version": "atlas.graph.v1", "op": "status"}),
                ("view_file", {"path": 'é"\\' * 2000}),
            ],
        )
    )
    calls = [line for line in frames if "content" in json.loads(line).get("result", {})]
    assert len(calls) == 3
    for result, frame in zip(results, calls, strict=True):
        assert result.isError
        payload = json.loads(result.content[0].text)
        code = payload["error"]["code"] if "schema_version" in payload else payload["error"]
        size = len(result_bytes(result))
        assert size <= budget or (code == "INVALID_BUDGET" and size <= FIXED_ERROR_ENVELOPE_CAP)
        assert b'"result":' + result_bytes(result) in frame
        assert len(frame) > size
