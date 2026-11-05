"""Write tests/fixtures/rails_jobs/provenance.json: sha256 and byte size per fixture file plus the
hand-derived expected totals (counted from the fixture text, not read from a build)."""
import hashlib
import json
from pathlib import Path

root = Path("/ws/mcp-server/tests/fixtures/rails_jobs")
files = {
    str(p.relative_to(root / "project")): {
        "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        "bytes": p.stat().st_size,
    }
    for p in sorted((root / "project").rglob("*"))
    if p.is_file()
}
document = {
    "description": (
        "Rails jobs fixture for ACI-S26. All files are ASCII, written for this slice. No file is "
        "under app/models/, config/routes.rb or db/migrate/, so the S24 and S25 adapters yield "
        "no records here. tmp/stale/w.rb sits under a skip-pattern component and is never indexed."
    ),
    "files": files,
    "expected": {
        "files": {"captured": 20, "failed": 0, "limited": 0, "scanned": 20},
        "classes": {"jobs": 13, "unbound": 2},
        "performs": 13,
        "sites": {"limited": 0, "located": 41, "skipped": 0, "unlocated": 0},
        "facts": {
            "async_argument": {"INFERRED": 16, "UNRESOLVED": 12},
            "async_enqueue": {"AMBIGUOUS": 3, "INFERRED": 23, "UNRESOLVED": 15},
        },
        "records": {"assertion": 69, "evidence": 69, "occurrence": 69, "producer": 1},
        "truncated": False,
        "derivation": (
            "enqueues: 35 sites in enqueuer.rb + 2 nightly + 1 daily + 2 outer + 1 spec = 41; "
            "INFERRED 23 (9 + 11 argument-diagnostic + B2 + daily + spec), AMBIGUOUS 3 (E8, U11, "
            "B1), UNRESOLVED 15 (E9-E14, D1-D4, S1, L1, L2, 2 unbound_namespace); arguments: 16 "
            "mapped (E1 1, E2 2, E3 2, E4 2, E7 1, E15 1, E16 1, B2 2, daily 2, spec 2) and 12 "
            "diagnostics (U1-U11 and fwd)."
        ),
    },
}
(root / "provenance.json").write_text(json.dumps(document, indent=1, sort_keys=True) + "\n")
print("ok", len(files))
