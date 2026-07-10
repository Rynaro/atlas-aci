#!/usr/bin/env python3
"""scripts/verify-export-size.py — AC-REL-2 verifier.

AC-REL-2 asks for one real-world data point: the export size of the
larger pinned reference repo (Spree @ a frozen SHA), measured once at
P3/release-prep and checked against a 104,857,600 byte (100 MiB)
ceiling. Re-cloning and re-indexing a ~2,200-file Ruby engine on every PR
would be expensive and, per the D3a probe's own established pattern
(`probe-lpa-vs-louvain.md`/`verify-probe-verdict.py`), pointless: the
number is deterministic given fixed source, so it is measured once and
recorded, and CI asserts the *recorded* number.

Applying the SAME defect-10 lesson this campaign keeps re-learning: a
verdict label in the sidecar is an assertion, not a measurement, until
something recomputes it. This script never trusts `recorded_verdict` as
printed — it recomputes `export_bytes <= ceiling_bytes` from the
sidecar's own recorded numbers and fails if the recorded label disagrees
with that computation, in EITHER direction (a recorded PASS over a
number that has since drifted above the ceiling is exactly as much a
lie as a recorded CUT over a genuinely passing number). `pinned_sha` and
`ceiling_bytes` are cross-checked against this script's own hardcoded,
frozen constants — never read from the sidecar as the verifier's actual
input — mirroring `verify-probe-verdict.py`'s bar/repo-set checks.

Exit 0 iff: the sidecar's `repo.pinned_sha` matches the frozen SHA,
`ceiling_bytes` matches the frozen ceiling, and the recomputed verdict
(`export_bytes <= ceiling_bytes`) matches the recorded `recorded_verdict`
label. Exit 1 (with a labeled reason) otherwise. Exit 2 on malformed
input (missing file, bad JSON, missing keys).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Frozen constants (AC-REL-2's own text) — never read from the sidecar.
FROZEN_PINNED_SHA = "6699cde44303ea85ef6e56c5e87c44a738ab73fc"
FROZEN_CEILING_BYTES = 104_857_600  # 100 MiB, per AC-REL-2's literal text


class VerificationError(Exception):
    pass


def verify(data: dict[str, Any]) -> None:
    repo = data.get("repo", {})
    pinned_sha = repo.get("pinned_sha")
    if pinned_sha != FROZEN_PINNED_SHA:
        raise VerificationError(
            f"repo.pinned_sha {pinned_sha!r} does not match the frozen AC-REL-2 SHA "
            f"{FROZEN_PINNED_SHA!r} -- this sidecar does not attest the pinned repo."
        )

    ceiling_bytes = data.get("ceiling_bytes")
    if ceiling_bytes != FROZEN_CEILING_BYTES:
        raise VerificationError(
            f"ceiling_bytes {ceiling_bytes!r} does not match the frozen AC-REL-2 ceiling "
            f"{FROZEN_CEILING_BYTES!r} -- a softened (or tightened) ceiling recorded here "
            "would never be caught by anything else."
        )

    export_bytes = data.get("export_bytes")
    if not isinstance(export_bytes, int) or export_bytes <= 0:
        raise VerificationError(f"export_bytes {export_bytes!r} is not a positive integer.")

    recomputed_verdict = "PASS" if export_bytes <= ceiling_bytes else "CUT"
    recorded_verdict = str(data.get("recorded_verdict", "")).strip().upper()
    if recomputed_verdict != recorded_verdict:
        raise VerificationError(
            f"recorded_verdict {recorded_verdict!r} does not equal the mechanical evaluation "
            f"({recomputed_verdict!r}, from export_bytes={export_bytes} vs "
            f"ceiling_bytes={ceiling_bytes}) -- a recorded PASS over a number that has since "
            "drifted above the ceiling is exactly as much a lie as a recorded CUT over a "
            "genuinely passing one."
        )


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <export-size-spree.json>", file=sys.stderr)
        return 2

    sidecar_path = Path(sys.argv[1])
    try:
        data = json.loads(sidecar_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"FAIL: could not read/parse sidecar JSON: {e}", file=sys.stderr)
        return 2

    try:
        verify(data)
    except VerificationError as e:
        print(f"FAIL (AC-REL-2): {e}", file=sys.stderr)
        return 1

    export_bytes = data["export_bytes"]
    ceiling_bytes = data["ceiling_bytes"]
    pct = 100 * export_bytes / ceiling_bytes
    print(
        f"PASS (AC-REL-2): export_bytes={export_bytes} <= ceiling_bytes={ceiling_bytes} "
        f"({pct:.2f}% of ceiling); recorded_verdict matches the mechanical evaluation.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
