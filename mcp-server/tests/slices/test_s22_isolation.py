"""ACI-S22 invariance gates: no MCP surface, no execution path, no overclaiming vocabulary."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from contextlib import ExitStack
from pathlib import Path

from click.testing import CliRunner
from test_s22_support import cov_world, lease

from atlas_aci.__main__ import cli
from atlas_aci.enforcement import READ_ONLY_TOOLS
from atlas_aci.protocol import results
from atlas_aci.query import admission, impact_admission, paging, validation
from atlas_aci.query import impact_limits as lim
from atlas_aci.query import validation_coverage as cov
from atlas_aci.query import validation_limits as vlim
from atlas_aci.query import validation_schema as schema
from atlas_aci.query.diff import Side

BASE_TOOLS_SHA256 = "94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56"
BASE_TOOLS_BYTES = 1631472
QUERY = Path(__file__).parents[2] / "src/atlas_aci/query"
MODULES = sorted(QUERY.glob("validation*.py"))
BANNED_VOCABULARY = {
    "covered",
    "verified",
    "passed",
    "proven",
    "safe",
    "skip",
    "skipped",
    "guaranteed",
}


# ---- G-MCP / G-ENVELOPE ----------------------------------------------------------------


def test_the_tool_surface_is_byte_identical_and_validation_is_cli_only(tmp_path):
    from test_s21_support import workspace

    assert len(READ_ONLY_TOOLS) == 7
    assert not any("validation" in name for name in READ_ONLY_TOOLS)
    out = CliRunner().invoke(
        cli, ["tools", "--repo", str(workspace(tmp_path))], catch_exceptions=False
    )
    assert out.exit_code == 0
    raw = out.stdout.encode()
    assert len(raw) == BASE_TOOLS_BYTES and hashlib.sha256(raw).hexdigest() == BASE_TOOLS_SHA256
    assert "validation" not in out.stdout.lower()
    assert "validation-plan" in cli.commands
    assert {"impact", "diff", "import-coverage"} <= set(cli.commands)
    assert "validation_plan" not in {t["name"] for t in json.loads(out.stdout)}


def test_the_envelope_minimums_are_the_base_values():
    assert admission.MINIMUM_BUDGET == 3436
    assert paging.COLLECTION_MINIMUM_BUDGET == 3448
    assert results.FIXED_ERROR_ENVELOPE_CAP == 797
    assert impact_admission.IMPACT_MINIMUM_BUDGET == 7730


def test_the_published_impact_request_schema_is_untouched():
    from atlas_aci.protocol.graph import GRAPH_INPUT_SCHEMA

    text = json.dumps(GRAPH_INPUT_SCHEMA)
    assert "validation" not in text and "coverage_graph_root" not in text


# ---- D13: no execution path in the planner (AST deny-list) ---------------------------------

BANNED_IMPORTS = {
    "subprocess",
    "multiprocessing",
    "pty",
    "runpy",
    "importlib",
    "ctypes",
    "atlas_aci.tools",
    "atlas_aci.server",
}
BANNED_CALLS = {"eval", "exec", "compile", "__import__"}
BANNED_OS = re.compile(r"^(exec.*|spawn.*|fork.*|system|popen|posix_spawn.*)$")
BANNED_CLOCKS = {"time", "monotonic", "monotonic_ns", "perf_counter", "check", "remaining_ms"}


def violations(source: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""] + [(node.module or "") + "." + a.name for a in node.names]
        else:
            names = []
        for name in names:
            if any(name == b or name.startswith(b + ".") for b in BANNED_IMPORTS):
                found.append("import " + name)
            if name.split(".")[0] in {"time"}:
                found.append("import " + name)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in BANNED_CALLS
        ):
            found.append("call " + node.func.id)
        if isinstance(node, ast.Attribute):
            owner = node.value.id if isinstance(node.value, ast.Name) else None
            if owner in {"os", "posix", "pty"} and BANNED_OS.match(node.attr):
                found.append("attr " + node.attr)
            if node.attr in BANNED_CLOCKS:
                found.append("clock " + node.attr)
    return found


def test_the_planner_modules_have_no_process_or_dynamic_execution_path():
    assert [m.name for m in MODULES] == [
        "validation.py",
        "validation_coverage.py",
        "validation_limits.py",
        "validation_schema.py",
    ]
    for module in MODULES:
        assert violations(module.read_text()) == [], module.name


def test_the_deny_list_has_teeth():
    planted = (
        "import subprocess\nfrom os import path\nimport os\nos.system('x')\nos.posix_spawn\n"
        "eval('1')\nexec('1')\n__import__('os')\nfrom atlas_aci.tools import search_text\n"
        "import importlib.metadata\nimport time\nwork.check()\nwork.remaining_ms()\n"
    )
    found = set(violations(planted))
    assert {
        "import subprocess",
        "attr system",
        "attr posix_spawn",
        "call eval",
        "call exec",
        "call __import__",
        "import atlas_aci.tools",
        "import importlib.metadata",
        "import time",
        "clock check",
        "clock remaining_ms",
    } <= found


# ---- the vocabulary gate (whole tokens only) ------------------------------------------------

FREE_TEXT = {"name", "path", "value", "raw_target", "tool", "report_timestamp", "generation_kind"}


def tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^0-9a-zA-Z]+", text.lower()) if t}


def vocabulary_hits(node, parent_key=None) -> set[str]:
    hits: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            hits |= tokens(str(key)) & BANNED_VOCABULARY
            hits |= vocabulary_hits(value, key)
    elif isinstance(node, list):
        for value in node:
            hits |= vocabulary_hits(value, parent_key)
    elif isinstance(node, str) and parent_key not in FREE_TEXT:
        hits |= tokens(node) & BANNED_VOCABULARY
    return hits


def schema_hits(node) -> set[str]:
    hits: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            hits |= tokens(str(key)) & BANNED_VOCABULARY
            if key in {"enum", "const"}:
                for entry in value if isinstance(value, list) else [value]:
                    hits |= tokens(str(entry)) & BANNED_VOCABULARY
            hits |= schema_hits(value)
    elif isinstance(node, list):
        for value in node:
            hits |= schema_hits(value)
    return hits


def test_no_plan_key_enum_or_value_overclaims(tmp_path):
    world = cov_world(tmp_path)
    result = json.loads(
        validation.canonical_bytes(validation.run(world.before, world.after, None, None, []))
    )
    assert result["recommendations"] and result["gaps"] and result["spans"]
    assert vocabulary_hits(result) == set()
    assert schema_hits(schema.PLAN_SCHEMA) == set()
    for name in (*vlim.LIMITATIONS, *vlim.GAP_REASONS, *vlim.COVERAGE_STATES):
        assert not tokens(name) & BANNED_VOCABULARY, name
    # Whole-token semantics: `coverage`, `coverage_sources` and `uncovered_source_text` are legal.
    assert "coverage_sources" in result and not vocabulary_hits(
        {"coverage": "uncovered_source_text"}
    )


def test_the_vocabulary_gate_has_teeth():
    assert vocabulary_hits({"state": "covered"}) == {"covered"}
    assert vocabulary_hits({"safe_to_skip": "x"}) == {"safe", "skip"}
    assert vocabulary_hits({"name": "covered"}) == set()  # free text is excluded
    planted = json.loads(json.dumps(schema.PLAN_SCHEMA))
    planted["properties"]["spans"]["items"]["properties"]["state"]["enum"].append("covered")
    assert schema_hits(planted) == {"covered"}


# ---- bounded, index-served reads ---------------------------------------------------------------


def test_the_observation_queries_are_served_by_the_target_index(tmp_path):
    world = cov_world(tmp_path)
    assert "json_extract(payload,'$.target_entity_id')" in cov.OBSERVATION_SQL
    assert "json_extract(payload,'$.relation')" in cov.OBSERVATION_SQL
    with ExitStack() as stack:
        held = lease(stack, world.before)
        file_id = held.db.execute(cov.FILE_ENTITY_SQL, ("app/calc.py",)).fetchone()[0]
        for statement in (cov.OBSERVATION_SQL, cov.OBSERVATION_COUNT_SQL):
            rows = held.db.execute(
                "EXPLAIN QUERY PLAN " + statement, ("test_executes", file_id, 10)
            ).fetchall()
            detail = " | ".join(r[3] for r in rows)
            assert "idx_assertions_target_relation_id" in detail, detail
            assert "SCAN assertions" not in detail and "TEMP B-TREE" not in detail, detail
        plan_rows = held.db.execute(
            "EXPLAIN QUERY PLAN SELECT path FROM refs WHERE callee_name=? ORDER BY id LIMIT ?",
            ("clamp", 10),
        ).fetchall()
        detail = " | ".join(r[3] for r in plan_rows)
        assert "idx_refs_callee" in detail and "TEMP B-TREE" not in detail, detail
    source = (QUERY / "validation.py").read_text()
    assert "LIMIT ?" in source and "ORDER BY id LIMIT" in source


def test_the_budget_charges_before_it_reads():
    budget = cov.Budget(3)
    assert budget.take(2) and budget.remaining == 1 and not budget.exhausted
    assert not budget.take(2) and budget.exhausted and budget.used == 2
    assert budget.take(1) and budget.remaining == 0
    assert cov.Budget(0).take(1) is False


# ---- closed vocabularies -------------------------------------------------------------------------


def test_closed_enums_and_limits_are_consistent_with_their_sources():
    assert set(lim.DIFF_REJECTIONS) <= set(vlim.SOURCE_REJECTIONS)
    assert {"duplicate_source", "source_limit"} <= set(vlim.SOURCE_REJECTIONS)
    assert len(vlim.LIMITATIONS) == 15 and len(set(vlim.LIMITATIONS)) == 15
    assert vlim.VALIDATION_LIMITS["max_spans"] == (256, 1, 2048)
    assert vlim.VALIDATION_LIMITS["max_coverage_sources"] == (4, 0, 8)
    assert set(vlim.COVERAGE_REASON.values()) | set(vlim.STRUCTURAL_REASON.values()) == set(
        vlim.GAP_REASONS
    )
    assert vlim.COMPLETENESS_REASONS == (
        "span_limit",
        "seed_limit",
        "impact_work_limit",
        "partial_extraction",
        "observation_limit",
        "reference_probe_limit",
        "reference_row_limit",
    )


def test_the_schema_is_closed_and_semantic_rules_reject_tampering(tmp_path):
    world = cov_world(tmp_path)
    good = validation.run(world.before, world.after)
    validation.validate_plan(good)
    for tamper in (
        lambda p: p.__setitem__("extra", True),
        lambda p: p["recommendations"][0].__setitem__("surprise", 1),
        lambda p: p["recommendations"].reverse(),
        lambda p: p["completeness"].__setitem__("state", "incomplete"),
        lambda p: p["gaps"][0].__setitem__("reasons", {}),
        lambda p: p["recommendations"][0].__setitem__("post_change_validation", True),
        lambda p: p.__setitem__("gaps_total", 0),
        lambda p: p.__setitem__("plan_id", "0" * 64),
        lambda p: p.__setitem__("ci_policy", "may_skip_ci"),
    ):
        broken = json.loads(json.dumps(good))
        tamper(broken)
        try:
            validation.validate_plan(broken)
        except ValueError as exc:
            assert str(exc).startswith("Invalid validation plan")
        else:
            raise AssertionError("tampered plan accepted")
    assert Side(world.repo, world.r0)  # the sides stay plain, unchanged operator inputs
