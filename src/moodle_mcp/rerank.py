"""Lightweight reranker — character-trigram cosine + lexical signals.

Dependency-free; no embeddings model required. Reranks the small N
candidates returned by the BM25 stage using:

  1. Character-trigram cosine similarity between query and (title + excerpt).
  2. Heading-overlap bonus.
  3. Phrase-match bonus (already applied upstream, retained here).
  4. URL-depth penalty (prefer canonical pages over deep variants).

In practice this catches near-misses BM25 misses — e.g. "create plugin"
vs page titled "Plugin development" — without an ML dependency.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from urllib.parse import urlparse


def _trigrams(text: str) -> Counter[str]:
    s = re.sub(r"\s+", " ", text.lower()).strip()
    if len(s) < 3:
        return Counter([s] if s else [])
    return Counter(s[i : i + 3] for i in range(len(s) - 2))


def _cosine(a: Counter[str], b: Counter[str]) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    if not common:
        return 0.0
    dot = sum(a[k] * b[k] for k in common)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _depth_penalty(url: str) -> float:
    parts = [p for p in urlparse(url).path.split("/") if p]
    return max(0.0, (len(parts) - 4) * 0.05)


def rerank_score(
    query: str,
    title: str,
    excerpt: str,
    headings: tuple[str, ...],
    url: str,
    base_score: float,
) -> float:
    """Combine BM25 base_score with trigram cosine + heading + depth signals.

    Returned score is monotonic in relevance but not directly comparable
    across queries.
    """
    q = _trigrams(query)
    if not q:
        return base_score
    text = (title + " . " + excerpt).strip()
    sim = _cosine(q, _trigrams(text))
    head_text = " ".join(headings)
    head_sim = _cosine(q, _trigrams(head_text)) if head_text else 0.0
    depth_pen = _depth_penalty(url)
    return base_score + 6.0 * sim + 2.0 * head_sim - depth_pen
