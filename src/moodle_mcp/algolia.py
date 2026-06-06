"""Optional Algolia DocSearch fast-path for moodledev.io.

moodledev.io is built on Docusaurus, which ships an Algolia DocSearch
index. When credentials are provided via env vars, this client queries
Algolia directly instead of the BM25-over-sitemap fallback — much
faster and higher-precision for natural-language queries.

Configured via:
  MOODLE_DOCS_ALGOLIA_APP_ID
  MOODLE_DOCS_ALGOLIA_API_KEY  (Algolia public search key)
  MOODLE_DOCS_ALGOLIA_INDEX    (default: "moodledev")

If any are missing, `available()` returns False and the caller falls
back to sitemap search.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from .config import ALGOLIA_API_KEY, ALGOLIA_APP_ID, ALGOLIA_INDEX


@dataclass(frozen=True)
class AlgoliaHit:
    url: str
    title: str
    excerpt: str
    score: float
    headings: tuple[str, ...]


def available() -> bool:
    return bool(ALGOLIA_APP_ID and ALGOLIA_API_KEY)


def _endpoint() -> str:
    return f"https://{ALGOLIA_APP_ID}-dsn.algolia.net/1/indexes/{ALGOLIA_INDEX}/query"


def _headings_from_hit(hit: dict[str, Any]) -> tuple[str, ...]:
    h = hit.get("hierarchy") or {}
    out: list[str] = []
    for k in ("lvl1", "lvl2", "lvl3"):
        v = h.get(k)
        if v and v not in out:
            out.append(str(v))
    return tuple(out)


def _title_from_hit(hit: dict[str, Any]) -> str:
    h = hit.get("hierarchy") or {}
    # DocSearch convention: lvl0 = section/category, lvl1 = page title.
    for k in ("lvl1", "lvl2", "lvl0"):
        v = h.get(k)
        if v:
            return str(v)
    return hit.get("title") or ""


def _excerpt_from_hit(hit: dict[str, Any]) -> str:
    if hit.get("content"):
        return str(hit["content"])[:600]
    sn = (hit.get("_snippetResult") or {}).get("content") or {}
    if isinstance(sn, dict) and sn.get("value"):
        return str(sn["value"])[:600]
    return ""


async def search(
    client: httpx.AsyncClient,
    query: str,
    limit: int = 5,
    facet_filters: list[str] | None = None,
) -> list[AlgoliaHit]:
    if not available():
        return []
    headers = {
        "X-Algolia-Application-Id": ALGOLIA_APP_ID,
        "X-Algolia-API-Key": ALGOLIA_API_KEY,
        "Content-Type": "application/json",
    }
    body: dict[str, Any] = {
        "query": query,
        "hitsPerPage": max(1, min(limit, 20)),
        "attributesToRetrieve": ["url", "content", "hierarchy", "type"],
        "attributesToSnippet": ["content:30"],
    }
    if facet_filters:
        body["facetFilters"] = facet_filters
    try:
        resp = await client.post(_endpoint(), headers=headers, json=body)
        resp.raise_for_status()
        payload = resp.json()
    except (httpx.HTTPError, ValueError):
        return []
    hits = payload.get("hits", [])
    out: list[AlgoliaHit] = []
    for i, h in enumerate(hits):
        url = h.get("url") or ""
        if not url:
            continue
        out.append(AlgoliaHit(
            url=url,
            title=_title_from_hit(h),
            excerpt=_excerpt_from_hit(h),
            score=float(len(hits) - i),
            headings=_headings_from_hit(h),
        ))
    return out
