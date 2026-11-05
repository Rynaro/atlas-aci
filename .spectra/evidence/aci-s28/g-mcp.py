"""G-MCP: the tools JSON is byte-identical to the base (run in the dev image, /ws = the repo)."""

import hashlib
import json
import sys
import tempfile
from pathlib import Path

from click.testing import CliRunner

sys.path.insert(0, "/ws/mcp-server/tests/slices")
from test_s21_support import workspace

from atlas_aci.__main__ import cli
from atlas_aci.enforcement import READ_ONLY_TOOLS
from atlas_aci.protocol import results
from atlas_aci.query import admission, impact_admission, paging

PIN = "94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56"
raw = CliRunner().invoke(cli, ["tools", "--repo", str(workspace(Path(tempfile.mkdtemp())))]).stdout
data = raw.encode()
report = {
    "sha256": hashlib.sha256(data).hexdigest(),
    "bytes": len(data),
    "MINIMUM_BUDGET": admission.MINIMUM_BUDGET,
    "COLLECTION_MINIMUM_BUDGET": paging.COLLECTION_MINIMUM_BUDGET,
    "FIXED_ERROR_ENVELOPE_CAP": results.FIXED_ERROR_ENVELOPE_CAP,
    "IMPACT_MINIMUM_BUDGET": impact_admission.IMPACT_MINIMUM_BUDGET,
    "READ_ONLY_TOOLS": len(READ_ONLY_TOOLS),
}
print(json.dumps(report))
ok = (
    report["sha256"] == PIN
    and report["bytes"] == 1631472
    and (report["MINIMUM_BUDGET"], report["COLLECTION_MINIMUM_BUDGET"]) == (3436, 3448)
    and (report["FIXED_ERROR_ENVELOPE_CAP"], report["IMPACT_MINIMUM_BUDGET"]) == (797, 7730)
    and report["READ_ONLY_TOOLS"] == 7
)
print("G-MCP MATCH" if ok else "G-MCP MISMATCH")
sys.exit(0 if ok else 1)
