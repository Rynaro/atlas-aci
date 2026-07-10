#!/usr/bin/env python3
"""D3a probe-verdict verifier — the AC-A3-1/F7 fix (checker MAJOR-1).

THE DEFECT THIS REPLACES: `.github/workflows/harden-gate.yml` used to gate
A3 on:

    grep -qiE "verdict.*:.*pass" "$PROBE_ARTIFACT"

— a check of the PROSE LABEL in `probe-lpa-vs-louvain.md`, never a single
Q value, never a single clause. The checker demonstrated the hole
directly: a forged copy of the artefact with `LPA_Q = 0.11111` (below the
absolute 0.30 floor, failing clauses 2 and 3 on both repos) and
`verdict: PASS` left untouched sailed straight through. AC-A3-1's own
frozen text names this exact failure mode (F7): "the check SHALL also
assert the recorded verdict equals the mechanical evaluation of the
recorded numbers (a maker cannot record failing numbers under a PASS
label)."

THE FIX: this script reads a MACHINE-READABLE sidecar
(`probe-lpa-vs-louvain.json`) — per repo: the pinned SHA, node/edge
counts, `LPA_Q`, and the raw per-seed Louvain `Q` values — and:

  1. Recomputes `Louvain_Q_median` per repo from the raw per-seed values
     itself (via `statistics.median`), NEVER trusting a precomputed
     median field — there isn't one in the sidecar schema, on purpose.
  2. Evaluates the three frozen clauses per repo, INDEPENDENTLY, never
     averaged across repos:
         Louvain_Q_median >= Q_struct (0.30)
         LPA_Q            >= Q_struct (0.30)
         LPA_Q            >= R (0.85) * Louvain_Q_median
  3. Computes an overall verdict — PASS iff every repo passes all three
     clauses, else CUT — and compares it to the sidecar's own
     `recorded_verdict` field.
  4. Fails (exit 1) if the two differ in EITHER direction: a maker
     recording PASS over failing numbers is a lie, and so is a maker
     recording CUT over genuinely passing numbers (per the checker's
     explicit instruction — both directions are dishonesty about what
     the numbers say, not just the one that would ship something bad).

Usage:
  python3 scripts/verify-probe-verdict.py <sidecar_json_path>

Exit 0 iff the recorded verdict equals the mechanical evaluation of the
sidecar's own recorded numbers. Exit 1 otherwise, with a per-repo,
per-clause diagnostic naming exactly what disagreed. Exit 2 on usage /
malformed-input errors.
"""

from __future__ import annotations

import json
import statistics
import sys
from typing import Any


def evaluate_repo(repo: dict[str, Any], q_struct: float, r: float) -> dict[str, Any]:
    seed_q = repo["louvain_q_by_seed"]
    # Sorted by seed (as an int, not string-lexically — "10" would
    # otherwise sort before "2") so the recomputation is itself
    # deterministic/reproducible, though median doesn't care about order.
    qs = [seed_q[k] for k in sorted(seed_q, key=int)]
    median = statistics.median(qs)
    lpa_q = repo["lpa_q"]
    threshold3 = r * median
    clause1 = median >= q_struct
    clause2 = lpa_q >= q_struct
    clause3 = lpa_q >= threshold3
    return {
        "name": repo["name"],
        "median": median,
        "lpa_q": lpa_q,
        "threshold3": threshold3,
        "clause1_median_ge_q_struct": clause1,
        "clause2_lpa_ge_q_struct": clause2,
        "clause3_lpa_ge_r_times_median": clause3,
        "repo_pass": clause1 and clause2 and clause3,
    }


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <sidecar_json_path>", file=sys.stderr)
        return 2

    try:
        with open(sys.argv[1]) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"FAIL: could not read/parse sidecar JSON: {e}", file=sys.stderr)
        return 2

    q_struct = data["bar"]["q_struct"]
    r = data["bar"]["r"]
    recorded_verdict = str(data["recorded_verdict"]).strip().upper()

    if not data["repos"]:
        print("FAIL: sidecar names zero repos — nothing to evaluate.", file=sys.stderr)
        return 2

    evaluations = [evaluate_repo(repo, q_struct, r) for repo in data["repos"]]
    computed_verdict = "PASS" if all(e["repo_pass"] for e in evaluations) else "CUT"

    print(f"bar: Q_struct={q_struct} R={r}")
    for e in evaluations:
        print(
            f"  {e['name']}: median={e['median']!r} lpa_q={e['lpa_q']!r} "
            f"r*median={e['threshold3']!r} | "
            f"clause1(median>=Q_struct)={e['clause1_median_ge_q_struct']} "
            f"clause2(lpa_q>=Q_struct)={e['clause2_lpa_ge_q_struct']} "
            f"clause3(lpa_q>=R*median)={e['clause3_lpa_ge_r_times_median']} "
            f"-> repo_pass={e['repo_pass']}"
        )
    print(f"computed_verdict={computed_verdict} recorded_verdict={recorded_verdict}")

    if computed_verdict != recorded_verdict:
        print(
            f"FAIL (AC-A3-1/F7): recorded verdict '{recorded_verdict}' does not equal "
            f"the mechanical evaluation of the recorded numbers ('{computed_verdict}'). "
            "A label is not evidence; the gate recomputes the arithmetic every time.",
            file=sys.stderr,
        )
        return 1

    print(f"OK (AC-A3-1/F7): recorded verdict '{recorded_verdict}' matches the recomputed verdict.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
