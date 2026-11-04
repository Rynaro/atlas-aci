"""ACI-S22: an evidence-backed validation plan from impact and observed coverage.

One library function (`run`) serves the operator CLI `atlas-aci validation-plan`. It
composes the S20 impact building blocks on already-held leases (change set, seeds and
propagation, each computed ONCE and never through `impact.compute`), then adds:

* observed execution: S21 `test_executes` records, matched to a changed span ONLY by a
  byte-identical file snapshot of the requested side (before or after);
* structural discovery: reverse-path tests and same-name references from test files,
  under a closed pytest default-convention rule;
* explicit gaps for every evaluated span without mapped evidence.

Recommendations are advisory only. Nothing is run, imported or collected: the planner
reads indexed records and snapshot bytes. A short list is never permission to skip CI.

Private symbols used (any drift fails `test_s22_semantics` loudly, naming them):
`atlas_aci.query.diff._open`, `_lease` (side opening, as `impact.run`), `_check_compatible`
(through `validation_coverage`) and `impact.ImpactError`/`impact.rejected`; the impact
module functions `change_set`, `select_seeds`, `propagate`, `normalize`, `open_side` and the
lease attributes `db`, `atlas_dir`, `snapshot_id`, `generation_id`, `read_source`. The
`impact._probe_relations` helper is reimplemented locally and kept equal by a unit test.

Post-propagation phases are bounded by row counters only: they never consult
`ImpactWork.check`, `remaining_ms` or a wall clock.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from atlas_aci.enforcement import ToolError
from atlas_aci.protocol.graph import VERSION
from atlas_aci.query import diff as symbol_diff
from atlas_aci.query import impact
from atlas_aci.query import impact_limits as lim
from atlas_aci.query import validation_coverage as cov
from atlas_aci.query import validation_limits as vlim
from atlas_aci.query.diff import DiffRejected, Side
from atlas_aci.query.impact_work import ImpactWork
from atlas_aci.query.validation_coverage import SourceRejected
from atlas_aci.query.validation_schema import validate_plan
from atlas_aci.storage import evidence as ev

__all__ = [
    "SourceRejected",
    "canonical_bytes",
    "collect",
    "normalize",
    "run",
    "validate_plan",
]


# ---- request ------------------------------------------------------------------


def normalize(arguments: dict[str, Any] | None, settings: impact.Settings):
    """Split arguments into impact's closed request and the closed validation limits."""
    given = dict(arguments or {})
    allowed = set(vlim.PROPAGATION_ARGUMENTS) | set(vlim.VALIDATION_LIMITS)
    if not isinstance(arguments or {}, dict) or set(given) - allowed:
        raise ToolError("INVALID_INPUT", "Validation arguments do not match the closed set.")
    limits: dict[str, int] = {}
    for name, (default, low, high) in vlim.VALIDATION_LIMITS.items():
        value = given.get(name, default)
        if type(value) is not int or not low <= value <= high:
            raise ToolError("INVALID_INPUT", "Invalid finite validation limit.")
        limits[name] = value
    propagation = {k: given[k] for k in vlim.PROPAGATION_ARGUMENTS if k in given}
    request = impact.normalize(
        {"schema_version": VERSION, "op": "impact", **propagation},
        max_response_bytes=settings.max_response_bytes,
    )
    return request, limits


# ---- spans ----------------------------------------------------------------------


def _span(item: dict[str, Any]) -> dict[str, Any]:
    record = item["record"]
    refs = {"before": record["before"], "after": record["after"]}
    lines = [r["line"] for r in refs.values() if r is not None]
    return {
        "change_record_id": record["id"],
        "entity_id": record["entity_id"],
        "name": record["name"],
        "kind": record["kind"],
        "language": record["language"],
        "path": record["path"],
        "category": record["category"],
        "refs": refs,
        "seed_side": item["side"],
        "first_line": min(lines) if lines else 0,
    }


def _span_order(span: dict[str, Any]) -> tuple[Any, ...]:
    return (
        vlim.CATEGORY_WEIGHT[span["category"]],
        span["path"],
        span["first_line"],
        span["change_record_id"],
    )


# ---- recommendations ------------------------------------------------------------


def _test_file_after(observed: cov.Observed, side_or_none, path: str, sha: str | None) -> str:
    after = observed.side_hash("after", path)
    if after is None:
        return "absent"
    if side_or_none == "after":
        return "same_bytes"
    return "same_bytes" if sha == after else "changed_bytes"


def _present_in_after(observed: cov.Observed, path: str) -> bool:
    record = observed.hashes["after"].get(path)
    return record is not None and record.get("state") == "indexed"


class _Observed:
    """Observed-execution evidence accumulated per test identity."""

    def __init__(self, observation: cov.Observation):
        self.key = ("coverage_test", observation.test_path, observation.nodeid)
        self.entity_id = observation.test_entity_id
        self.path = observation.test_path
        self.nodeid = observation.nodeid
        self.groups: dict[tuple[str, str, int], list[tuple[cov.Observation, str]]] = {}
        self.sources: dict[int, cov.Source] = {}


class _Structural:
    """Structural evidence accumulated per test code entity."""

    def __init__(self, entity: dict[str, Any]):
        self.key = ("code_entity", entity["id"])
        self.entity = {k: entity[k] for k in ("id", "name", "kind", "language", "path", "owners")}
        self.evidence: list[dict[str, Any]] = []


def _observed_entry(
    state: _Observed,
    span: dict[str, Any],
    side: str,
    source: cov.Source,
    items: list[tuple[cov.Observation, str]],
    readers: dict[str, Any],
    observed: cov.Observed,
    max_lines: int,
):
    lines = sorted({o.line for o, _ in items})
    body = sorted({o.line for o, scope in items if scope == "body"})
    pool = [o for o, scope in items if scope == "body"] or [o for o, _ in items]
    rep = min(pool, key=lambda o: (o.line, o.assertion_id))
    entry = {
        "kind": "observed_execution",
        "change_record_id": span["change_record_id"],
        "matched_side": side,
        "matched_snapshot_id": readers[side].snapshot_id,
        "matched_generation_id": readers[side].generation_id,
        "source": source.kind,
        "coverage_generation_id": source.reader.generation_id,
        "coverage_snapshot_id": source.reader.snapshot_id,
        "run_id": rep.run_id,
        "producer_id": rep.producer_id,
        "assertion_id": rep.assertion_id,
        "evidence_id": rep.evidence_id,
        "occurrence_id": rep.occurrence_id,
        "line": rep.line,
        "phases": sorted(rep.phases),
        "scope": "body" if body else "declaration",
        "matched_lines": lines[:max_lines],
        "matched_lines_total": len(lines),
        "body_lines_total": len(body),
        "test_file_after": _test_file_after(observed, None, state.path, rep.test_file_sha256),
    }
    key = (
        vlim.SIDE_ORDER[side],
        0 if body else 1,
        span["change_record_id"],
        source.index,
        entry["coverage_generation_id"],
        entry["run_id"] or "",
        rep.line,
    )
    return key, entry, body


def _evidence_slice(pairs: list[tuple[tuple[Any, ...], dict[str, Any]]], limit: int):
    pairs.sort(key=lambda pair: (pair[0], ev.canonical(pair[1])))
    return [entry for _key, entry in pairs[:limit]], len(pairs)


# ---- collect --------------------------------------------------------------------


async def collect(
    before: Any,
    after: Any,
    externals: list[Any],
    request: dict[str, Any],
    limits: dict[str, int],
    settings: impact.Settings,
) -> dict[str, Any]:
    """The plan for two held leases and held external sources. Never closes any reader."""
    change, loaded, diff_work = impact.change_set(before, after, settings.diff_limits)
    # Propagation allowance (and its deadline) starts after the unmetered change set exists.
    work = ImpactWork(**{name: request[name] for name in lim.IMPACT_LIMITS})
    readers = {"before": before, "after": after}
    changed_ids = {record["entity_id"] for record in change["changes"]}
    eligible = impact.select_seeds(change["changes"], loaded[1])
    seeds = eligible[: min(request["max_seeds"], settings.element_limit)]
    impacted, seed_rows, propagation_limited = await impact.propagate(
        readers, seeds, request, work, changed_ids
    )
    # ---- from here on: counters only, no ImpactWork.check / remaining_ms / wall clock ----

    all_spans = sorted((_span(item) for item in eligible), key=_span_order)
    spans = all_spans[: limits["max_spans"]]
    span_ids = {s["change_record_id"] for s in spans}
    python_spans = [s for s in spans if s["language"] == "python"]

    counters = cov.Counters()
    observed_budget = cov.Budget(limits["max_observation_rows"])
    reference_budget = cov.Budget(limits["max_reference_rows"])
    sources = [cov.make_source("before", 0, before), cov.make_source("after", 1, after)]
    sources += [
        cov.make_source("external", index + 2, reader) for index, reader in enumerate(externals)
    ]
    observed = cov.Observed(sources, observed_budget, counters)
    observed.read(sorted({ref["path"] for s in python_spans for ref in s["refs"].values() if ref}))
    geometry = cov.Geometry(readers, counters)

    # ---- observed evidence per span side ----
    coverage_state: dict[str, dict[str, str]] = {}
    observed_tests: dict[tuple[str, ...], _Observed] = {}
    for span in spans:
        states: dict[str, str] = {}
        for side in vlim.SIDES:
            ref = span["refs"][side]
            if ref is None:
                states[side] = "not_applicable"
                continue
            if span["language"] != "python":
                states[side] = "unsupported_language"
                continue
            path = ref["path"]
            geo = geometry.of(side, ref)
            matching = observed.matching_sources(side, path)
            matched_any = False
            body_any = False
            for source in matching:
                for observation in observed.overlapping(source, path, geo):
                    if observation.source_sha256 != ref["source_sha256"]:
                        continue
                    scope = "body" if cov.is_body(observation, geo) else "declaration"
                    matched_any = True
                    body_any = body_any or scope == "body"
                    holder = observed_tests.setdefault(
                        ("coverage_test", observation.test_path, observation.nodeid),
                        _Observed(observation),
                    )
                    holder.groups.setdefault(
                        (span["change_record_id"], side, source.index), []
                    ).append((observation, scope))
                    holder.sources[source.index] = source
            limited = any(path in s.limited for s in observed.sources if s.bearing)
            if limited:
                states[side] = "observation_limit"
            elif body_any:
                states[side] = "executed"
            elif matched_any:
                states[side] = "declaration_only"
            elif any(s.row_counts.get(path, 0) for s in matching):
                states[side] = "no_observation_in_span"
            elif matching:
                states[side] = "no_observation_in_file"
            elif observed.stale_with_rows(path):
                states[side] = "stale_only"
            else:
                states[side] = "no_coverage_source"
        coverage_state[span["change_record_id"]] = states

    # ---- structural discovery ----
    structurals: dict[str, _Structural] = {}
    mapped_structural: dict[str, set[str]] = {}

    def struct_state(entity: dict[str, Any]) -> _Structural:
        if entity["id"] not in structurals:
            structurals[entity["id"]] = _Structural(entity)
        return structurals[entity["id"]]

    for item in impacted:
        entity = item["entity"]
        record_id = item["change_record_id"]
        if record_id not in span_ids or not vlim.is_test_entity(entity):
            continue
        hops = [
            {
                "assertion_id": hop["assertion"]["id"],
                "evidence_id": hop["evidence"]["id"],
                "occurrence_id": hop["occurrence"]["id"],
                "producer_id": hop["evidence"]["producer_id"],
                "relation": hop["assertion"]["relation"],
                "resolution": hop["assertion"]["resolution"],
            }
            for hop in item["path"]["hops"]
        ]
        side = item["snapshot_side"]
        struct_state(entity).evidence.append(
            {
                "kind": "reverse_path",
                "change_record_id": record_id,
                "snapshot_side": side,
                "snapshot_id": item["snapshot_id"],
                "generation_id": item["generation_id"],
                "distance": item["distance"],
                "hops": hops,
                "test_file_after": _test_file_after(
                    observed, side, entity["path"], observed.side_hash(side, entity["path"])
                ),
            }
        )
        mapped_structural.setdefault(record_id, set()).add(entity["id"])

    # B probes: seed names first (span order), then impacted entities in impact rank order.
    probes: dict[tuple[str, str], list[tuple[str, str, int]]] = {}
    for span in spans:
        if span["language"] == "python":
            probes.setdefault((span["seed_side"], span["name"]), []).append(
                (span["entity_id"], span["change_record_id"], 0)
            )
    for item in impacted:
        entity = item["entity"]
        if item["change_record_id"] in span_ids and entity["language"] == "python":
            probes.setdefault((item["snapshot_side"], entity["name"]), []).append(
                (entity["id"], item["change_record_id"], item["distance"])
            )
    relations = vlim.probe_relations(request["relations"])
    assertion_cache: dict[tuple[str, str], list[dict[str, Any]] | None] = {}
    unprobed_records: set[str] = set()
    probe_limited = False

    def assertions_of(side: str, path: str):
        key = (side, path)
        if key not in assertion_cache:
            allowance = reference_budget.remaining
            rows = (
                readers[side]
                .db.execute(
                    "SELECT payload FROM assertions WHERE path=? LIMIT ?", (path, allowance + 1)
                )
                .fetchall()
            )
            counters.assertion_paths_loaded += 1
            if len(rows) > allowance:
                reference_budget.exhausted = True
                assertion_cache[key] = None
            else:
                reference_budget.take(len(rows))
                assertion_cache[key] = sorted(
                    (json.loads(r[0]) for r in rows), key=lambda a: a["id"]
                )
        return assertion_cache[key]

    b_hits: dict[tuple[str, str, str, str], tuple[Any, ...]] = {}
    for (side, name), owners in probes.items():
        if counters.reference_probes >= limits["max_reference_probes"]:
            probe_limited = True
            unprobed_records.update(record for _e, record, _d in owners)
            continue
        counters.reference_probes += 1
        allowance = reference_budget.remaining
        rows = (
            readers[side]
            .db.execute(
                "SELECT path FROM refs WHERE callee_name=? ORDER BY id LIMIT ?",
                (name, allowance + 1),
            )
            .fetchall()
        )
        complete = True
        if len(rows) > allowance:
            reference_budget.exhausted = True
            rows = rows[:allowance]
            complete = False
        reference_budget.take(len(rows))
        for path in sorted({r[0] for r in rows if vlim.is_test_path(r[0])}):
            found = assertions_of(side, path)
            if found is None:
                complete = False
                continue
            for assertion in found:
                if assertion["raw_target"] != name or assertion["relation"] not in relations:
                    continue
                entity = (
                    loaded[0 if side == "before" else 1]
                    .entities.get(path, {})
                    .get(assertion["source_entity_id"])
                )
                if entity is None or not vlim.is_test_entity(entity):
                    continue
                if not reference_budget.take(2):
                    complete = False
                    break
                proof = ev.get(readers[side].db, "evidence", assertion["evidence_id"])
                occurrence = ev.get(readers[side].db, "occurrence", proof["occurrence_id"])
                for via_id, record_id, distance in owners:
                    hit = (
                        occurrence["line"],
                        assertion["id"],
                        entity,
                        proof,
                        occurrence,
                        assertion,
                        distance + 1,
                    )
                    key = (entity["id"], record_id, via_id, side)
                    if key not in b_hits or hit[:2] < b_hits[key][:2]:
                        b_hits[key] = hit
        if not complete:
            unprobed_records.update(record for _e, record, _d in owners)
    for (_test_id, record_id, via_id, side), hit in sorted(b_hits.items()):
        _line, _aid, entity, proof, occurrence, assertion, distance = hit
        struct_state(entity).evidence.append(
            {
                "kind": "same_name_reference",
                "change_record_id": record_id,
                "snapshot_side": side,
                "snapshot_id": readers[side].snapshot_id,
                "generation_id": readers[side].generation_id,
                "distance": distance,
                "via_entity_id": via_id,
                "reference": {
                    "assertion_id": assertion["id"],
                    "evidence_id": proof["id"],
                    "occurrence_id": occurrence["id"],
                    "producer_id": proof["producer_id"],
                    "relation": assertion["relation"],
                    "resolution": assertion["resolution"],
                    "raw_target": assertion["raw_target"],
                    "line": occurrence["line"],
                },
                "test_file_after": _test_file_after(
                    observed, side, entity["path"], observed.side_hash(side, entity["path"])
                ),
            }
        )
        mapped_structural.setdefault(record_id, set()).add(entity["id"])

    # ---- recommendations ----
    observed_nodeids = {vlim.strip_parameters(t.nodeid) for t in observed_tests.values()}
    spans_by_id = {s["change_record_id"]: s for s in spans}
    weights = {s["change_record_id"]: vlim.CATEGORY_WEIGHT[s["category"]] for s in spans}
    max_evidence = limits["max_evidence_per_recommendation"]
    recommendations: list[dict[str, Any]] = []
    for tracked in observed_tests.values():
        pairs = []
        body_lines: set[tuple[str, str, int]] = set()
        mapped: set[str] = set()
        after_body = before_body = False
        for (record_id, side, source_index), items in sorted(tracked.groups.items()):
            span = spans_by_id[record_id]
            key, entry, body = _observed_entry(
                tracked,
                span,
                side,
                tracked.sources[source_index],
                items,
                readers,
                observed,
                limits["max_matched_lines"],
            )
            pairs.append((key, entry))
            mapped.add(record_id)
            body_lines.update((record_id, side, line) for line in body)
            after_body = after_body or (side == "after" and bool(body))
            before_body = before_body or (side == "before" and bool(body))
        emitted, total = _evidence_slice(pairs, max_evidence)
        basis_order = 0 if after_body else 1 if before_body else 2
        presence = {side: True for _r, side, _s in tracked.groups}
        recommendations.append(
            {
                "recommendation_id": vlim.recommendation_id(tracked.key),
                "action": "run_test",
                "basis": "observed_execution",
                "method": "coverage_context",
                "test": {
                    "identity_kind": "coverage_test",
                    "entity_id": tracked.entity_id,
                    "path": tracked.path,
                    "name": tracked.nodeid,
                    "selector": {"kind": "pytest_nodeid", "value": tracked.nodeid},
                },
                "observed_in": dict(sorted(presence.items())),
                "post_change_validation": "after" in presence,
                "present_in_after": _present_in_after(observed, tracked.path),
                "also_observed": False,
                "rank": {
                    "basis_order": basis_order,
                    "spans": len(mapped),
                    "body_lines": len(body_lines),
                    "category_weight": min(weights[r] for r in mapped),
                    "distance": 0,
                },
                "spans_total": len(mapped),
                "evidence_total": total,
                "evidence": emitted,
                "_mapped": sorted(mapped),
            }
        )
    for candidate in structurals.values():
        pairs = []
        for entry in candidate.evidence:
            order = 0 if entry["kind"] == "reverse_path" else 1
            pairs.append(
                (
                    (
                        vlim.SIDE_ORDER[entry["snapshot_side"]],
                        order,
                        entry["change_record_id"],
                        entry["distance"],
                    ),
                    entry,
                )
            )
        mapped = {entry["change_record_id"] for entry in candidate.evidence}
        emitted, total = _evidence_slice(pairs, max_evidence)
        has_reverse = any(e["kind"] == "reverse_path" for e in candidate.evidence)
        hint = vlim.selector_hint(candidate.entity)
        recommendations.append(
            {
                "recommendation_id": vlim.recommendation_id(candidate.key),
                "action": "run_test",
                "basis": "structural",
                "method": "reverse_path" if has_reverse else "same_name_reference",
                "test": {
                    "identity_kind": "code_entity",
                    "entity_id": candidate.entity["id"],
                    "path": candidate.entity["path"],
                    "name": candidate.entity["name"],
                    "selector": {"kind": "pytest_selector_hint", "value": hint},
                },
                "observed_in": {},
                "post_change_validation": False,
                "present_in_after": _present_in_after(observed, candidate.entity["path"]),
                "also_observed": hint in observed_nodeids,
                "rank": {
                    "basis_order": 3 if has_reverse else 4,
                    "spans": len(mapped),
                    "body_lines": 0,
                    "category_weight": min(weights[r] for r in mapped),
                    "distance": min(e["distance"] for e in candidate.evidence),
                },
                "spans_total": len(mapped),
                "evidence_total": total,
                "evidence": emitted,
                "_mapped": sorted(mapped),
            }
        )
    recommendations.sort(key=vlim.rank_key)
    chosen = recommendations[: limits["max_recommendations"]]
    by_span: dict[str, list[str]] = {}
    for rec in recommendations:
        for record_id in rec["_mapped"]:
            by_span.setdefault(record_id, []).append(rec["recommendation_id"])
    emitted_ids = {rec["recommendation_id"] for rec in chosen}
    for rec in recommendations:
        del rec["_mapped"]

    # ---- span and gap records ----
    expanded = {row["change_record_id"]: row for row in seed_rows if row["expansion"] == "expanded"}
    structural_state: dict[str, str] = {}
    for span in spans:
        record_id = span["change_record_id"]
        row = expanded.get(record_id)
        if record_id in mapped_structural:
            structural_state[record_id] = "found"
        elif (
            row is None
            or (row["frontier"] or {}).get("stop_reason") is not None
            or record_id in unprobed_records
        ):
            structural_state[record_id] = "incomplete"
        elif span["language"] != "python":
            structural_state[record_id] = "unsupported_language"
        else:
            structural_state[record_id] = "none"

    span_records = []
    gaps = []
    for span in spans:
        record_id = span["change_record_id"]
        states = coverage_state[record_id]
        structural = structural_state[record_id]
        if states["after"] == "executed":
            span_state = "observed_after"
        elif states["before"] == "executed":
            span_state = "observed_before_only"
        elif structural == "found":
            span_state = "structural_only"
        else:
            span_state = "gap"
        sides: dict[str, dict[str, Any] | None] = {}
        for side in vlim.SIDES:
            ref = span["refs"][side]
            if ref is None:
                sides[side] = None
                continue
            shape = geometry.of(side, ref) if span["language"] == "python" else None
            sides[side] = {
                "snapshot_id": ref["snapshot_id"],
                "generation_id": ref["generation_id"],
                "source_sha256": ref["source_sha256"],
                "line": ref["line"],
                "end_line": ref["end_line"],
                "header_end_line": shape["header_end_line"] if shape else ref["line"],
            }
        identity = {
            "change_record_id": record_id,
            "entity_id": span["entity_id"],
            "name": span["name"],
            "kind": span["kind"],
            "language": span["language"],
            "path": span["path"],
            "category": span["category"],
        }
        span_records.append(
            {
                **identity,
                "sides": sides,
                "coverage": {"before": states["before"], "after": states["after"]},
                "structural": structural,
                "recommendations": [i for i in by_span.get(record_id, []) if i in emitted_ids],
                "state": span_state,
            }
        )
        if span_state == "gap":
            gap_reasons = {
                vlim.COVERAGE_REASON[value]: True
                for value in states.values()
                if value in vlim.COVERAGE_REASON
            }
            gap_reasons[vlim.STRUCTURAL_REASON[structural]] = True
            gaps.append(
                {
                    **identity,
                    "action": "inspect",
                    "basis": "unsupported",
                    "lines": {
                        side: None
                        if span["refs"][side] is None
                        else [span["refs"][side]["line"], span["refs"][side]["end_line"]]
                        for side in vlim.SIDES
                    },
                    "coverage": {"before": states["before"], "after": states["after"]},
                    "structural": structural,
                    "reasons": dict(sorted(gap_reasons.items())),
                }
            )

    # ---- completeness ----
    reasons: dict[str, bool] = {}
    if len(all_spans) > len(spans):
        reasons["span_limit"] = True
    if len(eligible) > len(seeds):
        reasons["seed_limit"] = True
    if propagation_limited:
        reasons["impact_work_limit"] = True
    if change["partial_paths"]:
        reasons["partial_extraction"] = True
    if observed_budget.exhausted or any(s.limited for s in sources):
        reasons["observation_limit"] = True
    if probe_limited:
        reasons["reference_probe_limit"] = True
    if reference_budget.exhausted:
        reasons["reference_row_limit"] = True

    plan: dict[str, Any] = {
        "format": vlim.FORMAT,
        "status": "recommendations_only",
        "execution": "not_performed",
        "ci_policy": "advisory_only",
        "ranker": vlim.RANKER_ID,
        "test_rule": vlim.TEST_RULE_ID,
        "before": _generation(before),
        "after": _generation(after),
        "change_set_id": change["id"],
        "diff_work": diff_work,
        "impact": {
            "seeds_total": len(eligible),
            "seeds_expanded": len(expanded),
            "impacted_total": len(impacted),
            "relations": {name: True for name in request["relations"]},
            "confidence": {name: True for name in request["confidence"]},
            "frontiers": [
                {
                    "changed_entity_id": row["changed_entity_id"],
                    "snapshot_side": row["snapshot_side"],
                    "expansion": row["expansion"],
                    "outcome": (row["frontier"] or {}).get("outcome"),
                    "stop_reason": (row["frontier"] or {}).get("stop_reason"),
                    "commitment": (row["frontier"] or {}).get("commitment"),
                }
                for row in seed_rows
            ],
        },
        "effective_limits": {
            **{name: request[name] for name in lim.IMPACT_LIMITS if name != "max_boundary_probes"},
            "element_limit": settings.element_limit,
            "max_diff_entities": settings.diff_limits["max_entities"],
            "max_diff_occurrences": settings.diff_limits["max_occurrences"],
            "max_changed_files": settings.diff_limits["max_changed_files"],
            **limits,
        },
        "work": {
            "examined_edges": work.counts["examined_edges"],
            "visited_nodes": work.counts["visited_nodes"],
            "observation_rows_read": observed_budget.used,
            "stale_observation_rows": counters.stale_observation_rows,
            "reference_probes": counters.reference_probes,
            "reference_rows_read": reference_budget.used,
            "assertion_paths_loaded": counters.assertion_paths_loaded,
            "source_files_read": counters.source_files_read,
        },
        "coverage_sources": [
            {
                "source": source.kind,
                "generation_id": source.reader.generation_id,
                "snapshot_id": source.reader.snapshot_id,
                "generation_kind": source.generation_kind,
                "runs": source.runs,
                "files": observed.file_entries(source),
            }
            for source in sources
        ],
        "spans": span_records,
        "spans_total": len(all_spans),
        "recommendations": chosen,
        "recommendations_total": len(recommendations),
        "gaps": gaps[: limits["max_gaps"]],
        "gaps_total": len(gaps),
        "completeness": {
            "state": "incomplete" if reasons else "complete",
            "reasons": dict(sorted(reasons.items())),
        },
        "selection": {
            "spans": len(all_spans) > len(spans),
            "recommendations": len(recommendations) > len(chosen),
            "gaps": len(gaps) > limits["max_gaps"],
        },
        "limitations": {name: True for name in vlim.LIMITATIONS},
    }
    plan["plan_id"] = vlim.plan_id(plan)
    await asyncio.sleep(0)
    return plan


def _generation(reader: Any) -> dict[str, Any]:
    inputs = cov._manifest_json(reader, "generation_inputs") or {}
    return {
        "snapshot_id": reader.snapshot_id,
        "generation_id": reader.generation_id,
        "generation_kind": inputs.get("kind", "unknown"),
    }


# ---- surfaces -------------------------------------------------------------------


def run(
    before: Side,
    after: Side,
    arguments: dict[str, Any] | None = None,
    settings: impact.Settings | None = None,
    coverage_roots: Sequence[Path] = (),
) -> dict[str, Any]:
    """CLI entry: lease both sides and every external source read-only, plan, validate."""
    settings = settings or impact.Settings()
    request, limits = normalize(arguments, settings)
    roots = [Path(root) for root in coverage_roots]

    async def go() -> dict[str, Any]:
        with ExitStack() as stack:
            held = []
            for side in (before, after):
                graph = symbol_diff._open(stack, side)  # DiffUsageError -> CLI usage error
                try:
                    held.append(symbol_diff._lease(stack, graph))
                except DiffRejected as exc:
                    raise impact.rejected(exc.reason) from exc
            externals = cov.admit_externals(
                stack, after.repo, roots, held[0], held[1], limits["max_coverage_sources"]
            )
            return await collect(held[0], held[1], externals, request, limits, settings)

    plan = asyncio.run(go())
    validate_plan(plan)
    return plan


def canonical_bytes(plan: dict[str, Any]) -> bytes:
    """The exact bytes the CLI prints: canonical JSON plus one newline."""
    return ev.canonical(plan).encode("utf-8") + b"\n"
