"""ACI-S29: import one OpenAPI 3.1 JSON document and explain contract changes between snapshots.

Every import and comparison is real: documents are parsed, referenced and hashed by the shipped
code, the CLI runs through click, and nothing about the behaviour under test is mocked.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from atlas_aci.__main__ import cli
from atlas_aci.contracts import openapi_diff as od
from atlas_aci.enforcement import READ_ONLY_TOOLS
from atlas_aci.importers import openapi31 as o
from atlas_aci.protocol import results
from atlas_aci.query import admission, impact_admission, paging
from atlas_aci.reports.evidence import resolve_pointer as oracle
from atlas_aci.storage import evidence as ev

ROOT = Path(__file__).resolve().parents[3]
FX = ROOT / "mcp-server/tests/fixtures/openapi"
SRC = ROOT / "mcp-server/src/atlas_aci"
DOCS = ROOT / "docs/importers/openapi.md"
BASE_TOOLS_SHA256 = "94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56"
BASE_TOOLS_BYTES = 1631472


def fx(name: str) -> bytes:
    return (FX / name).read_bytes()


def load(name: str) -> dict:
    return json.loads(fx(name))


def enc(document) -> bytes:
    return json.dumps(document).encode()


def imp(raw: bytes, **limits) -> dict:
    snapshot = o.import_document(raw, o.normalize_options(limits))
    o.validate_snapshot(snapshot)
    return snapshot


def compare(before: bytes, after: bytes, findings=None, **limits) -> dict:
    options = o.normalize_options(limits)
    change = od.diff(
        o.import_document(before, options),
        o.import_document(after, options),
        od.normalize_diff_options({"max_findings": findings}),
    )
    od.validate_diff(change)
    return change


def cli_run(*args):
    return CliRunner().invoke(cli, [str(a) for a in args])


def resolves(document, at: str):
    """The pointer resolves in `document` through the independent reports.evidence oracle."""
    return oracle(document, at)


def S(*items):
    """Order-independent comparison helper: tuples mix None and str, so sort by repr."""
    return sorted(items, key=repr)


def shapes(change: dict, method: str, path: str) -> list[tuple]:
    """Sorted finding shapes of one identity: the exact set, not just presence."""
    return S(
        *(
            (
                f["kind"],
                f["reason"],
                f["change"],
                f["blocked_finding"],
                f["region"],
                ev.canonical(f["parameter"]) if f["parameter"] else None,
            )
            for f in change["findings"]
            if f["identity"] == {"method": method, "path": path}
        )
    )


def global_shapes(change: dict) -> list[tuple]:
    return S(
        *(
            (f["kind"], f["reason"], f["region"], f["before"]["pointer"] if f["before"] else "")
            for f in change["findings"]
            if f["identity"] is None
        )
    )


def doc_of(paths=None, **extra) -> dict:
    return {
        "openapi": "3.1.0",
        "info": {"title": "t", "version": "1"},
        "paths": paths or {},
        **extra,
    }


def op(**extra) -> dict:
    return {"responses": {"200": {"description": "ok"}}, **extra}


def query(name: str, **extra) -> dict:
    return {"name": name, "in": "query", "schema": {"type": "string"}, **extra}


def record_of(snapshot: dict, method: str, path: str) -> dict:
    found = [r for r in snapshot["operations"] if r["identity"] == {"method": method, "path": path}]
    assert len(found) == 1
    return found[0]


# ---- AC-S29-001 ------------------------------------------------------------------------------


def test_openapi_operation_identity():
    snapshot = imp(fx("identity-before.json"))
    identities = sorted(
        (r["identity"]["method"], r["identity"]["path"]) for r in snapshot["operations"]
    )
    assert identities == sorted(
        [
            ("GET", "/plain"),
            ("POST", "/plain"),
            ("GET", "/a"),
            ("POST", "/b"),
            ("GET", "/users/{}"),
            ("DELETE", "/users/{}"),
            ("GET", "/files/{}~v"),
            ("GET", "/users/"),
            ("POST", "/shout"),
            ("GET", "/stores/{}"),
            ("GET", "/x/{}"),
            ("GET", "/x/{}"),
        ]
    )
    assert ("GET", "/users/") in identities and ("GET", "/users/{}") in identities
    shout = [r for r in snapshot["operations"] if r["path_template"] == "/shout"]
    assert [r["identity"]["method"] for r in shout] == ["POST"]  # the uppercase GET key is unknown
    assert shout[0]["regions"]["other"]["pointers"] == ["/paths/~1shout/GET"]
    ids = {
        (r["identity"]["method"], r["identity"]["path"]): r["operation_id"]
        for r in snapshot["operations"]
    }
    assert ids[("GET", "/a")] == "dup" and ids[("POST", "/b")] == "dup"
    assert ids[("GET", "/plain")] is None and ids[("POST", "/plain")] is None
    store = record_of(snapshot, "GET", "/stores/{}")
    assert store["path_template"] == "/stores/{storeId}"
    assert store["path_item_pointer"] == "/paths/~1stores~1{storeId}"
    assert store["provenance"]["pointer"] == "/components/pathItems/Store/get"
    twins = [r for r in snapshot["operations"] if r["identity"]["path"] == "/x/{}"]
    assert len(twins) == 2
    assert [r["path_template"] for r in twins] == ["/x/{a}", "/x/{b}"]
    assert all(r["status"] == "incomplete" for r in twins)
    assert all(r["reasons"] == ["ambiguous_identity/normalized_identity_collision"] for r in twins)
    assert snapshot["status"] == "incomplete"

    change = compare(fx("identity-before.json"), fx("identity-after.json"))
    kinds = {f["kind"] for f in change["findings"]}
    assert "operation_removed" not in kinds and "operation_added" not in kinds
    region = ("review_required", "region_changed", None, None, "operationId", None)
    renamed = (
        "review_required",
        "path_parameter_changed",
        None,
        None,
        None,
        '{"in":"path","ordinal":0}',
    )
    assert shapes(change, "GET", "/plain") == [region]
    assert shapes(change, "POST", "/plain") == []
    assert shapes(change, "GET", "/a") == [region]
    assert shapes(change, "POST", "/b") == [region]
    assert shapes(change, "GET", "/users/{}") == S(region, renamed)
    assert shapes(change, "DELETE", "/users/{}") == S(region, renamed)
    ambiguous = [
        f for f in change["findings"] if f["identity"] == {"method": "GET", "path": "/x/{}"}
    ]
    assert len(ambiguous) == 4
    assert {(f["kind"], f["reason"]) for f in ambiguous} == {
        ("review_required", "ambiguous_identity")
    }
    assert sorted(bool(f["before"]) for f in ambiguous) == [False, False, True, True]
    assert all((f["before"] is None) != (f["after"] is None) for f in ambiguous)
    assert len(change["findings"]) == 1 + 1 + 1 + 2 + 2 + 4 and global_shapes(change) == []


# ---- AC-S29-002 ------------------------------------------------------------------------------


def _provenance_of(snapshot: dict) -> list[tuple[str, dict]]:
    found = []
    for item in snapshot["path_items"]:
        found.append(("path_item", item["provenance"]))
    for record in snapshot["operations"]:
        found.append(("operation", record["provenance"]))
        for param in record["parameters"]:
            found.append(("parameter", param["provenance"]))
    for region in snapshot["document_regions"]:
        found.append(("document_region", region["provenance"]))
    for diagnostic in snapshot["diagnostics"]:
        found.append(("diagnostic", diagnostic["provenance"]))
    return found


@pytest.mark.parametrize("name", ["petstore-before.json", "refs.json", "identity-before.json"])
def test_openapi_element_provenance(name, tmp_path):
    raw = fx(name)
    document = json.loads(raw)
    snapshot = imp(raw)
    run = cli_run("openapi-snapshot", FX / name)
    assert run.exit_code == 0, run.stderr
    assert run.stdout_bytes == o.canonical_bytes(snapshot)
    assert json.loads(run.stdout_bytes) == snapshot
    artifact = hashlib.sha256(raw).hexdigest()
    expected_id = hashlib.sha256(
        (
            "atlas-contract-snapshot.v1:"
            + ev.canonical(
                {
                    "artifact_sha256": artifact,
                    "profile": "atlas-openapi31-profile.v1",
                    "options": snapshot["options"],
                }
            )
        ).encode()
    ).hexdigest()
    assert snapshot["snapshot_id"] == expected_id and snapshot["artifact"]["sha256"] == artifact
    elements = _provenance_of(snapshot)
    assert elements and {k for k, _ in elements} >= {"operation", "path_item", "document_region"}
    for kind, prov in elements:
        assert prov["artifact_sha256"] == artifact, kind
        assert prov["snapshot_id"] == expected_id, kind
        resolves(document, prov["pointer"])
    for record in snapshot["operations"]:
        value = resolves(document, record["provenance"]["pointer"])
        assert isinstance(value, dict) and value.get("operationId") == record["operation_id"]
        resolves(document, record["path_item_pointer"])
        # region pointers inherit the enclosing operation's provenance (artifact, snapshot)
        for region in record["regions"].values():
            assert set(region) == {"sha256", "status", "pointers"}  # provenance is the operation's
            for at in region["pointers"]:
                resolves(document, at)
        for param in record["parameters"]:
            prov = param["provenance"]
            declared = resolves(document, prov["declared_pointer"])
            assert declared["name"] == param["name"] and declared["in"] == param["in"]
            if prov["ref_chain"]:
                assert prov["ref_chain"][-1] == prov["declared_pointer"]
                assert prov["pointer"] != prov["declared_pointer"]
            else:
                assert prov["pointer"] == prov["declared_pointer"]
    for item in snapshot["path_items"]:
        if item["declared_pointer"] is not None:
            resolves(document, item["declared_pointer"])
    for diagnostic in snapshot["diagnostics"]:
        if diagnostic["kind"] == "unresolved_reference":
            assert resolves(document, diagnostic["provenance"]["pointer"]) == diagnostic["ref"]
    for region in snapshot["document_regions"]:
        assert o.digest(resolves(document, region["pointer"])) == region["sha256"]
        assert region["provenance"]["pointer"] == region["pointer"]
    if name == "identity-before.json":
        pointers = [p for _, prov in elements for p in [prov["pointer"]]]
        assert any("~0" in p and "~1" in p for p in pointers)
    if name == "petstore-before.json":
        before, after = fx(name), fx("petstore-after.json")
        change = compare(before, after)
        assert change["findings"]
        for finding in change["findings"]:
            for side, source in (("before", before), ("after", after)):
                ref = finding[side]
                if ref is not None:
                    assert ref["artifact_sha256"] == hashlib.sha256(source).hexdigest()
                    resolves(json.loads(source), ref["pointer"])
                    if ref["declared_pointer"] is not None:
                        resolves(json.loads(source), ref["declared_pointer"])
        copied = tmp_path / "elsewhere" / "copy.json"
        copied.parent.mkdir()
        copied.write_bytes(raw)
        assert imp(copied.read_bytes())["snapshot_id"] == snapshot["snapshot_id"]
        assert imp(raw, max_ref_depth=7)["snapshot_id"] != snapshot["snapshot_id"]
        spaced = imp(raw + b"\n")
        assert spaced["artifact"]["sha256"] != artifact
        assert spaced["snapshot_id"] != snapshot["snapshot_id"]
        out = cli_run("openapi-snapshot", copied, "--max-ref-depth", 7)
        assert (
            json.loads(out.stdout_bytes)["snapshot_id"] == imp(raw, max_ref_depth=7)["snapshot_id"]
        )


# ---- AC-S29-003 ------------------------------------------------------------------------------


def test_openapi_removed_operation_finding():
    before, after = fx("petstore-before.json"), fx("petstore-after.json")
    change = compare(before, after)
    removed = [f for f in change["findings"] if f["kind"] == "operation_removed"]
    assert len(removed) == 1
    finding = removed[0]
    assert finding["identity"] == {"method": "DELETE", "path": "/pets/{}"}
    assert finding["action"] == "review" and finding["reason"] is None
    delete = resolves(json.loads(before), finding["before"]["pointer"])
    assert delete["operationId"] == "deletePet"
    assert finding["after"]["pointer"] == "/paths/~1pets~1{petId}"
    assert finding["after"]["artifact_sha256"] == hashlib.sha256(after).hexdigest()
    assert finding["before"]["artifact_sha256"] == hashlib.sha256(before).hexdigest()
    assert finding["before"]["snapshot_id"] != finding["after"]["snapshot_id"]
    assert shapes(change, "DELETE", "/pets/{}") == [
        ("operation_removed", None, None, None, None, None)
    ]

    whole = load("petstore-after.json")
    del whole["paths"]["/pets/{petId}"]
    gone = compare(before, enc(whole))
    removals = [f for f in gone["findings"] if f["kind"] == "operation_removed"]
    assert len(removals) == 2 and {f["after"]["pointer"] for f in removals} == {"/paths"}
    nothing = load("petstore-after.json")
    del nothing["paths"]
    empty = compare(before, enc(nothing))
    assert {
        f["after"]["pointer"] for f in empty["findings"] if f["kind"] == "operation_removed"
    } == {""}

    renamed = load("petstore-before.json")
    item = renamed["paths"].pop("/pets/{petId}")
    for operation in ("get", "delete"):
        item[operation]["parameters"] = [dict(item[operation]["parameters"][0], name="id")]
    renamed["paths"]["/pets/{id}"] = item
    moved = compare(before, enc(renamed))
    assert [f for f in moved["findings"] if f["kind"] == "operation_removed"] == []
    assert shapes(moved, "DELETE", "/pets/{}") == [
        ("review_required", "path_parameter_changed", None, None, None, '{"in":"path","ordinal":0}')
    ]

    blocked_after = load("petstore-after.json")
    blocked_after["paths"]["/pets/{petId}"] = {"$ref": "https://x.example/item.json"}
    blocked = compare(before, enc(blocked_after))
    assert [f for f in blocked["findings"] if f["kind"] == "operation_removed"] == []
    for method in ("GET", "DELETE"):
        assert shapes(blocked, method, "/pets/{}") == [
            ("review_required", "incomplete_region", None, "operation_removed", "path_item", None)
        ]
    assert ("review_required", "incomplete_region", "path_item", "/paths/~1pets~1{petId}") in (
        global_shapes(blocked)
    )
    same = compare(before, before)
    assert same["findings"] == [] and same["findings_total"] == 0
    assert same["summary"] == {
        "newly_required_parameter": 0,
        "operation_added": 0,
        "operation_removed": 0,
        "review_required": 0,
    }
    # the symmetric case: an operation appearing under an unresolved path item is blocked too
    holder = doc_of({"/p": {"$ref": "https://x.example/item.json"}})
    appeared = doc_of({"/p": {"get": op()}})
    added = compare(enc(holder), enc(appeared))
    assert shapes(added, "GET", "/p") == [
        ("review_required", "incomplete_region", None, "operation_added", "path_item", None)
    ]


# ---- AC-S29-004 ------------------------------------------------------------------------------


def _single(before_params, after_params, path="/p/{id}", **item):
    before = doc_of({path: {"get": op(parameters=before_params), **item}})
    after = doc_of({path: {"get": op(parameters=after_params), **item}})
    return compare(enc(before), enc(after))


def test_openapi_new_required_parameter_finding():
    before, after = fx("petstore-before.json"), fx("petstore-after.json")
    change = compare(before, after)
    pets = [f for f in change["findings"] if f["identity"] == {"method": "GET", "path": "/pets"}]
    assert shapes(change, "GET", "/pets") == S(
        (
            "newly_required_parameter",
            None,
            "added_required",
            None,
            None,
            '{"in":"query","name":"limit"}',
        ),
        (
            "newly_required_parameter",
            None,
            "optional_to_required",
            None,
            None,
            '{"in":"header","name":"x-tenant"}',
        ),
        (
            "newly_required_parameter",
            None,
            "optional_to_required",
            None,
            None,
            '{"in":"query","name":"page"}',
        ),
    )
    by_name = {f["parameter"]["name"]: f for f in pets}
    limit = by_name["limit"]
    assert limit["before"]["pointer"] == "/paths/~1pets/get"  # absent before: the operation
    assert limit["after"]["pointer"] == "/paths/~1pets/get/parameters/0"
    tenant = by_name["x-tenant"]
    assert tenant["before"]["pointer"] == "/paths/~1pets/parameters/0"  # path level, optional
    assert tenant["after"]["pointer"] == "/paths/~1pets/get/parameters/1"  # operation override
    assert tenant["reserved_header"] is False
    page = by_name["page"]
    assert page["after"]["declared_pointer"] == "/components/parameters/Page"
    assert page["after"]["pointer"] == "/paths/~1pets/get/parameters/2"
    assert all(f["action"] == "review" for f in pets)

    # negative controls, built by in-test mutation
    assert _single([query("q")], [query("q", required=False)])["findings"] == []
    assert _single([query("q", required=False)], [query("q")])["findings"] == []
    down = _single([query("q", required=True)], [query("q", required=False)])
    assert shapes(down, "GET", "/p/{}") == [
        ("review_required", "parameter_changed", None, None, None, '{"in":"query","name":"q"}')
    ]
    cookie = {"name": "c", "in": "cookie", "schema": {"type": "string"}}
    added = _single([], [cookie])
    assert shapes(added, "GET", "/p/{}") == [
        ("review_required", "parameter_added", None, None, None, '{"in":"cookie","name":"c"}')
    ]
    path_param = {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}
    pathy = _single([], [path_param])
    assert shapes(pathy, "GET", "/p/{}") == [
        ("review_required", "path_parameter_changed", None, None, None, '{"in":"path","ordinal":0}')
    ]
    auth = {"name": "Authorization", "in": "header", "required": True, "schema": {"type": "string"}}
    reserved = _single([], [auth])
    assert shapes(reserved, "GET", "/p/{}") == [
        (
            "review_required",
            "parameter_added",
            None,
            None,
            None,
            '{"in":"header","name":"authorization"}',
        )
    ]
    only = next(f for f in reserved["findings"] if f["identity"])
    assert only["reserved_header"] is True
    both = _single([query("q")], [query("q", required=True, schema={"type": "integer"})])
    assert shapes(both, "GET", "/p/{}") == S(
        (
            "newly_required_parameter",
            None,
            "optional_to_required",
            None,
            None,
            '{"in":"query","name":"q"}',
        ),
        ("review_required", "parameter_changed", None, None, None, '{"in":"query","name":"q"}'),
    )
    # header names compare case-insensitively inside the digests: no spurious parameter_changed
    path_level = {
        "/h": {"parameters": [{"name": "x-tenant", "in": "header", "schema": {"type": "string"}}]}
    }
    path_level["/h"]["get"] = op()
    mutated = copy.deepcopy(path_level)
    mutated["/h"]["get"]["parameters"] = [
        {"name": "X-Tenant", "in": "header", "required": True, "schema": {"type": "string"}}
    ]
    cased = compare(enc(doc_of(path_level)), enc(doc_of(mutated)))
    assert shapes(cased, "GET", "/h") == [
        (
            "newly_required_parameter",
            None,
            "optional_to_required",
            None,
            None,
            '{"in":"header","name":"x-tenant"}',
        )
    ]
    # a sibling on a Reference Object is folded into the use-site digest
    refd = doc_of(
        {"/s": {"get": op(parameters=[{"$ref": "#/components/parameters/P"}])}},
        components={"parameters": {"P": query("p")}},
    )
    plain = compare(enc(refd), enc(refd))
    assert plain["findings"] == []
    sibling = copy.deepcopy(refd)
    sibling["paths"]["/s"]["get"]["parameters"][0]["required"] = True
    assert shapes(compare(enc(refd), enc(sibling)), "GET", "/s")[0][:2] == (
        "review_required",
        "parameter_changed",
    )
    described = copy.deepcopy(refd)
    described["paths"]["/s"]["get"]["parameters"][0]["description"] = "words"
    described["paths"]["/s"]["get"]["parameters"][0]["summary"] = "words"
    described["paths"]["/s"]["get"]["parameters"][0]["x-extra"] = 1
    assert compare(enc(refd), enc(described))["findings"] == []


# ---- AC-S29-005 ------------------------------------------------------------------------------

UNRESOLVED_CASES = {
    "/remote": "remote",
    "/netpath": "remote",
    "/outside": "outside_document",
    "/anchor": "outside_document",
    "/root": "outside_document",
    "/missing": "target_missing",
    "/badescape": "target_missing",
    "/cycle-ab": "cyclic_reference",
    "/cycle-self": "cyclic_reference",
    "/cycle-three": "cyclic_reference",
    "/deep-chain": "reference_depth_exceeded",
}


def _diagnostics_for(snapshot: dict, region: str) -> list[dict]:
    return [d for d in snapshot["diagnostics"] if d["region_pointer"] == region]


def test_openapi_unresolved_reference_is_visible():
    raw = fx("refs.json")
    document = json.loads(raw)
    snapshot = imp(raw)
    assert snapshot["status"] == "incomplete" and snapshot["diagnostics_total"] > 0
    for path, reason in UNRESOLVED_CASES.items():
        method = "get"
        record = record_of(snapshot, "GET", path)
        region = record["provenance"]["pointer"] + "/parameters"
        assert region == "/paths" + o.pointer(path) + "/get/parameters"
        found = _diagnostics_for(snapshot, region)
        assert [(d["kind"], d["reason"]) for d in found] == [("unresolved_reference", reason)], path
        assert (
            record["status"] == "incomplete"
            and record["regions"]["parameters"]["status"] == "incomplete"
        )
        assert record["reasons"] == ["unresolved_reference/" + reason]
        member = found[0]["provenance"]["pointer"]
        assert resolves(document, member) == found[0]["ref"], path
        assert member.endswith("/$ref")
        if reason not in ("cyclic_reference", "reference_depth_exceeded"):
            use = document["paths"][path][method]["parameters"][0]["$ref"]
            assert found[0]["ref"] == use and member == region + "/0/$ref"
        assert all(
            record["regions"][name]["status"] == "complete" for name in o.REGION_NAMES[1:]
        ), path
    cycle = _diagnostics_for(snapshot, "/paths/~1cycle-ab/get/parameters")[0]
    assert cycle["provenance"]["pointer"] == "/components/parameters/CycB/$ref"
    assert cycle["ref"] == "#/components/parameters/CycA"
    own = _diagnostics_for(snapshot, "/paths/~1cycle-self/get/parameters")[0]
    assert own["provenance"]["pointer"] == "/components/parameters/Self/$ref"
    deep = _diagnostics_for(snapshot, "/paths/~1deep-chain/get/parameters")[0]
    assert deep["provenance"]["pointer"] == "/components/parameters/H8/$ref"
    shorter = imp(raw, max_ref_depth=2)
    three = _diagnostics_for(shorter, "/paths/~1cycle-three/get/parameters")
    assert [d["reason"] for d in three] == ["reference_depth_exceeded"]
    assert snapshot["options"]["max_ref_depth"] == 8 and shorter["options"]["max_ref_depth"] == 2

    # other positions
    body = record_of(snapshot, "POST", "/body-remote")
    assert body["regions"]["requestBody"]["status"] == "incomplete"
    assert body["regions"]["parameters"]["status"] == "complete"
    item = next(i for i in snapshot["path_items"] if i["path_template"] == "/remote-item")
    assert item["status"] == "incomplete" and item["reasons"] == ["unresolved_reference/remote"]
    assert not [r for r in snapshot["operations"] if r["path_template"] == "/remote-item"]
    schema = next(
        r for r in snapshot["document_regions"] if r["pointer"] == "/components/schemas/S"
    )
    assert schema["status"] == "incomplete"
    # a referenced component carrying an unresolved reference marks the use-site region
    via_param = record_of(snapshot, "GET", "/closure-param")
    assert via_param["regions"]["parameters"]["status"] == "incomplete"
    via_body = record_of(snapshot, "POST", "/closure-body")
    assert via_body["regions"]["requestBody"]["status"] == "incomplete"
    inside = _diagnostics_for(snapshot, "/paths/~1closure-body/post/requestBody")
    assert [d["provenance"]["pointer"] for d in inside] == [
        "/components/schemas/Hop/properties/far/$ref"
    ]
    # positive controls
    for path in ("/ok", "/ok-percent", "/ok-tilde"):
        record = record_of(snapshot, "GET", path)
        assert record["status"] == "complete" and len(record["parameters"]) == 1, path
    assert record_of(snapshot, "GET", "/ok-tilde")["parameters"][0]["name"] == "slashed"
    assert record_of(snapshot, "GET", "/ok-percent")["parameters"][0]["name"] == "spaced"

    starved = imp(raw, max_ref_expansions=0)
    assert {d["reason"] for d in starved["diagnostics"]} == {"reference_expansion_limit"}
    for path in [*UNRESOLVED_CASES, "/ok", "/ok-percent", "/ok-tilde"]:
        assert record_of(starved, "GET", path)["status"] == "incomplete", path
    capped = imp(raw, max_diagnostics=3)
    assert (
        len(capped["diagnostics"]) == 3
        and capped["diagnostics_total"] == snapshot["diagnostics_total"]
    )
    assert capped["status"] == "incomplete"
    assert [r["status"] for r in capped["operations"]] == [
        r["status"] for r in snapshot["operations"]
    ]

    # diff: unresolved regions are incomplete findings and never a definitive finding
    plain = copy.deepcopy(document)
    for path in plain["paths"]:
        for method, operation in plain["paths"][path].items():
            if method == "get" and "parameters" in operation:
                operation["parameters"] = [query("p", required=True)]
    change = compare(enc(plain), raw)
    for path in UNRESOLVED_CASES:
        assert shapes(change, "GET", path) == [
            ("review_required", "incomplete_region", None, None, "parameters", None)
        ], path
    assert [f for f in change["findings"] if f["kind"] != "review_required"] == []
    same = compare(raw, raw)
    assert shapes(same, "GET", "/remote") == [
        ("review_required", "incomplete_region", None, None, "parameters", None)
    ]
    assert {f["kind"] for f in same["findings"]} == {"review_required"}


# ---- AC-S29-006 ------------------------------------------------------------------------------

PAIRS = [
    ("petstore-before.json", "petstore-after.json"),
    ("identity-before.json", "identity-after.json"),
    ("refs.json", "refs.json"),
]


def _projection(change: dict) -> list:
    return [
        (
            f["kind"],
            f["reason"],
            f["change"],
            ev.canonical(f["identity"]),
            f["region"],
            ev.canonical(f["parameter"]),
            f["before"] and f["before"]["pointer"],
            f["after"] and f["after"]["pointer"],
        )
        for f in change["findings"]
    ]


def test_openapi_reimport_diff_is_idempotent(tmp_path):
    for before, after in PAIRS:
        first = compare(fx(before), fx(after))
        again = compare(fx(before), fx(after))
        assert first["findings"] and od.canonical_bytes(first) == od.canonical_bytes(again)
        runs = [
            cli_run("openapi-diff", "--before", FX / before, "--after", FX / after) for _ in "ab"
        ]
        assert [r.exit_code for r in runs] == [0, 0], runs[0].stderr
        assert runs[0].stdout_bytes == runs[1].stdout_bytes == od.canonical_bytes(first)
        moved = tmp_path / before.replace(".json", "") / "x"
        moved.mkdir(parents=True)
        (moved / "b.json").write_bytes(fx(before))
        (moved / "a.json").write_bytes(fx(after))
        elsewhere = cli_run(
            "openapi-diff", "--before", moved / "b.json", "--after", moved / "a.json"
        )
        assert elsewhere.stdout_bytes == od.canonical_bytes(first)
        rest = {k: v for k, v in first.items() if k != "id"}
        assert (
            first["id"]
            == hashlib.sha256(("atlas-contract-diff.v1:" + ev.canonical(rest)).encode()).hexdigest()
        )
        for finding in first["findings"]:
            body = {k: v for k, v in finding.items() if k != "id"}
            expected = hashlib.sha256(("atlas-contract-finding.v1:" + ev.canonical(body)).encode())
            assert finding["id"] == expected.hexdigest()
    outputs = []
    for seed in ("1", "2"):
        done = subprocess.run(
            [
                sys.executable,
                "-m",
                "atlas_aci",
                "openapi-diff",
                "--before",
                str(FX / "petstore-before.json"),
                "--after",
                str(FX / "petstore-after.json"),
            ],
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            check=False,
        )
        assert done.returncode == 0, done.stderr
        outputs.append(done.stdout)
    assert outputs[0] == outputs[1] == od.canonical_bytes(compare(fx(PAIRS[0][0]), fx(PAIRS[0][1])))

    # decided: identity binds to the bytes, findings bind to the content
    strict = compare(fx("petstore-before.json"), fx("petstore-after.json"))
    spaced = compare(fx("petstore-before.json"), fx("petstore-after-reformatted.json"))
    assert spaced["after"]["artifact_sha256"] != strict["after"]["artifact_sha256"]
    assert spaced["after"]["snapshot_id"] != strict["after"]["snapshot_id"]
    assert spaced["id"] != strict["id"] and od.canonical_bytes(spaced) != od.canonical_bytes(strict)
    assert _projection(spaced) == _projection(strict) and _projection(strict)

    capped = compare(fx("petstore-before.json"), fx("petstore-after.json"), findings=1)
    assert capped["id"] != strict["id"] and capped["options"]["max_findings"] == 1
    assert capped["findings"] == strict["findings"][:1]
    assert capped["findings_total"] == strict["findings_total"] and len(strict["findings"]) > 1
    assert capped["selection"] == {"findings_truncated": True}
    assert capped["summary"] == strict["summary"]
    run = cli_run(
        "openapi-diff", "--before", FX / "petstore-before.json", "--after",
        FX / "petstore-after.json", "--max-findings", 1,
    )  # fmt: skip
    assert run.stdout_bytes == od.canonical_bytes(capped)


# ---- AC-S29-007 ------------------------------------------------------------------------------


def test_openapi_unsupported_change_requires_review():
    before, after = fx("review-before.json"), fx("review-after.json")
    change = compare(before, after)

    def only(method, path, *expected):
        assert shapes(change, method, path) == ([expected] if expected else [])

    only("GET", "/r1", "review_required", "region_changed", None, None, "responses", None)
    only("POST", "/r2", "review_required", "region_changed", None, None, "requestBody", None)
    only("GET", "/r3", "review_required", "region_changed", None, None, "security", None)
    only("GET", "/r4", "review_required", "region_changed", None, None, "deprecated", None)
    only("GET", "/r5", "review_required", "region_changed", None, None, "servers", None)
    only("GET", "/r6", "review_required", "region_changed", None, None, "other", None)
    only("GET", "/r7", "review_required", "region_changed", None, None, "callbacks", None)
    only(
        "GET",
        "/r8",
        "review_required",
        "parameter_changed",
        None,
        None,
        None,
        '{"in":"query","name":"q"}',
    )
    only(
        "GET",
        "/r9",
        "review_required",
        "parameter_removed",
        None,
        None,
        None,
        '{"in":"query","name":"drop"}',
    )
    only("GET", "/r10")
    only("GET", "/r11", "review_required", "region_changed", None, None, "responses", None)
    assert global_shapes(change) == [
        ("review_required", "document_region_changed", "document", ""),
        ("review_required", "document_region_changed", "document", "/components/schemas/User"),
    ]
    assert len(change["findings"]) == 9 + 1 + 2 + 0
    sources = {"before": json.loads(before), "after": json.loads(after)}
    for finding in change["findings"]:
        assert finding["kind"] == "review_required" and finding["action"] == "review"
        assert finding["before"] or finding["after"]
        for side, document in sources.items():
            if finding[side] is not None:
                resolves(document, finding[side]["pointer"])
    hooks = [f for f in change["findings"] if f["identity"] is None and f["before"] is None]
    assert [h["after"]["pointer"] for h in hooks] == ["/webhooks/newPet"]
    # ignored fields: summary, description, tags, externalDocs and info.version give no finding
    assert change["options"]["ignored_fields"] == list(o.IGNORED_FIELDS)
    assert "document:info" in o.IGNORED_FIELDS and "operation:summary" in o.IGNORED_FIELDS
    assert {
        f["region"]
        for f in change["findings"]
        if f["identity"] == {"method": "GET", "path": "/r10"}
    } == set()
    # a description property inside a schema is not an ignored field
    assert shapes(change, "GET", "/r11")[0][4] == "responses"
    # no verdict: closed keys and closed kinds
    assert set(change) == {
        "format", "profile", "differ", "options", "before", "after", "findings",
        "findings_total", "selection", "summary", "review_reasons", "limitations", "id",
    }  # fmt: skip
    for finding in change["findings"]:
        assert set(finding) == {
            "type", "kind", "action", "reason", "change", "blocked_finding", "identity",
            "region", "parameter", "reserved_header", "before", "after", "id",
        }  # fmt: skip
        assert finding["kind"] in od.FINDING_KINDS
    # component edits are not attributed to operations (limitation) but never vanish
    refd = doc_of(
        {"/b": {"post": op(requestBody={"$ref": "#/components/requestBodies/X"})}},
        components={
            "requestBodies": {
                "X": {"content": {"application/json": {"schema": {"type": "string"}}}}
            }
        },
    )
    edited = copy.deepcopy(refd)
    edited["components"]["requestBodies"]["X"]["content"]["application/json"]["schema"]["type"] = (
        "integer"
    )
    edit = compare(enc(refd), enc(edited))
    assert [(f["reason"], f["region"]) for f in edit["findings"]] == [
        ("document_region_changed", "document")
    ]
    assert edit["findings"][0]["before"]["pointer"] == "/components/requestBodies/X"


# ---- closure conditions ----------------------------------------------------------------------


def test_openapi_path_item_without_operations_and_invalid_operations():
    def zero(servers="a", params=None, extra=None, key="/z"):
        item = {"servers": [{"url": "https://" + servers + ".example"}]}
        if params is not None:
            item["parameters"] = params
        if extra is not None:
            item["flag"] = extra
        return enc(doc_of({key: item}))

    assert compare(zero(), zero())["findings"] == []
    for changed in (zero("b"), zero(params=[query("q")]), zero(extra=1)):
        change = compare(zero(), changed)
        assert [(f["reason"], f["region"], f["identity"]) for f in change["findings"]] == [
            ("region_changed", "path_item", None)
        ]
        assert change["findings"][0]["before"]["pointer"] == "/paths/~1z"
    appeared = compare(enc(doc_of()), zero())
    assert [(f["reason"], f["region"]) for f in appeared["findings"]] == [
        ("region_changed", "path_item")
    ]
    assert appeared["findings"][0]["before"] is None and appeared["findings"][0]["after"]
    # the target of a path-item reference counts too
    shared = {"/z": {"$ref": "#/components/pathItems/Z"}}
    one = doc_of(
        shared, components={"pathItems": {"Z": {"servers": [{"url": "https://a.example"}]}}}
    )
    two = copy.deepcopy(one)
    two["components"]["pathItems"]["Z"]["servers"][0]["url"] = "https://b.example"
    moved = compare(enc(one), enc(two))
    assert ("region_changed", "path_item") in {
        (f["reason"], f["region"]) for f in moved["findings"]
    }

    # a non-object operation is kept as an incomplete record that blocks operation_removed
    snapshot = imp(enc(doc_of({"/n": {"get": None, "post": op()}})))
    broken = record_of(snapshot, "GET", "/n")
    assert broken["status"] == "incomplete" and broken["regions"] == {}
    assert broken["reasons"] == ["invalid_operation/not_an_object"]
    assert broken["provenance"]["pointer"] == "/paths/~1n/get"
    before = enc(doc_of({"/n": {"get": op()}}))
    after = enc(doc_of({"/n": {"get": None}}))
    change = compare(before, after)
    assert shapes(change, "GET", "/n") == [
        ("review_required", "incomplete_region", None, None, "operation", None)
    ]
    assert [f for f in change["findings"] if f["kind"] == "operation_removed"] == []


def test_openapi_shared_path_item_keys_are_computed_per_template():
    item = {
        "parameters": [{"name": "x", "in": "path", "required": True, "schema": {"type": "string"}}],
        "get": op(),
    }
    document = doc_of(
        {
            "/a/{x}": {"$ref": "#/components/pathItems/I"},
            "/b/{y}/c/{z}": {"$ref": "#/components/pathItems/I"},
        },
        components={"pathItems": {"I": item}},
    )
    snapshot = imp(enc(document))
    first = record_of(snapshot, "GET", "/a/{}")
    second = record_of(snapshot, "GET", "/b/{}/c/{}")
    assert first["status"] == "complete"
    assert [p["key"] for p in first["parameters"]] == [{"in": "path", "ordinal": 0}]
    assert second["status"] == "incomplete"
    assert second["reasons"] == ["invalid_parameter/path_parameter_not_in_template"]
    assert second["parameters"] == []
    assert first["provenance"]["pointer"] == second["provenance"]["pointer"]
    document["components"]["pathItems"]["I"]["parameters"][0]["name"] = "z"
    flipped = imp(enc(document))
    assert record_of(flipped, "GET", "/a/{}")["status"] == "incomplete"
    other = record_of(flipped, "GET", "/b/{}/c/{}")
    assert other["status"] == "complete"
    assert [p["key"] for p in other["parameters"]] == [{"in": "path", "ordinal": 1}]
    document["components"]["pathItems"]["I"]["parameters"][0]["name"] = "y"
    assert [p["key"] for p in record_of(imp(enc(document)), "GET", "/b/{}/c/{}")["parameters"]] == [
        {"in": "path", "ordinal": 0}
    ]


def test_openapi_extension_keys_and_reference_siblings():
    document = doc_of(
        {
            "x-internal": {"anything": 1},
            "/e": {"get": op()},
            "bad": {"get": op()},
            "/o/{a}{b}/{a}": {"get": op()},
            "/u/{": {"get": op()},
        },
        components={"x-more": 1, "schemas": {"S": {"type": "string"}}},
    )
    snapshot = imp(enc(document))
    templates = {i["path_template"]: i for i in snapshot["path_items"]}
    assert "x-internal" not in templates and set(templates) == {
        "/e",
        "bad",
        "/o/{a}{b}/{a}",
        "/u/{",
    }
    for bad in ("bad", "/o/{a}{b}/{a}", "/u/{"):
        assert templates[bad]["identity_path"] is None
        assert templates[bad]["reasons"] == ["invalid_path_template/malformed_template"]
    assert [r["identity"]["path"] for r in snapshot["operations"]] == ["/e"]
    assert [r["pointer"] for r in snapshot["document_regions"]] == ["/components/schemas/S"]
    assert "paths:x-*" in o.IGNORED_FIELDS and "components:x-*" in o.IGNORED_FIELDS
    # path-item reference siblings
    base = doc_of(components={"pathItems": {"P": {"get": op()}}})
    base["paths"] = {
        "/ok": {"$ref": "#/components/pathItems/P", "summary": "s", "description": "d", "x-a": 1}
    }
    clean = imp(enc(base))
    assert clean["status"] == "complete" and record_of(clean, "GET", "/ok")["status"] == "complete"
    base["paths"]["/ok"]["put"] = op()
    noisy = imp(enc(base))
    item = noisy["path_items"][0]
    assert item["status"] == "incomplete" and item["reasons"] == [
        "invalid_path_item/reference_with_siblings"
    ]
    odd = imp(enc(doc_of({"/s": "text", "/t": []})))
    assert {i["path_template"]: i["reasons"] for i in odd["path_items"]} == {
        "/s": ["invalid_path_item/not_an_object"],
        "/t": ["invalid_path_item/not_an_object"],
    }


def test_openapi_number_handling_and_limit_bounds():
    def digest_of(literal: str) -> str:
        snapshot = imp(
            ('{"openapi":"3.1.0","paths":{"/p":{"get":{"k":' + literal + "}}}}").encode()
        )
        return record_of(snapshot, "GET", "/p")["regions"]["other"]["sha256"]

    assert digest_of("1") != digest_of("1.0")
    assert digest_of("1.0") == digest_of("1.00000000000000001")
    assert digest_of("1" * 4300) != digest_of("1")
    with pytest.raises(o.OpenAPIRejected) as raised:
        digest_of("1" * 4301)
    assert raised.value.reason == "invalid_json"
    assert o.normalize_options({"max_operations": 20000}).max_operations == 20000
    with pytest.raises(o.OpenAPIUsageError):
        o.normalize_options({"max_operations": 20001})
    with pytest.raises(o.OpenAPIUsageError):
        o.normalize_options({"max_operations": True})
    with pytest.raises(o.OpenAPIUsageError):
        o.normalize_options({"unknown": 1})
    with pytest.raises(o.OpenAPIUsageError):
        od.normalize_diff_options({"max_findings": 100001})


# ---- rejections ------------------------------------------------------------------------------


def _with_openapi(value) -> bytes:
    return json.dumps({"openapi": value, "paths": {}}).encode()


REJECTIONS_TABLE = [
    ("bom", b"\xef\xbb\xbf" + fx("rejected-openapi320.json"), "invalid_json"),
    ("duplicate", b'{"openapi":"3.1.0","openapi":"3.1.0"}', "invalid_json"),
    ("nan", b'{"openapi":"3.1.0","x":NaN}', "invalid_json"),
    ("neg-infinity", b'{"openapi":"3.1.0","x":-Infinity}', "invalid_json"),
    ("overflow", b'{"openapi":"3.1.0","x":1e999}', "invalid_json"),
    ("surrogate", b'{"openapi":"3.1.0","x":"\\ud800"}', "invalid_json"),
    ("non-utf8", b'{"openapi":"3.1.0","x":"\xff"}', "invalid_json"),
    ("trailing", b'{"openapi":"3.1.0"} x', "invalid_json"),
    ("yaml", b"openapi: 3.1.0\ninfo:\n  title: t\n", "invalid_json"),
    ("huge-int", b'{"openapi":"3.1.0","x":' + b"9" * 4301 + b"}", "invalid_json"),
    ("empty", b"", "invalid_json"),
    ("array", b"[]", "not_an_object"),
    ("root-ref", b'{"$ref":"#/x","openapi":"3.1.0"}', "root_reference"),
    ("swagger", fx("rejected-swagger20.json"), "unsupported_profile"),
    ("3.0.3", fx("rejected-openapi303.json"), "unsupported_profile"),
    ("3.2.0", fx("rejected-openapi320.json"), "unsupported_profile"),
    ("3.1", _with_openapi("3.1"), "unsupported_profile"),
    ("3.1.0-rc1", _with_openapi("3.1.0-rc1"), "unsupported_profile"),
    ("numeric", _with_openapi(3.1), "unsupported_profile"),
    ("newline", _with_openapi("3.1.0\n"), "unsupported_profile"),
    ("arabic-indic", _with_openapi("3.1.\u0660"), "unsupported_profile"),
    ("missing", b'{"paths":{}}', "unsupported_profile"),
    ("swagger-key", b'{"swagger":"2.0","openapi":"3.1.0"}', "unsupported_profile"),
    ("paths-array", b'{"openapi":"3.1.0","paths":[]}', "invalid_document"),
    ("components-string", b'{"openapi":"3.1.0","components":"x"}', "invalid_document"),
    ("webhooks-array", b'{"openapi":"3.1.0","webhooks":[]}', "invalid_document"),
    ("dialect", b'{"openapi":"3.1.0","jsonSchemaDialect":1}', "invalid_document"),
]


@pytest.mark.parametrize(
    ("label", "raw", "reason"), REJECTIONS_TABLE, ids=[r[0] for r in REJECTIONS_TABLE]
)
def test_openapi_rejections_are_closed_reasons(label, raw, reason, tmp_path):
    with pytest.raises(o.OpenAPIRejected) as raised:
        o.import_document(raw)
    assert raised.value.reason == reason and str(raised.value) == "openapi_rejected: " + reason
    assert reason in o.REJECTIONS
    target = tmp_path / "secret-name" / "doc.json"
    target.parent.mkdir()
    target.write_bytes(raw)
    run = cli_run("openapi-snapshot", target)
    assert run.exit_code == 1 and run.stdout_bytes == b""
    assert run.stderr == f"Error: OpenAPI document rejected: {reason}\n"
    assert "secret-name" not in run.stderr and str(tmp_path) not in run.stderr
    other = cli_run("openapi-diff", "--before", FX / "petstore-before.json", "--after", target)
    assert other.exit_code == 1 and other.stdout_bytes == b""
    assert other.stderr == f"Error: OpenAPI document rejected: {reason} (after)\n"
    side = cli_run("openapi-diff", "--before", target, "--after", FX / "petstore-before.json")
    assert side.stderr == f"Error: OpenAPI document rejected: {reason} (before)\n"


def test_openapi_accepted_versions_and_limit_rejections(tmp_path):
    for version in ("3.1.0", "3.1.1", "3.1.10"):
        assert imp(_with_openapi(version))["artifact"]["openapi"] == version
    petstore = fx("petstore-before.json")
    for flag, value, reason in (
        ("--max-document-bytes", 10, "too_large"),
        ("--max-operations", 1, "limit_exceeded"),
        ("--max-parameters-per-operation", 1, "limit_exceeded"),
    ):
        name = flag[2:].replace("-", "_")
        with pytest.raises(o.OpenAPIRejected) as raised:
            imp(petstore, **{name: value})
        assert raised.value.reason == reason
        run = cli_run("openapi-snapshot", FX / "petstore-before.json", flag, value)
        assert run.exit_code == 1 and run.stdout_bytes == b""
        assert run.stderr == f"Error: OpenAPI document rejected: {reason}\n"
    many = doc_of({f"/p{i}": {"get": op()} for i in range(3)})
    with pytest.raises(o.OpenAPIRejected):
        imp(enc(many), max_operations=2)
    shared = doc_of(
        {f"/p{i}/{{a}}": {"$ref": "#/components/pathItems/I"} for i in range(3)},
        components={"pathItems": {"I": {"get": op(), "put": op()}}},
    )
    with pytest.raises(o.OpenAPIRejected):
        imp(enc(shared), max_operations=5)  # 6 operations from 3 path keys


def _nest(kind: str, depth: int, inner: str = "0") -> bytes:
    if kind == "array":
        return ("[" * depth + inner + "]" * depth).encode()
    if kind == "object":
        return ('{"a":' * depth + inner + "}" * depth).encode()
    opens = "".join("[" if i % 2 else '{"a":' for i in range(depth))
    closes = "".join("]" if i % 2 else "}" for i in reversed(range(depth)))
    return (opens + inner + closes).encode()


def test_openapi_nesting_bound_is_independent_of_the_interpreter():
    assert o.MAX_NESTING_DEPTH == 64
    options = o.normalize_options()
    for kind in ("array", "object", "mixed"):
        accepted = o.parse(_nest(kind, 64), options)
        assert ev.canonical(accepted)
        for depth in (65, 100000):
            with pytest.raises(o.OpenAPIRejected) as raised:
                o.parse(_nest(kind, depth), options)
            assert raised.value.reason == "nesting_too_deep"
    # brackets and escaped quotes inside strings are not counted
    assert o.parse(b'{"a":"' + b"[[[[" * 100 + b'"}', options)
    assert o.parse(b'{"a":"\\"' + b"[" * 100 + b'"}', options)
    assert o.parse(b'["\\"{"]', options) == ['"{']
    petstore = load("petstore-before.json")
    for total, ok in ((64, True), (65, False)):
        for key in ("x-deep", "deep"):
            wrapped = dict(
                petstore, **{key: json.loads("[" * (total - 1) + "0" + "]" * (total - 1))}
            )
            if ok:
                snapshot = imp(enc(wrapped))
                assert snapshot["status"] == "complete"
                assert [
                    r["pointer"] for r in snapshot["document_regions"] if "deep" in r["pointer"]
                ] == (["/deep"] if key == "deep" else [])
            else:
                with pytest.raises(o.OpenAPIRejected) as raised:
                    imp(enc(wrapped))
                assert raised.value.reason == "nesting_too_deep"


def test_openapi_pointer_strictness_and_percent_decoding():
    document = {"k": [10, 20], "a~b": 1, "a/b": 2, "": 3, "\u00e9": 4}
    for text in ("/k/-1", "/k/01", "/k/2", "/a~2b", "/k/\u0661", "/x", "x", "/k/" + "9" * 5000):
        assert o.resolve_pointer(document, text) is o.MISSING, text
    assert o.resolve_pointer(document, "/k/1") == 20 and o.resolve_pointer(document, "") is document
    for key in document:
        assert o.resolve_pointer(document, o.pointer(key)) == document[key]
        assert o.pointer(key) == "/" + key.replace("~", "~0").replace("/", "~1")
    assert [o.percent_decode(t) for t in ("%7B", "%25", "%7e", "%E2%82%AC")] == [
        "{",
        "%",
        "~",
        "\u20ac",
    ]
    assert [o.percent_decode(t) for t in ("%G1", "%C3", "a%2", "%")] == [None, None, None, None]
    assert o.classify("#/a%7Bb") == ("local", "/a{b")
    assert o.classify("#/a%G1") == ("bad", "target_missing")
    assert o.classify("#Anchor") == ("bad", "outside_document") and o.classify("#") == (
        "bad",
        "outside_document",
    )
    assert o.classify("https://x/y") == ("bad", "remote") and o.classify("//h/p") == (
        "bad",
        "remote",
    )
    assert o.classify("a.json#/x") == ("bad", "outside_document")
    assert o.classify("a\u0661:b") == ("bad", "outside_document")
    assert o.classify("x:y") == ("bad", "remote") and o.classify("a.json#/b:c") == (
        "bad",
        "outside_document",
    )
    assert o.classify("") == ("bad", "outside_document")
    for text in ("3.1.0", "3.1.1", "3.1.99"):
        assert o.profile_supported(text)
    for text in ("3.1.0\n", "3.1.\u0660", "3.1", "3.1.0-rc1", " 3.1.0", "3.0.3", 3.1, None):
        assert not o.profile_supported(text)


# ---- CLI -------------------------------------------------------------------------------------


def test_openapi_cli_bytes_exit_codes_and_fixed_texts(tmp_path):
    before, after = FX / "petstore-before.json", FX / "petstore-after.json"
    snapshot = cli_run("openapi-snapshot", before)
    assert snapshot.exit_code == 0 and snapshot.stdout_bytes == o.canonical_bytes(
        imp(fx("petstore-before.json"))
    )
    assert snapshot.stdout_bytes.endswith(b"}\n") and snapshot.stdout_bytes.count(b"\n") == 1
    change = cli_run("openapi-diff", "--before", before, "--after", after)
    expected = od.canonical_bytes(compare(fx("petstore-before.json"), fx("petstore-after.json")))
    assert (
        change.exit_code == 0
        and change.stdout_bytes == expected
        and json.loads(expected)["findings"]
    )
    limits = [
        "--max-document-bytes", "--max-operations", "--max-parameters-per-operation",
        "--max-ref-depth", "--max-ref-expansions", "--max-diagnostics",
    ]  # fmt: skip
    ranges = {
        "--max-document-bytes": (0, 33554433),
        "--max-operations": (0, 20001),
        "--max-parameters-per-operation": (0, 1025),
        "--max-ref-depth": (0, 65),
        "--max-ref-expansions": (-1, 1000001),
        "--max-diagnostics": (-1, 100001),
    }
    for flag in limits:
        for bad in ranges[flag]:
            for args in (
                ("openapi-snapshot", before),
                ("openapi-diff", "--before", before, "--after", after),
            ):
                run = cli_run(*args, flag, bad)
                assert run.exit_code == 2 and run.stdout_bytes == b"", (flag, bad)
                assert f"Error: Invalid OpenAPI limit: {flag[2:].replace('-', '_')}\n" in run.stderr
    for bad in (-1, 100001):
        run = cli_run("openapi-diff", "--before", before, "--after", after, "--max-findings", bad)
        assert run.exit_code == 2 and run.stdout_bytes == b""
        assert "Error: Invalid OpenAPI limit: max_findings\n" in run.stderr
    missing = tmp_path / "private-dir" / "nope.json"
    for target in (missing, tmp_path):
        run = cli_run("openapi-snapshot", target)
        assert run.exit_code == 1 and run.stdout_bytes == b""
        assert run.stderr == "Error: OpenAPI document cannot be read.\n"
        assert "private-dir" not in run.stderr and str(tmp_path) not in run.stderr
        left = cli_run("openapi-diff", "--before", target, "--after", after)
        assert (
            left.exit_code == 1
            and left.stderr == "Error: OpenAPI document cannot be read: before.\n"
        )
        right = cli_run("openapi-diff", "--before", before, "--after", target)
        assert (
            right.exit_code == 1
            and right.stderr == "Error: OpenAPI document cannot be read: after.\n"
        )
        assert right.stdout_bytes == b"" and str(tmp_path) not in right.stderr
    assert "import-openapi" not in cli.commands and "import_openapi" not in cli.commands


# ---- validators ------------------------------------------------------------------------------


def _rehash(change: dict) -> dict:
    for finding in change["findings"]:
        finding["id"] = od._identifier(
            od.FINDING_FORMAT, {k: v for k, v in finding.items() if k != "id"}
        )
    change["id"] = od._identifier(od.FORMAT, {k: v for k, v in change.items() if k != "id"})
    return change


def test_openapi_validators_accept_outputs_and_reject_tampering():
    for name in ("petstore-before.json", "petstore-after.json", "refs.json", "identity-before.json",
                 "review-after.json"):  # fmt: skip
        o.validate_snapshot(imp(fx(name)))
    change = compare(fx("petstore-before.json"), fx("petstore-after.json"))
    od.validate_diff(change)
    tampers = {
        "kind": lambda c: c["findings"][0].update(kind="operation_removed"),
        "reason": lambda c: c["findings"][-1].update(reason="region_changed"),
        "unsorted": lambda c: c["findings"].reverse(),
        "path_param": lambda c: c["findings"][0]["parameter"].update({"in": "path"}),
        "one_sided": lambda c: c["findings"][0].update(before=None, after=None),
        "summary": lambda c: c["summary"].update(operation_removed=5),
        "extra_key": lambda c: c.update(verdict="x"),
        "bound": lambda c: c["options"].update(max_findings=-1),
        "ignored": lambda c: c["options"]["ignored_fields"].pop(),
    }
    for label, tamper in tampers.items():
        broken = copy.deepcopy(change)
        tamper(broken)
        with pytest.raises(od.DiffInvalid):
            od.validate_diff(_rehash(broken))
        assert label
    stale = copy.deepcopy(change)
    stale["id"] = "0" * 64
    with pytest.raises(od.DiffInvalid):
        od.validate_diff(stale)
    forged = copy.deepcopy(change)
    forged["findings"][0]["id"] = "0" * 64
    with pytest.raises(od.DiffInvalid):
        od.validate_diff(forged)
    snapshot = imp(fx("identity-before.json"))
    snapshot_tampers = {
        "id": lambda s: s.update(snapshot_id="0" * 64),
        "status": lambda s: s.update(status="complete"),
        "order": lambda s: s["operations"].reverse(),
        "extra": lambda s: s.update(extra=1),
        "method": lambda s: s["operations"][0]["identity"].update(method="get"),
        "region": lambda s: s["operations"][0]["regions"].pop("other"),
        "provenance": lambda s: s["operations"][0]["provenance"].update(artifact_sha256="0" * 64),
        "limitations": lambda s: s["limitations"].pop(),
        "diag_total": lambda s: s.update(diagnostics_total=0),
    }
    for label, tamper in snapshot_tampers.items():
        broken = copy.deepcopy(snapshot)
        tamper(broken)
        with pytest.raises(o.SnapshotInvalid):
            o.validate_snapshot(broken)
        with pytest.raises(od.ContractDiffRejected) as raised:
            od.diff(broken, snapshot)
        assert raised.value.reason == "invalid_snapshot", label
    other = copy.deepcopy(snapshot)
    other["profile"] = "atlas-openapi31-profile.v2"
    with pytest.raises(od.ContractDiffRejected) as raised:
        od.diff(snapshot, other)
    assert raised.value.reason == "profile_mismatch"
    with pytest.raises(od.ContractDiffRejected):
        od.diff({}, {})


# ---- isolation, hygiene, vocabulary, docs ----------------------------------------------------


def test_openapi_surface_is_unchanged_and_cli_only(tmp_path):
    assert len(READ_ONLY_TOOLS) == 7
    assert not any("openapi" in name or "contract" in name for name in READ_ONLY_TOOLS)
    out = cli_run("tools", "--repo", tmp_path)
    assert out.exit_code == 0
    raw = out.stdout_bytes
    assert len(raw) == BASE_TOOLS_BYTES and hashlib.sha256(raw).hexdigest() == BASE_TOOLS_SHA256
    assert b"openapi" not in raw.lower() and b"atlas-contract" not in raw
    assert "openapi-snapshot" in cli.commands and "openapi-diff" in cli.commands
    assert not [name for name in cli.commands if name.startswith("import-") and "openapi" in name]
    assert admission.MINIMUM_BUDGET == 3436
    assert paging.COLLECTION_MINIMUM_BUDGET == 3448
    assert results.FIXED_ERROR_ENVELOPE_CAP == 797
    assert impact_admission.IMPACT_MINIMUM_BUDGET == 7730


BANNED_IMPORTS = {
    "time", "datetime", "random", "uuid", "os", "sys", "io", "pathlib", "tempfile", "shutil",
    "subprocess", "multiprocessing", "threading", "importlib", "socket", "urllib", "http",
    "sqlite3", "jsonschema", "atlas_aci.server", "atlas_aci.tools", "atlas_aci.codegraph",
    "atlas_aci.query", "atlas_aci.reports", "atlas_aci.architecture", "atlas_aci.counterfactual",
}  # fmt: skip
ALLOWED_IMPORTS = {
    "__future__", "hashlib", "json", "re", "math", "dataclasses", "typing", "collections.abc",
    "atlas_aci.storage.evidence", "atlas_aci.importers.openapi31",
}  # fmt: skip
BANNED_CALLS = {"eval", "exec", "compile", "__import__", "open"}


def violations(source: str) -> list[str]:
    found = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
            names += [(node.module or "") + "." + a.name for a in node.names]
        for name in names:
            if any(name == b or name.startswith(b + ".") for b in BANNED_IMPORTS):
                found.append("import " + name)
        if isinstance(node, ast.Import | ast.ImportFrom):
            module = names[0]
            whole = module in ALLOWED_IMPORTS
            parts = isinstance(node, ast.ImportFrom) and all(
                name in ALLOWED_IMPORTS for name in names[1:]
            )
            if not (whole or parts):
                found.append("unlisted import " + module)
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in BANNED_CALLS:
                found.append("call " + func.id)
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "re"
            ):
                arg = node.args[0] if node.args else None
                text = arg.value if isinstance(arg, ast.Constant) else None
                if isinstance(text, str) and ("\\d" in text or "$" in text):
                    found.append("regex " + text)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call):
                    func = inner.func
                    if isinstance(func, ast.Name) and func.id == node.name:
                        found.append("recursion " + node.name)
                    if (
                        isinstance(func, ast.Attribute)
                        and func.attr == node.name
                        and isinstance(func.value, ast.Name)
                        and func.value.id in ("self", "cls")
                    ):
                        found.append("recursion " + node.name)
    return found


def test_openapi_modules_are_pure_and_ascii():
    modules = sorted((SRC / "importers").glob("*.py")) + sorted((SRC / "contracts").glob("*.py"))
    assert [f"{m.parent.name}/{m.name}" for m in modules] == [
        "importers/__init__.py",
        "importers/openapi31.py",
        "contracts/__init__.py",
        "contracts/openapi_diff.py",
    ]
    for module in modules:
        text = module.read_text()
        assert violations(text) == [], module.name
        assert text.isascii(), module.name
        assert "alter table" not in text.lower() and "nx.compose(" not in text
        assert all(len(line) <= 100 for line in text.splitlines()), module.name
    planted = (
        "import time\nimport os.path\nfrom atlas_aci.tools import search_text\neval('1')\n"
        "compile('1', 'x', 'eval')\nimport pandas\nopen('f')\n__import__('os')\n"
        "import re\nre.compile(r'^[0-9]+$')\nre.compile(r'\\d')\n"
        "def loop(n):\n    return loop(n - 1)\n"
        "class A:\n    def walk(self):\n        return self.walk()\n"
    )
    assert {
        "import time",
        "import os.path",
        "import atlas_aci.tools",
        "call eval",
        "call compile",
        "unlisted import pandas",
        "call open",
        "call __import__",
        "regex ^[0-9]+$",
        "regex \\d",
        "recursion loop",
        "recursion walk",
    } <= set(violations(planted))
    assert "call compile" not in violations("import re\nre.compile('x')\n")
    assert Path(__file__).read_text().isascii()


BANNED_VOCABULARY = {
    "safe", "verified", "proven", "guaranteed", "compliant",
    "compatible", "incompatible", "breaking",
}  # fmt: skip


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^0-9a-zA-Z]+", text.lower()) if t}


def _vocabulary_hits(node, key=None) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        for name, value in node.items():
            found |= _tokens(str(name)) & BANNED_VOCABULARY
            found |= _vocabulary_hits(value, name)
    elif isinstance(node, list):
        for value in node:
            found |= _vocabulary_hits(value, key)
    elif isinstance(node, str) and key in {
        "kind", "action", "reason", "change", "blocked_finding", "region", "status", "format",
        "differ", "profile", "type", "level", "in",
    }:  # fmt: skip
        found |= _tokens(node) & BANNED_VOCABULARY
    return found


def test_openapi_vocabulary_does_not_overclaim():
    outputs = [imp(fx(n)) for n in ("petstore-before.json", "refs.json", "identity-before.json")]
    outputs += [compare(fx(a), fx(b)) for a, b in PAIRS]
    outputs.append(compare(fx("review-before.json"), fx("review-after.json")))
    assert all(_vocabulary_hits(item) == set() for item in outputs)
    assert _vocabulary_hits({"compatible": 1, "kind": "breaking"}) == {"compatible", "breaking"}
    constants = [
        *o.REJECTIONS, *o.REGION_NAMES, *o.LIMITATIONS, *o.IGNORED_FIELDS, *o.REFERENCE_REASONS,
        *od.FINDING_KINDS, *od.REVIEW_REASONS, *od.FINDING_REGIONS, *od.CHANGES, *od.REJECTIONS,
        o.FORMAT, o.PROFILE, od.FORMAT, od.FINDING_FORMAT, od.DIFFER,
        *o.DIAGNOSTIC_REASONS, *(r for rs in o.DIAGNOSTIC_REASONS.values() for r in rs),
    ]  # fmt: skip
    assert constants and all(not _tokens(token) & BANNED_VOCABULARY for token in constants)


def test_openapi_docs_state_the_interpretation_and_name_every_closed_value():
    docs = DOCS.read_text()
    changelog = (ROOT / "CHANGELOG.md").read_text()
    readme = (ROOT / "README.md").read_text()
    row = next(line for line in readme.splitlines() if "docs/importers/openapi.md" in line)
    unreleased = changelog.split("## [", 2)[1]
    entry = unreleased[unreleased.index("OpenAPI 3.1 contract import") :].split("\n- **", 1)[0]
    names = [
        *o.REJECTIONS, *o.REGION_NAMES, *o.LIMITATIONS, *o.IGNORED_FIELDS, *o.REFERENCE_REASONS,
        *od.FINDING_KINDS, *od.REVIEW_REASONS, *od.CHANGES, *o.LIMITS, "max_findings",
        *o.DIAGNOSTIC_REASONS, *(r for rs in o.DIAGNOSTIC_REASONS.values() for r in rs),
        "path_item", "operation", "document", "blocked_finding", "reserved_header",
        o.FORMAT, o.PROFILE, od.FORMAT, od.DIFFER, "openapi-snapshot", "openapi-diff",
        "MAX_NESTING_DEPTH", "contract snapshot", "not a source snapshot", "snapshot_id",
        "OpenAPI document cannot be read.", "OpenAPI document rejected:", "OpenAPI diff rejected:",
        "OpenAPI import failed:", "Invalid OpenAPI limit:", "components/requestBodies/X",
        "1.0", "ASCII digits",
    ]  # fmt: skip
    assert [n for n in names if n not in docs] == []
    for text in (docs, entry, row):
        assert "S19 is not invoked" in text and "change-set shape" in text, text[:40]
        assert "contract snapshot" in text and "not a source snapshot" in text
        assert "follow-up" in text and "artifact digest plus JSON pointer" in text
        assert "through S19" not in text
    for text in (docs, entry):
        assert "binds the artifact" in text and "profile and the options" in text
        assert "contract identity" in text
    assert "through S19" not in "".join(p.read_text() for p in (SRC / "contracts").glob("*.py"))
    assert "document_region_changed" in docs and "not as an operation finding" in docs
    assert "1.0" in docs and "collapse" in docs and "binary floats" in docs
    assert "inherit the enclosing operation's" in docs
    assert "Edits **inside** `components/requestBodies/X`" in docs


def test_openapi_fixture_provenance_lists_every_fixture():
    provenance = json.loads(fx("provenance.json"))
    assert provenance["authored"] == "hand-authored for ACI-S29; no external source"
    listed = provenance["files"]
    present = sorted(p.name for p in FX.iterdir() if p.name != "provenance.json")
    assert present and sorted(listed) == present
    for name, entry in listed.items():
        raw = fx(name)
        assert entry["sha256"] == hashlib.sha256(raw).hexdigest() and entry["bytes"] == len(raw)
        assert entry["purpose"]
    assert fx("petstore-after-reformatted.json") != fx("petstore-after.json")
    assert json.loads(fx("petstore-after-reformatted.json")) == json.loads(
        fx("petstore-after.json")
    )
