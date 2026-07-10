#!/usr/bin/env bash
# scripts/test-verify-export-size.sh
#
# Self-test for scripts/verify-export-size.py (AC-REL-2) — the same
# defect-10 lesson this campaign keeps re-learning, applied to the
# smallest possible check: a verdict LABEL is an assertion, not a
# measurement, until something recomputes it from the recorded numbers.
# Every scenario below forges the sidecar and asserts the verifier
# rejects it; scenario 1 asserts the REAL recorded sidecar is accepted.
#
# Usage: scripts/test-verify-export-size.sh
# Exit 0 if every scenario matches its expected outcome, 1 otherwise.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERIFY="$SCRIPT_DIR/verify-export-size.py"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REAL_JSON="$REPO_ROOT/.spectra/changes/aci-v2-harden-and-augment/export-size-spree.json"

if [ ! -f "$REAL_JSON" ]; then
    echo "SKIP: $REAL_JSON not present (A5's export-size measurement not yet recorded)."
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
_assert_exit "real recorded export-size sidecar (genuine PASS)" "$REAL_JSON" 0

# ---- Scenario 2: the defect-10 shape — export_bytes above ceiling, label left at PASS ----
python3 - "$REAL_JSON" "$TMP_DIR/scenario-2.json" << 'PYEOF'
import json
import sys

data = json.load(open(sys.argv[1]))
data["export_bytes"] = data["ceiling_bytes"] + 1  # one byte over
data["recorded_verdict"] = "PASS"  # left untouched, exactly like the checker's forgery
json.dump(data, open(sys.argv[2], "w"))
PYEOF
_assert_exit "forged: export_bytes one byte over ceiling, recorded_verdict left at PASS" \
    "$TMP_DIR/scenario-2.json" 1

# ---- Scenario 3: the mirror-image lie — a recorded CUT over a genuinely passing number ----
python3 - "$REAL_JSON" "$TMP_DIR/scenario-3.json" << 'PYEOF'
import json
import sys

data = json.load(open(sys.argv[1]))
data["recorded_verdict"] = "CUT"  # numbers still genuinely pass
json.dump(data, open(sys.argv[2], "w"))
PYEOF
_assert_exit "forged: recorded CUT over a genuinely passing export_bytes" \
    "$TMP_DIR/scenario-3.json" 1

# ---- Scenario 4: wrong pinned SHA (a different repo's number smuggled in) ----
python3 - "$REAL_JSON" "$TMP_DIR/scenario-4.json" << 'PYEOF'
import json
import sys

data = json.load(open(sys.argv[1]))
data["repo"]["pinned_sha"] = "0" * 40
json.dump(data, open(sys.argv[2], "w"))
PYEOF
_assert_exit "forged: pinned_sha does not match the frozen AC-REL-2 SHA" \
    "$TMP_DIR/scenario-4.json" 1

# ---- Scenario 5: a softened ceiling (raise the bar to fit a bigger export) ----
python3 - "$REAL_JSON" "$TMP_DIR/scenario-5.json" << 'PYEOF'
import json
import sys

data = json.load(open(sys.argv[1]))
data["ceiling_bytes"] = 999_999_999_999  # absurdly softened
# export_bytes left genuinely under the REAL ceiling but this ceiling
# no longer matches the frozen AC-REL-2 value -- must be rejected on
# that basis, independent of whether the (now-meaningless) comparison
# would also pass.
json.dump(data, open(sys.argv[2], "w"))
PYEOF
_assert_exit "forged: ceiling_bytes does not match the frozen AC-REL-2 ceiling" \
    "$TMP_DIR/scenario-5.json" 1

echo ""
echo "$pass_count passed, $fail_count failed"
[ "$fail_count" -eq 0 ]
