"""ACI-S34: write mapping.json and mapping-unresolved.json (literal Day-1 ids and digests).

Run in the dev image after make-world.py:
    sh dk34.sh /opt/venv/bin/python /ws/.spectra/evidence/aci-s34/make-fixtures.py
"""

import hashlib
import json
import shutil
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path

sys.path.insert(0, "/ws/mcp-server/tests/slices")
from test_s19_support import same_path_pair  # noqa: E402

from atlas_aci.overlays import runtime  # noqa: E402
from atlas_aci.storage import evidence as ev  # noqa: E402

FX = Path("/ws/mcp-server/tests/fixtures/adr_mapping")
WORLD = json.loads((FX / "world.json").read_text())
BEFORE = WORLD["before"]


def sha(data):
    return hashlib.sha256(data).hexdigest()


tmp = Path(tempfile.mkdtemp(prefix="s34f-"))
repo, before, after = same_path_pair(tmp, WORLD["before"], WORLD["after"])
with ExitStack() as stack:
    reader = runtime.open_active(stack, before)
    rows = [json.loads(p) for (p,) in reader.db.execute("SELECT payload FROM entities")]
    by_key = {}
    for e in rows:
        by_key[(e["path"], e["kind"], e["name"], e["ordinal"])] = e


def symbol(path, name, ordinal=0, kind="function"):
    e = by_key[(path, kind, name, ordinal)]
    return {"entity_id": e["id"], "path": path, "kind": kind, "name": name}


def file_target(path):
    e = by_key[(path, "file", path, 0)]
    return {"entity_id": e["id"], "path": path, "kind": "file", "name": path}


def span(path, start_marker, end_marker=None):
    raw = BEFORE[path].encode()
    start = raw.index(start_marker.encode())
    end = len(raw) - 1 if end_marker is None else raw.index(end_marker.encode()) - 1
    return {"start_byte": start, "end_byte": end, "sha256": sha(raw[start:end])}


def entry(mapping_id, decision, path, target, which=None):
    raw = BEFORE[path].encode()
    return {
        "mapping_id": mapping_id,
        "decision_id": decision,
        "document": {"path": path, "sha256": sha(raw)},
        "span": which or SPANS[path],
        "target": target,
    }


A1, A2, A3 = "docs/adr/0001-pricing.md", "docs/adr/0002-audit.md", "docs/adr/0003-legacy.md"
SPANS = {
    A1: span(A1, "## Decision", "## Consequences"),
    A2: span(A2, "## Decision"),
    A3: span(A3, "## Decision"),
}
D1, D2, D3 = "adr-0001", "adr-0002", "adr-0003"
mappings = [
    entry("charge", D1, A1, symbol("src/pricing.py", "charge")),
    entry("refund", D1, A1, symbol("src/refunds.py", "refund")),
    entry("charge-doc-edited", D2, A2, symbol("src/pricing.py", "charge")),
    entry("audit", D1, A1, symbol("src/audit.py", "audit")),
    entry("post", D1, A1, symbol("src/ledger.py", "post")),
    entry("summary", D1, A1, symbol("src/reports.py", "summary")),
    entry("summary-doc-edited", D2, A2, symbol("src/reports.py", "summary")),
    entry("summary-legacy-doc", D3, A3, symbol("src/reports.py", "summary")),
    entry("charge-legacy-doc", D3, A3, symbol("src/pricing.py", "charge")),
    entry("retire", D1, A1, symbol("src/legacy.py", "retire")),
    entry("retire-legacy-doc", D3, A3, symbol("src/legacy.py", "retire")),
    entry("rename", D1, A1, symbol("src/rename.py", "compute_total")),
    entry("fragile", D1, A1, symbol("src/fragile.py", "keep")),
    entry("limits", D1, A1, symbol("config/limits.yml", "max_charge", kind="key")),
    entry("settings-file", D1, A1, file_target("src/settings.py")),
    entry("constants-file", D1, A1, file_target("src/constants.py")),
    entry("fragile-file", D1, A1, file_target("src/fragile.py")),
    entry("legacy-file", D1, A1, file_target("src/legacy.py")),
    entry("twin-first", D1, A1, symbol("src/dupes.py", "twin", 0)),
    entry("twin-second", D1, A1, symbol("src/dupes.py", "twin", 1)),
    entry("dual-second", D1, A1, symbol("src/twins.py", "dual", 1)),
    entry("account-class", D1, A1, symbol("src/account.py", "Account", kind="class")),
]
doc = {"format": "atlas-rationale-mapping.v1", "snapshot_id": "@before@", "mappings": mappings}
(FX / "mapping.json").write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="ascii")

charge = symbol("src/pricing.py", "charge")
raw1 = BEFORE[A1].encode()
good = SPANS[A1]
past = {"start_byte": good["start_byte"], "end_byte": len(raw1) + 5, "sha256": good["sha256"]}
accent = raw1.index("é".encode())
split = {"start_byte": accent + 1, "end_byte": accent + 9, "sha256": sha(raw1[accent + 1 : accent + 9])}
bad_span = {**good, "sha256": "0" * 64}
unresolved = [
    entry("no-doc", D1, A1, charge),
    entry("bad-doc-digest", D1, A1, charge),
    entry("past-eof", D1, A1, charge, past),
    entry("split-accent", D1, A1, charge, split),
    entry("bad-span-digest", D1, A1, charge, bad_span),
    entry("absent-id", D1, A1, {**charge, "entity_id": "ab" * 32}),
    entry("name-mismatch", D1, A1, {**charge, "name": "charge2"}),
    entry("path-mismatch", D1, A1, {**charge, "path": "src/other.py"}),
]
unresolved[0]["document"] = {"path": "docs/adr/9999.md", "sha256": sha(b"absent")}
unresolved[1]["document"] = {"path": A1, "sha256": sha(b"not the document")}
doc2 = {"format": "atlas-rationale-mapping.v1", "snapshot_id": "@before@", "mappings": unresolved}
(FX / "mapping-unresolved.json").write_text(
    json.dumps(doc2, indent=1, sort_keys=True) + "\n", encoding="ascii"
)
print(len(mappings), len(unresolved), "split decodes:", end=" ")
try:
    raw1[accent + 1 : accent + 9].decode("utf-8")
    print(True)
except UnicodeDecodeError:
    print(False)
shutil.rmtree(tmp, ignore_errors=True)
