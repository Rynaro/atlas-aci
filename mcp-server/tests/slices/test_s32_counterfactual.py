"""ACI-S32: counterfactual analysis of a proposed patch in an isolated hypothetical snapshot.

Every tree is indexed for real (git repository, external graph root) and every patch is parsed
and applied by the shipped code; nothing about the behaviour under test is mocked. The only
injected failures are the ones named in their tests (cleanup after a failure in a later stage).
"""

# ruff: noqa: E501

from __future__ import annotations

import ast
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

import pytest
from click.testing import CliRunner
from test_s19_support import build, init_git, inventory, write_tree

from atlas_aci.__main__ import cli
from atlas_aci.architecture import checker, rules
from atlas_aci.codegraph import LANG_BY_EXT, CodeGraph
from atlas_aci.config import DEFAULT_SKIP_PATTERNS
from atlas_aci.counterfactual import analyze, patch_input
from atlas_aci.counterfactual.patch_input import PatchRejected
from atlas_aci.enforcement import READ_ONLY_TOOLS, ToolError
from atlas_aci.protocol import results
from atlas_aci.query import admission, impact, impact_admission, paging
from atlas_aci.query import diff as symbol_diff
from atlas_aci.query.diff import Side
from atlas_aci.storage import evidence as ev
from atlas_aci.storage import snapshots

ROOT = Path(__file__).resolve().parents[3]
FX = ROOT / "mcp-server/tests/fixtures/counterfactual"
SRC = ROOT / "mcp-server/src/atlas_aci/counterfactual"
DOCS = ROOT / "docs/counterfactual-analysis.md"
BASE_TOOLS_SHA256 = "94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56"
BASE_TOOLS_BYTES = 1631472
PINS = {
    "introduce-import.patch": "0531be78267bb2db8b69d8d2a8b1b07f2f4ed720eb46b936f61e2df33754f46f",
    "remove-import.patch": "307ce2b3d881f041cd45218a1b3e639eea6069b942d46cbfa602b31f30ec5048",
    "introduce-second.patch": "3f6c5ad5d531b0b30258cc2d5dfa7cf47b2e3b762a314d0c1864548666b06570",
    "declarations.patch": "0c60a2810b79d01ca893788ee47d5e98e594c3f62b3b0a7dbed55945774a02a8",
    "declarations-plain.patch": "9519039c0b3adc9db4367d08eef96b329dff9651fcde1f4d72509cb6df7684d8",
    "parse-error.patch": "4a5d66485dc88a2aeafda72af413ea959cc55f0e08eb487b71aab186132541b9",
}
GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}

A, B, C, E, N = (
    "pkg/domain/a.py",
    "pkg/domain/b.py",
    "pkg/infra/c.py",
    "pkg/domain/e.py",
    "pkg/domain/n.py",
)
INIT = {"pkg/__init__.py": "", "pkg/domain/__init__.py": "", "pkg/infra/__init__.py": ""}
BASE = {
    **INIT,
    A: "from .b import fb\n\n\ndef fa():\n    return fb()\n",
    B: "def fb():\n    return 1\n",
    E: "def fe():\n    return 5\n",
    C: "from ..domain.b import fb\n\n\ndef fc():\n    return fb()\n",
}
VIOL = {**BASE, B: "from ..infra.c import fc\n\n\ndef fb():\n    return 1\n"}
DECL = {k: v for k, v in BASE.items() if k != E}
DECL.update(
    {
        B: "def fb(x=0):\n    return 1\n\n\ndef fb2():\n    return 2\n",
        C: "from ..domain.b import fb\n\n\ndef fc():\n    return fb() + 1\n",
        N: "def fn():\n    return 7\n",
    }
)
RULES = json.dumps(
    {
        "format": rules.FORMAT,
        "rules": [
            {
                "id": "domain-a-not-infra",
                "relation": "import",
                "from": [A],
                "to": ["pkg/infra/**"],
            },
            {
                "id": "domain-not-infra",
                "relation": "import",
                "from": ["pkg/domain/**"],
                "to": ["pkg/infra/**"],
            },
        ],
    }
).encode()
ORIGINAL_IDENTITY = snapshots.identity


def snapshots_identity():
    return snapshots.identity


ZERO = "0" * 64


@dataclass
class World:
    repo: Path
    root: Path
    scratch: Path
    sid: str
    gid: str

    @property
    def side(self) -> Side:
        return Side(self.repo, self.root)


def reindex(repo: Path, root: Path) -> None:
    """`atlas-aci index --since`: an incremental lineage with retained history."""
    graph = CodeGraph(repo, graph_root=root)
    try:
        graph.build("incremental")
    finally:
        graph.close()


def lease_ids(repo: Path, root: Path) -> tuple[str, str]:
    with ExitStack() as stack:
        reader = symbol_diff._lease(stack, symbol_diff._open(stack, Side(repo, root)))
        return reader.snapshot_id, reader.generation_id


def make_world(base: Path, files, name="w") -> World:
    repo = base / name
    repo.mkdir()
    init_git(repo, commit=False)
    write_tree(repo, files)
    root = base / (name + "-graph")
    build(repo, root)
    scratch = base / (name + "-scratch")
    scratch.mkdir()
    sid, gid = lease_ids(repo, root)
    return World(repo, root, scratch, sid, gid)


def fixture_bytes(name: str) -> bytes:
    return (FX / name).read_bytes()


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    return make_world(tmp_path_factory.mktemp("s32-base"), BASE)


@pytest.fixture(scope="module")
def viol(tmp_path_factory):
    return make_world(tmp_path_factory.mktemp("s32-viol"), VIOL)


def cf(world, patch, rule_bytes=None, *, scratch=None, **limits):
    return analyze.run(
        world.side,
        world.sid,
        patch,
        rule_bytes=rule_bytes,
        limits=limits or None,
        scratch_root=world.scratch if scratch is None else scratch,
    )


def invoke(world, patch_file, *extra, sid=None, graph_root=None, scratch=None):
    return CliRunner().invoke(
        cli,
        [
            "counterfactual",
            "--repo",
            str(world.repo),
            "--graph-root",
            str(world.root if graph_root is None else graph_root),
            "--base-snapshot",
            world.sid if sid is None else sid,
            "--patch",
            str(patch_file),
            "--scratch-root",
            str(world.scratch if scratch is None else scratch),
            *extra,
        ],
    )


def raw_file(tmp_path, data: bytes, name="p.patch") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def git_run(repo, *args):
    done = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, env=GIT_ENV, check=False
    )
    assert done.returncode == 0, done.stderr
    return done.stdout


def git_patch(tmp_path, before: dict, after: dict, *, executable=()) -> bytes:
    """`git diff` between two trees of bytes, exactly as the plan's oracle command."""
    repo = tmp_path / ("oracle-" + hashlib.sha256(repr((before, after)).encode()).hexdigest()[:8])
    repo.mkdir()
    git_run(repo, "init", "-q")
    git_run(repo, "config", "user.email", "o@example.invalid")
    git_run(repo, "config", "user.name", "o")

    def tree(files):
        for existing in sorted(repo.rglob("*")):
            relative = existing.relative_to(repo)
            if existing.is_file() and ".git" not in relative.parts and str(relative) not in files:
                existing.unlink()
        for name, data in files.items():
            target = repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o755 if name in executable else 0o644)

    tree(before)
    git_run(repo, "add", "-A")
    git_run(repo, "commit", "-q", "-m", "o", "--allow-empty")
    tree(after)
    git_run(repo, "add", "-A")
    return git_run(
        repo,
        "-c",
        "core.quotePath=false",
        "diff",
        "--cached",
        "--no-renames",
        "--no-ext-diff",
        "--src-prefix=a/",
        "--dst-prefix=b/",
    )


def difflib_patch(before: dict, after: dict, *, prefixes=True) -> bytes:
    out = []
    for name in sorted(set(before) | set(after)):
        one, two = before.get(name), after.get(name)
        if one == two:
            continue
        pre_a, pre_b = ("a/", "b/") if prefixes else ("", "")
        out.extend(
            difflib.unified_diff(
                [] if one is None else one.decode().splitlines(True),
                [] if two is None else two.decode().splitlines(True),
                "/dev/null" if one is None else pre_a + name,
                "/dev/null" if two is None else pre_b + name,
            )
        )
    return "".join(out).encode()


def pack_bytes(world, path):
    with ExitStack() as stack:
        reader = symbol_diff._lease(stack, symbol_diff._open(stack, world.side))
        records = {
            r["path"]: r
            for r in json.loads(
                reader.db.execute(
                    "SELECT value FROM manifest WHERE key='source_observations'"
                ).fetchone()[0]
            )
        }
        return reader.read_source(path, records[path]["observed_source_sha256"])


def staged_tree(files, patched: dict):
    merged = {**files}
    for name, content in patched.items():
        if content is None:
            merged.pop(name, None)
        else:
            merged[name] = content
    return merged


@pytest.fixture(scope="module")
def decl_pair(tmp_path_factory):
    """DECL analysed on BASE; afterwards the same checkout is edited and indexed for real."""
    world = make_world(tmp_path_factory.mktemp("s32-decl"), BASE)
    patch = fixture_bytes("declarations.patch")
    report = cf(world, patch, RULES)
    plain = cf(world, fixture_bytes("declarations-plain.patch"), RULES)
    write_tree(world.repo, {**DECL, E: None})
    real_root = world.root.parent / "decl-real"
    build(world.repo, real_root)
    return world, report, plain, real_root


# ---- AC-S32-001 -------------------------------------------------------------------------------


def test_counterfactual_identity_is_hypothetical(decl_pair, tmp_path):
    world, report, _plain, real_root = decl_pair
    patch = fixture_bytes("declarations.patch")
    first = CliRunner().invoke(
        cli,
        [
            "counterfactual",
            "--repo",
            str(world.repo),
            "--graph-root",
            str(world.root),
            "--base-snapshot",
            world.sid,
            "--patch",
            str(FX / "declarations.patch"),
            "--rules",
            str(raw_file(tmp_path, RULES, "rules.json")),
            "--scratch-root",
            str(world.scratch),
        ],
    )
    assert first.exit_code == 0, first.stderr
    assert first.stdout_bytes == analyze.canonical_bytes(report)
    hyp = report["hypothetical"]
    assert hyp["kind"] == "hypothetical" and hyp["published"] is False
    assert hyp["base_snapshot_id"] == world.sid and hyp["base_generation_id"] == world.gid
    assert hyp["patch_sha256"] == hashlib.sha256(patch).hexdigest()
    assert hyp["analyzer_version"] == "atlas-counterfactual.v1"
    compat = symbol_diff.compare(world.side, Side(world.repo, real_root))["compatibility"]
    assert hyp["configuration_sha256"] == compat["configuration_sha256"]
    assert hyp["producer_sha256"] == compat["producer_sha256"]
    assert report["compatibility"] == compat
    identity = {k: v for k, v in hyp.items() if k != "hypothetical_snapshot_id"}
    assert analyze.hypothetical_snapshot_id(identity) == hyp["hypothetical_snapshot_id"]
    assert "limits" not in identity  # analysis and admission bounds are not part of identity
    assert hyp["hypothetical_snapshot_id"] not in {world.sid, report["staged"]["snapshot_id"]}
    assert report["staged"]["published"] is False and report["staged"]["informational"] is True
    # Sensitivity: a one-byte patch change, a rebuilt base, a scratch root, an analysis bound.
    # One byte of a hunk heading: it changes the patch digest, not what the patch does.
    changed = patch.replace(b"@@ -2,4 +2,4 @@ from", b"@@ -2,4 +2,4 @@ From")
    assert changed != patch and len(changed) == len(patch)
    other = cf(world, changed)
    assert other["hypothetical"]["patch_sha256"] != hyp["patch_sha256"]
    assert other["hypothetical"]["hypothetical_snapshot_id"] != hyp["hypothetical_snapshot_id"]
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    moved = cf(world, patch, RULES, scratch=elsewhere)
    assert analyze.canonical_bytes(moved) == analyze.canonical_bytes(report)
    bound = cf(world, patch, RULES, max_changed_files=199)
    assert bound["hypothetical"] == hyp and bound["report_id"] != report["report_id"]
    assert bound["effective_limits"]["diff"]["max_changed_files"] == 199
    # Not published: neither id names a stored source snapshot; ids and scratch are unchanged.
    graph = CodeGraph(world.repo, read_only=True, graph_root=world.root)
    try:
        for name in (hyp["hypothetical_snapshot_id"], report["staged"]["snapshot_id"]):
            with pytest.raises(ToolError), graph.source_lease(name):
                pass
    finally:
        graph.close()
    assert list(world.scratch.iterdir()) == [] and list(elsewhere.iterdir()) == []
    assert lease_ids(world.repo, world.root) == (world.sid, world.gid)


def test_counterfactual_identity_changes_with_the_rebuilt_base(tmp_path):
    edited = {**BASE, A: BASE[A] + "\n\ndef fa2():\n    return 2\n"}
    one = make_world(tmp_path, BASE, "one")
    two = make_world(tmp_path, edited, "two")
    patch = fixture_bytes("declarations.patch")
    first, second = cf(one, patch), cf(two, patch)
    assert one.sid != two.sid
    assert first["hypothetical"]["base_snapshot_id"] != second["hypothetical"]["base_snapshot_id"]
    assert (
        first["hypothetical"]["hypothetical_snapshot_id"]
        != second["hypothetical"]["hypothetical_snapshot_id"]
    )


# ---- AC-S32-002 -------------------------------------------------------------------------------


def test_counterfactual_source_diff_matches_proposal(decl_pair):
    world, report, plain, real_root = decl_pair
    real = symbol_diff.canonical_bytes(symbol_diff.compare(world.side, Side(world.repo, real_root)))
    change_set = report["source_diff"]["change_set"]
    assert change_set is not None and ev.canonical(change_set).encode() + b"\n" == real
    seen = {(c["name"], c["category"]) for c in change_set["changes"]}
    assert seen == {
        ("fb", "signature"),
        ("fc", "body"),
        ("fb2", "added"),
        ("fn", "added"),
        ("fe", "removed"),
    }
    real_sid, real_gid = lease_ids(world.repo, real_root)
    assert report["staged"]["snapshot_id"] == change_set["after"]["snapshot_id"] == real_sid
    assert report["staged"]["generation_id"] == real_gid
    files = report["patch"]["files"]
    assert [(f["path"], f["op"]) for f in files] == [
        (B, "modify"),
        (E, "delete"),
        (N, "create"),
        (C, "modify"),
    ]
    for item in files:
        base_data = DECL_BASE_BYTES.get(item["path"])
        if base_data is None:
            assert item["base_sha256"] is None and item["base_bytes"] is None
        else:
            assert item["base_sha256"] == hashlib.sha256(base_data).hexdigest()
            assert item["base_bytes"] == len(base_data)
        patched = (world.repo / item["path"]) if item["op"] != "delete" else None
        if patched is None:
            assert item["patched_sha256"] is None and item["patched_bytes"] is None
        else:
            assert item["patched_sha256"] == hashlib.sha256(patched.read_bytes()).hexdigest()
    assert report["source_diff"]["change_set"] == plain["source_diff"]["change_set"]
    assert ev.canonical(plain["source_diff"]["change_set"]).encode() + b"\n" == real
    assert report["patch"]["files_total"] == 4 and report["patch"]["hunks_total"] == 4
    assert plain["patch"]["files"] == [
        {**f, "mode": None} if f["mode"] is not None else f for f in report["patch"]["files"]
    ]


DECL_BASE_BYTES = {
    B: BASE[B].encode(),
    C: BASE[C].encode(),
    E: BASE[E].encode(),
    N: None,
}


ORACLE_CASES = {
    "crlf-modify": (b"a\r\nb\r\nc\r\n", b"a\r\nB\r\nc\r\n"),
    "crlf-add-line": (b"a\r\n", b"a\r\nb\r\n"),
    "old-side-no-newline-then-append": (b"a\nb", b"a\nb\nc\n"),
    "both-sides-no-newline": (b"a\nb", b"a\nB"),
    "new-side-no-newline": (b"a\nb\n", b"a\nb\nc"),
    "add-newline-only": (b"a", b"a\n"),
    "add-last-line": (b"a\n", b"a\nb\n"),
    "remove-last-line": (b"a\nb\n", b"a\n"),
    "remove-only-line": (b"a\n", b""),
    "fill-empty-file": (b"", b"a\n"),
    "empty-file-create": (None, b""),
    "empty-file-delete": (b"", None),
    "create": (None, b"one\ntwo\n"),
    "delete": (b"one\ntwo\n", None),
    "blank-and-space-lines": (b"a\n\n  \nb\n", b"a\n\n  \nB\n"),
    "unicode": ("h\u00e9llo\\n".encode(), "h\u00e9llo w\u00f6rld\\n".encode()),
    "two-hunks": (
        b"".join(b"line %d\n" % i for i in range(1, 41)),
        b"".join(b"line %d\n" % i if i not in {3, 37} else b"LINE %d\n" % i for i in range(1, 41)),
    ),
    "whole-replace": (b"x\ny\n", b"p\nq\nr\n"),
}


def test_applier_matches_git_oracle(tmp_path):
    for label, (before, after) in ORACLE_CASES.items():
        files_before = {} if before is None else {"f.py": before}
        files_after = {} if after is None else {"f.py": after}
        raw = git_patch(tmp_path, files_before, files_after)
        if before == after:
            continue
        assert raw, label
        parsed = patch_input.parse(raw)
        assert len(parsed.files) == 1, label
        item = parsed.files[0]
        expected_op = "create" if before is None else "delete" if after is None else "modify"
        assert item.op == expected_op, label
        result = patch_input.apply(item, before)
        assert result == after, label


def test_applier_matches_git_oracle_for_executable_create(tmp_path):
    raw = git_patch(tmp_path, {}, {"run.sh": b"echo hi\n"}, executable={"run.sh"})
    assert b"new file mode 100755" in raw
    item = patch_input.parse(raw).files[0]
    assert (item.op, item.mode) == ("create", "100755")
    assert patch_input.apply(item, None) == b"echo hi\n"


def test_applier_matches_difflib_oracle(tmp_path):
    before = {"a.py": b"x\ny\nz\n", "b.py": b"1\n2\n", "c.py": b"gone\n"}
    after = {"a.py": b"x\nY\nz\nw\n", "b.py": b"1\n2\n", "d.py": b"new\n"}
    for prefixes in (True, False):
        raw = difflib_patch(before, after, prefixes=prefixes)
        if not prefixes:
            # `-p0` style headers are not accepted: the a/ and b/ prefixes are mandatory.
            with pytest.raises(PatchRejected) as info:
                patch_input.parse(raw)
            assert info.value.reason == "malformed_patch"
            continue
        parsed = patch_input.parse(raw)
        by_path = {f.path: f for f in parsed.files}
        assert set(by_path) == {"a.py", "c.py", "d.py"}
        assert patch_input.apply(by_path["a.py"], before["a.py"]) == after["a.py"]
        assert patch_input.apply(by_path["c.py"], before["c.py"]) is None
        assert patch_input.apply(by_path["d.py"], None) == after["d.py"]
        assert all(f.mode is None for f in parsed.files)


def test_static_fixtures_are_pinned_and_match_the_git_oracle(tmp_path):
    for name, pin in PINS.items():
        data = fixture_bytes(name)
        assert b"\r" not in data, name
        assert hashlib.sha256(data).hexdigest() == pin, name
    tree = {k: v.encode() for k, v in BASE.items() if v}
    viol = {k: v.encode() for k, v in VIOL.items() if v}
    decl = {k: v.encode() for k, v in DECL.items() if v}
    assert git_patch(tmp_path, tree, viol) == fixture_bytes("introduce-import.patch")
    assert git_patch(tmp_path, viol, tree) == fixture_bytes("remove-import.patch")
    assert git_patch(tmp_path, tree, decl) == fixture_bytes("declarations.patch")
    assert difflib_patch(
        {k: v.encode() for k, v in BASE.items()}, {k: v.encode() for k, v in DECL.items()}
    ) == fixture_bytes("declarations-plain.patch")


# ---- AC-S32-003 -------------------------------------------------------------------------------


def pairs(items):
    return [(p["source_path"], p["target_path"]) for p in items]


def delta(report, rule_id):
    found = [r for r in report["architecture"]["rules"] if r["id"] == rule_id]
    assert len(found) == 1
    return found[0]


@pytest.fixture(scope="module")
def intro_pair(tmp_path_factory):
    world = make_world(tmp_path_factory.mktemp("s32-intro"), BASE)
    report = cf(world, fixture_bytes("introduce-import.patch"), RULES)
    capped = cf(
        world, fixture_bytes("introduce-import.patch"), RULES, max_counterexamples_per_rule=1
    )
    write_tree(world.repo, VIOL)
    real_root = world.root.parent / "intro-real"
    build(world.repo, real_root)
    return world, report, capped, real_root


def test_counterfactual_architecture_delta(intro_pair, viol):
    world, report, capped, real_root = intro_pair
    both = delta(report, "domain-not-infra")
    assert (both["before_outcome"], both["after_outcome"]) == (
        "satisfied_within_scope",
        "violated",
    )
    assert both["status"] == "complete" and both["reasons"] == {}
    assert pairs(both["introduced"]) == [(A, C), (B, C)] and both["resolved"] == []
    assert both["persisting"] == []
    only_a = delta(report, "domain-a-not-infra")
    assert pairs(only_a["introduced"]) == [(A, C)] and only_a["resolved"] == []
    assert report["analyses"]["architecture"] == {"status": "complete", "reasons": {}}
    # The embedded reports are the S31 reports of the base and of a real build of the patched tree.
    section = report["architecture"]
    assert ev.canonical(section["reports"]["before"]).encode() + b"\n" == checker.canonical_bytes(
        checker.run(world.side, RULES)
    )
    assert ev.canonical(section["reports"]["after"]).encode() + b"\n" == checker.canonical_bytes(
        checker.run(Side(world.repo, real_root), RULES)
    )
    # VIOL + remove-import: violated -> satisfied.
    removed = cf(viol, fixture_bytes("remove-import.patch"), RULES)
    gone = delta(removed, "domain-not-infra")
    assert (gone["before_outcome"], gone["after_outcome"]) == (
        "violated",
        "satisfied_within_scope",
    )
    assert pairs(gone["resolved"]) == [(A, C), (B, C)] and gone["introduced"] == []
    assert gone["persisting"] == [] and gone["status"] == "complete"
    # VIOL + introduce-second: one new pair, two persisting.
    second = delta(cf(viol, fixture_bytes("introduce-second.patch"), RULES), "domain-not-infra")
    assert pairs(second["introduced"]) == [(E, C)]
    assert pairs(second["persisting"]) == [(A, C), (B, C)] and second["resolved"] == []
    # Incomplete-side guard: truncated witnesses never produce a claim that needs the full set.
    truncated = delta(capped, "domain-not-infra")
    assert truncated["status"] == "incomplete"
    assert truncated["reasons"] == {"counterexamples_truncated": True}
    assert pairs(truncated["introduced"]) == [(A, C)]
    assert truncated["resolved"] is None and truncated["after_exhaustive"] is False
    assert capped["analyses"]["architecture"]["status"] == "incomplete"


# ---- AC-S32-004 -------------------------------------------------------------------------------


def git_state(repo: Path) -> dict[str, str]:
    env = {**GIT_ENV, "GIT_OPTIONAL_LOCKS": "0"}

    def run(*args):
        done = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, env=env, check=True
        )
        return done.stdout.decode()

    return {
        "head": run("rev-parse", "HEAD") if (repo / ".git" / "HEAD").exists() else "",
        "refs": run("for-each-ref"),
        "status": run("status", "--porcelain"),
        "index": hashlib.sha256((repo / ".git" / "index").read_bytes()).hexdigest()
        if (repo / ".git" / "index").exists()
        else "",
    }


def test_counterfactual_original_state_unchanged(tmp_path, monkeypatch):
    world = make_world(tmp_path, BASE)
    git_run(world.repo, "add", "-A")
    git_run(world.repo, "commit", "-q", "-m", "base")
    patch = fixture_bytes("declarations.patch")
    before_edits = cf(world, patch, RULES)
    write_tree(
        world.repo,
        {
            "pkg/domain/zz.py": "from ..infra.c import fc\n\n\ndef fz():\n    return 1\n",
            B: "def fb():\n    return 999\n",
            E: None,
        },
    )
    snapshot = inventory(world.repo, world.root, world.scratch)
    state = git_state(world.repo)
    assert any(".git/" in key for key in snapshot) and len(snapshot) > 20
    ok = cf(world, patch, RULES)
    assert analyze.canonical_bytes(ok) == analyze.canonical_bytes(before_edits)
    assert ok["extraction"]["counts"]["observed"] == len(BASE) - 1 + 1
    rejected = invoke(world, FX / "remove-import.patch")
    assert rejected.exit_code == 1 and rejected.stdout_bytes == b""
    assert rejected.stderr == "Error: Counterfactual patch rejected: base_mismatch\n"

    def boom(*_args, **_kwargs):
        raise RuntimeError(str(world.repo))

    monkeypatch.setattr(impact, "change_set", boom)
    with pytest.raises(analyze.CounterfactualFailed) as info:
        cf(world, patch)
    assert info.value.reason == "staged_incompatible" and info.value.detail == "index_unavailable"
    assert str(world.repo) not in str(info.value)
    failed = invoke(world, FX / "declarations.patch")
    assert failed.exit_code == 1 and failed.stdout_bytes == b""
    assert failed.stderr == (
        "Error: Counterfactual analysis failed: staged_incompatible: index_unavailable\n"
    )
    monkeypatch.undo()
    assert inventory(world.repo, world.root, world.scratch) == snapshot
    assert git_state(world.repo) == state
    assert list(world.scratch.iterdir()) == []
    assert lease_ids(world.repo, world.root) == (world.sid, world.gid)
    # Usage: a scratch root inside the repository or inside the active graph namespace.
    for bad in (world.repo / "pkg", world.root, world.root / "repos"):
        usage = invoke(world, FX / "declarations.patch", scratch=bad)
        assert usage.exit_code == 2 and usage.stdout_bytes == b""
        assert (
            "The scratch root must be an existing directory outside the repository and its "
            "graph root." in usage.stderr
        )
    missing = invoke(world, FX / "declarations.patch", scratch=tmp_path / "missing")
    assert missing.exit_code == 2
    assert inventory(world.repo, world.root, world.scratch) == snapshot


# ---- AC-S32-005 -------------------------------------------------------------------------------


def plain(path_a, path_b, *hunk_lines, header=None):
    head = header if header is not None else f"--- {path_a}\n+++ {path_b}\n"
    return (head + "".join(line + "\n" for line in hunk_lines)).encode()


def modify(path="a/pkg/domain/b.py", new=None, *, old_text="def fb():", new_text="def fb(y):"):
    other = new if new is not None else path.replace("a/", "b/", 1)
    return plain(path, other, "@@ -1 +1 @@", "-" + old_text, "+" + new_text)


def git_section(extra, *, body=None, files=True):
    text = "diff --git a/pkg/domain/b.py b/pkg/domain/b.py\n" + extra
    if files:
        text += "--- a/pkg/domain/b.py\n+++ b/pkg/domain/b.py\n"
    return (text + (body or "@@ -1 +1 @@\n-def fb():\n+def fb(y):\n")).encode()


def many_files(count):
    out = b""
    for i in range(count):
        out += plain(f"a/pkg/f{i}.py", f"b/pkg/f{i}.py", "@@ -1 +1 @@", "-x", "+y")
    return out


P1_CASES = {
    "escape-dotdot": (modify("a/../x.py"), "invalid_path"),
    "absolute": (modify("a//etc/x.py"), "invalid_path"),
    "backslash": (modify("a/pkg\\x.py"), "invalid_path"),
    "dot-git": (modify("a/.git/x.py"), "invalid_path"),
    "control-char": (modify("a/pkg/\x01.py"), "invalid_path"),
    "quoted": (b'--- "a/pkg/x y.py"\n+++ "b/pkg/x y.py"\n@@ -1 +1 @@\n-x\n+y\n', "invalid_path"),
    "paths-differ": (modify("a/pkg/domain/b.py", "b/pkg/domain/other.py"), "invalid_path"),
    "no-prefix": (
        plain("pkg/domain/b.py", "pkg/domain/b.py", "@@ -1 +1 @@", "-x", "+y"),
        "malformed_patch",
    ),
    "symlink-create": (
        b"diff --git a/pkg/l.py b/pkg/l.py\nnew file mode 120000\nindex 0000000..1234567\n"
        b"--- /dev/null\n+++ b/pkg/l.py\n@@ -0,0 +1 @@\n+target\n",
        "symlink_change",
    ),
    "symlink-new-mode": (
        git_section("old mode 100644\nnew mode 120000\n", files=False, body=""),
        "symlink_change",
    ),
    "submodule": (
        b"diff --git a/pkg/s b/pkg/s\nnew file mode 160000\nindex 0000000..1234567\n",
        "submodule_change",
    ),
    "unsupported-mode": (
        b"diff --git a/pkg/s.py b/pkg/s.py\nnew file mode 100664\nindex 0000000..1234567\n",
        "unsupported_mode",
    ),
    "mode-only": (
        b"diff --git a/pkg/domain/b.py b/pkg/domain/b.py\nold mode 100644\nnew mode 100755\n",
        "unsupported_patch_feature",
    ),
    "rename": (
        b"diff --git a/pkg/a.py b/pkg/b.py\nsimilarity index 100%\nrename from pkg/a.py\nrename to pkg/b.py\n",
        "unsupported_patch_feature",
    ),
    "copy": (
        b"diff --git a/pkg/a.py b/pkg/b.py\nsimilarity index 100%\ncopy from pkg/a.py\ncopy to pkg/b.py\n",
        "unsupported_patch_feature",
    ),
    "git-binary": (
        b"diff --git a/pkg/x.py b/pkg/x.py\nindex 1234567..2345678 100644\nGIT binary patch\nliteral 1\n",
        "binary_patch",
    ),
    "binary-differ": (
        b"diff --git a/pkg/x.py b/pkg/x.py\nindex 1234567..2345678 100644\n"
        b"Binary files a/pkg/x.py and b/pkg/x.py differ\n",
        "binary_patch",
    ),
    "nul": (modify() + b"\x00", "binary_patch"),
    "not-utf8": (b"--- a/pkg/x.py\n+++ b/pkg/x.py\n@@ -1 +1 @@\n-x\n+\xff\xfe\n", "not_utf8"),
    "duplicate": (modify() + modify(), "duplicate_path"),
    "count-mismatch": (
        plain("a/pkg/x.py", "b/pkg/x.py", "@@ -1,2 +1 @@", "-x", "+y"),
        "malformed_patch",
    ),
    "too-many-lines": (
        plain("a/pkg/x.py", "b/pkg/x.py", "@@ -1 +1 @@", "-x", "-w", "+y"),
        "malformed_patch",
    ),
    "unordered-hunks": (
        plain("a/pkg/x.py", "b/pkg/x.py", "@@ -5 +5 @@", "-x", "+y", "@@ -1 +1 @@", "-p", "+q"),
        "malformed_patch",
    ),
    "overlapping-hunks": (
        plain(
            "a/pkg/x.py",
            "b/pkg/x.py",
            "@@ -1,2 +1,2 @@",
            "-x",
            "-w",
            "+y",
            "+v",
            "@@ -2 +2 @@",
            "-w",
            "+v",
        ),
        "malformed_patch",
    ),
    "empty-patch": (b"", "malformed_patch"),
    "no-section": (b"just a commit message\n", "malformed_patch"),
    "empty-body-line": (
        plain("a/pkg/x.py", "b/pkg/x.py", "@@ -1,2 +1,2 @@", "-x", "", "+y", " z"),
        "malformed_patch",
    ),
    "oversize": (b"x" * 262145, "limit_exceeded"),
    "thirty-three-files": (many_files(33), "limit_exceeded"),
}
P1_OPTION_CASES = {
    "long-line": (
        modify(new_text="def fb(" + "y" * 40 + "):"),
        ("--max-line-bytes", "30"),
        "limit_exceeded",
    ),
    "too-many-hunks": (
        plain("a/pkg/x.py", "b/pkg/x.py", "@@ -1 +1 @@", "-x", "+y", "@@ -5 +5 @@", "-p", "+q"),
        ("--max-hunks", "1"),
        "limit_exceeded",
    ),
}


def unsafe_fixture(name):
    return fixture_bytes("unsafe-" + name + ".patch")


def run_rejected(world, tmp_path, label, data, reason, *extra, graph_root=None):
    result = invoke(world, raw_file(tmp_path, data, "case.patch"), *extra, graph_root=graph_root)
    assert result.exit_code == 1, (label, result.stderr)
    assert result.stdout_bytes == b"", label
    assert result.stderr == f"Error: Counterfactual patch rejected: {reason}\n", label
    assert str(tmp_path) not in result.stderr and "pkg" not in result.stderr, label
    assert list(world.scratch.iterdir()) == [], label


def test_counterfactual_unsafe_patch_rejected(base, tmp_path):
    cases = {**P1_CASES}
    cases["fixture-not-utf8"] = (unsafe_fixture("not-utf8"), "not_utf8")
    cases["fixture-symlink"] = (unsafe_fixture("symlink"), "symlink_change")
    cases["fixture-escape"] = (unsafe_fixture("escape"), "invalid_path")
    cases["fixture-binary"] = (unsafe_fixture("binary"), "binary_patch")
    assert len(cases) > 30
    before = inventory(base.repo, base.root, base.scratch)
    for label, (data, reason) in cases.items():
        run_rejected(base, tmp_path, label, data, reason)
        # P0/P1 rejections fire before the lease: a nonexistent graph root changes nothing.
        run_rejected(
            base, tmp_path, label + "/no-graph", data, reason, graph_root=tmp_path / "nonexistent"
        )
    for label, (data, extra, reason) in P1_OPTION_CASES.items():
        run_rejected(base, tmp_path, label, data, reason, *extra)
    # P2: base-relative checks, rejected before anything is indexed.
    p2 = {
        "context-changed": (modify(old_text="def other():"), "base_mismatch"),
        "offset-by-one": (
            plain(
                "a/pkg/domain/b.py", "b/pkg/domain/b.py", "@@ -2 +2 @@", "-def fb():", "+def fb(y):"
            ),
            "base_mismatch",
        ),
        "wrong-index-id": (
            re.sub(
                rb"index [0-9a-f]+\.\.",
                b"index 1234567..",
                fixture_bytes("introduce-import.patch"),
                count=1,
            ),
            "base_mismatch",
        ),
        "delete-with-residual": (
            plain("a/" + E, "/dev/null", "@@ -1 +0,0 @@", "-def fe():"),
            "base_mismatch",
        ),
        "create-over-existing": (
            plain("/dev/null", "b/" + B, "@@ -0,0 +1 @@", "+def fb():"),
            "path_exists_in_base",
        ),
        "modify-missing": (modify("a/pkg/missing.py"), "path_not_in_base"),
        "create-toml": (
            plain("/dev/null", "b/pyproject.toml", "@@ -0,0 +1 @@", "+a = 1"),
            "unsupported_path",
        ),
        "create-node-modules": (
            plain("/dev/null", "b/node_modules/x.js", "@@ -0,0 +1 @@", "+var a"),
            "unsupported_path",
        ),
        "create-dotfile-suffix": (
            plain("/dev/null", "b/pkg/.py", "@@ -0,0 +1 @@", "+x = 1"),
            "unsupported_path",
        ),
        "create-below-a-file": (
            plain("/dev/null", "b/pkg/domain/a.py/x.py", "@@ -0,0 +1 @@", "+x = 1"),
            "path_exists_in_base",
        ),
        "create-over-a-directory": (
            plain("/dev/null", "b/pkg/domain", "@@ -0,0 +1 @@", "+x = 1"),
            "unsupported_path",
        ),
    }
    for label, (data, reason) in p2.items():
        run_rejected(base, tmp_path, label, data, reason)
    run_rejected(
        base,
        tmp_path,
        "result-too-large",
        fixture_bytes("declarations.patch"),
        "limit_exceeded",
        "--max-result-file-bytes",
        "16",
    )
    # Base rejections: the same no-staging assertions.
    wrong = invoke(base, FX / "declarations.patch", sid="1" * 64)
    assert wrong.exit_code == 1 and wrong.stdout_bytes == b""
    assert wrong.stderr == "Error: Counterfactual analysis rejected: base_snapshot_mismatch\n"
    assert list(base.scratch.iterdir()) == []
    assert inventory(base.repo, base.root, base.scratch) == before


def test_unsafe_patch_against_the_live_edited_checkout_is_a_base_mismatch(tmp_path):
    world = make_world(tmp_path, BASE)
    write_tree(world.repo, {B: "def fb():\n    return 999\n"})
    live = {k: v.encode() for k, v in {**BASE, B: "def fb():\n    return 999\n"}.items() if v}
    raw = git_patch(tmp_path, live, {**live, B: b"def fb():\n    return 1000\n"})
    assert b"999" in raw
    run_rejected(world, tmp_path, "live-edit", raw, "base_mismatch")


def test_exported_and_imported_generation_is_not_a_buildable_base(tmp_path):
    world = make_world(tmp_path, BASE)
    artifact = tmp_path / "export.jsonl"
    source = CodeGraph(world.repo, graph_root=world.root)
    try:
        source.export_jsonl(artifact)
    finally:
        source.close()
    imported_root = tmp_path / "imported-graph"
    target = CodeGraph(world.repo, graph_root=imported_root)
    try:
        target.import_jsonl(artifact)
    finally:
        target.close()
    imported = World(world.repo, imported_root, world.scratch, world.sid, world.gid)
    result = invoke(imported, FX / "declarations.patch")
    assert result.exit_code == 1 and result.stdout_bytes == b""
    assert result.stderr == "Error: Counterfactual analysis rejected: base_not_buildable\n"
    assert list(world.scratch.iterdir()) == []


def test_base_with_retained_history_is_not_buildable(tmp_path):
    """A failed file kept under `--since` carries a history record a fresh staging cannot rebuild."""
    repo = tmp_path / "w"
    repo.mkdir()
    init_git(repo)
    write_tree(repo, BASE)
    root = tmp_path / "g"
    build(repo, root)
    write_tree(repo, {B: "def fb(:\n"})
    reindex(repo, root)
    sid, gid = lease_ids(repo, root)
    with ExitStack() as stack:
        reader = symbol_diff._lease(stack, symbol_diff._open(stack, Side(repo, root)))
        row = reader.db.execute("SELECT value FROM manifest WHERE key='source_observations'")
        kinds = {r["type"] for r in json.loads(row.fetchone()[0])}
    assert kinds == {"observation", "history"}
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    world = World(repo, root, scratch, sid, gid)
    with pytest.raises(analyze.CounterfactualRejected) as info:
        cf(world, fixture_bytes("introduce-second.patch"))
    assert info.value.reason == "base_not_buildable" and str(info.value) == "base_not_buildable"
    assert list(scratch.iterdir()) == []
    # Once the history is gone (file restored, then a file deleted, all under --since) the
    # incremental lineage reproduces exactly and the base is accepted.
    write_tree(repo, {B: BASE[B]})
    reindex(repo, root)
    write_tree(repo, {E: None})
    reindex(repo, root)
    sid, gid = lease_ids(repo, root)
    healed = World(repo, root, scratch, sid, gid)
    report = cf(healed, plain_patch_for_healed())
    assert report["analyses"]["extraction"]["status"] == "complete"
    assert report["base"]["snapshot_id"] == sid


def plain_patch_for_healed():
    return difflib_patch({B: BASE[B].encode()}, {B: b"def fb(x=0):\n    return 1\n"})


# ---- typed failures: nothing untyped crosses the library boundary -----------------------------


def test_unavailable_source_binding_is_a_typed_rejection(tmp_path):
    world = make_world(tmp_path, BASE)
    packs = sorted((world.root / "repos").rglob("*"))
    pack_files = [p for p in packs if p.is_file() and p.stat().st_size > 100 and "pack" in p.name]
    assert pack_files, [p.name for p in packs]
    digest = hashlib.sha256(BASE[B].encode()).hexdigest()
    corrupted = 0
    for pack in pack_files:
        data = bytearray(pack.read_bytes())
        spot = data.find(BASE[B].encode())
        if spot >= 0:
            data[spot] ^= 0xFF
            pack.write_bytes(bytes(data))
            corrupted += 1
    assert corrupted == 1 and digest
    before = inventory(world.root, world.scratch)
    with pytest.raises(analyze.CounterfactualRejected) as info:
        cf(world, fixture_bytes("declarations.patch"))
    assert info.value.reason == "source_snapshot_unavailable"
    result = invoke(world, FX / "declarations.patch")
    assert result.exit_code == 1 and result.stdout_bytes == b""
    assert result.stderr == "Error: Counterfactual analysis rejected: source_snapshot_unavailable\n"
    assert inventory(world.root, world.scratch) == before


def test_diff_phase_failures_map_to_closed_reasons(base, monkeypatch):
    patch = fixture_bytes("declarations.patch")
    for exc in (ValueError("x"), RuntimeError("x"), ToolError("SOURCE_UNAVAILABLE", "x")):

        def boom(*_args, _exc=exc, **_kwargs):
            raise _exc

        monkeypatch.setattr(impact, "change_set", boom)
        with pytest.raises(analyze.CounterfactualFailed) as info:
            cf(base, patch)
        assert (info.value.reason, info.value.detail) == (
            "staged_incompatible",
            "index_unavailable",
        )
        assert list(base.scratch.iterdir()) == []
    monkeypatch.undo()
    monkeypatch.setattr(
        symbol_diff, "_check_compatible", lambda *_a: (_ for _ in ()).throw(KeyError("k"))
    )
    with pytest.raises(analyze.CounterfactualFailed) as info:
        cf(base, patch)
    assert info.value.reason == "staged_incompatible"
    monkeypatch.undo()
    monkeypatch.setattr(checker, "check", lambda *_a, **_k: (_ for _ in ()).throw(OSError("p")))
    with pytest.raises(analyze.CounterfactualFailed) as info:
        cf(base, patch, RULES)
    assert info.value.reason == "architecture_failed" and list(base.scratch.iterdir()) == []


def test_build_failure_is_a_typed_failure_and_cleans_up(base, monkeypatch):
    def boom(self, *_args, **_kwargs):
        raise RuntimeError("Cannot index eligible source " + str(base.repo))

    monkeypatch.setattr(CodeGraph, "build", boom)
    with pytest.raises(analyze.CounterfactualFailed) as info:
        cf(base, fixture_bytes("declarations.patch"))
    assert info.value.reason == "staging_failed" and str(base.repo) not in str(info.value)
    assert list(base.scratch.iterdir()) == []


def git_shim(tmp_path: Path, kind: str) -> Path:
    shim = tmp_path / ("shim-" + kind)
    shim.mkdir()
    body = {
        # What a bind mount with a flipping owner looks like to the identity probe.
        "refuse": "#!/bin/sh\necho 'fatal: detected dubious ownership in repository' >&2\nexit 128\n",
        # Slower than the 3 s identity timeout.
        "slow": "#!/bin/sh\nexec sleep 4\n",
    }[kind]
    (shim / "git").write_text(body)
    (shim / "git").chmod(0o755)
    return shim


def test_staged_identity_is_the_recorded_base_identity_not_a_live_git_probe(
    base, tmp_path, monkeypatch
):
    """Root cause of the production flake: the staged build probed git live (ownership checks and
    the 3 s timeout), so the staged identity, snapshot id and verdict could differ between runs.
    The staged build now records the base's identity and starts no git process."""
    patch = fixture_bytes("declarations.patch")
    normal = analyze.canonical_bytes(cf(base, patch, RULES))
    assert snapshots_identity() is ORIGINAL_IDENTITY
    for kind in ("refuse", "slow"):
        with monkeypatch.context() as scoped:
            scoped.setenv("PATH", str(git_shim(tmp_path, kind)) + os.pathsep + os.environ["PATH"])
            started = time.monotonic()
            report = cf(base, patch, RULES)
            assert time.monotonic() - started < 3.5, kind  # no 3 s probe wait
        assert analyze.canonical_bytes(report) == normal, kind
        assert report["compatibility"]["identity_kind"] == "git"
        assert snapshots_identity() is ORIGINAL_IDENTITY
        assert list(base.scratch.iterdir()) == []


def test_a_base_recorded_without_git_stays_comparable_when_git_works_later(tmp_path, monkeypatch):
    world = make_world(tmp_path, BASE)
    unavailable_root = tmp_path / "unavailable-graph"
    with monkeypatch.context() as scoped:
        scoped.setenv("PATH", str(git_shim(tmp_path, "refuse")) + os.pathsep + os.environ["PATH"])
        build(world.repo, unavailable_root)
        sid, gid = lease_ids(world.repo, unavailable_root)
        refused = World(world.repo, unavailable_root, world.scratch, sid, gid)
        first = cf(refused, fixture_bytes("declarations.patch"), RULES)
    assert first["compatibility"]["identity_kind"] == "git_unavailable"
    # Real git works now (it did not when the base was recorded): the outcome is the same one.
    second = cf(refused, fixture_bytes("declarations.patch"), RULES)
    assert analyze.canonical_bytes(second) == analyze.canonical_bytes(first)
    assert second["staged"]["snapshot_id"] == first["staged"]["snapshot_id"]


def test_identity_swap_is_restored_after_a_failed_build(base, monkeypatch):
    def boom(self, *_args, **_kwargs):
        raise RuntimeError("x")

    monkeypatch.setattr(CodeGraph, "build", boom)
    with pytest.raises(analyze.CounterfactualFailed):
        cf(base, fixture_bytes("declarations.patch"))
    assert snapshots_identity() is ORIGINAL_IDENTITY


def test_a_non_git_checkout_is_a_valid_base(tmp_path):
    repo = tmp_path / "plain"
    repo.mkdir()
    write_tree(repo, BASE)
    root = tmp_path / "plain-graph"
    build(repo, root)
    scratch = tmp_path / "plain-scratch"
    scratch.mkdir()
    sid, gid = lease_ids(repo, root)
    world = World(repo, root, scratch, sid, gid)
    report = cf(world, fixture_bytes("declarations.patch"), RULES)
    assert report["compatibility"]["identity_kind"] == "non_git"
    assert report["compatibility"]["identity_rule"] == "same_root"
    assert analyze.canonical_bytes(report) == analyze.canonical_bytes(
        cf(world, fixture_bytes("declarations.patch"), RULES)
    )
    assert list(scratch.iterdir()) == []


def test_live_symlink_at_a_staged_path_blocks_analysis(tmp_path):
    world = make_world(tmp_path, BASE)
    outside = tmp_path / "outside.py"
    outside.write_text("def x():\n    return 1\n")
    (world.repo / A).unlink()
    (world.repo / A).symlink_to(outside)
    with pytest.raises(analyze.CounterfactualRejected) as info:
        cf(world, fixture_bytes("declarations.patch"))
    assert info.value.reason == "live_path_is_symlink"
    result = invoke(world, FX / "declarations.patch")
    assert result.exit_code == 1 and result.stdout_bytes == b""
    assert result.stderr == "Error: Counterfactual analysis rejected: live_path_is_symlink\n"
    assert list(world.scratch.iterdir()) == []


def test_base_size_bounds_count_distinct_blobs_once(tmp_path):
    shared = "def same():\n    return 1\n"
    files = {**INIT, "pkg/domain/x1.py": shared, "pkg/domain/x2.py": shared, B: BASE[B]}
    world = make_world(tmp_path, files)
    distinct = len(shared.encode()) + len(BASE[B].encode())
    assert distinct < len(shared.encode()) * 2 + len(BASE[B].encode())
    patch = difflib_patch({B: BASE[B].encode()}, {B: b"def fb(x=0):\n    return 1\n"})
    ok = cf(world, patch, max_base_bytes=distinct)
    assert ok["effective_limits"]["base"]["max_base_bytes"] == distinct
    with pytest.raises(analyze.CounterfactualRejected) as info:
        cf(world, patch, max_base_bytes=distinct - 1)
    assert info.value.reason == "base_too_large"
    with pytest.raises(analyze.CounterfactualRejected) as info:
        cf(world, patch, max_base_files=len(files) - 1)
    assert info.value.reason == "base_too_large"
    assert list(world.scratch.iterdir()) == []


def test_analysis_rejects_a_base_that_changed_underneath(tmp_path, monkeypatch):
    world = make_world(tmp_path, BASE)
    real_build = analyze._StagingGraph.build

    def rebuild(self, *args, **kwargs):
        done = real_build(self, *args, **kwargs)
        write_tree(world.repo, {A: BASE[A] + "\n\ndef late():\n    return 1\n"})
        build(world.repo, world.root)
        return done

    monkeypatch.setattr(analyze._StagingGraph, "build", rebuild)
    with pytest.raises(analyze.CounterfactualFailed) as info:
        cf(world, fixture_bytes("declarations.patch"))
    assert info.value.reason == "base_changed"
    assert list(world.scratch.iterdir()) == []


# ---- AC-S32-006 -------------------------------------------------------------------------------


def subprocess_run(world, patch_file, rules_file, seed):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "atlas_aci",
            "counterfactual",
            "--repo",
            str(world.repo),
            "--graph-root",
            str(world.root),
            "--base-snapshot",
            world.sid,
            "--patch",
            str(patch_file),
            "--rules",
            str(rules_file),
            "--scratch-root",
            str(world.scratch),
        ],
        env={**os.environ, "PYTHONHASHSEED": seed},
        capture_output=True,
        check=False,
    )


def test_counterfactual_replay_is_idempotent(base, tmp_path):
    patch = fixture_bytes("declarations.patch")
    rules_file = raw_file(tmp_path, RULES, "rules.json")
    first = cf(base, patch, RULES)
    second = cf(base, patch, RULES)
    assert analyze.canonical_bytes(first) == analyze.canonical_bytes(second)
    cli_run = invoke(base, FX / "declarations.patch", "--rules", str(rules_file))
    assert cli_run.exit_code == 0, cli_run.stderr
    assert cli_run.stdout_bytes == analyze.canonical_bytes(first)
    other_scratch = tmp_path / "other-scratch"
    other_scratch.mkdir()
    moved = invoke(
        base, FX / "declarations.patch", "--rules", str(rules_file), scratch=other_scratch
    )
    assert moved.stdout_bytes == cli_run.stdout_bytes
    timed = invoke(base, FX / "declarations.patch", "--rules", str(rules_file), "--timing")
    assert timed.exit_code == 0 and timed.stdout_bytes == cli_run.stdout_bytes
    # .py-only fixtures: the build emits no warning after the report, so the last line is ours.
    last = timed.stderr.splitlines()[-1]
    assert re.fullmatch(r"Counterfactual timing: elapsed_ms=[0-9]+", last), timed.stderr
    outputs = []
    for seed in ("1", "2"):
        done = subprocess_run(base, FX / "declarations.patch", rules_file, seed)
        assert done.returncode == 0, done.stderr
        outputs.append(done.stdout)
    assert outputs[0] == outputs[1] == analyze.canonical_bytes(first)
    twin = tmp_path / "twin-graph"
    build(base.repo, twin)
    assert lease_ids(base.repo, twin) == (base.sid, base.gid)
    rebuilt = analyze.run(
        Side(base.repo, twin),
        base.sid,
        patch,
        rule_bytes=RULES,
        scratch_root=base.scratch,
    )
    assert analyze.canonical_bytes(rebuilt) == analyze.canonical_bytes(first)
    assert first["report_id"] == analyze.report_id(first)
    hyp = first["hypothetical"]
    identity = {k: v for k, v in hyp.items() if k != "hypothetical_snapshot_id"}
    assert analyze.hypothetical_snapshot_id(identity) == hyp["hypothetical_snapshot_id"]
    shallow = cf(base, patch, RULES, max_depth=1)
    assert shallow["report_id"] != first["report_id"]
    assert shallow["hypothetical"] == hyp
    assert list(base.scratch.iterdir()) == []


# ---- AC-S32-007 -------------------------------------------------------------------------------


def status(report, name):
    return report["analyses"][name]


def test_counterfactual_incomplete_analysis_is_visible(base, viol, tmp_path):
    # (i) a patched file that no longer parses
    broken = cf(base, fixture_bytes("parse-error.patch"), RULES)
    assert status(broken, "extraction") == {
        "status": "incomplete",
        "reasons": {"staged_extraction_failed": True},
    }
    assert broken["extraction"]["failed"] == [{"path": B, "patched": True, "reason": "parse_error"}]
    assert broken["extraction"]["failed_total"] == 1
    diff_state = status(broken, "source_diff")
    assert diff_state["status"] == "incomplete"
    assert diff_state["reasons"] == {"partial_paths": True, "unknown_changes": True}
    assert broken["source_diff"]["partial_paths_touched"] == [B]
    assert broken["source_diff"]["partial_paths_preexisting"] == []
    analyze.validate_report(broken)
    # (ii) and (iii): admission bounds are visible, never silently dropped
    declarations = fixture_bytes("declarations.patch")
    limited = cf(base, declarations, RULES, max_changed_files=0)
    assert status(limited, "source_diff") == {
        "status": "incomplete",
        "reasons": {"changed_file_limit": True},
    }
    assert (
        limited["source_diff"]["change_set"] is None and limited["source_diff"]["diff_work"] is None
    )
    assert status(limited, "extraction")["status"] == "complete"
    assert status(limited, "architecture")["status"] == "complete"
    entities = cf(base, declarations, RULES, max_diff_entities=1)
    assert status(entities, "source_diff")["reasons"] == {"diff_entity_limit": True}
    occurrences = cf(base, declarations, RULES, max_diff_occurrences=1)
    assert status(occurrences, "source_diff")["reasons"] == {"diff_occurrence_limit": True}
    # (iv) a depth bound that stops a search
    shallow = cf(base, fixture_bytes("introduce-import.patch"), RULES, max_depth=1)
    assert status(shallow, "architecture") == {
        "status": "incomplete",
        "reasons": {"rule_budget_limited": True},
    }
    stopped = delta(shallow, "domain-a-not-infra")
    assert stopped["after_exhaustive"] is False and stopped["resolved"] is None
    assert stopped["introduced"] == [] and stopped["before_exhaustive"] is True
    reverse = cf(viol, fixture_bytes("remove-import.patch"), RULES, max_depth=1)
    one_hop = delta(reverse, "domain-a-not-infra")
    assert one_hop["before_exhaustive"] is False and one_hop["introduced"] is None
    # (v) an absolute import is an unresolved boundary; a missing relative module is another
    for added in ("import json\n", "from .missing import nothing\n"):
        edited = difflib_patch({A: BASE[A].encode()}, {A: (added + BASE[A]).encode()})
        unknown = cf(base, edited, RULES)
        assert status(unknown, "architecture") == {
            "status": "incomplete",
            "reasons": {"rule_scope_unknown": True},
        }
        assert delta(unknown, "domain-not-infra")["after_outcome"] == "unknown"
        assert delta(unknown, "domain-not-infra")["resolved"] is None
    # (vi) rules are optional
    plain_run = cf(base, declarations)
    assert status(plain_run, "architecture") == {"status": "not_requested", "reasons": {}}
    assert plain_run["architecture"] is None
    # A pre-existing partial path is listed apart and does not make the diff incomplete.
    flawed = make_world(tmp_path, {**BASE, "pkg/domain/bad.py": "def f(:\n"}, "flawed")
    quiet = cf(flawed, difflib_patch({E: BASE[E].encode()}, {E: b"def fe():\n    return 6\n"}))
    assert quiet["source_diff"]["partial_paths_preexisting"] == ["pkg/domain/bad.py"]
    assert quiet["source_diff"]["partial_paths_touched"] == []
    assert status(quiet, "source_diff")["status"] == "complete"
    assert status(quiet, "extraction")["status"] == "incomplete"
    # validate_report rejects every internal contradiction.
    good = cf(base, declarations, RULES)
    analyze.validate_report(good)

    def tampered(mutate, *, reseal=True):
        copy = json.loads(json.dumps(good))
        mutate(copy)
        if reseal:
            copy["report_id"] = analyze.report_id(copy)
        return copy

    def reasons_on_complete(r):
        r["analyses"]["source_diff"]["reasons"] = {"partial_paths": True}

    def published(r):
        r["hypothetical"]["published"] = True

    def non_null(r):
        entry = r["architecture"]["rules"][0]
        entry["before_exhaustive"] = False
        entry["introduced"] = []

    def not_requested(r):
        r["analyses"]["architecture"] = {"status": "not_requested", "reasons": {}}

    def swapped_files(r):
        r["patch"]["files"].reverse()

    for mutate in (reasons_on_complete, published, non_null, not_requested, swapped_files):
        with pytest.raises(ValueError):
            analyze.validate_report(tampered(mutate))
    forged = json.loads(json.dumps(good))
    forged["report_id"] = "0" * 64
    with pytest.raises(ValueError):
        analyze.validate_report(forged)
    retitled = tampered(lambda r: r["hypothetical"].update(hypothetical_snapshot_id="1" * 64))
    with pytest.raises(ValueError):
        analyze.validate_report(retitled)


# ---- grammar ----------------------------------------------------------------------------------


def parse_one(data: bytes):
    parsed = patch_input.parse(data)
    assert len(parsed.files) == 1
    return parsed.files[0]


GRAMMAR_OK = {
    "plain-minimal": (b"--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n", b"a\n", b"b\n"),
    "count-omitted-means-one": (b"--- a/x.py\n+++ b/x.py\n@@ -1 +1,1 @@\n-a\n+b\n", b"a\n", b"b\n"),
    "section-heading-after-hunk-header": (
        b"--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@ def f():\n-a\n+b\n",
        b"a\n",
        b"b\n",
    ),
    "plain-timestamp-after-tab": (
        b"--- a/x.py\t2024-01-01 00:00:00.000000000 +0000\n"
        b"+++ b/x.py\t2024-01-02 00:00:00.000000000 +0000\n@@ -1 +1 @@\n-a\n+b\n",
        b"a\n",
        b"b\n",
    ),
    "git-header-with-index-and-mode": (
        b"diff --git a/x.py b/x.py\nindex 78981922..61780798 100644\n--- a/x.py\n+++ b/x.py\n"
        b"@@ -1 +1 @@\n-a\n+b\n",
        b"a\n",
        None,
    ),
    "preamble-and-signature": (
        b"From: someone\nSubject: [PATCH] x\n\nmessage\n---\n x.py | 2 +-\n\n"
        b"--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n-- \n2.43.0\n",
        b"a\n",
        b"b\n",
    ),
    "no-newline-after-context-binds-both-sides": (
        b"--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n-x\n+X\n last\n\\ No newline at end of file\n",
        b"x\nlast",
        b"X\nlast",
    ),
    "no-newline-between-minus-and-plus": (
        b"--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-x\n\\ No newline at end of file\n+x\n",
        b"x",
        b"x\n",
    ),
    "no-newline-trailing-after-plus": (
        b"--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-x\n+y\n\\ No newline at end of file\n",
        b"x\n",
        b"y",
    ),
    "old-side-no-newline-then-appended-line": (
        b"--- a/x.py\n+++ b/x.py\n@@ -1 +1,2 @@\n-x\n\\ No newline at end of file\n+x\n+y\n",
        b"x",
        b"x\ny\n",
    ),
    "no-newline-after-minus-then-plus-lines": (
        b"--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n-a\n-b\n\\ No newline at end of file\n+a\n+B\n",
        b"a\nb",
        b"a\nB\n",
    ),
}


def test_patch_grammar_table_accepts_and_applies_exactly():
    for label, (data, before, after) in GRAMMAR_OK.items():
        item = parse_one(data)
        result = patch_input.apply(item, before)
        if after is not None:
            assert result == after, label
    spaced = (
        b"diff --git a/dir/my file.py b/dir/my file.py\nindex 1234567..2345678 100644\n"
        b"--- a/dir/my file.py\t\n+++ b/dir/my file.py\t\n@@ -1 +1 @@\n-a\n+b\n"
    )
    item = parse_one(spaced)
    assert item.path == "dir/my file.py" and item.mode == "100644"
    mixed = (
        b"--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n"
        b"diff --git a/y.py b/y.py\n--- a/y.py\n+++ b/y.py\n@@ -1 +1 @@\n-c\n+d\n"
    )
    assert [f.path for f in patch_input.parse(mixed).files] == ["x.py", "y.py"]


GRAMMAR_BAD = {
    "marker-before-any-body-line": b"--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n\\ No newline at end of file\n-a\n+b\n",
    "duplicate-marker-for-one-line": b"--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n\\ No newline at end of file\n\\ No newline at end of file\n+b\n",
    "line-after-context-marker": b"--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n c\n\\ No newline at end of file\n-a\n+b\n",
    "second-old-line-after-minus-marker": b"--- a/x.py\n+++ b/x.py\n@@ -1,2 +1 @@\n-a\n\\ No newline at end of file\n-b\n+c\n",
    "second-new-line-after-plus-marker": b"--- a/x.py\n+++ b/x.py\n@@ -1 +1,2 @@\n-a\n+b\n\\ No newline at end of file\n+c\n",
    "hunk-without-change": b"--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n a\n",
    "missing-plus-header": b"--- a/x.py\n@@ -1 +1 @@\n-a\n+b\n",
    "git-header-path-mismatch": b"diff --git a/x.py b/y.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n",
    "dev-null-without-new-file-mode": b"diff --git a/x.py b/x.py\n--- /dev/null\n+++ b/x.py\n@@ -0,0 +1 @@\n+a\n",
    "git-only-without-mode-line": b"diff --git a/x.py b/x.py\nindex 1234567..2345678 100644\n",
    "both-dev-null": b"--- /dev/null\n+++ /dev/null\n@@ -0,0 +1 @@\n+a\n",
    "create-with-context": b"--- /dev/null\n+++ b/x.py\n@@ -0,0 +1,2 @@\n+a\n+b\n@@ -0,0 +1 @@\n+c\n",
    "hunk-header-digits": b"--- a/x.py\n+++ b/x.py\n@@ -1 +1234567890 @@\n-a\n+b\n",
    "hunk-header-unicode-digits": "--- a/x.py\n+++ b/x.py\n@@ -\u0661 +1 @@\n-a\n+b\n".encode(),
    "missing-final-newline": b"--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b",
    "junk-between-sections": b"--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\nextra\n",
    "duplicate-ext-line": b"diff --git a/x.py b/x.py\nindex 1234567..2345678 100644\nindex 1234567..2345678 100644\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n",
}


def test_patch_grammar_table_rejects_malformed_input():
    for label, data in GRAMMAR_BAD.items():
        with pytest.raises(PatchRejected) as info:
            patch_input.parse(data)
        assert info.value.reason == "malformed_patch", (label, info.value.reason)
        assert str(info.value) == "malformed_patch"


def test_patch_global_scans_run_in_the_documented_order():
    limits = {"max_patch_bytes": 100, "max_line_bytes": 20}
    cases = [
        (b"\x00" * 101, "limit_exceeded"),
        (b"\x00\xff", "binary_patch"),
        (b"\xff\nGIT binary patch\n", "not_utf8"),
        (b"GIT binary patch\n" + b"x" * 40 + b"\n", "binary_patch"),
        (b"Binary files a and b differ\n", "binary_patch"),
        (b"x" * 40 + b"\n", "limit_exceeded"),
    ]
    for data, reason in cases:
        with pytest.raises(PatchRejected) as info:
            patch_input.parse(data, limits)
        assert info.value.reason == reason, data
    for bad in ({"max_files": 33}, {"max_hunks": 0}, {"nope": 1}, {"max_line_bytes": True}):
        with pytest.raises(patch_input.PatchUsageError):
            patch_input.normalize_patch_limits(bad)


def test_applier_is_exact_without_fuzz_or_offset():
    item = parse_one(b"--- a/x.py\n+++ b/x.py\n@@ -3 +3 @@\n-c\n+C\n")
    assert patch_input.apply(item, b"a\nb\nc\n") == b"a\nb\nC\n"
    for wrong in (b"a\nc\nb\n", b"c\na\nb\n", b"a\nb\nd\n", b"a\nb\n", b"a\nb\nc"):
        with pytest.raises(PatchRejected) as info:
            patch_input.apply(item, wrong)
        assert info.value.reason == "base_mismatch", wrong
    crlf = parse_one(b"--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n-a\r\n+A\r\n b\r\n")
    assert patch_input.apply(crlf, b"a\r\nb\r\n") == b"A\r\nb\r\n"
    with pytest.raises(PatchRejected):
        patch_input.apply(crlf, b"a\nb\r\n")
    inconsistent = parse_one(b"--- a/x.py\n+++ b/x.py\n@@ -1 +3 @@\n-a\n+b\n")
    with pytest.raises(PatchRejected) as info:
        patch_input.apply(inconsistent, b"a\n")
    assert info.value.reason == "malformed_patch"
    interior = parse_one(
        b"--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n\\ No newline at end of file\n"
    )
    with pytest.raises(PatchRejected) as info:
        patch_input.apply(interior, b"a\nz\n")
    assert info.value.reason == "malformed_patch"
    big = parse_one(b"--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+bbbbbbbbbb\n")
    with pytest.raises(PatchRejected) as info:
        patch_input.apply(big, b"a\n", {"max_result_file_bytes": 4})
    assert info.value.reason == "limit_exceeded"
    git_item = parse_one(
        b"diff --git a/x.py b/x.py\nindex 1234567..2345678 100644\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n"
    )
    with pytest.raises(PatchRejected) as info:
        patch_input.apply(git_item, b"a\n")
    assert info.value.reason == "base_mismatch"
    sha1, sha256 = patch_input.git_blob_ids(b"a\n")
    assert sha1 == "78981922613b2afb6025042ff6bd878ac1994e85" and len(sha256) == 64


def test_scope_uses_build_suffix_and_component_semantics():
    observed = {"pkg/a.py", "vendor/bundle/x.py", "docs/readme.md"}
    suffixes = tuple(LANG_BY_EXT)

    def create(path):
        return patch_input.FilePatch(path, "create", None, ())

    def check(path):
        patch_input.check_scope(create(path), observed, suffixes, DEFAULT_SKIP_PATTERNS)

    check("pkg/b.py")
    check("vendor/bundled/x.py")  # skip patterns match whole components only
    check("vendor/x.py")
    for bad, reason in (
        ("pkg/.py", "unsupported_path"),
        ("pkg/x.txt", "unsupported_path"),
        ("node_modules/x.py", "unsupported_path"),
        ("a/tmp/x.py", "unsupported_path"),
        ("pkg/a.py/c.py", "path_exists_in_base"),
        ("pkg", "unsupported_path"),
        ("vendor/bundle/x.py/y.py", "path_exists_in_base"),
    ):
        with pytest.raises(PatchRejected) as info:
            check(bad)
        assert info.value.reason == reason, bad
    # A created path that is a directory prefix of an observed or another created path.
    scope = patch_input.Scope(observed, ["pkg/new.py", "pkg/new.py/inner.py"], suffixes, ())
    for path in ("pkg/new.py", "pkg/new.py/inner.py"):
        with pytest.raises(PatchRejected) as info:
            scope.check(create(path))
        assert info.value.reason == "path_exists_in_base"
    modify_missing = patch_input.FilePatch("pkg/none.py", "modify", None, ())
    with pytest.raises(PatchRejected) as info:
        scope.check(modify_missing)
    assert info.value.reason == "path_not_in_base"


def test_cli_usage_errors_exit_two_with_fixed_text(base, tmp_path):
    patch = FX / "declarations.patch"
    cases = [
        (("--max-hunks", "0"), "Invalid counterfactual limit: max_hunks"),
        (("--max-files", "33"), "Invalid counterfactual limit: max_files"),
        (("--max-depth", "4"), "Invalid counterfactual limit: max_depth"),  # no --rules
        (("--max-listed-failures", "-1"), "Invalid counterfactual limit: max_listed_failures"),
    ]
    for extra, text in cases:
        result = invoke(base, patch, *extra)
        assert result.exit_code == 2 and result.stdout_bytes == b"", extra
        assert text in result.stderr, (extra, result.stderr)
    bad = invoke(base, patch, sid="ABC")
    assert bad.exit_code == 2
    assert "--base-snapshot must be a 64-character lowercase hex snapshot id." in bad.stderr
    absent = invoke(base, tmp_path / "missing.patch")
    assert absent.exit_code == 1 and absent.stdout_bytes == b""
    assert absent.stderr == "Error: Counterfactual patch file cannot be read.\n"
    nonrules = invoke(base, patch, "--rules", str(tmp_path / "missing.json"))
    assert nonrules.exit_code == 1
    assert nonrules.stderr == "Error: Architecture rules file cannot be read.\n"
    invalid = invoke(base, patch, "--rules", str(raw_file(tmp_path, b"{}", "bad.json")))
    assert invalid.exit_code == 1 and invalid.stdout_bytes == b""
    assert invalid.stderr.startswith("Error: Architecture rules rejected: invalid_rule: ")
    assert list(base.scratch.iterdir()) == []
    # Library callers get the same closed typing.
    with pytest.raises(analyze.CounterfactualUsageError):
        cf(base, patch.read_bytes(), max_depth=4)
    with pytest.raises(analyze.CounterfactualUsageError):
        analyze.run(base.side, "not-hex", patch.read_bytes())


# ---- surface, source rules, vocabulary, documentation ----------------------------------------


def test_counterfactual_is_cli_only_and_the_tool_surface_is_unchanged(base):
    assert len(READ_ONLY_TOOLS) == 7
    assert not any("counterfactual" in name for name in READ_ONLY_TOOLS)
    out = CliRunner().invoke(cli, ["tools", "--repo", str(base.repo)], catch_exceptions=False)
    assert out.exit_code == 0
    raw = out.stdout.encode()
    assert len(raw) == BASE_TOOLS_BYTES and hashlib.sha256(raw).hexdigest() == BASE_TOOLS_SHA256
    assert "counterfactual" not in out.stdout.lower()
    assert "counterfactual" in cli.commands
    assert admission.MINIMUM_BUDGET == 3436
    assert paging.COLLECTION_MINIMUM_BUDGET == 3448
    assert results.FIXED_ERROR_ENVELOPE_CAP == 797
    assert impact_admission.IMPACT_MINIMUM_BUDGET == 7730


COMMON_BANS = {
    "os",
    "sys",
    "io",
    "shutil",
    "subprocess",
    "multiprocessing",
    "threading",
    "time",
    "datetime",
    "random",
    "uuid",
    "socket",
    "urllib",
    "http",
    "importlib",
    "atlas_aci.server",
    "atlas_aci.tools",
}
ANALYZE_BANS = COMMON_BANS - {"threading"}  # one lock serialises the identity swap
PATCH_BANS = COMMON_BANS | {"pathlib", "tempfile", "atlas_aci.codegraph"}
BANNED_CALLS = {"eval", "exec", "compile", "__import__", "IndexedReader"}
PATCH_BANNED_CALLS = BANNED_CALLS | {"open"}
ANALYZE_ALLOWED = {
    "__future__",
    "hashlib",
    "json",
    "re",
    "sqlite3",
    "tempfile",
    "threading",
    "contextlib",
    "dataclasses",
    "pathlib",
    "typing",
    "collections.abc",
    "jsonschema",
    "atlas_aci.codegraph",
    "atlas_aci.config",
    "atlas_aci.enforcement",
    "atlas_aci.query.diff",
    "atlas_aci.query.impact",
    "atlas_aci.query.impact_limits",
    "atlas_aci.architecture",
    "atlas_aci.storage.evidence",
    "atlas_aci.storage.snapshots",
    "atlas_aci.counterfactual",
}
PATCH_ALLOWED = {
    "__future__",
    "hashlib",
    "re",
    "collections.abc",
    "dataclasses",
    "pathlib",
    "atlas_aci.storage.evidence",
}


def imported(source: str) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names.add(module)
            names.update(module + "." + a.name for a in node.names)
    return names


def source_violations(source: str, bans, banned_calls, allowed=None) -> list[str]:
    found = []
    for name in imported(source):
        if any(name == b or name.startswith(b + ".") for b in bans):
            found.append("import " + name)
        if allowed is not None and not any(
            name == a or name.startswith(a + ".") or a.startswith(name + ".") for a in allowed
        ):
            found.append("unlisted " + name)
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            func = node.func
            called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if isinstance(func, ast.Name) and called in banned_calls:
                found.append("call " + called)
    return found


def test_counterfactual_modules_have_no_clock_process_or_dynamic_path():
    names = sorted(p.name for p in SRC.glob("*.py"))
    assert names == ["__init__.py", "analyze.py", "patch_input.py"]
    patch_text = (SRC / "patch_input.py").read_text()
    analyze_text = (SRC / "analyze.py").read_text()
    assert (
        source_violations(patch_text, PATCH_BANS - {"pathlib"}, PATCH_BANNED_CALLS, PATCH_ALLOWED)
        == []
    )
    assert "from pathlib import PurePosixPath" in patch_text and "Path(" not in re.sub(
        r"PurePosixPath\(", "", patch_text
    )
    assert source_violations(analyze_text, ANALYZE_BANS, BANNED_CALLS, ANALYZE_ALLOWED) == []
    assert "TemporaryDirectory" in analyze_text and "gettempdir" in analyze_text
    for text in (patch_text, analyze_text, (SRC / "__init__.py").read_text()):
        assert text.isascii()
        assert "alter table" not in text.lower() and "recursion" not in text.lower().replace(
            "recursion limit", ""
        )
    # Positive controls: the scanner fails for each kind of violation in each module.
    planted = "import time\nimport os.path\nfrom atlas_aci.tools import x\neval('1')\nopen('f')\n"
    assert {
        "import time",
        "import os.path",
        "import atlas_aci.tools",
        "call eval",
        "call open",
    } <= set(source_violations(planted, PATCH_BANS, PATCH_BANNED_CALLS, PATCH_ALLOWED))
    assert {"import shutil", "call IndexedReader", "call __import__"} <= set(
        source_violations(
            "import shutil\nIndexedReader(1)\n__import__('os')\n",
            COMMON_BANS,
            BANNED_CALLS,
            ANALYZE_ALLOWED,
        )
    )
    assert "unlisted requests" in source_violations(
        "import requests\n", set(), set(), ANALYZE_ALLOWED
    )
    assert Path(__file__).read_text().isascii()


BANNED_VOCABULARY = {"safe", "verified", "proven", "guaranteed", "compliant", "applied"}


def tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^0-9a-zA-Z]+", text.lower()) if t}


def test_report_vocabulary_does_not_overclaim(base):
    report = cf(base, fixture_bytes("declarations.patch"), RULES)
    structural = {
        "format",
        "analyzer_version",
        "kind",
        "status",
        "op",
        "reason",
        "reasons",
        "outcome",
        "before_outcome",
        "after_outcome",
        "limitations",
        "generation_kind",
    }

    def hits(node, key=None, depth=0):
        assert depth < 12
        found = set()
        if isinstance(node, dict):
            for name, value in node.items():
                found |= tokens(str(name)) & BANNED_VOCABULARY
                if name not in {"change_set", "reports"}:
                    found |= hits(value, name, depth + 1)
        elif isinstance(node, list):
            for value in node:
                found |= hits(value, key, depth + 1)
        elif isinstance(node, str) and key in structural:
            found |= tokens(node) & BANNED_VOCABULARY
        return found

    assert hits(report) == set()
    assert hits({"safe": 1, "status": "verified"}) == {"safe", "verified"}
    for token in (
        *analyze.LIMITATIONS,
        *analyze.REJECTIONS,
        *analyze.FAILURES,
        *analyze.SOURCE_DIFF_REASONS,
        *analyze.DELTA_REASONS,
        *patch_input.PATCH_REJECTIONS,
    ):
        assert not tokens(token) & BANNED_VOCABULARY, token


def test_counterfactual_docs_name_every_reason_status_limit_and_limitation():
    docs = DOCS.read_text()
    names = [
        *patch_input.PATCH_REJECTIONS,
        *analyze.REJECTIONS,
        *analyze.FAILURES,
        *analyze.STATUSES,
        *analyze.SOURCE_DIFF_REASONS,
        *analyze.EXTRACTION_REASONS,
        *analyze.DELTA_REASONS,
        *analyze.LIMITS,
        *analyze.LIMITATIONS,
        analyze.FORMAT,
        analyze.ANALYZER,
        "counterfactual",
        "hypothetical_snapshot_id",
        "partial_paths_touched",
        "partial_paths_preexisting",
        "empty_source_selection",
        "empty_target_selection",
        "lower bound",
        "--timing",
        "--scratch-root",
        "structlog",
        "core.quotePath",
        "not_utf8",
        "20000",
        "256 MiB",
        "SCIP",
        "coverage",
        "live_path_is_symlink",
        "extraction cache",
        "history",
        "identity_kind_mismatch",
    ]
    assert [n for n in names if n not in docs] == []
    assert "Counterfactual analysis rejected: base_not_buildable" in docs
    assert "Counterfactual patch rejected: base_mismatch" in docs
    assert "Counterfactual analysis failed: staged_incompatible: identity_kind_mismatch" in docs


def test_every_static_fixture_is_lf_only_and_ascii():
    patches = sorted(FX.glob("*.patch"))
    assert len(patches) >= 6
    for path in patches:
        data = path.read_bytes()
        if path.name != "unsafe-not-utf8.patch":
            assert b"\r" not in data, path.name
        if path.name in PINS:
            assert hashlib.sha256(data).hexdigest() == PINS[path.name]
    rules_doc = json.loads((FX / "rules-domain-infra.json").read_bytes())
    assert rules.load((FX / "rules-domain-infra.json").read_bytes()).rules
    assert rules_doc["format"] == rules.FORMAT


def test_git_oracle_environment_does_not_read_user_config():
    assert GIT_ENV["GIT_CONFIG_GLOBAL"] == "/dev/null" and GIT_ENV["GIT_CONFIG_NOSYSTEM"] == "1"
    assert shutil.which("git")
