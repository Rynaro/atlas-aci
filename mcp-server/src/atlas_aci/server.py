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
                "'definitions_of:Name', 'subclasses_of:Class'. Bounded and "
                "flagged like every other tool. Run `atlas-aci index` first."
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
    """
    if not isinstance(result, dict):
        return result

    fields = bounded_fields_for(name, arguments)
    truncated_any = False
    for field in fields:
        items = result.get(field)
        if not isinstance(items, list):
            continue
        capped, overflowed = enforcement.cap_list_field(items)
        result[field] = capped
        truncated_any = truncated_any or overflowed

    if truncated_any:
        result["truncated"] = True
        result["returned_count"] = sum(
            len(result[f]) for f in fields if isinstance(result.get(f), list)
        )
        result["more_available"] = True
        result["retry_hint"] = "narrower_scope"

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


async def run_stdio(config: Config) -> None:
    """Start the MCP server over stdio. Used by all major hosts."""
    server = Server("atlas-aci")
    enforcement = Enforcement(config)
    memex = Memex(config.memex_root)
    # read_only=True: `serve` never writes under .atlas (DIR-2). On a
    # current-epoch match the read-only connection just works; on a
    # mismatch/never-indexed repo, search_symbol/graph_query fail fast via
    # `code_graph.epoch_ok()` — no sweep, no rebuild, no write, ever.
    code_graph = CodeGraph(repo=config.repo, read_only=True)

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
