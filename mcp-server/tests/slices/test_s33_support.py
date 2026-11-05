"""ACI-S33 shared fixtures: plumbing-built git worlds, real indexer builds and a spec oracle.

Repositories are built in `tmp_path` from the JSON specs in `tests/fixtures/cochange/history.json`
with git plumbing only (`hash-object`, `update-index --index-info`, `write-tree`, `commit-tree`,
`update-ref`): there is no working tree while building and no hook runs. The final checkout is a
`reset --hard` of the source commit. No golden overlay exists because snapshot ids depend on the
absolute path of the repository.

This module imports only the standard library and the indexer, so the production-image scenario
can load it by path (`make-history.py`).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
FX = ROOT / "mcp-server/tests/fixtures/cochange"
SPEC = json.loads((FX / "history.json").read_text())
TIMESTAMP = 1700000000
IDENT = "Atlas Fixture"
EMAIL = "fixture@example.invalid"
BUILD_CONFIG = [
    "-c", "init.defaultBranch=main",
    "-c", "core.hooksPath=/dev/null",
    "-c", "gc.auto=0",
    "-c", "core.autocrlf=false",
    "-c", "commit.gpgSign=false",
]  # fmt: skip
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def clean_env(**extra: str) -> dict[str, str]:
    """No inherited git variable and no HOME; only PATH and the fixed identity."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LC_ALL": "C",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": IDENT,
        "GIT_AUTHOR_EMAIL": EMAIL,
        "GIT_COMMITTER_NAME": IDENT,
        "GIT_COMMITTER_EMAIL": EMAIL,
    }
    env.update(extra)
    return env


def git(repo: Path | str, *args: str, input: bytes | None = None, env=None, check=True) -> bytes:
    """Run `git -C repo ...` in the clean environment and return stdout bytes."""
    argv = ["git", *BUILD_CONFIG, "-C", str(repo), *args]
    result = subprocess.run(
        argv, input=input, capture_output=True, env=env if env is not None else clean_env()
    )
    if check:
        assert result.returncode == 0, (args, result.stderr.decode("utf-8", "replace"))
    return result.stdout


def git_text(repo: Path | str, *args: str, **kwargs: Any) -> str:
    return git(repo, *args, **kwargs).decode("utf-8").strip()


# ---- the spec ----------------------------------------------------------------------------------

Entry = tuple[str, Any]  # ("blob", bytes) or ("gitlink", label)
State = dict[bytes, Entry]


def content_of(path: bytes, label: str, index: int) -> bytes:
    """Deterministic content of a touched path: valid Python for .py, plain text otherwise."""
    if path.endswith(b".py"):
        stem = path.rsplit(b"/", 1)[-1][:-3].decode("ascii")
        return f"def f_{stem}():\n    return {index}\n".encode()
    return label.encode() + b" " + path + b"\n"


def evolve(spec: dict[str, Any]) -> dict[str, State]:
    """The path -> entry state of every commit, from its first parent and its listed changes."""
    states: dict[str, State] = {}
    for index, commit in enumerate(spec["commits"]):
        label = commit["label"]
        parents = commit["parents"]
        state: State = dict(states[parents[0]]) if parents else {}
        for source, paths in commit.get("copy_from", {}).items():
            for path in paths:
                state[path.encode()] = states[source][path.encode()]
        for path in commit.get("touch", []):
            raw = path.encode()
            state[raw] = ("blob", content_of(raw, label, index))
        for path in commit.get("touch_hex", []):
            raw = bytes.fromhex(path)
            state[raw] = ("blob", content_of(raw, label, index))
        for path in commit.get("delete", []):
            del state[path.encode()]
        for path in commit.get("delete_hex", []):
            del state[bytes.fromhex(path)]
        for old, new in commit.get("rename", {}).items():
            state[new.encode()] = state.pop(old.encode())
        for path, target in commit.get("gitlink", {}).items():
            state[path.encode()] = ("gitlink", target)
        states[label] = state
    return states


@dataclass
class World:
    """One built repository: its path, the label -> sha map and the spec it came from."""

    name: str
    repo: Path
    spec: dict[str, Any]
    shas: dict[str, str]
    labels: dict[str, str] = field(default_factory=dict)
    graphs: Path | None = None
    snapshot_id: str | None = None

    def sha(self, label: str) -> str:
        return self.shas[label]

    @property
    def start(self) -> str:
        return self.shas[self.spec["start"]]

    @property
    def end(self) -> str:
        return self.shas[self.spec["end"]]

    @property
    def source(self) -> str:
        return self.shas[self.spec["source"]]

    def options(self, **overrides: Any) -> dict[str, Any]:
        """Library options of the world's default window (the snapshot must have been built)."""
        assert self.graphs is not None and self.snapshot_id is not None
        base: dict[str, Any] = {
            "repo": self.repo,
            "repository_id": self.spec["repository_id"],
            "start": self.start,
            "end": self.end,
            "source_commit": self.source,
            "snapshot_id": self.snapshot_id,
            "graph_root": self.graphs,
            "bulk_threshold": self.spec["bulk_threshold"],
        }
        base.update(overrides)
        return base


def _write_blob(repo: Path, data: bytes) -> str:
    return git_text(repo, "hash-object", "-w", "--stdin", input=data)


def build_world(name: str, dest: Path, spec: dict[str, Any] | None = None) -> World:
    """Build the named world in `dest` (created); the commit ids are deterministic."""
    spec = spec if spec is not None else SPEC["worlds"][name]
    dest.mkdir(parents=True)
    subprocess.run(["git", *BUILD_CONFIG, "init", "-q", str(dest)], check=True, env=clean_env())
    states = evolve(spec)
    shas: dict[str, str] = {}
    blobs: dict[bytes, str] = {}
    index_file = dest.parent / (dest.name + ".build-index")
    for position, commit in enumerate(spec["commits"]):
        label = commit["label"]
        lines = bytearray()
        for path, (kind, payload) in sorted(states[label].items()):
            if kind == "blob":
                oid = blobs.get(payload)
                if oid is None:
                    oid = blobs[payload] = _write_blob(dest, payload)
                mode = "100644"
            else:
                oid, mode = shas[payload], "160000"
            lines += f"{mode} {oid}\t".encode() + path + b"\0"
        env = clean_env(
            GIT_INDEX_FILE=str(index_file),
            GIT_AUTHOR_DATE=f"@{TIMESTAMP + position} +0000",
            GIT_COMMITTER_DATE=f"@{TIMESTAMP + position} +0000",
        )
        index_file.unlink(missing_ok=True)
        git(dest, "update-index", "--add", "-z", "--index-info", input=bytes(lines), env=env)
        tree = git_text(dest, "write-tree", env=env)
        parent_args = [item for parent in commit["parents"] for item in ("-p", shas[parent])]
        shas[label] = git_text(dest, "commit-tree", tree, *parent_args, "-m", label, env=env)
    index_file.unlink(missing_ok=True)
    git(dest, "update-ref", "refs/heads/main", shas[spec["source"]])
    git(dest, "reset", "-q", "--hard", shas[spec["source"]])
    return World(name, dest, spec, shas)


def index(world: World, graphs: Path, langs: list[str] | None = None) -> str:
    """Index the world's checkout into `graphs` and remember the snapshot id."""
    from test_s19_support import build

    stats = build(world.repo, graphs, langs if langs is not None else ["python"])
    world.graphs = graphs
    world.snapshot_id = str(stats["snapshot_id"])
    return world.snapshot_id


def build_indexed(name: str, tmp: Path) -> World:
    world = build_world(name, tmp / "repos" / name)
    index(world, tmp / "graphs" / name)
    return world


def shallow_clone(world: World, dest: Path, depth: int) -> World:
    """A `--depth` clone through file:// holding the same commit ids (not yet indexed)."""
    subprocess.run(
        ["git", "clone", "-q", "--depth", str(depth), "file://" + str(world.repo), str(dest)],
        check=True,
        capture_output=True,
        env=clean_env(),
    )
    return World(world.name + "-shallow", dest, world.spec, dict(world.shas))


def delete_loose_object(world: World, sha: str) -> None:
    path = world.repo / ".git" / "objects" / sha[:2] / sha[2:]
    assert path.is_file(), path
    path.chmod(0o644)
    path.unlink()


def tree_sha(world: World, label: str) -> str:
    return git_text(world.repo, "rev-parse", world.sha(label) + "^{tree}")


# ---- the spec oracle (no git parsing) ----------------------------------------------------------


def window_labels(spec: dict[str, Any], start: str, end: str) -> list[str]:
    """Labels reachable from `end` but not from `start`, by an iterative walk of the spec."""
    parents = {c["label"]: c["parents"] for c in spec["commits"]}

    def reach(tip: str) -> set[str]:
        seen: set[str] = set()
        todo = [tip]
        while todo:
            label = todo.pop()
            if label not in seen:
                seen.add(label)
                todo.extend(parents[label])
        return seen

    return sorted(reach(end) - reach(start))


def oracle(
    world: World,
    *,
    threshold: int | None = None,
    prefixes=("build/", "coverage/", "dist/", "node_modules/", "vendor/"),
    names=(
        "Cargo.lock",
        "Gemfile.lock",
        "composer.lock",
        "go.sum",
        "package-lock.json",
        "pnpm-lock.yaml",
        "poetry.lock",
        "uv.lock",
        "yarn.lock",
    ),
    suffixes=(".map", ".min.css", ".min.js", ".pb.go", ".pyc", "_pb2.py", "_pb2_grpc.py"),
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """Expected pairs and counts computed from the spec states alone (D5 applied by hand)."""
    spec = world.spec
    states = evolve(spec)
    limit = spec["bulk_threshold"] if threshold is None else threshold
    chosen = window_labels(spec, start or spec["start"], end or spec["end"])
    parent_of = {c["label"]: c["parents"] for c in spec["commits"]}
    counts = dict.fromkeys(
        [
            "merge_commits", "bulk_commits", "oversized_commits", "empty_commits",
            "single_path_commits", "pair_commits", "generated_paths", "gitlink_paths",
            "unrepresentable_paths",
        ],
        0,
    )  # fmt: skip
    pairs: dict[tuple[str, str], list[str]] = {}
    for label in chosen:
        parents = parent_of[label]
        if len(parents) > 1:
            counts["merge_commits"] += 1
            continue
        before = states[parents[0]] if parents else {}
        after = states[label]
        changed = sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))
        eligible: set[str] = set()
        for raw in changed:
            if "gitlink" in (before.get(raw, ("",))[0], after.get(raw, ("",))[0]):
                counts["gitlink_paths"] += 1
                continue
            try:
                path = raw.decode("utf-8")
            except UnicodeDecodeError:
                counts["unrepresentable_paths"] += 1
                continue
            base = path.rsplit("/", 1)[-1]
            if (
                any(path.startswith(p) for p in prefixes)
                or base in names
                or any(path.endswith(s) for s in suffixes)
            ):
                counts["generated_paths"] += 1
            else:
                eligible.add(path)
        ordered = sorted(eligible)
        if len(ordered) > limit:
            counts["bulk_commits"] += 1
        elif not ordered:
            counts["empty_commits"] += 1
        elif len(ordered) == 1:
            counts["single_path_commits"] += 1
        else:
            counts["pair_commits"] += 1
            for i, a in enumerate(ordered):
                for b in ordered[i + 1 :]:
                    pairs.setdefault((a, b), []).append(label)
    counts["commits_in_window"] = len(chosen)
    counts["pairs"] = len(pairs)
    counts["pair_observations"] = sum(len(v) for v in pairs.values())
    return {"pairs": pairs, "counts": counts}


def records_of(overlay: dict[str, Any]) -> dict[tuple[str, str], tuple[int, list[str]]]:
    return {(r["a"], r["b"]): (r["support"], r["supporting_commits"]) for r in overlay["records"]}


def expected_records(world: World, **kwargs: Any) -> dict[tuple[str, str], tuple[int, list[str]]]:
    found = oracle(world, **kwargs)["pairs"]
    return {
        key: (len(labels), sorted(world.sha(label) for label in labels))
        for key, labels in found.items()
    }


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---- inventories -------------------------------------------------------------------------------


def inventory(*roots: Path) -> dict[str, tuple[Any, ...]]:
    """Mode, size, mtime_ns and SHA-256 of every path (and directory) under the given trees."""
    found: dict[str, tuple[Any, ...]] = {}
    for root in roots:
        if not root.exists():
            found[str(root)] = ("<absent>",)
            continue
        for path in [root, *sorted(root.rglob("*"))]:
            info = os.lstat(path)
            digest = None
            if path.is_file() and not path.is_symlink():
                digest = sha256(path.read_bytes())
            found[str(path)] = (info.st_mode, info.st_size, info.st_mtime_ns, digest)
    return found


# ---- PATH shims (a refusing, slow or garbling git) ---------------------------------------------


def git_shim(tmp: Path, kind: str) -> Path:
    """A directory holding a `git` that refuses, is slow, or garbles `diff-tree` only."""
    shim = tmp / ("shim-" + kind)
    shim.mkdir(parents=True)
    real = subprocess.run(["sh", "-c", "command -v git"], capture_output=True, text=True)
    real_git = real.stdout.strip()
    body = {
        "refuse": "#!/bin/sh\necho 'fatal: detected dubious ownership' >&2\nexit 128\n",
        "slow": "#!/bin/sh\nexec sleep 8\n",
        "garble": (
            "#!/bin/sh\n"
            'for a in "$@"; do\n'
            '  if [ "$a" = "diff-tree" ]; then printf "garbage"; exit 0; fi\n'
            "done\n"
            'exec "$REAL_GIT" "$@"\n'
        ).replace("$REAL_GIT", real_git),
    }[kind]
    (shim / "git").write_text(body)
    (shim / "git").chmod(0o755)
    return shim


# ---- running the analyzer ----------------------------------------------------------------------

PROVISION = (
    'python -c "from tree_sitter_language_pack import download, get_parser; '
    "download(['python']); get_parser('python')\""
)


def provision_grammar() -> Any:
    """Fail (never skip) when the python grammar is not provisioned (outside timed windows)."""
    import pytest
    import tree_sitter_language_pack as pack

    try:
        pack.get_parser("python")
    except Exception as exc:
        pytest.fail(
            "the python grammar cannot be provisioned ("
            + type(exc).__name__
            + "); run once with network access: "
            + PROVISION,
            pytrace=False,
        )
    return pack.cache_dir()


def run(world: World, **overrides: Any) -> dict[str, Any]:
    """The library overlay of the world's default window (overrides replace option keys)."""
    from atlas_aci.history import cochange as lib

    return lib.cochange(world.options(**overrides))


def overlay_bytes(overlay: dict[str, Any]) -> bytes:
    from atlas_aci.history import cochange as lib

    return lib.canonical_bytes(overlay)


def normalized(overlay: dict[str, Any]) -> dict[str, Any]:
    """The overlay without the two fields that legitimately depend on the repository path."""
    copy = json.loads(json.dumps(overlay))
    copy["source_snapshot"].pop("snapshot_id")
    copy.pop("overlay_id")
    return copy


def cli_args(world: World, out: Path, *extra: Any, **overrides: Any) -> list[str]:
    """`cochange` arguments of the world's default window; overrides replace the named options."""
    values: dict[str, Any] = {
        "repo": world.repo,
        "repository-id": world.spec["repository_id"],
        "start": world.start,
        "end": world.end,
        "source-commit": world.source,
        "snapshot-id": world.snapshot_id,
        "graph-root": world.graphs,
        "bulk-threshold": world.spec["bulk_threshold"],
        "output": out,
    }
    for key, value in overrides.items():
        values[key.replace("_", "-")] = value
    args = ["cochange"]
    for key, value in values.items():
        if value is not None:
            args += ["--" + key, str(value)]
    return args + [str(item) for item in extra]


def cli_run(*args: Any, **kwargs: Any):
    from click.testing import CliRunner

    from atlas_aci.__main__ import cli

    return CliRunner().invoke(cli, [str(item) for item in args], **kwargs)


def sentinel_output(tmp: Path, name: str = "out.json") -> tuple[Path, bytes]:
    """An existing `--output` holding sentinel bytes a rejection must leave byte-unchanged."""
    out = tmp / name
    sentinel = b"SENTINEL-" + name.encode() + b"\x00\xff"
    out.write_bytes(sentinel)
    return out, sentinel


def assert_rejected_cli(result, text: str, out: Path, sentinel: bytes) -> None:
    """Exit 1, the exact fixed text, empty stdout, the pre-existing output untouched, no temp."""
    assert result.exit_code == 1, (result.exit_code, result.stderr)
    assert result.stdout_bytes == b""
    assert result.stderr == "Error: " + text + "\n"
    assert out.read_bytes() == sentinel
    assert [p.name for p in out.parent.iterdir() if p.name.startswith(".atlas-history")] == []
