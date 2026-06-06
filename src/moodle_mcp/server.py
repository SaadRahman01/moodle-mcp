"""MCP server wiring.

Exposes Moodle documentation tools, resources, and prompts.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    GetPromptResult,
    Prompt,
    PromptArgument,
    PromptMessage,
    Resource,
    TextContent,
    Tool,
)
from pydantic import AnyUrl

from .docs import BASE_URL, MoodleDocs
from .tools import (
    tool_capability_docs,
    tool_fetch_page,
    tool_hooks_listeners,
    tool_plugin_types,
    tool_search_docs,
    tool_search_tracker,
    tool_version_info,
    tool_xmldb,
)

log = logging.getLogger("moodle_mcp")


TOOLS: list[Tool] = [
    Tool(
        name="search_moodle_docs",
        description=(
            "Search the official Moodle developer documentation at moodledev.io. "
            "Returns top matching pages with title, URL, headings, and a short "
            "excerpt. Supports phrase matching with double quotes and synonym "
            "expansion (cap→capability, ws→webservice, hook↔listener, etc.). "
            "Use this for any API, plugin type, Hooks listener, capability, "
            "XMLDB, web service, or developer-facing Moodle concept."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": 'Natural-language query. Quote phrases for exact match: \'add a "capability"\'.',
                },
                "limit": {
                    "type": "integer", "minimum": 1, "maximum": 10, "default": 5,
                    "description": "Max number of results.",
                },
                "offset": {
                    "type": "integer", "minimum": 0, "default": 0,
                    "description": "Skip this many top results — use for pagination.",
                },
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="fetch_moodle_page",
        description=(
            "Fetch a single moodledev.io page and return its full extracted "
            "body text + headings. Use after search_moodle_docs when you need "
            "the whole page, not the excerpt. Accepts absolute moodledev.io "
            "URLs or relative paths (e.g. /docs/apis/core/hooks)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Absolute moodledev.io URL or path beginning with /docs/...",
                },
            },
            "required": ["url"],
        },
    ),
    Tool(
        name="get_hooks_api_listeners",
        description=(
            "Return the Moodle core Hooks API index — known hook classes and "
            "where listeners are declared. Use when the user asks about hook "
            "callbacks, db/hooks.php, or which hooks are dispatched by core."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="get_capability_docs",
        description=(
            "Look up Moodle capability/access API docs with a quick-reference "
            "card (db/access.php, has_capability, RISK_* bitmask). Optional "
            "component name (e.g. 'mod_quiz') focuses the search."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "component": {
                    "type": "string",
                    "description": "Optional component name to scope the search, e.g. 'mod_quiz'.",
                },
            },
        },
    ),
    Tool(
        name="lookup_db_xmldb",
        description=(
            "Search Moodle XMLDB / database conventions — install.xml schema, "
            "field types (XMLDB_TYPE_*), upgrade.php patterns."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Field name, table, or concept — e.g. 'foreign key', 'XMLDB_TYPE_TEXT'.",
                },
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="list_plugin_types",
        description=(
            "List all Moodle plugin types with one-line descriptions and a "
            "documentation URL when one exists in the sitemap."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="get_version_info",
        description=(
            "Return current Moodle versions parsed from moodledev.io/general/releases — "
            "useful when checking $plugin->requires or LTS targets."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="search_tracker",
        description=(
            "Search tracker.moodle.org (Jira) for issues matching the query. "
            "Returns key, status, resolution, last-updated. Use when the user "
            "asks about known bugs, MDL-XXXX tickets, or feature requests."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Free-text query."},
                "limit": {
                    "type": "integer", "minimum": 1, "maximum": 25, "default": 5,
                },
            },
            "required": ["query"],
        },
    ),
]


PROMPTS: list[Prompt] = [
    Prompt(
        name="moodle-plugin-skeleton",
        description="Walk me through scaffolding a new Moodle plugin of a given type.",
        arguments=[
            PromptArgument(
                name="plugin_type",
                description="Plugin type, e.g. mod, local, block, qtype.",
                required=True,
            ),
            PromptArgument(
                name="component_name",
                description="Component short name (no prefix), e.g. 'helloworld'.",
                required=True,
            ),
        ],
    ),
    Prompt(
        name="moodle-capability-review",
        description="Review a chunk of Moodle code for correct capability usage.",
        arguments=[
            PromptArgument(name="code", description="Code snippet to review.", required=True),
        ],
    ),
    Prompt(
        name="moodle-hooks-migration",
        description="Help me migrate legacy callbacks/observers to the Hooks API.",
        arguments=[
            PromptArgument(
                name="legacy_callback",
                description="Name of the legacy callback or observer.",
                required=True,
            ),
        ],
    ),
]


CURATED_RESOURCES: list[tuple[str, str, str]] = [
    ("docs/apis/core/hooks", "Hooks API overview", "Moodle core Hooks API index."),
    ("docs/apis/subsystems/access", "Access / capability API", "Capability declaration and checks."),
    ("docs/apis/plugintypes", "Plugin types index", "List of all Moodle plugin types."),
    ("docs/apis/core/dml", "Data Manipulation Layer", "DML — $DB->get_record, etc."),
    ("docs/apis/subsystems/xmldb", "XMLDB schema docs", "install.xml + upgrade.php patterns."),
    ("docs/apis/core/external", "External (web service) API", "Defining external functions."),
    ("docs/apis/subsystems/task", "Task API", "Scheduled and adhoc tasks."),
    ("docs/apis/subsystems/output", "Output API", "Renderers, templates, theming."),
    ("general/releases", "Moodle releases", "Currently supported versions."),
]


def _wrap(payload: dict[str, Any]) -> list[TextContent]:
    md = payload.get("markdown", "")
    data = payload.get("data")
    if data is None:
        return [TextContent(type="text", text=md)]
    return [
        TextContent(type="text", text=md),
        TextContent(
            type="text",
            text="```json\n" + json.dumps(data, indent=2, default=str) + "\n```",
        ),
    ]


def build_server() -> tuple[Server, MoodleDocs]:
    server: Server = Server("moodle-mcp")
    docs = MoodleDocs()

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        return TOOLS

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        if docs._client is None:
            await docs.__aenter__()
        try:
            if name == "search_moodle_docs":
                query = arguments.get("query", "").strip()
                if not query:
                    return [TextContent(type="text", text="Error: query is required.")]
                return _wrap(await tool_search_docs(
                    docs, query,
                    limit=int(arguments.get("limit", 5)),
                    offset=int(arguments.get("offset", 0)),
                ))
            if name == "fetch_moodle_page":
                url = arguments.get("url", "").strip()
                if not url:
                    return [TextContent(type="text", text="Error: url is required.")]
                return _wrap(await tool_fetch_page(docs, url))
            if name == "get_hooks_api_listeners":
                return _wrap(await tool_hooks_listeners(docs))
            if name == "get_capability_docs":
                comp = arguments.get("component")
                return _wrap(await tool_capability_docs(docs, comp))
            if name == "lookup_db_xmldb":
                q = arguments.get("query", "").strip()
                if not q:
                    return [TextContent(type="text", text="Error: query is required.")]
                return _wrap(await tool_xmldb(docs, q))
            if name == "list_plugin_types":
                return _wrap(await tool_plugin_types(docs))
            if name == "get_version_info":
                return _wrap(await tool_version_info(docs))
            if name == "search_tracker":
                q = arguments.get("query", "").strip()
                if not q:
                    return [TextContent(type="text", text="Error: query is required.")]
                return _wrap(await tool_search_tracker(
                    docs, q, limit=int(arguments.get("limit", 5)),
                ))
            raise ValueError(f"Unknown tool: {name}")
        except Exception as e:
            log.exception("Tool %s failed", name)
            return [TextContent(type="text", text=f"Error in {name}: {e}")]

    @server.list_resources()
    async def _list_resources() -> list[Resource]:
        out: list[Resource] = []
        for path, name, desc in CURATED_RESOURCES:
            out.append(Resource(
                uri=AnyUrl(f"moodle://{path}"),
                name=name,
                description=desc,
                mimeType="text/markdown",
            ))
        return out

    @server.read_resource()
    async def _read_resource(uri: AnyUrl) -> str:
        s = str(uri)
        if not s.startswith("moodle://"):
            raise ValueError(f"Unsupported scheme: {uri}")
        path = s[len("moodle://"):].strip("/")
        if docs._client is None:
            await docs.__aenter__()
        payload = await tool_fetch_page(docs, f"{BASE_URL}/{path}")
        return payload.get("markdown", "")

    @server.list_prompts()
    async def _list_prompts() -> list[Prompt]:
        return PROMPTS

    @server.get_prompt()
    async def _get_prompt(name: str, arguments: dict[str, str] | None) -> GetPromptResult:
        args = arguments or {}
        if name == "moodle-plugin-skeleton":
            plugin_type = args.get("plugin_type", "local")
            comp = args.get("component_name", "example")
            text = (
                f"Scaffold a new Moodle `{plugin_type}_{comp}` plugin. "
                "Use search_moodle_docs and list_plugin_types to confirm the "
                "required files (version.php, db/access.php, db/install.xml, "
                "lang/en/, settings.php where applicable). Show me the minimal "
                "directory tree and the contents of each file, citing the "
                "moodledev.io page that justifies each choice."
            )
        elif name == "moodle-capability-review":
            code = args.get("code", "")
            text = (
                "Review the following Moodle code for capability usage. Check: "
                "(1) every privileged action is guarded by require_capability "
                "or has_capability with the right context, (2) capabilities are "
                "declared in db/access.php with appropriate RISK_* flags, "
                "(3) no hard-coded role assumptions. Use get_capability_docs "
                "and search_moodle_docs to back up findings.\n\n"
                f"```php\n{code}\n```"
            )
        elif name == "moodle-hooks-migration":
            cb = args.get("legacy_callback", "")
            text = (
                f"Migrate the legacy callback `{cb}` to the Moodle Hooks API. "
                "Use get_hooks_api_listeners to find the matching hook class, "
                "then show the new db/hooks.php registration and the listener "
                "method signature."
            )
        else:
            raise ValueError(f"Unknown prompt: {name}")
        return GetPromptResult(
            description=name,
            messages=[PromptMessage(
                role="user",
                content=TextContent(type="text", text=text),
            )],
        )

    return server, docs


async def run() -> None:
    server, docs = build_server()
    try:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    finally:
        await docs.aclose()
