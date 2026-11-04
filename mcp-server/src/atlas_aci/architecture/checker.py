"""ACI-S31: evaluate forbidden-import rules over one leased, read-only graph snapshot.

A file-level breadth-first search over `import` assertions (resolution EXTRACTED or INFERRED
with a target entity). The source of an edge is `assertions.path` (the importing file); the
destination is the path of the target entity. Every other import fact met in a visited file is
a boundary and makes the result `unknown`; a bound that stops a search makes it
`incomplete_due_to_budget`. Bounds are counts only: there is no clock, so a report depends
only on the snapshot, the rule bytes, the checker version and the bounds.

Private symbols reused (a rename fails loudly, and the tests exercise all of them):
  - `atlas_aci.query.diff._open` and `_lease` (the authenticated read-only graph lease);
  - records are decoded locally with the public `ev.validate_record` plus the identity and
    coverage_test checks, and the tests assert equality with `IndexedReader._record`.
Hop provenance is the S12/S20 `canonical_assertion_proof` from `traversal.hydrate`.
`IndexedReader` is never instantiated (its deadline cannot be disarmed) and `navigate` is not
used: it walks entity ids, so a file-level import chain would dead-end as a false `no_path`.

Cost, all uncharged by the edge counters: the adjacency query decodes every assertion stored
under a visited file path (calls and references included, not only imports), because the
`relation` test is a residual filter on `idx_assertions_path`. Only
`(id, target_entity_id, resolution)` tuples are cached per path.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from atlas_aci.architecture import rules as rule_module
from atlas_aci.architecture.rules import Rule, RuleSet
from atlas_aci.query import diff as symbol_diff
from atlas_aci.query import traversal
from atlas_aci.query.diff import DiffRejected, DiffUsageError, Side
from atlas_aci.storage import evidence as ev

FORMAT = "atlas-architecture-report.v1"
CHECKER = "atlas-architecture-check.v1"
OUTCOMES = ("violated", "incomplete_due_to_budget", "unknown", "satisfied_within_scope")
REJECTIONS = ("index_unavailable", "source_snapshot_unavailable", "index_upgrade_required")
STOP_REASONS = (
    "depth_limit",
    "visited_node_limit",
    "examined_edge_limit",
    "total_examined_edge_limit",
)
BUDGET_REASONS = (*STOP_REASONS, "source_limit")
BOUNDARY_KINDS = (
    "unresolved_import",
    "dynamic_import_call",
    "import_language_unsupported",
    "file_not_indexed",
)
UNKNOWN_REASONS = (*BOUNDARY_KINDS, "empty_source_selection", "empty_target_selection")
REASONS = (*BUDGET_REASONS, *UNKNOWN_REASONS)
EDGE_RESOLUTIONS = ("EXTRACTED", "INFERRED")
IMPORT_LANGUAGES = ("python",)
DYNAMIC_IMPORT_CALLEES = (
    "__import__",
    "exec",
    "exec_module",
    "import_module",
    "load_module",
    "module_from_spec",
    "reload",
    "run_module",
    "run_path",
    "spec_from_file_location",
)
SCHEMA_EPOCHS = frozenset({8, 9})
LIMITS: dict[str, tuple[int, int, int]] = {
    "max_depth": (16, 1, 32),
    "max_visited_nodes": (1000, 1, 1000),
    "max_examined_edges": (10000, 0, 10000),
    "max_total_examined_edges": (200000, 0, 2000000),
    "max_sources_per_rule": (256, 1, 4096),
    "max_counterexamples_per_rule": (16, 1, 64),
    "max_boundaries_per_rule": (32, 0, 256),
}
LIMITATIONS = {
    name: True
    for name in (
        "dynamic_imports_are_not_modeled",
        "dynamic_import_probe_matches_callee_names_only",
        "satisfied_within_scope_is_not_a_proof_of_absence",
        "python_relative_imports_only",
        "absolute_imports_are_unresolved_boundaries",
        "import_targets_must_be_module_level_definitions",
        "import_edges_are_explicit_statements_not_package_initialization",
        "non_python_imports_are_not_modeled",
        "selectors_match_observed_source_files_only",
        "depth_limit_stop_is_conservative",
        "reports_do_not_enforce_policy",
        "not_exposed_over_mcp",
    )
}

OBSERVATIONS_SQL = "SELECT value FROM manifest WHERE key='source_observations'"
GENERATION_SQL = "SELECT value FROM manifest WHERE key='generation_inputs'"
ADJACENCY_SQL = (
    "SELECT id,payload FROM assertions INDEXED BY idx_assertions_path "
    "WHERE path=? AND json_extract(payload,'$.relation')='import' ORDER BY id LIMIT 10001"
)
DYNAMIC_SQL = (
    "SELECT id,payload FROM assertions INDEXED BY idx_assertions_path WHERE path=? "
    "AND json_extract(payload,'$.relation')='call' "
    "AND json_extract(payload,'$.raw_target') IN ("
    + ",".join("?" for _ in DYNAMIC_IMPORT_CALLEES)
    + ") ORDER BY id LIMIT 1"
)
RECORD_SQL = {
    kind: "SELECT id,payload FROM " + table + " WHERE id=?" for kind, table in ev.TABLES.items()
}


class ArchitectureRejected(ValueError):
    """The snapshot cannot be checked; `reason` is a closed code."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class ArchitectureUsageError(ValueError):
    """A bound is out of range; `limit` names it."""

    def __init__(self, limit: str):
        super().__init__("Invalid architecture check limit: " + limit)
        self.limit = limit


def normalize_limits(values: Mapping[str, Any] | None = None) -> dict[str, int]:
    """Defaults for absent or None entries; any unknown or out-of-range entry is rejected."""
    given = dict(values or {})
    for name in given:
        if name not in LIMITS:
            raise ArchitectureUsageError(str(name))
    result = {}
    for name, (default, low, high) in LIMITS.items():
        value = given.get(name)
        value = default if value is None else value
        if type(value) is not int or not low <= value <= high:
            raise ArchitectureUsageError(name)
        result[name] = value
    return result


def decode(row: Any, kind: str) -> dict[str, Any] | None:
    """Validate one stored row exactly as the indexed reader does (None for coverage tests)."""
    if row is None:
        return None
    record = json.loads(row[1])
    ev.validate_record(record)
    ev.require(record["type"] == kind and record["id"] == row[0], "indexed record identity")
    if kind == "entity" and record["kind"] == ev.COVERAGE_TEST_KIND:
        return None
    return record


def open_snapshot(stack: ExitStack, side: Side) -> Any:
    """Lease one complete graph namespace read-only, or raise a typed rejection."""
    graph = symbol_diff._open(stack, side)  # DiffUsageError passes through
    try:
        reader = symbol_diff._lease(stack, graph)
    except DiffRejected as exc:
        raise ArchitectureRejected(exc.reason) from exc
    if (
        reader._closed
        or not reader._validated_lease
        or reader._lease_schema_epoch not in SCHEMA_EPOCHS
    ):
        raise ArchitectureRejected("index_upgrade_required")
    if reader.snapshot_id is None:
        raise ArchitectureRejected("source_snapshot_unavailable")
    return reader


def canonical_bytes(report: dict[str, Any]) -> bytes:
    """The exact bytes the CLI prints: canonical JSON plus one newline."""
    return ev.canonical(report).encode("utf-8") + b"\n"


def report_id(report: dict[str, Any]) -> str:
    body = {key: value for key, value in report.items() if key != "report_id"}
    return hashlib.sha256((FORMAT + ":" + ev.canonical(body)).encode("utf-8")).hexdigest()


class _Records:
    """Clock-free adapter giving `traversal.hydrate` its `.reader` and `.record(...)`."""

    def __init__(self, owner: _Run):
        self.reader = owner.reader
        self._owner = owner

    def record(self, kind: str, identifier: str, *, endpoint: bool = False) -> Any:
        return self._owner.record(kind, identifier)


@dataclass
class _Trace:
    source: str
    visited: set[str] = field(default_factory=set)
    parent: dict[str, tuple[str, str]] = field(default_factory=dict)
    found: dict[str, list[tuple[str, str, str]]] = field(default_factory=dict)
    edges: int = 0
    depth: int = 0
    stop: str | None = None

    def chain(self, node: str) -> list[tuple[str, str, str]]:
        steps: list[tuple[str, str, str]] = []
        while node in self.parent:
            before, assertion_id = self.parent[node]
            steps.append((before, assertion_id, node))
            node = before
        steps.reverse()
        return steps


class _Run:
    def __init__(self, reader: Any, limits: dict[str, int]):
        self.reader = reader
        self.db = reader.db
        self.limits = limits
        self.total = 0
        self.work = {
            "adjacency_queries": 0,
            "probe_queries": 0,
            "entity_queries": 0,
            "file_entity_queries": 0,
            "hydration_queries": 0,
            "traversals": 0,
        }
        self.records = _Records(self)
        self.observations = self._observations()
        self.universe = sorted(self.observations)
        self._adjacency: dict[str, list[tuple[str, str | None, str]]] = {}
        self._probe: dict[str, str | None] = {}
        self._paths: dict[str, str] = {}
        self._files: dict[str, dict[str, Any]] = {}

    # ---- reads ----------------------------------------------------------------------
    def _observations(self) -> dict[str, dict[str, Any]]:
        row = self.db.execute(OBSERVATIONS_SQL).fetchone()
        ev.require(row is not None, "source observations")
        found: dict[str, dict[str, Any]] = {}
        for item in json.loads(row[0]):
            # Only `observation` records: `history` records carry stale facts and no state.
            if item.get("type") == "observation":
                found[item["path"]] = {
                    "language": item["language"],
                    "state": item["state"],
                    "reason": item["reason"],
                    "observed_source_sha256": item["observed_source_sha256"],
                }
        return found

    def scope(self) -> dict[str, Any]:
        row = self.db.execute(GENERATION_SQL).fetchone()
        ev.require(row is not None, "generation inputs")
        configuration = json.loads(row[0]).get("configuration")
        ev.require(isinstance(configuration, dict), "generation configuration")
        languages = configuration.get("languages")
        skips = configuration.get("skip_patterns")
        ev.require(
            isinstance(languages, list)
            and isinstance(skips, list)
            and all(isinstance(v, str) for v in (*languages, *skips)),
            "generation scope",
        )
        return {"languages": sorted(languages), "skip_patterns": sorted(skips)}

    def record(self, kind: str, identifier: str) -> dict[str, Any] | None:
        self.work["hydration_queries"] += 1
        return decode(self.db.execute(RECORD_SQL[kind], (identifier,)).fetchone(), kind)

    def entity_path(self, entity_id: str) -> str:
        if entity_id not in self._paths:
            self.work["entity_queries"] += 1
            record = decode(
                self.db.execute(RECORD_SQL["entity"], (entity_id,)).fetchone(), "entity"
            )
            ev.require(record is not None, "import endpoint entity")
            assert record is not None
            self._paths[entity_id] = record["path"]
        return self._paths[entity_id]

    def file_entity(self, path: str) -> dict[str, Any]:
        if path not in self._files:
            self.work["file_entity_queries"] += 1
            computed = ev.file_entity(path, self.observations[path]["language"])["id"]
            record = decode(self.db.execute(RECORD_SQL["entity"], (computed,)).fetchone(), "entity")
            ev.require(
                record is not None and record["kind"] == "file" and record["path"] == path,
                "file entity",
            )
            assert record is not None
            self._files[path] = record
        return self._files[path]

    def adjacency(self, path: str) -> list[tuple[str, str | None, str]]:
        if path not in self._adjacency:
            self.work["adjacency_queries"] += 1
            rows = []
            for identifier, payload in self.db.execute(ADJACENCY_SQL, (path,)):
                try:
                    item = json.loads(payload)
                    rows.append((identifier, item["target_entity_id"], item["resolution"]))
                except (KeyError, TypeError) as exc:
                    raise ValueError("Invalid evidence: malformed import assertion") from exc
            self._adjacency[path] = rows
        return self._adjacency[path]

    def dynamic_call(self, path: str) -> str | None:
        if path not in self._probe:
            self.work["probe_queries"] += 1
            row = self.db.execute(DYNAMIC_SQL, (path, *DYNAMIC_IMPORT_CALLEES)).fetchone()
            self._probe[path] = None if row is None else row[0]
        return self._probe[path]

    def classify(self, path: str) -> str | None:
        """The boundary kind that makes `path` non-traversable, or None if it is admissible."""
        item = self.observations.get(path)
        if item is None or item["state"] != "indexed":
            return "file_not_indexed"
        if item["language"] not in IMPORT_LANGUAGES:
            return "import_language_unsupported"
        return None

    # ---- traversal ------------------------------------------------------------------
    def traverse(
        self, source: str, targets: set[str], boundaries: set[tuple[str, str, str]]
    ) -> _Trace:
        trace = _Trace(source)
        self.work["traversals"] += 1
        kind = self.classify(source)
        if kind is not None:
            boundaries.add((kind, source, ""))
            return trace
        cap = self.limits
        trace.visited = {source}
        level = [source]
        while level:
            at_limit = trace.depth >= cap["max_depth"]
            following: set[str] = set()
            for current in level:
                call = self.dynamic_call(current)
                if call is not None:
                    boundaries.add(("dynamic_import_call", current, call))
                for identifier, target_id, resolution in self.adjacency(current):
                    if trace.edges >= cap["max_examined_edges"]:
                        trace.stop = "examined_edge_limit"
                        return trace
                    if self.total >= cap["max_total_examined_edges"]:
                        trace.stop = "total_examined_edge_limit"
                        return trace
                    trace.edges += 1
                    self.total += 1
                    if resolution not in EDGE_RESOLUTIONS or target_id is None:
                        boundaries.add(("unresolved_import", current, identifier))
                        continue
                    destination = self.entity_path(target_id)
                    refusal = self.classify(destination)
                    if refusal is not None:
                        boundaries.add((refusal, destination, identifier))
                        continue
                    if at_limit:
                        # Exact depth check: a frontier file only stops the search when it
                        # has an admissible import that would still change the result.
                        fresh = destination not in trace.visited
                        if fresh or (destination in targets and destination not in trace.found):
                            trace.stop = "depth_limit"
                            return trace
                        continue
                    if destination in targets and destination not in trace.found:
                        trace.found[destination] = [
                            *trace.chain(current),
                            (current, identifier, destination),
                        ]
                    if destination in trace.visited:
                        continue
                    if len(trace.visited) >= cap["max_visited_nodes"]:
                        trace.stop = "visited_node_limit"
                        return trace
                    trace.visited.add(destination)
                    trace.parent[destination] = (current, identifier)
                    following.add(destination)
            if at_limit:
                break
            level = sorted(following)
            trace.depth += 1
        return trace

    # ---- output ---------------------------------------------------------------------
    def witness(
        self, source: str, target: str, edges: list[tuple[str, str, str]]
    ) -> dict[str, Any]:
        paths = [edges[0][0], *(edge[2] for edge in edges)]
        nodes = [traversal.identity(self.file_entity(path)) for path in paths]
        hops = []
        for before, identifier, after in edges:
            assertion = self.record("assertion", identifier)
            ev.require(assertion is not None, "witness assertion")
            assert assertion is not None
            hop = traversal.hydrate(self.records, assertion, "outgoing")
            ev.require(self.entity_path(assertion["source_entity_id"]) == before, "hop source")
            ev.require(self.entity_path(assertion["target_entity_id"]) == after, "hop target")
            ev.require(hop["occurrence"]["path"] == before, "hop occurrence")
            hops.append(hop)
        return {
            "source_path": source,
            "target_path": target,
            "length": len(edges),
            "path": {"nodes": nodes, "hops": hops},
        }

    def boundary(self, key: tuple[str, str, str]) -> dict[str, Any]:
        kind, path, identifier = key
        item = self.observations.get(path)
        result: dict[str, Any] = {
            "kind": kind,
            "path": path,
            "language": item["language"] if item else None,
            "observation_state": item["state"] if item else None,
            "observation_reason": item["reason"] if item else None,
            "assertion_id": identifier or None,
            "evidence_id": None,
            "occurrence_id": None,
            "line": None,
            "relation": None,
            "resolution": None,
            "unresolved_reason": None,
            "raw_target": None,
        }
        if identifier:
            assertion = self.record("assertion", identifier)
            ev.require(assertion is not None, "boundary assertion")
            assert assertion is not None
            evidence = self.record("evidence", assertion["evidence_id"])
            ev.require(evidence is not None, "boundary evidence")
            assert evidence is not None
            occurrence = self.record("occurrence", evidence["occurrence_id"])
            ev.require(occurrence is not None, "boundary occurrence")
            assert occurrence is not None
            result.update(
                evidence_id=evidence["id"],
                occurrence_id=occurrence["id"],
                line=occurrence["line"],
                relation=assertion["relation"],
                resolution=assertion["resolution"],
                unresolved_reason=assertion["unresolved_reason"],
                raw_target=assertion["raw_target"],
            )
        return result

    def evaluate(self, rule: Rule) -> dict[str, Any]:
        cap = self.limits
        sources = sorted({p for s in rule.sources for p in rule_module.select(self.universe, s)})
        targets = {p for s in rule.targets for p in rule_module.select(self.universe, s)}
        reasons: set[str] = set()
        sources_total = len(sources)
        if not sources:
            reasons.add("empty_source_selection")
        if not targets:
            reasons.add("empty_target_selection")
        boundaries: set[tuple[str, str, str]] = set()
        stops: list[dict[str, Any]] = []
        counterexamples: list[dict[str, Any]] = []
        pairs = evaluated = edges = visited = 0
        if sources and targets:
            if sources_total > cap["max_sources_per_rule"]:
                reasons.add("source_limit")
                sources = sources[: cap["max_sources_per_rule"]]
            for source in sources:
                if self.total >= cap["max_total_examined_edges"]:
                    reasons.add("total_examined_edge_limit")
                    break
                trace = self.traverse(source, targets, boundaries)
                evaluated += 1
                edges += trace.edges
                visited += len(trace.visited)
                for target in sorted(trace.found):
                    pairs += 1
                    if len(counterexamples) < cap["max_counterexamples_per_rule"]:
                        counterexamples.append(self.witness(source, target, trace.found[target]))
                if trace.stop is not None:
                    reasons.add(trace.stop)
                    stops.append(
                        {
                            "source_path": source,
                            "stop_reason": trace.stop,
                            "depth": trace.depth,
                            "visited_files": len(trace.visited),
                            "examined_edges": trace.edges,
                        }
                    )
        reasons.update(kind for kind, _path, _id in boundaries)
        ordered = sorted(boundaries)
        if pairs:
            outcome = "violated"
        elif reasons & set(BUDGET_REASONS):
            outcome = "incomplete_due_to_budget"
        elif reasons:
            outcome = "unknown"
        else:
            outcome = "satisfied_within_scope"
        return {
            "index": rule.index,
            "id": rule.id,
            "description": rule.description,
            "relation": rule.relation,
            "from": [s.text for s in rule.sources],
            "to": [s.text for s in rule.targets],
            "outcome": outcome,
            "reasons": {reason: True for reason in sorted(reasons)},
            "selection": {
                "sources_total": sources_total,
                "sources_evaluated": evaluated,
                "targets_total": len(targets),
            },
            "counts": {
                "violating_pairs": pairs,
                "counterexamples": len(counterexamples),
                "boundaries_total": len(ordered),
                "traversals_stopped": len(stops),
                "examined_edges": edges,
                "visited_files": visited,
            },
            "counterexamples": counterexamples,
            "boundaries": [self.boundary(k) for k in ordered[: cap["max_boundaries_per_rule"]]],
            "stops": sorted(stops, key=lambda s: s["source_path"]),
        }


def check(reader: Any, ruleset: RuleSet, limits: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Evaluate `ruleset` on the leased `reader`; the report is canonical-JSON serialisable."""
    effective = normalize_limits(limits)
    run = _Run(reader, effective)
    reports = [run.evaluate(rule) for rule in ruleset.rules]
    report: dict[str, Any] = {
        "format": FORMAT,
        "checker": CHECKER,
        "rules_format": rule_module.FORMAT,
        "rules_sha256": ruleset.sha256,
        "snapshot": {
            "snapshot_id": reader.snapshot_id,
            "generation_id": reader._lease_generation_id,
        },
        "effective_limits": effective,
        "support": {
            "relation": "import",
            "edge_resolutions": list(EDGE_RESOLUTIONS),
            "import_languages": list(IMPORT_LANGUAGES),
            "dynamic_import_callees": list(DYNAMIC_IMPORT_CALLEES),
            "scope": run.scope(),
        },
        "limitations": dict(LIMITATIONS),
        "summary": {
            "rules_total": len(reports),
            **{o: sum(1 for r in reports if r["outcome"] == o) for o in OUTCOMES},
        },
        "work": {"examined_edges": run.total, **run.work},
        "rules": reports,
    }
    report["report_id"] = report_id(report)
    return report


# ---- report schema and validation ---------------------------------------------------------

_HASH = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
_COUNT = {"type": "integer", "minimum": 0}
_TEXT = {"type": "string"}
_TRUE = {"const": True}


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def _obj(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": sorted(properties),
        "properties": properties,
    }


def _enum(values: tuple[str, ...]) -> dict[str, Any]:
    return {"type": "string", "enum": list(values)}


def _flags(values: tuple[str, ...]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {name: _TRUE for name in values},
    }


def _array(item: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": item}


_NODE = _obj(
    {
        "projection": {"const": "entity_identity"},
        "id": _HASH,
        "name": _TEXT,
        "kind": _TEXT,
        "language": _TEXT,
        "path": _TEXT,
        "owners": _array(_TEXT),
        "identity_basis": _TEXT,
        "ordinal": _COUNT,
    }
)
_RECORD = {"type": "object"}
_HOP = _obj(
    {
        "projection": {"const": "canonical_assertion_proof"},
        "traversal_direction": {"const": "outgoing"},
        "assertion": _RECORD,
        "evidence": _RECORD,
        "occurrence": _RECORD,
        "producer": _RECORD,
        "source_availability": _enum(("available", "unavailable")),
    }
)
_WITNESS = _obj(
    {
        "source_path": _TEXT,
        "target_path": _TEXT,
        "length": {"type": "integer", "minimum": 1},
        "path": _obj(
            {
                "nodes": {**_array(_NODE), "minItems": 2},
                "hops": {**_array(_HOP), "minItems": 1},
            }
        ),
    }
)
_BOUNDARY = _obj(
    {
        "kind": _enum(BOUNDARY_KINDS),
        "path": _TEXT,
        "language": _nullable(_TEXT),
        "observation_state": _nullable(_TEXT),
        "observation_reason": _nullable(_TEXT),
        "assertion_id": _nullable(_HASH),
        "evidence_id": _nullable(_HASH),
        "occurrence_id": _nullable(_HASH),
        "line": _nullable(_COUNT),
        "relation": _nullable(_TEXT),
        "resolution": _nullable(_TEXT),
        "unresolved_reason": _nullable(_TEXT),
        "raw_target": _nullable(_TEXT),
    }
)
_STOP = _obj(
    {
        "source_path": _TEXT,
        "stop_reason": _enum(STOP_REASONS),
        "depth": _COUNT,
        "visited_files": _COUNT,
        "examined_edges": _COUNT,
    }
)
_RULE_REPORT = _obj(
    {
        "index": _COUNT,
        "id": _TEXT,
        "description": _nullable(_TEXT),
        "relation": {"const": "import"},
        "from": _array(_TEXT),
        "to": _array(_TEXT),
        "outcome": _enum(OUTCOMES),
        "reasons": _flags(REASONS),
        "selection": _obj(
            {"sources_total": _COUNT, "sources_evaluated": _COUNT, "targets_total": _COUNT}
        ),
        "counts": _obj(
            {
                "violating_pairs": _COUNT,
                "counterexamples": _COUNT,
                "boundaries_total": _COUNT,
                "traversals_stopped": _COUNT,
                "examined_edges": _COUNT,
                "visited_files": _COUNT,
            }
        ),
        "counterexamples": _array(_WITNESS),
        "boundaries": _array(_BOUNDARY),
        "stops": _array(_STOP),
    }
)
REPORT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    **_obj(
        {
            "format": {"const": FORMAT},
            "checker": {"const": CHECKER},
            "rules_format": {"const": rule_module.FORMAT},
            "rules_sha256": _HASH,
            "snapshot": _obj({"snapshot_id": _HASH, "generation_id": _HASH}),
            "effective_limits": _obj({name: _COUNT for name in LIMITS}),
            "support": _obj(
                {
                    "relation": {"const": "import"},
                    "edge_resolutions": _array(_enum(EDGE_RESOLUTIONS)),
                    "import_languages": _array(_TEXT),
                    "dynamic_import_callees": _array(_TEXT),
                    "scope": _obj({"languages": _array(_TEXT), "skip_patterns": _array(_TEXT)}),
                }
            ),
            "limitations": _obj({name: _TRUE for name in LIMITATIONS}),
            "summary": _obj({"rules_total": _COUNT, **{o: _COUNT for o in OUTCOMES}}),
            "work": _obj(
                {
                    name: _COUNT
                    for name in (
                        "examined_edges",
                        "traversals",
                        "adjacency_queries",
                        "probe_queries",
                        "entity_queries",
                        "file_entity_queries",
                        "hydration_queries",
                    )
                }
            ),
            "rules": _array(_RULE_REPORT),
            "report_id": _HASH,
        }
    ),
}
_REPORT_VALIDATOR = Draft202012Validator(REPORT_SCHEMA)


def _invalid(message: str) -> ValueError:
    return ValueError("Invalid architecture report: " + message)


def validate_report(report: dict[str, Any]) -> None:
    """The closed schema, then the semantic rules a schema cannot express."""
    if next(iter(_REPORT_VALIDATOR.iter_errors(report)), None) is not None:
        raise _invalid("schema")
    if report["limitations"] != LIMITATIONS:
        raise _invalid("limitations")
    caps = report["effective_limits"]
    for name, (_default, low, high) in LIMITS.items():
        if not low <= caps[name] <= high:
            raise _invalid("effective limit")
    rules = report["rules"]
    if [r["index"] for r in rules] != list(range(len(rules))):
        raise _invalid("rule order")
    for entry in rules:
        _validate_rule(entry, caps)
    summary = report["summary"]
    if summary["rules_total"] != len(rules) or any(
        summary[o] != sum(1 for r in rules if r["outcome"] == o) for o in OUTCOMES
    ):
        raise _invalid("summary")
    if report["report_id"] != report_id(report):
        raise _invalid("report_id")


def _validate_rule(entry: dict[str, Any], caps: dict[str, int]) -> None:
    counts = entry["counts"]
    reasons = set(entry["reasons"])
    if counts["violating_pairs"]:
        outcome = "violated"
    elif reasons & set(BUDGET_REASONS):
        outcome = "incomplete_due_to_budget"
    elif reasons:
        outcome = "unknown"
    else:
        outcome = "satisfied_within_scope"
    if entry["outcome"] != outcome:
        raise _invalid("outcome")
    selection = entry["selection"]
    examples, boundaries, stops = entry["counterexamples"], entry["boundaries"], entry["stops"]
    if not (
        counts["counterexamples"] == len(examples) <= counts["violating_pairs"]
        and len(examples) <= caps["max_counterexamples_per_rule"]
        and len(boundaries) <= counts["boundaries_total"]
        and len(boundaries) <= caps["max_boundaries_per_rule"]
        and counts["traversals_stopped"] == len(stops) <= selection["sources_evaluated"]
        and selection["sources_evaluated"] <= caps["max_sources_per_rule"]
        and selection["sources_evaluated"] <= selection["sources_total"]
        and counts["examined_edges"] <= caps["max_total_examined_edges"]
    ):
        raise _invalid("counts")
    if (
        (selection["sources_total"] == 0) != ("empty_source_selection" in reasons)
        or (selection["targets_total"] == 0) != ("empty_target_selection" in reasons)
        or {s["stop_reason"] for s in stops} - reasons
        or {b["kind"] for b in boundaries} - reasons
    ):
        raise _invalid("reasons")
    if [(e["source_path"], e["target_path"]) for e in examples] != sorted(
        (e["source_path"], e["target_path"]) for e in examples
    ):
        raise _invalid("witness order")
    for item in examples:
        nodes, hops = item["path"]["nodes"], item["path"]["hops"]
        if not (
            item["length"] == len(hops) == len(nodes) - 1
            and nodes[0]["path"] == item["source_path"]
            and nodes[-1]["path"] == item["target_path"]
        ):
            raise _invalid("witness shape")
    keys = [(b["kind"], b["path"], b["assertion_id"] or "") for b in boundaries]
    if keys != sorted(keys) or len(set(keys)) != len(keys):
        raise _invalid("boundary order")
    if [s["source_path"] for s in stops] != sorted(s["source_path"] for s in stops):
        raise _invalid("stop order")


def run(side: Side, rule_bytes: bytes, limits: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Limits, then rule validation, then the graph: an invalid rule never opens the graph."""
    effective = normalize_limits(limits)
    ruleset = rule_module.load(rule_bytes)
    with ExitStack() as stack:
        reader = open_snapshot(stack, side)
        report = check(reader, ruleset, effective)
    validate_report(report)
    return report


__all__ = [
    "LIMITS",
    "ArchitectureRejected",
    "ArchitectureUsageError",
    "DiffUsageError",
    "check",
    "open_snapshot",
    "run",
    "validate_report",
]
