"""Moodle docs search backend.

Strategy:
  1. Pull the sitemap (and child sitemaps if it is a sitemap-index) from
     moodledev.io, cache on disk with a 24h TTL.
  2. Score URLs against the query using slug + title overlap with a small
     BM25-style boost, synonym expansion, and phrase-match bonus.
  3. For the top N candidates, fetch pages concurrently and extract a short
     excerpt + headings via regex (no BeautifulSoup dependency).

Designed to stay correct even if moodledev.io changes layout: only requires
the sitemap and basic HTML to remain accessible.
"""
from __future__ import annotations

import asyncio
import math
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

from .cache import DiskCache

SITEMAP_URL = "https://moodledev.io/sitemap.xml"
BASE_URL = "https://moodledev.io"
USER_AGENT = "moodle-mcp/0.2 (+https://github.com/SaadRahman01/moodle-mcp)"
DEFAULT_TIMEOUT = 15.0
EXCERPT_LEN = 320
PAGE_TTL = 7 * 86400.0
SITEMAP_TTL = 86400.0
MAX_CONCURRENT_FETCHES = 6
MAX_RETRIES = 3

SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

_STOP = {
    "the", "a", "an", "and", "or", "for", "of", "to", "in", "on",
    "with", "by", "is", "are", "be", "do", "how", "what", "why",
}

SYNONYMS: dict[str, list[str]] = {
    "cap": ["capability", "capabilities"],
    "caps": ["capability", "capabilities"],
    "ws": ["webservice", "web", "service"],
    "db": ["database", "xmldb"],
    "schema": ["xmldb", "database"],
    "hook": ["hooks", "listener"],
    "hooks": ["hook", "listener"],
    "listener": ["hook", "hooks"],
    "lang": ["language", "string", "strings"],
    "i18n": ["language", "string"],
    "ui": ["output", "renderer", "template"],
    "mustache": ["template", "output"],
    "scss": ["theme", "css"],
    "amd": ["javascript", "js", "module"],
    "ajax": ["webservice", "external"],
    "external": ["webservice", "api"],
    "task": ["adhoc", "scheduled", "cron"],
    "cron": ["task", "scheduled"],
    "event": ["observer", "events"],
    "observer": ["event", "events"],
    "form": ["moodleform", "mform"],
    "settings": ["admin", "config"],
}

PHRASE_RE = re.compile(r'"([^"]+)"')


@dataclass(frozen=True)
class DocHit:
    url: str
    title: str
    excerpt: str
    score: float
    headings: tuple[str, ...] = ()


@dataclass
class PageIndex:
    """Lazily-built corpus of slug + title + heading tokens per URL."""
    by_url: dict[str, list[str]] = field(default_factory=dict)
    df: dict[str, int] = field(default_factory=dict)
    n_docs: int = 0


def _tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9_]+", text.lower()) if t and t not in _STOP]


def _expand_synonyms(tokens: list[str]) -> list[str]:
    out: list[str] = []
    for t in tokens:
        out.append(t)
        out.extend(SYNONYMS.get(t, ()))
    return out


def _extract_phrases(query: str) -> list[str]:
    return [p.strip().lower() for p in PHRASE_RE.findall(query) if p.strip()]


def _slug_tokens(url: str) -> list[str]:
    path = urlparse(url).path
    parts = re.split(r"[/_\-]+", path)
    return [p.lower() for p in parts if p and not p.isdigit()]


def _title_from_slug(url: str) -> str:
    parts = _slug_tokens(url)
    if not parts:
        return url
    return " ".join(p.capitalize() for p in parts[-3:])


def _slug_score(query_tokens: list[str], url: str) -> float:
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
    score += min(len(slug) * 0.05, 0.5)
    return score


def _bm25_score(query_tokens: list[str], doc_tokens: list[str], idx: PageIndex,
                k1: float = 1.5, b: float = 0.75, avgdl: float = 8.0) -> float:
    if not doc_tokens or not query_tokens or idx.n_docs == 0:
        return 0.0
    dl = len(doc_tokens)
    tf: dict[str, int] = {}
    for t in doc_tokens:
        tf[t] = tf.get(t, 0) + 1
    score = 0.0
    for qt in query_tokens:
        df = idx.df.get(qt, 0)
        if df == 0:
            continue
        idf = math.log(1 + (idx.n_docs - df + 0.5) / (df + 0.5))
        f = tf.get(qt, 0)
        if f == 0:
            continue
        denom = f + k1 * (1 - b + b * dl / avgdl)
        score += idf * (f * (k1 + 1)) / denom
    return score


class MoodleDocs:
    """Sitemap-backed search with disk + memory caching, concurrent fetches."""

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        cache: DiskCache | None = None,
    ) -> None:
        self._client = client
        self._owns_client = client is None
        self._cache = cache or DiskCache()
        self._urls: list[str] | None = None
        self._index: PageIndex | None = None
        self._fetch_sem = asyncio.Semaphore(MAX_CONCURRENT_FETCHES)

    async def __aenter__(self) -> MoodleDocs:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, br"},
                timeout=DEFAULT_TIMEOUT,
                follow_redirects=True,
                http2=True,
            )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def aclose(self) -> None:
        await self.__aexit__()

    async def _get(self, url: str, ttl: float | None = None) -> str:
        cached = self._cache.get(url, ttl=ttl)
        if cached is not None and self._cache.is_fresh(cached, ttl=ttl):
            return cached.body
        assert self._client is not None
        headers: dict[str, str] = {}
        if cached is not None:
            if cached.etag:
                headers["If-None-Match"] = cached.etag
            if cached.last_modified:
                headers["If-Modified-Since"] = cached.last_modified
        last_err: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                async with self._fetch_sem:
                    resp = await self._client.get(url, headers=headers)
                if resp.status_code == 304 and cached is not None:
                    self._cache.set(
                        url, cached.body,
                        etag=cached.etag, last_modified=cached.last_modified,
                    )
                    return cached.body
                resp.raise_for_status()
                body = resp.text
                self._cache.set(
                    url, body,
                    etag=resp.headers.get("ETag"),
                    last_modified=resp.headers.get("Last-Modified"),
                )
                return body
            except (httpx.HTTPError, httpx.TimeoutException) as e:
                last_err = e
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(0.5 * (2 ** attempt))
        if cached is not None:
            return cached.body
        raise last_err if last_err else RuntimeError(f"Failed to fetch {url}")

    async def _load_sitemap(self) -> list[str]:
        if self._urls is not None:
            return self._urls
        urls = await self._resolve_sitemap(SITEMAP_URL)
        self._urls = urls
        return urls

    async def _resolve_sitemap(self, sitemap_url: str, depth: int = 0) -> list[str]:
        if depth > 3:
            return []
        body = await self._get(sitemap_url, ttl=SITEMAP_TTL)
        page_urls, child_sitemaps = parse_sitemap(body)
        urls = list(page_urls)
        if child_sitemaps:
            child_results = await asyncio.gather(
                *(self._resolve_sitemap(s, depth + 1) for s in child_sitemaps),
                return_exceptions=True,
            )
            for r in child_results:
                if isinstance(r, list):
                    urls.extend(r)
        seen: set[str] = set()
        deduped: list[str] = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                deduped.append(u)
        return deduped

    def _build_index(self, urls: list[str]) -> PageIndex:
        idx = PageIndex(n_docs=len(urls))
        for u in urls:
            toks = _slug_tokens(u)
            idx.by_url[u] = toks
            seen = set(toks)
            for t in seen:
                idx.df[t] = idx.df.get(t, 0) + 1
        return idx

    def _candidates(self, qtok: list[str], limit: int) -> list[tuple[float, str]]:
        urls = self._urls or []
        if self._index is None:
            self._index = self._build_index(urls)
        idx = self._index
        scored: list[tuple[float, str]] = []
        for u in urls:
            doc_toks = idx.by_url.get(u, _slug_tokens(u))
            s = _slug_score(qtok, u) + 0.5 * _bm25_score(qtok, doc_toks, idx)
            if s > 0:
                scored.append((s, u))
        scored.sort(key=lambda x: -x[0])
        return scored[: max(limit * 3, limit)]

    async def search(
        self,
        query: str,
        limit: int = 5,
        offset: int = 0,
    ) -> list[DocHit]:
        urls = await self._load_sitemap()
        if not urls:
            return []
        base_tokens = _tokenize(query)
        qtok = _expand_synonyms(base_tokens)
        phrases = _extract_phrases(query)
        pool = self._candidates(qtok, limit + offset)
        if offset:
            pool = pool[offset:]
        top = pool[:limit]
        if not top:
            return []
        excerpts = await asyncio.gather(
            *(self._fetch_excerpt(u) for _, u in top),
            return_exceptions=True,
        )
        hits: list[DocHit] = []
        for (score, url), result in zip(top, excerpts, strict=False):
            if isinstance(result, BaseException):
                title, excerpt, headings = "", "", ()
            else:
                title, excerpt, headings = result
            text_lower = (title + " " + excerpt).lower()
            bonus = 0.0
            for p in phrases:
                if p and p in text_lower:
                    bonus += 4.0
            hits.append(DocHit(
                url=url,
                title=title or _title_from_slug(url),
                excerpt=excerpt,
                score=score + bonus,
                headings=headings,
            ))
        hits.sort(key=lambda h: -h.score)
        return hits

    async def fetch_page(self, url: str) -> tuple[str, str, tuple[str, ...]]:
        return await self._fetch_excerpt(url, excerpt_len=2000)

    async def fetch_full(self, url: str) -> str:
        return await self._get(url, ttl=PAGE_TTL)

    async def _fetch_excerpt(
        self, url: str, excerpt_len: int = EXCERPT_LEN,
    ) -> tuple[str, str, tuple[str, ...]]:
        try:
            body = await self._get(url, ttl=PAGE_TTL)
        except (httpx.HTTPError, RuntimeError):
            return "", "", ()
        return extract_page(body, excerpt_len=excerpt_len)


# ---- Pure helpers (no network) — easy to unit-test ----

def parse_sitemap(xml_text: str) -> tuple[list[str], list[str]]:
    """Return (page_urls, child_sitemap_urls) declared in a sitemap.xml document."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return [], []
    pages: list[str] = []
    sitemaps: list[str] = []
    for loc in root.findall("sm:url/sm:loc", SITEMAP_NS):
        if loc.text:
            pages.append(loc.text.strip())
    for loc in root.findall("sm:sitemap/sm:loc", SITEMAP_NS):
        if loc.text:
            sitemaps.append(loc.text.strip())
    return pages, sitemaps


_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_H2_RE = re.compile(r"<h2[^>]*>(.*?)</h2>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_WS_RE = re.compile(r"\s+")
_MAIN_RE = re.compile(r"<main[^>]*>(.*?)</main>", re.IGNORECASE | re.DOTALL)
_ARTICLE_RE = re.compile(r"<article[^>]*>(.*?)</article>", re.IGNORECASE | re.DOTALL)


def _strip(html_fragment: str) -> str:
    text = _SCRIPT_RE.sub(" ", html_fragment)
    text = _TAG_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def extract_page(html: str, excerpt_len: int = EXCERPT_LEN) -> tuple[str, str, tuple[str, ...]]:
    """Pull (title, excerpt, heading-tuple) from a doc page.

    Prefers <main>/<article> region. Heading tuple ordered by appearance,
    H1 first then H2.
    """
    title_m = _TITLE_RE.search(html)
    title = _WS_RE.sub(" ", title_m.group(1)).strip() if title_m else ""
    title = re.sub(r"\s*[|·–-]\s*moodledev\.io.*$", "", title, flags=re.IGNORECASE).strip()

    body = html
    main_m = _MAIN_RE.search(body) or _ARTICLE_RE.search(body)
    if main_m:
        body = main_m.group(1)

    headings: list[str] = []
    for h in _H1_RE.findall(body):
        s = _strip(h)
        if s and s not in headings:
            headings.append(s)
    for h in _H2_RE.findall(body):
        s = _strip(h)
        if s and s not in headings:
            headings.append(s)

    text = _strip(body)
    excerpt = text[:excerpt_len].rstrip()
    if len(text) > excerpt_len:
        excerpt += "…"
    return title, excerpt, tuple(headings[:12])


def extract_title_and_excerpt(html: str) -> tuple[str, str]:
    """Back-compat shim: title + excerpt only."""
    t, e, _ = extract_page(html)
    return t, e


def format_results(hits: Iterable[DocHit]) -> str:
    """Render hits as compact Markdown the model can read directly."""
    items = list(hits)
    if not items:
        return "No matches found on moodledev.io."
    lines = []
    for i, h in enumerate(items, 1):
        lines.append(f"### {i}. {h.title}")
        lines.append(h.url)
        if h.headings:
            lines.append("")
            lines.append("Sections: " + " · ".join(h.headings[:6]))
        if h.excerpt:
            lines.append("")
            lines.append(h.excerpt)
        lines.append("")
    return "\n".join(lines).rstrip()


def hits_to_dicts(hits: Iterable[DocHit]) -> list[dict]:
    return [
        {
            "url": h.url,
            "title": h.title,
            "excerpt": h.excerpt,
            "score": round(h.score, 4),
            "headings": list(h.headings),
        }
        for h in hits
    ]
