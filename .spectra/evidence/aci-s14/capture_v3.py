"""Capture a genuine atlas-capabilities.v3 snapshot from the unmodified base source."""
import json, subprocess, sys, tempfile
from pathlib import Path
from atlas_aci.codegraph import CodeGraph

out = Path(sys.argv[1])
with tempfile.TemporaryDirectory() as tmp:
    repo = Path(tmp) / "repo"
    repo.mkdir()
    (repo / "App.tsx").write_text("export function App() { return <div />; }\n")
    (repo / "util.ts").write_text("export function util() {}\n")
    graph = CodeGraph(repo)
    graph.build()
    with graph.reader_lease() as reader:
        data = reader._generation_data(reader.db)
    inputs = data[0]
    status = graph.status()
    graph.close()
caps = status["indexed"]["capabilities"]
assert caps["format"] == "atlas-capabilities.v3", caps["format"]
tsx = next(x for x in caps["languages"] if x["language"] == "tsx")
assert tsx["extraction"] == {"state": "unsupported", "reason": "no_extraction_query"}
record = {
    "description": "Genuine atlas-capabilities.v3 snapshot produced by base 738358de3863428786eec3066dda3078ca4d95d3 (pre-S14): TSX recognized but unsupported.",
    "base_commit": subprocess.run(["git", "-C", "/ws", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
    "generation_inputs_keys": sorted(inputs),
    "capabilities": caps,
    "producer": inputs.get("producer"),
    "configuration": inputs.get("configuration"),
    "status_indexed_keys": sorted(status["indexed"]),
}
out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
print("ok", sorted(inputs))
