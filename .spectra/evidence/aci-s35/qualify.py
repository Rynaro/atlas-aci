"""ACI-S35 Day-1 qualification (run in the dev image: python 3.12, uid 1000).

    sh dk35.sh /opt/venv/bin/python /ws/.spectra/evidence/aci-s35/qualify.py

Writes qualification.json beside this file. Exit 1 when a STOP RULE condition holds.
STOP: (a) library vs MCP bundle order differs or library context() fails; (b) validate_context
rejects a genuine saved record; (c) pytest's (classname, name) differs from the D3 mapping for a
supported nodeid shape; (d) interpreters differ (see qualify-expat.sh); (e) an unchanged rebuild
changes the generation id.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import platform
import pyexpat
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "mcp-server/tests/slices"))
sys.path.insert(0, str(HERE))

import junit_probe as jp  # noqa: E402
import test_s35_support as sup  # noqa: E402

from atlas_aci.codegraph import CodeGraph  # noqa: E402
from atlas_aci.config import DEFAULT_MAX_BOUND_FIELD_ELEMENTS, DEFAULT_MAX_RESPONSE_BYTES  # noqa: E402
from atlas_aci.overlays import runtime  # noqa: E402
from atlas_aci.protocol.context import validate_context  # noqa: E402
from atlas_aci.query.context import context  # noqa: E402
from atlas_aci.query.diff import Side  # noqa: E402
from atlas_aci.storage import evidence as ev  # noqa: E402

report: dict = {"python": platform.python_version(), "expat": pyexpat.EXPAT_VERSION}
stops: list[str] = []


@dataclass(frozen=True)
class Settings:
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    max_bound_field_elements: int = DEFAULT_MAX_BOUND_FIELD_ELEMENTS
    context_tokenizer: object | None = None


def library(world, name, arguments):
    with ExitStack() as stack:
        reader = runtime.open_active(stack, world.side(name))
        result = asyncio.run(context(reader, dict(arguments), Settings(), structured=True))
        return result, reader.query_limit, str(reader.repo)


def mcp_reader_facts(world, name):
    graph = CodeGraph(
        repo=world.repo,
        read_only=True,
        query_limit=DEFAULT_MAX_BOUND_FIELD_ELEMENTS + 1,
        graph_root=world.root(name),
    )
    try:
        with graph.reader_lease() as reader:
            return reader.query_limit, str(reader.repo)
    finally:
        graph.close()


tmp = Path(tempfile.mkdtemp(prefix="s35q-"))

# ---- (g) five builds ---------------------------------------------------------------------------
started = time.monotonic()
world = sup.build_world(tmp)
build_seconds = time.monotonic() - started
report["g_build_five_roots_seconds"] = round(build_seconds, 2)

# ---- (e) generation stability ------------------------------------------------------------------
records: dict[str, dict] = {}
for name in ("before", "rebuild", "unrelated", "removed", "after"):
    records[name] = {"snapshot_id": world.snapshot(name), "generation_id": world.generation(name)}
e = {
    "ids": records,
    "unchanged_rebuild_equal_snapshot": world.snapshot("rebuild") == world.snapshot("before"),
    "unchanged_rebuild_equal_generation": world.generation("rebuild") == world.generation("before"),
    "unrelated_edit_changes_snapshot": world.snapshot("unrelated") != world.snapshot("before"),
    "unrelated_edit_changes_generation": world.generation("unrelated")
    != world.generation("before"),
}
bytes_before, rec_before = sup.produce_record(world, "before")
_, rec_rebuild = sup.produce_record(world, "rebuild")
_, rec_unrelated = sup.produce_record(world, "unrelated")
_, rec_after = sup.produce_record(world, "after")
e["bundle_ids_unrelated_equal_before"] = sup.bundle_ids(rec_unrelated) == sup.bundle_ids(
    rec_before
)
e["bundle_ids_rebuild_equal_before"] = sup.bundle_ids(rec_rebuild) == sup.bundle_ids(rec_before)
e["referenced_edit_changes_bundle_ids"] = sup.bundle_ids(rec_after) != sup.bundle_ids(rec_before)
e["record_id_rebuild_equal_before"] = (
    rec_rebuild["data"]["retrieval_record_id"] == rec_before["data"]["retrieval_record_id"]
)
report["e_generation_stability"] = e
if not (e["unchanged_rebuild_equal_snapshot"] and e["unchanged_rebuild_equal_generation"]):
    stops.append("e: an unchanged rebuild changed the snapshot or generation id")

# ---- (a) library vs MCP ------------------------------------------------------------------------
arguments = sup.request_arguments("process_payment")
mcp_payload = rec_before
lib_payload, lib_limit, lib_repo = library(world, "before", arguments)
mcp_limit, mcp_repo = mcp_reader_facts(world, "before")
order_equal = sup.bundle_ids(lib_payload) == sup.bundle_ids(mcp_payload)
a = {
    "request": arguments,
    "bundle_order_equal": order_equal,
    "bundle_kinds": [b["kind"] for b in mcp_payload["data"]["bundles"]],
    "bundle_id_order_mcp": sup.bundle_ids(mcp_payload),
    "bundle_id_order_library": sup.bundle_ids(lib_payload),
    "retrieval_record_id_mcp": mcp_payload["data"]["retrieval_record_id"],
    "retrieval_record_id_library": lib_payload["data"]["retrieval_record_id"],
    "record_id_equal": lib_payload["data"]["retrieval_record_id"]
    == mcp_payload["data"]["retrieval_record_id"],
    "data_equal": lib_payload["data"] == mcp_payload["data"],
    "payload_equal_except_bounds": {k: v for k, v in lib_payload.items() if k != "bounds"}
    == {k: v for k, v in mcp_payload.items() if k != "bounds"},
    "bounds_library": lib_payload.get("bounds"),
    "bounds_mcp": mcp_payload.get("bounds"),
    "library_query_limit": lib_limit,
    "mcp_query_limit": mcp_limit,
    "query_limit_equal": lib_limit == mcp_limit,
    "library_repo": lib_repo,
    "mcp_repo": mcp_repo,
    "repo_string_equal": lib_repo == mcp_repo,
    "data_repository": mcp_payload["data"]["repository"],
    "collection_stop": mcp_payload["data"]["collection_stop"],
    "selection_reason": mcp_payload["data"]["selection_reason"],
    "bundle_count": len(sup.bundle_ids(mcp_payload)),
}
# 20 replays of each path: no stop, identical ids, identical record id.
replays = {"library": [], "mcp": []}
for _ in range(20):
    lib_again, _, _ = library(world, "before", arguments)
    replays["library"].append(
        (lib_again["data"]["collection_stop"], lib_again["data"]["selection_reason"],
         lib_again["data"]["retrieval_record_id"])
    )
    _, mcp_again = sup.produce_record(world, "before")
    replays["mcp"].append(
        (mcp_again["data"]["collection_stop"], mcp_again["data"]["selection_reason"],
         mcp_again["data"]["retrieval_record_id"])
    )
a["replays"] = {
    "count_each": 20,
    "all_collection_stop_none": all(r[0] is None for v in replays.values() for r in v),
    "all_selection_reason_none": all(r[1] is None for v in replays.values() for r in v),
    "all_record_ids_equal_the_first": len(
        {r[2] for v in replays.values() for r in v} | {a["retrieval_record_id_mcp"]}
    )
    == 1,
}
# Bundle A: the last bundle of the base query, in both records and at position >= 2.
last = sup.bundle_ids(mcp_payload)[-1]
_, rec_r3 = sup.produce_record(world, "before", "process_payment", max_candidates=16)
a["bundle_A"] = {
    "id": last,
    "kind": mcp_payload["data"]["bundles"][-1]["kind"],
    "position_in_rank_request": sup.bundle_ids(mcp_payload).index(last),
    "present_in_R3": last in sup.bundle_ids(rec_r3),
    "R3_request": "process_payment with max_candidates=16",
    "R3_record_id_differs": rec_r3["data"]["retrieval_record_id"]
    != mcp_payload["data"]["retrieval_record_id"],
    "R3_bundle_ids_equal_R1": sup.bundle_ids(rec_r3) == sup.bundle_ids(mcp_payload),
    "validate_card_query_bundle_ids_contain_A": last
    in sup.bundle_ids(sup.produce_record(world, "before", "validate_card")[1]),
}
report["a_library_vs_mcp"] = a
if not (order_equal and a["data_equal"]):
    stops.append("a: bundle order or data differs between library and MCP")
for key in ("collection_stop", "selection_reason"):
    if a[key] is not None:
        stops.append("a: the request was truncated (" + key + ")")

# ---- (b) the saved record ----------------------------------------------------------------------
path = tmp / "record.json"
path.write_bytes(bytes_before)
loaded = json.loads(path.read_bytes())
b: dict = {"saved_bytes": len(bytes_before), "round_trip_equal": loaded == mcp_payload}
try:
    validate_context(loaded, "context", DEFAULT_MAX_BOUND_FIELD_ELEMENTS)
    b["validate_context_accepts_saved_record"] = True
except ValueError as exc:
    b["validate_context_accepts_saved_record"] = False
    b["error"] = str(exc)
tampered = copy.deepcopy(loaded)
tampered["data"]["bundles"][0]["records"]["entity"]["name"] = "process_paymenT"
try:
    validate_context(tampered, "context", DEFAULT_MAX_BOUND_FIELD_ELEMENTS)
    b["tampered_byte_rejected"] = False
except ValueError as exc:
    b["tampered_byte_rejected"] = True
    b["tamper_error"] = str(exc)
report["b_saved_record"] = b
if not b["validate_context_accepts_saved_record"]:
    stops.append("b: validate_context rejects a genuine saved record")

# ---- (f) observations --------------------------------------------------------------------------
seen = sup.observations(world.side("before"))
available = {
    (s["path"], s["source_sha256"])
    for bundle in mcp_payload["data"]["bundles"]
    for s in bundle["sources"]
    if s.get("state") == "available"
}
with ExitStack() as stack:
    reader = runtime.open_active(stack, world.side("before"))
    row = reader.db.execute(runtime.OBSERVATIONS_SQL).fetchone()
items = json.loads(row[0])
report["f_observations"] = {
    "record_types": sorted({i.get("type") for i in items}),
    "fields_of_an_observation": sorted(next(i for i in items if i.get("type") == "observation")),
    "indexed_paths": sorted(seen),
    "every_available_bundle_source_equals_its_observation": all(
        seen.get(path_) == sha_ for path_, sha_ in available
    ),
    "available_bundle_sources": sorted(map(list, available)),
    "hex_shas": all(ev.hash_value(v) for v in seen.values()),
}

# ---- (g) timing --------------------------------------------------------------------------------
started = time.monotonic()
sup.produce_record(world, "before")
mcp_seconds = time.monotonic() - started
started = time.monotonic()
library(world, "before", arguments)
lib_seconds = time.monotonic() - started
report["g_timing"] = {
    "five_builds_seconds": round(build_seconds, 2),
    "one_mcp_call_seconds": round(mcp_seconds, 3),
    "one_library_call_seconds": round(lib_seconds, 3),
}

# ---- (c) real pytest JUnit ---------------------------------------------------------------------
suite = Path(tempfile.mkdtemp(prefix="s35suite-"))
TEST_PAY = '''import pytest

def test_pass():
    assert True

def test_fail():
    assert 1 == 2

@pytest.mark.skip(reason="no")
def test_skip():
    pass

@pytest.mark.xfail(reason="x")
def test_xfail():
    assert False

@pytest.fixture
def broken():
    raise RuntimeError("setup")

def test_setup_error(broken):
    pass

class TestCards:
    def test_ok(self):
        assert True

    class TestInner:
        def test_deep(self):
            assert True

@pytest.mark.parametrize("brand", ["visa", "master-card", "v1.2", "a b", "x<y&z\\"q", "a[b]", "p::q"])
def test_param(brand):
    assert True

@pytest.mark.parametrize("n", [1, 2])
def test_dup(n):
    assert True

def test_with_output():
    print("hello")
'''
files = {
    "tests/test_pay.py": TEST_PAY,
    "tests/sub-dir/test_dash.py": "def test_a():\n    assert True\n",
    "tests/test_nested/test_deep_mod.py": "def test_a():\n    assert True\n",
    "tests/my.pytools/test_dotdir.py": "def test_a():\n    assert True\n",
    "tests/a.b.py": "def test_a():\n    assert True\n",
    "tests/a/b.py": "def test_a():\n    assert True\n",
}
for name, text in files.items():
    target = suite / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
junit = suite / "junit.xml"
run = subprocess.run(
    [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "--import-mode=importlib",
     "-o", "python_files=*.py", "--junitxml", str(junit), "tests"],
    cwd=suite, capture_output=True, text=True, timeout=120,
)
listing = subprocess.run(
    [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "--collect-only",
     "--import-mode=importlib", "-o", "python_files=*.py", "tests"],
    cwd=suite, capture_output=True, text=True, timeout=120,
)
nodeids = [line for line in listing.stdout.splitlines() if "::" in line]
raw = junit.read_bytes()
cases = jp.parse(raw)
import pytest  # noqa: E402

xml_pairs = [(c[0], c[1]) for c in cases]
rows = []
mismatches = []
collisions = []
for nodeid in nodeids:
    mapped = jp.pair(nodeid)
    row = {"nodeid": nodeid, "supported": mapped is not None}
    if mapped is not None:
        count = xml_pairs.count(mapped)
        row["d3_pair"] = list(mapped)
        row["testcases_with_that_pair"] = count
        if count == 0:
            mismatches.append(nodeid)
        elif count > 1:
            collisions.append(nodeid)
    rows.append(row)
unsupported_pairs = sorted(
    list(p) for p in xml_pairs if p[0] in ("tests.my.pytools.test_dotdir", "tests.a.b")
)
report["c_pytest_junit"] = {
    "pytest": pytest.__version__,
    "junit_family": "xunit2 (default)",
    "declaration_bytes": raw[:38].decode("ascii"),
    "declaration_is_allowed": raw.startswith(jp.DECLARATIONS),
    "testsuites_root": raw[38:60].decode("ascii"),
    "trailing_newline": raw.endswith(b"\n"),
    "collected_nodeids": len(nodeids),
    "testcases": len(cases),
    "pairs_with_children": sorted({c[1].split("[")[0]: c[2] for c in cases if c[2]}.items()),
    "rows": rows,
    "supported_nodeids_with_a_mapping_mismatch": mismatches,
    "supported_nodeids_whose_pair_is_shared_by_several_testcases": collisions,
    "dir_with_dot_py_inside_and_a_dot_b_module_pairs_pytest_9": unsupported_pairs,
    "non_injective_note": (
        "tests/a/b.py and tests/a.b.py both give classname tests.a.b: the two shapes are "
        "unsupported by the NODEID grammar (a dot is never allowed in the path), so neither can "
        "verify the other; pytest versions that strip every '.py' substring would also change "
        "tests/my.pytools/..."
    ),
    "pytest_exit": run.returncode,
}
(HERE / "pytest-junit-capture-c.xml").write_bytes(raw)
if mismatches:
    stops.append("c: D3 mapping differs from pytest for " + ", ".join(mismatches))

# ---- (h) base invariants -----------------------------------------------------------------------
from click.testing import CliRunner  # noqa: E402

from atlas_aci.__main__ import cli  # noqa: E402
from atlas_aci.enforcement import READ_ONLY_TOOLS  # noqa: E402
from atlas_aci.protocol.results import FIXED_ERROR_ENVELOPE_CAP  # noqa: E402
from atlas_aci.query.admission import MINIMUM_BUDGET  # noqa: E402
from atlas_aci.query.impact_admission import IMPACT_MINIMUM_BUDGET  # noqa: E402
from atlas_aci.query.paging import COLLECTION_MINIMUM_BUDGET  # noqa: E402

tools = CliRunner().invoke(cli, ["tools", "--repo", str(ROOT)])
changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").splitlines()
unreleased = changelog.index("### Added")
bullets = [ln[:60] for ln in changelog[unreleased : unreleased + 330] if ln.startswith("- **")]
readme = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
history = next(i for i, ln in enumerate(readme) if "docs/history-signals.md" in ln)
base = {
    "tools_json_sha256": hashlib.sha256(tools.stdout_bytes).hexdigest(),
    "tools_json_bytes": len(tools.stdout_bytes),
    "READ_ONLY_TOOLS": sorted(READ_ONLY_TOOLS),
    "READ_ONLY_TOOLS_count": len(READ_ONLY_TOOLS),
    "MINIMUM_BUDGET": MINIMUM_BUDGET,
    "COLLECTION_MINIMUM_BUDGET": COLLECTION_MINIMUM_BUDGET,
    "FIXED_ERROR_ENVELOPE_CAP": FIXED_ERROR_ENVELOPE_CAP,
    "IMPACT_MINIMUM_BUDGET": IMPACT_MINIMUM_BUDGET,
    "ev_RELATIONS": sorted(ev.RELATIONS),
    "changelog_first_bullets": bullets[:5],
    "readme_history_row_line": history + 1,
    "readme_row_after_history": readme[history + 1][:80],
    "readme_row_after_that": readme[history + 2][:80],
}
report["h_base_invariants_partial"] = base
(HERE / "base-invariants.partial.json").write_text(json.dumps(base, indent=1, sort_keys=True) + "\n")
if base["tools_json_sha256"] != sup.BASE_TOOLS_SHA256 or base["tools_json_bytes"] != 1631472:
    stops.append("h: tools JSON differs from the pin")

report["stop_rule_triggered"] = stops
report["verdict"] = "STOP" if stops else "PROCEED"
(HERE / "qualification.json").write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
print(json.dumps({k: report[k] for k in ("verdict", "stop_rule_triggered")}, indent=1))
sys.exit(1 if stops else 0)
