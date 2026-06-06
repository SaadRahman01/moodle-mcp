"""MCP server wiring.

Exposes Moodle documentation tools, resources, and prompts.
"""
from __future__ import annotations

import contextlib
import json
import logging
import re
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

from .config import BASE_URL
from .docs import MoodleDocs
from .tools import (
    tool_call_ws_function,
    tool_capability_docs,
    tool_fetch_page,
    tool_hooks_listeners,
    tool_list_ws_functions,
    tool_plugin_types,
    tool_search_docs,
    tool_search_tracker,
    tool_version_info,
    tool_xmldb,
)

log = logging.getLogger("moodle_mcp")

# ToolAnnotations is only present on newer mcp SDKs; fall back to None.
ToolAnnotations: Any
try:  # pragma: no cover - import shim
    from mcp.types import ToolAnnotations as _ToolAnnotations
    ToolAnnotations = _ToolAnnotations
except ImportError:  # pragma: no cover
    ToolAnnotations = None


def _annotations(*, read_only: bool, open_world: bool = True, destructive: bool = False) -> Any:
    if ToolAnnotations is None:
        return None
    return ToolAnnotations(
        readOnlyHint=read_only,
        openWorldHint=open_world,
        destructiveHint=destructive,
    )


_VERSION_RE = re.compile(r"^\d+\.\d+$")


def _make_tool(
    name: str, description: str, schema: dict[str, Any], *, read_only: bool = True,
    destructive: bool = False,
) -> Tool:
    kwargs: dict[str, Any] = {
        "name": name,
        "description": description,
        "inputSchema": schema,
    }
    ann = _annotations(read_only=read_only, destructive=destructive)
    if ann is not None:
        kwargs["annotations"] = ann
    return Tool(**kwargs)


TOOLS: list[Tool] = [
    _make_tool(
        "search_moodle_docs",
        (
            "Search the official Moodle developer documentation at moodledev.io. "
            "Returns top matching pages with title, URL, headings, and a short "
            "excerpt. Supports phrase matching with double quotes and synonym "
            "expansion (cap→capability, ws→webservice, hook↔listener, etc.). "
            "Use this for any API, plugin type, Hooks listener, capability, "
            "XMLDB, web service, or developer-facing Moodle concept. "
            "When MOODLE_DOCS_ALGOLIA_APP_ID and _API_KEY env vars are set, "
            "uses Algolia DocSearch for higher-precision recall."
        ),
        {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": 'Natural-language query. Quote phrases for exact match: \'add a "capability"\'.',
                    "maxLength": 512,
                },
                "limit": {
                    "type": "integer", "minimum": 1, "maximum": 10, "default": 5,
                    "description": "Max number of results.",
                },
                "offset": {
                    "type": "integer", "minimum": 0, "default": 0,
                    "description": "Skip this many top results — use for pagination.",
                },
                "version": {
                    "type": "string",
                    "description": "Restrict to a specific Moodle docs version (e.g. '4.4'). Omit for any.",
                    "pattern": r"^\d+\.\d+$",
                },
            },
            "required": ["query"],
        },
    ),
    _make_tool(
        "fetch_moodle_page",
        (
            "Fetch a single moodledev.io page and return its full extracted "
            "body text + headings. Use after search_moodle_docs when you need "
            "the whole page, not the excerpt. Accepts absolute moodledev.io "
            "URLs or relative paths (e.g. /docs/apis/core/hooks)."
        ),
        {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Absolute moodledev.io URL or path beginning with /docs/...",
                    "maxLength": 2048,
                },
            },
            "required": ["url"],
        },
    ),
    _make_tool(
        "get_hooks_api_listeners",
        (
            "Return the Moodle core Hooks API index — known hook classes and "
            "where listeners are declared. Use when the user asks about hook "
            "callbacks, db/hooks.php, or which hooks are dispatched by core."
        ),
        {"type": "object", "properties": {}},
    ),
    _make_tool(
        "get_capability_docs",
        (
            "Look up Moodle capability/access API docs with a quick-reference "
            "card (db/access.php, has_capability, RISK_* bitmask). Optional "
            "component name (e.g. 'mod_quiz') focuses the search."
        ),
        {
            "type": "object",
            "properties": {
                "component": {
                    "type": "string",
                    "description": "Optional component name to scope the search, e.g. 'mod_quiz'.",
                },
            },
        },
    ),
    _make_tool(
        "lookup_db_xmldb",
        (
            "Search Moodle XMLDB / database conventions — install.xml schema, "
            "field types (XMLDB_TYPE_*), upgrade.php patterns."
        ),
        {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Field name, table, or concept — e.g. 'foreign key', 'XMLDB_TYPE_TEXT'.",
                    "maxLength": 512,
                },
            },
            "required": ["query"],
        },
    ),
    _make_tool(
        "list_plugin_types",
        (
            "List all Moodle plugin types with one-line descriptions and a "
            "documentation URL when one exists in the sitemap."
        ),
        {"type": "object", "properties": {}},
    ),
    _make_tool(
        "get_version_info",
        (
            "Return current Moodle versions parsed from moodledev.io/general/releases — "
            "useful when checking $plugin->requires or LTS targets."
        ),
        {"type": "object", "properties": {}},
    ),
    _make_tool(
        "search_tracker",
        (
            "Search tracker.moodle.org (Jira) for issues matching the query. "
            "Returns key, status, resolution, last-updated, and affected/fix "
            "versions. Use when the user asks about known bugs, MDL-XXXX "
            "tickets, or feature requests. Optional version filters scope the "
            "search to a release."
        ),
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Free-text query.", "maxLength": 512},
                "limit": {
                    "type": "integer", "minimum": 1, "maximum": 25, "default": 5,
                },
                "affects_version": {
                    "type": "string",
                    "description": "Jira affectedVersion name, e.g. '4.4'.",
                },
                "fix_version": {
                    "type": "string",
                    "description": "Jira fixVersion name, e.g. '4.4.2'.",
                },
            },
            "required": ["query"],
        },
    ),
    _make_tool(
        "list_ws_functions",
        (
            "List the Moodle Web Services functions available to the configured "
            "instance token (via core_webservice_get_site_info). Requires "
            "MOODLE_URL and MOODLE_TOKEN env vars. Read-only."
        ),
        {"type": "object", "properties": {}},
    ),
    _make_tool(
        "call_ws_function",
        (
            "Call a Moodle Web Services function on the configured instance. "
            "Requires MOODLE_URL and MOODLE_TOKEN env vars. The token's "
            "capabilities determine what may be invoked; this tool itself "
            "performs no privilege escalation. Many WS functions modify state — "
            "review the function name before invoking."
        ),
        {
            "type": "object",
            "properties": {
                "function": {
                    "type": "string",
                    "description": "WS function name, e.g. core_course_get_courses.",
                    "pattern": r"^[a-z][a-z0-9_]+$",
                    "maxLength": 128,
                },
                "args": {
                    "type": "object",
                    "description": "Function arguments. Nested dicts/lists are flattened to Moodle's bracketed-key form automatically.",
                    "additionalProperties": True,
                },
            },
            "required": ["function"],
        },
        read_only=False,
        destructive=True,
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
    Prompt(
        name="explain-capability",
        description="Explain a Moodle capability — what it grants, how to check, declaration template.",
        arguments=[
            PromptArgument(
                name="capability",
                description="Capability name, e.g. mod/quiz:attempt or local/foo:view.",
                required=True,
            ),
        ],
    ),
    Prompt(
        name="find-endpoint",
        description="Find the Moodle WS / external function or REST endpoint for a given action (e.g. 'list quiz attempts').",
        arguments=[
            PromptArgument(
                name="action",
                description="Plain-English action, e.g. 'list quiz attempts for a user'.",
                required=True,
            ),
            PromptArgument(
                name="component",
                description="Optional component scope, e.g. mod_quiz.",
                required=False,
            ),
        ],
    ),
    Prompt(
        name="explain-plugin-type",
        description="Explain a Moodle plugin type — purpose, required files, common pitfalls.",
        arguments=[
            PromptArgument(
                name="plugin_type",
                description="Plugin type, e.g. qtype, mod, format, auth.",
                required=True,
            ),
        ],
    ),
    Prompt(
        name="xmldb-upgrade",
        description="Generate an upgrade.php block for an XMLDB schema change.",
        arguments=[
            PromptArgument(
                name="change",
                description="Plain-English change, e.g. 'add column timecompleted to mdl_local_foo'.",
                required=True,
            ),
            PromptArgument(
                name="version",
                description="Target $plugin->version integer, e.g. 2026010100.",
                required=False,
            ),
        ],
    ),
    Prompt(
        name="diagnose-mdl",
        description="Triage a tracker.moodle.org issue (MDL-XXXXX) — summary, status, fix versions, workaround hints.",
        arguments=[
            PromptArgument(
                name="issue_key",
                description="Tracker issue key, e.g. MDL-12345.",
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


async def _emit_progress(server: Server, progress: float, total: float | None = None) -> None:
    """Best-effort progress notification. Silently noop when no request context."""
    with contextlib.suppress(Exception):
        ctx = server.request_context
        token = ctx.meta.progressToken if ctx and ctx.meta else None
        if token is None:
            return
        await ctx.session.send_progress_notification(token, progress, total)


async def _emit_log(server: Server, level: str, message: str) -> None:
    """Best-effort MCP logging/message notification. Silently noop without context."""
    with contextlib.suppress(Exception):
        ctx = server.request_context
        if ctx is None:
            return
        await ctx.session.send_log_message(level=level, data=message)  # type: ignore[arg-type]


def _new_request_id() -> str:
    import secrets
    return secrets.token_hex(4)


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
        rid = _new_request_id()
        log.info("[%s] tool %s called", rid, name)
        await _emit_log(server, "info", f"[{rid}] {name} started")
        try:
            if name == "search_moodle_docs":
                query = (arguments.get("query") or "").strip()
                if not query:
                    return [TextContent(type="text", text="Error: query is required.")]
                version = arguments.get("version")
                if version and not _VERSION_RE.match(str(version)):
                    return [TextContent(
                        type="text",
                        text=f"Error: version must look like '4.4', got {version!r}.",
                    )]
                await _emit_progress(server, 0.0, 1.0)
                result = await tool_search_docs(
                    docs, query,
                    limit=int(arguments.get("limit", 5)),
                    offset=int(arguments.get("offset", 0)),
                    version=version,
                )
                await _emit_progress(server, 1.0, 1.0)
                return _wrap(result)
            if name == "fetch_moodle_page":
                url = (arguments.get("url") or "").strip()
                if not url:
                    return [TextContent(type="text", text="Error: url is required.")]
                await _emit_progress(server, 0.0, 1.0)
                result = await tool_fetch_page(docs, url)
                await _emit_progress(server, 1.0, 1.0)
                return _wrap(result)
            if name == "get_hooks_api_listeners":
                return _wrap(await tool_hooks_listeners(docs))
            if name == "get_capability_docs":
                comp = arguments.get("component")
                return _wrap(await tool_capability_docs(docs, comp))
            if name == "lookup_db_xmldb":
                q = (arguments.get("query") or "").strip()
                if not q:
                    return [TextContent(type="text", text="Error: query is required.")]
                return _wrap(await tool_xmldb(docs, q))
            if name == "list_plugin_types":
                return _wrap(await tool_plugin_types(docs))
            if name == "get_version_info":
                return _wrap(await tool_version_info(docs))
            if name == "search_tracker":
                q = (arguments.get("query") or "").strip()
                if not q:
                    return [TextContent(type="text", text="Error: query is required.")]
                return _wrap(await tool_search_tracker(
                    docs, q,
                    limit=int(arguments.get("limit", 5)),
                    affects_version=arguments.get("affects_version"),
                    fix_version=arguments.get("fix_version"),
                ))
            if name == "list_ws_functions":
                return _wrap(await tool_list_ws_functions(docs))
            if name == "call_ws_function":
                fn = (arguments.get("function") or "").strip()
                if not fn:
                    return [TextContent(type="text", text="Error: function is required.")]
                return _wrap(await tool_call_ws_function(
                    docs, fn, arguments.get("args") or {},
                ))
            raise ValueError(f"Unknown tool: {name}")
        except Exception as e:
            log.exception("[%s] Tool %s failed", rid, name)
            await _emit_log(server, "error", f"[{rid}] {name} failed: {e}")
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
        elif name == "explain-capability":
            cap = args.get("capability", "")
            text = (
                f"Explain the Moodle capability `{cap}`. Cover: (1) what it "
                "grants, (2) which contexts it applies to, (3) the default "
                "role assignments, (4) any RISK_* flags it typically carries, "
                "(5) a db/access.php declaration template, and (6) the correct "
                "has_capability/require_capability check at the call site. Use "
                "get_capability_docs and search_moodle_docs to ground each "
                "claim; cite the moodledev.io page."
            )
        elif name == "find-endpoint":
            action = args.get("action", "")
            component = args.get("component") or ""
            scope = f" (scope: `{component}`)" if component else ""
            text = (
                f"Find the Moodle endpoint that performs: **{action}**{scope}. "
                "Steps: (1) search_moodle_docs for the action and component, "
                "(2) if MOODLE_URL+MOODLE_TOKEN are set, call list_ws_functions "
                "and filter by name pattern, (3) check the external (WS) API "
                "docs at docs/apis/core/external for the function signature. "
                "Report: function name, required params, return shape, since-"
                "version, and an example invocation via call_ws_function."
            )
        elif name == "explain-plugin-type":
            ptype = args.get("plugin_type", "")
            text = (
                f"Explain the Moodle plugin type `{ptype}`. Cover: (1) what "
                "this plugin type does, (2) directory layout under the Moodle "
                "tree, (3) required files (version.php, language pack, "
                "callbacks), (4) the canonical interface or base class to "
                "extend, (5) common pitfalls and gotchas. Use list_plugin_types "
                "and search_moodle_docs; cite the plugintypes/* page."
            )
        elif name == "xmldb-upgrade":
            change = args.get("change", "")
            version = args.get("version") or "<next-version>"
            text = (
                f"Write the `db/upgrade.php` block needed for: **{change}**. "
                f"Target $plugin->version: `{version}`. Show: (1) the "
                "`if ($oldversion < {version})` guard, (2) the XMLDB editor "
                "operations ($dbman->add_field / rename_table / etc.), (3) the "
                "upgrade_plugin_savepoint call, (4) the matching db/install.xml "
                "delta. Use lookup_db_xmldb to confirm field types and "
                "search_moodle_docs for upgrade conventions. Cite each rule."
            )
        elif name == "diagnose-mdl":
            key = args.get("issue_key", "")
            text = (
                f"Triage tracker issue `{key}`. Steps: (1) call search_tracker "
                f"with the key to fetch summary/status/resolution/fix versions, "
                "(2) summarize whether it's open, fixed, won't-fix, or "
                "duplicate, (3) if fixed, list the fix version(s) and whether "
                "they're released, (4) if open, suggest a workaround based on "
                "the summary and related docs. Link the tracker URL."
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
