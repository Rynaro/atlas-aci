"""ACI-S22: the closed JSON Schema of `atlas-validation-plan.v1` and its semantic validator.

`validate_plan` is what the CLI runs before printing anything: the JSON Schema, then the
semantic rules the schema cannot express (basis/evidence kinds, rank order, totals,
completeness, gap/span agreement and the content-addressed `plan_id`).
"""

from __future__ import annotations

from itertools import pairwise
from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from atlas_aci.query import impact_limits as lim
from atlas_aci.query import validation_limits as vlim

HASH = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
NULLABLE_HASH = {"anyOf": [HASH, {"type": "null"}]}
COUNT = {"type": "integer", "minimum": 0}
LINE = {"type": "integer", "minimum": 0}
TEXT = {"type": "string"}
TRUE = {"const": True}


def obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": sorted(properties if required is None else required),
        "properties": properties,
    }


def enum(values: tuple[str, ...]) -> dict[str, Any]:
    return {"type": "string", "enum": list(values)}


def flags(values: tuple[str, ...]) -> dict[str, Any]:
    return obj({name: TRUE for name in values}, [])


def array(item: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": item}


GENERATION = obj({"snapshot_id": HASH, "generation_id": HASH, "generation_kind": TEXT})
SIDE_REF = {
    "anyOf": [
        obj(
            {
                "snapshot_id": HASH,
                "generation_id": HASH,
                "source_sha256": HASH,
                "line": LINE,
                "end_line": LINE,
                "header_end_line": LINE,
            }
        ),
        {"type": "null"},
    ]
}
COVERAGE_PAIR = obj({"before": enum(vlim.COVERAGE_STATES), "after": enum(vlim.COVERAGE_STATES)})
SPAN_IDENTITY = {
    "change_record_id": HASH,
    "entity_id": HASH,
    "name": TEXT,
    "kind": TEXT,
    "language": TEXT,
    "path": TEXT,
    "category": enum(vlim.SPAN_CATEGORIES),
}
SPAN = obj(
    {
        **SPAN_IDENTITY,
        "sides": obj({"before": SIDE_REF, "after": SIDE_REF}),
        "coverage": COVERAGE_PAIR,
        "structural": enum(vlim.STRUCTURAL_STATES),
        "recommendations": array(HASH),
        "state": enum(vlim.SPAN_STATES),
    }
)
GAP = obj(
    {
        **SPAN_IDENTITY,
        "action": {"const": "inspect"},
        "basis": {"const": "unsupported"},
        "lines": obj(
            {
                "before": {"anyOf": [array(LINE), {"type": "null"}]},
                "after": {"anyOf": [array(LINE), {"type": "null"}]},
            }
        ),
        "coverage": COVERAGE_PAIR,
        "structural": enum(vlim.STRUCTURAL_STATES),
        "reasons": flags(vlim.GAP_REASONS),
    }
)
OBSERVED_EVIDENCE = obj(
    {
        "kind": {"const": "observed_execution"},
        "change_record_id": HASH,
        "matched_side": enum(vlim.SIDES),
        "matched_snapshot_id": HASH,
        "matched_generation_id": HASH,
        "source": enum(vlim.SOURCES),
        "coverage_generation_id": HASH,
        "coverage_snapshot_id": HASH,
        "run_id": HASH,
        "producer_id": HASH,
        "assertion_id": HASH,
        "evidence_id": HASH,
        "occurrence_id": HASH,
        "line": LINE,
        "phases": array(enum(("run", "setup", "teardown"))),
        "scope": enum(vlim.SCOPES),
        "matched_lines": array(LINE),
        "matched_lines_total": COUNT,
        "body_lines_total": COUNT,
        "test_file_after": enum(vlim.TEST_FILE_AFTER),
    }
)
STRUCTURAL_COMMON = {
    "change_record_id": HASH,
    "snapshot_side": enum(vlim.SIDES),
    "snapshot_id": HASH,
    "generation_id": HASH,
    "distance": {"type": "integer", "minimum": 1},
    "test_file_after": enum(vlim.TEST_FILE_AFTER),
}
HOP = obj(
    {
        "assertion_id": HASH,
        "evidence_id": HASH,
        "occurrence_id": HASH,
        "producer_id": HASH,
        "relation": TEXT,
        "resolution": TEXT,
    }
)
REVERSE_EVIDENCE = obj(
    {
        "kind": {"const": "reverse_path"},
        **STRUCTURAL_COMMON,
        "hops": {"type": "array", "items": HOP, "minItems": 1},
    }
)
REFERENCE_EVIDENCE = obj(
    {
        "kind": {"const": "same_name_reference"},
        **STRUCTURAL_COMMON,
        "via_entity_id": HASH,
        "reference": obj(
            {
                "assertion_id": HASH,
                "evidence_id": HASH,
                "occurrence_id": HASH,
                "producer_id": HASH,
                "relation": TEXT,
                "resolution": TEXT,
                "raw_target": TEXT,
                "line": LINE,
            }
        ),
    }
)
RECOMMENDATION = obj(
    {
        "recommendation_id": HASH,
        "action": {"const": "run_test"},
        "basis": enum(vlim.BASES),
        "method": enum(vlim.METHODS),
        "test": obj(
            {
                "identity_kind": enum(vlim.IDENTITY_KINDS),
                "entity_id": HASH,
                "path": TEXT,
                "name": TEXT,
                "selector": obj({"kind": enum(vlim.SELECTOR_KINDS), "value": TEXT}),
            }
        ),
        "observed_in": obj({"before": TRUE, "after": TRUE}, []),
        "post_change_validation": {"type": "boolean"},
        "present_in_after": {"type": "boolean"},
        "also_observed": {"type": "boolean"},
        "rank": obj(
            {
                "basis_order": {"type": "integer", "minimum": 0, "maximum": 4},
                "spans": COUNT,
                "body_lines": COUNT,
                "category_weight": COUNT,
                "distance": COUNT,
            }
        ),
        "spans_total": COUNT,
        "evidence_total": COUNT,
        "evidence": {
            "type": "array",
            "minItems": 1,
            "items": {"oneOf": [OBSERVED_EVIDENCE, REVERSE_EVIDENCE, REFERENCE_EVIDENCE]},
        },
    }
)
COVERAGE_SOURCE = obj(
    {
        "source": enum(vlim.SOURCES),
        "generation_id": HASH,
        "snapshot_id": HASH,
        "generation_kind": TEXT,
        "runs": array(
            obj(
                {
                    "run_id": HASH,
                    "producer_id": HASH,
                    "tool": TEXT,
                    "report_timestamp": {"anyOf": [TEXT, {"type": "null"}]},
                }
            )
        ),
        "files": array(
            obj(
                {
                    "path": TEXT,
                    "classification": enum(vlim.CLASSIFICATIONS),
                    "observation_rows": COUNT,
                }
            )
        ),
    }
)
FRONTIER = obj(
    {
        "changed_entity_id": HASH,
        "snapshot_side": enum(vlim.SIDES),
        "expansion": enum(("expanded", "not_expanded")),
        "outcome": {"anyOf": [TEXT, {"type": "null"}]},
        "stop_reason": {"anyOf": [TEXT, {"type": "null"}]},
        "commitment": NULLABLE_HASH,
    }
)
_LIMIT_NAMES = [n for n in lim.IMPACT_LIMITS if n != "max_boundary_probes"]
PLAN_SCHEMA: dict[str, Any] = obj(
    {
        "format": {"const": vlim.FORMAT},
        "status": {"const": "recommendations_only"},
        "execution": {"const": "not_performed"},
        "ci_policy": {"const": "advisory_only"},
        "ranker": {"const": vlim.RANKER_ID},
        "test_rule": {"const": vlim.TEST_RULE_ID},
        "before": GENERATION,
        "after": GENERATION,
        "change_set_id": HASH,
        "diff_work": {"type": "object", "additionalProperties": COUNT},
        "impact": obj(
            {
                "seeds_total": COUNT,
                "seeds_expanded": COUNT,
                "impacted_total": COUNT,
                "relations": {"type": "object", "additionalProperties": TRUE},
                "confidence": {"type": "object", "additionalProperties": TRUE},
                "frontiers": array(FRONTIER),
            }
        ),
        "effective_limits": obj(
            {
                **{name: COUNT for name in _LIMIT_NAMES},
                "element_limit": COUNT,
                "max_diff_entities": COUNT,
                "max_diff_occurrences": COUNT,
                "max_changed_files": COUNT,
                **{name: COUNT for name in vlim.VALIDATION_LIMITS},
            }
        ),
        "work": obj(
            {
                name: COUNT
                for name in (
                    "examined_edges",
                    "visited_nodes",
                    "observation_rows_read",
                    "stale_observation_rows",
                    "reference_probes",
                    "reference_rows_read",
                    "assertion_paths_loaded",
                    "source_files_read",
                )
            }
        ),
        "coverage_sources": array(COVERAGE_SOURCE),
        "spans": array(SPAN),
        "spans_total": COUNT,
        "recommendations": array(RECOMMENDATION),
        "recommendations_total": COUNT,
        "gaps": array(GAP),
        "gaps_total": COUNT,
        "completeness": obj(
            {
                "state": enum(("complete", "incomplete")),
                "reasons": flags(vlim.COMPLETENESS_REASONS),
            }
        ),
        "selection": obj(
            {
                "spans": {"type": "boolean"},
                "recommendations": {"type": "boolean"},
                "gaps": {"type": "boolean"},
            }
        ),
        "limitations": flags(vlim.LIMITATIONS),
        "plan_id": HASH,
    }
)
VALIDATOR = Draft202012Validator(PLAN_SCHEMA)


def _fail(message: str) -> ValueError:
    return ValueError("Invalid validation plan: " + message)


def _require(condition: Any, message: str) -> None:
    if not condition:
        raise _fail(message)


def validate_plan(plan: Any) -> None:
    """Schema, then the semantic rules. Raises `ValueError` with a path-free message."""
    if not isinstance(plan, dict) or not VALIDATOR.is_valid(plan):
        raise _fail("does not match the closed schema")
    _recommendations(plan)
    _spans_and_gaps(plan)
    _require(
        plan["completeness"]["state"]
        == ("incomplete" if plan["completeness"]["reasons"] else "complete"),
        "completeness state disagrees with its reasons",
    )
    _require(plan["plan_id"] == vlim.plan_id(plan), "plan_id does not match the content")


def _recommendations(plan: dict[str, Any]) -> None:
    recs = plan["recommendations"]
    _require(plan["recommendations_total"] >= len(recs), "recommendations_total below emitted")
    _require(
        plan["selection"]["recommendations"] == (plan["recommendations_total"] > len(recs)),
        "selection.recommendations disagrees with totals",
    )
    keys = [vlim.rank_key(r) for r in recs]
    _require(
        all(a < b for a, b in pairwise(keys)),
        "recommendations are not in strict rank order",
    )
    _require(len({r["recommendation_id"] for r in recs}) == len(recs), "duplicate recommendation")
    for rec in recs:
        test, kinds = rec["test"], {e["kind"] for e in rec["evidence"]}
        _require(rec["evidence_total"] >= len(rec["evidence"]), "evidence_total below emitted")
        _require(
            rec["post_change_validation"] == bool(rec["observed_in"].get("after")),
            "post_change_validation must hold exactly when observed in after",
        )
        _require(
            rec["rank"]["spans"] == rec["spans_total"], "rank.spans disagrees with spans_total"
        )
        if rec["basis"] == "observed_execution":
            _require(
                kinds == {"observed_execution"}
                and rec["method"] == "coverage_context"
                and test["identity_kind"] == "coverage_test"
                and test["selector"]["kind"] == "pytest_nodeid"
                and bool(rec["observed_in"])
                and not rec["also_observed"],
                "observed_execution recommendation carries the wrong evidence or identity",
            )
            _require(rec["rank"]["basis_order"] in {0, 1, 2}, "observed basis_order out of range")
            _require(rec["rank"]["distance"] == 0, "observed distance must be zero")
            if rec["evidence_total"] == len(rec["evidence"]):
                body = {e["matched_side"] for e in rec["evidence"] if e["scope"] == "body"}
                expected = 0 if "after" in body else 1 if "before" in body else 2
                _require(rec["rank"]["basis_order"] == expected, "observed basis_order disagrees")
                _require(
                    set(rec["observed_in"]) == {e["matched_side"] for e in rec["evidence"]},
                    "observed_in disagrees with the evidence",
                )
        else:
            _require(
                kinds <= {"reverse_path", "same_name_reference"}
                and test["identity_kind"] == "code_entity"
                and test["selector"]["kind"] == "pytest_selector_hint"
                and rec["observed_in"] == {}
                and rec["post_change_validation"] is False,
                "structural recommendation carries observed evidence or identity",
            )
            if rec["evidence_total"] == len(rec["evidence"]):
                reverse = "reverse_path" in kinds
                _require(
                    rec["method"] == ("reverse_path" if reverse else "same_name_reference")
                    and rec["rank"]["basis_order"] == (3 if reverse else 4),
                    "structural method disagrees with its evidence kinds",
                )
            _require(rec["rank"]["basis_order"] in {3, 4}, "structural basis_order out of range")
            _require(
                all(
                    "run_id" not in e and not any(k.startswith("coverage_") for k in e)
                    for e in rec["evidence"]
                ),
                "structural evidence carries execution fields",
            )


def _spans_and_gaps(plan: dict[str, Any]) -> None:
    spans, gaps = plan["spans"], plan["gaps"]
    _require(plan["spans_total"] >= len(spans), "spans_total below emitted")
    _require(
        plan["selection"]["spans"] == (plan["spans_total"] > len(spans)),
        "selection.spans disagrees with totals",
    )
    gap_spans = [s for s in spans if s["state"] == "gap"]
    _require(plan["gaps_total"] == len(gap_spans), "gaps_total disagrees with the gap spans")
    _require(len(gaps) <= plan["gaps_total"], "more gaps emitted than exist")
    _require(
        plan["selection"]["gaps"] == (plan["gaps_total"] > len(gaps)),
        "selection.gaps disagrees with totals",
    )
    by_id = {s["change_record_id"]: s for s in spans}
    _require(len(by_id) == len(spans), "duplicate span")
    _require(
        [g["change_record_id"] for g in gaps]
        == [s["change_record_id"] for s in gap_spans][: len(gaps)],
        "gaps do not follow the gap spans in order",
    )
    for gap in gaps:
        span = by_id.get(gap["change_record_id"])
        _require(span is not None and span["state"] == "gap", "gap without a gap span")
        assert span is not None
        _require(bool(gap["reasons"]), "gap without reasons")
        _require(
            gap["coverage"] == span["coverage"] and gap["structural"] == span["structural"],
            "gap disagrees with its span",
        )
    emitted_ids = {r["recommendation_id"] for r in plan["recommendations"]}
    for span in spans:
        _require(
            set(span["recommendations"]) <= emitted_ids, "span cites an unknown recommendation"
        )
        coverage, structural = span["coverage"], span["structural"]
        expected = (
            "observed_after"
            if coverage["after"] == "executed"
            else "observed_before_only"
            if coverage["before"] == "executed"
            else "structural_only"
            if structural == "found"
            else "gap"
        )
        _require(span["state"] == expected, "span state disagrees with its coverage")
