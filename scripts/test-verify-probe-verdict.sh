#!/usr/bin/env bash
# scripts/test-verify-probe-verdict.sh
#
# Self-test for scripts/verify-probe-verdict.py — covers three rounds of
# the same defect class ("the check measures a proxy instead of the
# invariant"), each found by attacking the previous round's fix:
#
#   - Defect 10 (MAJOR-1): the gate used to `grep -qiE "verdict.*:.*pass"`
#     the artefact's PROSE — never a Q value, never a clause. Scenarios
#     2-4 below forge failing/flipped numbers under an unchanged label.
#   - Defect 11: the recomputation it was replaced with still read its
#     BAR (`q_struct`/`r`) FROM the sidecar under audit — a verifier that
#     takes its bar from the file it grades can be handed a softened bar
#     next to failing numbers. Scenario 5 forges exactly that.
#   - Defect 12: nothing asserted WHICH repos, or how many, were graded —
#     AC-A3-4 requires PASS on BOTH pinned repos, independently, and
#     dropping the inconvenient one is the most direct way to lie about a
#     two-repo result. Scenarios 6-9 forge a dropped repo, an added repo,
#     a wrong seed set, and a wrong pinned SHA.
#
# This self-test asserts scripts/verify-probe-verdict.py rejects all nine
# forgeries and accepts the real, currently-recorded probe sidecar.
#
# Usage: scripts/test-verify-probe-verdict.sh
# Exit 0 if every scenario matches its expected outcome, 1 otherwise.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERIFY="$SCRIPT_DIR/verify-probe-verdict.py"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REAL_JSON="$REPO_ROOT/.spectra/changes/aci-v2-harden-and-augment/probe-lpa-vs-louvain.json"

if [ ! -f "$REAL_JSON" ]; then
    echo "SKIP: $REAL_JSON not present (A3 not yet built in this tree)."
    exit 0
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

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

# ---- Scenario 1: the REAL recorded sidecar must be accepted (exit 0) ----
_assert_exit "real recorded probe sidecar (genuine PASS)" "$REAL_JSON" 0

# ---- Scenario 2: the checker's exact forgery — failing numbers under a recorded PASS ----
python3 - "$REAL_JSON" "$TMP_DIR/forged-pass-over-failing.json" << 'PYEOF'
import json
import sys

data = json.load(open(sys.argv[1]))
for repo in data["repos"]:
    repo["lpa_q"] = 0.11111  # below the 0.30 floor -- fails clauses 2 AND 3
data["recorded_verdict"] = "PASS"  # left untouched, exactly like the checker's forgery
json.dump(data, open(sys.argv[2], "w"))
PYEOF
_assert_exit "forged: failing numbers (LPA_Q=0.11111) under recorded PASS" \
    "$TMP_DIR/forged-pass-over-failing.json" 1

# ---- Scenario 3: the mirror-image lie — a recorded CUT over genuinely passing numbers ----
python3 - "$REAL_JSON" "$TMP_DIR/forged-cut-over-passing.json" << 'PYEOF'
import json
import sys

data = json.load(open(sys.argv[1]))
data["recorded_verdict"] = "CUT"
json.dump(data, open(sys.argv[2], "w"))
PYEOF
_assert_exit "forged: recorded CUT over genuinely passing numbers" \
    "$TMP_DIR/forged-cut-over-passing.json" 1

# ---- Scenario 4: a single repo failing clause 1 (Louvain_Q_median < 0.30) must also flip the verdict ----
python3 - "$REAL_JSON" "$TMP_DIR/forged-median-floor.json" << 'PYEOF'
import json
import sys

data = json.load(open(sys.argv[1]))
data["repos"][0]["louvain_q_by_seed"] = {str(s): 0.1 for s in range(10)}
data["recorded_verdict"] = "PASS"
json.dump(data, open(sys.argv[2], "w"))
PYEOF
_assert_exit "forged: one repo's Louvain_Q_median below the 0.30 floor, recorded PASS" \
    "$TMP_DIR/forged-median-floor.json" 1

# ---- Scenario 5 (checker defect 11): softened bar alongside a failing LPA_Q ----
# The verifier must grade against its OWN hardcoded bar, never the
# sidecar's copy. This forges q_struct/r DOWN and hands it LPA_Q=0.20,
# which fails the FROZEN bar's clause 2 (>= 0.30) but would pass the
# forged, softened bar if the verifier ever trusted it.
python3 - "$REAL_JSON" "$TMP_DIR/forged-softened-bar.json" << 'PYEOF'
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
    "$TMP_DIR/forged-softened-bar.json" 1

# ---- Scenario 5b: a STALE bar declaration with otherwise-genuine, passing numbers ----
# Scenario 5 combines a wrong bar with numbers that also fail the frozen
# bar -- the hardcoded-arithmetic fix alone rejects it, independent of
# whether the sidecar's own bar/frozen-constant comparison ever runs. This
# scenario isolates that comparison specifically: the bar block disagrees
# with the frozen constants, but every number is left exactly as recorded
# (genuinely passing under the frozen bar) -- only
# _check_declared_bar_matches_frozen can catch this one; the arithmetic
# alone would happily compute PASS and match the recorded PASS.
python3 - "$REAL_JSON" "$TMP_DIR/forged-stale-bar-only.json" << 'PYEOF'
import json
import sys

data = json.load(open(sys.argv[1]))
data["bar"]["q_struct"] = 0.5  # disagrees with the frozen 0.30 -- numbers untouched
json.dump(data, open(sys.argv[2], "w"))
PYEOF
_assert_exit "forged: stale bar declaration (q_struct=0.5) over otherwise-genuine passing numbers" \
    "$TMP_DIR/forged-stale-bar-only.json" 1

# ---- Scenario 6 (checker defect 12): dropped repo (solidus removed) ----
python3 - "$REAL_JSON" "$TMP_DIR/forged-dropped-repo.json" << 'PYEOF'
import json
import sys

data = json.load(open(sys.argv[1]))
data["repos"] = [r for r in data["repos"] if r["name"] != "solidus"]
json.dump(data, open(sys.argv[2], "w"))
PYEOF
_assert_exit "forged: solidus dropped from repos (only spree remains)" \
    "$TMP_DIR/forged-dropped-repo.json" 1

# ---- Scenario 7 (checker defect 12): added repo (a third, unpinned entry) ----
python3 - "$REAL_JSON" "$TMP_DIR/forged-added-repo.json" << 'PYEOF'
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
    "$TMP_DIR/forged-added-repo.json" 1

# ---- Scenario 8 (checker defect 12): wrong seed set (seed 9 replaced with seed 10) ----
python3 - "$REAL_JSON" "$TMP_DIR/forged-wrong-seed-set.json" << 'PYEOF'
import json
import sys

data = json.load(open(sys.argv[1]))
repo = data["repos"][0]
seeds = repo["louvain_q_by_seed"]
seeds["10"] = seeds.pop("9")  # still 10 seeds total, but not 0..9
json.dump(data, open(sys.argv[2], "w"))
PYEOF
_assert_exit "forged: wrong seed set (seed 9 relabeled as seed 10)" \
    "$TMP_DIR/forged-wrong-seed-set.json" 1

# ---- Scenario 9 (checker defect 12): wrong pinned SHA (solidus's SHA altered) ----
python3 - "$REAL_JSON" "$TMP_DIR/forged-wrong-sha.json" << 'PYEOF'
import json
import sys

data = json.load(open(sys.argv[1]))
for repo in data["repos"]:
    if repo["name"] == "solidus":
        repo["pinned_sha"] = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
json.dump(data, open(sys.argv[2], "w"))
PYEOF
_assert_exit "forged: solidus's pinned_sha altered to an unpinned value" \
    "$TMP_DIR/forged-wrong-sha.json" 1

echo ""
echo "$pass_count passed, $fail_count failed"
if [ "$fail_count" -ne 0 ]; then
    exit 1
fi
