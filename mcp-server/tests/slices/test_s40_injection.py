"""ACI-S40 checker condition 1 (BLOCKER): ripgrep option injection through `search_text`.

Root cause: the user pattern used to be appended positionally, so `--pre=PROG` ran a program,
`--hostname-bin=PROG` could run one and `-fFILE` read a file as patterns. The command is now
`[... options ..., -e PATTERN, --, PATHS]` on every transport. These tests run the REAL `rg` with a
marker program (never a mock of the behaviour under test), a fake `rg` that records its argv, and
the three transports (stdio dispatch, S39 HTTP, shared mode). They also pin the argv audit.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import test_s39_support as s39
from test_s40_support import (
    BASE,
    World,
    error_code,
    fake_logger,
    fake_runs,
    get_labs,
    grant,
    tool_payload,
)

from atlas_aci.config import Config
from atlas_aci.enforcement import Enforcement, ToolError
from atlas_aci.query.authz import classify
from atlas_aci.server import dispatch_tool_call
from atlas_aci.tools import test_dry_run as dry_run_module
from atlas_aci.tools.search_text import search_text

SRC = Path(__file__).resolve().parents[2] / "src" / "atlas_aci"
REAL_RG = shutil.which("rg")


@pytest.fixture(scope="module")
def labs(tmp_path_factory):
    return get_labs(tmp_path_factory.mktemp("s40labs"))


def make_repo(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.txt").write_text("hello world\n--pre=literal\n-f\nneedle\n")
    (repo / "b.txt").write_text("another needle here\n")
    marker = tmp_path / "MARKER"
    program = tmp_path / "prog.sh"
    program.write_text(f'#!/bin/sh\ntouch "{marker}"\ncat "$1"\n')
    program.chmod(0o755)
    patterns = tmp_path / "patterns.txt"
    patterns.write_text("hello\n")
    config = Config(repo=repo, memex_root=tmp_path / "memex", max_calls_per_minute=0)
    return repo, marker, program, patterns, config


def injection_patterns(program: Path, patterns: Path) -> list[str]:
    return [
        f"--pre={program}",
        f"--hostname-bin={program}",
        f"-f{patterns}",
        f"--file={patterns}",
        "-e",
        "--files",
        "--version",
        "--pre-glob=*.sh",
        "--no-config",
    ]


def base_argv(pattern: str, path: str, *, regex: bool = True, limit: int = 50) -> list[str]:
    """The command as it was built before the fix: the pattern is positional."""
    cmd = ["rg", "--json", "--max-count", str(limit + 1), "--smart-case"]
    if not regex:
        cmd.append("--fixed-strings")
    return [*cmd, pattern, path]


async def run_search(config, pattern, scope=".", **extra):
    return await search_text(
        {"pattern": pattern, "scope": scope, **extra}, config, Enforcement(config)
    )


@pytest.mark.skipif(REAL_RG is None, reason="ripgrep is required")
async def test_the_marker_program_never_runs_and_the_pattern_is_searched_literally(tmp_path):
    repo, marker, program, patterns, config = make_repo(tmp_path)
    # Positive control: the old (positional) shape really executes the program and reads -f files.
    old = subprocess.run(
        base_argv(f"--pre={program}", str(repo / "a.txt")),
        capture_output=True,
        cwd=repo,
        timeout=30,
    )
    assert marker.exists(), "control: the base command ran the marker program"
    marker.unlink()
    old = subprocess.run(
        base_argv(f"-f{patterns}", str(repo / "a.txt")), capture_output=True, cwd=repo, timeout=30
    )
    assert b"hello world" in old.stdout, "control: the base command read the -f file as patterns"
    assert old.returncode in {0, 1}
    # The fixed command: every injection pattern is data.
    for pattern in injection_patterns(program, patterns):
        result = await run_search(config, pattern)
        assert not marker.exists(), pattern
        assert result["matches"] == [], pattern  # nothing in the repository contains it
    # The literal text is found as text, not interpreted.
    literal = await run_search(config, "--pre=literal", ".", regex=False)
    assert [m["path"] for m in literal["matches"]] == ["a.txt"] and literal["matches"][0][
        "line"
    ] == 2
    as_regex = await run_search(config, "--pre=literal")
    assert [m["path"] for m in as_regex["matches"]] == ["a.txt"]
    dash_f = await run_search(config, "-f", ".", regex=False)
    assert {(m["path"], m["line"]) for m in dash_f["matches"]} == {("a.txt", 3)}
    assert not marker.exists()


@pytest.mark.skipif(REAL_RG is None, reason="ripgrep is required")
async def test_benign_patterns_return_exactly_the_base_results(tmp_path):
    repo, _marker, _program, _patterns, config = make_repo(tmp_path)
    (repo / "sub").mkdir()
    (repo / "sub" / "c.py").write_text("def needle():\n    return Needle\n")
    cases = [
        ("needle", ".", True),
        ("NEEDLE", ".", True),
        ("Needle", "sub", True),
        ("hel+o", ".", True),
        ("hel+o", ".", False),
        ("needle", "*.txt", True),
        ("needle", "sub/*.py", True),
        ("zzz-no-match", ".", True),
        ("o w", "a.txt", False),
    ]
    for pattern, scope, regex in cases:
        scope_path = repo / scope if not any(c in scope for c in "*?[]") else None
        if scope_path is not None:
            argv = base_argv(pattern, str(scope_path), regex=regex)
        else:
            argv = [
                *base_argv(pattern, str(repo), regex=regex)[:-2],
                "-g",
                scope,
                pattern,
                str(repo),
            ]
        done = subprocess.run(argv, capture_output=True, cwd=repo, timeout=30)
        expected = []
        for line in done.stdout.splitlines():
            event = json.loads(line)
            if event.get("type") != "match":
                continue
            data = event["data"]
            for sub in data.get("submatches", []):
                expected.append(
                    {
                        "path": str(Path(data["path"]["text"]).relative_to(repo)),
                        "line": data["line_number"],
                        "col": sub["start"] + 1,
                        "preview": data["lines"]["text"].rstrip("\n")[:120],
                    }
                )
        actual = (await run_search(config, pattern, scope, regex=regex))["matches"]
        assert sorted(actual, key=json.dumps) == sorted(expected, key=json.dumps), (pattern, scope)
    positive = await run_search(config, "needle")
    assert len(positive["matches"]) >= 2  # the comparison above was not over empty sets


async def test_the_command_is_dash_e_pattern_dash_dash_paths(tmp_path, monkeypatch):
    repo, _marker, _program, _patterns, config = make_repo(tmp_path)
    directory = tmp_path / "fakebin"
    directory.mkdir()
    _script, log = fake_logger(directory, "rg")
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ['PATH']}")
    patterns = ["needle", "--pre=/bin/sh", "-f/etc/passwd", "-e", "--", "-", "a b"]
    for regex in (True, False):
        for pattern in patterns:
            await run_search(config, pattern, ".", regex=regex)
            await run_search(config, pattern, "*.txt", regex=regex)
    runs = fake_runs(log)
    expected = [
        (regex, pattern, scope)
        for regex in (True, False)
        for pattern in patterns
        for scope in (".", "*.txt")
    ]
    assert len(runs) == len(expected)
    for argv, (regex, pattern, scope) in zip(runs, expected, strict=True):
        # argv = options ... -e PATTERN -- PATH : the pattern is the value of -e and the paths
        # follow `--`, whatever the pattern looks like.
        assert argv[-4:-1] == ["-e", pattern, "--"], argv
        head = argv[:-4]
        assert head[:1] == ["--json"] and "--smart-case" in head
        assert ("--fixed-strings" in head) == (not regex)
        if scope == ".":
            assert argv[-1] == str(repo) and "-g" not in head
        else:
            assert head[-2:] == ["-g", "*.txt"] and argv[-1] == str(repo)
        assert pattern not in head  # the user pattern never appears in option position
        assert all(not part.startswith("--pre") for part in head)


@pytest.mark.skipif(REAL_RG is None, reason="ripgrep is required")
async def test_s39_http_transport_never_runs_the_program(tmp_path, labs):
    from atlas_aci.protocol import http

    _repo, marker, program, patterns, config = make_repo(tmp_path)
    app = http.build_app(config)
    async with s39.serving(app) as (host, port):
        client = s39.RawHttp(host, port)
        for pattern in injection_patterns(program, patterns):
            response = await client.tool("search_text", {"pattern": pattern, "scope": "."})
            payload = json.loads(response.rpc(1)["result"]["content"][0]["text"])
            assert payload["matches"] == [], pattern
            assert not marker.exists(), pattern
        literal = await client.tool("search_text", {"pattern": "--pre=literal", "scope": "."})
        assert json.loads(literal.rpc(1)["result"]["content"][0]["text"])["matches"]


@pytest.mark.skipif(REAL_RG is None, reason="ripgrep is required")
async def test_stdio_dispatch_never_runs_the_program(tmp_path):
    from atlas_aci.codegraph import CodeGraph
    from atlas_aci.memex import Memex

    repo, marker, program, patterns, config = make_repo(tmp_path)
    graph = CodeGraph(repo, read_only=True)
    try:
        for pattern in injection_patterns(program, patterns):
            result = await dispatch_tool_call(
                "search_text",
                {"pattern": pattern, "scope": "."},
                config,
                Enforcement(config),
                Memex(config.memex_root),
                graph,
            )
            assert result["matches"] == [] and not marker.exists(), pattern
    finally:
        graph.close()


def test_the_shared_classifier_rejects_option_like_and_non_string_patterns():
    for pattern in (
        "--pre=/bin/sh",
        "--hostname-bin=x",
        "-f/etc/passwd",
        "-e",
        "--files",
        "-",
        "-x",
    ):
        assert (
            classify("tools/call", "search_text", {"pattern": pattern}, "/r").argument == "rejected"
        )
    for pattern in (5, None, ["a"], {"a": 1}, True):
        assert (
            classify("tools/call", "search_text", {"pattern": pattern}, "/r").argument == "rejected"
        )
    for pattern in ("needle", "a-b", " -x", "x--pre", "", "\N{EN DASH}dash"):
        assert classify("tools/call", "search_text", {"pattern": pattern}, "/r").argument == "ok"


async def test_shared_mode_spawns_nothing_for_an_option_like_pattern(tmp_path, labs, monkeypatch):
    directory = tmp_path / "fakebin"
    directory.mkdir()
    _script, log = fake_logger(directory, "rg")
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ['PATH']}")
    world = World(tmp_path, labs, grants=[grant("p1", "a")])
    world.build()
    async with world.serve() as http_:
        rejected = [
            "--pre=/bin/sh",
            "--hostname-bin=/bin/sh",
            "-f/etc/passwd",
            "-e",
            "--files",
            "-",
            5,
            None,
            ["x"],
        ]
        for pattern in rejected:
            payload = tool_payload(
                await http_.tool("a", "p1", "search_text", {"pattern": pattern, "scope": "."})
            )
            assert payload["_isError"] is True and error_code(payload) in {"FORBIDDEN"}, pattern
        assert fake_runs(log) == []
        # Positive control: a benign pattern spawns the (fake) rg exactly once, with the fixed argv.
        payload = tool_payload(
            await http_.tool("a", "p1", "search_text", {"pattern": "needle", "scope": "."})
        )
        assert payload["_isError"] is False
        runs = fake_runs(log)
        assert len(runs) == 1 and runs[0][runs[0].index("-e") + 1] == "needle"
        assert runs[0][runs[0].index("-e") + 2] == "--"
    assert BASE.startswith("https://")


async def test_test_dry_run_refuses_an_option_like_case_before_any_spawn(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "test_x.py").write_text("def test_x():\n    pass\n")
    (repo / "x.test.js").write_text("test('x', () => {})\n")
    directory = tmp_path / "fakebin"
    directory.mkdir()
    logs = {}
    for name in ("pytest", "npx"):
        _script, logs[name] = fake_logger(directory, name)
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ['PATH']}")
    config = Config(
        repo=repo,
        memex_root=tmp_path / "memex",
        max_calls_per_minute=0,
        test_execution_enabled=True,
    )
    enforcement = Enforcement(config)
    for path, runner in (("test_x.py", "pytest"), ("x.test.js", "npx")):
        for case in ("--globalSetup=/tmp/x.js", "-x", "--config=evil.js", "-"):
            with pytest.raises(ToolError) as caught:
                await dry_run_module.test_dry_run({"path": path, "case": case}, config, enforcement)
            assert caught.value.code == "INVALID_INPUT"
        with pytest.raises(ToolError):
            await dry_run_module.test_dry_run({"path": path, "case": 5}, config, enforcement)  # type: ignore[dict-item]
        assert fake_runs(logs[runner]) == []
        # Positive control: a benign selector runs with the unchanged argv.
        await dry_run_module.test_dry_run({"path": path, "case": "test_name"}, config, enforcement)
        runs = fake_runs(logs[runner])
        assert len(runs) == 1 and "test_name" in runs[0]


# ----------------------------------------------------------------------------- the argv audit
AUDITED = {
    "tools/search_text.py": "create_subprocess_exec",
    "tools/test_dry_run.py": "create_subprocess_exec",
    "storage/snapshots.py": "subprocess.run",
    "history/cochange.py": "subprocess.Popen",
}


def spawn_sites() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = ast.unparse(node.func)
                if name in {
                    "asyncio.create_subprocess_exec",
                    "asyncio.create_subprocess_shell",
                    "subprocess.run",
                    "subprocess.Popen",
                    "subprocess.call",
                    "subprocess.check_call",
                    "subprocess.check_output",
                    "os.system",
                    "os.popen",
                    "os.execv",
                    "os.execvp",
                    "os.spawnv",
                }:
                    found.setdefault(str(path.relative_to(SRC)), set()).add(name.split(".")[-1])
    return found


def test_every_subprocess_call_site_is_in_the_audited_set():
    sites = spawn_sites()
    assert set(sites) == set(AUDITED), sites
    for file, call in AUDITED.items():
        assert call.split(".")[-1] in sites[file], (file, sites[file])
    audit = (
        Path(__file__).resolve().parents[3]
        / ".spectra"
        / "evidence"
        / "aci-s40"
        / "argv-audit.json"
    )
    if audit.exists():
        document = json.loads(audit.read_text())
        assert {row["file"] for row in document["sites"]} == {
            f"mcp-server/src/atlas_aci/{f}" for f in AUDITED
        }
        assert any(row["finding"] for row in document["sites"])


def test_the_three_shared_modules_never_spawn_a_process():
    for name in ("authz.py", "authn.py", "shared_http.py"):
        assert not [k for k in spawn_sites() if k.endswith(name)], name
