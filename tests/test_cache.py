"""Tests for the disk cache — corrupt JSON, concurrent writes, clear."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from moodle_mcp.cache import DiskCache


def test_set_and_get_roundtrip(tmp_path: Path) -> None:
    cache = DiskCache(tmp_path)
    cache.set("https://x", "body", etag='"abc"', last_modified="Mon")
    entry = cache.get("https://x")
    assert entry is not None
    assert entry.body == "body"
    assert entry.etag == '"abc"'
    assert entry.last_modified == "Mon"


def test_get_returns_none_for_missing(tmp_path: Path) -> None:
    cache = DiskCache(tmp_path)
    assert cache.get("https://missing") is None


def test_corrupt_json_returns_none(tmp_path: Path) -> None:
    cache = DiskCache(tmp_path)
    path = cache._key_path("https://corrupt")
    path.write_text("{not valid json", encoding="utf-8")
    assert cache.get("https://corrupt") is None


def test_is_fresh_respects_ttl(tmp_path: Path) -> None:
    cache = DiskCache(tmp_path, default_ttl=0.01)
    cache.set("https://x", "b")
    entry = cache.get("https://x")
    assert entry is not None
    assert cache.is_fresh(entry) is True
    time.sleep(0.02)
    entry = cache.get("https://x")
    assert entry is not None
    assert cache.is_fresh(entry) is False


def test_clear_removes_all_entries(tmp_path: Path) -> None:
    cache = DiskCache(tmp_path)
    cache.set("https://a", "x")
    cache.set("https://b", "y")
    assert list(tmp_path.glob("*.json"))
    cache.clear()
    assert list(tmp_path.glob("*.json")) == []


def test_concurrent_writes_dont_corrupt(tmp_path: Path) -> None:
    """Atomic rename should make concurrent sets safe — last write wins."""
    cache = DiskCache(tmp_path)

    async def writer(value: str) -> None:
        cache.set("https://contended", value)

    async def run_concurrent() -> None:
        await asyncio.gather(*(writer(f"v{i}") for i in range(20)))

    asyncio.run(run_concurrent())
    entry = cache.get("https://contended")
    assert entry is not None
    assert entry.body.startswith("v")
    # File must still be parseable JSON.
    raw = cache._key_path("https://contended").read_text("utf-8")
    parsed = json.loads(raw)
    assert parsed["body"] == entry.body


def test_set_then_overwrite(tmp_path: Path) -> None:
    cache = DiskCache(tmp_path)
    cache.set("https://x", "first")
    cache.set("https://x", "second")
    entry = cache.get("https://x")
    assert entry is not None
    assert entry.body == "second"


def test_get_returns_stale_entry_when_expired(tmp_path: Path) -> None:
    """get() returns the entry even past TTL; caller checks is_fresh separately."""
    cache = DiskCache(tmp_path, default_ttl=0.001)
    cache.set("https://x", "body")
    time.sleep(0.01)
    entry = cache.get("https://x")
    assert entry is not None
    assert entry.body == "body"
    assert cache.is_fresh(entry) is False


def test_xdg_cache_home_respected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    from moodle_mcp.cache import default_cache_dir
    d = default_cache_dir()
    assert tmp_path in d.parents or tmp_path == d.parent
