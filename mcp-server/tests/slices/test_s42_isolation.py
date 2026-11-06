"""ACI-S42 supporting tests: purity, vocabularies, the case table, invariance, documents and sets.

None of these starts a server: they read the committed manifest, evidence and documents, and
exercise the aggregator and the recorder's pure helpers on synthetic records and responses. Each
zero or "absent" check is paired with a positive control or a mutated copy that must trip it.
"""

from __future__ import annotations

import ast
import base64
import copy
import gzip
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest
import test_s42_support as s

ROOT = s.ROOT
SRC = ROOT / "mcp-server" / "src"
BANNED = {
    "safe", "proven", "guaranteed", "compliant", "compatible", "incompatible", "breaking",
    "applied", "trusted", "certified", "authentic", "superior", "optimal", "better", "improves",
    "improved", "improvement", "learned", "favourable", "favorable", "seamless", "universal",
}  # fmt: skip
AGGREGATOR_IMPORTS = {
    "__future__", "argparse", "gzip", "hashlib", "importlib", "json", "sys", "decimal", "pathlib",
    "typing", "collections", "dataclasses", "math", "re",
}  # fmt: skip
FORBIDDEN_NAMES = {
    "time", "os", "subprocess", "platform", "random", "socket", "urllib", "threading", "asyncio",
    "signal", "resource", "shutil", "tempfile", "uuid", "environ", "getenv", "atlas_aci",
}  # fmt: skip
LAZY_IMPORTS = {"mcp", "httpx", "jwt", "cryptography"}
# Day-1 (a) constants, recorded in .spectra/evidence/aci-s42/base-invariants.json at base 4f470d7.
TOOLS_SHA = "94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56"
TOOLS_BYTES = 1631472
PYPROJECT_SHA = "981f0784d6bc5c1502c91ab27657beb483460b97e066cd8846f51e98f1d7d92f"
UV_LOCK_SHA = "87ba265e76f382aaa4387da7ca5d89dc85eb3699b35ee4315e981302b19646fd"
DOCKERFILE_SHA = "8abfc6635110f158f2ce60d473dabbcf447f38e62b71fbdc38e178b17654f529"
DOCKERFILE_DEV_SHA = "1ac46d1137ac42b71608e6d0e7042b38b49a1a9054233d487d6508ae175e91e4"
SRC_TREE_DIGEST = "07bc58e0ec6c5c32da5d2a9a27dc5587dd49a571c6b9d84cf1f3b6b6209d7d1e"
V3_AGGREGATE_SHA = "64250ba512f18d7f7f8ce9027af920f2bb240320341e8e4b5056e3418dabf80c"
V3_RECORD_SHA = "400918d65dbb684c47758fd81848ca3ec4678c03536026e2757c4322cfa7f004"
HOSTS = ("claude-code", "github-copilot", "cursor")


def words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower()))


def sha_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text())


def request_text(case: dict[str, Any]) -> str:
    return json.dumps(case["request"], sort_keys=True)


# ------------------------------------------------------------------------------ purity
def test_the_aggregator_is_pure() -> None:
    tree = parse(s.AGGREGATOR)
    roots: set[str] = set()
    names: set[str] = set()
    spec_calls = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            roots.add((node.module or "").split(".")[0])
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Call):
            func = node.func
            if getattr(func, "attr", getattr(func, "id", None)) == "spec_from_file_location":
                spec_calls += 1
    assert roots <= AGGREGATOR_IMPORTS, roots - AGGREGATOR_IMPORTS
    assert not roots & FORBIDDEN_NAMES and not names & FORBIDDEN_NAMES, names & FORBIDDEN_NAMES
    assert spec_calls == 1 and "v3_aggregate.py" in s.AGGREGATOR.read_text()
    module = s.aggregator()
    for name in ("strip", "aggregate", "render_md", "read_raw_dir", "run", "main"):
        assert callable(getattr(module, name)), name
    # Control: the same scan catches an impure module.
    impure = ast.parse("import os, subprocess\nvalue = os.environ\n")
    found = {a.name for n in ast.walk(impure) if isinstance(n, ast.Import) for a in n.names}
    assert found & FORBIDDEN_NAMES == {"os", "subprocess"}
    # The loaded sibling is loaded under its own module name and never the v3 file itself.
    assert module._V3.__name__ == "s42_v3_aggregate_loaded"
    assert s.SCRIPTS.joinpath("v3_aggregate.py").is_file()


def test_the_recorder_imports_only_the_standard_library_at_the_top() -> None:
    tree = parse(s.RECORDER)
    top: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            top.add((node.module or "").split(".")[0])
    assert top and top <= set(sys.stdlib_module_names) | {"__future__"}, top - set(
        sys.stdlib_module_names
    )
    lazy: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for inner in ast.walk(node):
                if isinstance(inner, ast.Import):
                    lazy |= {a.name.split(".")[0] for a in inner.names}
                elif isinstance(inner, ast.ImportFrom):
                    lazy.add((inner.module or "").split(".")[0])
    assert lazy >= LAZY_IMPORTS, LAZY_IMPORTS - lazy
    assert not top & (LAZY_IMPORTS | {"atlas_aci"})
    # atlas_aci is only ever loaded lazily, by name, never as a top-level import.
    assert 'importlib.import_module("atlas_aci.protocol.revisions")' in s.RECORDER.read_text()
    # Every server is `python -m atlas_aci ...` as an argument list: no shell anywhere.
    text = s.RECORDER.read_text()
    assert "shell=True" not in text and "os.system" not in text and "os.popen" not in text


def test_the_sibling_scripts_are_untouched() -> None:
    names = {p.name for p in s.SCRIPTS.glob("s42_*.py")}
    assert names == {"s42_aggregate.py", "s42_record.py"}
    assert sha_of(s.SCRIPTS / "v3_aggregate.py") == V3_AGGREGATE_SHA  # frozen by the scope
    assert sha_of(s.SCRIPTS / "v3_record.py") == V3_RECORD_SHA


# ------------------------------------------------------------------------ vocabularies
def test_the_closed_vocabularies_are_unique_and_equal_the_manifest() -> None:
    aggregator = s.aggregator()
    manifest = s.manifest()
    for name in (
        "OUTCOMES", "CLASSES", "CHECKS", "UNRESOLVED_LIMITS", "HONESTY", "PHASES", "DOMAINS",
        "RECORD_KINDS", "STATUS", "FAILURES", "SOURCES", "RUSAGE_UNITS", "VERDICTS", "CHECK_STATES",
        "ERROR_CODES_AGG", "INVARIANTS", "SATURATION", "VALIDATION", "ERROR_CODES",
    ):  # fmt: skip
        values = getattr(aggregator, name)
        assert len(set(values)) == len(values) > 0, name
    assert tuple(manifest["required_checks"]) == aggregator.CHECKS
    assert tuple(manifest["unresolved_limits"]) == aggregator.UNRESOLVED_LIMITS
    assert tuple(manifest["honesty"]) == aggregator.HONESTY
    assert tuple(manifest["isolation"]["classes"]) == aggregator.CLASSES
    assert len(aggregator.CHECKS) == 13 and len(aggregator.UNRESOLVED_LIMITS) == 14
    assert aggregator.VERDICTS == ("blocked", "ready_for_operator_review")
    assert aggregator.CHECK_STATES == ("pass", "fail", "missing")
    # Control: a manifest that drifts from the constants is refused with a typed error.
    drifted = copy.deepcopy(manifest)
    drifted["unresolved_limits"] = drifted["unresolved_limits"][:-1]
    with pytest.raises(aggregator.ReadinessInputError) as caught:
        aggregator.check_manifest(drifted)
    assert caught.value.code == "vocabulary"
    openended = copy.deepcopy(manifest)
    deny = next(c for c in openended["isolation"]["cases"] if c["expected"] == "deny")
    deny["expected_outcomes"] = [*deny["expected_outcomes"], "tool_error:OTHER"]
    with pytest.raises(aggregator.ReadinessInputError):
        aggregator.check_manifest(openended)
    extra_key = {**manifest, "surprise": 1}
    with pytest.raises(aggregator.ReadinessInputError) as unknown:
        aggregator.check_manifest(extra_key)
    assert unknown.value.code == "unknown_shape"


# ------------------------------------------------------------------------ the case table
def test_pattern_and_witness_tokens_are_disjoint_and_witnesses_never_travel() -> None:
    manifest = s.manifest()
    canaries = manifest["canaries"]
    for domain in ("A", "B"):
        pattern, witness = canaries[domain]["pattern"], canaries[domain]["witness"]
        assert pattern != witness and pattern not in witness and witness not in pattern
    witnesses = {canaries[d]["witness"] for d in ("A", "B", "C")}
    assert len(witnesses) == 3 and "pattern" not in canaries["C"]  # C never appears in a request
    requests = " ".join(request_text(c) for c in manifest["isolation"]["cases"])
    requests += json.dumps(manifest["capacity"]["workload"]["mix"], sort_keys=True)
    requests += json.dumps(manifest["capacity"]["saturation"], sort_keys=True)
    for witness in witnesses:
        assert witness not in requests, witness
    assert canaries["A"]["pattern"] in requests and canaries["B"]["pattern"] in requests  # control
    trees = manifest["trees"]
    for tree, domain in (("A2", "A"), ("B", "B")):
        pattern, witness = canaries[domain]["pattern"], canaries[domain]["witness"]
        files = trees[tree]
        assert any(witness in name for name in files), tree  # in a file path
        same_line = [
            line
            for text in files.values()
            for line in text.splitlines()
            if pattern in line and witness in line
        ]
        assert len(same_line) >= (30 if domain == "A" else 2), tree  # on the same source line
    assert canaries["C"]["witness"] in json.dumps(trees["A1"])  # the before snapshot only
    assert canaries["C"]["witness"] not in json.dumps(trees["A2"])
    assert all(text.isascii() for tree in trees.values() for text in tree.values())
    assert all(canaries[t]["name"] in json.dumps(trees[t]) for t in ("T1", "T2"))
    assert canaries["T1"]["name"] not in json.dumps(trees["T2"])


def test_every_deny_case_has_a_same_shape_control_that_reaches_its_target() -> None:
    manifest = s.manifest()
    cases = manifest["isolation"]["cases"]
    by_key = {(c["phase"], c["id"]): c for c in cases}
    denies = [c for c in cases if c["expected"] == "deny"]
    assert len(denies) == 18  # 16 in the main phase, 2 in the revocation phase
    for case in denies:
        control = (
            by_key[(case["phase"], case["control"])]
            if (case["phase"], case["control"]) in by_key
            else by_key[("main", case["control"])]
        )
        assert control["expected"] == "allow" and control["expected_outcomes"] == ["ok"]
        assert case["target_domain"] in control["domains"], case["id"]
        assert case["domains"] == [] and case["target_domain"] in ("A", "B", "C")
        assert not any(o.endswith(":OTHER") or o == "http:other" for o in case["expected_outcomes"])
        req, ctl = case["request"], control["request"]
        assert req["kind"] == ctl["kind"], case["id"]
        if case["class"] != "disabled_tool":  # no control can exist for a tool nobody may call
            assert req.get("tool") == ctl.get("tool"), case["id"]
            assert set(req.get("arguments", {})) == set(ctl.get("arguments", {})), case["id"]
        else:
            assert req["tool"] in ("test_dry_run", "memex_read")
        assert case["nonce_carrier"] == ("$NONCE" in request_text(case))
    carriers = [c for c in cases if c["nonce_carrier"]]
    assert {c["id"] for c in carriers} >= {"M01", "M02", "M04", "D01", "D04", "R4"}
    assert any(c["id"] == "M07" and c["domains"] == ["A", "C"] for c in cases)
    # Isolation limits that keep W1, W2 and the main race apart (checker condition 4).
    limits = manifest["isolation"]["limits"]
    assert limits["max_leases_per_principal"] == 3 and limits["max_graph_leases"] == 8
    assert {c["id"] for c in cases if c["phase"] == "warm"} == {"W1", "W2"}
    uses = {c["id"]: c["request"].get("uses") for c in cases if c["request"]["kind"] != "tool"}
    assert uses["M08"] == "W1" and uses["M09"] == "W2" and uses["D10"] == "W2"
    assert manifest["isolation"]["settle_ms"] >= 300
    assert manifest["isolation"]["teeth_rerun"] == ["D01", "D02"]


def test_capacity_grants_cover_every_workload_call_and_saturation_fills_the_gate() -> None:
    manifest = s.manifest()
    cap = manifest["capacity"]
    work = cap["workload"]
    granted = {(g["subject"], g["repository"]): set(g["tools"]) for g in work["grants"]}
    assert len(work["mix"]) == work["calls_per_client"] == 12 and work["clients"] == 6
    for principal in work["client_principals"]:
        for repo, tool, _arguments in work["mix"]:
            assert tool in granted[(principal, repo)], (principal, repo, tool)
    sat = cap["saturation"]
    assert 2 * sat["holds"] == cap["listen"]["max_inflight"]  # p1 and p2 hold the whole gate
    assert sat["holds"] == cap["listen"]["max_inflight_per_principal"]
    assert len({sat["principal"], sat["other"], sat["gate_principal"]}) == 3
    assert work["max_attempts"] <= 10
    deadlines = manifest["deadlines"]
    assert deadlines["interrupt_case_s"] >= 120 and deadlines["recorder_total_s"] >= 300
    assert s.RECORDER_TIMEOUT_S >= deadlines["recorder_total_s"] >= 300
    assert deadlines["request_s"] == 30 and deadlines["startup_s"] == 60


def test_the_interruption_matrix_names_every_kind_and_both_teeth() -> None:
    manifest = s.manifest()
    cases = {c["id"]: c for c in manifest["interruption"]["cases"]}
    assert list(cases) == [
        "cancel_marker", "cancel_sigterm", "kill_checkpoint", "kill_mid_publish",
        "kill_post_replace", "kill_pre_receipt", "gc_vs_lease", "gc_vs_build",
        "teeth_replace_by_copy", "teeth_wrong_expectation",
    ]  # fmt: skip
    assert [c["id"] for c in cases.values() if not c["expect_valid"]] == [
        "teeth_replace_by_copy",
        "teeth_wrong_expectation",
    ]
    assert cases["cancel_marker"]["injection"]["seam"] == "staged"
    assert cases["cancel_sigterm"]["injection"]["seam"] == "staged"  # S41 uses staged
    assert cases["kill_checkpoint"]["injection"] == {"kind": "kill", "seam": "checkpoint", "at": 2}
    assert manifest["interruption"]["workers"] + 1 <= 4  # plus the independent-phase thread
    for case in cases.values():
        rendered = json.dumps(case["facts"])
        assert "-9" not in rendered and "137" not in rendered  # normalized, never a raw exit code


# --------------------------------------------------------------------- the D5 ledger rule
def _case(manifest: dict[str, Any], case_id: str) -> dict[str, Any]:
    return next(
        c for c in manifest["isolation"]["cases"] if c["id"] == case_id and c["phase"] == "main"
    )


def test_the_ledger_rule_fires_on_each_of_its_clauses_and_only_on_them() -> None:
    aggregator = s.aggregator()
    manifest = s.manifest()
    allow, deny, carrier = _case(manifest, "M01"), _case(manifest, "D03"), _case(manifest, "D01")
    clean_allow = s.synthetic_row(case_id="M01")
    clean_deny = s.synthetic_row(
        case_id="D03",
        **{"class": "ungranted_tool"},
        expected="deny",
        outcome="tool_error:FORBIDDEN",
        canary_hits=[],
        server_seen=None,
    )
    clean_carrier = s.synthetic_row(
        case_id="D01", **{"class": "ungranted_repo"}, expected="deny",
        outcome="http:403:repository_forbidden", canary_hits=[], server_seen=False,
    )  # fmt: skip
    for row, case in ((clean_allow, allow), (clean_deny, deny), (clean_carrier, carrier)):
        assert aggregator.is_unauthorized(row, case) is False  # controls: nothing fires
        assert s.independent_unauthorized(manifest, [row]) == []
    # Clause 1: a deny that succeeded.
    assert aggregator.is_unauthorized({**clean_deny, "outcome": "ok"}, deny) is True
    # Clause 2a: an allow row that hit a domain outside its authorization.
    leaky = {**clean_allow, "canary_hits": ["A", "B"]}
    assert aggregator.is_unauthorized(leaky, allow) is True
    # Clause 2b: a deny row with a typed error outcome that still carried a witness.
    carried = {**clean_deny, "canary_hits": ["A"]}
    assert carried["outcome"] == "tool_error:FORBIDDEN"
    assert aggregator.is_unauthorized(carried, deny) is True
    # Clause 3: a deny carrier whose nonce reached a server tool_call line.
    seen = {**clean_carrier, "server_seen": True}
    assert aggregator.is_unauthorized(seen, carrier) is True
    # The server_seen clause applies to carriers only.
    assert aggregator.is_unauthorized({**clean_deny, "server_seen": True}, deny) is False
    for row, case in ((leaky, allow), (carried, deny), (seen, carrier)):
        assert s.independent_unauthorized(manifest, [row]) == [row["case_id"]]
        assert case["id"] == row["case_id"]


def test_the_checks_read_status_first_and_never_raise_on_a_failed_record() -> None:
    committed = s.committed_records()
    baseline = s.aggregate(committed)["report"]
    assert baseline["verdict"] == "ready_for_operator_review"
    for record in committed:
        broken = copy.deepcopy(committed)
        target = next(
            r for r in broken if (r["kind"], r["unit"]) == (record["kind"], record["unit"])
        )
        target.pop("body")
        target.pop("observed", None)
        target.update(status="failed", failure={"code": "subprocess_error"})
        report = s.aggregate(broken)["report"]
        assert report["verdict"] == "blocked", (record["kind"], record["unit"])
        assert report["checks"]["records.complete"] == "fail"
        assert report["failed_checks"], (record["kind"], record["unit"])


def test_the_loader_maps_every_malformed_input_to_a_typed_error(tmp_path: Path) -> None:
    aggregator = s.aggregator()
    good = s.find(s.committed_records(), "environment", "main")
    plain = aggregator.canonical(good)
    assert aggregator.decode_record(gzip.compress(plain, mtime=0), "x") == good  # control
    bad_utf8 = gzip.compress(b'{"a":"\xff"}\n', mtime=0)
    cases = {
        "bad gzip": (b"not gzip at all", "not_canonical"),
        "truncated gzip": (gzip.compress(plain, mtime=0)[:20], "not_canonical"),
        "bad json": (gzip.compress(b"{nope", mtime=0), "not_canonical"),
        "bad utf-8": (bad_utf8, "not_canonical"),
        "not an object": (gzip.compress(b"[1,2,3]\n", mtime=0), "unknown_shape"),
        "not canonical": (
            gzip.compress(json.dumps(good, indent=2).encode(), mtime=0),
            "not_canonical",
        ),
    }
    for label, (raw, code) in cases.items():
        with pytest.raises(aggregator.ReadinessInputError) as caught:
            aggregator.decode_record(raw, label)
        assert caught.value.code == code, label
    manifest = s.manifest()
    extra = {**good, "surprise": 1}
    with pytest.raises(aggregator.ReadinessInputError) as unknown:
        aggregator.aggregate(manifest, [extra])
    assert unknown.value.code == "unknown_shape"
    folder = tmp_path / "raw"
    folder.mkdir()
    (folder / "readme.txt").write_text("x")
    with pytest.raises(aggregator.ReadinessInputError) as stray:
        aggregator.read_raw_dir(folder)
    assert stray.value.code == "unknown_shape"
    (folder / "readme.txt").unlink()
    (folder / "wrong.name.json.gz").write_bytes(gzip.compress(plain, mtime=0))
    with pytest.raises(aggregator.ReadinessInputError) as named:
        aggregator.read_raw_dir(folder)
    assert named.value.code == "unknown_shape"
    for body_change in (
        lambda r: r["body"].update(extra=1),
        lambda r: r["body"].pop("lock_canary"),
    ):
        broken = copy.deepcopy(good)
        body_change(broken)
        with pytest.raises(aggregator.ReadinessInputError) as shape:
            aggregator.aggregate(manifest, [broken])
        assert shape.value.code == "unknown_shape"


def test_rows_are_bound_to_the_manifest_case_table() -> None:
    aggregator = s.aggregator()
    committed = s.committed_records()
    for change, code in (
        (lambda r: s.row_of(r, "D01").update(case_id="D99"), "unknown_unit"),
        (lambda r: s.row_of(r, "D01").update(expected="allow"), "manifest_mismatch"),
        (lambda r: s.row_of(r, "D01").update(route="b"), "manifest_mismatch"),
        (lambda r: s.row_of(r, "D01").update(canary_hits=["Z"]), "vocabulary"),
        (lambda r: s.row_of(r, "D01").update(surprise=1), "unknown_shape"),
    ):
        broken = s.mutated(committed, "isolation", "main", change)
        with pytest.raises(aggregator.ReadinessInputError) as caught:
            s.aggregate(broken)
        assert caught.value.code == code


def test_strip_and_the_input_digest_ignore_observed_and_diagnostic_at_every_depth() -> None:
    aggregator = s.aggregator()
    value = {
        "a": 1,
        "observed": {"x": 1},
        "rows": [{"observed": 1, "k": [{"diagnostic": 2, "keep": 3}]}],
        "diagnostic": [],
    }
    assert aggregator.strip(value) == {"a": 1, "rows": [{"k": [{"keep": 3}]}]}
    committed = s.committed_records()
    report = s.aggregate(committed)["report"]
    by_name = {i["name"]: i["sha256"] for i in report["inputs"]}
    assert len(by_name) == len(committed)
    for record in committed:
        name = f"{record['kind']}.{record['unit']}"
        assert by_name[name] == aggregator.digest(aggregator.strip(record))
        gz_sha = hashlib.sha256((s.COMMITTED_RAW / f"{name}.json.gz").read_bytes()).hexdigest()
        assert by_name[name] != gz_sha  # never the digest of the compressed bytes
    changed = copy.deepcopy(committed)
    s.find(changed, "capacity", "main")["observed"]["attempts"] = 999
    assert s.aggregate(changed)["report"]["inputs"] == report["inputs"]


# ------------------------------------------------------------- the recorder's pure helpers
def _response(rec: Any, status: int, body: bytes, ctype: str = "text/event-stream", **headers: str):
    pairs = [("content-type", ctype), *headers.items()]
    return rec.Resp(status, "x", pairs, body)


def _sse(message: dict[str, Any]) -> bytes:
    return b"event: message\r\ndata: " + json.dumps(message).encode() + b"\r\n\r\n"


def test_the_outcome_classifier_follows_its_fixed_order() -> None:
    rec = s.recorder()
    ok_result = {
        "content": [{"type": "text", "text": json.dumps({"matches": []})}],
        "isError": False,
    }
    cases = [
        (_response(rec, 200, _sse({"jsonrpc": "2.0", "id": 1, "result": ok_result})), "ok"),
        (  # HTTP status and reason first: a 401 never reaches the parser
            _response(
                rec, 401, b'{"error":{"data":{"reason":"credentials_missing"}}}', "application/json"
            ),
            "http:401:credentials_missing",
        ),
        (
            _response(
                rec,
                403,
                b'{"error":{"data":{"reason":"repository_forbidden"}}}',
                "application/json",
            ),
            "http:403:repository_forbidden",
        ),
        (
            _response(
                rec, 503, b'{"error":{"data":{"reason":"inflight_limit"}}}', "application/json"
            ),
            "http:503:inflight_limit",
        ),
        (_response(rec, 500, b"boom", "text/plain"), "http:other"),
        (_response(rec, 401, b"not json", "text/plain"), "http:other"),
        (  # an error member gives rpc_error, with a reason from the closed set
            _response(
                rec,
                200,
                _sse(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "error": {"code": -32602, "data": {"reason": "SCOPE_MISMATCH"}},
                    }
                ),
            ),
            "rpc_error:SCOPE_MISMATCH",
        ),
        (  # an error member with a reason outside the set is OTHER, never silently typed
            _response(
                rec,
                200,
                _sse(
                    {"jsonrpc": "2.0", "id": 1, "error": {"code": -1, "data": {"reason": "WEIRD"}}}
                ),
            ),
            "rpc_error:OTHER",
        ),
        (  # isError: the code is read from the tool payload (legacy string or structured object)
            _response(
                rec,
                200,
                _sse(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "result": {
                            "isError": True,
                            "content": [
                                {"type": "text", "text": json.dumps({"error": "FORBIDDEN"})}
                            ],
                        },
                    }
                ),
            ),
            "tool_error:FORBIDDEN",
        ),
        (
            _response(
                rec,
                200,
                _sse(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "result": {
                            "isError": True,
                            "structuredContent": {"error": {"code": "EXPIRED_REFERENCE"}},
                            "content": [],
                        },
                    }
                ),
            ),
            "tool_error:EXPIRED_REFERENCE",
        ),
        (  # a status: error payload without isError is still an error, never ok
            _response(
                rec,
                200,
                _sse(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "result": {
                            "isError": False,
                            "structuredContent": {
                                "status": "error",
                                "error": {"code": "INVALID_REFERENCE"},
                            },
                            "content": [],
                        },
                    }
                ),
            ),
            "tool_error:INVALID_REFERENCE",
        ),
        (_response(rec, 200, b"event: message\r\ndata: {not json\r\n\r\n"), "transport_error"),
        (_response(rec, 200, b"", "application/json"), "transport_error"),
        (_response(rec, 200, b'{"jsonrpc":"2.0","id":1}', "application/json"), "transport_error"),
    ]
    for resp, expected in cases:
        assert rec.classify(resp)[0] == expected, (resp.status, resp.body[:60])
    assert rec.classify(None)[0] == "transport_error"


def test_the_canary_scan_decodes_resource_blobs_and_the_nonce_is_phase_specific() -> None:
    rec = s.recorder()
    witness = b"s42wit_a_deadbeef0000"
    blob = base64.b64encode(b"callee " + witness).decode()
    message = {"jsonrpc": "2.0", "id": 1, "result": {"contents": [{"blob": blob}]}}
    resp = _response(rec, 200, _sse(message))
    assert witness not in resp.body  # a raw-byte scan alone never sees it
    assert witness in rec.scan_bytes(resp)  # control: the decoded blob is scanned too
    plain = _response(rec, 200, _sse({"jsonrpc": "2.0", "id": 1, "result": {"contents": []}}))
    assert witness not in rec.scan_bytes(plain)
    assert rec.nonce_for("main", "D01") != rec.nonce_for("teeth", "D01")
    assert rec.nonce_for("main", "D01") == rec.nonce_for("main", "D01")
    assert (
        rec.nonce_for("main", "D01").startswith("s42n") and len(rec.nonce_for("main", "D01")) == 20
    )


def test_the_reaper_kills_every_tracked_child_and_a_missing_server_is_a_typed_failure(
    tmp_path: Path,
) -> None:
    rec = s.recorder()
    # The reaper kills every direct child of its process, so it runs in a process of its own.
    code = (
        "import contextlib, importlib.util, os, subprocess, sys\n"
        "spec = importlib.util.spec_from_file_location('rec', sys.argv[1])\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "sys.modules['rec'] = m\n"
        "spec.loader.exec_module(m)\n"
        "p = m.spawn([sys.executable, '-c', 'import time; time.sleep(120)'], env=m.child_env())\n"
        "assert p.poll() is None\n"  # control: alive before the reaper runs
        "os.kill(p.pid, 0)\n"
        "m.reap_children()\n"
        "with contextlib.suppress(subprocess.TimeoutExpired):\n"
        "    p.wait(timeout=15)\n"
        "try:\n"
        "    os.kill(p.pid, 0)\n"
        "except ProcessLookupError:\n"
        "    print('GONE')\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code, str(s.RECORDER)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0 and done.stdout.strip() == "GONE", (done.stdout, done.stderr)
    with pytest.raises(rec.RecorderError) as caught:
        rec.Server(
            "never",
            [sys.executable, "-c", "import sys; sys.stderr.write('no marker here')"],
            tmp_path,
            marker="shared_http_listening",
            startup_s=5,
        )
    assert caught.value.code == "server_start_timeout"
    env = rec.child_env({"ATLAS_ACI_JOB_FAULTS": "{}"})
    assert env["ATLAS_ACI_JOB_FAULTS"] == "{}"
    assert not [k for k in rec.child_env() if k.startswith("ATLAS_ACI_")]


def test_the_recorder_cli_exit_codes(tmp_path: Path) -> None:
    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(s.RECORDER), *args],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(ROOT / "mcp-server"),
        )

    helped = run("--help")
    assert helped.returncode == 0 and "--manifest" in helped.stdout
    out, work = tmp_path / "out", tmp_path / "work"
    base = ["--manifest", str(s.MANIFEST), "--out", str(out), "--work", str(work)]
    assert run(*base, "--phases", "nonsense").returncode == 2
    assert run(*base, "--workers", "9").returncode == 2
    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    refused = run("--manifest", str(bad), "--out", str(out), "--work", str(work))
    assert refused.returncode == 2 and "invalid manifest" in refused.stderr
    assert not out.exists()  # nothing is written for a usage error
    out.mkdir()
    (out / "keep.txt").write_text("x")
    occupied = run(*base)
    assert occupied.returncode == 3 and "not empty" in occupied.stderr
    assert [p.name for p in out.iterdir()] == ["keep.txt"]  # never overwritten


# ------------------------------------------------------------------------------ invariance
def test_the_product_surface_is_byte_unchanged() -> None:
    root = ROOT / "mcp-server"
    assert sha_of(root / "pyproject.toml") == PYPROJECT_SHA
    assert sha_of(root / "uv.lock") == UV_LOCK_SHA
    assert sha_of(root / "Dockerfile") == DOCKERFILE_SHA
    assert sha_of(root / "Dockerfile.dev") == DOCKERFILE_DEV_SHA
    files = sorted((root / "src").rglob("*.py"))
    digest = hashlib.sha256(
        json.dumps([[str(p.relative_to(root / "src")), sha_of(p)] for p in files]).encode()
    ).hexdigest()
    assert len(files) == 106 and digest == SRC_TREE_DIGEST
    from atlas_aci.enforcement import READ_ONLY_TOOLS

    assert len(READ_ONLY_TOOLS) == 7
    with tempfile.TemporaryDirectory() as repo:
        done = subprocess.run(
            [sys.executable, "-m", "atlas_aci", "tools", "--repo", repo],
            capture_output=True,
            timeout=120,
            cwd=str(root),
        )
    assert done.returncode == 0
    assert len(done.stdout) == TOOLS_BYTES
    assert hashlib.sha256(done.stdout).hexdigest() == TOOLS_SHA
    assert sha_of(ROOT / "atlas" / "evals" / "v3" / "manifest.v1.json") == (
        "3b901b99607d3db95d02a79794792cc8fb0df142901dc35fb502a1fa8e49b1ce"
    )


# --------------------------------------------------------------------------------- documents
def _documents() -> dict[str, str]:
    added = _changelog_bullets()
    evals = (ROOT / "atlas" / "evals" / "README.md").read_text()
    section = evals[evals.index("## ACI-S42") :]
    return {
        "evidence README": s.EVIDENCE_README.read_text(),
        "readiness report": s.REPORT_MD.read_text(),
        "changelog bullet": added[-3],
        "readme row": _readme_row(),
        "evals section": section,
    }


def _changelog_bullets() -> list[str]:
    lines = (ROOT / "CHANGELOG.md").read_text().splitlines()
    unreleased = lines.index("## [Unreleased]")
    sections = [i for i, line in enumerate(lines) if line.startswith("### ") and i > unreleased]
    added, changed = sections[1], sections[2]
    starts = [i for i in range(added, changed) if lines[i].startswith("- **")]
    ends = [*starts[1:], changed]
    return ["\n".join(lines[a:b]) for a, b in zip(starts, ends, strict=True)]


def _readme_row() -> str:
    readme = (ROOT / "README.md").read_text().splitlines()
    index = next(
        i
        for i, line in enumerate(readme)
        if line.startswith("| [`docs/operations/index-jobs-and-retention.md`]")
    )
    return readme[index + 1]


def test_the_documents_state_the_limits_and_use_no_overclaiming_words() -> None:
    manifest = s.manifest()
    docs = _documents()
    for name, text in docs.items():
        assert text.isascii(), name
        assert not words(text) & BANNED, (name, words(text) & BANNED)
        assert not s.ready_word(text), name  # only ready_for_operator_review is ever used
    for name in ("evidence README", "readiness report", "evals section"):
        text = docs[name]
        for statement in manifest["honesty"]:
            assert statement in text, (name, statement[:30])
        assert "zero within the enumerated cases" in text, name
        for limit in manifest["unresolved_limits"]:
            assert limit in text, (name, limit)
        for host in HOSTS:
            assert re.search(rf"{re.escape(host)}.{{0,12}}untested", text), (name, host)
        assert "ready_for_operator_review" in text
    assert (
        "isolation" in docs["readiness report"] and "enumerated cases" in docs["readiness report"]
    )
    assert (
        "No real host (Claude Code, GitHub Copilot, Cursor) was tested" in docs["readiness report"]
    )
    # Positive controls for the word scan itself.
    assert s.ready_word("this is ready") == ["ready"]
    assert not s.ready_word("already ready_for_operator_review readiness unready")
    assert "safe" in words("Safe to run") and "compatible" in words("is compatible")


def test_the_evidence_directory_holds_only_bounded_secret_free_canonical_records() -> None:
    aggregator = s.aggregator()
    manifest = s.manifest()
    sha = aggregator.manifest_sha(manifest)
    files = sorted(s.COMMITTED_RAW.iterdir())
    assert files and all(p.name.endswith(".json.gz") for p in files)
    assert sum(p.stat().st_size for p in files) < 400_000
    assert all(p.stat().st_size < 150_000 for p in files)
    assert len(files) == len(aggregator.expected_units(manifest))
    for path in files:
        record = aggregator.decode_record(path.read_bytes(), path.name)
        assert record["manifest_sha256"] == sha and record["status"] == "complete"
    secrets = (
        re.compile(r"-----BEGIN"),
        re.compile(r'"d"\s*:'),
        re.compile(r"eyJ[A-Za-z0-9_-]{20,}"),
    )
    scanned = 0
    for base in (s.EVIDENCE, ROOT / "atlas" / "evals" / "shared"):
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix == ".so":
                continue
            assert path.suffix != ".so"
            data = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
            text = data.decode("utf-8", errors="replace")
            for pattern in secrets:
                assert not pattern.search(text), (path.name, pattern.pattern)
            scanned += 1
    assert scanned >= 28
    assert not list(s.EVIDENCE.rglob("*.so")) and not list(s.EVIDENCE.rglob("*.pem"))
    # Control: the secret scan does catch a planted key and a planted token.
    assert secrets[0].search("-----BEGIN PRIVATE KEY-----")
    assert secrets[2].search("eyJ" + "a" * 30)
    report = json.loads(s.REPORT_JSON.read_text())
    md_verdict = next(
        line for line in s.REPORT_MD.read_text().splitlines() if line.startswith("Verdict: ")
    )
    assert md_verdict.split()[1] == report["report"]["verdict"]
    assert report["report"]["unresolved_limits"] == manifest["unresolved_limits"]
    assert report["report"]["environment"]["observed"]["image"]  # which environment produced it
    assert s.REPORT_JSON.read_text().isascii() and s.REPORT_JSON.read_text().endswith("\n")


def test_the_new_files_form_exactly_the_documented_sets() -> None:
    slices = ROOT / "mcp-server" / "tests" / "slices"
    assert {p.name for p in slices.glob("test_s42_*.py")} == {
        "test_s42_support.py",
        "test_s42_contract.py",
        "test_s42_isolation.py",
    }
    assert {p.name for p in (ROOT / "docs" / "operations").glob("*.md")} == {
        "shared-http-deployment.md",
        "index-jobs-and-retention.md",
    }
    assert sorted(p.name for p in s.EVIDENCE.iterdir()) == [
        "README.md",
        "raw",
        "readiness-report.json",
        "readiness-report.md",
    ]
    assert (ROOT / "atlas" / "evals" / "shared" / "manifest.v1.json").is_file()
    assert [p.name for p in (ROOT / "atlas" / "evals" / "shared").iterdir()] == ["manifest.v1.json"]


def test_the_changelog_bullet_and_the_readme_row_sit_where_the_pins_expect() -> None:
    bullets = _changelog_bullets()
    assert bullets[-3].startswith("- **Shared-operation readiness evidence (ACI-S42).**")
    assert bullets[-2].startswith(
        "- **Index jobs, retained generations and lease-aware garbage collection (ACI-S41).**"
    )
    assert bullets[-1].startswith("- **Shared multi-principal HTTP mode (ACI-S40).**")
    text = (ROOT / "CHANGELOG.md").read_text()
    assert text.count("(ACI-S42)") == 1
    body = bullets[-3]
    assert not any(line.startswith("- ") for line in body.splitlines()[1:])
    assert "(ACI-S41)" not in body and "(ACI-S40)" not in body
    for needle in (
        "scripts/eval/s42_record.py",
        "scripts/eval/s42_aggregate.py",
        "docs/evidence/s42/",
        "not a deployment; no product code changes",
    ):
        assert needle in body, needle
    readme = (ROOT / "README.md").read_text().splitlines()
    s41 = next(
        i
        for i, line in enumerate(readme)
        if line.startswith("| [`docs/operations/index-jobs-and-retention.md`]")
    )
    row = readme[s41 + 1]
    assert row.startswith("| [`docs/evidence/s42/README.md`](docs/evidence/s42/README.md) |")
    assert row.count("|") == 3 and "docs/operations/" not in row
    assert "canary-missions.md" not in row
    assert readme[s41 + 2].startswith("| [`INTEGRATION.md`]") or "INTEGRATION.md" in readme[s41 + 2]
    assert sum("docs/evidence/s42/README.md" in line for line in readme) == 1
