"""sec-relock preflight (run in atlas-aci-dev-relock BEFORE any pin edit): print one JSON document.

Records the four budgets, the tools JSON sha/bytes, GRAPH_OUTPUT_SHA, the SDK initialization
options, the SDK-model API surface server.py uses, and one S38 `capture` run (case hashes).
"""

import hashlib
import importlib.metadata as md
import inspect
import json
import subprocess
import sys
import tempfile
from pathlib import Path

FRESH = """
import hashlib, json, sys
from pathlib import Path
from atlas_aci.config import Config
from atlas_aci.server import build_tool_manifest
from atlas_aci.protocol import graph, results
from atlas_aci.query import admission, impact_admission, paging
root = Path(sys.argv[1])
manifest = build_tool_manifest(Config(repo=root, memex_root=root / "memex"))
raw = (json.dumps(manifest, indent=2) + "\\n").encode()
canon = lambda v: json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
print(json.dumps({
    "tools_sha": hashlib.sha256(raw).hexdigest(), "tools_bytes": len(raw),
    "FIXED_ERROR_ENVELOPE_CAP": results.FIXED_ERROR_ENVELOPE_CAP,
    "MINIMUM_BUDGET": admission.MINIMUM_BUDGET,
    "COLLECTION": paging.COLLECTION_MINIMUM_BUDGET,
    "IMPACT": impact_admission.IMPACT_MINIMUM_BUDGET,
    "GRAPH_OUTPUT_SHA": hashlib.sha256(canon(graph.GRAPH_OUTPUT_SCHEMA).encode()).hexdigest(),
}))
"""

PINS = {
    "FIXED_ERROR_ENVELOPE_CAP": 797,
    "MINIMUM_BUDGET": 3436,
    "COLLECTION": 3448,
    "IMPACT": 7730,
    "tools_sha": "94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56",
    "tools_bytes": 1631472,
    "GRAPH_OUTPUT_SHA": "713ec5b6b2af963d556ddc3829dfd6919d52b3be2a9d70d918a5a6e270d4cf60",
}

tmp = Path(tempfile.mkdtemp())
done = subprocess.run(
    [sys.executable, "-c", FRESH, str(tmp)], capture_output=True, text=True, timeout=300
)
assert done.returncode == 0, done.stderr[-800:]
seen = json.loads(done.stdout.splitlines()[-1])

from mcp.server import Server  # noqa: E402

opts = Server("x").create_initialization_options()
init = {
    "server_name": opts.server_name,
    "server_version": opts.server_version,
    "capabilities": json.loads(opts.capabilities.model_dump_json(by_alias=True, exclude_none=True)),
}

import mcp.types as types  # noqa: E402

api = {
    "LATEST_PROTOCOL_VERSION": types.LATEST_PROTOCOL_VERSION,
    "SUPPORTED_PROTOCOL_VERSIONS": list(types.SUPPORTED_PROTOCOL_VERSIONS)
    if hasattr(types, "SUPPORTED_PROTOCOL_VERSIONS")
    else None,
    "call_tool_signature": str(inspect.signature(Server.call_tool)),
}

sys.path.insert(0, "/ws/mcp-server/tests/slices")
import test_s38_support as sup  # noqa: E402

out = Path(tempfile.mkdtemp())
cap = sup.capture(out / "o", out / "fx", "sec-relock-preflight", 0, False)
goldens = {
    p.name: hashlib.sha256(p.read_bytes()).hexdigest()
    for p in sorted(Path("/ws/mcp-server/tests/fixtures/s38/transcripts").glob("*.json"))
}
case_diff = {}
for p in sorted((out / "o" / "transcripts").glob("*.json")):
    gold = Path("/ws/mcp-server/tests/fixtures/s38/transcripts") / p.name
    case_diff[p.name] = {
        "capture_sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        "golden_sha256": goldens.get(p.name),
        "identical": p.read_bytes() == gold.read_bytes(),
    }

doc = {
    "dependencies": {n: md.version(n) for n in ("mcp", "anyio", "pyjwt", "cryptography", "certifi")},
    "python": sys.version,
    "observed": seen,
    "pins": PINS,
    "pins_hold": {k: seen[k] == v for k, v in PINS.items()},
    "sdk_initialization_options": init,
    "sdk_api": api,
    "s38_capture": {"identity": cap["identity"], "sdk_version": sup.sdk_version(), "cases": case_diff},
}
doc["all_pins_hold"] = all(doc["pins_hold"].values())
Path(sys.argv[1]).write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
print("preflight: all_pins_hold =", doc["all_pins_hold"], "->", sys.argv[1])
sys.exit(0 if doc["all_pins_hold"] else 3)
