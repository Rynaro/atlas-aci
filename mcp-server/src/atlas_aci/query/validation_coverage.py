"""ACI-S22 coverage evidence: source admission, per-file classification, bounded reads.

A coverage source is a held graph lease (the before side, the after side, or an operator
supplied external graph root). Observations are matched to a changed span only by
byte-identical file snapshots: a source's observed file hash must equal the requested
side's file hash for that path; otherwise the source is `stale` for that file and its
rows are counted but never hydrated into evidence.

Private symbols used (kept honest by `test_s22_semantics`, which names them on failure):
`atlas_aci.query.diff._check_compatible` (external source compatibility) and the lease
attributes `db`, `atlas_dir`, `snapshot_id`, `generation_id`, `read_source`. Everything
else is public.

Reads are bounded by counters only (never a wall clock): every assertion row, evidence,
occurrence, test entity and producer loaded is charged against `max_observation_rows`,
and every reference probe row against `max_reference_rows`.
"""

from __future__ import annotations

import bisect
import json
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from atlas_aci.enforcement import ToolError
from atlas_aci.providers import coverage_py
from atlas_aci.query import diff as symbol_diff
from atlas_aci.query import impact
from atlas_aci.query import validation_limits as vlim
from atlas_aci.query.diff import DiffRejected, Side
from atlas_aci.storage import evidence as ev

# The literal json_extract expressions are what `idx_assertions_target_relation_id` indexes.
OBSERVATION_SQL = (
    "SELECT id,payload FROM assertions "
    "WHERE json_extract(payload,'$.relation')=? "
    "AND json_extract(payload,'$.target_entity_id')=? ORDER BY id LIMIT ?"
)
OBSERVATION_COUNT_SQL = (
    "SELECT COUNT(*) FROM (SELECT 1 FROM assertions "
    "WHERE json_extract(payload,'$.relation')=? "
    "AND json_extract(payload,'$.target_entity_id')=? LIMIT ?)"
)
FILE_ENTITY_SQL = (
    "SELECT id FROM entities WHERE path=? AND json_extract(payload,'$.kind')='file' ORDER BY id"
)


class SourceRejected(ToolError):
    """A typed external coverage source rejection. `reason` is a closed code; never a path."""

    def __init__(self, reason: str):
        super().__init__(
            "VALIDATION_SOURCE_REJECTED", "Coverage source rejected: " + reason, "none"
        )
        self.reason = reason


class Budget:
    """A row allowance. A failed `take` marks the budget exhausted and charges nothing."""

    def __init__(self, limit: int):
        self.limit = limit
        self.used = 0
        self.exhausted = False

    @property
    def remaining(self) -> int:
        return self.limit - self.used

    def take(self, amount: int = 1) -> bool:
        if self.used + amount > self.limit:
            self.exhausted = True
            return False
        self.used += amount
        return True


@dataclass
class Counters:
    stale_observation_rows: int = 0
    reference_probes: int = 0
    assertion_paths_loaded: int = 0
    source_files_read: int = 0


@dataclass(frozen=True)
class Observation:
    nodeid: str
    test_path: str
    test_entity_id: str
    assertion_id: str
    evidence_id: str
    occurrence_id: str
    producer_id: str
    run_id: str | None
    line: int
    start_byte: int
    end_byte: int
    phases: tuple[str, ...]
    source_sha256: str
    test_file_sha256: str | None


@dataclass
class Source:
    kind: str
    index: int
    reader: Any
    generation_kind: str
    runs: list[dict[str, Any]]
    observations: dict[str, dict[str, Any]]
    classifications: dict[str, str] = field(default_factory=dict)
    row_counts: dict[str, int] = field(default_factory=dict)
    rows: dict[str, list[Observation]] = field(default_factory=dict)
    limited: set[str] = field(default_factory=set)
    _index: dict[str, tuple[list[int], list[int]]] = field(default_factory=dict)

    @property
    def bearing(self) -> bool:
        return self.generation_kind == "coverage_import"

    def file_hash(self, path: str) -> str | None:
        record = self.observations.get(path)
        return None if record is None else record["observed_source_sha256"]


# ---- manifest reads (no private impact helpers) ---------------------------------


def _manifest_json(reader: Any, key: str) -> Any:
    row = reader.db.execute("SELECT value FROM manifest WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


def observations_of(reader: Any) -> dict[str, dict[str, Any]]:
    records = _manifest_json(reader, "source_observations") or []
    return {r["path"]: r for r in records if r.get("type") == "observation"}


def make_source(kind: str, index: int, reader: Any) -> Source:
    inputs = _manifest_json(reader, "generation_inputs") or {}
    generation_kind = inputs.get("kind", "unknown")
    runs = []
    if generation_kind == "coverage_import":
        for run in (inputs.get("coverage") or {}).get("runs", []):
            tool = run.get("tool") or {}
            runs.append(
                {
                    "run_id": run["run_id"],
                    "producer_id": run["producer_id"],
                    "tool": f"{tool.get('name', 'unknown')} {tool.get('version', '')}".strip(),
                    "report_timestamp": run.get("report_timestamp"),
                }
            )
    runs.sort(key=lambda r: (r["run_id"], r["producer_id"]))
    return Source(kind, index, reader, generation_kind, runs, observations_of(reader))


def open_external(stack: ExitStack, repo: Path, root: Path, after: Any) -> Any:
    """Lease one external coverage root at the after repository path; typed, path-free errors."""
    try:
        reader = impact.open_side(stack, Side(repo, root))
    except impact.ImpactError as exc:
        raise SourceRejected(exc.reason or "index_unavailable") from exc
    try:
        symbol_diff._check_compatible(reader, after)
    except DiffRejected as exc:
        raise SourceRejected(exc.reason) from exc
    return reader


def admit_externals(
    stack: ExitStack,
    repo: Path,
    roots: list[Path],
    before: Any,
    after: Any,
    limit: int,
) -> list[Any]:
    if len(roots) > limit:
        raise SourceRejected("source_limit")
    readers: list[Any] = []
    for root in roots:
        reader = open_external(stack, repo, root, after)
        for held in (before, after, *readers):
            if reader.atlas_dir == held.atlas_dir:
                raise SourceRejected("same_namespace")
        for held in (before, after, *readers):
            if reader.generation_id == held.generation_id:
                raise SourceRejected("duplicate_source")
        readers.append(reader)
    return sorted(readers, key=lambda r: r.generation_id)


# ---- line geometry --------------------------------------------------------------


class Geometry:
    """Byte-level span geometry of one side's file snapshot (physical-line based)."""

    def __init__(self, readers: dict[str, Any], counters: Counters):
        self.readers = readers
        self.counters = counters
        self._cache: dict[tuple[str, str, str], tuple[bytes | None, list[tuple[int, int]]]] = {}

    def _source(self, side: str, path: str, sha: str):
        key = (side, path, sha)
        if key not in self._cache:
            try:
                data = self.readers[side].read_source(path, sha)
            except ToolError:
                self._cache[key] = (None, [])
            else:
                self.counters.source_files_read += 1
                self._cache[key] = (data, coverage_py.line_spans(data))
        return self._cache[key]

    def of(self, side: str, ref: dict[str, Any]) -> dict[str, int]:
        """`start`/`end` byte span (start expanded to the physical line start), the byte end of
        the header line and its physical line number. Lines are for display only."""
        data, spans = self._source(side, ref["path"], ref["source_sha256"])
        start, end = ref["start_byte"], ref["end_byte"]
        if data is None or not spans:
            # Source bytes unavailable: every overlapping line is weighted as a declaration.
            return {
                "start": start,
                "end": end,
                "header_end_byte": end,
                "header_end_line": ref["line"],
            }
        starts = [s for s, _ in spans]
        first = max(0, bisect.bisect_right(starts, start) - 1)
        header = first
        for index in range(first, len(spans)):
            line_start, line_end = spans[index]
            if line_start >= end:
                break
            if data[line_start:line_end].lstrip().startswith((b"def ", b"async def ", b"class ")):
                header = index
                break
        return {
            "start": spans[first][0],
            "end": end,
            "header_end_byte": spans[header][1],
            "header_end_line": header + 1,
        }


def overlaps(observation: Observation, geometry: dict[str, int]) -> bool:
    """Byte overlap of the occurrence `[start,end)` with the (line-expanded) ref span."""
    return (
        observation.start_byte < geometry["end"]
        and max(observation.end_byte, observation.start_byte + 1) > geometry["start"]
    )


def is_body(observation: Observation, geometry: dict[str, int]) -> bool:
    return observation.start_byte >= geometry["header_end_byte"]


# ---- bounded observation reads --------------------------------------------------


class Observed:
    """Per-(source, changed path) classification and bounded, deterministic row reads."""

    def __init__(self, sources: list[Source], budget: Budget, counters: Counters):
        self.sources = sources
        self.budget = budget
        self.counters = counters
        self.hashes = {"before": sources[0].observations, "after": sources[1].observations}
        self._entities: dict[tuple[int, str], dict[str, Any]] = {}
        self._producers: dict[tuple[int, str], dict[str, Any]] = {}

    def side_hash(self, side: str, path: str) -> str | None:
        record = self.hashes[side].get(path)
        return None if record is None else record["observed_source_sha256"]

    def matches(self, source: Source, path: str) -> set[str]:
        sha = source.file_hash(path)
        if sha is None:
            return set()
        return {side for side in vlim.SIDES if sha == self.side_hash(side, path)}

    def read(self, paths: list[str]) -> None:
        """Classify and read every (bearing source, path) in sorted order."""
        for source in self.sources:
            if not source.bearing:
                continue
            for path in sorted(paths):
                self._read_one(source, path)

    def _read_one(self, source: Source, path: str) -> None:
        sides = self.matches(source, path)
        db = source.reader.db
        found = [row[0] for row in db.execute(FILE_ENTITY_SQL, (path,))]
        if len(found) != 1:
            source.classifications[path] = "not_observed"
            source.row_counts[path] = 0
            return
        file_id = found[0]
        if not sides:
            allowance = self.budget.remaining
            count = db.execute(
                OBSERVATION_COUNT_SQL, (ev.COVERAGE_RELATION, file_id, allowance + 1)
            ).fetchone()[0]
            if count > allowance:
                source.limited.add(path)
                count = allowance
                self.budget.exhausted = True
            self.budget.take(count)
            self.counters.stale_observation_rows += count
            source.row_counts[path] = count
            source.classifications[path] = (
                "stale" if count else ("unread" if path in source.limited else "not_observed")
            )
            return
        label = (
            "matches_both"
            if len(sides) == 2
            else "matches_before"
            if "before" in sides
            else "matches_after"
        )
        source.classifications[path] = label
        allowance = self.budget.remaining
        rows = db.execute(
            OBSERVATION_SQL, (ev.COVERAGE_RELATION, file_id, allowance + 1)
        ).fetchall()
        if len(rows) > allowance:
            source.limited.add(path)
            self.budget.exhausted = True
            rows = rows[:allowance]
        loaded: list[Observation] = []
        for _identifier, payload in rows:
            observation = self._hydrate(source, json.loads(payload))
            if observation is None:
                source.limited.add(path)
                break
            loaded.append(observation)
        loaded.sort(key=lambda o: (o.start_byte, o.nodeid, o.assertion_id))
        source.rows[path] = loaded
        source.row_counts[path] = len(loaded)

    def _fetch(self, source: Source, cache, kind: str, identifier: str):
        key = (source.index, identifier)
        if key in cache:
            return cache[key]
        if not self.budget.take(1):
            return None
        cache[key] = ev.get(source.reader.db, kind, identifier)
        return cache[key]

    def _hydrate(self, source: Source, assertion: dict[str, Any]) -> Observation | None:
        db = source.reader.db
        if not self.budget.take(1):  # the assertion row itself
            return None
        test = self._fetch(source, self._entities, "entity", assertion["source_entity_id"])
        if test is None or not self.budget.take(2):  # evidence + occurrence, never shared
            return None
        proof = ev.get(db, "evidence", assertion["evidence_id"])
        occurrence = ev.get(db, "occurrence", proof["occurrence_id"])
        producer = self._fetch(source, self._producers, "producer", proof["producer_id"])
        if producer is None:
            return None
        fingerprint = (producer.get("fingerprint") or {}).get("coverage") or {}
        role = occurrence["role"]
        phases = tuple(role[len(ev.COVERAGE_ROLE_PREFIX) :].split("+"))
        test_record = source.observations.get(test["path"])
        return Observation(
            nodeid=test["name"],
            test_path=test["path"],
            test_entity_id=test["id"],
            assertion_id=assertion["id"],
            evidence_id=proof["id"],
            occurrence_id=occurrence["id"],
            producer_id=producer["id"],
            run_id=fingerprint.get("run_id"),
            line=occurrence["line"],
            start_byte=occurrence["start_byte"],
            end_byte=occurrence["end_byte"],
            phases=phases,
            source_sha256=occurrence["source_sha256"],
            test_file_sha256=None if test_record is None else test_record["observed_source_sha256"],
        )

    # ---- per-span-side queries ---------------------------------------------------

    def matching_sources(self, side: str, path: str) -> list[Source]:
        return [
            s
            for s in self.sources
            if s.bearing and side in self.matches(s, path) and path in s.classifications
        ]

    def overlapping(self, source: Source, path: str, geometry: dict[str, int]):
        """Rows (sorted by start byte) whose occurrence overlaps the span, found by bisection."""
        rows = source.rows.get(path, [])
        if not rows:
            return []
        cached = source._index.get(path)
        if cached is None:
            starts = [o.start_byte for o in rows]
            ends: list[int] = []
            high = 0
            for o in rows:
                high = max(high, o.end_byte, o.start_byte + 1)
                ends.append(high)
            cached = source._index[path] = (starts, ends)
        starts, ends = cached
        high_index = bisect.bisect_left(starts, geometry["end"])
        low_index = bisect.bisect_right(ends, geometry["start"])
        return [o for o in rows[low_index:high_index] if overlaps(o, geometry)]

    def stale_with_rows(self, path: str) -> list[Source]:
        return [
            s
            for s in self.sources
            if s.bearing and s.classifications.get(path) == "stale" and s.row_counts.get(path, 0)
        ]

    def file_entries(self, source: Source) -> list[dict[str, Any]]:
        if not source.bearing:
            return []
        entries = []
        for path in sorted(source.classifications):
            label = source.classifications[path]
            entries.append(
                {
                    "path": path,
                    "classification": label,
                    "observation_rows": source.row_counts.get(path, 0),
                }
            )
        return entries
