"""ACI-S33 supporting tests: every closed rejection, every usage error, the git contract."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from test_s19_support import build, init_git
from test_s33_support import (
    World,
    build_indexed,
    build_world,
    cli_args,
    cli_run,
    git,
    git_shim,
    git_text,
    index,
    provision_grammar,
    records_of,
    run,
    sentinel_output,
)

from atlas_aci.history import cochange as lib
from atlas_aci.storage import snapshots

UNKNOWN = "1" * 40
SNAP = "f" * 64


@pytest.fixture(scope="session")
def grammar():
    return provision_grammar()


pytestmark = pytest.mark.usefixtures("grammar")


@pytest.fixture(scope="module")
def main(tmp_path_factory) -> World:
    return build_indexed("main", tmp_path_factory.mktemp("s33r-main"))


@pytest.fixture(scope="module")
def future(tmp_path_factory) -> World:
    return build_indexed("future", tmp_path_factory.mktemp("s33r-future"))


def reason(world: World, **overrides) -> str:
    with pytest.raises(lib.HistoryRejected) as caught:
        run(world, **overrides)
    return caught.value.reason


def fake(repo: Path, **overrides) -> dict:
    """Options that name a repository but no real commit (an early stage must reject first)."""
    base = {
        "repo": repo, "repository_id": "fake", "start": "a" * 40, "end": "b" * 40,
        "source_commit": "c" * 40, "snapshot_id": SNAP,
    }  # fmt: skip
    base.update(overrides)
    return base


def test_git_contract_probe(main):
    """Prints the git in use and asserts the return-code sets and flags the analyzer relies on."""
    version = subprocess.run(["git", "--version"], capture_output=True, text=True).stdout.strip()
    print("git contract probe:", version)
    c = main.shas
    r = lib._Run(lib.normalize_options(main.options()))
    code, data, over = r.git(["rev-parse", "--is-shallow-repository", "--show-object-format"], 100)
    assert (code, data, over) == (0, b"false\nsha1\n", False)
    assert r.git(["cat-file", "-e", "--end-of-options", c["C1"] + "^{commit}"], 0)[0] == 0
    assert r.git(["cat-file", "-e", "--end-of-options", UNKNOWN + "^{commit}"], 0)[0] in {1, 128}
    for args, expected in (
        ([c["C1"], c["C14"]], 0),
        ([c["C14"], c["C1"]], 1),
    ):
        code = r.git(["merge-base", "--is-ancestor", "--end-of-options", *args], 0)[0]
        assert code == expected
    assert r.git(["merge-base", "--is-ancestor", "--end-of-options", UNKNOWN, c["C14"]], 0)[
        0
    ] not in {
        0,
        1,
    }
    listed = r.git(["rev-list", "--parents", "--end-of-options", c["C14"], "^" + c["C13"]], 4100)
    assert listed[0] == 0 and listed[1] == f"{c['C14']} {c['C13']}\n".encode()  # ^ negates
    empty = ["diff-tree", "-r", "-z", "--raw", "--no-abbrev", "--no-renames", "--no-ext-diff"]
    flags = ["--no-textconv", "--ignore-submodules=none", "--end-of-options"]
    code, data, _over = r.git([*empty, *flags, lib.EMPTY_TREE, c["C0"]], 100000)
    assert code == 0 and data.startswith(b":000000 100644 ") and data.endswith(b"\0")
    tree = r.git(["ls-tree", "-r", "-z", "--full-tree", "--end-of-options", c["C14"]], 100000)
    assert tree[0] == 0 and b"160000 commit " in tree[1]
    assert r.git(["config", "--local", "--get-regexp", lib._PROMISOR_PATTERN], 100)[0] == 1
    # the environment is built from scratch, whatever the caller exports
    assert set(lib._git_env()) == {
        "PATH", "LC_ALL", "GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL", "GIT_OPTIONAL_LOCKS",
        "GIT_TERMINAL_PROMPT", "GIT_PAGER", "GIT_NO_REPLACE_OBJECTS", "GIT_NO_LAZY_FETCH",
        "GIT_ALLOW_PROTOCOL", "GIT_GRAFT_FILE",
    }  # fmt: skip


def test_history_unavailable_from_git_itself(main, tmp_path, monkeypatch):
    refuse = git_shim(tmp_path, "refuse")
    with monkeypatch.context() as scoped:
        scoped.setenv("PATH", str(refuse) + os.pathsep + os.environ["PATH"])
        assert reason(main) == "history_unavailable"
    with monkeypatch.context() as scoped:
        scoped.setenv("PATH", str(tmp_path / "nowhere"))  # no git at all
        assert reason(main) == "history_unavailable"
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(lib.HistoryRejected) as caught:
        lib.cochange(fake(plain))
    assert caught.value.reason == "history_unavailable"
    slow = git_shim(tmp_path, "slow")
    with monkeypatch.context() as scoped:
        scoped.setenv("PATH", str(slow) + os.pathsep + os.environ["PATH"])
        started = time.monotonic()
        assert reason(main, git_timeout=1) == "history_unavailable"
        assert time.monotonic() - started < 3
        started = time.monotonic()
        assert reason(main, git_deadline=1) == "history_unavailable"  # the overall deadline
        assert time.monotonic() - started < 3
    out, sentinel = sentinel_output(tmp_path)
    with monkeypatch.context() as scoped:
        scoped.setenv("PATH", str(refuse) + os.pathsep + os.environ["PATH"])
        result = cli_run(*cli_args(main, out))
    assert result.exit_code == 1 and result.stdout_bytes == b""
    assert result.stderr == "Error: History co-change rejected: history_unavailable\n"
    assert out.read_bytes() == sentinel


def test_object_format_and_toplevel(main, tmp_path):
    sha256 = tmp_path / "sha256"
    subprocess.run(
        ["git", "init", "-q", "--object-format=sha256", str(sha256)],
        check=True,
        env={"PATH": os.environ["PATH"], "GIT_CONFIG_GLOBAL": "/dev/null"},
    )
    with pytest.raises(lib.HistoryRejected) as caught:
        lib.cochange(fake(sha256))
    assert caught.value.reason == "unsupported_object_format"
    with pytest.raises(lib.HistoryRejected) as caught:
        lib.cochange(fake(main.repo / "src"))
    assert caught.value.reason == "repository_not_toplevel"


def test_a_partial_clone_is_refused_and_no_configured_program_runs(main, tmp_path):
    world = build_world("main", tmp_path / "promisor")
    index(world, tmp_path / "graphs")
    sentinel = tmp_path / "UPLOADPACK-SENTINEL"
    script = tmp_path / "uploadpack.sh"
    script.write_text(f"#!/bin/sh\ntouch {sentinel}\nexit 1\n")
    script.chmod(0o755)
    for key, value in (
        ("extensions.partialClone", "origin"), ("remote.origin.url", "file:///nonexistent"),
        ("remote.origin.promisor", "true"), ("remote.origin.uploadpack", str(script)),
        ("core.sshCommand", str(script)), ("core.gitProxy", str(script)),
    ):  # fmt: skip
        git(world.repo, "config", key, value)
    loose = world.repo / ".git" / "objects" / world.sha("C3")[:2] / world.sha("C3")[2:]
    loose.chmod(0o644)
    loose.unlink()  # the object a lazy fetch would go for
    assert reason(world) == "history_unavailable"
    for args in (["rev-list", world.end], ["cat-file", "commit", world.sha("C3")]):
        subprocess.run(
            ["git", "-C", str(world.repo), *args],
            capture_output=True,
            env={
                "PATH": os.environ["PATH"],
                "GIT_NO_LAZY_FETCH": "1",
                "GIT_ALLOW_PROTOCOL": "none",
            },
        )
    assert not sentinel.exists()
    only = build_world("main", tmp_path / "promisor-remote")  # the remote flag alone also refuses
    git(only.repo, "config", "remote.origin.promisor", "true")
    with pytest.raises(lib.HistoryRejected) as caught:
        lib.cochange(fake(only.repo))
    assert caught.value.reason == "history_unavailable"


def test_output_invalid_from_a_garbled_diff(main, tmp_path, monkeypatch):
    shim = git_shim(tmp_path, "garble")
    with monkeypatch.context() as scoped:
        scoped.setenv("PATH", str(shim) + os.pathsep + os.environ["PATH"])
        assert reason(main) == "history_output_invalid"


def test_snapshot_unavailable(main, future, tmp_path, monkeypatch):
    assert reason(main, snapshot_id="0" * 64) == "snapshot_unavailable"
    world = build_world("future", tmp_path / "noindex")  # no graph at all
    world.graphs, world.snapshot_id = tmp_path / "nowhere", SNAP
    assert reason(world) == "snapshot_unavailable"
    # superseded by a re-index: the first snapshot id is no longer the active one
    again = build_world("future", tmp_path / "reindexed")
    first = index(again, tmp_path / "graphs-again")
    (again.repo / "src" / "x.py").write_text("def f_x():\n    return 41\n")
    changed = index(again, tmp_path / "graphs-again")
    assert changed != first
    with pytest.raises(lib.HistoryRejected) as caught:
        lib.cochange(again.options(snapshot_id=first))
    assert caught.value.reason == "snapshot_unavailable"
    # an older schema epoch (a spy on the lease)
    real = lib.runtime.open_active

    def old_epoch(stack, side):
        reader = real(stack, side)
        reader._lease_schema_epoch = max(lib.runtime.SCHEMA_EPOCHS) - 1
        return reader

    monkeypatch.setattr(lib.runtime, "open_active", old_epoch)
    assert reason(future) == "snapshot_unavailable"


def test_snapshot_repository_mismatch(tmp_path):
    # a directory indexed while it was only part of another repository, then made a repository
    other = build_world("future", tmp_path / "host-repo")
    sub = other.repo / "sub"
    sub.mkdir()
    (sub / "a.py").write_text("def a():\n    return 1\n")
    graphs = tmp_path / "graphs-sub"
    first = build(sub, graphs, ["python"])["snapshot_id"]
    assert snapshots.identity(sub)["kind"] == "git"
    git(sub, "init", "-q")
    with pytest.raises(lib.HistoryRejected) as caught:
        lib.cochange(fake(sub, snapshot_id=first, graph_root=graphs))
    assert caught.value.reason == "snapshot_repository_mismatch"
    # a directory indexed before it was a git repository
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "a.py").write_text("def a():\n    return 1\n")
    plain_id = build(plain, tmp_path / "graphs-plain", ["python"])["snapshot_id"]
    assert snapshots.identity(plain)["kind"] == "non_git"
    init_git(plain)
    with pytest.raises(lib.HistoryRejected) as caught:
        lib.cochange(fake(plain, snapshot_id=plain_id, graph_root=tmp_path / "graphs-plain"))
    assert caught.value.reason == "snapshot_repository_mismatch"
    # indexed while git refused the repository (what a foreign-owned bind mount looks like)
    refuse = git_shim(tmp_path, "refuse")
    world = build_world("future", tmp_path / "refused")
    old_path = os.environ["PATH"]
    os.environ["PATH"] = str(refuse) + os.pathsep + old_path
    try:
        refused_id = build(world.repo, tmp_path / "graphs-refused", ["python"])["snapshot_id"]
        assert snapshots.identity(world.repo)["kind"] == "git_unavailable"
    finally:
        os.environ["PATH"] = old_path
    with pytest.raises(lib.HistoryRejected) as caught:
        lib.cochange(
            fake(world.repo, snapshot_id=refused_id, graph_root=tmp_path / "graphs-refused")
        )
    assert caught.value.reason == "snapshot_repository_mismatch"


def test_snapshot_commit_mismatch_and_source_unavailable(tmp_path, monkeypatch):
    edited = build_world("future", tmp_path / "edited")
    (edited.repo / "src" / "x.py").write_text("def f_x():\n    return 'edited'\n")
    index(edited, tmp_path / "graphs-edited")
    assert reason(edited) == "snapshot_commit_mismatch"
    untracked = build_world("future", tmp_path / "untracked")
    (untracked.repo / "src" / "z.py").write_text("def z():\n    return 1\n")
    index(untracked, tmp_path / "graphs-untracked")
    assert reason(untracked) == "snapshot_commit_mismatch"
    clean = build_indexed("future", tmp_path / "clean")

    real = lib.runtime.open_active

    def unreadable(stack, side):
        reader = real(stack, side)

        def broken(*args, **kwargs):
            raise ValueError("source unavailable")

        reader._source_lease.read = broken  # the lease is fine; reading one file is not
        return reader

    monkeypatch.setattr(lib.runtime, "open_active", unreadable)
    assert reason(clean) == "snapshot_source_unavailable"


def test_tree_size_ancestry_and_unknown_commits(main, future):
    assert reason(main, max_tree_bytes=1) == "source_tree_too_large"
    assert reason(main, start=main.end) == "start_not_ancestor_of_end"  # equal
    assert reason(main, start=main.sha("C14"), end=main.sha("C1")) == "start_not_ancestor_of_end"
    assert (
        reason(future, start=future.sha("S1"), end=future.sha("F2")) == "start_not_ancestor_of_end"
    )  # unrelated branches
    assert reason(main, end=UNKNOWN) == "commit_not_found"
    assert reason(main, source_commit=UNKNOWN) == "source_commit_not_found"
    assert reason(main, start=UNKNOWN) == "commit_not_found"


def test_pair_and_overlay_limits(main):
    assert reason(main, max_pairs=1) == "pair_limit_exceeded"
    assert reason(main, max_pair_observations=1) == "pair_limit_exceeded"
    assert run(main, max_pairs=9, max_pair_observations=13)["counts"]["pairs"] == 9
    assert reason(main, max_overlay_bytes=1) == "overlay_too_large"


def test_every_cli_rejection_has_a_fixed_text(main, future, tmp_path):
    out, sentinel = sentinel_output(tmp_path)
    cases = {
        "start_not_ancestor_of_end": {"start": main.end},
        "commit_not_found": {"end": UNKNOWN},
        "source_commit_not_found": {"source_commit": UNKNOWN},
        "source_tree_too_large": {"max_tree_bytes": 1},
        "pair_limit_exceeded": {"max_pairs": 1},
        "overlay_too_large": {"max_overlay_bytes": 1},
        "snapshot_unavailable": {"snapshot_id": "0" * 64},
    }
    for name, overrides in cases.items():
        result = cli_run(*cli_args(main, out, **overrides))
        assert result.exit_code == 1 and result.stdout_bytes == b"", name
        assert result.stderr == f"Error: History co-change rejected: {name}\n", name
        assert out.read_bytes() == sentinel
    mismatch = cli_run(*cli_args(future, out, source_commit=future.sha("F3")))
    assert mismatch.stderr == "Error: History co-change rejected: snapshot_commit_mismatch\n"
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".atlas-history")] == []
    # nothing a rejection prints names a path or an input
    marker = "MARKER-" + "x" * 8
    secret = tmp_path / ("dir-" + marker)
    secret.mkdir()
    result = cli_run(*cli_args(main, secret / "o.json", snapshot_id="0" * 64))
    assert marker not in result.stderr and str(tmp_path) not in result.stderr
    assert result.stdout_bytes == b""


def test_an_unexpected_error_names_only_its_type(main, tmp_path, monkeypatch):
    secret = "SECRET-" + str(tmp_path)

    def boom(*args, **kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(lib, "cochange", boom)
    out, sentinel = sentinel_output(tmp_path)
    result = cli_run(*cli_args(main, out))
    assert result.exit_code == 1 and result.stdout_bytes == b""
    assert result.stderr == "Error: History co-change failed: RuntimeError\n"
    assert out.read_bytes() == sentinel


def test_an_unwritable_output_is_a_fixed_text(main, tmp_path):
    result = cli_run(*cli_args(main, tmp_path / "missing-dir" / "o.json"))
    assert result.exit_code == 1 and result.stdout_bytes == b""
    assert result.stderr == "Error: History overlay cannot be written.\n"
    assert not (tmp_path / "missing-dir").exists()


# ---- usage -------------------------------------------------------------------------------------


def usage(result, text):
    assert result.exit_code == 2, (result.exit_code, result.stderr)
    assert result.stdout_bytes == b""
    assert result.stderr.rstrip("\n").split("\n")[-1] == "Error: " + text


@pytest.mark.parametrize(
    "option,value",
    [
        ("start", "abc"), ("start", "A" * 40), ("start", "a" * 41), ("start", "HEAD"),
        ("start", "--x"), ("end", "a" * 39), ("source-commit", "G" * 40),
        ("snapshot-id", "a" * 63), ("snapshot-id", "A" * 64), ("repository-id", "Bad Label"),
        ("repository-id", "-x"), ("repository-id", "x" * 65),
    ],
)  # fmt: skip
def test_malformed_identifiers_are_usage(main, tmp_path, option, value):
    result = cli_run(*cli_args(main, tmp_path / "o.json", **{option: value}))
    usage(result, "Invalid co-change option: " + option.replace("-", "_"))
    assert not (tmp_path / "o.json").exists()


@pytest.mark.parametrize("name", sorted(lib.LIMITS))
def test_every_limit_is_range_checked(main, tmp_path, name):
    low, high, default = lib.LIMITS[name]
    for bad in (low - 1, high + 1):
        result = cli_run(*cli_args(main, tmp_path / "o.json", **{name: bad}))
        usage(result, "Invalid co-change option: " + name)
    for bad in (True, "7", 1.5, [1]):
        with pytest.raises(lib.HistoryUsageError) as caught:
            lib.normalize_options(main.options(**{name: bad}))
        assert caught.value.what == name
    ok = lib.normalize_options(main.options(**{name: high}))
    assert getattr(ok, name) == high and (low, default) <= (high, high)
    assert not (tmp_path / "o.json").exists()


@pytest.mark.parametrize("name", sorted(lib.POLICY_LIMITS))
def test_the_policy_parameters_are_range_checked(main, tmp_path, name):
    low, high, _default = lib.POLICY_LIMITS[name]
    for bad in (low - 1, high + 1):
        usage(
            cli_run(*cli_args(main, tmp_path / "o.json", **{name: bad})),
            "Invalid co-change option: " + name,
        )
    for bad in (True, "7"):
        with pytest.raises(lib.HistoryUsageError):
            lib.normalize_options(main.options(**{name: bad}))


@pytest.mark.parametrize(
    "option,items",
    [
        ("generated-prefix", ["../x/"]), ("generated-prefix", ["/abs/"]),
        ("generated-prefix", ["x"]), ("generated-prefix", ["./"]), ("generated-prefix", [""]),
        ("generated-name", ["a/b"]), ("generated-name", [".."]), ("generated-name", ["*.js"]),
        ("generated-suffix", ["*.js"]), ("generated-suffix", ["a b"]),
        ("generated-suffix", ["x" * 65]), ("generated-name", [f"n{i}" for i in range(257)]),
    ],
)  # fmt: skip
def test_malformed_policy_items_are_usage(main, tmp_path, option, items):
    extra = []
    for item in items:
        extra += ["--" + option, item]
    result = cli_run(*cli_args(main, tmp_path / "o.json", *extra))
    usage(result, "Invalid co-change option: " + option.replace("-", "_"))


def test_the_output_stays_outside_the_repository_and_the_graph_root(main, tmp_path):
    text = "--output must be outside --repo and the graph root."
    for target in (
        main.repo / "o.json",
        main.repo / ".atlas" / "o.json",
        main.graphs / "o.json",
        main.repo / "src" / "o.json",
    ):
        result = cli_run(*cli_args(main, target))
        usage(result, text)
    link = tmp_path / "link"
    link.symlink_to(main.repo)
    usage(cli_run(*cli_args(main, link / "o.json")), text)
    graph_link = tmp_path / "graph-link"
    graph_link.symlink_to(main.graphs)
    usage(cli_run(*cli_args(main, graph_link / "o.json")), text)
    assert not (main.repo / "o.json").exists() and not (main.graphs / "o.json").exists()


def test_a_repository_that_cannot_be_a_namespace_is_usage(main, tmp_path):
    result = cli_run(*cli_args(main, tmp_path / "o.json", graph_root="/dev/null/graphs"))
    usage(result, "The repository cannot be opened as a graph namespace.")
    assert not (tmp_path / "o.json").exists()
    with pytest.raises(lib.DiffUsageError):
        run(main, graph_root=Path("/dev/null/graphs"))


def test_unknown_options_and_types_are_usage(main):
    for bad in ({"bogus": 1}, {"repo": 3}, {"no_generated_defaults": "yes"}):
        with pytest.raises(lib.HistoryUsageError):
            lib.normalize_options(main.options(**bad))
    with pytest.raises(lib.HistoryUsageError):
        lib.normalize_options({"repo": main.repo})  # the identifiers are required
    assert json.dumps(sorted(lib.USAGE_NAMES))  # the closed set is data
    assert git_text(main.repo, "rev-parse", "--is-inside-work-tree") == "true"


def test_a_large_commit_message_does_not_defeat_the_header_check(main):
    """The header is inside the cap, so the read is a success even though the message is not."""
    tree = git_text(main.repo, "rev-parse", main.sha("C1") + "^{tree}")
    body = b"x" * 200000
    with_parent = git_text(main.repo, "commit-tree", tree, "-p", main.sha("C0"), input=body)
    root = git_text(main.repo, "commit-tree", tree, input=body)
    run_ = lib._Run(lib.normalize_options(main.options()))
    assert lib._raw_parents_present(run_, with_parent) is True
    assert lib._raw_parents_present(run_, root) is False
    assert lib._raw_parents_present(run_, UNKNOWN) is None
    # an overflowing listing is a rejection, decided before any return code is read
    _code, data, overflow = run_.git(["cat-file", "commit", root], 10)
    assert overflow and len(data) == 10


def test_oversized_bulk_and_custom_generated_filters(tmp_path):
    many = [f"src/gen/f{i}.py" for i in range(60)]
    spec = {
        "repository_id": "s33-wide", "start": "B0", "end": "B2", "source": "B2",
        "bulk_threshold": 50,
        "commits": [
            {"label": "B0", "parents": [], "touch": ["src/a.py", "src/b.py", *many]},
            {"label": "B1", "parents": ["B0"], "touch": ["src/a.py", "src/b.py", *many]},
            {"label": "B2", "parents": ["B1"], "touch": ["src/a.py", "src/b.py"]},
        ],
    }  # fmt: skip
    world = build_world("wide", tmp_path / "wide", spec)
    index(world, tmp_path / "graphs")
    pair = ("src/a.py", "src/b.py")
    plain = run(world)
    assert plain["counts"]["bulk_commits"] == 1 and plain["counts"]["oversized_commits"] == 0
    assert {k: v[0] for k, v in records_of(plain).items()} == {pair: 1}
    capped = run(world, diff_output_cap=4096)  # B1's raw diff is larger than 4096 bytes
    assert capped["counts"]["oversized_commits"] == 1 and capped["counts"]["bulk_commits"] == 0
    assert {k: v[0] for k, v in records_of(capped).items()} == {pair: 1}
    assert capped["filter"]["filter_digest"] != plain["filter"]["filter_digest"]
    custom = run(world, generated_prefix=["src/gen/"])
    assert custom["counts"]["generated_paths"] == 60 and custom["counts"]["bulk_commits"] == 0
    assert {k: v[0] for k, v in records_of(custom).items()} == {pair: 2}
    assert "src/gen/" in custom["filter"]["policy"]["generated"]["prefixes"]
    wide = run(world, bulk_threshold=1000)
    assert wide["counts"]["pair_commits"] == 2 and wide["counts"]["bulk_commits"] == 0
    assert len(wide["records"]) == 62 * 61 // 2 and records_of(wide)[pair][0] == 2
