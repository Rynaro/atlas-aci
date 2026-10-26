"""Actual production UID, offline readonly SDK status and legacy interoperability."""

import asyncio
import hashlib
import json
import os
import subprocess
import uuid
from pathlib import Path

import anyio
import pytest
from jsonschema import Draft202012Validator
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import atlas_aci
from atlas_aci.protocol.graph import GRAPH_OUTPUT_SCHEMA
from atlas_aci.protocol.results import result_bytes

INVENTORY = """import errno,hashlib,json,os,stat
from pathlib import Path
assert os.geteuid()==10001
try:Path('/repo/negative-write').write_text('forbidden')
except OSError as e:assert e.errno==errno.EROFS
else:raise AssertionError('source writable')
r={}
for base in ['/repo','/graphs']:
 root=Path(base)
 for p in [root,*sorted(root.rglob('*'))]:
  s=p.lstat();assert stat.S_ISDIR(s.st_mode) or stat.S_ISREG(s.st_mode)
  row={'mode':s.st_mode,'size':s.st_size,'mtime_ns':s.st_mtime_ns,
       'uid':s.st_uid,'gid':s.st_gid,
       'sha256':hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None}
  r[str(p)]=row
print(json.dumps({'uid':os.geteuid(),'negative_errno':errno.EROFS,'entries':r},sort_keys=True))
"""


def test_offline_readonly_status_and_legacy(tmp_path):
    image = os.environ.get("ATLAS_ACI_S11_DOCKER_IMAGE")
    if not image:
        pytest.skip("set ATLAS_ACI_S11_DOCKER_IMAGE for required native integration")
    repo, graphs, memex = (tmp_path / name for name in ("repo", "graphs", "memex"))
    for path in (repo, graphs, memex):
        path.mkdir()
    graphs.chmod(0o777)
    memex.chmod(0o777)
    source = b"def target():\n return 1\ndef caller():\n target()\n"
    (repo / "a.py").write_bytes(source)
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

    runtime_paths = [
        "codegraph.py",
        "storage/evidence.py",
        "providers/python_imports.py",
        "indexing/extraction_cache.py",
        "resolvers/dependencies.py",
        "extractors/rust.py",
        "storage/snapshots.py",
        "server.py",
        "tools/view_file.py",
        "tools/graph_query.py",
        "protocol/__init__.py",
        "protocol/graph.py",
        "protocol/results.py",
    ]
    script = (
        "import hashlib,json,atlas_aci;from pathlib import Path;"
        "root=Path(atlas_aci.__file__).parent;names="
        + repr(runtime_paths)
        + ";print(json.dumps({name:hashlib.sha256((root/name).read_bytes()).hexdigest() "
        "for name in names},sort_keys=True))"
    )
    image_hashes = json.loads(run(["--entrypoint", "python", image, "-c", script]))
    host_root = Path(atlas_aci.__file__).parent
    assert image_hashes == {
        name: hashlib.sha256((host_root / name).read_bytes()).hexdigest() for name in runtime_paths
    }
    record["runtime_hashes"] = image_hashes
    run(["-v", f"{graphs}:/graphs", image, "index", "--repo", "/repo", "--graph-root", "/graphs"])
    mounts = ["-v", f"{graphs}:/graphs:ro", "-v", f"{memex}:/memex"]
    before = json.loads(run([*mounts, "--entrypoint", "python", image, "-c", INVENTORY]))
    name = "aci-s11-mcp-" + uuid.uuid4().hex

    async def exercise():
        command = [
            *base[1:3],
            "-i",
            "--name",
            name,
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
        record["stdio_argv"] = ["docker", *command]
        with anyio.fail_after(30):
            async with stdio_client(
                StdioServerParameters(command="docker", args=command)
            ) as streams:
                async with ClientSession(*streams) as session:
                    initialized = await session.initialize()
                    tools = await session.list_tools()
                    tool = next(t for t in tools.tools if t.name == "graph_query")
                    assert tool.outputSchema == GRAPH_OUTPUT_SCHEMA
                    status = await session.call_tool(
                        "graph_query", {"schema_version": "atlas.graph.v1", "op": "status"}
                    )
                    payload = status.structuredContent
                    Draft202012Validator(GRAPH_OUTPUT_SCHEMA).validate(payload)
                    assert not status.isError and payload["status"] == "ok"
                    assert payload == json.loads(status.content[0].text)
                    assert payload["data"]["source"]["state"] == "available"
                    ref = payload["data"]["source"]["references"][0]
                    assert ref["source_sha256"] == hashlib.sha256(source).hexdigest()
                    viewed = await session.call_tool(
                        "view_file",
                        {
                            "path": "a.py",
                            "start_line": 1,
                            "end_line": 4,
                            "snapshot_id": payload["snapshot_id"],
                            "source_sha256": ref["source_sha256"],
                        },
                    )
                    legacy = await session.call_tool("graph_query", {"query": "callers_of:target"})
                    assert not viewed.isError and not legacy.isError
                    assert "".join(json.loads(viewed.content[0].text)["lines"]) == source.decode()
                    assert (
                        json.loads(legacy.content[0].text)["generation_id"]
                        == payload["generation_id"]
                    )
                    assert len(json.loads(legacy.content[0].text)["edges"]) == 1
                    bad = await session.call_tool("graph_query", {"query": "invalid"})
                    assert bad.isError
                    record["negotiated_version"] = initialized.protocolVersion
                    record["results"] = [
                        r.model_dump(mode="json", exclude_none=True)
                        for r in (status, viewed, legacy, bad)
                    ]
                    record["result_byte_lengths"] = [
                        len(result_bytes(r)) for r in (status, viewed, legacy, bad)
                    ]

    try:
        asyncio.run(exercise())
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
    after = json.loads(run([*mounts, "--entrypoint", "python", image, "-c", INVENTORY]))
    record["before"], record["after"] = before, after
    record["source_sha256"] = hashlib.sha256((repo / "a.py").read_bytes()).hexdigest()
    assert not (repo / ".atlas").exists()
    evidence = os.environ.get("ATLAS_ACI_S11_EVIDENCE_DIR")
    if evidence:
        path = Path(evidence)
        path.mkdir(parents=True, exist_ok=True)
        (path / "docker-status.json").write_text(json.dumps(record, indent=2) + "\n")

    # Docker Desktop presents synthetic ownership on the /repo and /graphs mount roots.
    # Both raw observations remain above; only the two root presentation fields
    # are excluded. Every other field on both roots and every descendant
    # retain strict metadata, ownership and hash equality.
    def comparable(observed):
        return {
            **observed,
            "entries": {
                path: {
                    key: value
                    for key, value in row.items()
                    if path not in {"/repo", "/graphs"} or key not in {"uid", "gid"}
                }
                for path, row in observed["entries"].items()
            },
        }

    expected, actual = comparable(before), comparable(after)
    assert expected == actual, {
        path: {"before": expected["entries"].get(path), "after": actual["entries"].get(path)}
        for path in expected["entries"].keys() | actual["entries"].keys()
        if expected["entries"].get(path) != actual["entries"].get(path)
    }
