"""Current native9 FTS retrieval on the locked offline read-only Linux image."""

import asyncio
import hashlib
import json
import os
import subprocess
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_s12_docker import INVENTORY

import atlas_aci
from atlas_aci.protocol.results import result_bytes


def test_offline_readonly_retrieval_and_exact_source(tmp_path):
    image = os.environ.get("ATLAS_ACI_S17_DOCKER_IMAGE")
    if not image:
        pytest.skip("set ATLAS_ACI_S17_DOCKER_IMAGE for required native integration")
    repo, graphs, memex = (tmp_path / name for name in ("repo", "graphs", "memex"))
    for path in (repo, graphs, memex):
        path.mkdir()
    graphs.chmod(0o777)
    memex.chmod(0o777)
    source = b"def Alpha():\n pass\n# nightingale\n# task phrase caf\xc3\xa9\n"
    (repo / "a.py").write_bytes(source)
    (repo / "other.py").write_text("def Other():\n pass\n")
    base = ["docker", "run", "--rm", "--read-only", "--network", "none", "-v", f"{repo}:/repo:ro"]
    record = {"image": image, "commands": []}

    def run(args):
        command = [*base, *args]
        result = subprocess.run(command, capture_output=True, text=True, timeout=90)
        record["commands"].append(
            {
                "argv": command,
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        )
        assert result.returncode == 0, result.stderr
        return result.stdout

    host_root = Path(atlas_aci.__file__).parent
    runtime = {
        str(p.relative_to(host_root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(host_root.rglob("*.py"))
    }
    script = (
        "import hashlib,json;from pathlib import Path;import atlas_aci;"
        "r=Path(atlas_aci.__file__).parent;"
        "print(json.dumps({str(p.relative_to(r)):hashlib.sha256(p.read_bytes()).hexdigest() "
        "for p in r.rglob('*.py')}))"
    )
    record["runtime_hashes"] = json.loads(run(["--entrypoint", "python", image, "-c", script]))
    assert record["runtime_hashes"] == runtime
    run(["-v", f"{graphs}:/graphs", image, "index", "--repo", "/repo", "--graph-root", "/graphs"])
    mounts = ["-v", f"{graphs}:/graphs:ro", "-v", f"{memex}:/memex"]
    before = json.loads(run([*mounts, "--entrypoint", "python", image, "-c", INVENTORY]))

    async def exercise():
        args = [
            *base[1:3],
            "-i",
            *base[3:],
            *mounts,
            image,
            "serve",
            "--repo",
            "/repo",
            "--graph-root",
            "/graphs",
            "--memex-root",
            "/memex",
        ]
        record["stdio_argv"] = ["docker", *args]
        with anyio.fail_after(30):
            async with stdio_client(StdioServerParameters(command="docker", args=args)) as streams:
                async with ClientSession(*streams) as session:
                    initialized = await session.initialize()
                    assert len((await session.list_tools()).tools) == 7
                    request = {
                        "schema_version": "atlas.graph.v1",
                        "op": "retrieve",
                        "text": "nightingale task",
                        "max_bytes": 10000,
                    }
                    first = await session.call_tool("graph_query", request)
                    assert not first.isError, first
                    payload = first.structuredContent
                    assert payload == json.loads(first.content[0].text)
                    candidate = payload["data"]["candidates"][0]
                    assert (
                        len(payload["data"]["candidates"]) == 1
                        and candidate["kind"] == "source_file"
                    )
                    assert candidate["source"] == {
                        "state": "available",
                        "path": "a.py",
                        "granularity": "file",
                        "source_sha256": hashlib.sha256(source).hexdigest(),
                        "start_byte": 0,
                        "end_byte": len(source),
                        "source_size": len(source),
                    }
                    exact = await session.call_tool("graph_query", {**request, "text": "Alpha"})
                    entity = exact.structuredContent["data"]["candidates"][0]
                    assert not exact.isError and entity["match_tier"] == "exact_name"
                    assert entity["source"]["granularity"] == "occurrence"
                    identity = await session.call_tool(
                        "graph_query", {**request, "text": entity["entity"]["id"]}
                    )
                    assert (
                        not identity.isError
                        and identity.structuredContent["data"]["candidates"][0]["match_tier"]
                        == "exact_entity_id"
                    )
                    filtered = await session.call_tool(
                        "graph_query", {**request, "path_prefixes": ["other.py"]}
                    )
                    assert (
                        not filtered.isError
                        and filtered.structuredContent["data"]["candidates"] == []
                    )
                    interrupted = await session.call_tool(
                        "graph_query", {**request, "max_vm_operations": 0}
                    )
                    assert (
                        interrupted.isError
                        and interrupted.structuredContent["error"]["code"]
                        == "RETRIEVAL_BUDGET_EXCEEDED"
                    )
                    replay = await session.call_tool("graph_query", request)
                    assert not replay.isError and replay.structuredContent == payload
                    for result in [first, exact, identity, filtered, interrupted, replay]:
                        assert len(result_bytes(result)) <= 10000
                    record["protocol_version"] = initialized.protocolVersion
                    record["results"] = [
                        r.model_dump(mode="json", by_alias=True, exclude_none=True)
                        for r in [first, exact, identity, filtered, interrupted, replay]
                    ]

    try:
        asyncio.run(exercise())
    finally:
        after = json.loads(run([*mounts, "--entrypoint", "python", image, "-c", INVENTORY]))
        record["before"], record["after"] = before, after
        destination = os.environ.get("ATLAS_ACI_S17_NATIVE_RECORD")
        if destination:
            Path(destination).write_text(json.dumps(record, indent=2) + "\n")

    def comparable(value):
        entries = {p: dict(row) for p, row in value["entries"].items()}
        for root in ("/repo", "/graphs"):
            entries[root].pop("uid")
            entries[root].pop("gid")
        return {**value, "entries": entries}

    assert comparable(before) == comparable(after)
    assert not (repo / ".atlas").exists()
