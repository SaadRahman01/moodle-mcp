"""Tests for the optional Algolia DocSearch client."""
from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
import respx

from moodle_mcp import algolia


def test_available_false_without_credentials() -> None:
    with patch.object(algolia, "ALGOLIA_APP_ID", ""), \
         patch.object(algolia, "ALGOLIA_API_KEY", ""):
        assert algolia.available() is False


def test_available_true_with_credentials() -> None:
    with patch.object(algolia, "ALGOLIA_APP_ID", "APP"), \
         patch.object(algolia, "ALGOLIA_API_KEY", "KEY"):
        assert algolia.available() is True


@pytest.mark.asyncio
async def test_search_returns_empty_without_credentials() -> None:
    with patch.object(algolia, "ALGOLIA_APP_ID", ""), \
         patch.object(algolia, "ALGOLIA_API_KEY", ""):
        async with httpx.AsyncClient() as client:
            hits = await algolia.search(client, "anything", limit=5)
        assert hits == []


@pytest.mark.asyncio
@respx.mock
async def test_search_parses_hits() -> None:
    with patch.object(algolia, "ALGOLIA_APP_ID", "app"), \
         patch.object(algolia, "ALGOLIA_API_KEY", "KEY"), \
         patch.object(algolia, "ALGOLIA_INDEX", "moodledev"):
        respx.post("https://app-dsn.algolia.net/1/indexes/moodledev/query").mock(
            return_value=httpx.Response(200, json={
                "hits": [
                    {
                        "url": "https://moodledev.io/docs/apis/subsystems/access",
                        "hierarchy": {
                            "lvl0": "Docs",
                            "lvl1": "Access API",
                            "lvl2": "Capabilities",
                        },
                        "content": "Capabilities are declared in db/access.php",
                    },
                    {
                        "url": "https://moodledev.io/docs/apis/core/hooks",
                        "hierarchy": {"lvl1": "Hooks API"},
                        "content": "Hooks dispatched by core",
                    },
                ],
            })
        )
        async with httpx.AsyncClient() as client:
            hits = await algolia.search(client, "capability", limit=5)
    assert len(hits) == 2
    assert hits[0].url.endswith("/access")
    assert "Access API" in hits[0].title
    assert hits[0].score > hits[1].score


@pytest.mark.asyncio
@respx.mock
async def test_search_handles_http_error() -> None:
    with patch.object(algolia, "ALGOLIA_APP_ID", "app"), \
         patch.object(algolia, "ALGOLIA_API_KEY", "KEY"):
        respx.post(url__regex=r"https://app-dsn\.algolia\.net/.*").mock(
            return_value=httpx.Response(500)
        )
        async with httpx.AsyncClient() as client:
            hits = await algolia.search(client, "x", limit=5)
    assert hits == []
