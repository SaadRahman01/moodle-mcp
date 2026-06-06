"""Moodle docs search backend.

Strategy:
  1. Pull the sitemap from moodledev.io once per process (cached in memory).
  2. Score URLs against the query using term overlap + slug heuristics — no
     external search service required, no API key, durable against site
     redesigns.
  3. For the top N candidates, fetch the page and extract a short excerpt
     using simple HTML tag stripping (no BeautifulSoup dependency).

Designed to stay correct even if moodledev.io changes layout: only requires
the sitemap and basic HTML to remain accessible.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

SITEMAP_URL = "https://moodledev.io/sitemap.xml"
USER_AGENT = "moodle-mcp/0.1 (+https://github.com/SaadRahman01/moodle-mcp)"
DEFAULT_TIMEOUT = 10.0
EXCERPT_LEN = 320

# Namespace for sitemap XML.
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

_STOP = {
    "the", "a", "an", "and", "or", "for", "of", "to", "in", "on",
    "with", "by", "is", "are", "be", "do", "how", "what", "why",
}


@dataclass(frozen=True)
class DocHit:
    url: str
    title: str
    excerpt: str
    score: float


def _tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9_]+", text.lower()) if t and t not in _STOP]


def _slug_tokens(url: str) -> list[str]:
    path = urlparse(url).path
    parts = re.split(r"[/_\-]+", path)
    return [p.lower() for p in parts if p and not p.isdigit()]


def _title_from_slug(url: str) -> str:
    parts = _slug_tokens(url)
    if not parts:
        return url
    return " ".join(p.capitalize() for p in parts[-3:])


def _score(query_tokens: list[str], url: str) -> float:
    if not query_tokens:
        return 0.0
    slug = _slug_tokens(url)
    if not slug:
        return 0.0
    score = 0.0
    for qt in query_tokens:
        if qt in slug:
            score += 3.0
        else:
            for st in slug:
                if qt in st:
                    score += 1.0
                    break
    if score == 0.0:
        return 0.0
    # Tiny depth tiebreaker — prefer deeper, more-specific paths.
    score += min(len(slug) * 0.05, 0.5)
    return score


class MoodleDocs:
    """Sitemap-backed search with in-memory caching."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._owns_client = client is None
        self._urls: list[str] | None = None

    async def __aenter__(self) -> MoodleDocs:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={"User-Agent": USER_AGENT},
                timeout=DEFAULT_TIMEOUT,
                follow_redirects=True,
            )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def _load_sitemap(self) -> list[str]:
        if self._urls is not None:
            return self._urls
        assert self._client is not None
        resp = await self._client.get(SITEMAP_URL)
        resp.raise_for_status()
        self._urls = parse_sitemap(resp.text)
        return self._urls

    async def search(self, query: str, limit: int = 5) -> list[DocHit]:
        urls = await self._load_sitemap()
        qtok = _tokenize(query)
        scored = ((_score(qtok, u), u) for u in urls)
        ranked = sorted((s for s in scored if s[0] > 0), key=lambda x: -x[0])[:limit]
        hits: list[DocHit] = []
        for score, url in ranked:
            title, excerpt = await self._fetch_excerpt(url)
            hits.append(DocHit(url=url, title=title or _title_from_slug(url),
                               excerpt=excerpt, score=score))
        return hits

    async def _fetch_excerpt(self, url: str) -> tuple[str, str]:
        assert self._client is not None
        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError:
            return "", ""
        return extract_title_and_excerpt(resp.text)


# ---- Pure helpers (no network) — easy to unit-test ----

def parse_sitemap(xml_text: str) -> list[str]:
    """Return the list of URLs declared in a sitemap.xml document."""
    root = ET.fromstring(xml_text)
    urls: list[str] = []
    for loc in root.findall("sm:url/sm:loc", SITEMAP_NS):
        if loc.text:
            urls.append(loc.text.strip())
    # Also support sitemap-index files (rare on docs sites but cheap to handle).
    for loc in root.findall("sm:sitemap/sm:loc", SITEMAP_NS):
        if loc.text:
            urls.append(loc.text.strip())
    return urls


_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_WS_RE = re.compile(r"\s+")
_MAIN_RE = re.compile(r"<main[^>]*>(.*?)</main>", re.IGNORECASE | re.DOTALL)
_ARTICLE_RE = re.compile(r"<article[^>]*>(.*?)</article>", re.IGNORECASE | re.DOTALL)


def extract_title_and_excerpt(html: str) -> tuple[str, str]:
    """Pull the <title> and a short text excerpt from a doc page.

    Prefers the <main>/<article> region to skip nav/footer chrome.
    """
    title_m = _TITLE_RE.search(html)
    title = _WS_RE.sub(" ", title_m.group(1)).strip() if title_m else ""
    # Strip " | moodledev.io" suffix, etc.
    title = re.sub(r"\s*[|·–-]\s*moodledev\.io.*$", "", title, flags=re.IGNORECASE).strip()

    body = html
    main_m = _MAIN_RE.search(body) or _ARTICLE_RE.search(body)
    if main_m:
        body = main_m.group(1)
    body = _SCRIPT_RE.sub(" ", body)
    text = _WS_RE.sub(" ", _TAG_RE.sub(" ", body)).strip()
    excerpt = text[:EXCERPT_LEN].rstrip()
    if len(text) > EXCERPT_LEN:
        excerpt += "…"
    return title, excerpt


def format_results(hits: Iterable[DocHit]) -> str:
    """Render hits as compact Markdown the model can read directly."""
    items = list(hits)
    if not items:
        return "No matches found on moodledev.io."
    lines = []
    for i, h in enumerate(items, 1):
        lines.append(f"### {i}. {h.title}")
        lines.append(h.url)
        if h.excerpt:
            lines.append("")
            lines.append(h.excerpt)
        lines.append("")
    return "\n".join(lines).rstrip()
