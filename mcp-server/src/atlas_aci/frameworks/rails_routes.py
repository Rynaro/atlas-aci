"""Rails explicit routes (ACI-S24): literal ``config/routes.rb`` routes -> controller actions.

The adapter only reads the bytes the build already captured and the syntax tree tree-sitter
produced from them. It never runs Ruby, Rails, rake, bundler or any initializer, never expands
``resources``/``namespace``/``scope``/``concern``/``mount``/``draw :x``, and never guesses: a
declaration is either a supported literal route or a closed-vocabulary diagnostic.

Facts are shared-store records:

* one ``rails_route`` occurrence per declaration (the exact statement span, path
  ``config/routes.rb``), cited by one evidence record from a ``{"rails": ...}`` producer;
* one ``routes_to`` assertion per declaration whose source is the ``config/routes.rb`` file
  entity. ``raw_target`` is canonical JSON carrying the verb, path, target text and the
  ``rails_convention.v1`` mapping label. A unique controller action is ``INFERRED``, two or more
  are ``AMBIGUOUS`` with their candidate identities, everything else is ``UNRESOLVED`` with a
  closed reason.

The facts are a derived global function of (``config/routes.rb`` bytes, the Ruby ``symbols``
table, this adapter). ``refresh`` rebuilds them from scratch on every enabled build.

Portability: no recursion, no tree walk, explicit ASCII character classes and ``fullmatch``,
sorted outputs. This module imports only the standard library, ``atlas_aci.storage.evidence``
and ``atlas_aci.frameworks``.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from atlas_aci.storage import evidence as ev

ADAPTER = ev.RAILS_ADAPTER
ROUTES_PATH = ev.RAILS_ROUTES_PATH
MANIFEST_KEY = "rails_routes"
MANIFEST_FORMAT = "atlas-rails-routes.v1"
CONVENTION_LABEL = "rails_convention.v1"
UNEXPANDED_LABEL = "unexpanded"
LABELS = frozenset({CONVENTION_LABEL, UNEXPANDED_LABEL})
UNSUPPORTED_ROLE = ev.RAILS_ROLE_PREFIX + "unsupported:"

VERBS = frozenset({"get", "post", "put", "patch", "delete"})
STATEMENT_CONSTRUCTS = frozenset(
    {
        "collection",
        "concern",
        "concerns",
        "constraints",
        "controller",
        "defaults",
        "devise_for",
        "direct",
        "draw",
        "match",
        "member",
        "mount",
        "namespace",
        "nested",
        "resolve",
        "resource",
        "resources",
        "root",
        "scope",
        "shallow",
    }
)
VERB_CONSTRUCTS = frozenset(
    {
        "route_block",
        "hash_rocket_target",
        "extra_arguments",
        "non_literal_path",
        "interpolated_literal",
        "malformed_literal",
        "unsupported_option",
        "missing_target",
        "non_literal_target",
        "malformed_target",
    }
)
OTHER_CONSTRUCTS = frozenset(
    {"conditional", "other_statement", "unrecognized_draw_receiver", "declaration_limit"}
)
CONSTRUCTS = STATEMENT_CONSTRUCTS | VERB_CONSTRUCTS | OTHER_CONSTRUCTS

AMBIGUOUS_REASON = "ambiguous_controller_action"
ACTION_NOT_FOUND = "action_not_found"
CONTROLLER_NOT_FOUND = "controller_not_found"
UNSUPPORTED_REASON = "unsupported_route_construct"
REASONS = frozenset({AMBIGUOUS_REASON, ACTION_NOT_FOUND, CONTROLLER_NOT_FOUND, UNSUPPORTED_REASON})

MAX_DECLARATIONS = 5000
MAX_PATH_BYTES = 512
MAX_TARGET_BYTES = 256
STATES = frozenset({"indexed", "absent", "failed"})

TARGET_RE = re.compile(r"[a-z][a-z0-9_]*(/[a-z][a-z0-9_]*)*#[a-z_][a-z0-9_]*")
WRAPPER_RE = re.compile(
    rb"Rails\.application\.routes"
    rb"|[A-Z][A-Za-z0-9_]*(::[A-Z][A-Za-z0-9_]*)*::Application\.routes"
)
CONDITIONAL_TYPES = frozenset(
    {"if", "unless", "if_modifier", "unless_modifier", "conditional", "case"}
)
DRAW_BLOCK_TYPES = frozenset({"do_block", "block"})
# Not statements: comments, and the heredoc body that follows the statement owning its marker.
SKIPPED_STATEMENTS = frozenset({"comment", "heredoc_body"})
ROUTE_KEYS = frozenset({"mapping", "path", "target", "verb"})
DIAGNOSTIC_KEYS = frozenset({"construct", "mapping"})
RESOLUTIONS = ("AMBIGUOUS", "INFERRED", "UNRESOLVED")


@dataclass(frozen=True)
class Declaration:
    """One statement of a recognised draw block: a supported route or a diagnostic."""

    node: Any
    verb: str | None = None
    path: str | None = None
    target: str | None = None
    construct: str | None = None

    @property
    def role(self) -> str:
        if self.construct is not None:
            return UNSUPPORTED_ROLE + self.construct
        return ev.RAILS_ROLE_PREFIX + str(self.verb)

    @property
    def raw_target(self) -> str:
        if self.construct is not None:
            return ev.canonical({"construct": self.construct, "mapping": UNEXPANDED_LABEL})
        return ev.canonical(
            {
                "mapping": CONVENTION_LABEL,
                "path": self.path,
                "target": self.target,
                "verb": self.verb,
            }
        )


def _text(source: bytes, node: Any) -> bytes:
    return source[node.start_byte : node.end_byte]


def literal(source: bytes, node: Any, limit: int) -> tuple[str | None, str | None]:
    """``(value, error)`` for a plain string literal; the value is the source bytes between
    the delimiters, never a concatenation of child nodes (a lone ``#`` may split the content
    into several ``string_content`` children)."""
    if node.type != "string":
        return None, "non_literal"
    raw = _text(source, node)
    if len(raw) < 2 or raw[:1] not in (b'"', b"'") or raw[-1:] != raw[:1]:
        return None, "malformed_literal"
    children = list(node.children)
    inner = children[1:-1]
    if any(child.type == "interpolation" for child in inner):
        return None, "interpolated_literal"
    if len(children) < 2 or any(child.type != "string_content" for child in inner):
        return None, "malformed_literal"
    body = raw[1:-1]
    if not body or len(body) > limit:
        return None, "malformed_literal"
    try:
        return body.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, "malformed_literal"


def _route(source: bytes, node: Any, verb: str) -> Declaration:
    """Verb checks; the first failure wins (D4 step 4)."""

    def diagnostic(code: str) -> Declaration:
        return Declaration(node, construct=code)

    if node.child_by_field_name("block") is not None:
        return diagnostic("route_block")
    arguments = node.child_by_field_name("arguments")
    named = [
        c
        for c in (arguments.named_children if arguments is not None else [])
        if c.type != "comment"
    ]
    pairs = [c for c in named if c.type == "pair"]
    if any(any(part.type == "=>" for part in pair.children) for pair in pairs):
        return diagnostic("hash_rocket_target")
    positional = [c for c in named if c.type != "pair"]
    if len(positional) != 1 or positional[0].type in {
        "splat_argument",
        "hash_splat_argument",
        "block_argument",
        "hash",
    }:
        return diagnostic("extra_arguments")
    path, error = literal(source, positional[0], MAX_PATH_BYTES)
    if path is None:
        return diagnostic("non_literal_path" if error == "non_literal" else str(error))
    options: dict[str, Any] = {}
    for pair in pairs:
        key = pair.child_by_field_name("key")
        value = pair.child_by_field_name("value")
        if key is None or value is None or key.type != "hash_key_symbol":
            return diagnostic("unsupported_option")
        name = _text(source, key).decode("utf-8", errors="replace")
        if name not in {"to", "as"} or name in options:
            return diagnostic("unsupported_option")
        options[name] = value
    alias = options.get("as")
    # `as:` is accepted but not recorded; it must still be a plain literal or a symbol.
    if (
        alias is not None
        and alias.type != "simple_symbol"
        and literal(source, alias, MAX_PATH_BYTES)[0] is None
    ):
        return diagnostic("unsupported_option")
    if "to" not in options:
        return diagnostic("missing_target")
    target, error = literal(source, options["to"], MAX_TARGET_BYTES)
    if target is None:
        return diagnostic("non_literal_target" if error == "non_literal" else str(error))
    if TARGET_RE.fullmatch(target) is None:
        return diagnostic("malformed_target")
    return Declaration(node, verb=verb, path=path, target=target)


def _classify(source: bytes, node: Any) -> Declaration:
    """D4 step 3: precedence-ordered classification of one draw-block statement."""
    kind = node.type
    if kind == "call" and node.child_by_field_name("receiver") is None:
        method = node.child_by_field_name("method")
        name = _text(source, method).decode("utf-8", errors="replace") if method else ""
        if name in VERBS:
            return _route(source, node, name)
        if name in STATEMENT_CONSTRUCTS:
            return Declaration(node, construct=name)
    elif kind == "identifier":
        name = _text(source, node).decode("utf-8", errors="replace")
        if name in STATEMENT_CONSTRUCTS:
            return Declaration(node, construct=name)
    if kind in CONDITIONAL_TYPES:
        return Declaration(node, construct="conditional")
    return Declaration(node, construct="other_statement")


def _draw_block(source: bytes, node: Any) -> Any | None:
    """The block of a top-level ``<routes>.draw do ... end``; ``None`` if this is not one."""
    if node.type != "call":
        return None
    method = node.child_by_field_name("method")
    if method is None or _text(source, method) != b"draw":
        return None
    block = node.child_by_field_name("block")
    if block is None or block.type not in DRAW_BLOCK_TYPES:
        return None
    return block


def _wrapper(source: bytes, node: Any) -> bool:
    receiver = node.child_by_field_name("receiver")
    return (
        receiver is not None
        and node.child_by_field_name("arguments") is None
        and WRAPPER_RE.fullmatch(_text(source, receiver)) is not None
    )


def scan(tree: Any, source: bytes) -> tuple[list[Declaration], bool]:
    """Pure extraction: ``(declarations in source order, truncated)``.

    Only direct statements of a recognised top-level draw block are read; nested blocks are
    never entered. Fixed depth, no recursion.
    """
    declarations: list[Declaration] = []
    for statement in tree.root_node.named_children:
        if statement.type == "comment":
            continue
        block = _draw_block(source, statement)
        if block is None:
            continue  # ordinary top-level Ruby is ignored: no record, no diagnostic
        recognised = _wrapper(source, statement)
        if recognised:
            body = block.child_by_field_name("body")
            nodes = [
                c
                for c in (body.named_children if body is not None else [])
                if c.type not in SKIPPED_STATEMENTS
            ]
        else:
            nodes = [statement]
        for node in nodes:
            if len(declarations) >= MAX_DECLARATIONS:
                declarations.append(Declaration(node, construct="declaration_limit"))
                return declarations, True
            declarations.append(
                _classify(source, node)
                if recognised
                else Declaration(node, construct="unrecognized_draw_receiver")
            )
    return declarations, False


def camelize(controller: str) -> str:
    """ActiveSupport default inflection for the ``TARGET_RE`` alphabet (no acronyms)."""
    return (
        "::".join(
            "".join(part[:1].upper() + part[1:] for part in segment.split("_"))
            for segment in controller.split("/")
        )
        + "Controller"
    )


Resolution = tuple[str, str | None, list[str], str | None]


class Resolver:
    """Convention lookup over the build's Ruby ``symbols`` table (one scan per build)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.classes: dict[str, set[str]] = {}
        for row in conn.execute(
            "SELECT path, binding FROM symbols WHERE lang='ruby' AND kind='class' "
            "ORDER BY path, line_start, id"
        ):
            binding = _binding(row[1])
            namespace = binding.get("namespace")
            if isinstance(namespace, str):
                self.classes.setdefault(namespace, set()).add(row[0])
        self.methods: dict[str, list[tuple[str, str, dict[str, Any]]]] = {}

    def _methods(self, action: str) -> list[tuple[str, str, dict[str, Any]]]:
        if action not in self.methods:
            self.methods[action] = [
                (row[0], row[1], _binding(row[2]))
                for row in self.conn.execute(
                    "SELECT entity_id, path, binding FROM symbols "
                    "WHERE lang='ruby' AND kind='method' AND name=? "
                    "ORDER BY path, line_start, id",
                    (action,),
                )
            ]
        return self.methods[action]

    def resolve(self, target: str) -> Resolution:
        controller, _, action = target.partition("#")
        qualified = camelize(controller)
        paths = self.classes.get(qualified)
        if paths is None:
            return "UNRESOLVED", None, [], CONTROLLER_NOT_FOUND
        owner = "ruby:" + qualified
        ids = sorted(
            {
                entity_id
                for entity_id, path, binding in self._methods(action)
                if entity_id is not None
                and binding.get("owner") == owner
                and binding.get("category") == "instance"
                and path in paths
            }
        )
        if not ids:
            return "UNRESOLVED", None, [], ACTION_NOT_FOUND
        if len(ids) == 1:
            return "INFERRED", ids[0], [], None
        return "AMBIGUOUS", None, ids, AMBIGUOUS_REASON


def _binding(value: Any) -> dict[str, Any]:
    try:
        result = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return result if isinstance(result, dict) else {}


def adapter_sha256() -> str:
    """Identity of this adapter's own bytes (the only file the adapter reads)."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _rails_producer_clause() -> str:
    return "SELECT id FROM producers WHERE json_extract(payload,'$.fingerprint.rails') IS NOT NULL"


def has_rails_records(conn: sqlite3.Connection) -> bool:
    """Cheap probe: rails records cannot exist without their producer or the manifest key."""
    if conn.execute("SELECT 1 FROM manifest WHERE key=?", (MANIFEST_KEY,)).fetchone():
        return True
    return conn.execute(_rails_producer_clause() + " LIMIT 1").fetchone() is not None


def purge(conn: sqlite3.Connection) -> None:
    """Remove every rails-owned record and the manifest key (whatever the flag)."""
    conn.execute(
        "DELETE FROM assertions WHERE json_extract(payload,'$.relation')=?",
        (ev.RAILS_RELATION,),
    )
    conn.execute(
        "DELETE FROM evidence WHERE json_extract(payload,'$.producer_id') IN ("
        + _rails_producer_clause()
        + ")"
    )
    conn.execute(
        "DELETE FROM occurrences WHERE json_extract(payload,'$.kind')=?",
        (ev.RAILS_ROUTE_KIND,),
    )
    conn.execute(
        "DELETE FROM producers WHERE json_extract(payload,'$.fingerprint.rails') IS NOT NULL"
    )
    conn.execute("DELETE FROM manifest WHERE key=?", (MANIFEST_KEY,))


def _summary(
    state: str,
    declarations: int = 0,
    routes: int = 0,
    resolutions: dict[str, int] | None = None,
    truncated: bool = False,
    producer_id: str | None = None,
) -> dict[str, Any]:
    counts = {name: 0 for name in RESOLUTIONS}
    counts.update(resolutions or {})
    return {
        "format": MANIFEST_FORMAT,
        "adapter": ADAPTER,
        "adapter_sha256": adapter_sha256(),
        "routes_path": ROUTES_PATH,
        "state": state,
        "declarations": declarations,
        "routes": routes,
        "diagnostics": declarations - routes,
        "resolutions": counts,
        "truncated": truncated,
        "producer_id": producer_id,
    }


def _write_manifest(conn: sqlite3.Connection, summary: dict[str, Any]) -> dict[str, Any]:
    conn.execute(
        "INSERT OR REPLACE INTO manifest(key,value) VALUES (?,?)",
        (MANIFEST_KEY, ev.canonical(summary)),
    )
    return summary


def refresh(
    conn: sqlite3.Connection,
    *,
    enabled: bool,
    capture: tuple[bytes, str] | None,
    observations: Iterable[dict[str, Any]],
    get_parser: Callable[[str], Any],
    graph_producer_id: str,
) -> dict[str, Any] | None:
    """Purge every rails-owned record, then (enabled only) rebuild them from scratch.

    Runs inside the build's staging transaction after edge resolution. ``get_parser`` is only
    called when the adapter is enabled and ``config/routes.rb`` is an indexed observation.
    """
    if has_rails_records(conn):
        purge(conn)
    if not enabled:
        return None
    # `observations` also holds `history` records; only the observation is current state.
    current = {
        item["path"]: item
        for item in observations
        if item["type"] == "observation" and item["path"] == ROUTES_PATH
    }
    observed = current.get(ROUTES_PATH)
    if observed is None:
        return _write_manifest(conn, _summary("absent"))
    file_id = ev.file_entity(ROUTES_PATH, "ruby")["id"]
    if (
        observed["state"] != "indexed"
        or capture is None
        or capture[1] != observed["observed_source_sha256"]
        or conn.execute("SELECT 1 FROM entities WHERE id=?", (file_id,)).fetchone() is None
    ):
        return _write_manifest(conn, _summary("failed"))
    source, source_hash = capture
    tree = get_parser("ruby").parse(source)
    if tree.root_node.has_error:
        return _write_manifest(conn, _summary("failed"))
    declarations, truncated = scan(tree, source)
    resolver = Resolver(conn)
    producer_id: str | None = None
    counts = {name: 0 for name in RESOLUTIONS}
    routes = 0
    if declarations:
        producer = ev.producer(
            {
                "rails": {
                    "adapter": ADAPTER,
                    "adapter_sha256": adapter_sha256(),
                    "graph_producer_id": graph_producer_id,
                    "routes_path": ROUTES_PATH,
                }
            }
        )
        producer_id = producer["id"]
        ev.put(conn, producer)
    for declaration in declarations:
        occurrence = ev.occurrence(
            declaration.node,
            ROUTES_PATH,
            "ruby",
            source,
            file_id,
            ev.RAILS_ROUTE_KIND,
            declaration.role,
            source_hash,
        )
        proof = ev.provenance(occurrence, str(producer_id))
        if declaration.construct is not None:
            resolution: Resolution = "UNRESOLVED", None, [], UNSUPPORTED_REASON
        else:
            routes += 1
            resolution = resolver.resolve(str(declaration.target))
        assertion = ev.assertion(
            file_id,
            resolution[1],
            ev.RAILS_RELATION,
            proof["id"],
            resolution[0],
            declaration.raw_target,
            resolution[2],
            resolution[3],
        )
        counts[resolution[0]] += 1
        ev.put(conn, occurrence, ROUTES_PATH)
        ev.put(conn, proof, ROUTES_PATH)
        ev.put(conn, assertion, ROUTES_PATH)
    return _write_manifest(
        conn,
        _summary("indexed", len(declarations), routes, counts, truncated, producer_id),
    )


def record_ids(records: list[dict[str, Any]]) -> set[str]:
    """IDs of every canonical record contributed by a rails producer."""
    producers = {
        r["id"]
        for r in records
        if r["type"] == "producer"
        and isinstance(r["fingerprint"], dict)
        and "rails" in r["fingerprint"]
    }
    proofs = {r["id"] for r in records if r["type"] == "evidence" and r["producer_id"] in producers}
    occurrences = {
        r["occurrence_id"] for r in records if r["type"] == "evidence" and r["id"] in proofs
    }
    assertions = {
        r["id"] for r in records if r["type"] == "assertion" and r["evidence_id"] in proofs
    }
    return producers | proofs | occurrences | assertions


def _decode(raw_target: Any) -> dict[str, Any]:
    ev.require(isinstance(raw_target, str), "rails raw_target type")
    try:
        value = json.loads(raw_target)
    except ValueError as exc:
        raise ValueError("Invalid evidence: rails raw_target is not JSON") from exc
    ev.require(isinstance(value, dict), "rails raw_target is not an object")
    ev.require(ev.canonical(value) == raw_target, "rails raw_target is not canonical")
    return value


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _byte_length(value: str) -> int:
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValueError("Invalid evidence: rails literal is not UTF-8") from exc


def _method_entity(conn: sqlite3.Connection, identifier: Any) -> None:
    ev.require(ev.hash_value(identifier), "rails target identity")
    entity = ev.get(conn, "entity", identifier)
    ev.require(
        entity["kind"] == "method" and entity["language"] == "ruby",
        "rails target is not a ruby method entity",
    )


def _check_assertion(conn: sqlite3.Connection, item: dict[str, Any], role: str) -> None:
    ev.require(
        item["source_entity_id"] == ev.file_entity(ROUTES_PATH, "ruby")["id"], "rails source"
    )
    value = _decode(item["raw_target"])
    resolution = item["resolution"]
    reason = item["unresolved_reason"]
    if set(value) == DIAGNOSTIC_KEYS:
        construct = value["construct"]
        ev.require(
            value["mapping"] == UNEXPANDED_LABEL
            and isinstance(construct, str)
            and construct in CONSTRUCTS
            and role == UNSUPPORTED_ROLE + construct,
            "rails diagnostic",
        )
        ev.require(
            resolution == "UNRESOLVED"
            and reason == UNSUPPORTED_REASON
            and item["target_entity_id"] is None
            and not item["candidate_ids"],
            "rails diagnostic resolution",
        )
        return
    ev.require(set(value) == ROUTE_KEYS, "rails raw_target shape")
    verb, path, target = value["verb"], value["path"], value["target"]
    ev.require(
        value["mapping"] == CONVENTION_LABEL
        and isinstance(verb, str)
        and verb in VERBS
        and role == ev.RAILS_ROLE_PREFIX + verb
        and _is_text(path)
        and 1 <= _byte_length(path) <= MAX_PATH_BYTES
        and _is_text(target)
        and _byte_length(target) <= MAX_TARGET_BYTES
        and TARGET_RE.fullmatch(target) is not None,
        "rails route",
    )
    if resolution == "INFERRED":
        ev.require(reason is None and not item["candidate_ids"], "rails inferred route")
        _method_entity(conn, item["target_entity_id"])
    elif resolution == "AMBIGUOUS":
        ev.require(
            reason == AMBIGUOUS_REASON
            and item["target_entity_id"] is None
            and len(item["candidate_ids"]) >= 2,
            "rails ambiguous route",
        )
        for identifier in item["candidate_ids"]:
            _method_entity(conn, identifier)
    else:
        ev.require(
            resolution == "UNRESOLVED"
            and reason in {ACTION_NOT_FOUND, CONTROLLER_NOT_FOUND}
            and item["target_entity_id"] is None
            and not item["candidate_ids"],
            "rails unresolved route",
        )


def validate_records(conn: sqlite3.Connection) -> None:
    """Referential and shape completeness of the rails records (build, predecessor, import,
    export). Never compares the adapter hash to the running adapter; every failure is
    ``ValueError("Invalid evidence: rails ...")``."""
    try:
        _validate_records(conn)
    except (KeyError, TypeError, IndexError, AttributeError) as exc:
        raise ValueError("Invalid evidence: rails malformed record") from exc


def _validate_records(conn: sqlite3.Connection) -> None:
    producers = {
        row[0]: json.loads(row[1])
        for row in conn.execute(
            "SELECT id, payload FROM producers "
            "WHERE json_extract(payload,'$.fingerprint.rails') IS NOT NULL"
        )
    }
    ev.require(len(producers) <= 1, "rails: more than one producer")
    for producer in producers.values():
        ev.require(
            conn.execute(
                "SELECT 1 FROM producers WHERE id=?",
                (producer["fingerprint"]["rails"]["graph_producer_id"],),
            ).fetchone()
            is not None,
            "rails: graph producer is missing",
        )
    file_id = ev.file_entity(ROUTES_PATH, "ruby")["id"]
    occurrences = {
        row[0]: json.loads(row[1])
        for row in conn.execute(
            "SELECT id, payload FROM occurrences WHERE json_extract(payload,'$.kind')=?",
            (ev.RAILS_ROUTE_KIND,),
        )
    }
    proofs: dict[str, dict[str, Any]] = {}
    if producers:
        for row in conn.execute(
            "SELECT id, payload FROM evidence "
            "WHERE json_extract(payload,'$.producer_id') IN (" + _rails_producer_clause() + ")"
        ):
            proofs[row[0]] = json.loads(row[1])
    assertions = {
        row[0]: json.loads(row[1])
        for row in conn.execute(
            "SELECT id, payload FROM assertions WHERE json_extract(payload,'$.relation')=?",
            (ev.RAILS_RELATION,),
        )
    }
    ev.require(len(assertions) <= MAX_DECLARATIONS + 1, "rails: too many declarations")
    ev.require(
        all(proof["occurrence_id"] in occurrences for proof in proofs.values()),
        "rails: evidence cites a non-route occurrence",
    )
    cited_occurrences = sorted(proof["occurrence_id"] for proof in proofs.values())
    ev.require(
        cited_occurrences == sorted(occurrences),
        "rails: route occurrence without exactly one evidence",
    )
    ev.require(
        all(item["entity_id"] == file_id for item in occurrences.values()),
        "rails: route occurrence entity",
    )
    ev.require(
        sorted(item["evidence_id"] for item in assertions.values()) == sorted(proofs),
        "rails: evidence without exactly one routes_to assertion",
    )
    for item in assertions.values():
        proof = proofs[item["evidence_id"]]
        _check_assertion(conn, item, occurrences[proof["occurrence_id"]]["role"])
    for identifier in producers:
        ev.require(
            any(proof["producer_id"] == identifier for proof in proofs.values()),
            "rails: producer is cited by no evidence",
        )
