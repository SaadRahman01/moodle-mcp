"""Tests for the MCP server tool-call dispatch path."""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from moodle_mcp.cache import DiskCache
from moodle_mcp.server import build_server

SITEMAP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://moodledev.io/docs/apis/subsystems/access</loc></url>
</urlset>"""

PAGE_HTML = """<html><head><title>x</title></head>
<body><main><h1>X</h1><p>body</p></main></body></html>"""


async def _dispatch(server, docs, name, arguments):
    """Invoke the registered tool-call handler directly."""
    # The low-level server stores the handler on the request handlers dict.
    handlers = server.request_handlers
    from mcp.types import CallToolRequest, CallToolRequestParams
    req = CallToolRequest(
        method="tools/call",
        params=CallToolRequestParams(name=name, arguments=arguments),
    )
    handler = handlers[CallToolRequest]
    result = await handler(req)
    return result


@pytest.mark.asyncio
@respx.mock
async def test_dispatch_unknown_tool_returns_error(tmp_path: Path) -> None:
    server, docs = build_server()
    docs._cache = DiskCache(tmp_path / "c", default_ttl=60.0)
    respx.get("https://moodledev.io/sitemap.xml").mock(
        return_value=httpx.Response(200, text=SITEMAP_XML)
    )
    respx.get(url__regex=r"https://moodledev\.io/docs/.*").mock(
        return_value=httpx.Response(200, text=PAGE_HTML)
    )
    try:
        result = await _dispatch(server, docs, "totally_made_up_tool", {})
    finally:
        await docs.aclose()
    text = result.root.content[0].text
    assert "Unknown tool" in text or "Error" in text


@pytest.mark.asyncio
@respx.mock
async def test_dispatch_empty_query_returns_error(tmp_path: Path) -> None:
    server, docs = build_server()
    docs._cache = DiskCache(tmp_path / "c", default_ttl=60.0)
    try:
        result = await _dispatch(server, docs, "search_moodle_docs", {"query": ""})
    finally:
        await docs.aclose()
    text = result.root.content[0].text
    assert "required" in text.lower()


@pytest.mark.asyncio
@respx.mock
async def test_dispatch_invalid_version_returns_error(tmp_path: Path) -> None:
    server, docs = build_server()
    docs._cache = DiskCache(tmp_path / "c", default_ttl=60.0)
    try:
        result = await _dispatch(
            server, docs, "search_moodle_docs",
            {"query": "x", "version": "garbage"},
        )
    finally:
        await docs.aclose()
    text = result.root.content[0].text
    # Either the schema validator (preferred) or our runtime check catches it.
    assert "version" in text.lower() or "validation" in text.lower()


@pytest.mark.asyncio
@respx.mock
async def test_dispatch_ws_tool_unconfigured(tmp_path: Path) -> None:
    """call_ws_function returns a friendly error when env not set."""
    from unittest.mock import patch

    from moodle_mcp import ws

    server, docs = build_server()
    docs._cache = DiskCache(tmp_path / "c", default_ttl=60.0)
    try:
        with patch.object(ws, "MOODLE_URL", ""), patch.object(ws, "MOODLE_TOKEN", ""):
            result = await _dispatch(
                server, docs, "call_ws_function",
                {"function": "core_webservice_get_site_info"},
            )
    finally:
        await docs.aclose()
    text = result.root.content[0].text
    assert "not configured" in text.lower() or "Moodle WS" in text
