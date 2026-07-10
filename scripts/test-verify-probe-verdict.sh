#!/usr/bin/env bash
# scripts/test-verify-probe-verdict.sh
#
# Self-test for scripts/verify-probe-verdict.py — covers five rounds of
# the same defect class ("the check measures a proxy instead of the
# invariant"), each found by attacking the previous round's fix:
#
#   - Defect 10 (MAJOR-1): the gate used to `grep -qiE "verdict.*:.*pass"`
#     the artefact's PROSE — never a Q value, never a clause. Scenarios
#     2-4 below forge failing/flipped numbers under an unchanged label.
#   - Defect 11: the recomputation it was replaced with still read its
#     BAR (`q_struct`/`r`) FROM the sidecar under audit. Scenarios 5/5b.
#   - Defect 12: nothing asserted WHICH repos, or how many, were graded,
#     nor which seeds. Scenarios 6-9 forge a dropped repo, an added repo,
#     a wrong seed set, and a wrong pinned SHA.
#   - Defect 13: every `Q` (and `node_count`/`edge_count`) was taken ON
#     FAITH from the sidecar — a maker could under-report the Louvain
#     baseline and ship a weak clusterer against a lowered bar. Scenario
#     10 forges exactly that; scenarios 11/12 attack the graph BUNDLE
#     this was fixed with (a tampered bundle, and a bundle edited so a
#     recomputed Q genuinely diverges from its recorded value).
#   - The staleness hole: nothing tied the sidecar to the indexer that
#     produced it. Scenario 13 forges a stale `indexer_fingerprint`.
#
# This self-test asserts scripts/verify-probe-verdict.py rejects every
# forgery below and accepts the real, currently-recorded probe sidecar +
# graph bundle.
#
# Usage: scripts/test-verify-probe-verdict.sh
# Exit 0 if every scenario matches its expected outcome, 1 otherwise.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERIFY="$SCRIPT_DIR/verify-probe-verdict.py"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SIDECAR_DIR="$REPO_ROOT/.spectra/changes/aci-v2-harden-and-augment"
REAL_JSON="$SIDECAR_DIR/probe-lpa-vs-louvain.json"
REAL_BUNDLE="$SIDECAR_DIR/probe-graphs.json.gz"

if [ ! -f "$REAL_JSON" ]; then
    echo "SKIP: $REAL_JSON not present (A3 not yet built in this tree)."
    exit 0
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

# Most scenarios forge only the sidecar JSON, never the graph bundle --
# each of those gets its OWN directory with a copy of the REAL bundle
# alongside the forged sidecar (the sidecar's `graph_bundle.filename` is
# a bare filename, resolved relative to the sidecar's own directory).
_scenario_dir() {
    local name="$1"
    local dir="$TMP_DIR/$name"
    mkdir -p "$dir"
    cp "$REAL_BUNDLE" "$dir/probe-graphs.json.gz"
    echo "$dir"
}

pass_count=0
fail_count=0

_assert_exit() {
    local scenario="$1" json_path="$2" expected_exit="$3"
    local actual_exit=0
    python3 "$VERIFY" "$json_path" > "$TMP_DIR/out.log" 2>&1 || actual_exit=$?
    if [ "$actual_exit" -eq "$expected_exit" ]; then
        echo "PASS: $scenario (exit=$actual_exit)"
        pass_count=$((pass_count + 1))
    else
        echo "FAIL: $scenario -- expected exit=$expected_exit, got exit=$actual_exit"
        cat "$TMP_DIR/out.log"
        fail_count=$((fail_count + 1))
    fi
}

# ---- Scenario 1: the REAL recorded sidecar (+ real bundle) must be accepted (exit 0) ----
_assert_exit "real recorded probe sidecar (genuine PASS)" "$REAL_JSON" 0

# ---- Scenario 2: the checker's exact forgery — failing numbers under a recorded PASS ----
dir="$(_scenario_dir scenario-2)"
python3 - "$REAL_JSON" "$dir/probe-lpa-vs-louvain.json" << 'PYEOF'
import json
import sys

data = json.load(open(sys.argv[1]))
for repo in data["repos"]:
    repo["lpa_q"] = 0.11111  # below the 0.30 floor -- fails clauses 2 AND 3
data["recorded_verdict"] = "PASS"  # left untouched, exactly like the checker's forgery
json.dump(data, open(sys.argv[2], "w"))
PYEOF
_assert_exit "forged: failing numbers (LPA_Q=0.11111) under recorded PASS" \
    "$dir/probe-lpa-vs-louvain.json" 1

# ---- Scenario 3: the mirror-image lie — a recorded CUT over genuinely passing numbers ----
dir="$(_scenario_dir scenario-3)"
python3 - "$REAL_JSON" "$dir/probe-lpa-vs-louvain.json" << 'PYEOF'
import json
import sys

data = json.load(open(sys.argv[1]))
data["recorded_verdict"] = "CUT"
json.dump(data, open(sys.argv[2], "w"))
PYEOF
_assert_exit "forged: recorded CUT over genuinely passing numbers" \
    "$dir/probe-lpa-vs-louvain.json" 1

# ---- Scenario 4: a single repo failing clause 1 (Louvain_Q_median < 0.30) must also flip the verdict ----
dir="$(_scenario_dir scenario-4)"
python3 - "$REAL_JSON" "$dir/probe-lpa-vs-louvain.json" << 'PYEOF'
import json
import sys

data = json.load(open(sys.argv[1]))
data["repos"][0]["louvain_q_by_seed"] = {str(s): 0.1 for s in range(10)}
data["recorded_verdict"] = "PASS"
json.dump(data, open(sys.argv[2], "w"))
PYEOF
_assert_exit "forged: one repo's Louvain_Q_median below the 0.30 floor, recorded PASS" \
    "$dir/probe-lpa-vs-louvain.json" 1

# ---- Scenario 5 (checker defect 11): softened bar alongside a failing LPA_Q ----
# The verifier must grade against its OWN hardcoded bar, never the
# sidecar's copy. This forges q_struct/r DOWN and hands it LPA_Q=0.20,
# which fails the FROZEN bar's clause 2 (>= 0.30) but would pass the
# forged, softened bar if the verifier ever trusted it.
dir="$(_scenario_dir scenario-5)"
python3 - "$REAL_JSON" "$dir/probe-lpa-vs-louvain.json" << 'PYEOF'
import json
import sys

data = json.load(open(sys.argv[1]))
data["bar"]["q_struct"] = 0.01
data["bar"]["r"] = 0.10
for repo in data["repos"]:
    repo["lpa_q"] = 0.20  # fails the FROZEN q_struct=0.30, passes the forged 0.01
data["recorded_verdict"] = "PASS"
json.dump(data, open(sys.argv[2], "w"))
PYEOF
_assert_exit "forged: softened bar (q_struct=0.01, r=0.10) alongside failing LPA_Q=0.20" \
    "$dir/probe-lpa-vs-louvain.json" 1

# ---- Scenario 5b: a STALE bar declaration with otherwise-genuine, passing numbers ----
# Scenario 5 combines a wrong bar with numbers that also fail the frozen
# bar -- the hardcoded-arithmetic fix alone rejects it, independent of
# whether the sidecar's own bar/frozen-constant comparison ever runs. This
# scenario isolates that comparison specifically: the bar block disagrees
# with the frozen constants, but every number is left exactly as recorded
# (genuinely passing under the frozen bar) -- only
# _check_declared_bar_matches_frozen can catch this one; the arithmetic
# alone would happily compute PASS and match the recorded PASS.
dir="$(_scenario_dir scenario-5b)"
python3 - "$REAL_JSON" "$dir/probe-lpa-vs-louvain.json" << 'PYEOF'
import json
import sys

data = json.load(open(sys.argv[1]))
data["bar"]["q_struct"] = 0.5  # disagrees with the frozen 0.30 -- numbers untouched
json.dump(data, open(sys.argv[2], "w"))
PYEOF
_assert_exit "forged: stale bar declaration (q_struct=0.5) over otherwise-genuine passing numbers" \
    "$dir/probe-lpa-vs-louvain.json" 1

# ---- Scenario 6 (checker defect 12): dropped repo (solidus removed) ----
dir="$(_scenario_dir scenario-6)"
python3 - "$REAL_JSON" "$dir/probe-lpa-vs-louvain.json" << 'PYEOF'
import json
import sys

data = json.load(open(sys.argv[1]))
data["repos"] = [r for r in data["repos"] if r["name"] != "solidus"]
json.dump(data, open(sys.argv[2], "w"))
PYEOF
_assert_exit "forged: solidus dropped from repos (only spree remains)" \
    "$dir/probe-lpa-vs-louvain.json" 1

# ---- Scenario 7 (checker defect 12): added repo (a third, unpinned entry) ----
dir="$(_scenario_dir scenario-7)"
python3 - "$REAL_JSON" "$dir/probe-lpa-vs-louvain.json" << 'PYEOF'
import json
import sys

data = json.load(open(sys.argv[1]))
extra = dict(data["repos"][0])
extra["name"] = "not-a-pinned-repo"
extra["pinned_sha"] = "0000000000000000000000000000000000000000"
data["repos"].append(extra)
json.dump(data, open(sys.argv[2], "w"))
PYEOF
_assert_exit "forged: a third, unpinned repo added to repos" \
    "$dir/probe-lpa-vs-louvain.json" 1

# ---- Scenario 8 (checker defect 12): wrong seed set (seed 9 replaced with seed 10) ----
dir="$(_scenario_dir scenario-8)"
python3 - "$REAL_JSON" "$dir/probe-lpa-vs-louvain.json" << 'PYEOF'
import json
import sys

data = json.load(open(sys.argv[1]))
repo = data["repos"][0]
seeds = repo["louvain_q_by_seed"]
seeds["10"] = seeds.pop("9")  # still 10 seeds total, but not 0..9
json.dump(data, open(sys.argv[2], "w"))
PYEOF
_assert_exit "forged: wrong seed set (seed 9 relabeled as seed 10)" \
    "$dir/probe-lpa-vs-louvain.json" 1

# ---- Scenario 9 (checker defect 12): wrong pinned SHA (solidus's SHA altered) ----
dir="$(_scenario_dir scenario-9)"
python3 - "$REAL_JSON" "$dir/probe-lpa-vs-louvain.json" << 'PYEOF'
import json
import sys

data = json.load(open(sys.argv[1]))
for repo in data["repos"]:
    if repo["name"] == "solidus":
        repo["pinned_sha"] = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
json.dump(data, open(sys.argv[2], "w"))
PYEOF
_assert_exit "forged: solidus's pinned_sha altered to an unpinned value" \
    "$dir/probe-lpa-vs-louvain.json" 1

# ---- Scenario 10 (checker defect 13): under-reported Louvain baseline ----
# The coordinator's exact attack: report all ten seeds at Q=0.35 (median
# 0.35, still clears the 0.30 floor) and LPA_Q=0.31 (clears 0.30 AND
# 0.85*0.35=0.2975) -- a self-consistent but DISHONEST pair of numbers
# that a maker could use to ship a clusterer scoring Q=0.31 against a
# REAL baseline of ~0.74. The graph bundle is untouched (still the real,
# honest data), so the recomputed Q from the graph will disagree wildly
# with these forged recorded values.
dir="$(_scenario_dir scenario-10)"
python3 - "$REAL_JSON" "$dir/probe-lpa-vs-louvain.json" << 'PYEOF'
import json
import sys

data = json.load(open(sys.argv[1]))
for repo in data["repos"]:
    repo["lpa_q"] = 0.31
    repo["louvain_q_by_seed"] = {str(s): 0.35 for s in range(10)}
data["recorded_verdict"] = "PASS"
json.dump(data, open(sys.argv[2], "w"))
PYEOF
_assert_exit "forged: under-reported Louvain baseline (all seeds Q=0.35, LPA_Q=0.31)" \
    "$dir/probe-lpa-vs-louvain.json" 1

# ---- Scenario 11 (checker defect 13): tampered graph bundle (sha256 mismatch) ----
dir="$(_scenario_dir scenario-11)"
cp "$REAL_JSON" "$dir/probe-lpa-vs-louvain.json"
printf '\x00' >> "$dir/probe-graphs.json.gz"  # flip: append one byte
_assert_exit "forged: graph bundle tampered with, sha256 no longer matches" \
    "$dir/probe-lpa-vs-louvain.json" 1

# ---- Scenario 12 (checker defect 13): graph REWIRED so a recomputed Q genuinely diverges ----
# Unlike scenario 11 (a stale sha256 the sidecar never re-signed), this
# tampers with the bundle's ACTUAL graph content -- REWIRES one edge
# (removes an existing edge, adds a different, previously-absent one) so
# node_count/edge_count/lpa_community_count are ALL UNCHANGED (isolating
# this from the separate count-recompute check), but connectivity, and
# therefore true modularity, differs. sha256 is then recomputed over the
# TAMPERED bytes and written into the sidecar's own graph_bundle.sha256,
# so the integrity check (scenario 11's guard) is satisfied. The
# sidecar's recorded lpa_q/louvain_q_by_seed are left as the ORIGINAL,
# honest values. Only the Q-recomputation-vs-recorded check (a DIFFERENT
# guard than sha256 or the count checks) can catch this.
dir="$(_scenario_dir scenario-12)"
python3 - "$dir/probe-graphs.json.gz" "$REAL_JSON" "$dir/probe-lpa-vs-louvain.json" << 'PYEOF'
import gzip
import hashlib
import json
import sys

bundle_path, sidecar_path, out_sidecar_path = sys.argv[1:4]

bundle = json.loads(gzip.decompress(open(bundle_path, "rb").read()).decode("utf-8"))
edges = bundle["solidus"]["edges"]
existing = {tuple(e) for e in edges}
removed = tuple(edges[0])
# Find a previously-absent pair to rewire to, preserving edge_count exactly.
n = len(bundle["solidus"]["nodes"])
new_edge = None
for u in range(n):
    for v in range(u + 1, n):
        if (u, v) not in existing and (u, v) != removed:
            new_edge = (u, v)
            break
    if new_edge:
        break
edges.remove(list(removed))
edges.append(list(new_edge))
edges.sort()
tampered = gzip.compress(json.dumps(bundle, separators=(",", ":")).encode("utf-8"), mtime=0)
open(bundle_path, "wb").write(tampered)
new_sha256 = hashlib.sha256(tampered).hexdigest()

sidecar = json.load(open(sidecar_path))
sidecar["graph_bundle"]["sha256"] = new_sha256  # matches the tampered file -- sha256 check alone won't catch this
json.dump(sidecar, open(out_sidecar_path, "w"))
PYEOF
_assert_exit "forged: graph rewired (sha256 re-signed to match, counts unchanged, but Q now diverges)" \
    "$dir/probe-lpa-vs-louvain.json" 1

# ---- Scenario 13: stale indexer_fingerprint ----
dir="$(_scenario_dir scenario-13)"
python3 - "$REAL_JSON" "$dir/probe-lpa-vs-louvain.json" << 'PYEOF'
import json
import sys

data = json.load(open(sys.argv[1]))
data["indexer_fingerprint"] = "0" * 64  # does not match the current tree's codegraph.py
json.dump(data, open(sys.argv[2], "w"))
PYEOF
_assert_exit "forged: stale indexer_fingerprint (does not match the current tree)" \
    "$dir/probe-lpa-vs-louvain.json" 1

# ---- Fingerprint coverage (checker defect 15): every graph-determining
# function, and both probe scripts, must flip compute_indexer_fingerprint()
# when edited -- proving the whole-file-hash fix actually covers what it
# claims to, not just confident_edges() (the under-coverage the checker
# found IN the defect-13 fix itself). Builds a throwaway copy of the real
# repo tree, edits exactly ONE function (or script) at a time, and asserts
# the fingerprint changes relative to an untouched baseline copy.
_fingerprint_dir="$TMP_DIR/fingerprint-coverage"
mkdir -p "$_fingerprint_dir/baseline/mcp-server/src/atlas_aci" "$_fingerprint_dir/baseline/scripts"
cp "$REPO_ROOT/mcp-server/src/atlas_aci/codegraph.py" \
    "$_fingerprint_dir/baseline/mcp-server/src/atlas_aci/codegraph.py"
cp "$REPO_ROOT/scripts/probe-export-confident-graph.py" \
    "$_fingerprint_dir/baseline/scripts/probe-export-confident-graph.py"
cp "$REPO_ROOT/scripts/probe-assemble-graph-bundle.py" \
    "$_fingerprint_dir/baseline/scripts/probe-assemble-graph-bundle.py"

_fingerprint_of() {
    local root="$1"
    python3 -c "
import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('verify_probe_verdict', '$VERIFY')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
print(mod.compute_indexer_fingerprint(Path('$root')))
"
}

baseline_fp="$(_fingerprint_of "$_fingerprint_dir/baseline")"

_assert_fingerprint_flips() {
    local scenario="$1" edited_root="$2"
    local edited_fp
    edited_fp="$(_fingerprint_of "$edited_root")"
    if [ "$edited_fp" != "$baseline_fp" ]; then
        echo "PASS: $scenario (fingerprint flipped)"
        pass_count=$((pass_count + 1))
    else
        echo "FAIL: $scenario -- fingerprint did NOT change; this function/script is NOT covered"
        fail_count=$((fail_count + 1))
    fi
}

for fn in confident_edges _resolve_source_node _enclosing_symbol _target_kind; do
    edited_dir="$_fingerprint_dir/edit-$fn"
    mkdir -p "$edited_dir/mcp-server/src/atlas_aci" "$edited_dir/scripts"
    cp "$_fingerprint_dir/baseline/scripts/probe-export-confident-graph.py" "$edited_dir/scripts/"
    cp "$_fingerprint_dir/baseline/scripts/probe-assemble-graph-bundle.py" "$edited_dir/scripts/"
    python3 - "$_fingerprint_dir/baseline/mcp-server/src/atlas_aci/codegraph.py" \
        "$edited_dir/mcp-server/src/atlas_aci/codegraph.py" "$fn" << 'PYEOF'
import re
import sys

src_path, out_path, fn_name = sys.argv[1:4]
source = open(src_path).read()
pattern = re.compile(rf"(    def {re.escape(fn_name)}\([^)]*\)[^:]*:\n)")
match = pattern.search(source)
if not match:
    raise SystemExit(f"could not find def {fn_name}( in {src_path}")
insertion = match.end()
edited = (
    source[:insertion]
    + "        pass  # VERIFICATION-ONLY: proving the fingerprint covers this function\n"
    + source[insertion:]
)
open(out_path, "w").write(edited)
PYEOF
    _assert_fingerprint_flips "editing $fn() alone flips the indexer fingerprint" "$edited_dir"
done

for script in probe-export-confident-graph.py probe-assemble-graph-bundle.py; do
    edited_dir="$_fingerprint_dir/edit-$script"
    mkdir -p "$edited_dir/mcp-server/src/atlas_aci" "$edited_dir/scripts"
    cp "$_fingerprint_dir/baseline/mcp-server/src/atlas_aci/codegraph.py" \
        "$edited_dir/mcp-server/src/atlas_aci/"
    cp "$_fingerprint_dir/baseline/scripts/probe-export-confident-graph.py" "$edited_dir/scripts/"
    cp "$_fingerprint_dir/baseline/scripts/probe-assemble-graph-bundle.py" "$edited_dir/scripts/"
    printf '\n# VERIFICATION-ONLY: proving the fingerprint covers this script\n' \
        >> "$edited_dir/scripts/$script"
    _assert_fingerprint_flips "editing $script alone flips the indexer fingerprint" "$edited_dir"
done

echo ""
echo "$pass_count passed, $fail_count failed"
if [ "$fail_count" -ne 0 ]; then
    exit 1
fi
