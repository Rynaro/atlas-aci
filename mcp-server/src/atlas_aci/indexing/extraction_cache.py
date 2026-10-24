"""Owner-local, content-addressed raw extraction packets; never resolved graph facts.

Integrity checks detect inconsistent derived data, not a malicious owner who can
fabricate an entirely coherent packet. Read-only graph operations never use this
store. Missing, failed, incompatible or invalid entries always require parsing.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from atlas_aci.storage import evidence as ev

FORMAT = "atlas-extraction-cache.v1"
PACKET_FIELDS = {
    "symbols",
    "refs",
    "assignments",
    "rationale",
    "records",
    "imports",
    "bindings",
    "file_entity",
}
SYMBOL_FIELDS = {
    "name",
    "kind",
    "path",
    "line_start",
    "line_end",
    "lang",
    "binding",
    "entity_id",
    "occurrence_id",
}
REF_FIELDS = {
    "callee_name",
    "path",
    "line",
    "lang",
    "col",
    "relation",
    "qualified",
    "qualifier_name",
    "binding",
    "source_entity_id",
    "occurrence_id",
    "evidence_id",
}
RATIONALE_FIELDS = {
    "path",
    "line",
    "text",
    "label",
    "lang",
    "occurrence_id",
    "evidence_id",
    "target_entity_id",
}
IMPORT_FIELDS = {
    "path",
    "local_name",
    "scope",
    "byte_start",
    "description",
    "lang",
    "source_entity_id",
    "occurrence_id",
    "evidence_id",
    "assertion_id",
}


def digest(value: Any) -> str:
    return hashlib.sha256(ev.canonical(value).encode()).hexdigest()


def inputs(source: bytes, path: str, language: str, base: dict[str, Any]) -> dict[str, Any]:
    return {
        "format": FORMAT,
        "packet_version": 1,
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "source_size": len(source),
        "path": path,
        "language": language,
        "schema": base["schema"],
        "producer": base["producer"],
        "configuration": base["configuration"],
    }


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError("Invalid extraction packet: " + reason)


def integer(value: Any, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def binding(value: Any, expected: dict[str, Any], occurrence: dict[str, Any]) -> None:
    require(isinstance(value, str), "binding encoding")
    obj = json.loads(value)
    base = {
        "scope",
        "scopes",
        "identity",
        "byte",
        "owner",
        "namespace",
        "category",
        "self_name",
        "self_scope",
        "this_allowed",
        "source_name",
        "source_kind",
        "receiver",
        "unsupported",
    }
    require(
        isinstance(obj, dict)
        and base <= obj.keys() <= base | {"import_export_safe", "unsupported_source_owner"},
        "binding fields",
    )
    require(
        all(isinstance(obj[k], str) for k in ("scope", "identity", "namespace", "category")),
        "binding strings",
    )
    require(type(obj["byte"]) is int and obj["byte"] == occurrence["start_byte"], "binding byte")
    prefix = expected["language"] + ":" + expected["path"] + ":"
    require(
        all(isinstance(obj[k], str) and obj[k].startswith(prefix) for k in ("scope", "identity")),
        "binding scope location",
    )

    require(
        isinstance(obj["scopes"], list) and all(isinstance(x, str) for x in obj["scopes"]),
        "binding scopes",
    )
    require(all(scope.startswith(prefix) for scope in obj["scopes"]), "binding scopes location")
    require(
        obj["self_scope"] is None
        or (isinstance(obj["self_scope"], str) and obj["self_scope"].startswith(prefix)),
        "self scope location",
    )
    for key in ("owner", "self_name", "self_scope", "source_name", "source_kind", "receiver"):
        require(obj[key] is None or isinstance(obj[key], str), "binding optional strings")
    for key in ("this_allowed", "unsupported", "import_export_safe", "unsupported_source_owner"):
        require(key not in obj or type(obj[key]) is bool, "binding boolean")


@dataclass
class ExtractionPacket:
    """All four raw output lists and all four mutable extractor sidechannels."""

    payload: dict[str, Any]

    def validate(self, expected: dict[str, Any], source: bytes) -> None:
        p = self.payload
        require(isinstance(p, dict) and set(p) == PACKET_FIELDS, "complete closed packet")
        require(
            expected["source_sha256"] == hashlib.sha256(source).hexdigest()
            and expected["source_size"] == len(source),
            "consumed buffer",
        )
        require(
            all(isinstance(p[k], list) for k in PACKET_FIELDS - {"file_entity"}), "packet lists"
        )
        for family in PACKET_FIELDS - {"file_entity"}:
            encoded = [ev.canonical(row) for row in p[family]]
            require(len(encoded) == len(set(encoded)), "duplicate raw " + family)
        for family in ("symbols", "refs", "rationale", "imports"):
            require(all(isinstance(row, dict) for row in p[family]), "raw rows")
            ids = [row.get("occurrence_id") for row in p[family]]
            require(
                all(ev.hash_value(value) for value in ids) and len(ids) == len(set(ids)),
                "raw occurrence uniqueness",
            )
        path, lang = expected["path"], expected["language"]
        records: dict[str, dict[str, Any]] = {}
        starts = [0, *[i + 1 for i, value in enumerate(source) if value == 10]]
        for record in p["records"]:
            ev.validate_record(record)
            require(
                record["type"] in {"entity", "producer", "occurrence", "evidence"},
                "raw record kind",
            )
            require(record["id"] not in records, "duplicate ID")
            records[record["id"]] = record
            if record["type"] in {"entity", "occurrence"}:
                require((record["path"], record["language"]) == (path, lang), "path/language")
            if record["type"] == "entity":
                require(record["identity_basis"] == "parsed", "parsed entity")
            if record["type"] in {"occurrence", "evidence"}:
                require(
                    record["provenance_state"] == "source"
                    and record["source_sha256"] == expected["source_sha256"]
                    and record["source_size"] == len(source),
                    "consumed provenance",
                )
            if record["type"] == "occurrence":
                for prefix, offset in (("", record["start_byte"]), ("end_", record["end_byte"])):
                    line = bisect.bisect_right(starts, offset)
                    require(
                        (record[prefix + "line"], record[prefix + "column"])
                        == (line, offset - starts[line - 1]),
                        "byte/display span",
                    )
        producer = ev.producer(expected["producer"])
        require(
            [x for x in p["records"] if x["type"] == "producer"] == [producer],
            "exact current producer",
        )
        file_entity = ev.file_entity(path, lang)
        require(
            p["file_entity"] == file_entity["id"] and records.get(file_entity["id"]) == file_entity,
            "file entity",
        )
        # Reuse the authoritative closed-record and cross-link validator, without
        # building any graph projection or persisting a SQLite file.
        with sqlite3.connect(":memory:") as conn:
            conn.executescript(ev.DDL)
            for record in p["records"]:
                ev.put(conn, record)
            ev.validate_database(conn)
        used_occurrences: set[str] = set()
        used_evidence: set[str] = set()
        used_entities = {p["file_entity"]}

        def linked(occurrence_id, proof_id, entity_id, kind, role):
            occurrence = records.get(occurrence_id, {})
            require(
                occurrence.get("type") == "occurrence"
                and occurrence["kind"] == kind
                and occurrence["role"] == role
                and occurrence["entity_id"] == entity_id,
                "raw occurrence link",
            )
            used_occurrences.add(occurrence_id)
            used_entities.add(entity_id)
            if proof_id is not None:
                proof = records.get(proof_id, {})
                require(
                    proof.get("type") == "evidence"
                    and proof["occurrence_id"] == occurrence_id
                    and proof["producer_id"] == producer["id"],
                    "raw proof link",
                )
                used_evidence.add(proof_id)
            return occurrence

        for row in p["symbols"]:
            require(isinstance(row, dict) and set(row) == SYMBOL_FIELDS, "symbol fields")
            require(
                all(isinstance(row[k], str) and row[k] for k in ("name", "kind", "path", "lang")),
                "symbol strings",
            )
            require(
                integer(row["line_start"], 1) and integer(row["line_end"], row["line_start"]),
                "symbol lines",
            )
            entity = records.get(row["entity_id"], {})
            require(
                entity.get("type") == "entity"
                and (entity["name"], entity["kind"]) == (row["name"], row["kind"]),
                "symbol entity",
            )
            occurrence = linked(
                row["occurrence_id"],
                None,
                row["entity_id"],
                "definition",
                "definition:def." + row["kind"],
            )
            require(
                (row["line_start"], row["line_end"])
                == (occurrence["line"], occurrence["end_line"]),
                "symbol display",
            )
            binding(row["binding"], expected, occurrence)
        for row in p["refs"]:
            require(isinstance(row, dict) and set(row) == REF_FIELDS, "ref fields")
            require(
                isinstance(row["callee_name"], str)
                and bool(row["callee_name"])
                and row["relation"] in ev.RELATIONS
                and type(row["qualified"]) is bool,
                "raw reference",
            )
            require(
                row["qualifier_name"] is None or isinstance(row["qualifier_name"], str), "qualifier"
            )
            occurrence = linked(
                row["occurrence_id"],
                row["evidence_id"],
                row["source_entity_id"],
                "reference",
                "reference:" + row["relation"],
            )
            require(
                type(row["line"]) is int
                and type(row["col"]) is int
                and (row["line"], row["col"]) == (occurrence["line"], occurrence["column"]),
                "ref display",
            )
            binding(row["binding"], expected, occurrence)
        for row in p["rationale"]:
            require(isinstance(row, dict) and set(row) == RATIONALE_FIELDS, "rationale fields")
            require(
                integer(row["line"], 1)
                and isinstance(row["text"], str)
                and (row["label"] is None or isinstance(row["label"], str)),
                "rationale types",
            )
            occurrence = linked(
                row["occurrence_id"], row["evidence_id"], p["file_entity"], "rationale", "rationale"
            )
            require(
                occurrence["line"] <= row["line"] <= occurrence["end_line"],
                "rationale display span",
            )
            require(
                row["target_entity_id"] is None
                or records.get(row["target_entity_id"], {}).get("type") == "entity",
                "rationale target",
            )
            if row["target_entity_id"] is not None:
                used_entities.add(row["target_entity_id"])
        for row in p["imports"]:
            require(isinstance(row, dict) and set(row) == IMPORT_FIELDS, "import fields")
            require(
                row["assertion_id"] is None and lang == "python" and integer(row["byte_start"]),
                "raw import",
            )
            require(
                all(isinstance(row[k], str) for k in ("local_name", "scope", "description")),
                "import strings",
            )
            description = json.loads(row["description"])
            require(
                isinstance(description, dict)
                and set(description) == {"level", "module", "name", "local_name", "reason"},
                "import description",
            )
            require(
                integer(description["level"])
                and isinstance(description["module"], list)
                and all(isinstance(v, str) and v.isidentifier() for v in description["module"])
                and isinstance(description["name"], str)
                and bool(description["name"])
                and description["local_name"] == row["local_name"]
                and (description["reason"] is None or isinstance(description["reason"], str)),
                "import description types",
            )
            occurrence = linked(
                row["occurrence_id"],
                row["evidence_id"],
                row["source_entity_id"],
                "reference",
                "import:" + description["name"],
            )
            require(row["byte_start"] <= occurrence["start_byte"], "import byte order")
            require(row["scope"].startswith(lang + ":" + path + ":"), "import scope path")
        assignments = []
        for fact in p["assignments"]:
            require(
                isinstance(fact, list)
                and len(fact) == 4
                and all(isinstance(v, str) for v in fact[:3])
                and integer(fact[3]),
                "assignment types",
            )
            assignments.append(tuple(fact))
        require(len(assignments) == len(set(assignments)), "duplicate assignments")
        bound = []
        for entry in p["bindings"]:
            require(
                isinstance(entry, dict)
                and set(entry) == {"fact", "links"}
                and isinstance(entry["fact"], list)
                and isinstance(entry["links"], list)
                and len(entry["links"]) == 2,
                "binding links",
            )
            fact = tuple(entry["fact"])
            require(fact in assignments and fact not in bound, "assignment coverage")
            bound.append(fact)
            occurrence = linked(
                entry["links"][0],
                entry["links"][1],
                p["file_entity"],
                "reference",
                "binding:" + fact[2] + ":" + fact[0],
            )
            require(fact[3] == occurrence["start_byte"], "assignment byte")
            require(fact[1].startswith(lang + ":" + path + ":"), "assignment scope path")
        require(set(bound) == set(assignments), "complete assignment sidechannel")
        for key in ("symbols", "refs", "rationale", "imports"):
            for row in p[key]:
                require((row["path"], row["lang"]) == (path, lang), "raw row location")
        file_occurrences = [
            x for x in p["records"] if x["type"] == "occurrence" and x["role"] == "file"
        ]
        require(len(file_occurrences) == 1, "file observation closure")
        linked(file_occurrences[0]["id"], None, p["file_entity"], "definition", "file")
        # Every raw occurrence has exactly one producer proof. No omitted raw
        # list can be hidden behind otherwise valid canonical evidence.
        for proof in (x for x in p["records"] if x["type"] == "evidence"):
            require(proof["occurrence_id"] in used_occurrences, "unrepresented evidence")
            used_evidence.add(proof["id"])
        require(
            {x["id"] for x in p["records"] if x["type"] == "occurrence"} == used_occurrences,
            "complete occurrence coverage",
        )
        require(
            {x["id"] for x in p["records"] if x["type"] == "entity"} == used_entities,
            "complete entity coverage",
        )
        require(len(used_evidence) == len(used_occurrences), "complete proof coverage")


class ExtractionCache:
    def __init__(self, atlas_dir: Path):
        self.atlas_dir = atlas_dir
        self.directory = atlas_dir / "extraction-cache" / "v1"

    def path(self, expected: dict[str, Any]) -> Path:
        key = digest(expected)
        path = self.directory / key[:2] / (key + ".json")
        # Derived cache components may not redirect a writer or even a lookup
        # into source/sibling namespaces. Configured root aliases were already
        # canonicalized by CodeGraph; this rejects static cache misconfiguration.
        for component in (self.directory.parent, self.directory, path.parent, path):
            if component.is_symlink():
                raise OSError("Extraction cache path is a symlink")
        if not path.resolve().is_relative_to(self.atlas_dir.resolve()):
            raise OSError("Extraction cache escaped graph namespace")
        return path

    def lookup(
        self, expected: dict[str, Any], source: bytes
    ) -> tuple[ExtractionPacket | None, str]:
        try:
            item = json.loads(self.path(expected).read_bytes())
            require(
                isinstance(item, dict)
                and set(item)
                == {"format", "key", "inputs", "state", "packet", "failure", "checksum"},
                "envelope fields",
            )
            if (
                item["format"] != FORMAT
                or ev.canonical(item["inputs"]) != ev.canonical(expected)
                or item["key"] != digest(item["inputs"])
                or item["key"] != digest(expected)
            ):
                return None, "incompatible"
            require(
                item["checksum"] == digest({"packet": item["packet"], "failure": item["failure"]}),
                "checksum",
            )
            if item["state"] == "failed":
                require(
                    item["packet"] is None
                    and isinstance(item["failure"], dict)
                    and set(item["failure"]) == {"reason", "location"},
                    "failed entry",
                )
                return None, "failed"
            require(item["state"] == "complete" and item["failure"] is None, "complete entry")
            packet = ExtractionPacket(item["packet"])
            packet.validate(expected, source)
            return packet, "hit"
        except FileNotFoundError:
            return None, "missing"
        except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, IndexError):
            return None, "invalid"

    def write(
        self, expected: dict[str, Any], packet: ExtractionPacket | None, failure=None
    ) -> bool:
        temporary = None
        try:
            path = self.path(expected)
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"packet": packet.payload if packet else None, "failure": failure}
            item = {
                "format": FORMAT,
                "key": digest(expected),
                "inputs": expected,
                "state": "complete" if packet else "failed",
                **payload,
                "checksum": digest(payload),
            }
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, prefix=".entry-", delete=False
            ) as stream:
                temporary = Path(stream.name)
                stream.write(ev.canonical(item))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            return True
        except OSError:
            return False
        finally:
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)
