"""Record the S20 base invariants (run on the unmodified base tree).

dk20.sh /opt/venv/bin/python /ws/.spectra/evidence/aci-s20/base-invariants.py <out.json>
"""
import hashlib, json, sys
from pathlib import Path

from atlas_aci.config import Config
from atlas_aci.protocol import graph as g
from atlas_aci.protocol.results import FIXED_ERROR_ENVELOPE_CAP
from atlas_aci.query.admission import MINIMUM_BUDGET
from atlas_aci.query.paging import COLLECTION_MINIMUM_BUDGET
from atlas_aci.query.traversal import LIMITS
from atlas_aci.server import build_tool_manifest
from atlas_aci.storage import evidence as ev


def h(v):
    return hashlib.sha256(json.dumps(v, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


tools = build_tool_manifest(Config(repo=Path("/ws"), memex_root=Path("/tmp/memex-x")))
out = {
    "MINIMUM_BUDGET": MINIMUM_BUDGET,
    "COLLECTION_MINIMUM_BUDGET": COLLECTION_MINIMUM_BUDGET,
    "FIXED_ERROR_ENVELOPE_CAP": FIXED_ERROR_ENVELOPE_CAP,
    "RELATIONS": sorted(ev.RELATIONS),
    "TRAVERSAL_LIMITS": {k: list(v) for k, v in LIMITS.items()},
    "input_oneOf_sha256": [h(m) for m in g.GRAPH_INPUT_SCHEMA["oneOf"]],
    "envelope_operation_enum": g.ENVELOPE_SCHEMA["properties"]["operation"]["enum"],
    "envelope_data_anyOf_sha256": [h(m) for m in g.ENVELOPE_SCHEMA["properties"]["data"]["anyOf"]],
    "envelope_oneOf_sha256": [h(m) for m in g.ENVELOPE_SCHEMA["oneOf"]],
    "legacy_output_sha256": h(g.LEGACY_OUTPUT_SCHEMA),
    "tools": {t["name"]: h(t) for t in tools},
    "tool_input_schemas": {t["name"]: h(t["inputSchema"]) for t in tools},
    "tools_manifest_sha256": h(tools),
}
Path(sys.argv[1]).write_text(json.dumps(out, indent=1, sort_keys=True))
print(json.dumps(out, indent=1, sort_keys=True))
