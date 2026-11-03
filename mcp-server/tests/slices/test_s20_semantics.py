"""ACI-S20 propagation semantics: seeds, ranking, boundaries, probes, change-set equality."""

from __future__ import annotations

from contextlib import ExitStack

from test_s20_support import (
    CLEAN_V1,
    CLEAN_V2,
    RUBY_V1,
    RUBY_V2,
    V1,
    V2,
    by_name,
    chain_files,
    lease,
    same_path_pair,
    seed_named,
    served_pair,
)

from atlas_aci.query import diff as symbol_diff
from atlas_aci.query import impact as change_impact
from atlas_aci.query.impact import _seed_side

BASE = "def f(a):\n    return a\n\n\ndef g():\n    return f(1)\n"


def names(items):
    return {i["entity"]["name"] for i in items}


def test_format_and_location_changes_never_seed(tmp_path):
    fmt = served_pair(
        tmp_path, {"m.py": BASE}, {"m.py": BASE.replace("    return a", "    # note\n    return a")}
    )
    try:
        summary = fmt.impact()
        assert summary["data"]["change_summary"]["format"] == 1
        assert summary["data"]["seeds"] == [] and summary["data"]["impacted"] == []
        assert summary["data"]["coverage"]["state"] == "complete"
    finally:
        fmt.close()
    moved_root = tmp_path / "moved"
    moved_root.mkdir()
    moved = served_pair(moved_root, {"m.py": BASE}, {"m.py": "\n\n" + BASE})
    try:
        data = moved.impact()["data"]
        assert data["change_summary"]["location"] >= 1
        assert data["seeds"] == [] and data["seeds_total"] == 0 and data["impacted"] == []
    finally:
        moved.close()


def test_added_symbol_seeds_the_after_side(tmp_path):
    first = {"a.py": "def use():\n    return newfn()\n"}
    second = {"a.py": "def use():\n    return newfn()\n\n\ndef newfn():\n    return 1\n"}
    served = served_pair(tmp_path, first, second)
    try:
        data = served.impact()["data"]
        seed = seed_named(data, "newfn")
        assert seed["category"] == "added" and seed["snapshot_side"] == "after"
        # The previously unresolved call now resolves: its caller is potentially affected.
        item = by_name(data["impacted"], "use")
        assert item["change_category"] == "added" and item["snapshot_side"] == "after"
        assert item["rank"]["category_weight"] == 4
    finally:
        served.close()


def test_extraction_failure_is_a_one_sided_seed_and_partial_coverage(tmp_path):
    good = BASE + "\n\ndef caller():\n    return f(2)\n"
    for first, second, side in (
        ({"a.py": good}, {"a.py": "def f(:\n"}, "before"),
        ({"a.py": "def f(:\n"}, {"a.py": good}, "after"),
    ):
        root = tmp_path / side
        root.mkdir()
        served = served_pair(root, first, second)
        try:
            data = served.impact()["data"]
            assert data["change_summary"]["unknown"] >= 1
            seeds = [s for s in data["seeds"] if s["category"] == "unknown"]
            assert seeds and {s["snapshot_side"] for s in seeds} == {side}
            assert data["coverage"]["reasons"]["partial_extraction"] is True
            assert data["coverage"]["state"] == "incomplete"
            if side == "before":  # the surviving evidence is the before graph
                assert "caller" in names(data["impacted"])
                assert {i["snapshot_side"] for i in data["impacted"]} == {"before"}
        finally:
            served.close()


def _record(category, before, after, entity_id="e1", path="a.py"):
    return {
        "category": category,
        "before": before,
        "after": after,
        "entity_id": entity_id,
        "path": path,
    }


def test_seed_side_table_including_ambiguous_definition_records():
    present = {"a.py": {"in-after": {}}}
    cases = [
        (_record("removed", {}, None), "before"),
        (_record("added", None, {}), "after"),
        (_record("signature", {}, {}), "after"),
        (_record("body", {}, {}), "after"),
        (_record("format", {}, {}), None),
        (_record("location", {}, {}), None),
        (_record("unknown", {}, {}), "after"),  # both refs: cannot prove no change
        (_record("unknown", {}, None), "before"),  # extraction_failed_after
        (_record("unknown", None, {}), "after"),  # extraction_failed_before
        (_record("unknown", None, None, "in-after"), "after"),  # ambiguous definition
        (_record("unknown", None, None, "only-before"), "before"),
    ]
    for record, expected in cases:
        assert _seed_side(record, present) == expected, record


def test_seed_limit_and_shared_visited_cap(tmp_path):
    served = served_pair(tmp_path, V1, V2)
    try:
        limited = served.impact(max_seeds=1)["data"]
        assert len(limited["seeds"]) == 1 and limited["seeds_total"] == 3
        assert limited["coverage"]["reasons"]["seed_limit"] is True
        assert limited["coverage"]["state"] == "incomplete"
        # More seeds than the visited cap: the allowance is shared, never reset per seed.
        capped = served.impact(max_visited_nodes=1)["data"]
        assert len(capped["seeds"]) == 3
        assert capped["work"]["visited_nodes"] <= 1
        expanded = [s for s in capped["seeds"] if s["expansion"] == "expanded"]
        skipped = [s for s in capped["seeds"] if s["expansion"] == "not_expanded"]
        assert len(expanded) == 1 and len(skipped) == 2
        assert all(s["frontier"] is None for s in skipped)
        assert capped["coverage"]["reasons"]["work_limit"] is True
        roomy = served.impact(max_visited_nodes=6)["data"]
        assert roomy["work"]["visited_nodes"] <= 6
        assert sum(s["expansion"] == "expanded" for s in roomy["seeds"]) >= 2
    finally:
        served.close()


def test_best_explanation_across_seeds_and_sides(tmp_path):
    first = {
        "a.py": (
            "def f(x):\n    return x\n\n\ndef g(x):\n    return x\n\n\n"
            "def old():\n    return 0\n\n\n"
            "def mid():\n    return f(1)\n\n\ndef top():\n    mid()\n    return g(1)\n\n\n"
            "def caller():\n    f(1)\n    g(1)\n    return old()\n"
        )
    }
    second = {
        "a.py": first["a.py"]
        .replace("def f(x):", "def f(x, y):")
        .replace("def g(x):\n    return x", "def g(x):\n    return x + 1")
        .replace("def old():\n    return 0\n\n\n", "")
    }
    served = served_pair(tmp_path, first, second)
    try:
        data = served.impact()["data"]
        categories = {s["name"]: s["category"] for s in data["seeds"]}
        assert categories == {"f": "signature", "g": "body", "old": "removed"}
        items = data["impacted"]
        assert items
        # `top`: distance 1 from the body seed beats distance 2 from the signature seed.
        top = by_name(items, "top")
        assert (top["distance"], top["change_category"]) == (1, "body")
        assert top["reached_from_seeds"] == 2
        # `mid`: only the signature seed reaches it.
        mid = by_name(items, "mid")
        assert (mid["distance"], mid["change_category"], mid["reached_from_seeds"]) == (
            1,
            "signature",
            1,
        )
        # `caller`: three seeds at distance 1 across both sides; removed(before) wins on weight.
        caller = by_name(items, "caller")
        assert caller["distance"] == 1 and caller["reached_from_seeds"] == 3
        assert (caller["change_category"], caller["snapshot_side"]) == ("removed", "before")
        keys = [
            (i["distance"], i["rank"]["category_weight"], i["rank"]["side_order"]) for i in items
        ]
        assert keys == sorted(keys)  # ranked order
    finally:
        served.close()


def test_also_changed_marks_an_impacted_seed(tmp_path):
    first = {"m.py": BASE}
    second = {"m.py": BASE.replace("(a):", "(a, b):").replace("return f(1)", "return f(1) + 1")}
    served = served_pair(tmp_path, first, second)
    try:
        data = served.impact()["data"]
        assert {s["name"]: s["category"] for s in data["seeds"]} == {
            "f": "signature",
            "g": "body",
        }
        g = by_name(data["impacted"], "g")
        assert g["also_changed"] is True and g["change_category"] == "signature"
        assert by_name([i for i in data["impacted"]], "g")["distance"] == 1
        assert "f" not in names(data["impacted"])
    finally:
        served.close()


def test_ambiguous_references_are_reported_as_incomplete_coverage(tmp_path):
    served = served_pair(tmp_path, RUBY_V1, RUBY_V2)
    try:
        data = served.impact()["data"]
        seed = seed_named(data, "dup_name")
        assert seed["category"] == "signature" and seed["snapshot_side"] == "after"
        boundaries = [b for b in data["coverage"]["boundaries"] if b["name"] == "dup_name"]
        assert boundaries
        boundary = boundaries[0]
        assert boundary["ambiguous_reference_count"] >= 1
        assert boundary["unresolved_reference_count"] == 0
        assert boundary["reference_coverage"] == "known"
        assert boundary["reasons"] == {"ambiguous_reference": True}
        assert data["coverage"]["state"] == "incomplete"
        assert data["coverage"]["reasons"]["ambiguous_reference"] is True
        # The ambiguous edge is never traversed as if it were resolved.
        assert "call" not in {
            h["assertion"]["relation"] for i in data["impacted"] for h in i["path"]["hops"]
        }
    finally:
        served.close()


def test_import_relation_is_opt_in_and_rationale_is_not_a_relation(tmp_path):
    served = served_pair(tmp_path, V1, V2)
    try:
        default = served.impact()["data"]
        assert "import" not in default["propagation"]["relations"]
        assert not [i for i in default["impacted"] if i["entity"]["kind"] == "file"]
        opted = served.impact(relations=["call", "import"])["data"]
        files = [i for i in opted["impacted"] if i["entity"]["kind"] == "file"]
        assert files
        assert all(i["path"]["hops"][-1]["assertion"]["relation"] == "import" for i in files)
        assert "pkg/shapes.py" in {i["entity"]["path"] for i in files}
        assert served.call({"relations": ["rationale_for"]}).isError
        assert (
            tuple(
                sorted(
                    {"call", "construct", "import", "superclass", "include", "extend", "prepend"}
                )
            )
            == change_impact.lim.RELATIONS
        )
    finally:
        served.close()


def test_probe_allowance_deadline_and_vm_limits_never_report_partial_counts(tmp_path):
    served = served_pair(tmp_path, V1, V2)
    try:
        full = served.impact()["data"]
        reference = {b["entity_id"]: b for b in full["coverage"]["boundaries"]}
        assert reference
        none = served.impact(max_boundary_probes=0)["data"]
        assert none["work"]["boundary_probes"] == 0 and none["coverage"]["boundaries"] == []
        assert none["coverage"]["reasons"]["boundary_probe_limit"] is True
        assert none["work"]["boundary_probe_stop"] == "probe_limit"
        one = served.impact(max_boundary_probes=1)["data"]
        assert one["work"]["boundary_probes"] == 1 == one["work"]["boundary_probes_completed"]
        assert one["coverage"]["reasons"]["boundary_probe_limit"] is True
        dead = served.impact(deadline_ms=0)["data"]
        assert all(s["expansion"] == "not_expanded" for s in dead["seeds"])
        assert dead["coverage"]["reasons"]["work_limit"] is True
        assert dead["coverage"]["reasons"]["boundary_probe_limit"] is True
        assert dead["work"]["boundary_probe_stop"] == "impact_deadline"
        stopped = set()
        for vm in range(0, 2400, 25):
            data = served.impact(max_vm_operations=vm)["data"]
            work = data["work"]
            assert work["vm_operations_reserved"] <= vm
            assert work["boundary_probes_completed"] <= work["boundary_probes"]
            if work["boundary_probe_stop"] is not None:
                stopped.add(work["boundary_probe_stop"])
                assert data["coverage"]["reasons"]["boundary_probe_limit"] is True
            for boundary in data["coverage"]["boundaries"]:
                assert boundary == reference[boundary["entity_id"]]  # never a partial count
        assert any(reason.startswith("impact_") for reason in stopped)
    finally:
        served.close()


def test_change_set_equals_the_snapshot_diff_for_every_fixture_pair(tmp_path):
    chain_first, chain_second = chain_files(4, fan_a=2, fan_b=1)
    good = BASE + "\n\ndef caller():\n    return f(2)\n"
    pairs = {
        "package": (V1, V2),
        "ruby": (RUBY_V1, RUBY_V2),
        "clean": (CLEAN_V1, CLEAN_V2),
        "chain": (chain_first, chain_second),
        "failed": ({"a.py": good}, {"a.py": "def f(:\n"}),
        "identical": (V1, V1),
    }
    for label, (first, second) in pairs.items():
        root = tmp_path / label
        root.mkdir()
        repo, before, after = same_path_pair(root, first, second)
        expected = symbol_diff.compare(before, after)
        # (1) the CLI path: both leases opened by the library.
        from_cli = change_impact.run(before, after)["data"]
        # (2) the MCP path: the AFTER lease is the one dispatch acquired.
        from test_s20_support import Served

        served = Served(root, repo, before, after)
        try:
            from_mcp = served.impact()["data"]
        finally:
            served.close()
        assert from_cli["change_set_id"] == from_mcp["change_set_id"] == expected["id"], label
        assert from_mcp["change_summary"] == expected["summary"]
        assert from_mcp["before"] == expected["before"] and from_mcp["after"] == expected["after"]
        # The seed set is exactly the eligible subset of the diff's records.
        eligible = {
            r["entity_id"]
            for r in expected["changes"]
            if r["category"] not in {"format", "location"}
        }
        assert {s["changed_entity_id"] for s in from_mcp["seeds"]} <= eligible
        assert from_mcp["seeds_total"] == len(eligible)


def test_seed_entities_exist_on_their_tagged_side(tmp_path):
    served = served_pair(tmp_path, V1, V2)
    try:
        data = served.impact()["data"]
        assert data["seeds"]
        with ExitStack() as stack:
            readers = {"before": lease(stack, served.before), "after": lease(stack, served.after)}
            for seed in data["seeds"]:
                reader = readers[seed["snapshot_side"]]
                row = reader.db.execute(
                    "SELECT 1 FROM entities WHERE id=?", (seed["changed_entity_id"],)
                ).fetchone()
                assert row is not None
                assert seed["snapshot_id"] == reader.snapshot_id
    finally:
        served.close()


def test_distance_equals_frontier_depth_and_the_seed_is_never_its_own_child(tmp_path):
    first, second = chain_files(3)
    served = served_pair(tmp_path, first, second)
    try:
        data = served.impact(max_depth=32)["data"]
        (seed,) = data["seeds"]
        assert seed["name"] == "c0" and seed["frontier"]["outcome"] == "complete"
        items = data["impacted"]
        assert [i["entity"]["name"] for i in items] == ["c1", "c2", "c3"]
        assert [i["distance"] for i in items] == [1, 2, 3]
        assert max(i["distance"] for i in items) == seed["frontier"]["depth"] == 3
        assert all(i["distance"] <= data["effective_limits"]["max_depth"] for i in items)
        assert all(i["entity"]["id"] != i["changed_entity_id"] for i in items)
        assert all(len(i["path"]["hops"]) == i["distance"] for i in items)
        shallow = served.impact(max_depth=2)["data"]
        assert [i["distance"] for i in shallow["impacted"]] == [1, 2]
        assert shallow["seeds"][0]["frontier"]["stop_reason"] == "depth_limit"
        assert shallow["coverage"]["reasons"]["work_limit"] is True
    finally:
        served.close()
