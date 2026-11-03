"""ACI-S20: potential change impact through typed reverse paths.

One library function (`compute`) serves both the MCP `graph_query op=impact` and the
operator CLI `atlas-aci impact`. It composes the S19 change-set internals on two
already-held leases (no second acquisition, so no mixed generations), then reuses
the S12 `navigate` engine per seed under ONE shared allowance (S18 precedent).

The output is *potential* impact by static reachability only: never breakage,
never runtime observation. Same-name unresolved/ambiguous references are reported
as incomplete coverage; a short list is therefore never a complete one.

Coupling note: this module deliberately reaches into `atlas_aci.query.diff`
private helpers (`_open`, `_lease`, `_check_compatible`, `_Loaded`, `_build`) so that
`diff.py` stays byte-unchanged and S19 digests cannot churn. The G-DIFF test pins
the equality `impact change_set_id == diff.compare(...)["id"]`.

Read-only: nothing is written under either graph root or repository. Parsing in
the change-set phase is synchronous and blocks the event loop for its (operator
bounded) duration; see `docs/change-impact.md`.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import Any

from atlas_aci.codegraph import CodeGraph
from atlas_aci.config import DEFAULT_MAX_BOUND_FIELD_ELEMENTS, DEFAULT_MAX_RESPONSE_BYTES, Config
from atlas_aci.enforcement import ToolError
from atlas_aci.protocol.graph import INPUT_VALIDATOR, VERSION
from atlas_aci.query import diff as symbol_diff
from atlas_aci.query import impact_admission as admission
from atlas_aci.query import impact_limits as lim
from atlas_aci.query.diff import DiffRejected, DiffUsageError, Side
from atlas_aci.query.impact_work import ImpactWork
from atlas_aci.query.indexed import IndexedReader, QueryStopped
from atlas_aci.query.traversal import navigate
from atlas_aci.storage import evidence as ev

AMBIGUOUS_SQL = (
    "SELECT COUNT(*) FROM edges WHERE callee_name=? AND confidence='AMBIGUOUS' "
    "AND relation IN ({marks})"
)
ENTITY_PATHS_SQL = (
    "SELECT DISTINCT path FROM entities WHERE json_extract(payload,'$.kind')<>'file' "
    "AND json_extract(payload,'$.kind') NOT LIKE 'scip\\_%' ESCAPE '\\'"
)


class ImpactError(ToolError):
    """A typed impact failure. `reason` is a closed code; messages never carry paths."""

    def __init__(self, code: str, message: str, reason: str | None = None, retry_hint="none"):
        super().__init__(code, message, retry_hint)
        self.reason = reason


def rejected(reason: str) -> ImpactError:
    return ImpactError("SNAPSHOT_DIFF_REJECTED", "Snapshot diff rejected: " + reason, reason)


@dataclass(frozen=True)
class Settings:
    """Operator-owned bounds shared by the MCP server (via `Config`) and the CLI."""

    element_limit: int = DEFAULT_MAX_BOUND_FIELD_ELEMENTS
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    diff_limits: dict[str, int] = field(default_factory=lambda: dict(lim.DEFAULT_DIFF_LIMITS))

    @classmethod
    def from_config(cls, config: Config) -> Settings:
        return cls(
            element_limit=config.max_bound_field_elements,
            max_response_bytes=config.max_response_bytes,
            diff_limits={
                "max_entities": config.impact_max_diff_entities,
                "max_occurrences": config.impact_max_diff_occurrences,
                "max_changed_files": config.impact_max_changed_files,
            },
        )


# ---- request ------------------------------------------------------------------


def normalize(arguments: Any, *, max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES):
    """Validate against the published input schema and the closed limits."""
    if (
        not isinstance(arguments, dict)
        or arguments.get("op") != "impact"
        or not INPUT_VALIDATOR.is_valid(arguments)
    ):
        raise ToolError("INVALID_INPUT", "Impact arguments do not match the published schema.")
    request: dict[str, Any] = {}
    for name, (default, low, high) in lim.IMPACT_LIMITS.items():
        value = arguments.get(name, default)
        if type(value) is not int or not low <= value <= high:
            raise ToolError("INVALID_INPUT", "Invalid finite impact limit.")
        request[name] = value
    relations = arguments.get("relations", list(lim.DEFAULT_RELATIONS))
    confidence = arguments.get("confidence", list(lim.DEFAULT_CONFIDENCE))
    if (
        not isinstance(relations, list)
        or not relations
        or len(set(relations)) != len(relations)
        or not set(relations) <= set(lim.RELATIONS)
        or not isinstance(confidence, list)
        or not confidence
        or len(set(confidence)) != len(confidence)
        or not set(confidence) <= set(lim.DEFAULT_CONFIDENCE)
    ):
        raise ToolError("INVALID_INPUT", "Invalid impact propagation constraints.")
    budget = arguments.get("max_bytes", max_response_bytes)
    if type(budget) is not int or not 0 <= budget <= max_response_bytes:
        raise ToolError("INVALID_BUDGET", "Invalid impact result budget.")
    return {
        **request,
        "relations": sorted(relations),
        "confidence": sorted(confidence),
        "max_bytes": budget,
    }


# ---- change-set phase (unmetered, bounded by operator diff limits) --------------


def _observations(reader: Any) -> dict[str, dict[str, Any]]:
    row = reader.db.execute("SELECT value FROM manifest WHERE key='source_observations'").fetchone()
    records = json.loads(row[0]) if row else []
    return {r["path"]: r for r in records if r.get("type") == "observation"}


def _admit_diff(before: Any, after: Any, limits: dict[str, int]) -> dict[str, int]:
    """Bound the unmetered diff work BEFORE any entity load or parse."""
    counts = {}
    for name, reader in (("before", before), ("after", after)):
        counts[name + "_entities"] = reader.db.execute("SELECT COUNT(*) FROM entities").fetchone()[
            0
        ]
        counts[name + "_occurrences"] = reader.db.execute(
            "SELECT COUNT(*) FROM occurrences"
        ).fetchone()[0]
    if max(counts["before_entities"], counts["after_entities"]) > limits["max_entities"]:
        raise ImpactError(
            "IMPACT_DIFF_TOO_LARGE",
            "Change set exceeds the operator limit: diff_entity_limit",
            "diff_entity_limit",
        )
    if max(counts["before_occurrences"], counts["after_occurrences"]) > limits["max_occurrences"]:
        raise ImpactError(
            "IMPACT_DIFF_TOO_LARGE",
            "Change set exceeds the operator limit: diff_occurrence_limit",
            "diff_occurrence_limit",
        )
    old, new = _observations(before), _observations(after)

    def failed(records, path):
        record = records.get(path)
        return record is not None and record["state"] != "indexed"

    # Exactly the paths `diff._build` visits (entity-bearing or failed on either side),
    # including entity paths that have no observation record, and exactly those it parses.
    paths = {
        row[0] for reader in (before, after) for row in reader.db.execute(ENTITY_PATHS_SQL)
    } | {p for records in (old, new) for p in records if failed(records, p)}
    changed = 0
    for path in paths:
        one, two = failed(old, path), failed(new, path)
        if one and two:
            continue
        if one or two:
            changed += 1
            continue
        same = (
            path in old
            and path in new
            and old[path]["observed_source_sha256"] == new[path]["observed_source_sha256"]
        )
        changed += 0 if same else 1
    if changed > limits["max_changed_files"]:
        raise ImpactError(
            "IMPACT_DIFF_TOO_LARGE",
            "Change set exceeds the operator limit: changed_file_limit",
            "changed_file_limit",
        )
    return {**counts, "changed_files": changed}


def change_set(before: Any, after: Any, limits: dict[str, int]):
    """The S19 change set over two held leases plus the deterministic `diff_work`."""
    if before.atlas_dir == after.atlas_dir:
        raise rejected("same_namespace")
    try:
        compatibility = symbol_diff._check_compatible(before, after)
    except DiffRejected as exc:
        raise rejected(exc.reason) from exc
    try:
        diff_work = _admit_diff(before, after, limits)
        loaded = [
            symbol_diff._Loaded(
                reader,
                reader.snapshot_id,
                reader.generation_id,
                reader._source_lease.manifest["context"],
            )
            for reader in (before, after)
        ]
        for side in loaded:
            side.load()
        result = symbol_diff._build(loaded[0], loaded[1], compatibility, symbol_diff.FileAnalysis)
    except (sqlite3.Error, OSError) as exc:
        raise rejected("index_unavailable") from exc
    diff_work["parsed_files"] = len(loaded[0]._analysis) + len(loaded[1]._analysis)
    return result, loaded, diff_work


# ---- seeds ----------------------------------------------------------------------


def _seed_side(record: dict[str, Any], new_entities: dict[str, dict[str, Any]]) -> str | None:
    category = record["category"]
    if category in {"format", "location"}:
        return None
    if category == "removed":
        return "before"
    if category in {"added", "signature", "body"}:
        return "after"
    one, two = record["before"], record["after"]  # unknown
    if (one is None) != (two is None):
        return "before" if one is not None else "after"
    if one is None:
        return "after" if record["entity_id"] in new_entities.get(record["path"], {}) else "before"
    return "after"


def select_seeds(changes: list[dict[str, Any]], new: Any) -> list[dict[str, Any]]:
    eligible: list[dict[str, Any]] = []
    for record in changes:
        side = _seed_side(record, new.entities)
        if side is None:
            continue
        eligible.append({"record": record, "side": side})
    eligible.sort(
        key=lambda s: (
            lim.CATEGORY_WEIGHT[s["record"]["category"]],
            lim.SIDES.index(s["side"]),
            s["record"]["entity_id"],
        )
    )
    return eligible


# ---- propagation ----------------------------------------------------------------


def _paths(nav: dict[str, Any], seed_id: str):
    """Rebuild navigate's own BFS tree: the first hop reaching a fresh child is its parent."""
    nodes = {n["id"]: n for n in nav["nodes"]}
    parent: dict[str, dict[str, Any]] = {}
    for hop in nav["edges"]:
        assertion = hop["assertion"]
        child = assertion["source_entity_id"]
        if child == seed_id or child in parent:
            continue
        parent[child] = hop
    for child in parent:
        hops: list[dict[str, Any]] = []
        chain = [child]
        cursor = child
        while cursor != seed_id:
            hop = parent[cursor]
            hops.append(hop)
            cursor = hop["assertion"]["target_entity_id"]
            chain.append(cursor)
            if len(chain) > len(parent) + 1:
                raise ValueError("Impact parent chain does not reach its seed")
        chain.reverse()
        hops.reverse()
        yield child, [nodes[i] for i in chain], hops


async def propagate(readers, seeds, request, work, changed_ids):
    """Per-seed reverse traversal under one allowance; returns (impacted, seed rows, stops)."""
    best: dict[str, dict[str, Any]] = {}
    reached: dict[str, set[str]] = {}
    rows = []
    limited = False
    for seed in seeds:
        record, side = seed["record"], seed["side"]
        reader = readers[side]
        row = {
            "changed_entity_id": record["entity_id"],
            "change_record_id": record["id"],
            "category": record["category"],
            "snapshot_side": side,
            "snapshot_id": reader.snapshot_id,
            "generation_id": reader.generation_id,
            "name": record["name"],
            "kind": record["kind"],
            "language": record["language"],
            "path": record["path"],
            "expansion": "not_expanded",
            "frontier": None,
        }
        rows.append(row)
        if work.stop_reason is not None or work.remaining("visited_nodes") < 1:
            limited = True
            continue
        try:
            milliseconds = work.remaining_ms()
        except QueryStopped:
            limited = True
            continue
        nav = await navigate(
            reader,
            seed={"entity_id": record["entity_id"]},
            operation="neighborhood",
            direction="incoming",
            relations=request["relations"],
            confidence=request["confidence"],
            max_examined_edges=work.remaining("examined_edges"),
            max_visited_nodes=work.remaining("visited_nodes"),
            max_depth=request["max_depth"],
            deadline_ms=milliseconds,
            accounting=work,
            admit=None,
        )
        work.add_visited(nav["work"]["visited_nodes"])
        row["expansion"] = "expanded"
        row["frontier"] = {
            "outcome": nav["outcome"],
            "stop_reason": nav["stop_reason"],
            "commitment": nav["frontier"]["commitment"],
            "queued_nodes": nav["frontier"]["queued_nodes"],
            "depth": nav["frontier"]["depth"],
        }
        limited = limited or nav["stop_reason"] is not None
        for child, nodes, hops in _paths(nav, record["entity_id"]):
            item = {
                "status": "potentially_affected",
                "basis": "static_reachability",
                "entity": nodes[-1],
                "also_changed": child in changed_ids,
                "snapshot_side": side,
                "snapshot_id": reader.snapshot_id,
                "generation_id": reader.generation_id,
                "changed_entity_id": record["entity_id"],
                "change_category": record["category"],
                "change_record_id": record["id"],
                "distance": len(hops),
                "reached_from_seeds": 0,
                "rank": {
                    "distance": len(hops),
                    "category_weight": lim.CATEGORY_WEIGHT[record["category"]],
                    "side_order": lim.SIDES.index(side),
                },
                "path": {"nodes": nodes, "hops": hops},
            }
            reached.setdefault(child, set()).add(record["entity_id"])
            if child not in best or lim.rank_key(item) < lim.rank_key(best[child]):
                best[child] = item
    impacted = sorted(best.values(), key=lim.rank_key)
    for item in impacted:
        item["reached_from_seeds"] = len(reached[item["entity"]["id"]])
    return impacted, rows, limited


# ---- boundaries -----------------------------------------------------------------


def _probe_relations(relations: list[str]) -> tuple[str, ...]:
    chosen = set(relations) | {lim.PROBE_RELATION_EXTRA}
    if "construct" in relations:
        chosen.add("call")
    return tuple(sorted(chosen))


def probe_boundaries(readers, entities, request, work):
    """Name-based, metered probes. Returns (boundaries, unprobed, stop, work_limited)."""
    relations = _probe_relations(request["relations"])
    marks = ",".join("?" for _ in relations)
    results: dict[tuple[str, str], tuple[int | None, int]] = {}
    stop = None
    work_limited = False
    with ExitStack() as stack:
        queries: dict[str, IndexedReader] = {}
        try:
            for side in ("before", "after"):
                if any(s == side for s, _ in entities):
                    queries[side] = stack.enter_context(
                        IndexedReader(
                            readers[side],
                            max_examined_edges=0,
                            deadline_ms=work.remaining_ms(),
                            accounting=work,
                        )
                    )
        except QueryStopped as exc:
            stop, work_limited = exc.reason, True
            queries.clear()
        if stop is None:
            for side, entity in entities:
                key = (side, entity["name"])
                if key in results:
                    continue
                if work.counts["boundary_probes"] >= work.limits["boundary_probes"]:
                    stop = "probe_limit"
                    break
                work.counts["boundary_probes"] += 1
                query, reader = queries[side], readers[side]
                try:
                    work.before_sql("boundary")
                    work.before_sql("boundary")  # unresolved_ref_count: manifest + count
                    unresolved = reader.unresolved_ref_count(entity["name"], relations)
                    work.before_sql("boundary")
                    ambiguous = reader.db.execute(
                        AMBIGUOUS_SQL.format(marks=marks), (entity["name"], *relations)
                    ).fetchone()[0]
                except QueryStopped as exc:
                    stop, work_limited = exc.reason, True
                    break
                except sqlite3.OperationalError as exc:
                    interrupted = getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_INTERRUPT
                    if interrupted and (query.shared_stop_reason or query.deadline_triggered):
                        stop = query.shared_stop_reason or "deadline"
                        work_limited = True
                        break
                    raise
                results[key] = (unresolved, ambiguous)
                work.counts["boundary_probes_completed"] += 1
    boundaries = []
    unprobed = False
    seen = set()
    for side, entity in entities:
        if (side, entity["id"]) in seen:
            continue
        seen.add((side, entity["id"]))
        found = results.get((side, entity["name"]))
        if found is None:
            unprobed = True
            continue
        unresolved, ambiguous = found
        reasons = {}
        if unresolved is not None and unresolved > 0:
            reasons["unresolved_reference"] = True
        if ambiguous > 0:
            reasons["ambiguous_reference"] = True
        if unresolved is None:
            reasons["reference_coverage_unknown"] = True
        if reasons:
            boundaries.append(
                {
                    "entity_id": entity["id"],
                    "name": entity["name"],
                    "language": entity["language"],
                    "snapshot_side": side,
                    "unresolved_reference_count": unresolved,
                    "ambiguous_reference_count": ambiguous,
                    "reference_coverage": "unknown" if unresolved is None else "known",
                    "basis": "same_name_reference",
                    "reasons": reasons,
                }
            )
    return boundaries, unprobed, stop, work_limited


# ---- compute --------------------------------------------------------------------


async def compute(
    before: Any,
    after: Any,
    request: dict[str, Any],
    *,
    element_limit: int,
    max_bytes: int,
    diff_limits: dict[str, int],
) -> dict[str, Any]:
    """Impact envelope for two held leases. Never closes either reader."""
    change, loaded, diff_work = change_set(before, after, diff_limits)
    # The propagation deadline starts only after the (unmetered) change set exists.
    work = ImpactWork(**{name: request[name] for name in lim.IMPACT_LIMITS})
    readers = {"before": before, "after": after}
    changed_ids = {record["entity_id"] for record in change["changes"]}
    eligible = select_seeds(change["changes"], loaded[1])
    effective = min(request["max_seeds"], element_limit)
    seeds = eligible[:effective]
    categories = {name: 0 for name in lim.SEED_CATEGORIES}
    for seed in eligible:
        categories[seed["record"]["category"]] += 1
    impacted, rows, limited = await propagate(readers, seeds, request, work, changed_ids)

    entities = []
    for row in rows:
        entities.append((row["snapshot_side"], {**row, "id": row["changed_entity_id"]}))
    for item in impacted:
        entity = item["entity"]
        entities.append((item["snapshot_side"], entity))
    boundaries, unprobed, probe_stop, probe_limited = probe_boundaries(
        readers, entities, request, work
    )

    reasons: dict[str, bool] = {}
    for boundary in boundaries:
        for name in boundary["reasons"]:
            reasons[name] = True
    if limited or probe_limited:
        reasons["work_limit"] = True
    if len(eligible) > len(seeds):
        reasons["seed_limit"] = True
    if unprobed:
        reasons["boundary_probe_limit"] = True
    if change["partial_paths"]:
        reasons["partial_extraction"] = True

    evidence_status = admission.evidence_rank(
        before._lease_evidence_status, after._lease_evidence_status
    )
    base = {
        "projection": "change_impact",
        "status": "potential_impact",
        "basis": "static_reachability",
        "runtime_observation": "not_performed",
        "before": {"snapshot_id": before.snapshot_id, "generation_id": before.generation_id},
        "after": {"snapshot_id": after.snapshot_id, "generation_id": after.generation_id},
        "change_set_id": change["id"],
        "change_summary": change["summary"],
        "propagation": {
            "direction": "incoming",
            "relations": {name: True for name in request["relations"]},
            "confidence": {name: True for name in request["confidence"]},
            "seed_categories": categories,
            "ranker": lim.RANKER_ID,
        },
        "effective_limits": {
            **{name: request[name] for name in lim.IMPACT_LIMITS},
            "max_bytes": max_bytes,
            "element_limit": element_limit,
            "max_diff_entities": diff_limits["max_entities"],
            "max_diff_occurrences": diff_limits["max_occurrences"],
            "max_changed_files": diff_limits["max_changed_files"],
        },
        "work": {
            "examined_edges": work.counts["examined_edges"],
            "visited_nodes": work.counts["visited_nodes"],
            "sql_queries": work.counts["sql_queries"],
            "vm_operations_reserved": work.counts["vm_operations_reserved"],
            "vm_progress_callbacks": work.counts["vm_progress_callbacks"],
            "boundary_probes": work.counts["boundary_probes"],
            "boundary_probes_completed": work.counts["boundary_probes_completed"],
            "boundary_probe_stop": probe_stop,
        },
        "diff_work": diff_work,
        "seeds": rows,
        "seeds_total": len(eligible),
        "impacted_total": len(impacted),
        "coverage": {
            "state": "incomplete" if reasons else "complete",
            "reasons": reasons,
            "boundaries_total": len(boundaries),
        },
        "limitations": {name: True for name in lim.LIMITATIONS},
    }
    atoms = [("impacted", item) for item in impacted] + [("boundaries", b) for b in boundaries]
    chosen_impacted, chosen_boundaries, reason = admission.prefix(
        base,
        atoms,
        budget=max_bytes,
        element_limit=element_limit,
        generation_id=after.generation_id,
        snapshot_id=after.snapshot_id,
    )
    data = admission.assemble(base, chosen_impacted, chosen_boundaries, reason)
    result = admission.frame(
        data,
        element_limit=element_limit,
        generation_id=after.generation_id,
        snapshot_id=after.snapshot_id,
        evidence_status=evidence_status,
    )
    from atlas_aci.protocol.results import result_bytes, sdk_result

    if len(result_bytes(sdk_result(result, error=False, structured=True))) > max_bytes:
        raise ToolError("INVALID_BUDGET", "Final impact result exceeds the result budget.")
    await asyncio.sleep(0)
    return result


# ---- surfaces -------------------------------------------------------------------


def before_side(config: Config) -> Side:
    if config.before_repo is None and config.before_graph_root is None:
        raise ToolError(
            "IMPACT_UNCONFIGURED",
            "Impact needs an operator-configured before snapshot (serve --before-graph-root).",
            "different_tool",
        )
    return Side(config.before_repo or config.repo, config.before_graph_root)


def open_side(stack: ExitStack, side: Side) -> Any:
    """Open and lease one snapshot; every failure maps to a closed, path-free reason."""
    try:
        graph = symbol_diff._open(stack, side)
    except (DiffUsageError, ValueError, OSError, sqlite3.Error) as exc:
        raise rejected("index_unavailable") from exc
    try:
        return symbol_diff._lease(stack, graph)
    except DiffRejected as exc:
        raise rejected(exc.reason) from exc
    except (ValueError, OSError, sqlite3.Error) as exc:
        raise rejected("index_unavailable") from exc


def validate_configuration(config: Config) -> None:
    """Startup check: a read-only CodeGraph per side and two distinct namespaces."""
    if config.before_repo is None and config.before_graph_root is None:
        return
    side = before_side(config)
    graphs = []
    try:
        for repo, root in ((side.repo, side.graph_root), (config.repo, config.graph_root)):
            graphs.append(CodeGraph(repo, read_only=True, graph_root=root))
        if graphs[0].atlas_dir == graphs[1].atlas_dir:
            raise ValueError("The before and after snapshots resolve to the same graph namespace.")
    finally:
        for graph in graphs:
            graph.close()


async def serve(after_reader: Any, arguments: dict[str, Any], config: Config) -> dict[str, Any]:
    """MCP entry: the served lease is the after side; the before side is operator-set."""
    request = normalize(arguments, max_response_bytes=config.max_response_bytes)
    side = before_side(config)
    settings = Settings.from_config(config)
    with ExitStack() as stack:
        before_reader = open_side(stack, side)
        expected = arguments.get("expected_before_generation_id")
        if expected is not None and expected != before_reader.generation_id:
            raise ToolError("SNAPSHOT_MISMATCH", "Requested before generation does not match.")
        return await compute(
            before_reader,
            after_reader,
            request,
            element_limit=settings.element_limit,
            max_bytes=request["max_bytes"],
            diff_limits=settings.diff_limits,
        )


def run(
    before: Side,
    after: Side,
    arguments: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """CLI entry: open both sides read-only, compute, validate; return the envelope."""
    from atlas_aci.protocol.graph import validate_impact

    settings = settings or Settings()
    request = normalize(
        {"schema_version": VERSION, "op": "impact", **(arguments or {})},
        max_response_bytes=settings.max_response_bytes,
    )

    async def go() -> dict[str, Any]:
        with ExitStack() as stack:
            readers = []
            for side in (before, after):
                graph = symbol_diff._open(stack, side)  # DiffUsageError -> CLI usage error
                try:
                    readers.append(symbol_diff._lease(stack, graph))
                except DiffRejected as exc:
                    raise rejected(exc.reason) from exc
            return await compute(
                readers[0],
                readers[1],
                request,
                element_limit=settings.element_limit,
                max_bytes=request["max_bytes"],
                diff_limits=settings.diff_limits,
            )

    envelope = asyncio.run(go())
    validate_impact(envelope, "impact", settings.element_limit)
    return envelope


def canonical_bytes(envelope: dict[str, Any]) -> bytes:
    """The exact bytes the CLI prints: canonical JSON plus one newline."""
    return ev.canonical(envelope).encode("utf-8") + b"\n"
