"""ACI-S20 acceptance criteria: potential change impact, real builds, real MCP dispatch."""

from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
from contextlib import ExitStack

from click.testing import CliRunner
from test_s20_support import (
    V1,
    V2,
    Served,
    build,
    by_name,
    chain_files,
    exists,
    lease,
    seed_named,
    served_pair,
    wipe,
    write_tree,
)

from atlas_aci.__main__ import cli
from atlas_aci.protocol import graph
from atlas_aci.protocol.impact import IMPACT_DATA, validate_impact
from atlas_aci.protocol.results import result_bytes
from atlas_aci.query.impact_admission import IMPACT_MINIMUM_BUDGET
from atlas_aci.storage import evidence as ev


def cli_args(served):
    return [
        "--repo",
        str(served.repo),
        "--before-graph-root",
        str(served.before.graph_root),
        "--after-graph-root",
        str(served.after.graph_root),
    ]


def test_reverse_caller_impact(tmp_path):
    served = served_pair(tmp_path, V1, V2)
    try:
        data = served.impact()["data"]
        impacted = data["impacted"]
        assert impacted
        area = seed_named(data, "area")
        assert area["category"] == "signature" and area["snapshot_side"] == "after"
        size = by_name(impacted, "size")
        assert size["status"] == "potentially_affected"
        assert size["changed_entity_id"] == area["changed_entity_id"]
        assert size["change_category"] == "signature"
        assert size["snapshot_side"] == "after" and size["distance"] == 1
        assert size["entity"]["path"] == "pkg/shapes.py"  # cross-file, file itself unchanged
        # The same-file resolved caller is also reported.
        doubled = by_name(impacted, "doubled")
        assert doubled["changed_entity_id"] == area["changed_entity_id"]
        assert doubled["entity"]["path"] == "pkg/geometry.py"
        # Negative controls: an unchanged callee's caller and an unchanged sibling are absent.
        names = {i["entity"]["name"] for i in impacted}
        assert "unrelated" not in names and "perimeter" not in names
        # CLI and MCP share one library function: byte-identical canonical envelope.
        run = CliRunner().invoke(cli, ["impact", *cli_args(served)])
        assert run.exit_code == 0, run.output
        envelope = served.call({}).structuredContent
        assert json.loads(run.stdout_bytes) == envelope
        assert run.stdout_bytes == ev.canonical(envelope).encode() + b"\n"
    finally:
        served.close()


def test_impact_reason_path(tmp_path):
    served = served_pair(tmp_path, V1, V2)
    try:
        data = served.impact()["data"]
        impacted = data["impacted"]
        assert impacted
        seeds = {s["changed_entity_id"]: s for s in data["seeds"]}
        assert seeds
        sides = {i["snapshot_side"] for i in impacted}
        assert sides == {"before", "after"}  # both snapshots' evidence paths are exercised
        allowed = {
            "relations": sorted(data["propagation"]["relations"]),
            "confidence": sorted(data["propagation"]["confidence"]),
            "direction": "incoming",
        }
        with ExitStack() as stack:
            readers = {
                "before": lease(stack, served.before),
                "after": lease(stack, served.after),
            }
            for item in impacted:
                path, reader = item["path"], readers[item["snapshot_side"]]
                nodes, hops = path["nodes"], path["hops"]
                assert nodes and hops
                assert nodes[0]["id"] == item["changed_entity_id"]
                assert item["changed_entity_id"] in seeds
                assert seeds[item["changed_entity_id"]]["snapshot_side"] == item["snapshot_side"]
                assert nodes[-1]["id"] == item["entity"]["id"]
                assert len(hops) == item["distance"] == len(nodes) - 1
                identities = {n["id"]: n for n in nodes}
                for position, hop in enumerate(hops):
                    assert hop["traversal_direction"] == "incoming"
                    assert hop["assertion"]["target_entity_id"] == nodes[position]["id"]
                    assert hop["assertion"]["source_entity_id"] == nodes[position + 1]["id"]
                    graph.validate_hop(hop, identities, allowed)
                    assert exists(reader, "assertions", hop["assertion"]["id"])
                    assert exists(reader, "evidence", hop["evidence"]["id"])
                    assert exists(reader, "occurrences", hop["occurrence"]["id"])
                assert item["snapshot_id"] == reader.snapshot_id
                assert item["generation_id"] == reader.generation_id
                assert item["distance"] <= data["effective_limits"]["max_depth"]
    finally:
        served.close()


def _words(value):
    """Every key and string value, lower-cased."""
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key).lower()
            yield from _words(child)
    elif isinstance(value, list):
        for child in value:
            yield from _words(child)
    elif isinstance(value, str):
        yield value.lower()


def test_no_breakage_claim(tmp_path):
    served = served_pair(tmp_path, V1, V2)
    try:
        payload = served.impact()
        data = payload["data"]
        assert data["status"] == "potential_impact"
        assert data["basis"] == "static_reachability"
        assert data["runtime_observation"] == "not_performed"
        assert data["impacted"]
        assert all(i["basis"] == "static_reachability" for i in data["impacted"])
        assert all(i["status"] == "potentially_affected" for i in data["impacted"])
        # The published schema pins these constants: any other value is rejected.
        assert graph.STRUCTURED_VALIDATOR.is_valid(payload)
        mutations = [
            lambda p: p["data"].__setitem__("status", "broken"),
            lambda p: p["data"].__setitem__("basis", "runtime_trace"),
            lambda p: p["data"].__setitem__("runtime_observation", "performed"),
            lambda p: p["data"]["impacted"][0].__setitem__("basis", "confirmed"),
            lambda p: p["data"]["impacted"][0].__setitem__("status", "breakage"),
        ]
        for mutate in mutations:
            broken = copy.deepcopy(payload)
            mutate(broken)
            assert not graph.STRUCTURED_VALIDATOR.is_valid(broken)
        # Independent validator agrees on an otherwise structurally valid mutation.
        validate_impact(payload, "impact", served.config.max_bound_field_elements)
        # No key or string value in the schema or the payload claims breakage.
        pattern = re.compile(r"break|broken|confirm")
        for source in (IMPACT_DATA, data):
            words = list(_words(source))
            assert words
            assert not [w for w in words if pattern.search(w)]
    finally:
        served.close()


def test_unresolved_boundary(tmp_path):
    served = served_pair(tmp_path, V1, V2)
    try:
        payload = served.impact()
        data = payload["data"]
        size = by_name(data["impacted"], "size")
        assert data["coverage"]["state"] == "incomplete"
        assert data["coverage"]["reasons"]["unresolved_reference"] is True
        boundaries = [
            b for b in data["coverage"]["boundaries"] if b["entity_id"] == size["entity"]["id"]
        ]
        assert len(boundaries) == 1
        boundary = boundaries[0]
        assert boundary["snapshot_side"] == "after"
        assert boundary["unresolved_reference_count"] >= 1
        assert boundary["basis"] == "same_name_reference"
        # Expansion continued: the other impacted items are still returned.
        assert {"doubled", "main"} <= {i["entity"]["name"] for i in data["impacted"]}

        # Under a byte budget that drops the boundary list the state is still reported.
        def returned(budget):
            result = served.call({"max_bytes": budget})
            if result.isError:
                return None
            return result.structuredContent["data"]

        low, high = IMPACT_MINIMUM_BUDGET, 200_000
        assert returned(high)["coverage"]["boundaries_returned"] == 1
        while low < high:
            middle = (low + high) // 2
            found = returned(middle)
            if found is not None and found["coverage"]["boundaries_returned"] == 1:
                high = middle
            else:
                low = middle + 1
        squeezed = returned(low - 1)
        assert squeezed is not None
        assert squeezed["coverage"]["boundaries"] == []
        assert squeezed["coverage"]["boundaries_total"] == 1
        assert squeezed["coverage"]["state"] == "incomplete"
        assert squeezed["coverage"]["reasons"]["unresolved_reference"] is True
        assert squeezed["selection"] == {"truncated": True, "reason": "byte_limit"}
        assert squeezed["impacted_returned"] == squeezed["impacted_total"] > 0
    finally:
        served.close()
    # Negative control: no unresolved/ambiguous reference and no limit hit -> complete.
    clean = tmp_path / "clean"
    clean.mkdir()
    from test_s20_support import CLEAN_V1, CLEAN_V2

    control = served_pair(clean, CLEAN_V1, CLEAN_V2)
    try:
        found = control.impact()["data"]
        assert found["impacted"]
        assert found["coverage"]["state"] == "complete"
        assert found["coverage"]["reasons"] == {}
        assert found["coverage"]["boundaries"] == []
    finally:
        control.close()


def test_impact_budget(tmp_path):
    first, second = chain_files(30)
    served = served_pair(tmp_path, first, second)
    try:
        data = served.impact(max_visited_nodes=5, max_examined_edges=8, max_depth=32)["data"]
        limits = data["effective_limits"]
        work = data["work"]
        assert data["impacted"]
        assert work["visited_nodes"] <= 5 and work["examined_edges"] <= 8
        assert work["vm_operations_reserved"] <= limits["max_vm_operations"]
        assert work["boundary_probes"] <= limits["max_boundary_probes"]
        stops = {s["frontier"]["stop_reason"] for s in data["seeds"] if s["frontier"]}
        assert stops
        assert stops <= {"visited_node_limit", "examined_edge_limit"} or all(
            s.startswith("impact_") for s in stops
        )
        assert data["coverage"]["state"] == "incomplete"
        assert data["coverage"]["reasons"]["work_limit"] is True
        # Sweep: every limit combination keeps every counter within its limit.
        for edges in range(0, 12):
            for visited in (1, 2, 3, 5, 8):
                swept = served.impact(max_examined_edges=edges, max_visited_nodes=visited)["data"]
                assert swept["work"]["examined_edges"] <= edges
                assert swept["work"]["visited_nodes"] <= visited
        for probes in (0, 1, 2):
            swept = served.impact(max_boundary_probes=probes)["data"]
            assert swept["work"]["boundary_probes"] <= probes
        for vm in (0, 1, 50, 500, 5000):
            swept = served.impact(max_vm_operations=vm)["data"]
            assert swept["work"]["vm_operations_reserved"] <= vm
        # The diff phase is bounded by the operator limits before any parse.
        diff_work = data["diff_work"]
        assert diff_work["changed_files"] <= limits["max_changed_files"]
        assert diff_work["before_entities"] <= limits["max_diff_entities"]
    finally:
        served.close()
    # Two changed seeds with 10 callers each share ONE allowance (no per-seed reset).
    fan_first, fan_second = chain_files(0, fan_a=10, fan_b=10)
    fan_root = tmp_path / "fan"
    fan_root.mkdir()
    fan = served_pair(fan_root, fan_first, fan_second)
    try:
        unbounded = fan.impact()["data"]
        assert unbounded["work"]["examined_edges"] >= 20
        limited = fan.impact(max_examined_edges=12)["data"]
        assert limited["work"]["examined_edges"] <= 12
        assert limited["impacted_total"] < unbounded["impacted_total"]
        assert limited["coverage"]["reasons"]["work_limit"] is True
    finally:
        fan.close()
    # Operator diff admission: a zero changed-file allowance yields no result at all.
    strict_root = tmp_path / "strict"
    strict_root.mkdir()
    strict = served_pair(strict_root, V1, V2, impact_max_changed_files=0)
    try:
        result = strict.call({})
        assert result.isError
        payload = result.structuredContent
        assert payload["data"] is None
        assert payload["error"]["code"] == "IMPACT_DIFF_TOO_LARGE"
        assert "changed_file_limit" in payload["error"]["message"]
        assert str(tmp_path) not in result.content[0].text
    finally:
        strict.close()


def test_impact_replay(tmp_path):
    served = served_pair(tmp_path, V1, V2)
    try:
        once = served.call({})
        again = served.call({})
        assert not once.isError
        assert result_bytes(once) == result_bytes(again)
        envelope = once.structuredContent
        data = envelope["data"]
        commitments = [s["frontier"]["commitment"] for s in data["seeds"] if s["frontier"]]
        assert commitments  # generous deadline: every frontier is the same deterministic one
        assert all(s["frontier"]["stop_reason"] is None for s in data["seeds"])
        process = subprocess.run(
            [sys.executable, "-m", "atlas_aci", "impact", *cli_args(served)], capture_output=True
        )
        assert process.returncode == 0, process.stderr
        assert json.loads(process.stdout) == envelope
        assert json.loads(process.stdout)["data"]["impact_id"] == data["impact_id"]
        # Different configuration -> different content address.
        narrower = served.impact(relations=["call"])["data"]
        shallower = served.impact(max_depth=1)["data"]
        assert narrower["impact_id"] != data["impact_id"]
        assert shallower["impact_id"] != data["impact_id"]
        assert narrower["impact_id"] != shallower["impact_id"]
    finally:
        served.close()
    # Rebuild both sides at the same repo path into fresh roots: same ids, same result.
    repo, before, after = served.repo, served.before, served.after
    wipe(before.graph_root, after.graph_root)
    write_tree(repo, V1)
    build(repo, tmp_path / "fresh-before")
    write_tree(repo, V2)
    build(repo, tmp_path / "fresh-after")
    from atlas_aci.query.diff import Side

    rebuilt = Served(
        tmp_path,
        repo,
        Side(repo, tmp_path / "fresh-before"),
        Side(repo, tmp_path / "fresh-after"),
    )
    try:
        again = rebuilt.impact()
        assert again["data"]["before"] == data["before"]
        assert again["data"]["after"] == data["after"]
        assert again["snapshot_id"] == envelope["snapshot_id"]
        assert again["generation_id"] == envelope["generation_id"]
        assert ev.canonical(again) == ev.canonical(envelope)
    finally:
        rebuilt.close()


def test_removed_symbol_impact(tmp_path):
    served = served_pair(tmp_path, V1, V2)
    try:
        data = served.impact()["data"]
        legacy = seed_named(data, "legacy")
        assert legacy["category"] == "removed" and legacy["snapshot_side"] == "before"
        main = by_name(data["impacted"], "main")
        assert main["snapshot_side"] == "before"
        assert main["changed_entity_id"] == legacy["changed_entity_id"]
        assert main["path"]["nodes"][0]["id"] == legacy["changed_entity_id"]
        assert main["snapshot_id"] == data["before"]["snapshot_id"]
        assert data["before"]["snapshot_id"] != data["after"]["snapshot_id"]
        hop = main["path"]["hops"][0]
        with ExitStack() as stack:
            before = lease(stack, served.before)
            after = lease(stack, served.after)
            assert main["snapshot_id"] == before.snapshot_id != after.snapshot_id
            assert exists(before, "assertions", hop["assertion"]["id"])
            assert not exists(after, "assertions", hop["assertion"]["id"])
            target = legacy["changed_entity_id"]
            resolved = after.db.execute(
                "SELECT COUNT(*) FROM assertions WHERE json_extract(payload,'$.target_entity_id')=?"
                " AND json_extract(payload,'$.resolution') IN ('EXTRACTED','INFERRED')",
                (target,),
            ).fetchone()[0]
            assert resolved == 0
            # After side: main -> legacy is UNRESOLVED with a null target.
            unresolved = [
                json.loads(row[0])
                for row in after.db.execute(
                    "SELECT payload FROM assertions "
                    "WHERE json_extract(payload,'$.source_entity_id')=?",
                    (main["entity"]["id"],),
                )
            ]
            assert unresolved
            call = [a for a in unresolved if a["raw_target"] == "legacy"]
            assert len(call) == 1
            assert call[0]["resolution"] == "UNRESOLVED" and call[0]["target_entity_id"] is None
            # Navigating the removed entity on the after lease finds nothing.
            import asyncio

            from atlas_aci.query.traversal import navigate

            nav = asyncio.run(
                navigate(
                    after, seed={"entity_id": target}, direction="incoming", relations=["call"]
                )
            )
            assert nav["outcome"] == "no_evidence"
    finally:
        served.close()
