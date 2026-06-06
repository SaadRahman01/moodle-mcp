"""MCP server wiring.

Exposes one tool — `search_moodle_docs` — backed by `docs.MoodleDocs`.
Add more tools in future versions; keep this file focused on protocol
plumbing only.
"""
from __future__ import annotations

import logging
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .docs import MoodleDocs, format_results

log = logging.getLogger("moodle_mcp")

SEARCH_TOOL = Tool(
    name="search_moodle_docs",
    description=(
        "Search the official Moodle developer documentation at moodledev.io. "
        "Returns the top matching pages with title, URL, and a short excerpt. "
        "Use this whenever the user asks about a Moodle API, plugin type, "
        "Hooks API listener, capability, XMLDB workflow, web service, or any "
        "developer-facing Moodle concept."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural-language query, e.g. 'how to add a capability' or 'pluginfile callback'.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "default": 5,
                "description": "Maximum number of results to return.",
            },
        },
        "required": ["query"],
    },
)


def build_server() -> Server:
    server: Server = Server("moodle-mcp")
    docs = MoodleDocs()

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        return [SEARCH_TOOL]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        if name != SEARCH_TOOL.name:
            raise ValueError(f"Unknown tool: {name}")
        query = arguments.get("query", "").strip()
        if not query:
            return [TextContent(type="text", text="Error: query is required.")]
        limit = int(arguments.get("limit", 5))
        async with docs:
            hits = await docs.search(query, limit=limit)
        return [TextContent(type="text", text=format_results(hits))]

    return server


async def run() -> None:
    server = build_server()
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())
