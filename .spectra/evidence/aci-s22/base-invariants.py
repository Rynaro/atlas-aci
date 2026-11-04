import hashlib, json, subprocess, sys
from click.testing import CliRunner
sys.path.insert(0, "/ws/mcp-server/tests/slices")
import tempfile
from pathlib import Path
from atlas_aci.__main__ import cli
from atlas_aci.enforcement import READ_ONLY_TOOLS
from atlas_aci.query import admission, paging
from atlas_aci.protocol import results
from atlas_aci.query import impact_admission
from atlas_aci.storage import evidence as ev
d = Path(tempfile.mkdtemp()); (d / "r").mkdir()
out = CliRunner().invoke(cli, ["tools", "--repo", str(d / "r")], catch_exceptions=False)
raw = out.stdout.encode()
import gzip
tail = gzip.open("/ws/.spectra/evidence/aci-s22/base-full-suite.log.gz", "rt").read().strip().splitlines()[-2:]
res = {
 "base_commit": "4843ddc",
 "tools_json_sha256": hashlib.sha256(raw).hexdigest(),
 "tools_json_bytes": len(raw),
 "MINIMUM_BUDGET": admission.MINIMUM_BUDGET,
 "COLLECTION_MINIMUM_BUDGET": paging.COLLECTION_MINIMUM_BUDGET,
 "FIXED_ERROR_ENVELOPE_CAP": results.FIXED_ERROR_ENVELOPE_CAP,
 "IMPACT_MINIMUM_BUDGET": impact_admission.IMPACT_MINIMUM_BUDGET,
 "READ_ONLY_TOOLS_count": len(READ_ONLY_TOOLS),
 "RELATIONS": sorted(ev.RELATIONS),
 "full_suite_at_base": {
   "command": "pytest -q -p no:cacheprovider (docker, uid 1000)",
   "exit": int(open("/ws/.spectra/evidence/aci-s22/base-full-suite.exit").read()),
   "summary": tail[-1],
   "failed": [l for l in tail if l.startswith("FAILED")] ,
   "known_preexisting_failure": "tests/slices/test_s01_contract.py::test_call_deadline_after_initialization",
 },
}
Path("/ws/.spectra/evidence/aci-s22/base-invariants.json").write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
print(json.dumps(res, indent=1))
