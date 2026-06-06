"""Tests for high-level tools and the MCP server tool dispatch path."""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from moodle_mcp.cache import DiskCache
from moodle_mcp.docs import MoodleDocs
from moodle_mcp.tools import (
    tool_capability_docs,
    tool_fetch_page,
    tool_hooks_listeners,
    tool_plugin_types,
    tool_search_docs,
    tool_search_tracker,
    tool_version_info,
    tool_xmldb,
)

SITEMAP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://moodledev.io/docs/apis/subsystems/access</loc></url>
  <url><loc>https://moodledev.io/docs/apis/core/hooks</loc></url>
  <url><loc>https://moodledev.io/docs/apis/plugintypes/local</loc></url>
  <url><loc>https://moodledev.io/docs/apis/plugintypes/mod</loc></url>
  <url><loc>https://moodledev.io/docs/apis/subsystems/xmldb</loc></url>
  <url><loc>https://moodledev.io/general/releases</loc></url>
</urlset>
"""

PAGE_HTML = """<html><head><title>Sample Page | moodledev.io</title></head>
<body><main>
<h1>Sample heading</h1>
<h2>Subsection</h2>
<p>This page documents the Moodle access capability and XMLDB schema.</p>
</main></body></html>"""

HOOKS_HTML = """<html><head><title>Hooks API | moodledev.io</title></head>
<body><main>
<h1>Hooks API</h1>
<p>Use \\core\\hook\\after_config and \\core_user\\hook\\extend_user_menu in your db/hooks.php file.</p>
</main></body></html>"""

RELEASES_HTML = """<html><head><title>Releases</title></head>
<body><main>
<h1>Releases</h1>
<p>Moodle 4.4 LTS is the current long-term release. Moodle 4.5 follows. Moodle 4.3 is still supported.</p>
</main></body></html>"""


@pytest.fixture
def tmp_cache(tmp_path: Path) -> DiskCache:
    return DiskCache(tmp_path / "cache", default_ttl=60.0)


@pytest.fixture
def mocked_routes():
    respx.get("https://moodledev.io/sitemap.xml").mock(
        return_value=httpx.Response(200, text=SITEMAP_XML)
    )
    respx.get("https://moodledev.io/docs/apis/core/hooks").mock(
        return_value=httpx.Response(200, text=HOOKS_HTML)
    )
    respx.get("https://moodledev.io/general/releases").mock(
        return_value=httpx.Response(200, text=RELEASES_HTML)
    )
    respx.get(url__regex=r"https://moodledev\.io/docs/.*").mock(
        return_value=httpx.Response(200, text=PAGE_HTML)
    )


@pytest.mark.asyncio
@respx.mock
async def test_tool_search_docs_returns_markdown_and_data(
    tmp_cache: DiskCache, mocked_routes: None,
) -> None:
    async with MoodleDocs(cache=tmp_cache) as docs:
        out = await tool_search_docs(docs, "access capability", limit=2)
    assert "###" in out["markdown"]
    assert isinstance(out["data"]["hits"], list)
    assert out["data"]["limit"] == 2


@pytest.mark.asyncio
@respx.mock
async def test_tool_fetch_page_handles_relative_and_absolute(
    tmp_cache: DiskCache, mocked_routes: None,
) -> None:
    async with MoodleDocs(cache=tmp_cache) as docs:
        rel = await tool_fetch_page(docs, "/docs/apis/subsystems/access")
        absu = await tool_fetch_page(docs, "https://moodledev.io/docs/apis/subsystems/access")
    assert "Sample heading" in rel["markdown"]
    assert rel["data"]["url"].endswith("/access")
    assert absu["data"]["url"].endswith("/access")


@pytest.mark.asyncio
@respx.mock
async def test_tool_fetch_page_rejects_offsite(tmp_cache: DiskCache) -> None:
    async with MoodleDocs(cache=tmp_cache) as docs:
        out = await tool_fetch_page(docs, "https://evil.example.com/foo")
    assert "Error" in out["markdown"]
    assert out["data"]["error"] == "out-of-scope-host"


@pytest.mark.asyncio
@respx.mock
async def test_tool_hooks_listeners_parses_hook_classes(
    tmp_cache: DiskCache, mocked_routes: None,
) -> None:
    async with MoodleDocs(cache=tmp_cache) as docs:
        out = await tool_hooks_listeners(docs)
    assert "Hooks" in out["markdown"]
    classes = out["data"]["hook_classes"]
    assert any("hook" in c.lower() for c in classes)


@pytest.mark.asyncio
@respx.mock
async def test_tool_capability_docs_includes_quickref(
    tmp_cache: DiskCache, mocked_routes: None,
) -> None:
    async with MoodleDocs(cache=tmp_cache) as docs:
        out = await tool_capability_docs(docs, component="mod_quiz")
    assert "Quick reference" in out["markdown"]
    assert "RISK_" in out["markdown"]
    assert out["data"]["component"] == "mod_quiz"


@pytest.mark.asyncio
@respx.mock
async def test_tool_xmldb_returns_hits(
    tmp_cache: DiskCache, mocked_routes: None,
) -> None:
    async with MoodleDocs(cache=tmp_cache) as docs:
        out = await tool_xmldb(docs, "foreign key")
    assert "XMLDB" in out["markdown"]


@pytest.mark.asyncio
@respx.mock
async def test_tool_plugin_types_lists_known_types(
    tmp_cache: DiskCache, mocked_routes: None,
) -> None:
    async with MoodleDocs(cache=tmp_cache) as docs:
        out = await tool_plugin_types(docs)
    types = {t["type"] for t in out["data"]["plugin_types"]}
    assert "mod" in types
    assert "local" in types
    assert "qtype" in types


@pytest.mark.asyncio
@respx.mock
async def test_tool_version_info_parses_versions(
    tmp_cache: DiskCache, mocked_routes: None,
) -> None:
    async with MoodleDocs(cache=tmp_cache) as docs:
        out = await tool_version_info(docs)
    versions = out["data"]["versions"]
    assert versions
    assert any(v["version"].startswith("4.") for v in versions)
    assert any(v["lts"] for v in versions)


@pytest.mark.asyncio
@respx.mock
async def test_tool_search_tracker(tmp_cache: DiskCache) -> None:
    payload = {
        "issues": [
            {
                "key": "MDL-12345",
                "fields": {
                    "summary": "Quiz attempt regression",
                    "status": {"name": "Open"},
                    "resolution": None,
                    "updated": "2026-01-01T00:00:00.000+0000",
                },
            }
        ]
    }
    respx.get("https://tracker.moodle.org/rest/api/2/search").mock(
        return_value=httpx.Response(200, json=payload)
    )
    async with MoodleDocs(cache=tmp_cache) as docs:
        out = await tool_search_tracker(docs, "quiz attempt", limit=5)
    assert "MDL-12345" in out["markdown"]
    assert out["data"]["issues"][0]["key"] == "MDL-12345"


@pytest.mark.asyncio
@respx.mock
async def test_tool_search_tracker_handles_failure(tmp_cache: DiskCache) -> None:
    respx.get("https://tracker.moodle.org/rest/api/2/search").mock(
        return_value=httpx.Response(500)
    )
    async with MoodleDocs(cache=tmp_cache) as docs:
        out = await tool_search_tracker(docs, "anything", limit=2)
    assert "failed" in out["markdown"].lower()
    assert "error" in out["data"]
