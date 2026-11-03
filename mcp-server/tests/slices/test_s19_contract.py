"""ACI-S19 acceptance criteria: symbol/contract snapshot diff, real builds only."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from contextlib import ExitStack

import pytest
from click.testing import CliRunner
from test_s19_support import (
    build,
    git,
    init_git,
    inventory,
    one,
    records,
    same_path_pair,
    wipe,
    write_tree,
)

from atlas_aci.__main__ import cli
from atlas_aci.codegraph import CodeGraph
from atlas_aci.query import diff as symbol_diff
from atlas_aci.query.diff import DiffRejected, Side

PY_V1 = """class Shape:
    def area(self, w, h):
        return w * h


def area(w, h):
    return w * h


def steady(q):
    return q
"""
PY_V2 = PY_V1.replace("def area(w, h):", "def area(w, h, scale=1):")
TS_V1 = """export class Box {
  scale(w: number): number {
    return w * 2;
  }
  keep(v: number): number {
    return v;
  }
}
"""
TS_V2 = TS_V1.replace("scale(w: number)", "scale(w: string)")


def test_signature_change(tmp_path):
    change_set = _diff(
        tmp_path, {"geo.py": PY_V1, "box.ts": TS_V1}, {"geo.py": PY_V2, "box.ts": TS_V2}
    )
    # Python: the free function changed; the same-named method and `steady` did not.
    area = one(change_set, "area", "signature", path="geo.py", kind="function")
    assert area["signature_changed"] is True and area["body_changed"] is False
    assert area["before"]["signature_sha256"] != area["after"]["signature_sha256"]
    assert area["before"]["body_sha256"] == area["after"]["body_sha256"]
    assert area["before"]["fingerprint_basis"] == area["after"]["fingerprint_basis"] == "parsed"
    # One record per entity id, and the entity was neither added nor removed.
    ids = [r["entity_id"] for r in change_set["changes"]]
    assert ids and len(ids) == len(set(ids))
    assert not records(change_set, "added") and not records(change_set, "removed")
    # TypeScript parameter-type edit on a method.
    scale = one(change_set, "scale", "signature", path="box.ts", kind="method")
    assert scale["signature_changed"] is True
    assert scale["before"]["signature_sha256"] != scale["after"]["signature_sha256"]
    assert scale["body_changed"] is False
    # Negative controls: nothing else in either file is reported as a signature change.
    assert sorted(r["name"] for r in records(change_set, "signature")) == ["area", "scale"]
    assert change_set["summary"]["signature"] == 2
    assert change_set["summary"]["unchanged"] >= 3  # Shape, Shape.area, steady, Box, keep ...


BODY_V1 = """def steady(q):
    return q


def area(w, h):
    return w * h


def noted(a):
    # keep
    return a


def nest(x):
    if x:
        a()
        b()
"""
BODY_V2 = """def steady(q):
    return q


def area(w, h):
    return w * h * 2


def noted(a):
    # kept
    return  a


def nest(x):
    if x:
        a()
    b()
"""


def test_body_change(tmp_path):
    change_set = _diff(tmp_path, {"m.py": BODY_V1}, {"m.py": BODY_V2})
    area = one(change_set, "area", "body")
    assert area["before"]["signature_sha256"] == area["after"]["signature_sha256"]
    assert area["before"]["body_sha256"] != area["after"]["body_sha256"]
    assert area["signature_changed"] is False and area["body_changed"] is True
    # Control: a comment/whitespace-only edit is `format`, never `body`.
    noted = one(change_set, "noted")
    assert noted["category"] == "format"
    assert noted["text_changed"] is True and noted["body_changed"] is False
    assert noted["before"]["body_sha256"] == noted["after"]["body_sha256"]
    # Control: dedenting a statement keeps the leaf sequence but changes the structure.
    nest = one(change_set, "nest")
    assert nest["category"] == "body" and nest["signature_changed"] is False
    # Negative control: the untouched function is only counted.
    assert not records(change_set, name="steady")
    assert change_set["summary"]["body"] == 2 and change_set["summary"]["format"] == 1
    assert change_set["summary"]["signature"] == 0


LOC_V1 = """class K:
    def m(self):
        return 1


def f(a):
    return a
"""


def test_location_only(tmp_path):
    shifted = "\n\n# a module note\n\n" + LOC_V1
    inserted = 4
    change_set = _diff(
        tmp_path,
        {"loc.py": LOC_V1, "other.py": "def untouched(z):\n    return z\n"},
        {"loc.py": shifted},
    )
    found = [r for r in change_set["changes"] if r["path"] == "loc.py"]
    assert len(found) == 3  # K, K.m, f: one record each
    assert found and all(r["category"] == "location" for r in found)
    for record in found:
        before, after = record["before"], record["after"]
        assert after["line"] == before["line"] + inserted
        assert before["declaration_sha256"] == after["declaration_sha256"]
        assert before["signature_sha256"] == after["signature_sha256"]
        assert before["body_sha256"] == after["body_sha256"]
        assert before["own_text_sha256"] == after["own_text_sha256"]
        assert before["occurrence_id"] != after["occurrence_id"]
        assert record["signature_changed"] is False and record["body_changed"] is False
        assert record["text_changed"] is False and record["position_changed"] is True
    # Entity identity is position-free: same ids and ordinals on both sides.
    assert sorted(r["name"] for r in found) == ["K", "f", "m"]
    # Negative control: the file that did not change produces no record.
    assert not records(change_set, path="other.py")
    assert change_set["summary"]["location"] == 3
    assert change_set["summary"]["signature"] == change_set["summary"]["body"] == 0
    assert change_set["summary"]["added"] == change_set["summary"]["removed"] == 0


def test_diff_evidence(tmp_path):
    _repo, before, after = same_path_pair(
        tmp_path,
        {"geo.py": PY_V1, "box.ts": TS_V1},
        {"geo.py": "# top\n" + PY_V2, "box.ts": TS_V2},
    )
    change_set = symbol_diff.compare(before, after)
    both = [r for r in change_set["changes"] if r["before"] and r["after"]]
    assert len(both) >= 3
    assert {r["category"] for r in both} >= {"signature", "location"}
    with ExitStack() as stack:
        readers = []
        for side in (before, after):
            graph = CodeGraph(side.repo, read_only=True, graph_root=side.graph_root)
            stack.callback(graph.close)
            readers.append(stack.enter_context(graph.reader_lease()))
        old, new = readers
        assert old.snapshot_id and new.snapshot_id and old.snapshot_id != new.snapshot_id
        assert change_set["before"] == {
            "snapshot_id": old.snapshot_id,
            "generation_id": old.generation_id,
        }
        assert change_set["after"] == {
            "snapshot_id": new.snapshot_id,
            "generation_id": new.generation_id,
        }
        for record in both:
            for label, reader in (("before", old), ("after", new)):
                ref = record[label]
                assert ref["snapshot_id"] == reader.snapshot_id
                assert ref["generation_id"] == reader.generation_id
                source = reader.read_source(ref["path"], ref["source_sha256"])
                span = source[ref["start_byte"] : ref["end_byte"]]
                assert hashlib.sha256(span).hexdigest() == ref["declaration_sha256"]
                occurrence = reader.db.execute(
                    "SELECT payload FROM occurrences WHERE id=?", (ref["occurrence_id"],)
                ).fetchone()
                assert occurrence is not None
                payload = json.loads(occurrence[0])
                assert payload["entity_id"] == record["entity_id"]
                assert payload["role"] == "definition:def." + record["kind"]
                proof = reader.db.execute(
                    "SELECT payload FROM evidence WHERE id=?", (ref["evidence_id"],)
                ).fetchone()
                assert proof is not None
                assert json.loads(proof[0])["occurrence_id"] == ref["occurrence_id"]
            assert record["before"]["snapshot_id"] != record["after"]["snapshot_id"]


def test_incompatible_repositories(tmp_path):
    files = {"a.py": "def f(a):\n    return a\n"}
    # Two unrelated git repositories with identical content.
    one_repo, two_repo = tmp_path / "one", tmp_path / "two"
    for repo in (one_repo, two_repo):
        repo.mkdir()
        init_git(repo)
        write_tree(repo, files)
    build(one_repo, tmp_path / "g1")
    build(two_repo, tmp_path / "g2")
    # Two distinct non-git directories, and one git against one non-git directory.
    plain_one, plain_two = tmp_path / "plain1", tmp_path / "plain2"
    for repo in (plain_one, plain_two):
        repo.mkdir()
        write_tree(repo, files)
    build(plain_one, tmp_path / "g3")
    build(plain_two, tmp_path / "g4")
    trees = [tmp_path / name for name in ("g1", "g2", "g3", "g4")]
    watched = [*trees, one_repo, two_repo, plain_one, plain_two]
    snapshot = inventory(*watched)

    def reason(first, second):
        with pytest.raises(DiffRejected) as info:
            symbol_diff.compare(first, second)
        return info.value.reason

    assert reason(Side(one_repo, trees[0]), Side(two_repo, trees[1])) == (
        "repository_identity_mismatch"
    )
    assert reason(Side(plain_one, trees[2]), Side(plain_two, trees[3])) == (
        "repository_identity_mismatch"
    )
    assert reason(Side(one_repo, trees[0]), Side(plain_one, trees[2])) == "identity_kind_mismatch"
    result = CliRunner().invoke(
        cli,
        [
            "diff",
            "--before-repo",
            str(one_repo),
            "--after-repo",
            str(two_repo),
            "--before-graph-root",
            str(trees[0]),
            "--after-graph-root",
            str(trees[1]),
        ],
    )
    assert result.exit_code == 1 and result.stdout == ""
    assert "repository_identity_mismatch" in result.output
    assert inventory(*watched) == snapshot  # G-RO: rejection writes nothing anywhere
    # Positive control 1: a worktree pair sharing one graph root (same common dir).
    primary = tmp_path / "primary"
    primary.mkdir()
    init_git(primary)
    write_tree(primary, files)
    git(primary, "add", "-A")
    git(primary, "commit", "-q", "-m", "fixture")
    linked = tmp_path / "linked"
    git(primary, "worktree", "add", "-q", "--detach", str(linked), "HEAD")
    try:
        write_tree(linked, {"a.py": "def f(a, b):\n    return a\n"})
        shared = tmp_path / "shared"
        build(primary, shared)
        build(linked, shared)
        before_tree = inventory(shared, primary, linked)
        change_set = symbol_diff.compare(Side(primary, shared), Side(linked, shared))
        assert change_set["compatibility"]["identity_rule"] == "git_common_dir"
        assert one(change_set, "f", "signature")
        assert inventory(shared, primary, linked) == before_tree
    finally:
        git(primary, "worktree", "remove", "--force", str(linked))
    # Positive control 2: the same path indexed into two roots.
    roomy = tmp_path / "roomy"
    roomy.mkdir()
    _repo, before, after = same_path_pair(
        roomy, {"a.py": files["a.py"]}, {"a.py": files["a.py"] + "\n"}
    )
    assert symbol_diff.compare(before, after)["format"] == "atlas-symbol-diff.v1"


def test_diff_replay(tmp_path):
    first = {"geo.py": PY_V1, "box.ts": TS_V1}
    second = {"geo.py": "# top\n" + PY_V2, "box.ts": TS_V2}
    repo, before, after = same_path_pair(tmp_path, first, second)
    once = symbol_diff.compare(before, after)
    again = symbol_diff.compare(before, after)
    assert once["changes"] and once == again
    assert symbol_diff.canonical_bytes(once) == symbol_diff.canonical_bytes(again)
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "atlas_aci",
            "diff",
            "--repo",
            str(repo),
            "--before-graph-root",
            str(before.graph_root),
            "--after-graph-root",
            str(after.graph_root),
        ],
        capture_output=True,
    )
    assert process.returncode == 0, process.stderr
    assert process.stdout == symbol_diff.canonical_bytes(once)
    # Rebuild both sides at the same path into fresh roots: same ids, same change set.
    wipe(before.graph_root, after.graph_root)
    write_tree(repo, first)
    build(repo, tmp_path / "fresh-before")
    first_ids = _ids(tmp_path / "fresh-before", repo)
    write_tree(repo, second)
    build(repo, tmp_path / "fresh-after")
    second_ids = _ids(tmp_path / "fresh-after", repo)
    rebuilt = symbol_diff.compare(
        Side(repo, tmp_path / "fresh-before"), Side(repo, tmp_path / "fresh-after")
    )
    assert first_ids == (once["before"]["snapshot_id"], once["before"]["generation_id"])
    assert second_ids == (once["after"]["snapshot_id"], once["after"]["generation_id"])
    assert rebuilt["id"] == once["id"]
    assert symbol_diff.canonical_bytes(rebuilt) == symbol_diff.canonical_bytes(once)


def _ids(root, repo):
    graph = CodeGraph(repo, read_only=True, graph_root=root)
    try:
        with graph.reader_lease() as reader:
            return reader.snapshot_id, reader.generation_id
    finally:
        graph.close()


def _diff(tmp_path, first, second):
    _repo, before, after = same_path_pair(tmp_path, first, second)
    return symbol_diff.compare(before, after)
