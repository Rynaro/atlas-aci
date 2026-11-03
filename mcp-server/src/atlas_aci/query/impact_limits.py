"""ACI-S20 closed constants: limits, vocabularies, ranking and content address.

Pure data and one pure function. Both the protocol schema/validator and the
library import it, so the published schema and the generated result can only
disagree through a code change visible here.
"""

from __future__ import annotations

import hashlib
from typing import Any

from atlas_aci.storage import evidence as ev

FORMAT = "atlas-impact.v1"
RANKER_ID = "atlas-impact-rank.v1"

# (default, low, high). `deadline_ms` bounds propagation and boundary probing, never
# lease acquisition or the unmetered change-set phase (see docs/change-impact.md).
IMPACT_LIMITS: dict[str, tuple[int, int, int]] = {
    "max_seeds": (32, 1, 64),
    "max_depth": (3, 1, 32),
    "max_visited_nodes": (200, 1, 1000),
    "max_examined_edges": (2000, 0, 10000),
    "max_boundary_probes": (256, 0, 2048),
    "max_vm_operations": (1_000_000, 0, 10_000_000),
    "deadline_ms": (2000, 0, 2000),
}
# Operator-only (Config / CLI); never request arguments.
DEFAULT_DIFF_LIMITS = {
    "max_entities": 50_000,
    "max_occurrences": 250_000,
    "max_changed_files": 200,
}
DIFF_REJECTIONS = (
    "index_unavailable",
    "source_snapshot_unavailable",
    "identity_kind_mismatch",
    "repository_identity_mismatch",
    "schema_mismatch",
    "configuration_mismatch",
    "producer_mismatch",
    "same_namespace",
)
DIFF_TOO_LARGE_REASONS = ("diff_entity_limit", "diff_occurrence_limit", "changed_file_limit")

# `rationale_for` is a comment-to-code edge, not a dependency: never a propagation relation.
RELATIONS = tuple(sorted(ev.RELATIONS - {"rationale_for"}))
DEFAULT_RELATIONS = ("call", "construct", "extend", "include", "prepend", "superclass")
DEFAULT_CONFIDENCE = ("EXTRACTED", "INFERRED")

SEED_CATEGORIES = ("removed", "signature", "unknown", "body", "added")
CATEGORY_WEIGHT = {name: index for index, name in enumerate(SEED_CATEGORIES)}
SIDES = ("after", "before")  # side_order: after=0, before=1

COVERAGE_REASONS = (
    "unresolved_reference",
    "ambiguous_reference",
    "reference_coverage_unknown",
    "work_limit",
    "seed_limit",
    "boundary_probe_limit",
    "partial_extraction",
)
FRONTIER_STOPS = (
    "deadline",
    "depth_limit",
    "visited_node_limit",
    "examined_edge_limit",
    "impact_deadline",
    "impact_vm_operations_reserved_limit",
    "impact_examined_edges_limit",
    "impact_boundary_probes_limit",
)
PROBE_STOPS = (
    "probe_limit",
    "deadline",
    "impact_deadline",
    "impact_vm_operations_reserved_limit",
    "impact_examined_edges_limit",
    "impact_boundary_probes_limit",
)
PROBE_RELATION_EXTRA = "jsx"

LIMITATIONS = (
    "aliased_reference_names_not_probed",
    "behavior_bearing_comment_edits_are_masked_by_format_classification",
    "boundary_probes_match_by_spelled_name_and_over_approximate",
    "coverage_complete_means_no_same_name_unresolved_references_not_proof_of_completeness",
    "dynamic_dispatch_reflection_and_runtime_wiring_are_not_modeled",
    "format_and_location_changes_are_not_seeds",
    "import_relation_is_opt_in_module_level_coupling",
    "removed_symbol_callers_are_explained_from_the_before_snapshot",
    "results_are_not_paginated",
    "rust_and_jsx_references_are_never_resolved",
    "static_reachability_is_potential_impact_only",
    "test_selection_and_runtime_observation_are_not_performed",
)


def impact_id(data: dict[str, Any]) -> str:
    body = {key: value for key, value in data.items() if key != "impact_id"}
    return hashlib.sha256((FORMAT + ":" + ev.canonical(body)).encode("utf-8")).hexdigest()


def rank_key(item: dict[str, Any]) -> tuple[Any, ...]:
    rank = item["rank"]
    return (
        rank["distance"],
        rank["category_weight"],
        rank["side_order"],
        item["changed_entity_id"],
        item["entity"]["id"],
    )
