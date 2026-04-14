"""MCP server wiring.

Translates between the MCP protocol (tools/list, tools/call) and the
ATLAS tool implementations. Every call passes through enforcement.
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from atlas_aci.codegraph import CodeGraph
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
                "Index-backed symbol lookup. Returns definitions and references. "
                "Run `atlas-aci index` first to build the graph."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": ["any", "class", "module", "method", "function"],
                        "default": "any",
                    },
                },
            },
        },
        {
            "name": "graph_query",
            "description": (
                "Query the code graph. DSL: 'callers_of:Symbol', "
                "'definitions_of:Name', 'subclasses_of:Class'. "
                "Run `atlas-aci index` first."
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
                "Byte-exact retrieval of a previously captured excerpt. "
                "Refs are returned by other tools when they cite source content."
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


async def run_stdio(config: Config) -> None:
    """Start the MCP server over stdio. Used by all major hosts."""
    server = Server("atlas-aci")
    enforcement = Enforcement(config)
    memex = Memex(config.memex_root)
    code_graph = CodeGraph(repo=config.repo)

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        manifest = build_tool_manifest(config)
        return [Tool(**t) for t in manifest]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        try:
            enforcement.assert_read_only(name)
            enforcement.assert_rate_limit()

            if name == "view_file":
                result = await view_file(arguments, config, enforcement)
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
