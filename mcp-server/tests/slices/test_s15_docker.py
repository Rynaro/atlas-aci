"""Actual offline production Rust grammar and read-only stdio navigation."""

import asyncio
import errno
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from atlas_aci.codegraph import DEFAULT_LANGS

FIXTURE = Path(__file__).parents[1] / "fixtures/rust_workspace"


async def rust_mcp(args):
    with anyio.fail_after(45):
        async with stdio_client(StdioServerParameters(command="docker", args=args)) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                results = {}
                for name, arguments in [
                    ("search_symbol", {"name": "Alpha"}),
                    ("graph_query", {"query": "callers_of:free"}),
                    ("graph_query", {"query": "definitions_of:execute"}),
                ]:
                    response = await session.call_tool(name, arguments)
                    assert not response.isError, response
                    results[arguments.get("query", "Alpha")] = json.loads(response.content[0].text)
                return results


def test_rust_offline_readonly_stdio(tmp_path):
    image = os.environ.get("ATLAS_ACI_S15_DOCKER_IMAGE")
    if not image:
        pytest.skip("set ATLAS_ACI_S15_DOCKER_IMAGE for the required production integration")
    source = tmp_path / "source"
    shutil.copytree(FIXTURE, source)
    graphs, memex = tmp_path / "graphs", tmp_path / "memex"
    for directory in (graphs, memex):
        directory.mkdir(mode=0o777)
        directory.chmod(0o777)
    record = {"image": image, "commands": [], "default_languages": list(DEFAULT_LANGS)}
    mounts = [
        "--read-only",
        "--network",
        "none",
        "-v",
        f"{source}:/repo:ro",
        "-v",
        f"{graphs}:/graphs",
        "-v",
        f"{memex}:/memex",
    ]

    def run(args, readonly_graph=False):
        selected = list(mounts)
        if readonly_graph:
            selected[selected.index(f"{graphs}:/graphs")] = f"{graphs}:/graphs:ro"
        command = ["docker", "run", "--rm", *selected, *args]
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
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

    def snapshot():
        return json.loads(
            run(
                [
                    "--entrypoint",
                    "python",
                    image,
                    "-c",
                    "\n".join(
                        [
                            "import os,json,hashlib;from pathlib import Path",
                            "assert os.geteuid()==10001",
                            "result={}",
                            "for location in ('/repo','/graphs'):",
                            " root=Path(location);files={}",
                            " for p in [root,*sorted(root.rglob('*'))]:",
                            (
                                "  s=p.stat(); row={'sha256':hashlib.sha256(p.read_bytes()).hexdig"
                                "est() if p.is_file() else None,'size':s.st_size,'mode':s.st_mode,"
                                "'mtime_ns':s.st_mtime_ns,'uid':s.st_uid,'gid':s.st_gid}"
                            ),
                            "  files[str(p.relative_to(root))]=row",
                            " result[location]=files",
                            "print(json.dumps(result,sort_keys=True))",
                        ]
                    ),
                ],
                readonly_graph=True,
            )
        )

    try:
        record["grammar"] = json.loads(
            run(
                [
                    "--entrypoint",
                    "python",
                    image,
                    "-c",
                    "\n".join(
                        [
                            (
                                "import errno,os,json,hashlib,platform,importlib.metadata;from pat"
                                "hlib import Path"
                            ),
                            (
                                "from tree_sitter_language_pack import get_language,get_parser;imp"
                                "ort tree_sitter_language_pack as pack"
                            ),
                            "assert os.geteuid()==10001",
                            "try: Path('/repo/negative-write').write_text('forbidden')",
                            "except OSError as e: assert e.errno==errno.EROFS;source_errno=e.errno",
                            "else: raise AssertionError('source writable')",
                            "lang=get_language('rust');parser=get_parser('rust')",
                            (
                                "tree=parser.parse(b'mod inner { pub fn call() {} } struct S; impl"
                                " S {fn method() {}} trait T {fn sig();} macro_rules! m {()=>{}}')"
                            ),
                            "assert not tree.root_node.has_error",
                            (
                                "libs=list(Path('/tmp/.cache/tree-sitter-language-pack').rglob('li"
                                "btree_sitter_rust.so'));assert len(libs)==1"
                            ),
                            "native=Path(pack.__file__).parent/'_native.abi3.so'",
                            (
                                "print(json.dumps({'uid':os.geteuid(),'source_errno':source_errno,"
                                "'platform':platform.machine(),'package':importlib.metadata.versio"
                                "n('tree-sitter-language-pack'),'tree_sitter':importlib.metadata.v"
                                "ersion('tree-sitter'),'abi':lang.abi_version,'semantic_version':l"
                                "ang.semantic_version,'rust_sha256':hashlib.sha256(libs[0].read_by"
                                "tes()).hexdigest(),'rust_bytes':libs[0].stat().st_size,'sdk_sha25"
                                "6':hashlib.sha256(native.read_bytes()).hexdigest(),'tree':str(tre"
                                "e.root_node)},sort_keys=True))"
                            ),
                        ]
                    ),
                ]
            )
        )
        assert record["grammar"]["source_errno"] == errno.EROFS
        assert record["grammar"]["abi"] == 15
        assert record["grammar"]["package"] == "1.6.0"
        run([image, "index", "--repo", "/repo", "--graph-root", "/graphs"])
        assert not (source / ".atlas").exists()
        record["status"] = json.loads(
            run([image, "status", "--repo", "/repo", "--graph-root", "/graphs"], True)
        )
        record["before"] = snapshot()
        name = "atlas-s15-" + uuid.uuid4().hex
        args = [
            "run",
            "--rm",
            "-i",
            "--name",
            name,
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
        args[args.index(f"{graphs}:/graphs")] = f"{graphs}:/graphs:ro"
        record["stdio_argv"] = ["docker", *args]
        try:
            record["mcp"] = asyncio.run(rust_mcp(args))
        finally:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
        record["after"] = snapshot()
        # Docker Desktop synthesizes mount-root ownership independently for each
        # container. Record both owners; all descendant ownership stays strict.
        record["mount_root_owners"] = {}
        for location in ("/repo", "/graphs"):
            owners = []
            for phase in ("before", "after"):
                root = record[phase][location]["."]
                owners.append({key: root.pop(key) for key in ("uid", "gid")})
            record["mount_root_owners"][location] = owners
        assert record["before"] == record["after"]
        assert any(row["kind"] == "struct" for row in record["mcp"]["Alpha"]["definitions"])
        calls = record["mcp"]["callers_of:free"]
        assert calls["edges"] == [] and calls["unresolved_refs"] > 0
        assert all(
            row["kind"] == "method"
            for row in record["mcp"]["definitions_of:execute"]["definitions"]
        )
        assert record["status"]["completeness"]["state"] == "complete"
        inspect = subprocess.run(
            ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert inspect.returncode == 0
        record["image_id"] = inspect.stdout.strip()
    finally:
        evidence = os.environ.get("ATLAS_ACI_S15_EVIDENCE_DIR")
        if evidence:
            target = Path(evidence)
            target.mkdir(parents=True, exist_ok=True)
            (target / "docker-rust.json").write_text(
                json.dumps(record, sort_keys=True, indent=2) + "\n"
            )
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--read-only",
                "--network",
                "none",
                "-v",
                f"{graphs}:/graphs",
                "--entrypoint",
                "python",
                image,
                "-c",
                "import shutil;shutil.rmtree('/graphs/repos',ignore_errors=True)",
            ],
            capture_output=True,
            timeout=30,
        )
