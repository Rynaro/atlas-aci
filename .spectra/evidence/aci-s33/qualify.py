"""ACI-S33 Day-1 qualification probe (one environment per run; prints one JSON document).

Usage (inside a container with the repository mounted at /ws):
    python /ws/.spectra/evidence/aci-s33/qualify.py LABEL WORKDIR [--index]

Builds the main world with the test builder, runs every D3 git invocation of the analyzer through
a runner that copies the analyzer's environment and prefix, and records the return code, the
sha256 and the stderr cleanliness of each. `--index` additionally indexes the world (needs the
atlas_aci package) and checks the blob binding and the hostile-environment variants through the
library itself.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "mcp-server/tests/slices"))
sys.path.insert(0, str(REPO / "mcp-server/src"))
import test_s33_support as sup  # noqa: E402

label, workdir = sys.argv[1], Path(sys.argv[2])
do_index = "--index" in sys.argv
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

ENV = {
    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    "LC_ALL": "C",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_PAGER": "cat",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_NO_LAZY_FETCH": "1",
    "GIT_ALLOW_PROTOCOL": "none",
    "GIT_GRAFT_FILE": "/dev/null",
}
PREFIX = [
    "-c", "core.fsmonitor=false", "-c", "core.quotePath=false",
    "-c", "diff.external=", "-c", "diff.renames=false",
]  # fmt: skip
PROMISOR = r"^(extensions\.partialclone|remote\..*\.(promisor|partialclonefilter))$"
out: dict = {"label": label}


def run(repo, *args, env=None):
    argv = ["git", "--no-optional-locks", "-C", str(repo), *PREFIX, *args]
    r = subprocess.run(argv, capture_output=True, env=env if env is not None else ENV)
    return r


def rec(repo, *args, env=None):
    r = run(repo, *args, env=env)
    return {
        "rc": r.returncode,
        "sha256": hashlib.sha256(r.stdout).hexdigest(),
        "bytes": len(r.stdout),
        "stderr_empty": r.stderr == b"",
        "stderr_head": r.stderr[:120].decode("utf-8", "replace"),
    }


def digest_only(entry):
    return {k: entry[k] for k in ("rc", "sha256", "bytes")}


shutil.rmtree(workdir, ignore_errors=True)
workdir.mkdir(parents=True)
version = subprocess.run(["git", "--version"], capture_output=True, text=True).stdout.strip()
out["git_version"] = version
out["python"] = sys.version.split()[0]
world = sup.build_world("main", workdir / "main")
shas = world.shas
out["commit_ids"] = shas
repo = world.repo
S = shas
START, END = S["C0"], S["C14"]

# (c) every D3 argv on the world
calls = {}
calls["probe"] = rec(repo, "rev-parse", "--show-toplevel", "--absolute-git-dir",
                     "--git-common-dir", "--is-shallow-repository", "--is-bare-repository",
                     "--show-object-format")  # fmt: skip
calls["promisor_config"] = rec(repo, "config", "--local", "--get-regexp", PROMISOR)
calls["exists_end"] = rec(repo, "cat-file", "-e", "--end-of-options", END + "^{commit}")
calls["exists_missing"] = rec(repo, "cat-file", "-e", "--end-of-options", "1" * 40 + "^{commit}")
calls["exists_tree_as_commit"] = rec(
    repo, "cat-file", "-e", "--end-of-options", sup.tree_sha(world, "C14") + "^{commit}"
)
calls["ancestor_yes"] = rec(repo, "merge-base", "--is-ancestor", "--end-of-options", S["C3"], END)
calls["ancestor_no"] = rec(repo, "merge-base", "--is-ancestor", "--end-of-options", END, S["C3"])
calls["ancestor_side"] = rec(repo, "merge-base", "--is-ancestor", "--end-of-options", S["C3"], S["C4"])
calls["ancestor_missing"] = rec(repo, "merge-base", "--is-ancestor", "--end-of-options", "1" * 40, END)
calls["ls_tree"] = rec(repo, "ls-tree", "-r", "-z", "--full-tree", "--end-of-options", END)
calls["rev_list"] = rec(repo, "rev-list", "--parents", "--max-count=15", "--end-of-options", END, "^" + START)
calls["rev_list_max1"] = rec(repo, "rev-list", "--parents", "--max-count=2", "--end-of-options", END, "^" + START)
calls["rev_list_reversed"] = rec(repo, "rev-list", "--parents", "--max-count=15", "--end-of-options", START, "^" + END)
calls["rev_list_negation_after_eoo"] = rec(
    repo, "rev-list", "--end-of-options", END, "^" + S["C13"]
)
out["rev_list_negation_lines"] = len(
    run(repo, "rev-list", "--end-of-options", END, "^" + S["C13"]).stdout.split()
)
calls["cat_commit"] = rec(repo, "cat-file", "commit", S["C5"] if "C5" in S else S["M5"])
raw = {}
for lbl, sha in S.items():
    parent = git_parent = sup.git_text(repo, "rev-list", "--parents", "-n1", sha).split()
    p = parent[1] if len(parent) > 1 else EMPTY_TREE
    raw[lbl] = rec(
        repo, "diff-tree", "-r", "-z", "--raw", "--no-abbrev", "--no-renames", "--no-ext-diff",
        "--no-textconv", "--ignore-submodules=none", "--end-of-options", p, sha,
    )  # fmt: skip
calls["diff_tree"] = {k: digest_only(v) for k, v in raw.items()}
out["diff_tree_stderr_clean"] = all(v["stderr_empty"] for v in raw.values())
calls["diff_external_empty_ok"] = digest_only(
    rec(repo, "diff-tree", "-r", "--raw", "--end-of-options", EMPTY_TREE, S["C1"])
)
out["stderr_clean"] = {
    k: v["stderr_empty"] for k, v in calls.items() if isinstance(v, dict) and "stderr_empty" in v
}
out["calls"] = {
    k: (digest_only(v) if "rc" in v else v) for k, v in calls.items()
}
out["raw_diff_sample"] = {
    lbl: run(repo, "diff-tree", "-r", "-z", "--raw", "--no-abbrev", "--no-renames",
             "--ignore-submodules=none", "--end-of-options",
             (sup.git_text(repo, "rev-list", "--parents", "-n1", S[lbl]).split() + [EMPTY_TREE])[1], S[lbl]
             ).stdout.replace(b"\0", b"|").decode("utf-8", "replace")
    for lbl in ("C0", "C9", "C10", "C12", "C13")
}  # fmt: skip
out["rev_list_text"] = run(repo, "rev-list", "--parents", "--end-of-options", END, "^" + START).stdout.decode().count("\n")

# (d) missing objects
d = {}
w2 = sup.build_world("main", workdir / "missing-commit")
sup.delete_loose_object(w2, w2.sha("C3"))
d["missing_commit_rev_list"] = rec(w2.repo, "rev-list", "--parents", "--end-of-options", END, "^" + START)
d["missing_commit_cat_e"] = rec(w2.repo, "cat-file", "-e", "--end-of-options", w2.sha("C3") + "^{commit}")
d["missing_commit_merge_base"] = rec(
    w2.repo, "merge-base", "--is-ancestor", "--end-of-options", w2.sha("C1"), END
)
d["missing_commit_merge_base_across"] = rec(
    w2.repo, "merge-base", "--is-ancestor", "--end-of-options", w2.sha("C2"), END
)
w3 = sup.build_world("main", workdir / "missing-tree")
tree = sup.tree_sha(w3, "C8")
sup.delete_loose_object(w3, tree)
d["missing_tree_diff_tree"] = rec(
    w3.repo, "diff-tree", "-r", "-z", "--raw", "--no-abbrev", "--no-renames", "--no-ext-diff",
    "--no-textconv", "--ignore-submodules=none", "--end-of-options", w3.sha("C7"), w3.sha("C8"),
)  # fmt: skip
d["missing_tree_rev_list"] = rec(w3.repo, "rev-list", "--parents", "--end-of-options", END, "^" + START)
d["missing_tree_diff_parent_side"] = rec(
    w3.repo, "diff-tree", "-r", "-z", "--raw", "--no-abbrev", "--no-renames", "--no-ext-diff",
    "--no-textconv", "--ignore-submodules=none", "--end-of-options", w3.sha("C8"), w3.sha("C9"),
)  # fmt: skip
out["missing"] = d

# gitlink <-> blob type change: one entry or D+A?
gl = {
    "repository_id": "gl", "start": "G0", "end": "G2", "source": "G2", "bulk_threshold": 5,
    "commits": [
        {"label": "G0", "parents": [], "touch": ["src/m.py", "src/n.py"]},
        {"label": "G1", "parents": ["G0"], "delete": ["src/m.py"], "gitlink": {"src/m.py": "G0"}},
        {"label": "G2", "parents": ["G1"], "delete": ["src/m.py"], "touch": ["src/m.py"]},
    ],
}  # fmt: skip
# the spec evolve cannot express blob->gitlink at one path in one commit (delete then gitlink works)
gw = sup.build_world("gl", workdir / "gitlink-type", gl)
out["gitlink_type_change"] = {
    lbl: run(gw.repo, "diff-tree", "-r", "-z", "--raw", "--no-abbrev", "--no-renames",
             "--ignore-submodules=none", "--end-of-options", gw.sha(p), gw.sha(lbl)
             ).stdout.replace(b"\0", b"|").decode("utf-8", "replace")
    for lbl, p in (("G1", "G0"), ("G2", "G1"))
}  # fmt: skip

# (e) shallow clones
e = {}
sh = sup.shallow_clone(world, workdir / "shallow", 2)
e["is_shallow"] = run(sh.repo, "rev-parse", "--is-shallow-repository").stdout.decode().strip()
e["start_missing_exists"] = rec(sh.repo, "cat-file", "-e", "--end-of-options", START + "^{commit}")
e["rev_list_boundary"] = run(sh.repo, "rev-list", "--parents", "--end-of-options", END).stdout.decode().replace(S["C14"], "C14").replace(S["C13"], "C13")
e["cat_commit_boundary_has_parent"] = b"\nparent " in run(sh.repo, "cat-file", "commit", S["C13"]).stdout
e["rev_list_start_missing"] = rec(sh.repo, "rev-list", "--parents", "--end-of-options", END, "^" + START)
e["merge_base_across"] = rec(sh.repo, "merge-base", "--is-ancestor", "--end-of-options", S["C13"], END)
e["merge_base_start_missing"] = rec(sh.repo, "merge-base", "--is-ancestor", "--end-of-options", START, END)
e["window_c13_c14"] = rec(sh.repo, "rev-list", "--parents", "--end-of-options", END, "^" + S["C13"])
fut = sup.build_world("future", workdir / "future")
mer = sup.build_world("shallow_merge", workdir / "merge")
msh = sup.shallow_clone(mer, workdir / "merge-shallow", 2)
e["merge_rev_list"] = run(msh.repo, "rev-list", "--parents", "--end-of-options", mer.sha("X2"), "^" + mer.sha("X1")).stdout.decode()
e["merge_rev_list_labels"] = [
    [next((k for k, v in mer.shas.items() if v == t), "?") for t in row.split()]
    for row in e["merge_rev_list"].splitlines()
]
e["merge_boundary_raw_has_parent"] = b"\nparent " in run(msh.repo, "cat-file", "commit", mer.sha("Y1")).stdout
del e["merge_rev_list"]
out["shallow"] = e

# (h) hostile config and objects, same argv, same outputs
h = {}
base_diff = rec(repo, "diff-tree", "-r", "-z", "--raw", "--no-abbrev", "--no-renames",
                "--no-ext-diff", "--no-textconv", "--ignore-submodules=none", "--end-of-options",
                S["C11"], S["C12"])  # fmt: skip
base_rl = rec(repo, "rev-list", "--parents", "--end-of-options", END, "^" + START)
base_gl = rec(repo, "diff-tree", "-r", "-z", "--raw", "--no-abbrev", "--no-renames",
              "--no-ext-diff", "--no-textconv", "--ignore-submodules=none", "--end-of-options",
              S["C8"], S["C9"])  # fmt: skip
hw = sup.build_world("main", workdir / "hostile")
sentinel = workdir / "SENTINEL"
script = workdir / "evil.sh"
script.write_text(f"#!/bin/sh\ntouch {sentinel}\nexit 0\n")
script.chmod(0o755)
sup.git(hw.repo, "config", "diff.renames", "copies")
sup.git(hw.repo, "config", "core.fsmonitor", str(script))
sup.git(hw.repo, "config", "diff.external", str(script))
sup.git(hw.repo, "config", "diff.ignoreSubmodules", "all")
sup.git(hw.repo, "config", "core.attributesFile", "/nonexistent")
(hw.repo / ".gitmodules").write_text('[submodule "mod"]\n\tpath = vendor/mod\n\turl = ./x\n\tignore = all\n')
sup.git(hw.repo, "replace", hw.sha("C2"), hw.sha("C1"))
# grafts: pretend C4 has no parent
(hw.repo / ".git" / "info").mkdir(exist_ok=True)
(hw.repo / ".git" / "info" / "grafts").write_text(hw.sha("C4") + "\n")
h["diff_c12_same"] = rec(hw.repo, "diff-tree", "-r", "-z", "--raw", "--no-abbrev", "--no-renames",
                         "--no-ext-diff", "--no-textconv", "--ignore-submodules=none",
                         "--end-of-options", hw.sha("C11"), hw.sha("C12")) == base_diff
h["diff_c9_gitlink_same"] = rec(hw.repo, "diff-tree", "-r", "-z", "--raw", "--no-abbrev",
                                "--no-renames", "--no-ext-diff", "--no-textconv",
                                "--ignore-submodules=none", "--end-of-options",
                                hw.sha("C8"), hw.sha("C9")) == base_gl
h["rev_list_same_with_replace_and_grafts"] = rec(hw.repo, "rev-list", "--parents", "--end-of-options", END, "^" + START) == base_rl
h["sentinel_created"] = sentinel.exists()
# the same hostile repository with the graft file NOT neutralised (what GIT_GRAFT_FILE guards)
env_g = dict(ENV)
del env_g["GIT_GRAFT_FILE"]
h["rev_list_graft_unneutralised_differs"] = rec(hw.repo, "rev-list", "--parents", "--end-of-options", END, "^" + START, env=env_g) != base_rl
h["graft_note"] = run(hw.repo, "rev-list", "--parents", "--end-of-options", END, "^" + START, env=env_g).stderr[:200].decode("utf-8", "replace")
h["gitmodules_only_same"] = True
# promisor remote + uploadpack sentinel + a missing object: the probe sees it; nothing runs
pw = sup.build_world("main", workdir / "promisor")
psent = workdir / "UPLOADPACK-SENTINEL"
pscript = workdir / "uploadpack.sh"
pscript.write_text(f"#!/bin/sh\ntouch {psent}\nexit 1\n")
pscript.chmod(0o755)
sup.git(pw.repo, "config", "extensions.partialClone", "origin")
sup.git(pw.repo, "config", "remote.origin.url", "file:///nonexistent")
sup.git(pw.repo, "config", "remote.origin.promisor", "true")
sup.git(pw.repo, "config", "remote.origin.uploadpack", str(pscript))
sup.git(pw.repo, "config", "core.sshCommand", str(pscript))
sup.git(pw.repo, "config", "core.gitProxy", str(pscript))
sup.delete_loose_object(pw, pw.sha("C3"))
h["promisor_probe"] = digest_only(rec(pw.repo, "config", "--local", "--get-regexp", PROMISOR))
h["promisor_rev_list"] = digest_only(rec(pw.repo, "rev-list", "--parents", "--end-of-options", END, "^" + START))
h["promisor_cat_commit_missing"] = digest_only(rec(pw.repo, "cat-file", "commit", pw.sha("C3")))
h["promisor_diff_missing"] = digest_only(rec(pw.repo, "diff-tree", "-r", "-z", "--raw", "--end-of-options", pw.sha("C2"), pw.sha("C3")))
h["uploadpack_sentinel_created"] = psent.exists()
out["hostile"] = h

# (g) dubious ownership is exercised by qualify.sh (root-created repository, probed as uid 1000)
out["uid"] = os.getuid()

# (i) timing: a 1000-commit linear window
n = 1000
big = {
    "repository_id": "big", "start": "L0", "end": f"L{n}", "source": f"L{n}", "bulk_threshold": 5,
    "commits": [{"label": "L0", "parents": [], "touch": ["src/a.py", "src/b.py"]}]
    + [{"label": f"L{i}", "parents": [f"L{i - 1}"], "touch": ["src/a.py", f"src/m{i % 7}.py"]} for i in range(1, n + 1)],
}  # fmt: skip
t0 = time.monotonic()
bw = sup.build_world("big", workdir / "big", big)
out["timing_build_seconds"] = round(time.monotonic() - t0, 2)
t0 = time.monotonic()
rl = run(bw.repo, "rev-list", "--parents", "--max-count=1001", "--end-of-options", bw.sha(f"L{n}"), "^" + bw.sha("L0"))
count = 0
for row in rl.stdout.decode().splitlines():
    parts = row.split()
    p = parts[1] if len(parts) > 1 else EMPTY_TREE
    dt = run(bw.repo, "diff-tree", "-r", "-z", "--raw", "--no-abbrev", "--no-renames", "--no-ext-diff",
             "--no-textconv", "--ignore-submodules=none", "--end-of-options", p, parts[0])
    assert dt.returncode == 0
    count += 1
out["timing_walk_seconds_1000_commits"] = round(time.monotonic() - t0, 2)
out["timing_walk_commits"] = count

# (f) blob binding through the library (needs atlas_aci)
if do_index:
    from atlas_aci.history import cochange as lib
    from atlas_aci.overlays import runtime

    f = {}
    graphs = workdir / "graphs"
    snap = sup.index(world, graphs / "main")
    f["snapshot_id_len"] = len(snap)
    opts = world.options()
    options = lib.normalize_options(opts)
    t0 = time.monotonic()
    overlay = lib.cochange(options)
    f["cochange_seconds"] = round(time.monotonic() - t0, 2)
    f["state"] = overlay["coverage"]["state"]
    f["files_compared"] = overlay["source_snapshot"]["files_compared"]
    f["records"] = len(overlay["records"])
    f["counts"] = overlay["counts"]
    f["overlay_bytes"] = len(runtime.canonical_bytes(overlay))
    f["overlay_sha256"] = hashlib.sha256(runtime.canonical_bytes(overlay)).hexdigest()
    f["lib_env_equals_qualify_env"] = lib._git_env() == ENV
    f["lib_prefix_equals_qualify_prefix"] = list(lib._PREFIX_ARGV) == PREFIX
    # hostile process environment: the library must ignore it
    hostile = {
        "GIT_DIR": "/nonexistent", "GIT_WORK_TREE": "/nonexistent", "GIT_INDEX_FILE": "/nonexistent",
        "GIT_EXTERNAL_DIFF": str(script), "GIT_CONFIG_PARAMETERS": "'diff.renames=copies'",
        "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "diff.renames", "GIT_CONFIG_VALUE_0": "copies",
        "GIT_OBJECT_DIRECTORY": "/nonexistent", "GIT_SHALLOW_FILE": "/nonexistent",
        "GIT_REPLACE_REF_BASE": "refs/nothing/", "GIT_TRACE": "/nonexistent/trace",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": "/nonexistent",
    }  # fmt: skip
    saved = dict(os.environ)
    os.environ.update(hostile)
    try:
        again = lib.cochange(options)
    finally:
        os.environ.clear()
        os.environ.update(saved)
    f["hostile_env_same_overlay"] = runtime.canonical_bytes(again) == runtime.canonical_bytes(overlay)
    f["sentinel_after_hostile_env"] = sentinel.exists()
    out["library"] = f

print(json.dumps(out, indent=1, sort_keys=True))
