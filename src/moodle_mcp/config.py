"""Centralized URLs, constants, and environment-variable config.

Keeps URL strings in one place so changes to upstream hosts don't ripple
through the codebase. Reads optional environment variables for Moodle
Web Services integration and Algolia DocSearch.
"""
from __future__ import annotations

import os

BASE_URL = "https://moodledev.io"
SITEMAP_URL = f"{BASE_URL}/sitemap.xml"
RELEASES_URL = f"{BASE_URL}/general/releases"
TRACKER_BASE = "https://tracker.moodle.org"
TRACKER_SEARCH = f"{TRACKER_BASE}/rest/api/2/search"
USER_AGENT = "moodle-mcp/0.3 (+https://github.com/SaadRahman01/moodle-mcp)"

DEFAULT_TIMEOUT = 15.0
EXCERPT_LEN = 320
PAGE_TTL = 7 * 86400.0
SITEMAP_TTL = 86400.0
MAX_CONCURRENT_FETCHES = 6
MAX_RETRIES = 3
MAX_QUERY_LEN = 512

# Moodle instance WS settings — opt-in via env.
MOODLE_URL = os.environ.get("MOODLE_URL", "").rstrip("/")
MOODLE_TOKEN = os.environ.get("MOODLE_TOKEN", "")
MOODLE_WS_ALLOW_INSECURE = os.environ.get("MOODLE_WS_ALLOW_INSECURE", "").lower() in {
    "1", "true", "yes",
}

# Algolia DocSearch fast-path (optional). Falls back to sitemap BM25 if unset.
ALGOLIA_APP_ID = os.environ.get("MOODLE_DOCS_ALGOLIA_APP_ID", "")
ALGOLIA_API_KEY = os.environ.get("MOODLE_DOCS_ALGOLIA_API_KEY", "")
ALGOLIA_INDEX = os.environ.get("MOODLE_DOCS_ALGOLIA_INDEX", "moodledev")
