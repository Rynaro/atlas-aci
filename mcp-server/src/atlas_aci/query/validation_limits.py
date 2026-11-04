"""ACI-S22 closed constants: formats, vocabularies, limits, test rule, ranking, content address.

Pure data and pure functions. The schema, the validator and the library import this
module, so the published schema and the generated plan can only disagree through a code
change visible here.

Nothing here executes, imports or collects tests: a "test" is only ever a name, a path
and a selector hint derived from indexed records.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from atlas_aci.query import impact_limits as lim
from atlas_aci.storage import evidence as ev

FORMAT = "atlas-validation-plan.v1"
RANKER_ID = "atlas-validation-rank.v1"
TEST_RULE_ID = "atlas-pytest-default-conventions.v1"

# (default, low, high). Impact propagation limits keep impact's own ranges
# (`impact_limits.IMPACT_LIMITS`); diff limits are operator-only (impact defaults).
VALIDATION_LIMITS: dict[str, tuple[int, int, int]] = {
    "max_spans": (256, 1, 2048),
    "max_recommendations": (50, 1, 500),
    "max_gaps": (256, 1, 2048),
    "max_evidence_per_recommendation": (8, 1, 64),
    "max_matched_lines": (32, 1, 256),
    "max_observation_rows": (100_000, 0, 1_000_000),
    "max_reference_probes": (64, 0, 512),
    "max_reference_rows": (2000, 0, 20_000),
    "max_coverage_sources": (4, 0, 8),
}

# Propagation arguments accepted by the planner: exactly impact's published request
# minus the byte budget (a plan has no byte budget; it is a CLI/library result).
PROPAGATION_ARGUMENTS = (
    "relations",
    "confidence",
    *[name for name in lim.IMPACT_LIMITS if name != "max_boundary_probes"],
)

SPAN_CATEGORIES = lim.SEED_CATEGORIES
CATEGORY_WEIGHT = lim.CATEGORY_WEIGHT
SIDES = ("before", "after")
SIDE_ORDER = {"after": 0, "before": 1}

BASES = ("observed_execution", "structural")
METHODS = ("coverage_context", "reverse_path", "same_name_reference")
IDENTITY_KINDS = ("coverage_test", "code_entity")
SELECTOR_KINDS = ("pytest_nodeid", "pytest_selector_hint")
EVIDENCE_KINDS = ("observed_execution", "reverse_path", "same_name_reference")
SCOPES = ("body", "declaration")
TEST_FILE_AFTER = ("same_bytes", "changed_bytes", "absent")
SOURCES = ("before", "after", "external")
CLASSIFICATIONS = (
    "matches_before",
    "matches_after",
    "matches_both",
    "stale",
    "not_observed",
    "unread",
)
COVERAGE_STATES = (
    "not_applicable",
    "unsupported_language",
    "observation_limit",
    "executed",
    "declaration_only",
    "no_observation_in_span",
    "no_observation_in_file",
    "stale_only",
    "no_coverage_source",
)
STRUCTURAL_STATES = ("found", "incomplete", "unsupported_language", "none")
SPAN_STATES = ("observed_after", "observed_before_only", "structural_only", "gap")
GAP_REASONS = (
    "no_observation_in_span",
    "no_observation_in_file",
    "no_coverage_source",
    "observation_limit",
    "unsupported_language",
    "stale_coverage_only",
    "declaration_only_execution",
    "no_structural_test",
    "structural_discovery_incomplete",
)
COVERAGE_REASON = {
    "no_observation_in_span": "no_observation_in_span",
    "no_observation_in_file": "no_observation_in_file",
    "no_coverage_source": "no_coverage_source",
    "observation_limit": "observation_limit",
    "unsupported_language": "unsupported_language",
    "stale_only": "stale_coverage_only",
    "declaration_only": "declaration_only_execution",
}
STRUCTURAL_REASON = {
    "none": "no_structural_test",
    "incomplete": "structural_discovery_incomplete",
    "unsupported_language": "unsupported_language",
}
COMPLETENESS_REASONS = (
    "span_limit",
    "seed_limit",
    "impact_work_limit",
    "partial_extraction",
    "observation_limit",
    "reference_probe_limit",
    "reference_row_limit",
)
SOURCE_REJECTIONS = (*lim.DIFF_REJECTIONS, "duplicate_source", "source_limit")

LIMITATIONS = (
    "recommendations_are_advisory_and_do_not_change_ci",
    "tests_are_not_executed_by_planning",
    "observed_execution_is_not_a_pass_or_assertion",
    "before_snapshot_observation_is_not_post_change_validation",
    "absence_of_observation_is_not_evidence_of_non_execution",
    "coverage_matches_only_byte_identical_file_snapshots",
    "declaration_lines_are_weighted_below_body_lines",
    "structural_tests_follow_pytest_default_conventions_only",
    "same_name_references_over_approximate",
    "absolute_test_imports_do_not_yield_reverse_paths",
    "observed_and_structural_identities_are_not_merged",
    "format_and_location_changes_are_not_spans",
    "impact_boundaries_not_reported_use_atlas_aci_impact",
    "results_are_not_paginated",
    "not_exposed_over_mcp",
)

_TEST_FILE = re.compile(r"^(test_.*|.*_test)\.py$")
_PARAM_SUFFIX = re.compile(r"\[.*\]$", re.DOTALL)


# ---- the closed pytest default-convention test rule ----------------------------


def is_test_path(path: str) -> bool:
    """Basename matches pytest's default `test_*.py` / `*_test.py` file patterns."""
    return _TEST_FILE.match(path.rsplit("/", 1)[-1]) is not None


def is_test_entity(entity: dict[str, Any]) -> bool:
    """`atlas-pytest-default-conventions.v1`: a closed predicate over indexed fields only."""
    if entity.get("language") != "python" or entity.get("kind") not in {"function", "method"}:
        return False
    name = entity.get("name")
    if not isinstance(name, str) or not name.startswith("test"):
        return False
    if not is_test_path(entity["path"]):
        return False
    classes = [o for o in entity.get("owners") or [] if o.get("kind") == "class"]
    if classes:
        owner = classes[-1].get("name")
        return isinstance(owner, str) and owner.startswith("Test")
    return True


def selector_hint(entity: dict[str, Any]) -> str:
    classes = [
        o["name"]
        for o in entity.get("owners") or []
        if o.get("kind") == "class" and isinstance(o.get("name"), str)
    ]
    return "::".join([entity["path"], *classes, entity["name"]])


def strip_parameters(nodeid: str) -> str:
    return _PARAM_SUFFIX.sub("", nodeid)


def probe_relations(relations: list[str]) -> tuple[str, ...]:
    """Local copy of `impact._probe_relations` (kept equal by a unit test)."""
    chosen = set(relations) | {lim.PROBE_RELATION_EXTRA}
    if "construct" in relations:
        chosen.add("call")
    return tuple(sorted(chosen))


# ---- ranking and content addresses ---------------------------------------------


def rank_key(rec: dict[str, Any]) -> tuple[Any, ...]:
    rank = rec["rank"]
    return (
        rank["basis_order"],
        -rank["spans"],
        -rank["body_lines"],
        rank["category_weight"],
        rank["distance"],
        rec["test"]["selector"]["value"],
        rec["recommendation_id"],
    )


def recommendation_id(key: tuple[str, ...]) -> str:
    body = ev.canonical(list(key))
    return hashlib.sha256(
        ("atlas-validation-recommendation.v1:" + body).encode("utf-8")
    ).hexdigest()


def plan_id(plan: dict[str, Any]) -> str:
    body = {key: value for key, value in plan.items() if key != "plan_id"}
    return hashlib.sha256((FORMAT + ":" + ev.canonical(body)).encode("utf-8")).hexdigest()
