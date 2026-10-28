"""Native context bundles and typed source membership across G2/G3 pruning."""

import asyncio
import base64
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_s08_contract import git

import atlas_aci
from atlas_aci.codegraph import CodeGraph
from atlas_aci.protocol.results import result_bytes


def test_native_context_and_definition_source_after_prune(tmp_path):
    image = os.environ.get("ATLAS_ACI_S18_DOCKER_IMAGE")
    if not image:
        pytest.skip("set ATLAS_ACI_S18_DOCKER_IMAGE for required native integration")
    primary = tmp_path / "primary"
    primary.mkdir()
    git(primary, "init")
    git(primary, "config", "user.email", "fixture@example.invalid")
    git(primary, "config", "user.name", "fixture")
    original = b"def A():\n " + b"B(); " * 20 + b"\ndef B():\n pass\n"
    (primary / "a.py").write_bytes(original)
    git(primary, "add", "a.py")
    git(primary, "commit", "-m", "native source fixture")
    source = (tmp_path / "source").resolve()
    git(primary, "worktree", "add", "--detach", str(source), "HEAD")
    graphs, memex = (tmp_path / name for name in ("graphs", "memex"))
    for path in (graphs, memex):
        path.mkdir()
        path.chmod(0o777)
    record = {"image": image, "commands": [], "publisher": sys.platform}
    mounts = [
        "--read-only",
        "--network",
        "none",
        "-v",
        f"{source}:{source}:ro",
        "-v",
        f"{graphs}:/graphs:ro",
        "-v",
        f"{memex}:/memex",
    ]
    host = CodeGraph(source, graph_root=graphs) if sys.platform == "darwin" else None

    def run(args, writable=False):
        selected = list(mounts)
        if writable:
            selected[selected.index(f"{graphs}:/graphs:ro")] = f"{graphs}:/graphs"
        command = ["docker", "run", "--rm", *selected, *args]
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

    def publish(content=None):
        if host is not None:
            return host.build(overlays={"a.py": content} if content else None)
        script = (
            "import json;from pathlib import Path;from atlas_aci.codegraph import CodeGraph;"
            f'g=CodeGraph(Path({str(source)!r}),graph_root=Path("/graphs"));'
            f"print(json.dumps(g.build(overlays={ {'a.py': content} if content else None!r})));"
            "g.close()"
        )
        return json.loads(run(["--entrypoint", "python", image, "-c", script], True))

    inventory_script = """import errno,hashlib,json,os,stat,sys
from pathlib import Path
assert os.geteuid()==10001
try:(Path(sys.argv[1])/'negative-write').write_text('forbidden')
except OSError as e:assert e.errno==errno.EROFS
else:raise AssertionError('source writable')
r={}
def visit(p):
 s=p.lstat();assert stat.S_ISDIR(s.st_mode) or stat.S_ISREG(s.st_mode)
 r[str(p)]={'mode':s.st_mode,'uid':s.st_uid,'gid':s.st_gid,'size':s.st_size,
 'mtime_ns':s.st_mtime_ns,'sha256':None}
 if stat.S_ISREG(s.st_mode):r[str(p)]['sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
 if stat.S_ISDIR(s.st_mode):
  with os.scandir(p) as it:
   for child in sorted(it,key=lambda e:e.name):visit(Path(child.path))
for root in [Path(sys.argv[1]),Path('/graphs')]:visit(root)
print(json.dumps({'uid':os.geteuid(),'negative_errno':errno.EROFS,'entries':r},sort_keys=True))
"""

    def inventory():
        return json.loads(
            run(["--entrypoint", "python", image, "-c", inventory_script, str(source)])
        )

    runtime = Path(atlas_aci.__file__).parent
    names = sorted(str(p.relative_to(runtime)) for p in runtime.rglob("*.py"))
    script = (
        "import hashlib,json,atlas_aci;from pathlib import Path;r=Path(atlas_aci.__file__).parent;"
        f"names={names!r};"
        "print(json.dumps({p:hashlib.sha256((r/p).read_bytes()).hexdigest() for p in names}))"
    )
    record["runtime_hashes"] = json.loads(run(["--entrypoint", "python", image, "-c", script]))
    assert record["runtime_hashes"] == {
        p: hashlib.sha256((runtime / p).read_bytes()).hexdigest() for p in names
    }
    record["g1"] = publish()
    server = """import asyncio,sys,structlog
from pathlib import Path
from atlas_aci.config import Config
from atlas_aci.server import run_stdio
structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
asyncio.run(run_stdio(Config(repo=Path(sys.argv[1]),graph_root=Path('/graphs'),memex_root=Path('/memex'),max_calls_per_minute=0)))
"""

    async def exercise():
        command = [
            "run",
            "--rm",
            "-i",
            *mounts,
            "--entrypoint",
            "python",
            image,
            "-c",
            server,
            str(source),
        ]
        record["stdio_argv"] = ["docker", *command]
        with anyio.fail_after(45):
            async with stdio_client(
                StdioServerParameters(command="docker", args=command)
            ) as streams:
                async with ClientSession(*streams) as session:
                    record["protocol"] = (await session.initialize()).protocolVersion
                    first = await session.call_tool(
                        "graph_query",
                        {
                            "schema_version": "atlas.graph.v1",
                            "op": "context",
                            "text": "A",
                            "continuation": True,
                            "source_policy": "references",
                            "max_bytes": 40000,
                        },
                    )
                    assert not first.isError, first
                    payload = first.structuredContent
                    assert payload["data"]["bundles"] and payload["data"]["has_more"]
                    descriptor = payload["data"]["resources"][0]
                    assert (
                        descriptor["kind"] == "definition" and descriptor["membership"]["entity_id"]
                    )
                    assert (
                        payload["data"]["token_usage"]["count"]
                        == (len(result_bytes(first)) + 3) // 4
                    )
                    assert len(result_bytes(first)) <= 40000
                    record["first"] = payload
                    for number in (2, 3):
                        record["g" + str(number)] = publish(f"def new{number}():\n pass\n".encode())
                    # Path retirement is observed as the SAME container UID that indexed on Linux.
                    check = (
                        'from pathlib import Path;p=Path("/graphs");'
                        f'assert not list(p.rglob("manifest.{payload["snapshot_id"]}.json"));'
                        'print("old path pruned")'
                    )
                    run(["--entrypoint", "python", image, "-c", check])
                    record["before"] = inventory()
                    resource = await session.read_resource(descriptor["uri"])
                    assert (
                        base64.b64decode(resource.contents[0].blob)
                        == original[descriptor["start_byte"] : descriptor["end_byte"]]
                    )
                    args = {
                        "schema_version": "atlas.graph.v1",
                        "op": "continue",
                        "cursor": payload["data"]["next_cursor"],
                        "query_digest": payload["data"]["query_digest"],
                    }
                    left, right = await asyncio.gather(
                        session.call_tool("graph_query", args),
                        session.call_tool("graph_query", args),
                    )
                    assert not left.isError and result_bytes(left) == result_bytes(right)
                    assert left.structuredContent["generation_id"] == payload["generation_id"]
                    assert left.structuredContent["data"]["bundles"]
                    assert len(result_bytes(left)) <= 40000
                    assert (
                        left.structuredContent["data"]["token_usage"]["count"]
                        == (len(result_bytes(left)) + 3) // 4
                    )
                    record["continuation"] = left.structuredContent
                    record["resource"] = resource.model_dump(
                        mode="json", by_alias=True, exclude_none=True
                    )
                    record["after"] = inventory()

                    def comparable(value):
                        rows = json.loads(json.dumps(value))
                        for root in (str(source), "/graphs"):
                            for key in ("uid", "gid"):
                                rows["entries"][root].pop(key)
                        return rows

                    assert comparable(record["before"]) == comparable(record["after"])

    try:
        asyncio.run(exercise())
    finally:
        if host is not None:
            host.close()
        destination = os.environ.get("ATLAS_ACI_S18_NATIVE_RECORD")
        if destination:
            Path(destination).write_text(json.dumps(record, indent=2) + "\n")
