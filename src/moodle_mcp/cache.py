"""Disk cache for sitemap + fetched pages.

Survives MCP server restart — sitemap fetch ~200ms, doc pages ~150ms each.
TTL-based invalidation, atomic writes, safe to share across processes.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


def default_cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "moodle-mcp"


@dataclass(frozen=True)
class CacheEntry:
    body: str
    fetched_at: float
    etag: str | None
    last_modified: str | None


class DiskCache:
    def __init__(self, root: Path | None = None, default_ttl: float = 86400.0) -> None:
        self.root = root or default_cache_dir()
        self.default_ttl = default_ttl
        self.root.mkdir(parents=True, exist_ok=True)

    def _key_path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.root / f"{digest}.json"

    def get(self, key: str, ttl: float | None = None) -> CacheEntry | None:
        path = self._key_path(key)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        fetched_at = float(data.get("fetched_at", 0))
        max_age = ttl if ttl is not None else self.default_ttl
        if time.time() - fetched_at > max_age:
            return CacheEntry(
                body=data.get("body", ""),
                fetched_at=fetched_at,
                etag=data.get("etag"),
                last_modified=data.get("last_modified"),
            )
        return CacheEntry(
            body=data.get("body", ""),
            fetched_at=fetched_at,
            etag=data.get("etag"),
            last_modified=data.get("last_modified"),
        )

    def is_fresh(self, entry: CacheEntry, ttl: float | None = None) -> bool:
        max_age = ttl if ttl is not None else self.default_ttl
        return (time.time() - entry.fetched_at) <= max_age

    def set(
        self,
        key: str,
        body: str,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> None:
        path = self._key_path(key)
        payload = {
            "key": key,
            "body": body,
            "fetched_at": time.time(),
            "etag": etag,
            "last_modified": last_modified,
        }
        fd, tmp_name = tempfile.mkstemp(dir=str(self.root), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def clear(self) -> None:
        for p in self.root.glob("*.json"):
            try:
                p.unlink()
            except OSError:
                pass
