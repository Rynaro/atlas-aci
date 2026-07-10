"""MCP server wiring.

Translates between the MCP protocol (tools/list, tools/call) and the
ATLAS tool implementations. Every call passes through enforcement, then
through the D2 central bounds chokepoint below before it is ever
serialized back to the agent.
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from atlas_aci.codegraph import PRODUCED_KINDS, CodeGraph, parse_query_verb
from atlas_aci.config import Config
from atlas_aci.enforcement import Enforcement, ToolError
from atlas_aci.memex import Memex
from atlas_aci.tools.graph_query import graph_query
from atlas_aci.tools.list_dir import list_dir
from atlas_aci.tools.search_symbol import search_symbol
from atlas_aci.tools.search_text import search_text
from atlas_aci.tools.test_dry_run import test_dry_run
from atlas_aci.tools.view_file import view_file

log = structlog.get_logger()


def build_tool_manifest(_config: Config) -> list[dict[str, Any]]:
    """The tool descriptors served via tools/list. Bounds embedded in descriptions."""
    return [
        {
            "name": "view_file",
            "description": (
                "Read a window of lines from a file in the repository. "
                "MAX 100 lines per call. Use next_cursor to page. "
                "Binary files are rejected; UTF-8 text only."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["path", "start_line", "end_line"],
                "properties": {
                    "path": {"type": "string", "description": "Repo-relative file path"},
                    "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": "integer", "minimum": 1},
                },
            },
        },
        {
            "name": "list_dir",
            "description": (
                "List a directory. MAX 200 entries per call. Skip-list applied "
                "(node_modules, vendor, tmp, .git, etc.)."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["path"],
                "properties": {
                    "path": {"type": "string"},
                    "glob": {"type": "string", "description": "Optional fnmatch pattern"},
                },
            },
        },
        {
            "name": "search_text",
            "description": (
                "Ripgrep-backed regex search. MAX 50 matches. If overflow=true, "
                "narrow the scope or use search_symbol instead. Smart-case by default."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["pattern", "scope"],
                "properties": {
                    "pattern": {"type": "string"},
                    "scope": {
                        "type": "string",
                        "description": "Repo-relative path or glob (e.g. app/**/*.rb)",
                    },
                    "regex": {"type": "boolean", "default": True},
                    "limit": {"type": "integer", "maximum": 50, "default": 50},
                },
            },
        },
        {
            "name": "search_symbol",
            "description": (
                "Index-backed symbol lookup. Returns definitions and references, "
                "bounded and flagged like every other tool (README §Mechanical "
                "bounds). Run `atlas-aci index` first to build the graph."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {"type": "string"},
                    "kind": {
                        "type": "string",
                        # Mechanically derived from codegraph.PRODUCED_KINDS —
                        # a superset of every kind the indexer can actually
                        # produce across all shipped languages (AC-DOC-6).
                        "enum": ["any", *PRODUCED_KINDS],
                        "default": "any",
                    },
                },
            },
        },
        {
            "name": "graph_query",
            "description": (
                "Query the code graph. DSL: 'callers_of:Symbol', "
                "'definitions_of:Name', 'subclasses_of:Class', 'god_nodes:' "
                "(degree-centrality ranking over the confident subgraph). "
                "Bounded and flagged like every other tool. Run `atlas-aci "
                "index` first."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["query"],
                "properties": {"query": {"type": "string"}},
            },
        },
        {
            "name": "test_dry_run",
            "description": (
                "Run a single test file (optionally filtered by case name) with a "
                "30s timeout. Output capped at 8KiB. SANDBOXING IS THE OPERATOR'S "
                "RESPONSIBILITY — do not enable for untrusted models without a "
                "DevContainer or microVM."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["path"],
                "properties": {
                    "path": {"type": "string"},
                    "case": {"type": "string"},
                },
            },
        },
        {
            "name": "memex_read",
            "description": (
                "Byte-exact retrieval of a previously captured excerpt via its "
                "memex://excerpt/<sha256> ref. Refs are minted by "
                "`Memex.write()`; no ATLAS tool currently emits one — the "
                "caller supplies a ref it obtained out-of-band."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["ref"],
                "properties": {
                    "ref": {
                        "type": "string",
                        "pattern": "^memex://excerpt/[a-f0-9]+$",
                    },
                },
            },
        },
    ]


# ---- D2 central bounds chokepoint ----
#
# Every tool (and every graph_query verb, since graph_query's response shape
# varies by verb) that returns a list-valued field MUST declare it here as a
# non-empty tuple of field names — the `_bounded_field` convention. This is
# what makes "a tool forgot to cap its response" (search_symbol, graph_query
# before H2) structurally impossible for current *and future* tools:
# `test_every_list_returning_tool_registers_a_bounded_field` (AC-H-15) fails
# the build if a list-bearing tool/verb has no entry here.
#
# Tools with no list-valued field at all (test_dry_run, memex_read) are
# deliberately absent — not forgotten. They still get the universal byte
# ceiling for free via `apply_central_bounds` below.
TOOL_BOUNDED_FIELDS: dict[str, tuple[str, ...]] = {
    "view_file": ("lines",),
    "list_dir": ("entries",),
    "search_text": ("matches",),
    "search_symbol": ("definitions", "references"),
}

GRAPH_QUERY_VERB_BOUNDED_FIELDS: dict[str, tuple[str, ...]] = {
    "callers_of": ("edges",),
    "definitions_of": ("definitions", "references"),
    "subclasses_of": ("edges",),
    "god_nodes": ("god_nodes",),
}


def bounded_fields_for(name: str, arguments: dict[str, Any]) -> tuple[str, ...]:
    """The declared list-valued field(s) for a tool call (the D2 convention).

    `graph_query`'s response shape varies by verb, so its bounded fields are
    looked up from the verb parsed out of the DSL string in
    ``arguments["query"]`` rather than from the response shape (an error
    response has no list at all).
    """
    if name == "graph_query":
        verb = parse_query_verb(arguments.get("query", ""))
        return GRAPH_QUERY_VERB_BOUNDED_FIELDS.get(verb, ())
    return TOOL_BOUNDED_FIELDS.get(name, ())


# ---- R-1 (P0 checker residual, tracked for A1) ----
#
# The registry above protects every top-level list field a tool/verb
# declares — but a *sub-field* nested one level inside a bounded field's own
# elements can still escape it entirely, because `bounded_fields_for` only
# ever looks at the RESPONSE's top level. A1 introduces exactly this shape:
# `callers_of`/`subclasses_of` return an `edges` list (already capped above),
# but an AMBIGUOUS edge's own `candidates[]` (AC-A1-5, "the full ordered
# candidates[] attached, never dropped") is *itself* unbounded — a common
# name can resolve to hundreds of same-named candidates. Declared the same
# way as the top-level registry, one level down: verb -> {containing_field:
# nested_field_name}. Empty for every tool/verb with no such nesting (most of
# them) — unlike the top-level registry, not every bounded field needs an
# entry here, only ones whose own elements carry their own list.
GRAPH_QUERY_VERB_NESTED_BOUNDED_FIELDS: dict[str, dict[str, str]] = {
    "callers_of": {"edges": "candidates"},
    "subclasses_of": {"edges": "candidates"},
}


def nested_bounded_fields_for(name: str, arguments: dict[str, Any]) -> dict[str, str]:
    """The R-1 sibling of `bounded_fields_for`: which nested sub-field (if
    any) inside a top-level bounded field's own list elements must ALSO be
    capped."""
    if name == "graph_query":
        verb = parse_query_verb(arguments.get("query", ""))
        return GRAPH_QUERY_VERB_NESTED_BOUNDED_FIELDS.get(verb, {})
    return {}


def apply_central_bounds(
    name: str,
    arguments: dict[str, Any],
    result: Any,
    enforcement: Enforcement,
) -> Any:
    """The D2 chokepoint: element-cap the declared list field(s) on a whole-
    element boundary, then check the universal serialized-byte ceiling.

    Order matters (AC-H-3): element-capping runs first, so a response that
    would blow the byte ceiling *before* truncation but easily fits *after*
    truncation is truncated-and-flagged, not hard-failed. Hard-fail
    (``ToolError``) is reserved for the absolute byte-ceiling backstop — a
    single response that still can't fit even after element truncation
    (AC-H-6).

    F-6 (vocabulary unification): list_dir/search_text/view_file all signal
    their own tool-level overflow via the same literal ``overflow: true``
    key — folded here into the un-ignorable ``truncated`` contract, so a
    consumer that checks only ``truncated`` can never miss an overflow just
    because a *different* layer (the tool itself, not the central cap)
    detected it.

    NEW-1 (checker, second pass): this deliberately does NOT promote bare
    ``next_cursor`` presence. ``truncated`` must mean "you asked for more
    than you got" (the *request* was clamped — e.g. view_file's window
    exceeded ``max_lines_per_view``), never "the file/result merely
    continues past what you asked for". Those are different facts:
    view_file sets ``next_cursor``/``total_lines`` on *every* window that
    doesn't reach EOF, including a fully-satisfied, un-clamped read — a
    naive "next_cursor present => truncated" rule made `truncated` fire on
    nearly every call to the single most-used tool (training every
    consumer to ignore it) and attached ``retry_hint: narrower_scope`` to
    normal pagination, which is actively wrong advice (the correct action
    is to page via ``next_cursor``, not narrow the query). Only the literal
    ``overflow`` key — set exactly when the request itself was clamped —
    triggers the unified contract; ``next_cursor``/``total_lines`` remain a
    separate, strictly more informative continuation contract on their own.
    """
    if not isinstance(result, dict):
        return result

    fields = bounded_fields_for(name, arguments)
    truncated_fields: set[str] = set()
    for field in fields:
        items = result.get(field)
        if not isinstance(items, list):
            continue
        capped, overflowed = enforcement.cap_list_field(items)
        result[field] = capped
        if overflowed:
            truncated_fields.add(field)

    if result.get("overflow") and fields:
        truncated_fields.update(f for f in fields if isinstance(result.get(f), list))

    # R-1: cap any registered nested sub-field one level inside an already-
    # capped field's own elements (e.g. graph_query edges[].candidates) —
    # the sub-field escape the top-level-only registry above cannot see.
    # Runs against the SURVIVING (already top-level-capped) elements, so a
    # candidates[] belonging to an edge the top-level cap already dropped is
    # never even visited. Reported under the dotted key "field.nested_field"
    # in both `truncated_fields` and `returned_count` — distinguishable from
    # a top-level field's own truncation, never conflated with it.
    nested_returned_counts: dict[str, int] = {}
    for field, nested_field in nested_bounded_fields_for(name, arguments).items():
        items = result.get(field)
        if not isinstance(items, list):
            continue
        nested_key = f"{field}.{nested_field}"
        saw_nested_list = False
        nested_overflowed = False
        total_returned = 0
        for element in items:
            if not isinstance(element, dict):
                continue
            nested_items = element.get(nested_field)
            if not isinstance(nested_items, list):
                continue
            saw_nested_list = True
            capped, overflowed = enforcement.cap_list_field(nested_items)
            element[nested_field] = capped
            total_returned += len(capped)
            if overflowed:
                nested_overflowed = True
        if not saw_nested_list:
            continue
        nested_returned_counts[nested_key] = total_returned
        if nested_overflowed:
            truncated_fields.add(nested_key)

    if truncated_fields:
        result["truncated"] = True
        # F-7: which field(s) actually lost data — a bare summed total (or a
        # single "something truncated" boolean) can't tell a consumer this,
        # which matters for multi-field tools like search_symbol
        # (definitions + references can overflow independently).
        result["truncated_fields"] = sorted(truncated_fields)
        # Per-field counts, not summed — same reasoning: `returned_count`
        # must be attributable to a specific field, not a conflated total.
        returned_count = {f: len(result[f]) for f in fields if isinstance(result.get(f), list)}
        returned_count.update(nested_returned_counts)
        result["returned_count"] = returned_count
        result["more_available"] = True
        result.setdefault("retry_hint", "narrower_scope")

    # Absolute byte ceiling — the backstop, deliberately a separate (larger)
    # threshold than any individual tool's own byte cap (Config.
    # max_response_bytes docstring). Measured on the compact serialization
    # (information content), not the indent=2 presentation whitespace the
    # wire format uses for readability.
    body = json.dumps(result, separators=(",", ":"), default=str).encode("utf-8")
    if enforcement.over_absolute_byte_ceiling(body):
        raise ToolError(
            "RESPONSE_TOO_LARGE",
            f"Tool {name!r} response exceeds the absolute byte ceiling "
            f"({enforcement.config.max_response_bytes}B) even after element "
            f"truncation.",
            retry_hint="narrower_scope",
        )
    return result


async def dispatch_tool_call(
    name: str,
    arguments: dict[str, Any],
    config: Config,
    enforcement: Enforcement,
    memex: Memex,
    code_graph: CodeGraph,
) -> dict[str, Any]:
    """The tool dispatch table plus the D2 central bounds chokepoint.

    Kept as a standalone module-level function (not a closure inside
    `run_stdio`) so it is directly unit-testable without booting a stdio
    server — every `test_server.py` test calls this.
    """
    enforcement.assert_read_only(name)
    enforcement.assert_rate_limit()

    if name == "view_file":
        result: dict[str, Any] = await view_file(arguments, config, enforcement)
    elif name == "list_dir":
        result = await list_dir(arguments, config, enforcement)
    elif name == "search_text":
        result = await search_text(arguments, config, enforcement)
    elif name == "search_symbol":
        result = await search_symbol(arguments, config, enforcement, code_graph)
    elif name == "graph_query":
        result = await graph_query(arguments, config, enforcement, code_graph)
    elif name == "test_dry_run":
        result = await test_dry_run(arguments, config, enforcement)
    elif name == "memex_read":
        content = memex.read(arguments["ref"])
        result = {
            "ref": arguments["ref"],
            "content": content.decode("utf-8", errors="replace"),
        }
    else:
        raise ToolError("FORBIDDEN", f"Unknown tool {name!r}.", "none")

    return apply_central_bounds(name, arguments, result, enforcement)


def build_server_state(config: Config) -> tuple[Enforcement, Memex, CodeGraph]:
    """Construct the per-process state `run_stdio` wires together.

    Kept as a standalone function (not inlined in `run_stdio`) specifically
    so the ``CodeGraph.query_limit`` <-> ``Config.max_bound_field_elements``
    wiring is directly unit-testable without booting a stdio server
    (NEW-2, checker second pass): ``CodeGraph.__init__``'s own default for
    ``query_limit`` is ``DEFAULT_MAX_BOUND_FIELD_ELEMENTS + 1`` — a *module*
    constant — not ``config.max_bound_field_elements + 1``. Those two only
    agree today because this function passes the latter explicitly; nothing
    previously asserted that the wiring survives a refactor. A
    default-constructed `CodeGraph` paired with a custom-cap `Config` would
    silently reopen F-1 at a different cap value — the identical "two
    numbers that must agree, with nothing checking" failure mode.
    """
    enforcement = Enforcement(config)
    memex = Memex(config.memex_root)
    # read_only=True: `serve` never writes under .atlas (DIR-2). On a
    # current-epoch match the read-only connection just works; on a
    # mismatch/never-indexed repo, search_symbol/graph_query fail fast via
    # `code_graph.epoch_ok()` — no sweep, no rebuild, no write, ever.
    # query_limit=cap+1 (never the bare cap — F-1) is what makes the SQL
    # LIMIT and the central `_bounded_field` cap agree on the same boundary
    # instead of silently colliding at it (see CodeGraph's docstring).
    code_graph = CodeGraph(
        repo=config.repo,
        read_only=True,
        query_limit=config.max_bound_field_elements + 1,
    )
    return enforcement, memex, code_graph


async def run_stdio(config: Config) -> None:
    """Start the MCP server over stdio. Used by all major hosts."""
    server = Server("atlas-aci")
    enforcement, memex, code_graph = build_server_state(config)

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        manifest = build_tool_manifest(config)
        return [Tool(**t) for t in manifest]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        try:
            result = await dispatch_tool_call(
                name, arguments, config, enforcement, memex, code_graph
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        except ToolError as e:
            return [TextContent(type="text", text=json.dumps(e.to_dict(), indent=2))]
        except Exception as e:
            log.exception("tool_unhandled_error", tool=name, error=str(e))
            return [
                TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "error": "INTERNAL",
                            "message": str(e),
                            "retry_hint": "none",
                        },
                        indent=2,
                    ),
                )
            ]

    log.info("server_starting", repo=str(config.repo), memex=str(config.memex_root))

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )
