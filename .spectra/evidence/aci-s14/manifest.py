"""SHA-256 manifest of declared, tracked implementation files (AC-S14-RUN-01)."""

import fnmatch
import hashlib
import json
import subprocess
import sys
from pathlib import Path

EXCLUDED = {
    ".spectra/state/aci-s14.execution.json",
    ".spectra/evidence/aci-s14/final-reentry-before.json",
    ".spectra/evidence/aci-s14/final-reentry-after.json",
    ".spectra/evidence/aci-s14/final-reentry-summary.json",
    ".spectra/evidence/aci-s14/final-resume-identity.json",
    ".spectra/evidence/aci-s14/acceptance-runs.json",
}
root = Path(sys.argv[1])
globs = [g for g in (root / ".spectra/plans/aci-s14.scope.txt").read_text().split("\n") if g]
tracked = subprocess.run(
    ["git", "-C", str(root), "ls-files"], capture_output=True, text=True, check=True
).stdout.split("\n")
manifest = {
    path: hashlib.sha256((root / path).read_bytes()).hexdigest()
    for path in sorted(tracked)
    if path and path not in EXCLUDED and any(fnmatch.fnmatch(path, g) for g in globs)
}
print(json.dumps(manifest, indent=2, sort_keys=True))
