"""Tests for Retry-After parsing + version filter + cache resilience."""
from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest
import respx

from moodle_mcp.cache import DiskCache
from moodle_mcp.docs import MoodleDocs, _matches_version, _parse_retry_after, _url_version


def test_parse_retry_after_seconds() -> None:
    assert _parse_retry_after("5") == 5.0


def test_parse_retry_after_http_date() -> None:
    future = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(time.time() + 10))
    delay = _parse_retry_after(future)
    assert delay is not None
    assert 0 < delay <= 11


def test_parse_retry_after_none() -> None:
    assert _parse_retry_after(None) is None
    assert _parse_retry_after("garbage") is None


def test_url_version_extracts() -> None:
    assert _url_version("https://moodledev.io/docs/4.4/apis/x") == "4.4"
    assert _url_version("https://moodledev.io/docs/apis/x") is None


def test_matches_version_versionless_passes() -> None:
    assert _matches_version("https://moodledev.io/docs/apis/x", "4.4") is True


def test_matches_version_mismatch_excludes() -> None:
    assert _matches_version("https://moodledev.io/docs/4.3/x", "4.4") is False
    assert _matches_version("https://moodledev.io/docs/4.4/x", "4.4") is True


@pytest.mark.asyncio
@respx.mock
async def test_429_with_retry_after_respected(tmp_path: Path) -> None:
    cache = DiskCache(tmp_path / "c", default_ttl=60.0)
    sitemap = """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://moodledev.io/docs/x</loc></url>
</urlset>"""
    route = respx.get("https://moodledev.io/sitemap.xml")
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "0"}),
        httpx.Response(200, text=sitemap),
    ]
    async with MoodleDocs(cache=cache) as docs:
        urls = await docs._load_sitemap()
    assert urls == ["https://moodledev.io/docs/x"]


@pytest.mark.asyncio
@respx.mock
async def test_stale_cache_served_when_fetch_fails(tmp_path: Path) -> None:
    cache = DiskCache(tmp_path / "c", default_ttl=0.001)
    cache.set(
        "https://moodledev.io/docs/x",
        "<html><title>stale</title><body><main>old body</main></body></html>",
    )
    time.sleep(0.01)
    respx.get("https://moodledev.io/docs/x").mock(return_value=httpx.Response(500))
    async with MoodleDocs(cache=cache) as docs:
        body = await docs._get("https://moodledev.io/docs/x", ttl=0.001)
    assert "stale" in body
