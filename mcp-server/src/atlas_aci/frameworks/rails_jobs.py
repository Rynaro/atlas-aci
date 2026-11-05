"""Rails direct job enqueues and positional argument paths (ACI-S26).

The adapter only reads the bytes the build already captured and the syntax trees tree-sitter
produced from them. It never runs Ruby, Rails, Sidekiq, ActiveJob, Redis or any job, never opens a
queue or a database of the repository, never evaluates an argument and never guesses: an enqueue
is either a supported, lexically resolved fact or a closed-vocabulary diagnostic.

Facts are shared-store records, one ``rails_job`` occurrence per enqueue statement (the exact
``call`` span) and per mapped or diagnosed argument, each cited by one evidence record from a
``{"rails_jobs": ...}`` producer, and one assertion per occurrence:

* ``async_enqueue``: a direct ``Const.perform_async(...)`` (a Sidekiq class) or
  ``Const.perform_later(...)`` (an ActiveJob class) linked to the one explicit instance
  ``perform`` method entity of the class it names. It is an asynchronous boundary, never a call,
  and never proof that the job ran. It is ``INFERRED`` for exactly one existing name with exactly
  one recognised ``perform``, ``AMBIGUOUS`` (with the sorted ``perform`` candidates) for several,
  and ``UNRESOLVED`` with a closed reason otherwise. It is never ``EXTRACTED``.
* ``async_argument``: for a supported fixed positional arity, positional argument ``i`` of the
  enqueue delivered to parameter ``i`` of the same ``perform`` (``INFERRED``, spanning the
  argument), or exactly one ``UNRESOLVED`` diagnostic per enqueue whose arguments cannot be
  mapped. No partial mapping is ever emitted. Argument values are never evaluated or copied.

The facts are a derived global function of (captured Ruby bytes that mention a job construct, the
Ruby ``symbols`` and ``refs`` tables, this adapter). ``refresh`` rebuilds them from scratch on
every enabled build.

Portability: no recursion, no generic tree walk, explicit ASCII character classes and
``fullmatch``, sorted outputs. This module imports only the standard library and
``atlas_aci.storage.evidence``.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from atlas_aci.storage import evidence as ev

ADAPTER = ev.RAILS_JOBS_ADAPTER
MANIFEST_KEY = "rails_jobs"
MANIFEST_FORMAT = "atlas-rails-jobs.v1"
ROLE_PREFIX = ev.RAILS_JOB_ROLE_PREFIX
ENQUEUE_ROLE = ROLE_PREFIX + "enqueue:"
ENQUEUE_UNSUPPORTED_ROLE = ENQUEUE_ROLE + "unsupported:"
ARGUMENT_ROLE = ROLE_PREFIX + "argument:"
MAPPED_ROLE = ARGUMENT_ROLE + "mapped"
ARGUMENT_UNSUPPORTED_ROLE = ARGUMENT_ROLE + "unsupported:"
RECEIVER_LABEL = "rails_job_receiver.v1"
POSITIONAL_LABEL = "rails_job_positional.v1"
UNEXPANDED_LABEL = "unexpanded"
LABELS = frozenset({RECEIVER_LABEL, POSITIONAL_LABEL, UNEXPANDED_LABEL})

SIDEKIQ = "sidekiq"
ACTIVE_JOB = "active_job"
MECHANISMS = frozenset({SIDEKIQ, ACTIVE_JOB})
ENQUEUE_METHODS = {"perform_async": SIDEKIQ, "perform_later": ACTIVE_JOB}
DELAYED_METHODS = frozenset({"perform_in", "perform_at"})
SITE_METHODS = frozenset(ENQUEUE_METHODS) | DELAYED_METHODS
SIDEKIQ_MIXINS = frozenset(
    {b"Sidekiq::Worker", b"Sidekiq::Job", b"::Sidekiq::Worker", b"::Sidekiq::Job"}
)
ACTIVE_JOB_BASES = frozenset({b"ApplicationJob", b"ActiveJob::Base", b"::ActiveJob::Base"})

ENQUEUE_CONSTRUCTS = frozenset(
    {"dynamic_receiver", "set_chain", "delayed_enqueue", "unbound_namespace", "site_limit"}
)
# In precedence order: the first one present on a call is the one reported.
CALL_ARGUMENT_CONSTRUCTS = (
    "forwarded_arguments",
    "splat",
    "hash_splat",
    "keyword_arguments",
    "block_argument",
    "block",
)
ARGUMENT_TYPE_CODES = {
    "forward_argument": "forwarded_arguments",
    "splat_argument": "splat",
    "hash_splat_argument": "hash_splat",
    "pair": "keyword_arguments",
    "block_argument": "block_argument",
}
ARGUMENT_CONSTRUCTS = frozenset(CALL_ARGUMENT_CONSTRUCTS) | {
    "argument_limit",
    "unsupported_signature",
    "arity_mismatch",
}
# A non-INFERRED enqueue can only fail on the call side or on the argument cap.
UNBOUND_ARGUMENT_CONSTRUCTS = frozenset(CALL_ARGUMENT_CONSTRUCTS) | {"argument_limit"}

CLASS_NOT_FOUND_REASON = "job_class_not_found"
PERFORM_NOT_FOUND_REASON = "perform_not_found"
NOT_RECOGNIZED_REASON = "target_not_recognized_job"
AMBIGUOUS_REASON = "ambiguous_job_target"
UNSUPPORTED_REASON = "unsupported_argument_mapping"
REASONS = frozenset(
    {
        CLASS_NOT_FOUND_REASON,
        PERFORM_NOT_FOUND_REASON,
        NOT_RECOGNIZED_REASON,
        AMBIGUOUS_REASON,
        UNSUPPORTED_REASON,
    }
)
UNRESOLVED_ENQUEUE_REASONS = frozenset(
    {CLASS_NOT_FOUND_REASON, PERFORM_NOT_FOUND_REASON, NOT_RECOGNIZED_REASON}
)
ARGUMENT_STATES = frozenset({"mapped", "none", "unsupported", "unbound"})
RESOLUTIONS = {
    ev.ASYNC_ENQUEUE_RELATION: ("AMBIGUOUS", "INFERRED", "UNRESOLVED"),
    ev.ASYNC_ARGUMENT_RELATION: ("INFERRED", "UNRESOLVED"),
}
JOB_RELATIONS = ev.RAILS_JOB_RELATIONS

MAX_FILES = 10000
MAX_FILE_BYTES = 1048576
MAX_CAPTURE_BYTES = 67108864
MAX_LIMITED_PATHS = 10000
MAX_SITES = 5000
MAX_ARGUMENTS = 16
MAX_RECEIVER_BYTES = 256
MAX_PARAM_NAME_BYTES = 128

CLASS_NAME_RE = re.compile(r"(::)?[A-Z][A-Za-z0-9_]*(::[A-Z][A-Za-z0-9_]*)*")
JOB_KEY_RE = re.compile(r"[A-Z][A-Za-z0-9_]*(::[A-Z][A-Za-z0-9_]*)*")
PARAM_RE = re.compile(r"[a-z_][A-Za-z0-9_]*")
# Over-capture (`perform_inline`, `def perform_now`) is harmless and bounded; every real site
# mentions one of its own method names, so this can never miss one.
CAPTURE_RE = re.compile(
    rb"perform_async|perform_later|perform_in|perform_at|Sidekiq::Worker|Sidekiq::Job"
    rb"|ApplicationJob|ActiveJob::Base|def[ \t]+perform"
)

ENQUEUE_KEYS = frozenset({"arguments", "arity", "kind", "mapping", "mechanism", "receiver"})
ENQUEUE_DIAGNOSTIC_KEYS = frozenset({"construct", "kind", "mapping"})
MAPPED_KEYS = frozenset({"arg", "enqueue", "mapping", "param", "param_name"})
ARGUMENT_DIAGNOSTIC_KEYS = frozenset({"construct", "enqueue", "mapping"})
SKIPPED_CHILDREN = frozenset({"comment", "heredoc_body"})
CONSTANT_TYPES = frozenset({"constant", "scope_resolution"})
SOURCE_KINDS = frozenset({"method", "class", "module", "file"})
MAX_ASSERTIONS = (MAX_SITES + 1) * (1 + MAX_ARGUMENTS)

Resolution = tuple[str, str | None, list[str], str | None]


@dataclass(frozen=True)
class Span:
    """A plain source span (all `ev.occurrence` reads); no parse tree is retained."""

    start_byte: int
    end_byte: int
    start_point: tuple[int, int]
    end_point: tuple[int, int]


def _span(node: Any) -> Span:
    return Span(
        node.start_byte,
        node.end_byte,
        (node.start_point[0], node.start_point[1]),
        (node.end_point[0], node.end_point[1]),
    )


def _text(source: bytes, node: Any) -> bytes:
    return source[node.start_byte : node.end_byte]


def _decode(raw: bytes) -> str | None:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


# ---- pure extraction ----


@dataclass(frozen=True)
class Signature:
    """The fixed positional part of an explicit ``perform``: ``names`` are the required then the
    optional parameter names in order. ``supported`` is false for any shape the mapping cannot
    state exactly (splat, destructuring, forwarding, a required keyword, a required parameter
    after an optional one, an unrecognised node or name)."""

    supported: bool
    names: tuple[str, ...]
    required: int
    optional: int


UNSUPPORTED_SIGNATURE = Signature(False, (), 0, 0)
TOLERATED_PARAMETERS = frozenset({"hash_splat_parameter", "hash_splat_nil", "block_parameter"})
UNSUPPORTED_PARAMETERS = frozenset(
    {"splat_parameter", "destructured_parameter", "forward_parameter"}
)


def _parameter_name(source: bytes, node: Any) -> str | None:
    if len(_text(source, node)) > MAX_PARAM_NAME_BYTES:
        return None
    name = _decode(_text(source, node))
    if name is None or PARAM_RE.fullmatch(name) is None:
        return None
    return name


def signature(source: bytes, method: Any) -> Signature:
    """D4.3: the named children of the ``parameters`` field (comments aside)."""
    parameters = method.child_by_field_name("parameters")
    children = (
        []
        if parameters is None
        else [child for child in parameters.named_children if child.type != "comment"]
    )
    names: list[str] = []
    required = 0
    optional = 0
    for child in children:
        kind = child.type
        if kind == "identifier":
            if optional:
                return UNSUPPORTED_SIGNATURE
            name = _parameter_name(source, child)
            if name is None:
                return UNSUPPORTED_SIGNATURE
            names.append(name)
            required += 1
        elif kind == "optional_parameter":
            holder = child.child_by_field_name("name")
            name = None if holder is None else _parameter_name(source, holder)
            if name is None:
                return UNSUPPORTED_SIGNATURE
            names.append(name)
            optional += 1
        elif kind == "keyword_parameter":
            if child.child_by_field_name("value") is None:
                return UNSUPPORTED_SIGNATURE
        elif kind in UNSUPPORTED_PARAMETERS or kind not in TOLERATED_PARAMETERS:
            return UNSUPPORTED_SIGNATURE
    return Signature(True, tuple(names), required, optional)


@dataclass(frozen=True)
class Perform:
    span: Span
    signature: Signature


@dataclass
class JobClass:
    """A ``class`` node with its recognised job mechanisms and direct explicit performs."""

    span: Span
    compact: bool
    mechanisms: frozenset[str]
    performs: list[Perform] = field(default_factory=list)


def _classes(tree: Any) -> list[Any]:
    """Every ``class`` node reachable through ``module``/``class`` bodies, in source order, with
    the number of enclosing class/module nodes. Iterative; methods, blocks and conditionals are
    never entered."""
    found: list[tuple[Any, int]] = []
    stack = [(child, 0) for child in reversed(tree.root_node.named_children)]
    while stack:
        node, enclosing = stack.pop()
        if node.type not in {"class", "module"}:
            continue
        if node.type == "class":
            found.append((node, enclosing))
        body = node.child_by_field_name("body")
        if body is not None:
            stack.extend((child, enclosing + 1) for child in reversed(body.named_children))
    found.sort(key=lambda item: item[0].start_byte)
    return found


def _body_statements(node: Any) -> list[Any]:
    body = node.child_by_field_name("body")
    if body is None:
        return []
    return [child for child in body.named_children if child.type not in SKIPPED_CHILDREN]


def _includes_sidekiq(source: bytes, statement: Any) -> bool:
    if statement.type != "call" or statement.child_by_field_name("receiver") is not None:
        return False
    method = statement.child_by_field_name("method")
    if method is None or _text(source, method) != b"include":
        return False
    arguments = statement.child_by_field_name("arguments")
    if arguments is None:
        return False
    return any(_text(source, child) in SIDEKIQ_MIXINS for child in arguments.named_children)


def scan_classes(tree: Any, source: bytes) -> list[JobClass]:
    """Pure extraction: every class of one file, by position, with its mechanisms (a direct
    ``include Sidekiq::Worker|Job`` or a direct ``ApplicationJob``/``ActiveJob::Base``
    superclass) and its direct ``def perform`` statements. ``singleton_method`` and
    ``class << self`` contents are never read."""
    result: list[JobClass] = []
    for node, enclosing in _classes(tree):
        name = node.child_by_field_name("name")
        compact = name is not None and name.type == "scope_resolution" and enclosing > 0
        mechanisms: set[str] = set()
        holder = node.child_by_field_name("superclass")
        if (
            holder is not None
            and holder.named_children
            and _text(source, holder.named_children[0]) in ACTIVE_JOB_BASES
        ):
            mechanisms.add(ACTIVE_JOB)
        performs: list[Perform] = []
        for statement in _body_statements(node):
            if _includes_sidekiq(source, statement):
                mechanisms.add(SIDEKIQ)
            elif statement.type == "method":
                label = statement.child_by_field_name("name")
                if label is not None and _text(source, label) == b"perform":
                    performs.append(Perform(_span(statement), signature(source, statement)))
        result.append(JobClass(_span(node), compact, frozenset(mechanisms), performs))
    return result


@dataclass(frozen=True)
class Arguments:
    """The call-side argument boundary. ``count`` is the positional-plus-unsupported child count
    of the argument list; ``positional`` keeps the spans of the first ``MAX_ARGUMENTS`` of them;
    ``construct`` is the first call-side code (precedence order) with its node span."""

    count: int
    positional: tuple[Span, ...]
    construct: str | None
    construct_span: Span | None
    list_span: Span


def call_arguments(call: Any) -> Arguments:
    """D4.2: the ``arguments`` field's named children, comments and heredoc bodies aside."""
    holder = call.child_by_field_name("arguments")
    children = (
        []
        if holder is None
        else [child for child in holder.named_children if child.type not in SKIPPED_CHILDREN]
    )
    first: dict[str, Any] = {}
    positional: list[Span] = []
    for child in children:
        code = ARGUMENT_TYPE_CODES.get(child.type)
        if code is not None:
            first.setdefault(code, child)
        elif len(positional) < MAX_ARGUMENTS:
            positional.append(_span(child))
    block = call.child_by_field_name("block")
    if block is not None:
        first.setdefault("block", block)
    construct = next((code for code in CALL_ARGUMENT_CONSTRUCTS if code in first), None)
    return Arguments(
        len(children),
        tuple(positional),
        construct,
        None if construct is None else _span(first[construct]),
        _span(call if holder is None else holder),
    )


def classify(source: bytes, call: Any, method: str) -> tuple[str | None, str | None]:
    """D4.2 receiver classification, first match wins: ``(construct, receiver)``; the receiver
    text is only returned for a resolvable direct constant receiver."""
    if method in DELAYED_METHODS:
        return "delayed_enqueue", None
    receiver = call.child_by_field_name("receiver")
    if receiver is None:
        return "dynamic_receiver", None
    if receiver.type == "call":
        inner = receiver.child_by_field_name("method")
        origin = receiver.child_by_field_name("receiver")
        if (
            inner is not None
            and _text(source, inner) == b"set"
            and origin is not None
            and _constant_name(source, origin) is not None
        ):
            return "set_chain", None
    name = _constant_name(source, receiver)
    if name is None:
        return "dynamic_receiver", None
    return None, name


def _constant_name(source: bytes, node: Any) -> str | None:
    if node.type not in CONSTANT_TYPES:
        return None
    raw = _text(source, node)
    if len(raw) > MAX_RECEIVER_BYTES:
        return None
    name = _decode(raw)
    if name is None or CLASS_NAME_RE.fullmatch(name) is None:
        return None
    return name


def nesting(source: bytes, call: Any, namespace: str) -> tuple[str, ...] | None:
    """The ``Module.nesting`` full names of a call site, innermost first, or ``None`` when the
    lexical scope cannot be stated exactly: an unnamed or non-constant name, a name that starts
    with ``::``, a ``scope_resolution`` name with an enclosing class/module (Ruby may define it
    outside the lexical parent), or an innermost name that differs from the extractor's
    ``binding.namespace`` or is not a plain constant path. A top-level compact ``class A::B``
    stays bound."""
    chain: list[Any] = []
    node = call.parent
    while node is not None:
        if node.type in {"class", "module"}:
            chain.append(node)
        node = node.parent
    chain.reverse()
    names: list[str] = []
    prefix = ""
    for depth, scope in enumerate(chain):
        holder = scope.child_by_field_name("name")
        if holder is None or holder.type not in CONSTANT_TYPES:
            return None
        text = _decode(_text(source, holder))
        if text is None or text.startswith("::"):
            return None
        if holder.type == "scope_resolution" and depth > 0:
            return None
        prefix = prefix + "::" + text if prefix else text
        names.append(prefix)
    if names:
        if names[-1] != namespace or JOB_KEY_RE.fullmatch(names[-1]) is None:
            return None
    elif namespace != "":
        return None
    return tuple(reversed(names))


def candidates(lexical: tuple[str, ...], receiver: str) -> list[str]:
    """The lexical candidate names, innermost first (names only, no Ruby precedence). A rooted
    receiver names exactly one constant; an empty nesting yields the receiver itself."""
    if receiver.startswith("::"):
        return [receiver[2:]]
    names = [scope + "::" + receiver for scope in lexical]
    names.append(receiver)
    return list(dict.fromkeys(names))


@dataclass(frozen=True)
class Site:
    """One located enqueue site. ``construct`` is set for every diagnostic-only site."""

    path: str
    span: Span
    method: str
    entity_id: str
    construct: str | None
    receiver: str | None
    lexical: tuple[str, ...]
    arguments: Arguments | None


def build_site(
    source: bytes, call: Any, method: str, path: str, entity_id: str, namespace: str
) -> Site:
    span = _span(call)
    construct, receiver = classify(source, call, method)
    if construct is not None or receiver is None:
        return Site(path, span, method, entity_id, construct, None, (), None)
    lexical = nesting(source, call, namespace)
    if lexical is None:
        if not receiver.startswith("::"):
            return Site(path, span, method, entity_id, "unbound_namespace", receiver, (), None)
        lexical = ()
    return Site(path, span, method, entity_id, None, receiver, lexical, call_arguments(call))


# ---- capture ----


@dataclass
class Capture:
    """Bounded Ruby bytes that mention a job construct, retained by the build (flag-gated by the
    caller). Files the bounds drop are remembered (as a bounded set) so that names defined in
    them are never resolved to a single job."""

    files: dict[str, tuple[bytes, str]] = field(default_factory=dict)
    limited: int = 0
    limited_paths: set[str] = field(default_factory=set)
    overflow: bool = False
    total_bytes: int = 0

    def offer(self, path: str, source: bytes, source_hash: str) -> None:
        if CAPTURE_RE.search(source) is None:
            return
        if (
            len(self.files) >= MAX_FILES
            or len(source) > MAX_FILE_BYTES
            or self.total_bytes + len(source) > MAX_CAPTURE_BYTES
        ):
            self.limited += 1
            if len(self.limited_paths) < MAX_LIMITED_PATHS:
                self.limited_paths.add(path)
            else:
                self.overflow = True
            return
        self.files[path] = (source, source_hash)
        self.total_bytes += len(source)


# ---- resolution ----


@dataclass(frozen=True)
class Decision:
    resolution: Resolution
    signature: Signature | None


def _unresolved(reason: str) -> Decision:
    return Decision(("UNRESOLVED", None, [], reason), None)


def _binding(value: Any) -> dict[str, Any]:
    try:
        result = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return result if isinstance(result, dict) else {}


class Resolver:
    """Class and perform lookup over the build's Ruby ``symbols`` table (one scan per build).

    ``namespaces`` holds every class/module name of the repository. ``unsafe`` holds the names
    that have a class/module row the adapter cannot account for (a definition in a limited or
    ineligible file, an unbound or unmatched class): such a name never resolves to a job.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        scanned: set[str],
        unsafe_paths: set[str],
        unsafe_all: bool,
    ) -> None:
        self.unsafe_all = unsafe_all
        self.namespaces: set[str] = set()
        self.unsafe: set[str] = set()
        self.classes: dict[tuple[str, int, int], list[tuple[str, str | None]]] = {}
        self.methods: dict[tuple[str, int, int], list[tuple[str, str | None, str | None]]] = {}
        self.jobs: dict[str, set[str]] = {}
        self.performs: dict[str, dict[str, Signature]] = {}
        for row in conn.execute(
            "SELECT s.path, s.kind, s.entity_id, s.binding, "
            "json_extract(o.payload,'$.start_byte'), json_extract(o.payload,'$.end_byte') "
            "FROM symbols s JOIN occurrences o ON o.id=s.occurrence_id "
            "WHERE s.lang='ruby' AND (s.kind IN ('class','module') "
            "OR (s.kind='method' AND s.name='perform')) "
            "ORDER BY s.path, s.line_start, s.id"
        ):
            binding = _binding(row[3])
            key = (row[0], row[4], row[5])
            if row[1] == "method":
                if row[0] in scanned:
                    self.methods.setdefault(key, []).append(
                        (row[2], binding.get("owner"), binding.get("category"))
                    )
                continue
            namespace = binding.get("namespace")
            if isinstance(namespace, str):
                self.namespaces.add(namespace)
                if row[0] in unsafe_paths:
                    self.unsafe.add(namespace)
            if row[1] == "class" and row[0] in scanned:
                self.classes.setdefault(key, []).append(
                    (row[2], namespace if isinstance(namespace, str) else None)
                )
        self.matched: set[tuple[str, int, int]] = set()

    def bind_class(self, path: str, item: JobClass) -> str | None:
        """The namespace of the one class symbol with exactly this span, when the class is bound
        (an unbound or unmatched class makes its name unsafe and is never a job)."""
        key = (path, item.span.start_byte, item.span.end_byte)
        rows = self.classes.get(key, [])
        if len(rows) == 1 and rows[0][1] is not None:
            self.matched.add(key)
            namespace = rows[0][1]
            if not item.compact and JOB_KEY_RE.fullmatch(namespace) is not None:
                return namespace
            self.unsafe.add(namespace)
            return None
        for _identifier, other in rows:
            if other is not None:
                self.unsafe.add(other)
        self.matched.add(key)
        return None

    def mark_unmatched(self) -> None:
        """A class symbol in a scanned file that no scanned class node accounts for."""
        for key, rows in self.classes.items():
            if key not in self.matched:
                for _identifier, namespace in rows:
                    if namespace is not None:
                        self.unsafe.add(namespace)

    def bind_perform(self, path: str, namespace: str, item: Perform) -> str | None:
        """The entity id of the one instance method symbol with exactly this span and owner."""
        rows = self.methods.get((path, item.span.start_byte, item.span.end_byte), [])
        if len(rows) != 1:
            return None
        identifier, owner, category = rows[0]
        if owner != "ruby:" + namespace or category != "instance":
            return None
        return identifier

    def add(self, namespace: str, item: JobClass, performs: dict[str, Signature]) -> None:
        if item.mechanisms:
            self.jobs.setdefault(namespace, set()).update(item.mechanisms)
        if performs:
            self.performs.setdefault(namespace, {}).update(performs)

    def resolve(self, lexical: tuple[str, ...], receiver: str, mechanism: str) -> Decision:
        existing = [c for c in candidates(lexical, receiver) if c in self.namespaces]
        if not existing:
            return _unresolved(CLASS_NOT_FOUND_REASON)
        if self.unsafe_all or any(name in self.unsafe for name in existing):
            return _unresolved(NOT_RECOGNIZED_REASON)
        found: dict[str, Signature] = {}
        for name in existing:
            if mechanism in self.jobs.get(name, ()):
                found.update(self.performs.get(name, {}))
        ids = sorted(found)
        # One existing name with one perform is the only unique answer. Atlas evaluates neither
        # Ruby constant precedence nor ancestor lookup: several existing names never resolve.
        if len(existing) == 1 and len(ids) == 1:
            return Decision(("INFERRED", ids[0], [], None), found[ids[0]])
        if len(ids) >= 2:
            return Decision(("AMBIGUOUS", None, ids, AMBIGUOUS_REASON), None)
        if len(existing) == 1 and mechanism in self.jobs.get(existing[0], ()):
            return _unresolved(PERFORM_NOT_FOUND_REASON)
        return _unresolved(NOT_RECOGNIZED_REASON)


def argument_decision(
    arguments: Arguments, decision: Decision
) -> tuple[str, str | None, Span | None]:
    """D4.4: ``(state, construct, diagnostic span)``; the first matching step wins."""
    if arguments.construct is not None:
        return "unsupported", arguments.construct, arguments.construct_span
    if arguments.count > MAX_ARGUMENTS:
        return "unsupported", "argument_limit", arguments.list_span
    if decision.signature is None:
        return "unbound", None, None
    known = decision.signature
    if not known.supported:
        return "unsupported", "unsupported_signature", arguments.list_span
    if not known.required <= arguments.count <= known.required + known.optional:
        return "unsupported", "arity_mismatch", arguments.list_span
    if arguments.count == 0:
        return "none", None, None
    return "mapped", None, None


# ---- identity, purge, manifest ----


def adapter_sha256() -> str:
    """Identity of this adapter's own bytes (the only file the adapter reads)."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _producer_clause() -> str:
    return (
        "SELECT id FROM producers WHERE json_extract(payload,'$.fingerprint.rails_jobs') "
        "IS NOT NULL"
    )


def has_records(conn: sqlite3.Connection) -> bool:
    """Cheap probe: rails_jobs records cannot exist without their producer or manifest key."""
    if conn.execute("SELECT 1 FROM manifest WHERE key=?", (MANIFEST_KEY,)).fetchone():
        return True
    return conn.execute(_producer_clause() + " LIMIT 1").fetchone() is not None


def purge(conn: sqlite3.Connection) -> None:
    """Remove every rails_jobs-owned record and the manifest key (whatever the flag)."""
    for relation in sorted(JOB_RELATIONS):
        conn.execute(
            "DELETE FROM assertions WHERE json_extract(payload,'$.relation')=?", (relation,)
        )
    conn.execute(
        "DELETE FROM evidence WHERE json_extract(payload,'$.producer_id') IN ("
        + _producer_clause()
        + ")"
    )
    conn.execute(
        "DELETE FROM occurrences WHERE json_extract(payload,'$.kind')=?", (ev.RAILS_JOB_KIND,)
    )
    conn.execute(
        "DELETE FROM producers WHERE json_extract(payload,'$.fingerprint.rails_jobs') IS NOT NULL"
    )
    conn.execute("DELETE FROM manifest WHERE key=?", (MANIFEST_KEY,))


# ---- refresh ----


@dataclass(frozen=True)
class Fact:
    """One record triple to store: its occurrence (final), source entity and assertion body."""

    path: str
    occurrence: dict[str, Any]
    entity_id: str
    relation: str
    resolution: Resolution
    raw: str


def _fact(
    site: Site,
    source: bytes,
    source_hash: str,
    span: Span,
    role: str,
    relation: str,
    resolution: Resolution,
    raw: str,
) -> Fact:
    occurrence = ev.occurrence(
        span, site.path, "ruby", source, site.entity_id, ev.RAILS_JOB_KIND, role, source_hash
    )
    return Fact(site.path, occurrence, site.entity_id, relation, resolution, raw)


def _diagnostic_resolution() -> Resolution:
    return "UNRESOLVED", None, [], UNSUPPORTED_REASON


def site_facts(site: Site, source: bytes, source_hash: str, resolver: Resolver) -> list[Fact]:
    """Every fact of one site: its enqueue, then its mapped arguments or its one diagnostic."""
    if site.construct is not None or site.arguments is None or site.receiver is None:
        raw = ev.canonical(
            {"construct": site.construct, "kind": site.method, "mapping": UNEXPANDED_LABEL}
        )
        role = ENQUEUE_UNSUPPORTED_ROLE + str(site.construct)
        return [
            _fact(
                site,
                source,
                source_hash,
                site.span,
                role,
                ev.ASYNC_ENQUEUE_RELATION,
                _diagnostic_resolution(),
                raw,
            )
        ]
    mechanism = ENQUEUE_METHODS[site.method]
    decision = resolver.resolve(site.lexical, site.receiver, mechanism)
    state, construct, diagnostic_span = argument_decision(site.arguments, decision)
    body: dict[str, Any] = {
        "arguments": state,
        "arity": site.arguments.count,
        "kind": site.method,
        "mapping": RECEIVER_LABEL,
        "mechanism": mechanism,
        "receiver": site.receiver,
    }
    if state == "unsupported":
        body["construct"] = construct
    enqueue = _fact(
        site,
        source,
        source_hash,
        site.span,
        ENQUEUE_ROLE + site.method,
        ev.ASYNC_ENQUEUE_RELATION,
        decision.resolution,
        ev.canonical(body),
    )
    facts = [enqueue]
    enqueue_id = enqueue.occurrence["id"]
    if state == "unsupported" and diagnostic_span is not None:
        raw = ev.canonical(
            {"construct": construct, "enqueue": enqueue_id, "mapping": UNEXPANDED_LABEL}
        )
        facts.append(
            _fact(
                site,
                source,
                source_hash,
                diagnostic_span,
                ARGUMENT_UNSUPPORTED_ROLE + str(construct),
                ev.ASYNC_ARGUMENT_RELATION,
                _diagnostic_resolution(),
                raw,
            )
        )
    elif state == "mapped" and decision.signature is not None:
        for index, argument in enumerate(site.arguments.positional):
            raw = ev.canonical(
                {
                    "arg": index,
                    "enqueue": enqueue_id,
                    "mapping": POSITIONAL_LABEL,
                    "param": index,
                    "param_name": decision.signature.names[index],
                }
            )
            facts.append(
                _fact(
                    site,
                    source,
                    source_hash,
                    argument,
                    MAPPED_ROLE,
                    ev.ASYNC_ARGUMENT_RELATION,
                    decision.resolution,
                    raw,
                )
            )
    return facts


def _summary(
    stats: dict[str, int],
    facts: list[Fact],
    truncated: bool,
    producer_id: str | None,
) -> dict[str, Any]:
    tally: dict[str, dict[str, int]] = {
        relation: {name: 0 for name in names} for relation, names in RESOLUTIONS.items()
    }
    for item in facts:
        tally[item.relation][item.resolution[0]] += 1
    return {
        "format": MANIFEST_FORMAT,
        "adapter": ADAPTER,
        "adapter_sha256": adapter_sha256(),
        "files": {
            "captured": stats["files_captured"],
            "scanned": stats["files_scanned"],
            "failed": stats["files_failed"],
            "limited": stats["files_limited"],
        },
        "classes": {"jobs": stats["classes_jobs"], "unbound": stats["classes_unbound"]},
        "performs": stats["performs"],
        "sites": {
            "located": stats["sites_located"],
            "unlocated": stats["sites_unlocated"],
            "skipped": stats["sites_skipped"],
            "limited": stats["sites_limited"],
        },
        "facts": tally,
        "truncated": truncated,
        "producer_id": producer_id,
    }


def refresh(
    conn: sqlite3.Connection,
    *,
    enabled: bool,
    capture: Capture,
    observations: Iterable[dict[str, Any]],
    get_parser: Callable[[str], Any],
    graph_producer_id: str,
) -> dict[str, Any] | None:
    """Purge every rails_jobs-owned record, then (enabled only) rebuild them from scratch.

    Runs inside the build's staging transaction after edge resolution. ``get_parser`` is only
    called when the adapter is enabled and at least one captured file is an indexed observation
    with a matching hash.
    """
    if has_records(conn):
        purge(conn)
    if not enabled:
        return None
    # `observations` also holds `history` records; only the observation is current state.
    current = {item["path"]: item for item in observations if item["type"] == "observation"}
    stats = {
        "files_captured": len(capture.files),
        "files_scanned": 0,
        "files_failed": 0,
        "files_limited": capture.limited,
        "classes_jobs": 0,
        "classes_unbound": 0,
        "performs": 0,
        "sites_located": 0,
        "sites_unlocated": 0,
        "sites_skipped": 0,
        "sites_limited": 0,
    }
    eligible: list[str] = []
    ineligible: set[str] = set()
    for path in sorted(capture.files):
        observed = current.get(path)
        file_id = ev.file_entity(path, "ruby")["id"]
        if (
            observed is None
            or observed["state"] != "indexed"
            or observed["observed_source_sha256"] != capture.files[path][1]
            or conn.execute("SELECT 1 FROM entities WHERE id=?", (file_id,)).fetchone() is None
        ):
            stats["files_failed"] += 1
            ineligible.add(path)
        else:
            eligible.append(path)
    wanted = set(eligible)
    rows: dict[str, dict[tuple[int, int], tuple[str | None, str]]] = {}
    for row in conn.execute(
        "SELECT r.path, r.binding, r.source_entity_id, "
        "json_extract(o.payload,'$.start_byte') AS sb, json_extract(o.payload,'$.end_byte') AS eb "
        "FROM refs r JOIN occurrences o ON o.id=r.occurrence_id "
        "WHERE r.lang='ruby' AND r.relation='call' AND r.callee_name IN (?,?,?,?) "
        "ORDER BY r.path, sb, eb, r.id",
        tuple(sorted(SITE_METHODS)),
    ):
        if row[0] not in wanted:
            stats["sites_skipped"] += 1
            continue
        namespace = _binding(row[1]).get("namespace")
        rows.setdefault(row[0], {}).setdefault(
            (row[3], row[4]), (row[2], namespace if isinstance(namespace, str) else "")
        )
    parser: Any = None
    scanned: set[str] = set()
    scans: list[tuple[str, list[JobClass]]] = []
    sites: list[Site] = []
    for path in eligible:
        source = capture.files[path][0]
        if parser is None:
            parser = get_parser("ruby")
        tree = parser.parse(source)
        if tree.root_node.has_error:
            stats["files_failed"] += 1
            stats["sites_skipped"] += len(rows.get(path, {}))
            ineligible.add(path)
            del tree
            continue
        # Scans return plain spans; the tree is released before the next file is parsed.
        stats["files_scanned"] += 1
        scanned.add(path)
        scans.append((path, scan_classes(tree, source)))
        for (start, end), (entity_id, namespace) in sorted(rows.get(path, {}).items()):
            call = _locate(tree.root_node, start, end)
            method = None if call is None else _text(source, call.child_by_field_name("method"))
            name = None if method is None else _decode(method)
            if call is None or entity_id is None or name not in SITE_METHODS:
                stats["sites_unlocated"] += 1
                continue
            stats["sites_located"] += 1
            if len(sites) > MAX_SITES:
                stats["sites_limited"] += 1
                continue
            sites.append(build_site(source, call, str(name), path, entity_id, namespace))
        del tree
    facts: list[Fact] = []
    resolver: Resolver | None = None
    if scanned:
        resolver = Resolver(
            conn,
            scanned,
            ineligible | capture.limited_paths,
            capture.overflow,
        )
        for path, classes in scans:
            for item in classes:
                namespace = resolver.bind_class(path, item)
                if namespace is None:
                    stats["classes_unbound"] += 1
                    continue
                bound: dict[str, Signature] = {}
                for perform in item.performs:
                    identifier = resolver.bind_perform(path, namespace, perform)
                    if identifier is not None:
                        bound[identifier] = perform.signature
                        stats["performs"] += 1
                resolver.add(namespace, item, bound)
        resolver.mark_unmatched()
        stats["classes_jobs"] = len(resolver.jobs)
        for index, site in enumerate(sites):
            if index >= MAX_SITES:
                site = Site(
                    site.path, site.span, site.method, site.entity_id, "site_limit", None, (), None
                )
            source, source_hash = capture.files[site.path]
            facts.extend(site_facts(site, source, source_hash, resolver))
    facts.sort(
        key=lambda f: (
            f.path,
            f.occurrence["start_byte"],
            f.occurrence["end_byte"],
            f.occurrence["role"],
        )
    )
    truncated = (
        len(sites) > MAX_SITES
        or stats["sites_limited"] > 0
        or capture.limited > 0
        or capture.overflow
    )
    producer_id: str | None = None
    if facts:
        producer = ev.producer(
            {
                "rails_jobs": {
                    "adapter": ADAPTER,
                    "adapter_sha256": adapter_sha256(),
                    "graph_producer_id": graph_producer_id,
                }
            }
        )
        producer_id = producer["id"]
        ev.put(conn, producer)
    for fact in facts:
        proof = ev.provenance(fact.occurrence, str(producer_id))
        resolution = fact.resolution
        assertion = ev.assertion(
            fact.entity_id,
            resolution[1],
            fact.relation,
            proof["id"],
            resolution[0],
            fact.raw,
            resolution[2],
            resolution[3],
        )
        ev.put(conn, fact.occurrence, fact.path)
        ev.put(conn, proof, fact.path)
        ev.put(conn, assertion, fact.path)
    summary = _summary(stats, facts, truncated, producer_id)
    conn.execute(
        "INSERT OR REPLACE INTO manifest(key,value) VALUES (?,?)",
        (MANIFEST_KEY, ev.canonical(summary)),
    )
    return summary


def _locate(root: Any, start: int, end: int) -> Any:
    """The ``call`` node whose ``method`` identifier spans exactly ``(start, end)``, else None."""
    node = root.descendant_for_byte_range(start, end)
    if node is None or node.type != "identifier":
        return None
    parent = node.parent
    if parent is None or parent.type != "call":
        return None
    method = parent.child_by_field_name("method")
    if method is None or (method.start_byte, method.end_byte) != (start, end):
        return None
    return parent


def record_ids(records: list[dict[str, Any]]) -> set[str]:
    """IDs of every canonical record contributed by a rails_jobs producer."""
    producers = {
        r["id"]
        for r in records
        if r["type"] == "producer"
        and isinstance(r["fingerprint"], dict)
        and "rails_jobs" in r["fingerprint"]
    }
    proofs = {r["id"] for r in records if r["type"] == "evidence" and r["producer_id"] in producers}
    occurrences = {
        r["occurrence_id"] for r in records if r["type"] == "evidence" and r["id"] in proofs
    }
    assertions = {
        r["id"] for r in records if r["type"] == "assertion" and r["evidence_id"] in proofs
    }
    return producers | proofs | occurrences | assertions


# ---- validation ----


def _decode_raw(raw_target: Any) -> dict[str, Any]:
    ev.require(isinstance(raw_target, str), "rails_jobs raw_target type")
    try:
        value = json.loads(raw_target)
    except ValueError as exc:
        raise ValueError("Invalid evidence: rails_jobs raw_target is not JSON") from exc
    ev.require(isinstance(value, dict), "rails_jobs raw_target is not an object")
    ev.require(ev.canonical(value) == raw_target, "rails_jobs raw_target is not canonical")
    return value


def _is_ascii_match(value: Any, pattern: re.Pattern[str], limit: int) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= limit
        and value.isascii()
        and pattern.fullmatch(value) is not None
    )


def _is_count(value: Any, limit: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= limit


def _perform_entity(conn: sqlite3.Connection, identifier: Any) -> None:
    ev.require(ev.hash_value(identifier), "rails_jobs target identity")
    entity = ev.get(conn, "entity", identifier)
    ev.require(
        entity["kind"] == "method" and entity["name"] == "perform" and entity["language"] == "ruby",
        "rails_jobs target is not a ruby perform method entity",
    )


def _check_enqueue(conn: sqlite3.Connection, item: dict[str, Any], rest: str, value: Any) -> None:
    resolution = item["resolution"]
    reason = item["unresolved_reason"]
    ev.require(resolution in RESOLUTIONS[ev.ASYNC_ENQUEUE_RELATION], "async_enqueue resolution")
    if rest.startswith("unsupported:"):
        code = rest[len("unsupported:") :]
        ev.require(
            set(value) == ENQUEUE_DIAGNOSTIC_KEYS
            and value["mapping"] == UNEXPANDED_LABEL
            and value["construct"] == code
            and code in ENQUEUE_CONSTRUCTS
            and isinstance(value["kind"], str)
            and value["kind"] in SITE_METHODS,
            "rails_jobs enqueue diagnostic",
        )
        ev.require(
            resolution == "UNRESOLVED"
            and reason == UNSUPPORTED_REASON
            and item["target_entity_id"] is None
            and not item["candidate_ids"],
            "rails_jobs enqueue diagnostic resolution",
        )
        return
    ev.require(rest in ENQUEUE_METHODS, "rails_jobs enqueue kind")
    arguments = value.get("arguments")
    expected = ENQUEUE_KEYS | ({"construct"} if arguments == "unsupported" else set())
    ev.require(
        set(value) == expected
        and value["mapping"] == RECEIVER_LABEL
        and value["kind"] == rest
        and value["mechanism"] == ENQUEUE_METHODS[rest]
        and _is_ascii_match(value["receiver"], CLASS_NAME_RE, MAX_RECEIVER_BYTES)
        and _is_count(value["arity"], 1 << 31)
        and isinstance(arguments, str)
        and arguments in ARGUMENT_STATES,
        "rails_jobs enqueue shape",
    )
    construct = value.get("construct")
    if arguments == "unsupported":
        ev.require(
            isinstance(construct, str) and construct in ARGUMENT_CONSTRUCTS,
            "rails_jobs enqueue construct",
        )
    if resolution == "INFERRED":
        ev.require(
            reason is None
            and not item["candidate_ids"]
            and arguments in {"mapped", "none", "unsupported"},
            "rails_jobs inferred enqueue",
        )
        _perform_entity(conn, item["target_entity_id"])
    else:
        ev.require(
            item["target_entity_id"] is None
            and arguments in {"unbound", "unsupported"}
            and (arguments == "unbound" or construct in UNBOUND_ARGUMENT_CONSTRUCTS),
            "rails_jobs unresolved enqueue arguments",
        )
        if resolution == "AMBIGUOUS":
            ev.require(
                reason == AMBIGUOUS_REASON and len(item["candidate_ids"]) >= 2,
                "rails_jobs ambiguous enqueue",
            )
            for identifier in item["candidate_ids"]:
                _perform_entity(conn, identifier)
        else:
            ev.require(
                isinstance(reason, str)
                and reason in UNRESOLVED_ENQUEUE_REASONS
                and not item["candidate_ids"],
                "rails_jobs unresolved enqueue",
            )
    if arguments == "mapped":
        ev.require(resolution == "INFERRED" and value["arity"] >= 1, "rails_jobs mapped enqueue")
    if arguments == "none":
        ev.require(resolution == "INFERRED" and value["arity"] == 0, "rails_jobs empty enqueue")


def _check_argument(conn: sqlite3.Connection, item: dict[str, Any], rest: str, value: Any) -> None:
    resolution = item["resolution"]
    ev.require(resolution in RESOLUTIONS[ev.ASYNC_ARGUMENT_RELATION], "async_argument resolution")
    ev.require(
        isinstance(value.get("enqueue"), str) and ev.hash_value(value["enqueue"]),
        "rails_jobs argument enqueue reference",
    )
    if rest.startswith("unsupported:"):
        code = rest[len("unsupported:") :]
        ev.require(
            set(value) == ARGUMENT_DIAGNOSTIC_KEYS
            and value["mapping"] == UNEXPANDED_LABEL
            and value["construct"] == code
            and code in ARGUMENT_CONSTRUCTS,
            "rails_jobs argument diagnostic",
        )
        ev.require(
            resolution == "UNRESOLVED"
            and item["unresolved_reason"] == UNSUPPORTED_REASON
            and item["target_entity_id"] is None
            and not item["candidate_ids"],
            "rails_jobs argument diagnostic resolution",
        )
        return
    ev.require(
        rest == "mapped"
        and set(value) == MAPPED_KEYS
        and value["mapping"] == POSITIONAL_LABEL
        and _is_count(value["arg"], MAX_ARGUMENTS - 1)
        and value["param"] == value["arg"]
        and _is_ascii_match(value["param_name"], PARAM_RE, MAX_PARAM_NAME_BYTES),
        "rails_jobs mapped argument",
    )
    ev.require(
        resolution == "INFERRED"
        and item["unresolved_reason"] is None
        and not item["candidate_ids"],
        "rails_jobs mapped argument resolution",
    )
    _perform_entity(conn, item["target_entity_id"])


AREAS = {"enqueue:": ev.ASYNC_ENQUEUE_RELATION, "argument:": ev.ASYNC_ARGUMENT_RELATION}


def _check_assertion(
    conn: sqlite3.Connection, item: dict[str, Any], occurrence: dict[str, Any]
) -> dict[str, Any]:
    role = occurrence["role"]
    ev.require(isinstance(role, str) and role.startswith(ROLE_PREFIX), "rails_jobs occurrence role")
    area = role[len(ROLE_PREFIX) :]
    relation = item["relation"]
    matched = [prefix for prefix, name in AREAS.items() if area.startswith(prefix)]
    ev.require(len(matched) == 1 and AREAS[matched[0]] == relation, "rails_jobs role/relation area")
    rest = area[len(matched[0]) :]
    ev.require(item["source_entity_id"] == occurrence["entity_id"], "rails_jobs source")
    source = ev.get(conn, "entity", item["source_entity_id"])
    ev.require(
        source["kind"] in SOURCE_KINDS
        and source["language"] == "ruby"
        and source["path"] == occurrence["path"],
        "rails_jobs source entity",
    )
    value = _decode_raw(item["raw_target"])
    if relation == ev.ASYNC_ENQUEUE_RELATION:
        _check_enqueue(conn, item, rest, value)
    else:
        _check_argument(conn, item, rest, value)
    return value


def validate_records(conn: sqlite3.Connection) -> None:
    """Referential and shape completeness of the rails_jobs records (build, predecessor, import,
    export). Never compares the adapter hash to the running value; every failure is
    ``ValueError("Invalid evidence: rails_jobs ...")``."""
    try:
        _validate_records(conn)
    except (KeyError, TypeError, IndexError, AttributeError) as exc:
        raise ValueError("Invalid evidence: rails_jobs malformed record") from exc


def _validate_records(conn: sqlite3.Connection) -> None:
    producers = {
        row[0]: json.loads(row[1])
        for row in conn.execute(
            "SELECT id, payload FROM producers "
            "WHERE json_extract(payload,'$.fingerprint.rails_jobs') IS NOT NULL"
        )
    }
    ev.require(len(producers) <= 1, "rails_jobs: more than one producer")
    for producer in producers.values():
        ev.require(
            conn.execute(
                "SELECT 1 FROM producers WHERE id=?",
                (producer["fingerprint"]["rails_jobs"]["graph_producer_id"],),
            ).fetchone()
            is not None,
            "rails_jobs: graph producer is missing",
        )
    occurrences = {
        row[0]: json.loads(row[1])
        for row in conn.execute(
            "SELECT id, payload FROM occurrences WHERE json_extract(payload,'$.kind')=?",
            (ev.RAILS_JOB_KIND,),
        )
    }
    proofs: dict[str, dict[str, Any]] = {}
    if producers:
        for row in conn.execute(
            "SELECT id, payload FROM evidence "
            "WHERE json_extract(payload,'$.producer_id') IN (" + _producer_clause() + ")"
        ):
            proofs[row[0]] = json.loads(row[1])
    assertions: dict[str, dict[str, Any]] = {}
    for relation in sorted(JOB_RELATIONS):
        for row in conn.execute(
            "SELECT id, payload FROM assertions WHERE json_extract(payload,'$.relation')=?",
            (relation,),
        ):
            assertions[row[0]] = json.loads(row[1])
    ev.require(len(assertions) <= MAX_ASSERTIONS, "rails_jobs: too many records")
    ev.require(
        all(proof["occurrence_id"] in occurrences for proof in proofs.values()),
        "rails_jobs: evidence cites a non-job occurrence",
    )
    ev.require(
        sorted(proof["occurrence_id"] for proof in proofs.values()) == sorted(occurrences),
        "rails_jobs: job occurrence without exactly one evidence",
    )
    ev.require(
        sorted(item["evidence_id"] for item in assertions.values()) == sorted(proofs),
        "rails_jobs: evidence without exactly one assertion",
    )
    by_occurrence: dict[str, dict[str, Any]] = {}
    values: dict[str, dict[str, Any]] = {}
    for key, item in assertions.items():
        occurrence = occurrences[proofs[item["evidence_id"]]["occurrence_id"]]
        values[key] = _check_assertion(conn, item, occurrence)
        by_occurrence[occurrence["id"]] = item
    _check_cross_records(assertions, values, occurrences, proofs, by_occurrence)
    for identifier in producers:
        ev.require(
            any(proof["producer_id"] == identifier for proof in proofs.values()),
            "rails_jobs: producer is cited by no evidence",
        )


def _check_cross_records(
    assertions: dict[str, dict[str, Any]],
    values: dict[str, dict[str, Any]],
    occurrences: dict[str, dict[str, Any]],
    proofs: dict[str, dict[str, Any]],
    by_occurrence: dict[str, dict[str, Any]],
) -> None:
    """Every argument record agrees with the one enqueue it names: same path and source, span
    inside the enqueue span, the same target, ordinals exactly ``0..arity-1`` once each for a
    mapped enqueue, exactly one diagnostic (same construct) for an unsupported one, none else."""
    mapped: dict[str, list[int]] = {}
    diagnostics: dict[str, list[str]] = {}
    for key, item in assertions.items():
        if item["relation"] != ev.ASYNC_ARGUMENT_RELATION:
            continue
        occurrence = occurrences[proofs[item["evidence_id"]]["occurrence_id"]]
        value = values[key]
        owner = occurrences.get(value["enqueue"])
        if (
            owner is None
            or not owner["role"].startswith(ENQUEUE_ROLE)
            or owner["role"].startswith(ENQUEUE_UNSUPPORTED_ROLE)
        ):
            raise ValueError("Invalid evidence: rails_jobs argument references no regular enqueue")
        parent = by_occurrence[owner["id"]]
        parent_value = values[parent["id"]]
        ev.require(
            owner["path"] == occurrence["path"]
            and owner["start_byte"] <= occurrence["start_byte"]
            and occurrence["end_byte"] <= owner["end_byte"]
            and parent["source_entity_id"] == item["source_entity_id"],
            "rails_jobs argument outside its enqueue",
        )
        if occurrence["role"] == MAPPED_ROLE:
            ev.require(
                parent["resolution"] == "INFERRED"
                and parent_value["arguments"] == "mapped"
                and parent["target_entity_id"] == item["target_entity_id"],
                "rails_jobs mapped argument disagrees with its enqueue",
            )
            mapped.setdefault(owner["id"], []).append(value["arg"])
        else:
            ev.require(
                parent_value["arguments"] == "unsupported"
                and parent_value["construct"] == value["construct"],
                "rails_jobs argument diagnostic disagrees with its enqueue",
            )
            diagnostics.setdefault(owner["id"], []).append(value["construct"])
    for key, item in assertions.items():
        occurrence = occurrences[proofs[item["evidence_id"]]["occurrence_id"]]
        role = occurrence["role"]
        if (
            item["relation"] != ev.ASYNC_ENQUEUE_RELATION
            or not role.startswith(ENQUEUE_ROLE)
            or role.startswith(ENQUEUE_UNSUPPORTED_ROLE)
        ):
            continue
        state = values[key]["arguments"]
        ordinals = sorted(mapped.get(occurrence["id"], []))
        ev.require(
            ordinals == (list(range(values[key]["arity"])) if state == "mapped" else []),
            "rails_jobs mapped ordinals differ from the arity",
        )
        ev.require(
            len(diagnostics.get(occurrence["id"], [])) == (1 if state == "unsupported" else 0),
            "rails_jobs unsupported enqueue without exactly one diagnostic",
        )
