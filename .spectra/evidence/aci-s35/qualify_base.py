"""Write base-invariants.json: the Day-1 (h) invariants plus the full suite at the base commit."""
import json
from pathlib import Path

here = Path(__file__).resolve().parent
base = json.loads((here / "base-invariants.partial.json").read_text())
base["tree"] = "git archive bd6da89 (read-only copy mounted at /ws in the dev image, uid 1000)"
base["full_suite_at_base"] = {
    "command": (
        "pytest -q -p no:cacheprovider --deselect "
        "tests/slices/test_s08_integration.py::test_nonregular_pack_does_not_block_acquisition"
    ),
    "result": "1 failed, 2331 passed, 10 skipped, 1 deselected in 763.55s (0:12:43)",
    "failed": ["tests/slices/test_s01_contract.py::test_call_deadline_after_initialization"],
    "note": (
        "the failure is the known pre-existing uid 1000 failure; CI at the base commit "
        "(run 42047653138): 2333 passed, 10 skipped"
    ),
}
base["criteria_sha256"] = "f4d64ce3859e8ea711b23775a1e276204172ab5cf0b5fd8f5ab259f7484856c7"
(here / "base-invariants.json").write_text(json.dumps(base, indent=1, sort_keys=True) + "\n")
print("written")
