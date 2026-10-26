"""atlas-evidence.v1 keys, closed records and SQLite persistence.

No repository scanning or resolver lives here. Byte provenance is supplied by
an actual parser, or explicitly unavailable for a validated legacy projection.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import PurePosixPath
from typing import Any

FORMAT = "atlas-evidence.v1"
TABLES = {
    "entity": "entities",
    "occurrence": "occurrences",
    "producer": "producers",
    "evidence": "evidence",
    "assertion": "assertions",
}
DDL = "\n".join(
    f"CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, path TEXT, payload TEXT NOT NULL);\n"
    f"CREATE INDEX IF NOT EXISTS idx_{table}_path ON {table}(path);"
    for table in TABLES.values()
)
# Native-only navigation indexes; portable canonical records are unchanged.
NAVIGATION_INDEXES = {
    "idx_assertions_source_relation_id": (
        "CREATE INDEX IF NOT EXISTS idx_assertions_source_relation_id ON assertions("
        "json_extract(payload,'$.source_entity_id'),json_extract(payload,'$.relation'),id);"
    ),
    "idx_assertions_target_relation_id": (
        "CREATE INDEX IF NOT EXISTS idx_assertions_target_relation_id ON assertions("
        "json_extract(payload,'$.target_entity_id'),json_extract(payload,'$.relation'),id);"
    ),
    "idx_entities_name_id": (
        "CREATE INDEX IF NOT EXISTS idx_entities_name_id ON entities("
        "json_extract(payload,'$.name'),id);"
    ),
}
DDL += "\n" + "\n".join(NAVIGATION_INDEXES.values())
RELATIONS = frozenset(
    {"call", "construct", "import", "superclass", "include", "extend", "prepend", "rationale_for"}
)
FIELDS = {
    "entity": {
        "type",
        "id",
        "language",
        "path",
        "kind",
        "owners",
        "name",
        "ordinal",
        "identity_basis",
    },
    "occurrence": {
        "type",
        "id",
        "entity_id",
        "kind",
        "path",
        "language",
        "source_sha256",
        "source_size",
        "start_byte",
        "end_byte",
        "line",
        "column",
        "end_line",
        "end_column",
        "role",
        "provenance_state",
    },
    "producer": {"type", "id", "fingerprint", "identity_basis"},
    "evidence": {
        "type",
        "id",
        "occurrence_id",
        "producer_id",
        "source_sha256",
        "source_size",
        "start_byte",
        "end_byte",
        "provenance_state",
    },
    "assertion": {
        "type",
        "id",
        "source_entity_id",
        "target_entity_id",
        "relation",
        "evidence_id",
        "resolution",
        "candidate_ids",
        "raw_target",
        "unresolved_reason",
    },
    "projection": {"type", "id", "table", "row", "links"},
}
# Portable projections exclude SQLite IDs and diagnostic file-cache metadata.
COLUMNS = {
    "symbols": ("name", "kind", "path", "line_start", "line_end", "lang", "binding"),
    "refs": (
        "callee_name",
        "relation",
        "qualified",
        "qualifier_name",
        "path",
        "line",
        "col",
        "lang",
        "binding",
        "unresolved_reason",
    ),
    "edges": (
        "relation",
        "source_path",
        "source_line",
        "source_col",
        "source_name",
        "source_kind",
        "callee_name",
        "confidence",
        "target_path",
        "target_line",
        "target_name",
        "candidates",
        "lang",
    ),
    "rationale": (
        "path",
        "line",
        "text",
        "label",
        "target_path",
        "target_line",
        "target_name",
        "lang",
    ),
    "assignment_targets": ("path", "name", "scope", "binding_kind", "byte_start"),
    "import_bindings": ("path", "local_name", "scope", "byte_start", "description", "lang"),
}
LINK_COLUMNS = {
    "symbols": ("entity_id", "occurrence_id"),
    "refs": ("source_entity_id", "occurrence_id", "evidence_id"),
    "edges": ("assertion_id",),
    "rationale": ("occurrence_id", "evidence_id", "assertion_id", "target_entity_id"),
    "assignment_targets": ("entity_id", "occurrence_id", "evidence_id"),
    "import_bindings": ("source_entity_id", "occurrence_id", "evidence_id", "assertion_id"),
}


def canonical(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def identifier(record: dict[str, Any]) -> str:
    payload = {key: value for key, value in record.items() if key != "id"}
    return hashlib.sha256(
        (
            ("atlas-evidence.v2" if record["type"] in {"history", "observation"} else FORMAT)
            + ":"
            + record["type"]
            + "\0"
            + canonical(payload)
        ).encode()
    ).hexdigest()


def record(record_type: str, **fields: Any) -> dict[str, Any]:
    result = {"type": record_type, **fields}
    result["id"] = identifier(result)
    return result


def require(condition: Any, message: str) -> None:
    if not condition:
        raise ValueError("Invalid evidence: " + message)


def hash_value(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def relative_path(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and "\\" not in value
        and not PurePosixPath(value).is_absolute()
        and re.match(r"^[A-Za-z]:[\\/]", value) is None
        and value == PurePosixPath(value).as_posix()
        and all(part not in {".", ".."} for part in value.split("/"))
    )


def integer(value: Any, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def _validate_record(item: Any) -> None:
    require(isinstance(item, dict), "record must be an object")
    kind = item.get("type")
    require(isinstance(kind, str) and kind in FIELDS, "record type")
    require(set(item) == FIELDS[kind], f"closed {kind} fields")
    require(hash_value(item["id"]) and identifier(item) == item["id"], f"{kind} ID")
    if kind in {"observation", "history"}:
        validate_observation(item)
        return
    if kind == "entity":
        require(relative_path(item["path"]), "entity path")
        require(
            all(isinstance(item[key], str) and item[key] for key in ("language", "kind", "name")),
            "entity strings",
        )
        require(item["identity_basis"] in {"parsed", "legacy_projection"}, "identity basis")
        require(integer(item["ordinal"]), "duplicate ordinal")
        require(isinstance(item["owners"], list), "owner chain")
        for owner in item["owners"]:
            require(
                isinstance(owner, dict) and set(owner) == {"kind", "name", "ordinal"},
                "owner fields",
            )
            require(isinstance(owner["kind"], str) and bool(owner["kind"]), "owner kind")
            require(owner["name"] is None or isinstance(owner["name"], str), "owner name")
            require(integer(owner["ordinal"]), "owner ordinal")
    elif kind == "producer":
        require(
            item["identity_basis"] in {"parsed", "legacy_metadata", "legacy_unavailable"},
            "producer basis",
        )
        fingerprint = item["fingerprint"]
        if fingerprint is None:
            require(item["identity_basis"] == "legacy_unavailable", "unknown producer")
        else:
            require(isinstance(fingerprint, dict), "producer fingerprint")
            require(
                set(fingerprint)
                in (
                    {"codegraph_sha256", "queries_sha256", "packages"},
                    {"codegraph_sha256", "queries_sha256", "packages", "modules"},
                ),
                "producer fields",
            )
            require(
                hash_value(fingerprint["codegraph_sha256"])
                and hash_value(fingerprint["queries_sha256"]),
                "producer hashes",
            )
            packages = fingerprint["packages"]
            require(
                isinstance(packages, dict)
                and set(packages) == {"tree-sitter", "tree-sitter-language-pack"},
                "parser packages",
            )
            require(
                all(isinstance(value, str) and value for value in packages.values()),
                "parser versions",
            )
            if item["identity_basis"] == "parsed":
                require("modules" in fingerprint, "graph module fingerprints")
            if item["identity_basis"] == "legacy_metadata":
                require(
                    "modules" not in fingerprint,
                    "legacy producer must use known epoch6 fingerprint shape",
                )
            if "modules" in fingerprint:
                modules = fingerprint["modules"]
                require(
                    isinstance(modules, dict)
                    and set(modules)
                    in (
                        {"storage/evidence.py", "providers/python_imports.py"},
                        {
                            "storage/evidence.py",
                            "providers/python_imports.py",
                            "indexing/extraction_cache.py",
                        },
                        {
                            "storage/evidence.py",
                            "providers/python_imports.py",
                            "indexing/extraction_cache.py",
                            "resolvers/dependencies.py",
                        },
                        {
                            "storage/evidence.py",
                            "providers/python_imports.py",
                            "indexing/extraction_cache.py",
                            "resolvers/dependencies.py",
                            "extractors/rust.py",
                        },
                        {
                            "storage/evidence.py",
                            "providers/python_imports.py",
                            "indexing/extraction_cache.py",
                            "resolvers/dependencies.py",
                            "extractors/rust.py",
                            "storage/snapshots.py",
                        },
                    ),
                    "module set",
                )
                require(all(hash_value(value) for value in modules.values()), "module hashes")
    elif kind in {"occurrence", "evidence"}:
        state = item["provenance_state"]
        require(state in {"source", "legacy_unavailable"}, "provenance state")
        require(item["source_sha256"] is None or hash_value(item["source_sha256"]), "source hash")
        span = (item["start_byte"], item["end_byte"], item["source_size"])
        if state == "source":
            require(
                hash_value(item["source_sha256"]) and all(integer(value) for value in span),
                "known source span",
            )
            require(span[0] <= span[1] <= span[2], "half-open byte span")
        else:
            require(all(value is None for value in span), "unavailable span must be null")
        if kind == "occurrence":
            require(
                (state == "legacy_unavailable") == (item["kind"] == "legacy"),
                "occurrence provenance kind",
            )
            require(
                isinstance(item["role"], str)
                and (state == "legacy_unavailable") == item["role"].startswith("legacy_"),
                "occurrence provenance role",
            )
            require(relative_path(item["path"]), "occurrence path")
            require(item["entity_id"] is None or hash_value(item["entity_id"]), "occurrence entity")
            require(
                item["kind"] in {"definition", "reference", "rationale", "legacy"},
                "occurrence kind",
            )
            require(
                all(isinstance(item[key], str) and item[key] for key in ("language", "role")),
                "occurrence strings",
            )
            for key in ("line", "column", "end_line", "end_column"):
                require(
                    item[key] is None or integer(item[key], 1 if "line" in key else 0),
                    "display location",
                )
            if state == "source":
                require(
                    all(
                        item[key] is not None
                        for key in ("line", "column", "end_line", "end_column")
                    ),
                    "source display location",
                )
                require(
                    (item["line"], item["column"]) <= (item["end_line"], item["end_column"]),
                    "display span",
                )
        else:
            require(
                hash_value(item["occurrence_id"]) and hash_value(item["producer_id"]),
                "evidence links",
            )
    elif kind == "assertion":
        require(
            hash_value(item["source_entity_id"]) and hash_value(item["evidence_id"]),
            "assertion links",
        )
        require(
            item["target_entity_id"] is None or hash_value(item["target_entity_id"]),
            "assertion target",
        )
        require(item["relation"] in RELATIONS, "relation")
        require(
            item["resolution"]
            in {"EXTRACTED", "INFERRED", "AMBIGUOUS", "UNRESOLVED", "LEGACY_UNAVAILABLE"},
            "resolution",
        )
        candidates = item["candidate_ids"]
        require(
            isinstance(candidates, list)
            and all(hash_value(value) for value in candidates)
            and candidates == sorted(set(candidates)),
            "candidate IDs",
        )
        require(isinstance(item["raw_target"], str) and bool(item["raw_target"]), "raw target")
        if item["resolution"] in {"EXTRACTED", "INFERRED"}:
            require(
                item["target_entity_id"] is not None
                and not candidates
                and item["unresolved_reason"] is None,
                "resolved endpoints",
            )
        else:
            require(
                item["target_entity_id"] is None
                and isinstance(item["unresolved_reason"], str)
                and bool(item["unresolved_reason"]),
                "unresolved semantics",
            )
            require(bool(candidates) == (item["resolution"] == "AMBIGUOUS"), "ambiguity candidates")
    else:
        table = item["table"]
        require(isinstance(table, str) and table in COLUMNS, "projection table")
        require(
            isinstance(item["row"], dict) and set(item["row"]) == set(COLUMNS[table]),
            "projection columns",
        )
        require(
            isinstance(item["links"], dict) and set(item["links"]) == set(LINK_COLUMNS[table]),
            "projection link columns",
        )
        require(
            all(hash_value(value) or value is None for value in item["links"].values()),
            "projection link IDs",
        )
        for key, value in item["row"].items():
            if "path" in key and value is not None:
                require(relative_path(value), "projection path")
            if (
                key
                in {
                    "line",
                    "line_start",
                    "line_end",
                    "source_line",
                    "target_line",
                    "col",
                    "source_col",
                    "byte_start",
                }
                and value is not None
            ):
                require(integer(value, 1 if "line" in key else 0), "projection numeric location")
        require(isinstance(item["row"].get("lang", "python"), str), "projection language")
        row = item["row"]
        required_strings = {
            "symbols": ("name", "kind", "path", "lang", "binding"),
            "refs": ("callee_name", "relation", "path", "lang", "binding"),
            "edges": ("relation", "source_path", "callee_name", "confidence", "lang"),
            "rationale": ("path", "text", "lang"),
            "assignment_targets": ("path", "name", "scope", "binding_kind"),
            "import_bindings": ("path", "local_name", "scope", "description", "lang"),
        }
        require(
            all(isinstance(row[key], str) and row[key] for key in required_strings[table]),
            "projection strings",
        )
        require(
            all(
                value is not None
                for key, value in item["links"].items()
                if key != "target_entity_id"
            ),
            "missing projection links",
        )
        if table == "symbols":
            require(
                integer(row["line_start"], 1)
                and integer(row["line_end"], 1)
                and row["line_start"] <= row["line_end"],
                "definition display span",
            )
        if table in {"symbols", "refs"}:
            require(isinstance(json.loads(row["binding"]), dict), "binding object")
        if table == "refs":
            require(row["relation"] in RELATIONS - {"rationale_for"}, "reference relation")
            require(type(row["qualified"]) is int and row["qualified"] in {0, 1}, "qualified flag")
            require(
                all(
                    row[key] is None or isinstance(row[key], str)
                    for key in ("qualifier_name", "unresolved_reason")
                ),
                "nullable reference strings",
            )
        if table == "edges":
            require(row["relation"] in RELATIONS - {"rationale_for"}, "edge relation")
            require(row["confidence"] in {"EXTRACTED", "INFERRED", "AMBIGUOUS"}, "edge confidence")
            require(
                all(
                    row[key] is None or isinstance(row[key], str)
                    for key in ("source_name", "source_kind", "target_path", "target_name")
                ),
                "edge strings",
            )
            if row["confidence"] == "AMBIGUOUS":
                require(
                    all(row[key] is None for key in ("target_path", "target_line", "target_name")),
                    "ambiguous concrete target",
                )
                candidates = json.loads(row["candidates"])
                require(isinstance(candidates, list) and len(candidates) >= 2, "edge candidates")
                for candidate in candidates:
                    require(
                        isinstance(candidate, dict) and set(candidate) == {"path", "line", "name"},
                        "candidate fields",
                    )
                    require(
                        relative_path(candidate["path"])
                        and integer(candidate["line"], 1)
                        and isinstance(candidate["name"], str),
                        "candidate values",
                    )
            else:
                require(
                    relative_path(row["target_path"])
                    and integer(row["target_line"], 1)
                    and isinstance(row["target_name"], str)
                    and row["candidates"] is None,
                    "resolved edge",
                )
        if table == "rationale":
            require(row["label"] is None or isinstance(row["label"], str), "rationale label")
            require(
                all(row[key] is None for key in ("target_path", "target_line", "target_name"))
                or (
                    relative_path(row["target_path"])
                    and integer(row["target_line"], 1)
                    and isinstance(row["target_name"], str)
                ),
                "rationale endpoint",
            )
        if table == "assignment_targets":
            require(
                row["binding_kind"] in {"assignment", "parameter", "import", "unsupported"},
                "binding kind",
            )


def validate_record(item: Any) -> None:
    try:
        _validate_record(item)
    except (KeyError, TypeError, OverflowError) as exc:
        raise ValueError("Invalid evidence: malformed typed record") from exc


def put(conn: sqlite3.Connection, item: dict[str, Any], path: str | None = None) -> None:
    validate_record(item)
    table = TABLES[item["type"]]
    payload = canonical(item)
    previous = conn.execute(f"SELECT payload FROM {table} WHERE id=?", (item["id"],)).fetchone()
    require(previous is None or previous[0] == payload, "conflicting canonical ID")
    conn.execute(
        f"INSERT OR IGNORE INTO {table}(id,path,payload) VALUES (?,?,?)",
        (item["id"], path, payload),
    )


def get(conn: sqlite3.Connection, kind: str, identifier_value: str) -> dict[str, Any]:
    row = conn.execute(
        f"SELECT payload FROM {TABLES[kind]} WHERE id=?", (identifier_value,)
    ).fetchone()
    require(row is not None, f"missing {kind} link")
    return json.loads(row[0])


def records(conn: sqlite3.Connection):
    for kind in sorted(TABLES):
        for row in conn.execute(f"SELECT payload FROM {TABLES[kind]} ORDER BY id"):
            yield json.loads(row[0])


def remove_path(conn: sqlite3.Connection, path: str) -> None:
    for table in TABLES.values():
        conn.execute(f"DELETE FROM {table} WHERE path=?", (path,))


def validate_database(conn: sqlite3.Connection) -> None:
    for item in records(conn):
        validate_record(item)
        kind = item["type"]
        if kind == "occurrence" and item["entity_id"] is not None:
            entity = get(conn, "entity", item["entity_id"])
            require(
                (entity["path"], entity["language"]) == (item["path"], item["language"]),
                "occurrence/entity location",
            )
            require(
                (item["provenance_state"] == "source") == (entity["identity_basis"] == "parsed"),
                "occurrence/entity identity basis",
            )
        elif kind == "evidence":
            occurrence = get(conn, "occurrence", item["occurrence_id"])
            producer_record = get(conn, "producer", item["producer_id"])
            require(
                (item["provenance_state"] == "source")
                == (producer_record["identity_basis"] == "parsed"),
                "evidence/producer identity basis",
            )
            for key in (
                "source_sha256",
                "source_size",
                "start_byte",
                "end_byte",
                "provenance_state",
            ):
                require(item[key] == occurrence[key], "evidence/occurrence provenance")
        elif kind == "assertion":
            source = get(conn, "entity", item["source_entity_id"])
            evidence = get(conn, "evidence", item["evidence_id"])
            occurrence = get(conn, "occurrence", evidence["occurrence_id"])
            require(
                source["path"] == occurrence["path"]
                and source["language"] == occurrence["language"],
                "assertion source location",
            )
            legacy = evidence["provenance_state"] == "legacy_unavailable"
            require(
                legacy == (source["identity_basis"] == "legacy_projection"),
                "assertion source identity basis",
            )
            require(
                item["resolution"] != "LEGACY_UNAVAILABLE" or legacy,
                "legacy resolution discrimination",
            )
            for target_id in (
                [item["target_entity_id"]] if item["target_entity_id"] is not None else []
            ) + item["candidate_ids"]:
                target = get(conn, "entity", target_id)
                require(
                    legacy == (target["identity_basis"] == "legacy_projection"),
                    "assertion target identity basis",
                )


def producer(fingerprint: dict[str, Any] | None, legacy: bool = False) -> dict[str, Any]:
    return record(
        "producer",
        fingerprint=fingerprint,
        identity_basis="legacy_metadata"
        if legacy and fingerprint
        else "legacy_unavailable"
        if legacy
        else "parsed",
    )


def file_entity(path: str, language: str, legacy: bool = False) -> dict[str, Any]:
    return record(
        "entity",
        path=path,
        language=language,
        kind="file",
        name=path,
        owners=[],
        ordinal=0,
        identity_basis="legacy_projection" if legacy else "parsed",
    )


SCOPES = frozenset(
    {
        "class",
        "module",
        "method",
        "singleton_method",
        "class_definition",
        "function_definition",
        "class_declaration",
        "function_declaration",
        "function_expression",
        "method_definition",
        "arrow_function",
        "lambda",
        "block",
        "statement_block",
        "do_block",
    }
)


def structural_entities(
    tree,
    declarations: dict[int, tuple[Any, str, str]],
    path: str,
    language: str,
    *,
    scope_overrides: dict[int, tuple[str, str | None]] | None = None,
):
    """AST-order matching-sibling ordinals; comments/whitespace are irrelevant."""
    result = {}
    counters: dict[tuple[int, str, str | None], int] = {}
    pending: list[tuple[Any, list[dict[str, Any]], int]] = [(tree.root_node, [], 0)]
    while pending:
        node, owners, parent = pending.pop()
        declaration = declarations.get(node.id)
        override = scope_overrides.get(node.id) if language == "rust" and scope_overrides else None
        relevant = declaration is not None or override is not None or node.type in SCOPES
        children_owners, children_parent = owners, parent
        if relevant:
            kind, name = (
                (declaration[1], declaration[2])
                if declaration
                else override
                if override is not None
                else (node.type, None)
            )
            key = (parent, kind, name)
            ordinal = counters.get(key, 0)
            counters[key] = ordinal + 1
            if declaration:
                result[node.id] = record(
                    "entity",
                    path=path,
                    language=language,
                    kind=kind,
                    name=name,
                    owners=owners,
                    ordinal=ordinal,
                    identity_basis="parsed",
                )
            children_owners = [*owners, {"kind": kind, "name": name, "ordinal": ordinal}]
            children_parent = node.id
        pending.extend(
            (child, children_owners, children_parent) for child in reversed(node.named_children)
        )
    return result


def occurrence(
    node,
    path: str,
    language: str,
    source: bytes,
    entity_id: str | None,
    kind: str,
    role: str,
    source_hash: str | None = None,
):
    return record(
        "occurrence",
        entity_id=entity_id,
        kind=kind,
        path=path,
        language=language,
        source_sha256=source_hash or hashlib.sha256(source).hexdigest(),
        source_size=len(source),
        start_byte=node.start_byte,
        end_byte=node.end_byte,
        line=node.start_point[0] + 1,
        column=node.start_point[1],
        end_line=node.end_point[0] + 1,
        end_column=node.end_point[1],
        role=role,
        provenance_state="source",
    )


def provenance(occurrence_record: dict[str, Any], producer_id: str):
    return record(
        "evidence",
        occurrence_id=occurrence_record["id"],
        producer_id=producer_id,
        **{
            key: occurrence_record[key]
            for key in (
                "source_sha256",
                "source_size",
                "start_byte",
                "end_byte",
                "provenance_state",
            )
        },
    )


def assertion(
    source: str,
    target: str | None,
    relation: str,
    evidence_id: str,
    resolution: str,
    raw_target: str,
    candidates=(),
    reason: str | None = None,
):
    return record(
        "assertion",
        source_entity_id=source,
        target_entity_id=target,
        relation=relation,
        evidence_id=evidence_id,
        resolution=resolution,
        candidate_ids=sorted(set(candidates)),
        raw_target=raw_target,
        unresolved_reason=reason,
    )


# Supporting first-proof records are separate from active query projections.
# Unchanged canonical facts retain atlas-evidence.v1 identities.
OBSERVATION_FORMAT = "atlas-evidence.v2"
FIELDS["history"] = {"type", "id", "path", "language", "records"}
FIELDS["observation"] = {
    "type",
    "id",
    "path",
    "language",
    "observed_source_sha256",
    "state",
    "reason",
    "location",
    "historical_id",
    "retained_record_ids",
    "retained_evidence_ids",
    "retained_fact_source_sha256s",
    "retained_producer_ids",
}


def validate_observation(item: dict[str, Any]) -> None:
    require(relative_path(item["path"]), "observation path")
    require(isinstance(item["language"], str) and bool(item["language"]), "observation language")
    if item["type"] == "history":
        records_ = item["records"]
        require(isinstance(records_, list) and bool(records_), "historical closure")
        require(
            all(isinstance(r, dict) and r.get("type") in {*TABLES, "projection"} for r in records_),
            "historical record types",
        )
        require(
            records_ == sorted(records_, key=lambda r: (r["type"], r["id"])), "historical order"
        )
        require(len({r["id"] for r in records_}) == len(records_), "duplicate historical record")
        for r in records_:
            validate_record(r)
        by_id = {r["id"]: r for r in records_}

        def linked(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key.endswith("_id") and child is not None:
                        require(child in by_id, "dangling historical endpoint")
                    elif key == "candidate_ids":
                        require(all(v in by_id for v in child), "historical candidates")
                    elif key != "id":
                        linked(child)
            elif isinstance(value, list):
                for child in value:
                    linked(child)

        for r in records_:
            linked(r)
        require(
            any(r["type"] == "occurrence" and r["path"] == item["path"] for r in records_),
            "historical source occurrence",
        )
    else:
        require(hash_value(item["observed_source_sha256"]), "observed source hash")
        require(
            item["state"] in {"indexed", "failed", "unsupported", "disabled"}, "observation state"
        )
        require(item["reason"] is None or isinstance(item["reason"], str), "observation reason")
        location = item["location"]
        require(
            location is None
            or (
                isinstance(location, dict)
                and set(location) == {"line", "column", "end_line", "end_column"}
                and all(integer(v) for v in location.values())
            ),
            "observation location",
        )
        if item["state"] == "failed":
            require(
                item["reason"] == "parse_error" and location is not None,
                "failed extraction diagnostic",
            )
        else:
            require(item["historical_id"] is None, "fresh observation cannot retain stale history")
        require(
            item["historical_id"] is None or hash_value(item["historical_id"]), "history identity"
        )
        for key in ("retained_record_ids", "retained_evidence_ids", "retained_producer_ids"):
            require(
                isinstance(item[key], list)
                and item[key] == sorted(set(item[key]))
                and all(hash_value(v) for v in item[key]),
                "retained IDs",
            )
        hashes = item["retained_fact_source_sha256s"]
        require(
            isinstance(hashes, list)
            and hashes == sorted(set(hashes))
            and all(hash_value(v) for v in hashes),
            "retained source hashes",
        )


def observation(path, language, source_hash, state, reason, location, history):
    records_ = history["records"] if history else []
    return record(
        "observation",
        path=path,
        language=language,
        observed_source_sha256=source_hash,
        state=state,
        reason=reason,
        location=location,
        historical_id=history["id"] if history else None,
        retained_record_ids=sorted(r["id"] for r in records_),
        retained_evidence_ids=sorted(r["id"] for r in records_ if r["type"] == "evidence"),
        retained_fact_source_sha256s=sorted(
            {
                r["source_sha256"]
                for r in records_
                if r["type"] == "evidence" and r["source_sha256"] is not None
            }
        ),
        retained_producer_ids=sorted(r["id"] for r in records_ if r["type"] == "producer"),
    )
