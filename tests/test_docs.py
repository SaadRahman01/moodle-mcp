"""Tests for the docs search backend.

Pure-function tests use no network. Integration test against MoodleDocs uses
respx to mock httpx so the suite stays fully offline.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from moodle_mcp.docs import (
    DocHit,
    MoodleDocs,
    extract_title_and_excerpt,
    format_results,
    parse_sitemap,
)

SITEMAP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://moodledev.io/docs/apis/subsystems/access</loc></url>
  <url><loc>https://moodledev.io/docs/apis/core/hooks/index</loc></url>
  <url><loc>https://moodledev.io/docs/apis/plugintypes/local</loc></url>
  <url><loc>https://moodledev.io/docs/guides/upgrading</loc></url>
</urlset>
"""

PAGE_HTML = """<!doctype html>
<html><head><title>Access API — capabilities | moodledev.io</title></head>
<body>
  <nav>nav stuff</nav>
  <main>
    <h1>Access API</h1>
    <p>Capabilities are declared in db/access.php and checked at runtime using
    has_capability(). Always require_capability() before performing privileged
    operations.</p>
  </main>
  <footer>footer chrome</footer>
</body></html>
"""


def test_parse_sitemap_extracts_loc_entries() -> None:
    urls = parse_sitemap(SITEMAP_XML)
    assert "https://moodledev.io/docs/apis/subsystems/access" in urls
    assert len(urls) == 4


def test_extract_title_strips_suffix_and_chrome() -> None:
    title, excerpt = extract_title_and_excerpt(PAGE_HTML)
    assert title == "Access API — capabilities"
    assert "Capabilities are declared" in excerpt
    assert "nav stuff" not in excerpt
    assert "footer chrome" not in excerpt


def test_extract_handles_pages_with_no_main() -> None:
    html = "<html><head><title>x</title></head><body><p>hello world</p></body></html>"
    title, excerpt = extract_title_and_excerpt(html)
    assert title == "x"
    assert "hello world" in excerpt


def test_format_results_handles_empty() -> None:
    assert "No matches" in format_results([])


def test_format_results_renders_markdown() -> None:
    out = format_results([
        DocHit(url="https://moodledev.io/x", title="X Page", excerpt="body", score=1.0)
    ])
    assert "### 1. X Page" in out
    assert "https://moodledev.io/x" in out
    assert "body" in out


@pytest.mark.asyncio
@respx.mock
async def test_search_returns_relevant_hits() -> None:
    respx.get("https://moodledev.io/sitemap.xml").mock(
        return_value=httpx.Response(200, text=SITEMAP_XML)
    )
    respx.get("https://moodledev.io/docs/apis/subsystems/access").mock(
        return_value=httpx.Response(200, text=PAGE_HTML)
    )
    respx.get("https://moodledev.io/docs/apis/core/hooks/index").mock(
        return_value=httpx.Response(200, text=PAGE_HTML)
    )
    respx.get("https://moodledev.io/docs/apis/plugintypes/local").mock(
        return_value=httpx.Response(200, text=PAGE_HTML)
    )
    respx.get("https://moodledev.io/docs/guides/upgrading").mock(
        return_value=httpx.Response(200, text=PAGE_HTML)
    )

    async with MoodleDocs() as docs:
        hits = await docs.search("access capability", limit=3)
    assert hits, "expected at least one hit"
    assert hits[0].url.endswith("/access")
    assert hits[0].score > 0


@pytest.mark.asyncio
@respx.mock
async def test_search_with_no_matches_returns_empty() -> None:
    respx.get("https://moodledev.io/sitemap.xml").mock(
        return_value=httpx.Response(200, text=SITEMAP_XML)
    )
    async with MoodleDocs() as docs:
        hits = await docs.search("xyzzy-no-such-thing-anywhere", limit=5)
    assert hits == []
