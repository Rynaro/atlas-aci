"""Compare regenerated S38 transcripts (new) with the committed ones (old): print every
differing JSON path and assert the ONLY differences are the mcp version strings (1.27.0 -> 1.30.0)
at `sdk.mcp` and `...serverInfo.version`; also assert old-with-version-substituted == new bytes.
usage: golden-diff.py OLD_DIR NEW_DIR   (exit 0 only when the stop rule holds)"""

import hashlib
import json
import sys
from pathlib import Path

OLD, NEW = Path(sys.argv[1]), Path(sys.argv[2])
ALLOWED = ("1.27.0", "1.30.0")


def walk(a, b, path, out):
    if type(a) is not type(b):
        out.append((path, a, b))
    elif isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append((f"{path}/{k}", a.get(k, "<absent>"), b.get(k, "<absent>")))
            else:
                walk(a[k], b[k], f"{path}/{k}", out)
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append((path, f"len {len(a)}", f"len {len(b)}"))
        for i, (x, y) in enumerate(zip(a, b)):
            walk(x, y, f"{path}[{i}]", out)
    elif a != b:
        out.append((path, a, b))


bad = 0
total_diffs = 0
print(f"old={OLD}\nnew={NEW}")
for old in sorted(OLD.glob("*.json")):
    new = NEW / old.name
    ob, nb = old.read_bytes(), new.read_bytes()
    diffs: list = []
    walk(json.loads(ob), json.loads(nb), "", diffs)
    substituted = ob.replace(ALLOWED[0].encode(), ALLOWED[1].encode())
    ok_paths = all(
        (p.endswith("/sdk/mcp") or p.endswith("serverInfo/version")) and (a, b) == ALLOWED
        for p, a, b in diffs
    )
    ok_bytes = substituted == nb
    print(
        f"{old.name}: old {len(ob)}B {hashlib.sha256(ob).hexdigest()[:16]} -> new {len(nb)}B "
        f"{hashlib.sha256(nb).hexdigest()[:16]}; json diffs={len(diffs)}; "
        f"only-version-paths={ok_paths}; bytes==old.replace(version)={ok_bytes}"
    )
    for p, a, b in diffs:
        print(f"    {p}: {a!r} -> {b!r}")
    total_diffs += len(diffs)
    if not (ok_paths and ok_bytes and len(nb) == len(ob)):
        bad += 1
print(f"files differing beyond the version string: {bad}; total version diffs: {total_diffs}")
sys.exit(1 if bad else 0)
