"""Tests for the dependency-free trigram reranker."""
from __future__ import annotations

from moodle_mcp.rerank import rerank_score


def test_rerank_boosts_relevant_title() -> None:
    base = 1.0
    high = rerank_score(
        "capability access",
        title="Access API — capabilities",
        excerpt="Capabilities are declared in db/access.php",
        headings=("Access API", "Declaring capabilities"),
        url="https://moodledev.io/docs/apis/subsystems/access",
        base_score=base,
    )
    low = rerank_score(
        "capability access",
        title="Plugin types",
        excerpt="Activity modules and blocks.",
        headings=("Plugin types",),
        url="https://moodledev.io/docs/apis/plugintypes/local",
        base_score=base,
    )
    assert high > low


def test_rerank_depth_penalty() -> None:
    deep = "https://moodledev.io/docs/a/b/c/d/e/f"
    shallow = "https://moodledev.io/docs/a"
    s_deep = rerank_score("a", "A page", "body", (), deep, 1.0)
    s_shallow = rerank_score("a", "A page", "body", (), shallow, 1.0)
    assert s_shallow >= s_deep


def test_rerank_handles_empty_query() -> None:
    assert rerank_score("", "t", "e", (), "https://moodledev.io/x", 1.0) == 1.0
