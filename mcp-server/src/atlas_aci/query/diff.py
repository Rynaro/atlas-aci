"""ACI-S19: compare two indexed snapshots at symbol and contract boundaries.

A pure, offline, read-only library. Each side is a complete graph namespace
(`Side(repo, graph_root)`) opened through the existing pinned reader lease; the
two source snapshots, never the live checkout, supply every byte. The result is
the content-addressed change set ``atlas-symbol-diff.v1``.

Nothing here writes to either graph root or either repository, mutates the
indexer, or is reachable from an MCP tool: it is an operator surface only.
Declaration fingerprints are recomputed from hash-verified snapshot bytes by
`atlas_aci.query.diff_syntax`; `scip_*` entities and JSONL-imported or legacy
generations (no source snapshot) are out of scope.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import sqlite3
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from atlas_aci.codegraph import CodeGraph
from atlas_aci.enforcement import ToolError
from atlas_aci.query import diff_syntax as syntax
from atlas_aci.query.diff_syntax import EntityInput, Facts, FileAnalysis, Unknown
from atlas_aci.storage import evidence as ev

FORMAT = "atlas-symbol-diff.v1"
CATEGORIES = ("added", "removed", "signature", "body", "format", "location", "unknown")
REJECTIONS = (
    "index_unavailable",
    "source_snapshot_unavailable",
    "identity_kind_mismatch",
    "repository_identity_mismatch",
    "schema_mismatch",
    "configuration_mismatch",
    "producer_mismatch",
)
UNKNOWN_REASONS = (
    "fingerprint_unsupported_kind",
    "extraction_failed_before",
    "extraction_failed_after",
    "definition_occurrence_ambiguous",
    "source_unavailable",
    "declaration_node_not_found",
    "parse_error_in_declaration",
    "uncovered_source_text",
    "unmapped_declaration_child",
    "non_utf8_declaration",
    "inconsistent_evidence",
)
_PACKAGES = ("tree-sitter", "tree-sitter-language-pack")


class DiffRejected(ValueError):
    """The two snapshots cannot be soundly compared; `reason` is a closed code."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class DiffUsageError(ValueError):
    """The invocation itself is invalid (not a property of the snapshots)."""


@dataclass(frozen=True)
class Side:
    """One snapshot namespace: a repository and where its graph is stored."""

    repo: Path
    graph_root: Path | None = None


def _sha(value: Any) -> str:
    return hashlib.sha256(ev.canonical(value).encode("utf-8")).hexdigest()


@dataclass
class _Loaded:
    reader: Any
    snapshot_id: str
    generation_id: str
    context: dict[str, Any]
    observations: dict[str, dict[str, Any]] = field(default_factory=dict)
    entities: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    definitions: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    _evidence: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    _analysis: dict[str, FileAnalysis | Unknown] = field(default_factory=dict)

    # ---- loading ------------------------------------------------------------
    def load(self) -> None:
        db = self.reader.db
        # Authenticated at lease acquisition; read directly rather than paying the
        # full `_observation_records` revalidation a second time.
        row = db.execute("SELECT value FROM manifest WHERE key='source_observations'").fetchone()
        for record in json.loads(row[0]) if row else []:
            if record.get("type") == "observation":
                self.observations[record["path"]] = record
        for _path, payload in db.execute("SELECT path, payload FROM entities"):
            entity = json.loads(payload)
            kind = entity["kind"]
            if kind == "file" or kind.startswith("scip_"):
                continue
            self.entities.setdefault(entity["path"], {})[entity["id"]] = entity
        known = {i for group in self.entities.values() for i in group}
        wanted: dict[str, str] = {
            i: e["kind"] for group in self.entities.values() for i, e in group.items()
        }
        query = "SELECT payload FROM occurrences WHERE json_extract(payload,'$.kind')='definition'"
        for (payload,) in db.execute(query):
            occurrence = json.loads(payload)
            entity_id = occurrence["entity_id"]
            if (
                entity_id in known
                and occurrence["provenance_state"] == "source"
                and occurrence["role"] == "definition:def." + wanted[entity_id]
            ):
                self.definitions.setdefault(entity_id, []).append(occurrence)

    def definition(self, entity_id: str) -> dict[str, Any] | None:
        matches = self.definitions.get(entity_id, [])
        return matches[0] if len(matches) == 1 else None

    def evidence_id(self, path: str, occurrence_id: str) -> str | None:
        if path not in self._evidence:
            table: dict[str, list[str]] = {}
            for identifier, payload in self.reader.db.execute(
                "SELECT id, payload FROM evidence WHERE path=?", (path,)
            ):
                table.setdefault(json.loads(payload)["occurrence_id"], []).append(identifier)
            self._evidence[path] = table
        found = self._evidence[path].get(occurrence_id, [])
        return found[0] if len(found) == 1 else None

    def analysis(self, path: str, factory: Callable[..., FileAnalysis]) -> FileAnalysis | Unknown:
        if path in self._analysis:
            return self._analysis[path]
        result: FileAnalysis | Unknown
        group = self.entities.get(path, {})
        inputs = []
        sha = None
        language = None
        for entity_id, entity in sorted(group.items()):
            occurrence = self.definition(entity_id)
            if occurrence is None:
                continue
            sha = occurrence["source_sha256"]
            language = entity["language"]
            inputs.append(
                EntityInput(
                    entity_id,
                    entity["kind"],
                    entity["name"],
                    occurrence["start_byte"],
                    occurrence["end_byte"],
                )
            )
        if sha is None or language is None:
            result = Unknown("definition_occurrence_ambiguous")
        else:
            try:
                source = self.reader.read_source(path, sha)
            except ToolError:
                result = Unknown("source_unavailable")
            else:
                try:
                    result = factory(language, source, inputs)
                except Exception:  # grammar/runtime failure: fail closed, never guess
                    result = Unknown("declaration_node_not_found")
        self._analysis[path] = result
        return result

    def facts(self, path: str, entity_id: str, factory: Callable[..., FileAnalysis]):
        """`Facts` for one entity, or the `Unknown` explaining why not."""
        if self.definition(entity_id) is None:
            return Unknown("definition_occurrence_ambiguous")
        analysis = self.analysis(path, factory)
        if isinstance(analysis, Unknown):
            return analysis
        try:
            return analysis.facts(entity_id)
        except Unknown as failure:
            return failure


def _open(stack: ExitStack, side: Side) -> CodeGraph:
    try:
        graph = CodeGraph(side.repo, read_only=True, graph_root=side.graph_root)
    except (ValueError, OSError) as exc:
        raise DiffUsageError(str(exc)) from exc
    stack.callback(graph.close)
    return graph


def _lease(stack: ExitStack, graph: CodeGraph) -> Any:
    try:
        return stack.enter_context(graph.reader_lease())
    except ToolError as exc:
        if exc.code == "INDEX_UNAVAILABLE":
            raise DiffRejected("index_unavailable") from exc
        raise DiffRejected("source_snapshot_unavailable") from exc
    except (sqlite3.Error, OSError, RuntimeError) as exc:
        raise DiffRejected("index_unavailable") from exc


def _check_compatible(before: Any, after: Any) -> dict[str, Any]:
    for reader in (before, after):
        if reader.snapshot_id is None or reader._source_lease is None:
            raise DiffRejected("source_snapshot_unavailable")
    one = before._source_lease.manifest["context"]
    two = after._source_lease.manifest["context"]
    if one["identity"]["kind"] != two["identity"]["kind"]:
        raise DiffRejected("identity_kind_mismatch")
    kind = one["identity"]["kind"]
    if kind == "git":
        rule = "git_common_dir"
        same = one["identity"]["common_dir"] == two["identity"]["common_dir"]
    else:
        rule = "same_root"
        same = one["identity"]["root"] == two["identity"]["root"]
    if not same:
        raise DiffRejected("repository_identity_mismatch")
    if one["schema"] != two["schema"]:
        raise DiffRejected("schema_mismatch")
    if one["configuration"] != two["configuration"]:
        raise DiffRejected("configuration_mismatch")
    running = {name: importlib.metadata.version(name) for name in _PACKAGES}
    if (
        one["producer"] != two["producer"]
        or {k: one["producer"].get("packages", {}).get(k) for k in _PACKAGES} != running
    ):
        raise DiffRejected("producer_mismatch")
    return {
        "identity_kind": kind,
        "identity_rule": rule,
        "schema": one["schema"],
        "configuration_sha256": _sha(one["configuration"]),
        "producer_sha256": _sha(one["producer"]),
    }


def _position(occurrence: dict[str, Any]) -> tuple[int, int, int]:
    return occurrence["start_byte"], occurrence["line"], occurrence["column"]


def _ref(side: _Loaded, entity_id: str, path: str, facts: Facts | Unknown | None) -> dict[str, Any]:
    occurrence = side.definition(entity_id)
    assert occurrence is not None
    usable = facts if isinstance(facts, Facts) else None
    return {
        "snapshot_id": side.snapshot_id,
        "generation_id": side.generation_id,
        "occurrence_id": occurrence["id"],
        "evidence_id": side.evidence_id(path, occurrence["id"]),
        "path": path,
        "source_sha256": occurrence["source_sha256"],
        "start_byte": occurrence["start_byte"],
        "end_byte": occurrence["end_byte"],
        "line": occurrence["line"],
        "column": occurrence["column"],
        "end_line": occurrence["end_line"],
        "end_column": occurrence["end_column"],
        "declaration_sha256": usable.declaration_sha256 if usable else None,
        "own_text_sha256": usable.own_text_sha256 if usable else None,
        "signature_sha256": usable.signature_sha256 if usable else None,
        "body_sha256": usable.body_sha256 if usable else None,
        "fingerprint_basis": usable.basis if usable else "unsupported",
    }


def _entity_fields(entity: dict[str, Any]) -> dict[str, Any]:
    return {
        "entity_id": entity["id"],
        "language": entity["language"],
        "kind": entity["kind"],
        "name": entity["name"],
        "owners": entity["owners"],
        "ordinal": entity["ordinal"],
    }


def _record(
    category: str,
    entity: dict[str, Any],
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    *,
    reason: str | None = None,
    flags: tuple[bool | None, ...] = (False, False, False, False),
) -> dict[str, Any]:
    record = {
        "type": "symbol_change",
        "category": category,
        **_entity_fields(entity),
        "path": entity["path"],
        "reason": reason,
        "signature_changed": flags[0],
        "body_changed": flags[1],
        "text_changed": flags[2],
        "position_changed": flags[3],
        "before": before,
        "after": after,
    }
    record["id"] = _sha(record)
    return record


def _classify(
    one: Facts | Unknown, two: Facts | Unknown, moved: bool
) -> tuple[str, str | None, tuple[bool | None, ...]]:
    if isinstance(one, Unknown) or isinstance(two, Unknown):
        failure = one if isinstance(one, Unknown) else two
        assert isinstance(failure, Unknown)
        return "unknown", failure.reason, (None, None, None, moved)
    text = one.own_text_sha256 != two.own_text_sha256
    if one.basis == "raw_text" or two.basis == "raw_text":
        if text:
            return "unknown", syntax.FINGERPRINT_UNSUPPORTED, (None, None, True, moved)
        return ("location" if moved else "unchanged"), None, (False, False, False, moved)
    signature = one.signature_sha256 != two.signature_sha256
    body = one.body_sha256 != two.body_sha256
    flags = (signature, body, text, moved)
    if signature:
        return "signature", None, flags
    if body:
        return "body", None, flags
    if text:
        return "format", None, flags
    return ("location" if moved else "unchanged"), None, flags


def _tokens(facts: Facts) -> int | None:
    count = facts.body_tokens if facts.body_sha256 is not None else facts.signature_tokens
    return count if count is not None and count >= syntax.MIN_CANDIDATE_TOKENS else None


def _candidates(
    removed: list[tuple[dict[str, Any], dict[str, Any], Facts | Unknown]],
    added: list[tuple[dict[str, Any], dict[str, Any], Facts | Unknown]],
) -> list[dict[str, Any]]:
    """Pair a removed and an added declaration only when the match is unique both ways."""

    def keyed(items):
        groups: dict[tuple[Any, ...], list[Any]] = {}
        for entity, record, facts in items:
            if (
                isinstance(facts, Facts)
                and facts.basis == "parsed"
                and facts.signature_sha256 is not None
                and _tokens(facts) is not None
            ):
                key = (
                    entity["language"],
                    entity["kind"],
                    facts.signature_sha256,
                    facts.body_sha256,
                )
                groups.setdefault(key, []).append((entity, record))
        return groups

    old, new = keyed(removed), keyed(added)
    result = []
    for key, members in old.items():
        partners = new.get(key, [])
        if len(members) != 1 or len(partners) != 1:
            continue
        (gone, gone_record), (born, born_record) = members[0], partners[0]
        renamed = gone["name"] != born["name"]
        relocated = gone["path"] != born["path"] or gone["owners"] != born["owners"]
        if renamed and relocated:
            relation = "rename_and_move"
        elif renamed:
            relation = "rename"
        elif relocated:
            relation = "move"
        else:
            continue
        candidate = {
            "type": "symbol_candidate",
            "relation": relation,
            "basis": "identical_normalized_signature_and_body",
            "language": gone["language"],
            "kind": gone["kind"],
            "before_entity_id": gone["id"],
            "after_entity_id": born["id"],
            "before_record_id": gone_record["id"],
            "after_record_id": born_record["id"],
            "before": {"path": gone["path"], "name": gone["name"], "owners": gone["owners"]},
            "after": {"path": born["path"], "name": born["name"], "owners": born["owners"]},
        }
        candidate["id"] = _sha(candidate)
        result.append(candidate)
    return sorted(result, key=lambda c: (c["before_entity_id"], c["after_entity_id"]))


def compare(
    before: Side,
    after: Side,
    *,
    analysis_factory: Callable[..., FileAnalysis] = FileAnalysis,
) -> dict[str, Any]:
    """Return the canonical change set for two complete, compatible snapshots.

    Raises `DiffRejected` (closed reason) when the snapshots are unrelated or
    unverifiable and `DiffUsageError` for an invalid invocation. Both leases are
    released on every path, including rejection.
    """
    with ExitStack() as stack:
        graphs = [_open(stack, side) for side in (before, after)]
        if graphs[0].atlas_dir == graphs[1].atlas_dir:
            raise DiffUsageError("both sides resolve to the same graph namespace")
        readers = [_lease(stack, graph) for graph in graphs]
        compatibility = _check_compatible(*readers)
        old = _Loaded(
            readers[0],
            readers[0].snapshot_id,
            readers[0].generation_id,
            readers[0]._source_lease.manifest["context"],
        )
        new = _Loaded(
            readers[1],
            readers[1].snapshot_id,
            readers[1].generation_id,
            readers[1]._source_lease.manifest["context"],
        )
        old.load()
        new.load()
        return _build(old, new, compatibility, analysis_factory)


def _failed(side: _Loaded, path: str) -> bool:
    record = side.observations.get(path)
    return record is not None and record["state"] != "indexed"


def _build(
    old: _Loaded,
    new: _Loaded,
    compatibility: dict[str, Any],
    factory: Callable[..., FileAnalysis],
) -> dict[str, Any]:
    changes: list[dict[str, Any]] = []
    removed: list[tuple[dict[str, Any], dict[str, Any], Facts | Unknown]] = []
    added: list[tuple[dict[str, Any], dict[str, Any], Facts | Unknown]] = []
    partial: set[str] = set()
    unchanged = 0
    paths = (
        set(old.entities)
        | set(new.entities)
        | {p for side in (old, new) for p in side.observations if _failed(side, p)}
    )
    for path in sorted(paths):
        failed_old, failed_new = _failed(old, path), _failed(new, path)
        if failed_old or failed_new:
            partial.add(path)
            if failed_old and failed_new:
                continue
            survivor, reason, other = (
                (new, "extraction_failed_before", True)
                if failed_old
                else (old, "extraction_failed_after", False)
            )
            for entity_id, entity in sorted(survivor.entities.get(path, {}).items()):
                if survivor.definition(entity_id) is None:
                    changes.append(
                        _record(
                            "unknown",
                            entity,
                            None,
                            None,
                            reason="definition_occurrence_ambiguous",
                            flags=(None, None, None, None),
                        )
                    )
                    continue
                facts = survivor.facts(path, entity_id, factory)
                ref = _ref(survivor, entity_id, path, facts)
                changes.append(
                    _record(
                        "unknown",
                        entity,
                        ref if not other else None,
                        ref if other else None,
                        reason=reason,
                        flags=(None, None, None, None),
                    )
                )
            continue
        one, two = old.entities.get(path, {}), new.entities.get(path, {})
        same_file = (
            path in old.observations
            and path in new.observations
            and old.observations[path]["observed_source_sha256"]
            == new.observations[path]["observed_source_sha256"]
        )
        for entity_id in sorted(set(one) - set(two)):
            entity = one[entity_id]
            facts = old.facts(path, entity_id, factory)
            gone_ref = _ref(old, entity_id, path, facts) if old.definition(entity_id) else None
            record = _record("removed", entity, gone_ref, None)
            changes.append(record)
            removed.append((entity, record, facts))
        for entity_id in sorted(set(two) - set(one)):
            entity = two[entity_id]
            facts = new.facts(path, entity_id, factory)
            born_ref = _ref(new, entity_id, path, facts) if new.definition(entity_id) else None
            record = _record("added", entity, None, born_ref)
            changes.append(record)
            added.append((entity, record, facts))
        for entity_id in sorted(set(one) & set(two)):
            entity = two[entity_id]
            first, second = old.definition(entity_id), new.definition(entity_id)
            if first is None or second is None:
                changes.append(
                    _record(
                        "unknown",
                        entity,
                        None,
                        None,
                        reason="definition_occurrence_ambiguous",
                        flags=(None, None, None, None),
                    )
                )
                continue
            if same_file:
                if first["id"] == second["id"]:
                    unchanged += 1  # unchanged-file fast path: no parse on either side
                    continue
                changes.append(
                    _record(
                        "unknown",
                        entity,
                        _ref(old, entity_id, path, None),
                        _ref(new, entity_id, path, None),
                        reason="inconsistent_evidence",
                        flags=(None, None, None, None),
                    )
                )
                continue
            facts_old = old.facts(path, entity_id, factory)
            facts_new = new.facts(path, entity_id, factory)
            category, why, flags = _classify(
                facts_old, facts_new, _position(first) != _position(second)
            )
            if category == "unchanged":
                unchanged += 1
                continue
            changes.append(
                _record(
                    category,
                    entity,
                    _ref(old, entity_id, path, facts_old),
                    _ref(new, entity_id, path, facts_new),
                    reason=why,
                    flags=flags,
                )
            )

    changes.sort(key=lambda record: (record["path"], record["entity_id"]))
    candidates = _candidates(removed, added)
    summary = {name: 0 for name in CATEGORIES}
    for record in changes:
        summary[record["category"]] += 1
    summary["unchanged"] = unchanged
    summary["candidates"] = len(candidates)
    rest = {
        "format": FORMAT,
        "before": {"snapshot_id": old.snapshot_id, "generation_id": old.generation_id},
        "after": {"snapshot_id": new.snapshot_id, "generation_id": new.generation_id},
        "compatibility": compatibility,
        "changes": changes,
        "candidates": candidates,
        "partial_paths": sorted(partial),
        "summary": dict(sorted(summary.items())),
        "limitations": list(syntax.LIMITATIONS),
    }
    identifier = hashlib.sha256((FORMAT + ":" + ev.canonical(rest)).encode("utf-8")).hexdigest()
    return {**rest, "id": identifier}


def canonical_bytes(change_set: dict[str, Any]) -> bytes:
    """The exact bytes the CLI prints: canonical JSON plus one newline."""
    return ev.canonical(change_set).encode("utf-8") + b"\n"
