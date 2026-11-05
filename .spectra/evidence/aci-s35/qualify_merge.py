"""Merge the cross-interpreter expat parity (expat/*.json) into qualification.json."""
import json
from pathlib import Path

here = Path(__file__).resolve().parent
report = json.loads((here / "qualification.json").read_text())
runs = {}
for path in sorted((here / "expat").glob("*.json")):
    data = json.loads(path.read_text())
    runs[path.stem] = {k: data[k] for k in ("python", "expat", "variants", "results_sha256")}
hashes = {r["results_sha256"] for r in runs.values()}
report["d_expat_independence"] = {
    "interpreters": runs,
    "all_results_identical": len(hashes) == 1,
    "note": (
        "every available image carries the same libexpat (see 'expat'); parity across expat "
        "versions is covered by construction (the pre-scan removes DTDs, entities, comments, "
        "CDATA and PIs before the parser exists) and by the CI python 3.14 run, not measured "
        "here. Interpreter parity: dev image 3.12, python:3.12-slim, python:3.13-slim, python:3.14."
    ),
}
if len(hashes) != 1:
    report["stop_rule_triggered"].append("d: results differ between interpreters")
    report["verdict"] = "STOP"
(here / "qualification.json").write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
print(report["verdict"], report["d_expat_independence"]["all_results_identical"])
