#!/usr/bin/env bash
# scripts/test-verify-probe-verdict.sh
#
# Self-test for scripts/verify-probe-verdict.py — the fix for checker
# MAJOR-1 (AC-A3-1/F7, the tenth instance of "the check measures a proxy
# instead of the invariant" in this campaign, and the sharpest one: the
# criterion literally names the failure mode it exists to close, and the
# gate reintroduced it).
#
# The checker proved the old gate (`grep -qiE "verdict.*:.*pass"` over the
# artefact's PROSE) was defeatable: forge a copy with LPA_Q=0.11111 (below
# the 0.30 floor, failing clauses 2 and 3 on both repos), leave
# `verdict: PASS` untouched, and the old gate passed it — no Q value was
# ever parsed, no clause ever evaluated.
#
# This self-test forges the SAME class of artefact (failing numbers under
# a recorded PASS) plus its mirror image (a recorded CUT over genuinely
# passing numbers — "a recorded CUT over passing numbers is equally a
# lie") and asserts scripts/verify-probe-verdict.py rejects both, then
# asserts it accepts the real, currently-recorded probe sidecar.
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

echo ""
echo "$pass_count passed, $fail_count failed"
if [ "$fail_count" -ne 0 ]; then
    exit 1
fi
