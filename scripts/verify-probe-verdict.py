#!/usr/bin/env python3
"""D3a probe-verdict verifier — the AC-A3-1/F7 fix (checker defect 11).

THE FIRST FIX (defect 10, already landed): `.github/workflows/harden-gate.yml`
used to gate A3 on `grep -qiE "verdict.*:.*pass" "$PROBE_ARTIFACT"` — a check
of the PROSE LABEL, never a single Q value. This script replaced that with
actual recomputation of the three-clause rule from raw numbers.

THE ELEVENTH DEFECT (checker, this pass): the recomputation still read its
BAR from the artefact under audit —

    q_struct = data["bar"]["q_struct"]
    r        = data["bar"]["r"]

The checker softened the bar inside a forged sidecar (`q_struct: 0.01`,
`r: 0.10`) and handed it `LPA_Q = 0.20` — a number that FAILS the real,
frozen bar (`Q_struct=0.30`) but passes the artefact's own softened one.
The verifier happily recomputed against the softened bar and reported a
match. This defeats the entire point of D3a's pre-registration: `R=0.85`
and `Q_struct=0.30` were fixed by FORGE *before* a line of `communities()`
was written, specifically so the measurement could never choose the bar
it is graded against. A verifier that takes the bar FROM the file it is
auditing hands that power right back to whoever writes the file.

THE FIX: `Q_struct`/`R` (and the rest of the bar: `K`, `seeds`,
`resolution`) are now this script's OWN facts — sourced from the frozen
criteria (`sha256:5c3adddbd075a7c12bdd965ee760484a04e5a9a6a4ce05302cbd7bc4147fc7e7`)
and FORGE's D3a pre-registration record, HARDCODED here, never read from
or derived from the sidecar under audit. The sidecar's own declared `bar`
block is still read and cross-checked against these hardcoded values —
ANY mismatch is itself a hard failure (a tampered or stale sidecar must
be loud, never silently re-graded against whatever it happens to claim
about itself). The pass/fail arithmetic always runs against the
HARDCODED constants, never the artefact's copies of them, even when the
two happen to agree.

Usage:
  python3 scripts/verify-probe-verdict.py <sidecar_json_path>

Exit 0 iff (a) the sidecar's declared bar agrees with the frozen
constants below, AND (b) the recorded verdict equals the mechanical
evaluation of the recorded per-seed numbers against those frozen
constants. Exit 1 on any provenance mismatch OR any verdict/arithmetic
mismatch, with a diagnostic naming exactly what disagreed. Exit 2 on
usage / malformed-input errors.
"""

from __future__ import annotations

import json
import statistics
import sys
from typing import Any

# ---------------------------------------------------------------------------
# FROZEN, EXTERNAL FACTS — D3a pre-registration (FORGE), frozen criteria
# sha256:5c3adddbd075a7c12bdd965ee760484a04e5a9a6a4ce05302cbd7bc4147fc7e7.
#
# Checker defect 11: these must NEVER be read from, or derived from, the
# sidecar under audit — a verifier that takes its bar from the file it is
# grading can be handed a softened bar next to failing numbers and wave
# them through. Do not parameterize any of the five values below from
# JSON input; the sidecar's own copies are cross-checked against these,
# never substituted for them.
# ---------------------------------------------------------------------------
FROZEN_Q_STRUCT: float = 0.30
FROZEN_R: float = 0.85
FROZEN_K: int = 10
FROZEN_SEEDS: tuple[int, ...] = tuple(range(10))  # 0..9, frozen
FROZEN_RESOLUTION: float = 1.0


class ProvenanceError(Exception):
    """The sidecar's declared facts (here: the bar constants) disagree
    with the frozen, external constants above. This is raised for ANY
    such mismatch — never silently reconciled by preferring either side's
    value; the mismatch itself is the finding."""


def _check_declared_bar_matches_frozen(bar: dict[str, Any]) -> None:
    mismatches: list[str] = []
    if bar.get("q_struct") != FROZEN_Q_STRUCT:
        mismatches.append(
            f"q_struct: sidecar declares {bar.get('q_struct')!r}, frozen is {FROZEN_Q_STRUCT!r}"
        )
    if bar.get("r") != FROZEN_R:
        mismatches.append(f"r: sidecar declares {bar.get('r')!r}, frozen is {FROZEN_R!r}")
    if bar.get("k") != FROZEN_K:
        mismatches.append(f"k: sidecar declares {bar.get('k')!r}, frozen is {FROZEN_K!r}")
    if bar.get("resolution") != FROZEN_RESOLUTION:
        declared_res = bar.get("resolution")
        mismatches.append(f"resolution: declares {declared_res!r}, frozen is {FROZEN_RESOLUTION!r}")
    declared_seeds = tuple(sorted(bar.get("seeds", [])))
    if declared_seeds != FROZEN_SEEDS:
        mismatches.append(
            f"bar.seeds: sidecar declares {declared_seeds!r}, frozen is {FROZEN_SEEDS!r}"
        )
    if mismatches:
        raise ProvenanceError(
            "sidecar's declared bar disagrees with the frozen, external D3a "
            "constants -- a mismatch is itself a finding, never silently "
            "reconciled:\n  " + "\n  ".join(mismatches)
        )


def evaluate_repo(repo: dict[str, Any]) -> dict[str, Any]:
    seed_q = repo["louvain_q_by_seed"]
    # Sorted by seed (as an int, not string-lexically — "10" would
    # otherwise sort before "2") so the recomputation is itself
    # deterministic/reproducible, though median doesn't care about order.
    qs = [seed_q[k] for k in sorted(seed_q, key=int)]
    median = statistics.median(qs)
    lpa_q = repo["lpa_q"]
    threshold3 = FROZEN_R * median
    clause1 = median >= FROZEN_Q_STRUCT
    clause2 = lpa_q >= FROZEN_Q_STRUCT
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

    # Provenance gate FIRST, before any arithmetic runs at all — checker
    # defect 11. Checks the sidecar's OWN bar declaration against the
    # hardcoded, external facts above; never feeds a sidecar value into
    # the evaluation itself.
    try:
        _check_declared_bar_matches_frozen(data.get("bar", {}))
    except ProvenanceError as e:
        print(f"FAIL (AC-A3-1/F7, provenance): {e}", file=sys.stderr)
        return 1

    recorded_verdict = str(data["recorded_verdict"]).strip().upper()

    if not data["repos"]:
        print("FAIL: sidecar names zero repos — nothing to evaluate.", file=sys.stderr)
        return 2

    evaluations = [evaluate_repo(repo) for repo in data["repos"]]
    computed_verdict = "PASS" if all(e["repo_pass"] for e in evaluations) else "CUT"

    print(f"frozen bar (hardcoded): Q_struct={FROZEN_Q_STRUCT} R={FROZEN_R}")
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
            f"the mechanical evaluation of the recorded numbers against the FROZEN "
            f"bar ('{computed_verdict}'). A label is not evidence; the gate "
            "recomputes the arithmetic every time, against constants it holds "
            "itself, never against constants the artefact supplies.",
            file=sys.stderr,
        )
        return 1

    print(
        f"OK (AC-A3-1/F7): recorded verdict '{recorded_verdict}' matches the "
        "recomputed verdict under the frozen, hardcoded bar; the sidecar's "
        "declared bar agrees with those same frozen constants."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
