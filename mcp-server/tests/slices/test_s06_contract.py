"""External graph storage with actual parser, SQLite, stdio and :ro mounts."""

from __future__ import annotations

import asyncio
import errno
import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import anyio
import pytest
from click.testing import CliRunner
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from atlas_aci.__main__ import cli
from atlas_aci.codegraph import SCHEMA_EPOCH, CodeGraph
from atlas_aci.config import Config
from atlas_aci.enforcement import ToolError
from atlas_aci.server import build_server_state, dispatch_tool_call

SOURCE = (
    b"def s06_unique_target():\n    return 42\ndef s06_caller():\n    return s06_unique_target()\n"
)


def snapshot(root: Path) -> dict:
    result = {}
    for p in [root, *sorted(root.rglob("*"))]:
        metadata = p.stat()
        result[str(p.relative_to(root))] = {
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None,
            "mtime_ns": metadata.st_mtime_ns,
            "size": metadata.st_size,
            "mode": metadata.st_mode,
        }
    return result


def invoke(repo: Path, command: str, *args: str):
    result = CliRunner().invoke(cli, [command, "--repo", str(repo), *args])
    assert result.exit_code == 0, result.output + repr(result.exception)
    return result


async def mcp_calls(command: str, args: list[str], env=None):
    with anyio.fail_after(30):
        async with stdio_client(
            StdioServerParameters(command=command, args=args, env=env)
        ) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                names = [tool.name for tool in (await session.list_tools()).tools]
                responses = {}
                for name, arguments in [
                    ("search_symbol", {"name": "s06_unique_target"}),
                    ("graph_query", {"query": "callers_of:s06_unique_target"}),
                ]:
                    response = await session.call_tool(name, arguments)
                    responses[name] = {
                        "is_error": response.isError,
                        "body": json.loads(response.content[0].text),
                    }
                return {"tools": names, "responses": responses}


@pytest.fixture(scope="module")
def readonly_index(tmp_path_factory):
    image = os.environ.get("ATLAS_ACI_S06_DOCKER_IMAGE")
    if not image:
        pytest.skip("set ATLAS_ACI_S06_DOCKER_IMAGE for required kernel :ro integration")
    base = tmp_path_factory.mktemp("s06-container")
    source, graphs, memex = (base / name for name in ("source", "graphs", "memex"))
    for path in (source, graphs, memex):
        path.mkdir()
        path.chmod(0o777 if path != source else 0o755)
    (source / "sample.py").write_bytes(SOURCE)
    before = snapshot(source)
    record = {"image": image, "source_before": before, "commands": []}
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

    def run(args, expected=0, readonly_graph=False):
        name = "atlas-s06-run-" + uuid.uuid4().hex
        selected_mounts = list(mounts)
        if readonly_graph:
            selected_mounts[selected_mounts.index(f"{graphs}:/graphs")] = f"{graphs}:/graphs:ro"
        cmd = ["docker", "run", "--rm", "--name", name, *selected_mounts, *args]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
            record["commands"].append(
                {
                    "argv": cmd,
                    "exit_code": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                }
            )
            assert result.returncode == expected, result.stderr
            return result.stdout if expected == 0 else result.stderr
        finally:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)

    proof = run(
        [
            "--entrypoint",
            "python",
            image,
            "-c",
            "import os,errno,json;from pathlib import Path\n"
            "assert os.geteuid()==10001\n"
            "try: Path('/repo/s06-negative-write').write_text('forbidden')\n"
            "except OSError as e: assert e.errno==errno.EROFS;code=e.errno\n"
            "else: raise AssertionError('source is writable')\n"
            "p=Path('/graphs/write-sentinel');p.write_text('writable');p.unlink()\n"
            "print(json.dumps({'source_errno':code,'uid':os.geteuid(),'graphs_writable':True}))",
        ]
    )
    record["mount_proof"] = json.loads(proof)
    denied = run(
        [image, "index", "--repo", "/repo", "--graph-root", "/graphs"],
        expected=1,
        readonly_graph=True,
    )
    assert "Read-only file system" in denied
    record["readonly_graph_rejected"] = True
    run([image, "index", "--repo", "/repo", "--graph-root", "/graphs"])
    namespace = graphs / "repos" / hashlib.sha256(b"/repo").hexdigest()
    assert (namespace / f"graph.{SCHEMA_EPOCH}.db").is_file()
    assert (namespace / ".index.lock").is_file()

    def graph_snapshot():
        # Linux bind mounts preserve UID10001/mode0600 from mkstemp. Inspect
        # as the production owner, with the graph mount itself read-only;
        # the host pytest UID is not entitled to read those private files.
        raw = run(
            [
                "--entrypoint",
                "python",
                image,
                "-c",
                "import os,json,hashlib;from pathlib import Path\n"
                "assert os.geteuid()==10001\n"
                "root=Path('/graphs');result={}\n"
                "for p in [root,*sorted(root.rglob('*'))]:\n"
                " s=p.stat()\n"
                " result[str(p.relative_to(root))]={\n"
                "  'sha256':hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None,\n"
                "  'mtime_ns':s.st_mtime_ns,'size':s.st_size,'mode':s.st_mode,\n"
                "  'uid':s.st_uid,'gid':s.st_gid}\n"
                "print(json.dumps({'reader_uid':os.geteuid(),'files':result},sort_keys=True))",
            ],
            readonly_graph=True,
        )
        observed = json.loads(raw)
        assert observed["reader_uid"] == 10001
        record["graph_snapshot_reader_uid"] = observed["reader_uid"]
        # Docker Desktop synthesizes the bind mount root owner per container.
        # Preserve both observations; compare all its stable original fields
        # and retain strict owner comparisons for every real descendant.
        mount_root = observed["files"]["."]
        ownership = {key: mount_root.pop(key) for key in ("uid", "gid")}
        record.setdefault("graph_mount_root_ownership", []).append(ownership)
        return observed["files"]

    graph_before = graph_snapshot()
    name = "atlas-s06-" + uuid.uuid4().hex
    serve = [
        "run",
        "--rm",
        "-i",
        "--name",
        name,
        "--read-only",
        "--network",
        "none",
        "-v",
        f"{source}:/repo:ro",
        "-v",
        f"{graphs}:/graphs:ro",
        "-v",
        f"{memex}:/memex",
        image,
        "serve",
        "--repo",
        "/repo",
        "--graph-root",
        "/graphs",
        "--memex-root",
        "/memex",
    ]
    try:
        record["stdio_argv"] = ["docker", *serve]
        record["mcp"] = asyncio.run(mcp_calls("docker", serve))
        record["source_after"] = snapshot(source)
        record["graph_before"] = graph_before
        record["graph_after"] = graph_snapshot()
        inspect = subprocess.run(
            ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert inspect.returncode == 0
        record["image_id"] = inspect.stdout.strip()
        evidence = os.environ.get("ATLAS_ACI_S06_EVIDENCE_DIR")
        if evidence:
            target = Path(evidence)
            target.mkdir(parents=True, exist_ok=True)
            (target / "docker-integration.json").write_text(
                json.dumps(record, sort_keys=True, indent=2) + "\n"
            )
        yield record
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
        # Only fixture-owned graph files; cleanup as their production UID.
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


def test_readonly_source_index(readonly_index):
    assert readonly_index["mount_proof"] == {
        "source_errno": errno.EROFS,
        "uid": 10001,
        "graphs_writable": True,
    }
    assert any(name.endswith(f"graph.{SCHEMA_EPOCH}.db") for name in readonly_index["graph_before"])
    assert not any(name.startswith(".atlas/") for name in readonly_index["source_after"])


def test_source_unchanged(readonly_index):
    assert readonly_index["source_before"] == readonly_index["source_after"]
    assert readonly_index["graph_before"] == readonly_index["graph_after"]


def test_serve_external_root(readonly_index):
    mcp = readonly_index["mcp"]
    assert {"search_symbol", "graph_query"} <= set(mcp["tools"])
    assert not mcp["responses"]["search_symbol"]["is_error"]
    assert (
        mcp["responses"]["search_symbol"]["body"]["definitions"][0]["name"] == "s06_unique_target"
    )
    assert not mcp["responses"]["graph_query"]["is_error"]
    assert mcp["responses"]["graph_query"]["body"]["edges"][0]["source"]["name"] == "s06_caller"


def test_default_compatibility(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "sample.py").write_bytes(SOURCE)
    invoke(repo, "index")
    graph = CodeGraph(repo)
    assert graph.atlas_dir == repo / ".atlas"
    assert graph.search_symbol("s06_unique_target")["definitions"]
    assert json.loads(invoke(repo, "status").stdout)["generation_id"] == graph.generation_id
    output = tmp_path / "export.jsonl"
    invoke(repo, "export", str(output))
    invoke(repo, "import", str(output))
    config = Config(repo, tmp_path / "memex")
    assert config.graph_root is None
    _enforcement, _memex, server_graph = build_server_state(config)
    assert server_graph.atlas_dir == repo / ".atlas"
    server_graph.close()
    graph.close()


def test_root_identity_isolation(tmp_path: Path):
    root = tmp_path / "graphs"
    repos = [tmp_path / "first", tmp_path / "second"]
    graphs = []
    for i, repo in enumerate(repos):
        repo.mkdir()
        (repo / "sample.py").write_text(f"def target_{i}():\n    pass\n")
        graph = CodeGraph(repo, graph_root=root)
        graph.build()
        graphs.append(graph)
    assert graphs[0].atlas_dir != graphs[1].atlas_dir
    assert (
        graphs[0].atlas_dir
        == root / "repos" / hashlib.sha256(str(repos[0].resolve()).encode()).hexdigest()
    )
    assert not graphs[0].search_symbol("target_1")["definitions"]
    before = snapshot(graphs[1].atlas_dir)
    (graphs[0].atlas_dir / "graph.0.db").write_bytes(b"old epoch")
    graphs[0].build()
    assert snapshot(graphs[1].atlas_dir) == before
    alias = tmp_path / "alias"
    alias.symlink_to(repos[0], target_is_directory=True)
    aliased = CodeGraph(alias, graph_root=root, read_only=True)
    assert aliased.atlas_dir == graphs[0].atlas_dir
    aliased.close()
    root_alias = tmp_path / "graph-root-alias"
    root_alias.symlink_to(root, target_is_directory=True)
    aliased_root = CodeGraph(repos[0], graph_root=root_alias, read_only=True)
    assert aliased_root.atlas_dir == graphs[0].atlas_dir
    assert aliased_root.search_symbol("target_0")["definitions"]
    aliased_root.close()
    for graph in graphs:
        graph.close()


def test_external_replay(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "sample.py").write_bytes(SOURCE)
    root = tmp_path / "graphs"
    first, second = tmp_path / "first.jsonl", tmp_path / "second.jsonl"
    invoke(repo, "index", "--graph-root", str(root))
    invoke(repo, "export", "--graph-root", str(root), str(first))
    before = invoke(repo, "status", "--graph-root", str(root)).stdout
    invoke(repo, "index", "--graph-root", str(root), "--since", "HEAD")
    invoke(repo, "export", "--graph-root", str(root), str(second))
    assert first.read_bytes() == second.read_bytes()
    assert invoke(repo, "status", "--graph-root", str(root)).stdout == before
    assert not (repo / ".atlas").exists()


def test_wrong_root_never_falls_back(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "sample.py").write_bytes(SOURCE)
    local = CodeGraph(repo)
    local.build()
    missing = tmp_path / "missing-graphs"
    reader = CodeGraph(repo, read_only=True, graph_root=missing)
    assert reader.atlas_dir != local.atlas_dir
    with pytest.raises(ToolError) as unavailable, reader.reader_lease():
        pass
    assert unavailable.value.code == "INDEX_UNAVAILABLE"
    assert str(reader.atlas_dir) in unavailable.value.message
    assert not missing.exists()
    assert (
        json.loads(invoke(repo, "status", "--graph-root", str(missing)).stdout)["generation_id"]
        is None
    )
    output = tmp_path / "missing.jsonl"
    result = CliRunner().invoke(
        cli, ["export", "--repo", str(repo), "--graph-root", str(missing), str(output)]
    )
    assert result.exit_code != 0 and str(reader.atlas_dir) in result.output
    assert not output.exists() and not missing.exists()
    config = Config(repo, tmp_path / "memex", graph_root=missing)
    enforcement, memex, server_graph = build_server_state(config)
    with pytest.raises(ToolError):
        asyncio.run(
            dispatch_tool_call(
                "search_symbol",
                {"name": "s06_unique_target"},
                config,
                enforcement,
                memex,
                server_graph,
            )
        )
    server_graph.close()
    reader.close()
    local.close()


def test_explicit_root_wins_and_is_read_only(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    file = repo / "sample.py"
    file.write_text("def local_only():\n    pass\n")
    local = CodeGraph(repo)
    local.build()
    local_before = snapshot(local.atlas_dir)
    file.write_bytes(SOURCE)
    root = tmp_path / "graphs"
    external = CodeGraph(repo, graph_root=root)
    external.build()
    assert snapshot(local.atlas_dir) == local_before
    config = Config(repo, tmp_path / "memex", graph_root=root)
    enforcement, memex, reader = build_server_state(config)
    before = snapshot(root)
    found = asyncio.run(
        dispatch_tool_call(
            "search_symbol", {"name": "s06_unique_target"}, config, enforcement, memex, reader
        )
    )
    assert found["definitions"]
    assert not reader.search_symbol("local_only")["definitions"]
    invoke(repo, "status", "--graph-root", str(root))
    invoke(repo, "export", "--graph-root", str(root), str(tmp_path / "external.jsonl"))
    assert snapshot(root) == before
    assert config.memex_root == tmp_path / "memex"
    assert config.graph_root == root
    reader.close()
    external.close()
    local.close()


@pytest.mark.parametrize(
    "root_kind", ["file", "file_parent", "inside_source", "source_alias", "namespace_escape"]
)
def test_invalid_roots(tmp_path: Path, root_kind: str):
    repo = tmp_path / "repo"
    repo.mkdir()
    root = tmp_path / "graphs"
    if root_kind in {"file", "file_parent"}:
        root.write_text("not a directory")
        if root_kind == "file_parent":
            root = root / "nested"
    elif root_kind == "inside_source":
        root = repo / "graphs"
    elif root_kind == "source_alias":
        root.symlink_to(repo, target_is_directory=True)
    else:
        outside = tmp_path / "outside"
        outside.mkdir()
        (root / "repos").mkdir(parents=True)
        identity = hashlib.sha256(str(repo.resolve()).encode()).hexdigest()
        (root / "repos" / identity).symlink_to(outside, target_is_directory=True)
    before = snapshot(tmp_path)
    with pytest.raises((ValueError, OSError)):
        CodeGraph(repo, graph_root=root, read_only=True)
    assert snapshot(tmp_path) == before
    assert not (repo / ".atlas").exists()


def test_external_import_and_failed_attempt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "sample.py"
    source.write_bytes(SOURCE)
    root = tmp_path / "graphs"
    graph = CodeGraph(repo, graph_root=root)
    graph.build()
    reader = CodeGraph(repo, read_only=True, graph_root=root)
    with reader.reader_lease() as lease:
        old = lease.generation_id
        source.write_text("def replacement():\n    pass\n")
        graph.build()
        assert lease.generation_id == old
        assert lease.search_symbol("s06_unique_target")["definitions"]
    valid = graph.db_path.read_bytes()
    source.write_text("def broken(:\n    pass\n")
    # Syntax recovery alone now publishes partial observations; preserve this
    # test's fatal external-publication obligation after an actual parser run.
    import tree_sitter_language_pack as pack

    original = pack.get_parser

    def fatal_provider(language):
        parser = original(language)

        class FailingParser:
            def parse(self, buffer):
                tree = parser.parse(buffer)
                assert tree.root_node.has_error
                raise LookupError("controlled provider exception after real parse")

        return FailingParser()

    monkeypatch.setattr(pack, "get_parser", fatal_provider)
    with pytest.raises(RuntimeError):
        graph.build()
    assert graph.db_path.read_bytes() == valid
    assert (graph.atlas_dir / "index-attempt.v1.json").is_file()
    assert not (repo / ".atlas").exists()
    assert (
        json.loads(invoke(repo, "status", "--graph-root", str(root)).stdout)["completeness"][
            "state"
        ]
        == "failed"
    )
    portable = tmp_path / "portable.jsonl"
    invoke(repo, "export", "--graph-root", str(root), str(portable))
    target = tmp_path / "source-free"
    target.mkdir()
    invoke(target, "import", "--graph-root", str(root), str(portable))
    imported = CodeGraph(target, read_only=True, graph_root=root)
    assert imported.search_symbol("replacement")["definitions"]
    assert imported.atlas_dir != graph.atlas_dir
    assert not (target / ".atlas").exists()
    assert not list(graph.atlas_dir.glob("*.tmp.*"))
    imported.close()
    reader.close()
    graph.close()


def test_real_git_worktree_storage_isolation(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "sample.py").write_bytes(SOURCE)

    def git(*args):
        result = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=30
        )
        assert result.returncode == 0, result.stderr

    git("init", "-q")
    git("add", "sample.py")
    git(
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "fixture",
    )
    worktree = tmp_path / "worktree"
    git("worktree", "add", "--detach", str(worktree))
    root = tmp_path / "graphs"
    first = CodeGraph(repo, graph_root=root)
    second = CodeGraph(worktree, graph_root=root)
    first.build()
    second.build()
    assert first.atlas_dir != second.atlas_dir
    assert first.generation_id != second.generation_id
    before = snapshot(second.atlas_dir)
    first.build(since="HEAD")
    assert snapshot(second.atlas_dir) == before
    first.close()
    second.close()


async def test_stdio_explicit_missing_root_rejects_decoy(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "sample.py").write_bytes(SOURCE)
    local = CodeGraph(repo)
    local.build()
    before = snapshot(repo)
    missing = tmp_path / "missing-graphs"
    response = await mcp_calls(
        sys.executable,
        [
            "-m",
            "atlas_aci",
            "serve",
            "--repo",
            str(repo),
            "--graph-root",
            str(missing),
            "--memex-root",
            str(tmp_path / "memex"),
        ],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")},
    )
    assert response["responses"]["search_symbol"]["body"]["error"] == "INDEX_UNAVAILABLE"
    assert response["responses"]["graph_query"]["body"]["error"] == "INDEX_UNAVAILABLE"
    assert not missing.exists()
    assert snapshot(repo) == before
    local.close()


@pytest.mark.parametrize("opened_before_redirect", [False, True])
def test_namespace_cannot_redirect_to_sibling(tmp_path: Path, opened_before_redirect: bool):
    root = tmp_path / "graphs"
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "sample.py").write_text("def first_only():\n    pass\n")
    (second / "sample.py").write_text("def second_only():\n    pass\n")
    sibling = CodeGraph(second, graph_root=root)
    sibling.build()
    writer = CodeGraph(first, graph_root=root) if opened_before_redirect else None
    redirected = root / "repos" / hashlib.sha256(str(first.resolve()).encode()).hexdigest()
    if redirected.exists():
        redirected.rmdir()
    redirected.symlink_to(sibling.atlas_dir, target_is_directory=True)
    before = snapshot(sibling.atlas_dir)
    with pytest.raises(ValueError, match="namespace"):
        selected = writer or CodeGraph(first, graph_root=root)
        selected.build()
    assert snapshot(sibling.atlas_dir) == before
    assert sibling.epoch_ok()
    assert sibling.search_symbol("second_only")["definitions"]
    with pytest.raises(ValueError, match="namespace"):
        CodeGraph(first, read_only=True, graph_root=root)
    if writer:
        writer.close()
    sibling.close()
