"""Tests for the docs search backend.

Pure-function tests use no network. Integration test against MoodleDocs uses
respx to mock httpx so the suite stays fully offline.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from moodle_mcp.cache import DiskCache
from moodle_mcp.docs import (
    DocHit,
    MoodleDocs,
    extract_page,
    extract_title_and_excerpt,
    format_results,
    hits_to_dicts,
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

SITEMAP_INDEX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://moodledev.io/sitemap-docs.xml</loc></sitemap>
</sitemapindex>
"""

PAGE_HTML = """<!doctype html>
<html><head><title>Access API — capabilities | moodledev.io</title></head>
<body>
  <nav>nav stuff</nav>
  <main>
    <h1>Access API</h1>
    <h2>Declaring capabilities</h2>
    <p>Capabilities are declared in db/access.php and checked at runtime using
    has_capability(). Always require_capability() before performing privileged
    operations.</p>
  </main>
  <footer>footer chrome</footer>
</body></html>
"""


@pytest.fixture
def tmp_cache(tmp_path: Path) -> DiskCache:
    return DiskCache(tmp_path / "cache", default_ttl=60.0)


def test_parse_sitemap_extracts_loc_entries() -> None:
    pages, sitemaps = parse_sitemap(SITEMAP_XML)
    assert "https://moodledev.io/docs/apis/subsystems/access" in pages
    assert len(pages) == 4
    assert sitemaps == []


def test_parse_sitemap_index_returns_child_sitemaps() -> None:
    pages, sitemaps = parse_sitemap(SITEMAP_INDEX_XML)
    assert pages == []
    assert sitemaps == ["https://moodledev.io/sitemap-docs.xml"]


def test_parse_sitemap_handles_malformed_xml() -> None:
    pages, sitemaps = parse_sitemap("not xml at all <<<")
    assert pages == []
    assert sitemaps == []


def test_extract_title_strips_suffix_and_chrome() -> None:
    title, excerpt = extract_title_and_excerpt(PAGE_HTML)
    assert title == "Access API — capabilities"
    assert "Capabilities are declared" in excerpt
    assert "nav stuff" not in excerpt
    assert "footer chrome" not in excerpt


def test_extract_page_returns_headings() -> None:
    title, excerpt, headings = extract_page(PAGE_HTML)
    assert title == "Access API — capabilities"
    assert "Access API" in headings
    assert "Declaring capabilities" in headings


def test_extract_handles_pages_with_no_main() -> None:
    html = "<html><head><title>x</title></head><body><p>hello world</p></body></html>"
    title, excerpt = extract_title_and_excerpt(html)
    assert title == "x"
    assert "hello world" in excerpt


def test_format_results_handles_empty() -> None:
    assert "No matches" in format_results([])


def test_format_results_renders_markdown_with_headings() -> None:
    out = format_results([
        DocHit(
            url="https://moodledev.io/x",
            title="X Page",
            excerpt="body",
            score=1.0,
            headings=("Section A", "Section B"),
        )
    ])
    assert "### 1. X Page" in out
    assert "https://moodledev.io/x" in out
    assert "body" in out
    assert "Section A" in out


def test_hits_to_dicts_round_trip() -> None:
    hits = [DocHit(url="u", title="t", excerpt="e", score=1.234567, headings=("h",))]
    data = hits_to_dicts(hits)
    assert data[0]["url"] == "u"
    assert data[0]["headings"] == ["h"]
    assert data[0]["score"] == 1.2346


@pytest.mark.asyncio
@respx.mock
async def test_search_returns_relevant_hits(tmp_cache: DiskCache) -> None:
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

    async with MoodleDocs(cache=tmp_cache) as docs:
        hits = await docs.search("access capability", limit=3)
    assert hits, "expected at least one hit"
    assert hits[0].url.endswith("/access")
    assert hits[0].score > 0


@pytest.mark.asyncio
@respx.mock
async def test_search_with_no_matches_returns_empty(tmp_cache: DiskCache) -> None:
    respx.get("https://moodledev.io/sitemap.xml").mock(
        return_value=httpx.Response(200, text=SITEMAP_XML)
    )
    async with MoodleDocs(cache=tmp_cache) as docs:
        hits = await docs.search("xyzzy-no-such-thing-anywhere", limit=5)
    assert hits == []


@pytest.mark.asyncio
@respx.mock
async def test_synonym_expansion_finds_hooks_via_listener(tmp_cache: DiskCache) -> None:
    respx.get("https://moodledev.io/sitemap.xml").mock(
        return_value=httpx.Response(200, text=SITEMAP_XML)
    )
    respx.get(url__regex=r"https://moodledev\.io/docs/.*").mock(
        return_value=httpx.Response(200, text=PAGE_HTML)
    )
    async with MoodleDocs(cache=tmp_cache) as docs:
        hits = await docs.search("listener", limit=3)
    assert hits
    assert any("hooks" in h.url for h in hits)


@pytest.mark.asyncio
@respx.mock
async def test_sitemap_index_is_recursed(tmp_cache: DiskCache) -> None:
    respx.get("https://moodledev.io/sitemap.xml").mock(
        return_value=httpx.Response(200, text=SITEMAP_INDEX_XML)
    )
    respx.get("https://moodledev.io/sitemap-docs.xml").mock(
        return_value=httpx.Response(200, text=SITEMAP_XML)
    )
    respx.get(url__regex=r"https://moodledev\.io/docs/.*").mock(
        return_value=httpx.Response(200, text=PAGE_HTML)
    )
    async with MoodleDocs(cache=tmp_cache) as docs:
        hits = await docs.search("access", limit=3)
    assert hits
    assert hits[0].url.endswith("/access")


@pytest.mark.asyncio
@respx.mock
async def test_retry_then_success(tmp_cache: DiskCache) -> None:
    route = respx.get("https://moodledev.io/sitemap.xml")
    route.side_effect = [
        httpx.Response(500),
        httpx.Response(500),
        httpx.Response(200, text=SITEMAP_XML),
    ]
    respx.get(url__regex=r"https://moodledev\.io/docs/.*").mock(
        return_value=httpx.Response(200, text=PAGE_HTML)
    )
    async with MoodleDocs(cache=tmp_cache) as docs:
        hits = await docs.search("access", limit=2)
    assert hits


@pytest.mark.asyncio
@respx.mock
async def test_search_pagination_offset(tmp_cache: DiskCache) -> None:
    respx.get("https://moodledev.io/sitemap.xml").mock(
        return_value=httpx.Response(200, text=SITEMAP_XML)
    )
    respx.get(url__regex=r"https://moodledev\.io/docs/.*").mock(
        return_value=httpx.Response(200, text=PAGE_HTML)
    )
    async with MoodleDocs(cache=tmp_cache) as docs:
        page1 = await docs.search("access", limit=1, offset=0)
        page2 = await docs.search("access", limit=1, offset=1)
    if page1 and page2:
        assert page1[0].url != page2[0].url
