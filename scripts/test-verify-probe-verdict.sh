#!/usr/bin/env bash
# scripts/test-verify-probe-verdict.sh
#
# Self-test for scripts/verify-probe-verdict.py — covers seven rounds of
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
#   - The staleness hole (defect 15's context): nothing tied the sidecar
#     to the indexer that produced it. Scenario 13 forges a stale
#     `indexer_fingerprint`.
#   - Two closures the checker named directly: node-identity uniqueness
#     (scenario 14: two node indices claiming the same identity) and the
#     shipped LPA partition, re-derived rather than trusted (scenario 15:
#     a pure relabeling that preserves Q/count but disagrees with a
#     genuine re-run of the algorithm).
#   - Defect 17: hashing `codegraph.py` verbatim still only certified
#     "source text matches," not "this code builds the same graph" — the
#     fingerprint is now BEHAVIOURAL (runs the real export path against a
#     committed fixture and hashes the output). The scenarios after
#     "stale indexer_fingerprint" below prove both directions plus one
#     deliberately-defended exception (`_target_kind`).
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
RESOLVE="$SCRIPT_DIR/resolve-probe-artifact.sh"
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

# ---- Scenario 14 (checker instruction): duplicate node identity ----
# Nothing else in this bundle would catch two different node INDICES
# claiming the identical (path, line, name) triple -- indices stay valid
# integers, edges stay well-formed pairs, node_count still matches.
# Degree/modularity would silently be computed over an aliased,
# ill-formed node set. Renames node index 1's identity to be identical to
# node index 0's (re-signing sha256 to match the tampered bytes).
dir="$(_scenario_dir scenario-14)"
python3 - "$dir/probe-graphs.json.gz" "$REAL_JSON" "$dir/probe-lpa-vs-louvain.json" << 'PYEOF'
import gzip
import hashlib
import json
import sys

bundle_path, sidecar_path, out_sidecar_path = sys.argv[1:4]

bundle = json.loads(gzip.decompress(open(bundle_path, "rb").read()).decode("utf-8"))
solidus = bundle["solidus"]
solidus["nodes"][1] = list(solidus["nodes"][0])  # index 1 now claims index 0's identity
tampered = gzip.compress(json.dumps(bundle, separators=(",", ":")).encode("utf-8"), mtime=0)
open(bundle_path, "wb").write(tampered)
new_sha256 = hashlib.sha256(tampered).hexdigest()

sidecar = json.load(open(sidecar_path))
sidecar["graph_bundle"]["sha256"] = new_sha256
json.dump(sidecar, open(out_sidecar_path, "w"))
PYEOF
_assert_exit "forged: two node indices claim the identical (path, line, name) identity" \
    "$dir/probe-lpa-vs-louvain.json" 1

# ---- Scenario 15 (checker instruction): shipped LPA partition mismatch ----
# Isolating this from the count/Q guards specifically: swaps community
# IDs 0 and 1 throughout `lpa_labels` -- a pure relabeling that preserves
# the PARTITION (same grouping, so node_count/edge_count/
# lpa_community_count/LPA_Q are all UNCHANGED, since modularity and
# community counts are invariant under any bijective relabeling of
# community IDs) but no longer matches the SPECIFIC canonical numbering
# (0..N-1 by ascending smallest-member node) the shipped algorithm
# actually assigns. Only a genuine re-run of the algorithm -- not a count
# or a Q check -- can catch a mislabeling that preserves both.
dir="$(_scenario_dir scenario-15)"
python3 - "$dir/probe-graphs.json.gz" "$REAL_JSON" "$dir/probe-lpa-vs-louvain.json" << 'PYEOF'
import gzip
import hashlib
import json
import sys

bundle_path, sidecar_path, out_sidecar_path = sys.argv[1:4]

bundle = json.loads(gzip.decompress(open(bundle_path, "rb").read()).decode("utf-8"))
solidus = bundle["solidus"]
labels = solidus["lpa_labels"]
solidus["lpa_labels"] = [1 if label == 0 else 0 if label == 1 else label for label in labels]
tampered = gzip.compress(json.dumps(bundle, separators=(",", ":")).encode("utf-8"), mtime=0)
open(bundle_path, "wb").write(tampered)
new_sha256 = hashlib.sha256(tampered).hexdigest()

sidecar = json.load(open(sidecar_path))
sidecar["graph_bundle"]["sha256"] = new_sha256
json.dump(sidecar, open(out_sidecar_path, "w"))
PYEOF
_assert_exit \
    "forged: lpa_labels relabeled (community IDs 0/1 swapped, same partition/Q/count) disagrees with the shipped algorithm re-run" \
    "$dir/probe-lpa-vs-louvain.json" 1

# ---- Behavioural fingerprint (checker defect 17): the fingerprint now
# runs scripts/fingerprint-fixture/ through the ACTUAL export path
# (probe-export-confident-graph.py, via `uv run --frozen` inside a real
# mcp-server environment) and hashes what comes out, not codegraph.py's
# source text. Proving this needs a REAL, runnable copy of the repo (a
# full mcp-server/ + scripts/ tree, uv-synced once) -- one throwaway copy
# is built here and reused across every scenario below (editing one file,
# measuring, then reverting that same file), rather than a fresh
# from-scratch tree per scenario, since each scenario would otherwise pay
# a full `uv sync` cost.
_fp_root="$TMP_DIR/fingerprint-behavioural"
mkdir -p "$_fp_root"
rsync -a --exclude='.venv' --exclude='.atlas' --exclude='__pycache__' \
    --exclude='.pytest_cache' --exclude='*.pyc' \
    "$REPO_ROOT/mcp-server/" "$_fp_root/mcp-server/"
mkdir -p "$_fp_root/scripts"
cp "$REPO_ROOT/scripts/probe-export-confident-graph.py" "$_fp_root/scripts/"
cp -r "$REPO_ROOT/scripts/fingerprint-fixture" "$_fp_root/scripts/"

_fingerprint_of_fp_root() {
    python3 -c "
import importlib.util
from pathlib import Path
spec = importlib.util.spec_from_file_location('verify_probe_verdict', '$VERIFY')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
print(mod.compute_indexer_fingerprint(Path('$_fp_root')))
"
}

echo "(building the one-time throwaway uv environment for behavioural fingerprint scenarios...)"
baseline_fp="$(_fingerprint_of_fp_root)"

_CODEGRAPH_COPY="$_fp_root/mcp-server/src/atlas_aci/codegraph.py"
_EXPORT_SCRIPT_COPY="$_fp_root/scripts/probe-export-confident-graph.py"

_assert_fingerprint_after_edit() {
    local scenario="$1" expect_flip="$2"
    local edited_fp
    edited_fp="$(_fingerprint_of_fp_root)"
    if { [ "$expect_flip" = "yes" ] && [ "$edited_fp" != "$baseline_fp" ]; } || \
       { [ "$expect_flip" = "no" ] && [ "$edited_fp" = "$baseline_fp" ]; }; then
        echo "PASS: $scenario"
        pass_count=$((pass_count + 1))
    else
        echo "FAIL: $scenario -- expected flip=$expect_flip, baseline=$baseline_fp got=$edited_fp"
        fail_count=$((fail_count + 1))
    fi
}

# ---- Direction 1: a semantically-null edit must NOT move the fingerprint ----
cp "$_CODEGRAPH_COPY" "$_CODEGRAPH_COPY.bak"
python3 -c "
path = '$_CODEGRAPH_COPY'
src = open(path).read()
old = 'SCHEMA_EPOCH = 5'
assert old in src
open(path, 'w').write(src.replace(old, old + '  # semantically-null comment, verification only', 1))
"
_assert_fingerprint_after_edit "semantically-null comment edit does NOT flip the fingerprint" no
mv "$_CODEGRAPH_COPY.bak" "$_CODEGRAPH_COPY"

# ---- Direction 2: editing a graph-determining function alone MUST flip it ----
# Checker-instinct self-correction: inserting a bare `pass` as the first
# statement before a function's EXISTING logic changes nothing at
# runtime (the docstring just stops being `__doc__`, the rest of the
# body still executes identically) -- that trick only ever worked
# against the OLD source-text hash, where any textual change sufficed.
# A behavioural fingerprint needs a genuine short-circuit: return a
# DIFFERENT value immediately, of the same type the function actually
# returns, so the rest of the body never runs.
declare -A _SHORT_CIRCUIT=(
    [confident_edges]="return []"
    [_resolve_source_node]="return None, None"
    [_enclosing_symbol]="return None, None"
    [_target_kind]="return None"
)
# `_target_kind` deliberately excluded from the "must flip" loop below —
# see the dedicated "must NOT flip" scenario right after it, and its
# comment, for why: verified this is a considered, defended exclusion,
# not a silent omission.
for fn in confident_edges _resolve_source_node _enclosing_symbol; do
    cp "$_CODEGRAPH_COPY" "$_CODEGRAPH_COPY.bak"
    python3 - "$_CODEGRAPH_COPY" "$fn" "${_SHORT_CIRCUIT[$fn]}" << 'PYEOF'
import re
import sys

path, fn_name, short_circuit = sys.argv[1:4]
source = open(path).read()
pattern = re.compile(rf"(    def {re.escape(fn_name)}\([^)]*\)[^:]*:\n)")
match = pattern.search(source)
if not match:
    raise SystemExit(f"could not find def {fn_name}( in {path}")
insertion = match.end()
edited = (
    source[:insertion]
    + f"        {short_circuit}  # VERIFICATION-ONLY: a genuine short-circuit, not a no-op\n"
    + source[insertion:]
)
open(path, "w").write(edited)
PYEOF
    _assert_fingerprint_after_edit "editing $fn() alone flips the behavioural fingerprint" yes
    mv "$_CODEGRAPH_COPY.bak" "$_CODEGRAPH_COPY"
done

# ---- The export script itself must also be covered ----
# Same correction as above: this script's SOURCE TEXT is no longer
# hashed at all (only its OUTPUT is) -- a trailing comment is exactly as
# inert here as `pass` was for the functions above. The genuine
# behavioural edit: force the exported edge list empty, the same shape
# of change a real regression in this script's own logic would produce.
cp "$_EXPORT_SCRIPT_COPY" "$_EXPORT_SCRIPT_COPY.bak"
python3 -c "
path = '$_EXPORT_SCRIPT_COPY'
src = open(path).read()
old = '\"edges\": [list(e) for e in canonical_edges],'
assert old in src, 'anchor not found -- probe-export-confident-graph.py output shape changed'
open(path, 'w').write(src.replace(old, '\"edges\": [],  # VERIFICATION-ONLY: a genuine output change', 1))
"
_assert_fingerprint_after_edit "editing probe-export-confident-graph.py's own output alone flips the behavioural fingerprint" yes
mv "$_EXPORT_SCRIPT_COPY.bak" "$_EXPORT_SCRIPT_COPY"

# ---- `_target_kind` must NOT flip it — a considered, defended exclusion ----
# `_target_kind()` resolves only the decorative "kind" METADATA field
# god_nodes()/communities() attach to each member for display -- it does
# NOT determine which nodes/edges exist, the LPA algorithm's outcome, or
# any field probe-export-confident-graph.py currently exports (the
# exported "nodes" are bare [path, line, name] triples; "kind" is never
# in that output at all). It has no path into the confident subgraph's
# actual SHAPE. A behavioural fingerprint that measures the shape
# correctly stays silent on a change that does not touch the shape --
# this is more honest than the earlier whole-file hash's "include it
# defensively, just in case," which could not tell the difference
# between a function that determines the graph and one that only
# decorates it. Tested here, not silently assumed.
cp "$_CODEGRAPH_COPY" "$_CODEGRAPH_COPY.bak"
python3 - "$_CODEGRAPH_COPY" "_target_kind" "${_SHORT_CIRCUIT[_target_kind]}" << 'PYEOF'
import re
import sys

path, fn_name, short_circuit = sys.argv[1:4]
source = open(path).read()
pattern = re.compile(rf"(    def {re.escape(fn_name)}\([^)]*\)[^:]*:\n)")
match = pattern.search(source)
if not match:
    raise SystemExit(f"could not find def {fn_name}( in {path}")
insertion = match.end()
edited = (
    source[:insertion]
    + f"        {short_circuit}  # VERIFICATION-ONLY: a genuine short-circuit, not a no-op\n"
    + source[insertion:]
)
open(path, "w").write(edited)
PYEOF
_assert_fingerprint_after_edit \
    "editing _target_kind() alone does NOT flip the behavioural fingerprint (decorative metadata only)" no
mv "$_CODEGRAPH_COPY.bak" "$_CODEGRAPH_COPY"

# ---- A4-shaped edit (unrelated new field in build_stats) must NOT flip it ----
# The exact regression this fix closed: an earlier revision hashed the
# WHOLE export dict including `build_stats`, so A4 adding an unrelated
# `rationale` count there flipped the fingerprint even though the
# confident subgraph never moved. Reproduces that shape directly against
# the export script's own output construction.
cp "$_CODEGRAPH_COPY" "$_CODEGRAPH_COPY.bak"
python3 -c "
path = '$_CODEGRAPH_COPY'
src = open(path).read()
old = '\"schema_epoch\": SCHEMA_EPOCH,\n        }'
assert old in src, 'anchor not found -- build_stats dict shape changed'
new = '\"schema_epoch\": SCHEMA_EPOCH,\n            \"unrelated_new_stat\": 12345,\n        }'
open(path, 'w').write(src.replace(old, new, 1))
"
_assert_fingerprint_after_edit \
    "an unrelated new build_stats field does NOT flip the behavioural fingerprint" no
mv "$_CODEGRAPH_COPY.bak" "$_CODEGRAPH_COPY"

## ---- Path-resolution scenarios (post-ESL-archive robustness) ----
#
# harden-gate.yml used to hardcode this change's probe artefacts at their
# PROPOSED-time path (.spectra/changes/aci-v2-harden-and-augment/). The
# ESL `archive` verb MOVES (never copies) a verified change folder to
# .spectra/changes/archive/<date>-<change-id>/ once it reaches its
# lifecycle terminus -- silently breaking that hardcoded path on the very
# next PR touching an A3 path. scripts/resolve-probe-artifact.sh replaces
# the fixed path with a search under .spectra/changes/ (one recursive
# search covers active AND archive/ subtrees) that fails loudly on zero
# or more than one match rather than picking a first match. These four
# scenarios build disposable .spectra/changes/ trees (never the real repo
# tree) and prove: (A) resolves + verifies in the CURRENT active-location
# layout, (B) resolves + verifies IDENTICALLY once moved to a simulated
# archive/2026-07-10-aci-v2-harden-and-augment/ folder, (C) fails loudly
# when the artefact is absent, (D) fails loudly when it is duplicated
# across both an active and an archived copy (ambiguity is an error, not
# a first-match fallback).

if [ ! -x "$RESOLVE" ]; then
    echo "FAIL: $RESOLVE is missing or not executable"
    fail_count=$((fail_count + 1))
else
    PATHRES_ROOT="$TMP_DIR/pathres"
    mkdir -p "$PATHRES_ROOT"

    _assert_resolve_and_verify() {
        local scenario="$1" search_root="$2" expected_path="$3"
        local resolved resolve_exit=0
        resolved="$("$RESOLVE" probe-lpa-vs-louvain.json "$search_root" 2>&1)" || resolve_exit=$?
        if [ "$resolve_exit" -ne 0 ]; then
            echo "FAIL: $scenario -- resolve-probe-artifact.sh exited $resolve_exit: $resolved"
            fail_count=$((fail_count + 1))
            return
        fi
        if [ "$resolved" != "$expected_path" ]; then
            echo "FAIL: $scenario -- resolved '$resolved', expected '$expected_path'"
            fail_count=$((fail_count + 1))
            return
        fi
        if python3 "$VERIFY" "$resolved" > /dev/null 2>&1; then
            echo "PASS: $scenario (resolved=$resolved, verify-probe-verdict.py: PASS)"
            pass_count=$((pass_count + 1))
        else
            echo "FAIL: $scenario -- resolved correctly but verify-probe-verdict.py rejected it"
            fail_count=$((fail_count + 1))
        fi
    }

    _assert_resolve_fails() {
        local scenario="$1" search_root="$2"
        local output resolve_exit=0
        output="$("$RESOLVE" probe-lpa-vs-louvain.json "$search_root" 2>&1)" || resolve_exit=$?
        if [ "$resolve_exit" -ne 0 ]; then
            echo "PASS: $scenario (exit=$resolve_exit: $output)"
            pass_count=$((pass_count + 1))
        else
            echo "FAIL: $scenario -- expected a non-zero exit, got 0 (resolved: $output)"
            fail_count=$((fail_count + 1))
        fi
    }

    # ---- Scenario A: CURRENT active-location layout ----
    dir="$PATHRES_ROOT/active/.spectra/changes/aci-v2-harden-and-augment"
    mkdir -p "$dir"
    cp "$REAL_JSON" "$dir/probe-lpa-vs-louvain.json"
    cp "$REAL_BUNDLE" "$dir/probe-graphs.json.gz"
    _assert_resolve_and_verify \
        "gate resolves + verifies against the ACTIVE change-folder location" \
        "$PATHRES_ROOT/active/.spectra/changes" \
        "$dir/probe-lpa-vs-louvain.json"

    # ---- Scenario B: simulated archived location (post `tonberry archive`) ----
    dir="$PATHRES_ROOT/archived/.spectra/changes/archive/2026-07-10-aci-v2-harden-and-augment"
    mkdir -p "$dir"
    cp "$REAL_JSON" "$dir/probe-lpa-vs-louvain.json"
    cp "$REAL_BUNDLE" "$dir/probe-graphs.json.gz"
    _assert_resolve_and_verify \
        "gate resolves + verifies IDENTICALLY against the ARCHIVED location (simulated archive/2026-07-10-aci-v2-harden-and-augment/)" \
        "$PATHRES_ROOT/archived/.spectra/changes" \
        "$dir/probe-lpa-vs-louvain.json"

    # ---- Scenario C: absent -- must fail loudly, never silently pass zero artefacts ----
    dir="$PATHRES_ROOT/absent/.spectra/changes"
    mkdir -p "$dir"
    _assert_resolve_fails \
        "gate fails loudly when no probe artefact exists anywhere under .spectra/changes/" \
        "$dir"

    # ---- Scenario D: duplicated across active AND archived -- ambiguity is a hard error ----
    dup_root="$PATHRES_ROOT/duplicated/.spectra/changes"
    mkdir -p "$dup_root/aci-v2-harden-and-augment" \
             "$dup_root/archive/2026-07-10-aci-v2-harden-and-augment"
    cp "$REAL_JSON" "$dup_root/aci-v2-harden-and-augment/probe-lpa-vs-louvain.json"
    cp "$REAL_JSON" "$dup_root/archive/2026-07-10-aci-v2-harden-and-augment/probe-lpa-vs-louvain.json"
    _assert_resolve_fails \
        "gate fails loudly when the probe artefact is duplicated (active AND archived copies both present)" \
        "$dup_root"
fi

echo ""
echo "$pass_count passed, $fail_count failed"
if [ "$fail_count" -ne 0 ]; then
    exit 1
fi
