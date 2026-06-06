"""Tests for the MCP server wiring — tool listing, dispatch, resources, prompts."""
from __future__ import annotations

import httpx
import pytest
import respx

from moodle_mcp.server import (
    CURATED_RESOURCES,
    PROMPTS,
    TOOLS,
    build_server,
)

SITEMAP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://moodledev.io/docs/apis/subsystems/access</loc></url>
  <url><loc>https://moodledev.io/docs/apis/core/hooks</loc></url>
</urlset>
"""

PAGE_HTML = """<html><head><title>Stub | moodledev.io</title></head>
<body><main><h1>Stub</h1><p>capability access body.</p></main></body></html>"""


def test_tools_have_required_metadata() -> None:
    names = {t.name for t in TOOLS}
    assert "search_moodle_docs" in names
    assert "fetch_moodle_page" in names
    assert "get_hooks_api_listeners" in names
    assert "get_capability_docs" in names
    assert "lookup_db_xmldb" in names
    assert "list_plugin_types" in names
    assert "get_version_info" in names
    assert "search_tracker" in names
    for t in TOOLS:
        assert t.description
        assert t.inputSchema["type"] == "object"


def test_prompts_have_required_args() -> None:
    names = {p.name for p in PROMPTS}
    assert "moodle-plugin-skeleton" in names
    assert "moodle-capability-review" in names
    assert "moodle-hooks-migration" in names


def test_curated_resources_well_formed() -> None:
    assert any(path == "docs/apis/core/hooks" for path, _, _ in CURATED_RESOURCES)
    assert all(name and desc for _, name, desc in CURATED_RESOURCES)


@pytest.mark.asyncio
@respx.mock
async def test_build_server_returns_server_and_docs(tmp_path) -> None:
    from moodle_mcp.cache import DiskCache
    server, docs = build_server()
    docs._cache = DiskCache(tmp_path / "cache", default_ttl=60.0)
    respx.get("https://moodledev.io/sitemap.xml").mock(
        return_value=httpx.Response(200, text=SITEMAP_XML)
    )
    respx.get(url__regex=r"https://moodledev\.io/docs/.*").mock(
        return_value=httpx.Response(200, text=PAGE_HTML)
    )
    await docs.__aenter__()
    try:
        urls = await docs._load_sitemap()
        assert len(urls) == 2
    finally:
        await docs.aclose()
