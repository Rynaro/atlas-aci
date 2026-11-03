"""ACI-S19 shared fixtures: real indexer builds into real graph namespaces."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from atlas_aci.codegraph import CodeGraph
from atlas_aci.query import diff as symbol_diff
from atlas_aci.query.diff import Side


def git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def write_tree(repo: Path, files: dict[str, str | bytes | None]) -> None:
    """Make `repo` hold exactly `files` among the paths named; None deletes."""
    for name, content in files.items():
        target = repo / name
        if content is None:
            target.unlink(missing_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode() if isinstance(content, str) else content)


def init_git(repo: Path, *, commit: bool = False) -> None:
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "config", "user.name", "fixture")
    if commit:
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "fixture")


def build(repo: Path, root: Path, langs: list[str] | None = None) -> dict:
    graph = CodeGraph(repo, graph_root=root, langs=langs)
    try:
        return graph.build()
    finally:
        graph.close()


def same_path_pair(
    tmp_path: Path,
    first: dict[str, str | bytes | None],
    second: dict[str, str | bytes | None],
    *,
    repo_name: str = "repo",
    git_repo: bool = True,
    langs: list[str] | None = None,
) -> tuple[Path, Side, Side]:
    """Index `first`, edit the same checkout to `second`, index into a second root."""
    repo = tmp_path / repo_name
    repo.mkdir()
    if git_repo:
        init_git(repo)
    write_tree(repo, first)
    build(repo, tmp_path / "root-before", langs)
    write_tree(repo, second)
    build(repo, tmp_path / "root-after", langs)
    return repo, Side(repo, tmp_path / "root-before"), Side(repo, tmp_path / "root-after")


def diff(tmp_path, first, second, **kwargs):
    _repo, before, after = same_path_pair(tmp_path, first, second, **kwargs)
    return symbol_diff.compare(before, after)


def records(change_set, category=None, **match):
    found = [
        r
        for r in change_set["changes"]
        if (category is None or r["category"] == category)
        and all(r[k] == v for k, v in match.items())
    ]
    return found


def one(change_set, name, category=None, **match):
    found = records(change_set, category, name=name, **match)
    assert len(found) == 1, (name, change_set["changes"])
    return found[0]


def inventory(*roots: Path) -> dict[str, str]:
    """SHA-256 of every file (and empty directory) under the given trees."""
    result: dict[str, str] = {}
    for root in roots:
        if not root.exists():
            result[str(root)] = "<absent>"
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and not path.is_symlink():
                result[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
            elif path.is_dir():
                result.setdefault(str(path) + "/", "<dir>")
    return result


def decode(change_set) -> str:
    return json.dumps(change_set, sort_keys=True)


def wipe(*paths: Path) -> None:
    for path in paths:
        shutil.rmtree(path, ignore_errors=True)
