#!/bin/sh
# ACI-S38 G-PRESERVE: preserve/base (captured from a worktree of the base commit) equals
# preserve/after (this checkout): per-frame N2-only sha256/bytes for every case (8 goldens and
# U1-U4, M1-M3, P1, P2, R1 incl. the tools/list frames for 2099 and int), the in-process dump
# (build_tool_manifest sha for the 4 revisions, 2099-01-01, the empty string, the default and the
# `atlas-aci tools` sha, plus call_tool_result bytes for each of the 7 tools under legacy and
# modern shaping), the observed AC-004 wire, and the normalised transcripts (== committed goldens).
# Only the `identity` object (label, paths, source hashes) may differ, and it must show that the
# base capture came from the base tree and the after capture from the modified one.
# Usage: g-preserve.sh SCRATCH   (the directory given to capture-preserve.sh)
set -eu
SCRATCH=$1
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
DEV=${DEV_IMAGE:-atlas-aci-dev-s16}
NAME=s38-gpreserve-$$
trap 'docker rm -f "$NAME" > /dev/null 2>&1 || true' EXIT INT TERM
docker run --rm -i --name "$NAME" --user 1000:1000 \
    -v "$HERE":/ev:ro -v "$SCRATCH":/scratch:ro -v "$REPO/mcp-server/tests/fixtures/s38":/golden:ro "$DEV" \
    /opt/venv/bin/python - <<'PY'
import json, sys
from pathlib import Path

ev = Path("/ev/preserve")
failed = 0


def load(label, name):
    return json.loads((ev / label / name).read_text())


def check(ok, text):
    global failed
    print(("PASS  " if ok else "FAIL  ") + text)
    failed += 0 if ok else 1


for name in ("frames.json", "inprocess.json"):
    b, a = load("base", name), load("after", name)
    ib, ia = b.pop("identity"), a.pop("identity")
    check(b == a and bool(b), f"{name}: base == after outside the identity header")
    check(ib["label"] == "base" and ia["label"] == "after", f"{name}: labels are base and after")
    check(ib["server_py_sha256"] != ia["server_py_sha256"], f"{name}: the two trees differ in server.py")
    check(ib["results_py_sha256"] != ia["results_py_sha256"], f"{name}: the two trees differ in results.py")
for name in ("ac004.json", "case-sha256.json"):
    b, a = load("base", name), load("after", name)
    check(b == a and bool(b), f"{name}: base == after")
frames = load("base", "frames.json")["frames"]
need = {f"case:{c}" for c in (
    "legacy.2024-11-05", "legacy.2025-03-26", "modern.2025-06-18", "modern.2025-11-25",
    "unsupported.2099-01-01", "unsupported.integer", "malformed.missing-version", "malformed.pre-initialize")}
need |= {f"ac004:{c}" for c in ("U1", "U2", "U3", "U4", "M1", "M2a", "M2b", "M3", "P1", "P2", "R1")}
check(need <= set(frames) and all(frames[k] for k in need), "frames cover every case and AC-004 row, none empty")
check(any(f["bytes"] > 300000 for f in frames["ac004:U1"]) and any(f["bytes"] > 300000 for f in frames["ac004:U4"]),
      "the tools/list frames for 2099 and the integer are present (> 300000 bytes)")
dump = load("base", "inprocess.json")
check(set(dump["manifests"]) == {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25", "2099-01-01", "", "<default>"},
      "manifest digests cover the 4 revisions, 2099-01-01, the empty string and the default")
check(dump["cli_tools"]["sha256"] == "94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56"
      and dump["cli_tools"]["bytes"] == 1631472, "atlas-aci tools sha/bytes pin")
tools = ("view_file", "list_dir", "search_text", "search_symbol", "graph_query", "test_dry_run", "memex_read")
check(sorted(dump["call_tool_result"]) == sorted(f"{v}:{t}" for v in ("2024-11-05", "2025-11-25") for t in tools),
      "call_tool_result bytes cover each of the 7 tools under legacy and modern shaping")
for case in ("legacy.2024-11-05", "legacy.2025-03-26", "modern.2025-06-18", "modern.2025-11-25",
             "unsupported.2099-01-01", "unsupported.integer", "malformed.missing-version", "malformed.pre-initialize"):
    after = (Path("/scratch/transcripts-after") / f"{case}.v1.json").read_bytes()
    base = (Path("/scratch/transcripts-base") / f"{case}.v1.json").read_bytes()
    golden = (Path("/golden/transcripts") / f"{case}.v1.json").read_bytes()
    check(bool(after) and after == base == golden, f"transcript {case}: after == base == committed golden")
ident = load("after", "identity.json") if (ev / "after" / "identity.json").exists() else None
print("RESULT: " + ("all checks passed" if not failed else f"{failed} check(s) failed"))
sys.exit(1 if failed else 0)
PY
