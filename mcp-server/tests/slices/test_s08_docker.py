"""Production readonly graph/source leases across publication and real MCP calls."""

import asyncio
import errno
import hashlib
import json
import os
import selectors
import subprocess
import sys
import uuid

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_s08_contract import NEW, OLD, git

from atlas_aci.codegraph import CodeGraph

READER = """import errno,json,os,sys,sqlite3,subprocess
from pathlib import Path
from atlas_aci.codegraph import CodeGraph
assert os.geteuid()==10001
source=Path(sys.argv[1]);g=CodeGraph(source,graph_root=Path('/graphs'),read_only=True)
try:(source/'negative-write').write_text('forbidden')
except OSError as exc:assert exc.errno==errno.EROFS;code=exc.errno
else:raise AssertionError('source writable')
base=len(os.listdir('/proc/self/fd'))
try:
 with g.reader_lease() as failed:
  fd=failed._source_lease.fd;db=failed.db
  raise ValueError('controlled error')
except ValueError:pass
try:os.fstat(fd)
except OSError:pass
else:raise AssertionError('pack leaked')
try:db.execute('SELECT 1')
except sqlite3.ProgrammingError:pass
else:raise AssertionError('SQLite leaked')
assert len(os.listdir('/proc/self/fd'))==base
with g.reader_lease() as reader:
 assert len(os.listdir('/proc/self/fd'))==base+2
 print(json.dumps({'ready':True,'uid':os.geteuid(),'errno':code,'token':reader.snapshot_id,'generation_id':reader.generation_id,'pack':reader._source_lease.manifest['pack'],'identity':reader._source_lease.manifest['context']['identity'],'git_runtime_code':subprocess.run(['git','-C',str(source),'rev-parse','--absolute-git-dir'],capture_output=True).returncode}),flush=True)
 assert sys.stdin.readline().strip()=='READ'
 print(json.dumps({'bytes':reader.read_source('a.py').decode(),'definitions':reader.search_symbol('old_target')['definitions']}),flush=True)
assert len(os.listdir('/proc/self/fd'))==base
g.close()
"""


def test_native_publication_readonly_stdio(tmp_path):
    image = os.environ.get("ATLAS_ACI_S08_DOCKER_IMAGE")
    if not image:
        pytest.skip("set ATLAS_ACI_S08_DOCKER_IMAGE for required native integration")
    primary = tmp_path / "primary"
    primary.mkdir()
    git(primary, "init")
    git(primary, "config", "user.email", "fixture@example.invalid")
    git(primary, "config", "user.name", "fixture")
    (primary / "a.py").write_bytes(OLD)
    git(primary, "add", "a.py")
    git(primary, "commit", "-m", "source fixture")
    source = (tmp_path / "source").resolve()
    git(primary, "worktree", "add", "--detach", str(source), "HEAD")
    graphs = (tmp_path / "graphs").resolve()
    graphs.mkdir()
    graphs.chmod(0o777)
    memex = (tmp_path / "memex").resolve()
    memex.mkdir()
    memex.chmod(0o777)
    record = {
        "image": image,
        "commands": [],
        "publisher": "macOS native host"
        if sys.platform == "darwin"
        else "Linux production UID10001 writer process",
    }
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

    def container(args, writable=False):
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
            return host.build(overlays={"a.py": content} if content is not None else None)
        script = (
            "import json;from pathlib import Path;from atlas_aci.codegraph imp"
            "ort CodeGraph;g=CodeGraph(Path("
            + repr(str(source))
            + "),graph_root=Path('/graphs'));print(json.dumps(g.build(overlays="
            + repr({"a.py": content} if content is not None else None)
            + ")));g.close()"
        )
        return json.loads(container(["--entrypoint", "python", image, "-c", script], True))

    def inventory():
        script = (
            "import hashlib,json,os;from pathlib import Path;r={};root=Path('/"
            "graphs');\nfor p in sorted(root.rglob('*')):\n s=p.stat();r[str(p.r"
            "elative_to(root))]={'sha256':hashlib.sha256(p.read_bytes()).hexdi"
            "gest() if p.is_file() else None,'mode':s.st_mode,'mtime_ns':s.st_"
            "mtime_ns,'uid':s.st_uid,'gid':s.st_gid}\nprint(json.dumps(r,sort_k"
            "eys=True))"
        )
        return json.loads(container(["--entrypoint", "python", image, "-c", script]))

    child = None
    name = "aci-s08-held-" + uuid.uuid4().hex
    stdio_name = "aci-s08-mcp-" + uuid.uuid4().hex
    try:
        record["g1"] = publish()
        command = [
            "docker",
            "run",
            "--rm",
            "-i",
            "--name",
            name,
            *mounts,
            "--entrypoint",
            "python",
            image,
            "-u",
            "-c",
            READER,
            str(source),
        ]
        record["held_argv"] = command
        child = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        selector = selectors.DefaultSelector()
        selector.register(child.stdout, selectors.EVENT_READ)
        assert selector.select(30), "no acquisition barrier"
        line = child.stdout.readline()
        assert line, child.stderr.read()
        record["acquired"] = json.loads(line)
        selector.close()
        assert record["acquired"]["errno"] == errno.EROFS and record["acquired"]["uid"] == 10001
        assert record["acquired"]["git_runtime_code"] != 0
        expected_identity = "git" if host is not None else "git_unavailable"
        assert record["acquired"]["identity"]["kind"] == expected_identity
        token = record["acquired"]["token"]

        async def run_mcp():
            args = [
                "run",
                "--rm",
                "-i",
                "--name",
                stdio_name,
                *mounts,
                image,
                "serve",
                "--repo",
                str(source),
                "--graph-root",
                "/graphs",
                "--memex-root",
                "/memex",
            ]
            record["stdio_argv"] = ["docker", *args]
            with anyio.fail_after(60):
                async with stdio_client(
                    StdioServerParameters(command="docker", args=args)
                ) as streams:
                    async with ClientSession(*streams) as session:
                        await session.initialize()
                        schema = next(
                            t.inputSchema
                            for t in (await session.list_tools()).tools
                            if t.name == "view_file"
                        )
                        assert {"snapshot_id", "source_sha256"} <= set(schema["properties"])

                        async def call(tool, arguments):
                            reply = await session.call_tool(tool, arguments)
                            body = json.loads(reply.content[0].text)
                            record.setdefault("mcp", []).append(
                                {"tool": tool, "arguments": arguments, "body": body}
                            )
                            return body

                        before = inventory()
                        defs = await call("graph_query", {"query": "definitions_of:old_target"})
                        assert (
                            defs["definitions"]
                            and defs["snapshot_id"] == token
                            and defs["source_available"]
                        )
                        first = await call(
                            "view_file",
                            {
                                "path": "a.py",
                                "start_line": 1,
                                "end_line": 2,
                                "snapshot_id": token,
                                "source_sha256": hashlib.sha256(OLD).hexdigest(),
                            },
                        )
                        assert (
                            "".join(first["lines"]) == OLD.decode()
                            and first["source_mode"] == "snapshot"
                        )
                        assert inventory() == before
                        (source / "a.py").write_bytes(NEW)
                        record["g2"] = publish()
                        old = await call(
                            "view_file",
                            {"path": "a.py", "start_line": 1, "end_line": 2, "snapshot_id": token},
                        )
                        assert "".join(old["lines"]) == OLD.decode()
                        live = await call(
                            "view_file", {"path": "a.py", "start_line": 1, "end_line": 2}
                        )
                        assert (
                            "".join(live["lines"]) == NEW.decode() and live["source_mode"] == "live"
                        )
                        record["g3"] = publish(b"def newest(): pass\n")
                        before = inventory()
                        denied = await call(
                            "view_file",
                            {"path": "a.py", "start_line": 1, "end_line": 2, "snapshot_id": token},
                        )
                        assert denied["error"] == "SNAPSHOT_UNAVAILABLE"
                        current = await call("search_symbol", {"name": "newest"})
                        assert current["definitions"] and current["snapshot_id"] != token
                        assert inventory() == before

        asyncio.run(run_mcp())
        child.stdin.write("READ\n")
        child.stdin.flush()
        out, err = child.communicate(timeout=30)
        record["held_result"] = {"exit_code": child.returncode, "stdout": out, "stderr": err}
        assert child.returncode == 0, err
        answer = json.loads(out)
        assert answer["bytes"] == OLD.decode() and answer["definitions"]
        assert (source / "a.py").read_bytes() == NEW and not (source / ".atlas").exists()
        final = inventory()
        assert sum("/source-snapshots/manifest." in "/" + p for p in final) == 2
        assert not any(token in p for p in final)
        record["final_inventory"] = final
        inspect = subprocess.run(
            ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert inspect.returncode == 0
        record["image_id"] = inspect.stdout.strip()
        record["passed"] = True
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.wait()
        for container_name in (name, stdio_name):
            subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, timeout=30)
        if host is not None:
            host.close()
        evidence = os.environ.get("ATLAS_ACI_S08_EVIDENCE_DIR")
        if evidence:
            from pathlib import Path

            path = Path(evidence)
            path.mkdir(parents=True, exist_ok=True)
            (path / "native-docker-stdio.json").write_text(
                json.dumps(record, indent=2, sort_keys=True) + "\n"
            )
        container(
            [
                "--entrypoint",
                "python",
                image,
                "-c",
                "import shutil;shutil.rmtree('/graphs/repos',ignore_errors=True)",
            ],
            True,
        )
        git(primary, "worktree", "remove", "--force", str(source))
