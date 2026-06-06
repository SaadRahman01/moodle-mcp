"""High-level tools that build on top of MoodleDocs.

Each function here corresponds to one MCP tool exposed in server.py. They
return a dict shaped { "markdown": str, "data": Any } so server.py can ship
both human-readable and structured payloads.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from . import ws
from .config import BASE_URL, MAX_QUERY_LEN, RELEASES_URL, TRACKER_SEARCH
from .docs import (
    DocHit,
    MoodleDocs,
    extract_page,
    format_results,
    hits_to_dicts,
)

# ---- search_moodle_docs ----

async def tool_search_docs(
    docs: MoodleDocs,
    query: str,
    limit: int = 5,
    offset: int = 0,
    version: str | None = None,
) -> dict[str, Any]:
    query = (query or "")[:MAX_QUERY_LEN]
    hits = await docs.search(query, limit=limit, offset=offset, version=version)
    return {
        "markdown": format_results(hits),
        "data": {
            "hits": hits_to_dicts(hits),
            "offset": offset,
            "limit": limit,
            "version": version,
        },
    }


# ---- fetch_moodle_page ----

_MD_LINK_RE = re.compile(r"<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)
_CODE_BLOCK_RE = re.compile(r"<pre[^>]*>(.*?)</pre>", re.IGNORECASE | re.DOTALL)


def _normalize_doc_url(url: str) -> tuple[str, str | None]:
    """Return (normalized_url, error_code).

    Rejects non-moodledev.io hosts, non-http(s) schemes, and path traversal.
    """
    if not url:
        return "", "empty-url"
    if not url.startswith("http"):
        url = BASE_URL.rstrip("/") + "/" + url.lstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return url, "bad-scheme"
    if parsed.hostname not in {"moodledev.io", "www.moodledev.io"}:
        return url, "out-of-scope-host"
    # Reject path traversal — Docusaurus URLs are simple, no '..' segments.
    if ".." in parsed.path.split("/"):
        return url, "path-traversal"
    # Drop fragments and reassemble.
    cleaned = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", parsed.query, ""))
    return cleaned, None


async def tool_fetch_page(docs: MoodleDocs, url: str) -> dict[str, Any]:
    normalized, err = _normalize_doc_url(url)
    if err:
        return {
            "markdown": f"Error: {err} ({url}).",
            "data": {"error": err, "url": url},
        }
    try:
        html = await docs.fetch_full(normalized)
    except httpx.HTTPError as e:
        return {
            "markdown": f"Error fetching {normalized}: {e}",
            "data": {"error": str(e), "url": normalized},
        }
    title, excerpt, headings = extract_page(html, excerpt_len=8000)
    md_lines = [f"# {title or normalized}", "", normalized, ""]
    if headings:
        md_lines.append("## Sections")
        for h in headings:
            md_lines.append(f"- {h}")
        md_lines.append("")
    md_lines.append(excerpt)
    return {
        "markdown": "\n".join(md_lines).rstrip(),
        "data": {
            "url": normalized,
            "title": title,
            "headings": list(headings),
            "body": excerpt,
        },
    }


# ---- get_hooks_api_listeners ----

HOOKS_INDEX = f"{BASE_URL}/docs/apis/core/hooks"


async def tool_hooks_listeners(docs: MoodleDocs) -> dict[str, Any]:
    candidates = [
        f"{BASE_URL}/docs/apis/core/hooks",
        f"{BASE_URL}/docs/apis/core/hooks/index",
        f"{BASE_URL}/docs/apis/core/hooks/coreHooks",
    ]
    last_err: Exception | None = None
    html = ""
    for u in candidates:
        try:
            html = await docs.fetch_full(u)
            break
        except httpx.HTTPError as e:
            last_err = e
    if not html:
        urls = await docs._load_sitemap()
        hook_urls = [u for u in urls if "/hooks" in u]
        return {
            "markdown": (
                "Hooks index page not directly reachable. "
                f"Last error: {last_err}. Try one of these:\n\n"
                + "\n".join(f"- {u}" for u in hook_urls[:20])
            ),
            "data": {"index_unavailable": True, "candidate_urls": hook_urls[:20]},
        }
    title, excerpt, headings = extract_page(html, excerpt_len=12000)
    hook_classes = sorted(set(re.findall(r"\\([A-Za-z_][A-Za-z0-9_\\]*hook[A-Za-z0-9_]*)",
                                          excerpt, re.IGNORECASE)))
    md = [f"# {title or 'Moodle Hooks API'}", "", *(
        ["Detected hook classes:", *(f"- `{h}`" for h in hook_classes), ""]
        if hook_classes else []
    ), "## Page excerpt", "", excerpt]
    return {
        "markdown": "\n".join(md).rstrip(),
        "data": {
            "title": title,
            "headings": list(headings),
            "hook_classes": hook_classes,
            "excerpt": excerpt,
        },
    }


# ---- get_capability_docs ----

async def tool_capability_docs(docs: MoodleDocs, component: str | None = None) -> dict[str, Any]:
    query = "access capability"
    if component:
        query = f"{component} access capability"
    hits = await docs.search(query, limit=6)
    md = ["# Capability documentation", ""]
    if component:
        md.append(f"Filtered for component: `{component}`")
        md.append("")
    md.append(format_results(hits))
    md.append("")
    md.append("## Quick reference")
    md.append("- Declare capabilities in `db/access.php`.")
    md.append("- Check via `has_capability($cap, $context)` or `require_capability(...)`.")
    md.append("- Risk bitmask: `RISK_SPAM`, `RISK_PERSONAL`, `RISK_XSS`, `RISK_CONFIG`, `RISK_MANAGERS`, `RISK_DATALOSS`.")
    return {
        "markdown": "\n".join(md),
        "data": {"hits": hits_to_dicts(hits), "component": component},
    }


# ---- lookup_db_xmldb ----

async def tool_xmldb(docs: MoodleDocs, query: str) -> dict[str, Any]:
    q = f"xmldb database {query}"
    hits = await docs.search(q, limit=5)
    md = ["# XMLDB / database lookup", "", f"Query: `{query}`", "", format_results(hits)]
    return {"markdown": "\n".join(md), "data": {"hits": hits_to_dicts(hits)}}


# ---- list_plugin_types ----

PLUGIN_TYPE_HINTS = {
    "mod": "Activity modules (assignments, quizzes, forums).",
    "block": "Sidebar / dashboard blocks.",
    "local": "Catch-all for site-wide customisations.",
    "theme": "Visual themes.",
    "filter": "Text filters applied to content output.",
    "format": "Course formats (topics, weeks, grid, etc.).",
    "auth": "Authentication plugins.",
    "enrol": "Enrolment plugins.",
    "qtype": "Question types for the quiz module.",
    "qbehaviour": "Question behaviour plugins.",
    "qbank": "Question bank plugins.",
    "report": "Site / course reports.",
    "tool": "Admin tools (under admin/tool/...).",
    "repository": "File repository plugins.",
    "portfolio": "Portfolio export plugins.",
    "atto": "Atto editor plugins.",
    "tinymce": "TinyMCE editor plugins (legacy).",
    "editor": "Editor plugins (host).",
    "antivirus": "Antivirus scanners.",
    "availability": "Availability conditions.",
    "calendartype": "Calendar type plugins.",
    "cachestore": "MUC cache stores.",
    "cachelock": "MUC cache locks.",
    "communication": "Communication providers (Matrix, etc.).",
    "contentbank": "Content bank content types.",
    "customfield": "Custom field types.",
    "dataformat": "Data export formats.",
    "fileconverter": "File converters (PDF, etc.).",
    "h5plib": "H5P libraries.",
    "media": "Media players.",
    "message": "Message output plugins.",
    "mlbackend": "ML analytics backends.",
    "mnetservice": "MNet services (legacy).",
    "paygw": "Payment gateways.",
    "plagiarism": "Plagiarism detectors.",
    "profilefield": "User profile field types.",
    "search": "Global search engines.",
    "webservice": "Web-service protocols.",
    "workshopallocation": "Workshop allocators.",
    "workshopeval": "Workshop evaluators.",
    "workshopform": "Workshop grading strategies.",
}


async def tool_plugin_types(docs: MoodleDocs) -> dict[str, Any]:
    urls = await docs._load_sitemap()
    plugin_urls: dict[str, list[str]] = {}
    for u in urls:
        m = re.search(r"/docs/apis/plugintypes/([^/]+)", u)
        if m:
            plugin_urls.setdefault(m.group(1), []).append(u)
    rows = []
    seen = set()
    for ptype in sorted(set(list(PLUGIN_TYPE_HINTS) + list(plugin_urls))):
        seen.add(ptype)
        hint = PLUGIN_TYPE_HINTS.get(ptype, "")
        url = plugin_urls.get(ptype, [None])[0]
        rows.append({"type": ptype, "description": hint, "doc_url": url})
    md = ["# Moodle plugin types", "", "| Type | Description | Docs |", "| --- | --- | --- |"]
    for r in rows:
        link = f"[link]({r['doc_url']})" if r["doc_url"] else "—"
        md.append(f"| `{r['type']}` | {r['description']} | {link} |")
    return {"markdown": "\n".join(md), "data": {"plugin_types": rows}}


# ---- get_version_info ----

_VERSION_RE = re.compile(
    r"Moodle\s+([0-9]+\.[0-9]+(?:\.[0-9]+)?)(?:[^A-Za-z0-9]+(LTS))?",
    re.IGNORECASE,
)


async def tool_version_info(docs: MoodleDocs) -> dict[str, Any]:
    try:
        html = await docs.fetch_full(RELEASES_URL)
    except httpx.HTTPError as e:
        return {
            "markdown": f"Could not reach {RELEASES_URL}: {e}",
            "data": {"error": str(e)},
        }
    _, excerpt, headings = extract_page(html, excerpt_len=8000)
    versions: list[dict[str, Any]] = []
    seen_v: set[str] = set()
    for m in _VERSION_RE.finditer(excerpt):
        v = m.group(1)
        if v in seen_v:
            continue
        seen_v.add(v)
        versions.append({"version": v, "lts": bool(m.group(2))})
        if len(versions) >= 8:
            break
    md = ["# Moodle versions (from moodledev.io/general/releases)", ""]
    if versions:
        md.append("| Version | LTS |")
        md.append("| --- | --- |")
        for v in versions:
            md.append(f"| {v['version']} | {'yes' if v['lts'] else ''} |")
    else:
        md.append("No version strings parsed — open the page directly: " + RELEASES_URL)
    return {"markdown": "\n".join(md), "data": {"versions": versions, "source": RELEASES_URL}}


# ---- search_tracker ----

async def tool_search_tracker(
    docs: MoodleDocs,
    query: str,
    limit: int = 5,
    affects_version: str | None = None,
    fix_version: str | None = None,
) -> dict[str, Any]:
    assert docs._client is not None
    query = (query or "")[:MAX_QUERY_LEN]
    jql_parts = [f'text ~ "{query}"']
    if affects_version:
        jql_parts.append(f'affectedVersion = "{affects_version}"')
    if fix_version:
        jql_parts.append(f'fixVersion = "{fix_version}"')
    params = {
        "jql": " AND ".join(jql_parts) + " ORDER BY updated DESC",
        "fields": "summary,status,resolution,components,updated,versions,fixVersions",
        "maxResults": str(min(max(limit, 1), 25)),
    }
    try:
        resp = await docs._client.get(TRACKER_SEARCH, params=params)
        resp.raise_for_status()
        payload = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        return {
            "markdown": f"Tracker search failed: {e}",
            "data": {"error": str(e)},
        }
    issues = payload.get("issues", [])
    rows: list[dict[str, Any]] = []
    for issue in issues:
        f = issue.get("fields", {})
        rows.append({
            "key": issue.get("key"),
            "url": f"https://tracker.moodle.org/browse/{issue.get('key')}",
            "summary": f.get("summary"),
            "status": (f.get("status") or {}).get("name"),
            "resolution": (f.get("resolution") or {}).get("name"),
            "updated": f.get("updated"),
            "affects_versions": [v.get("name") for v in (f.get("versions") or [])],
            "fix_versions": [v.get("name") for v in (f.get("fixVersions") or [])],
        })
    if not rows:
        return {
            "markdown": f"No tracker results for `{query}`.",
            "data": {"issues": [], "truncated": False},
        }
    md = [f"# tracker.moodle.org results for `{query}`", ""]
    for r in rows:
        md.append(f"### {r['key']} — {r['summary']}")
        md.append(r["url"])
        md.append(
            f"Status: {r['status']} · Resolution: {r['resolution'] or '—'} · "
            f"Updated: {r['updated']}"
        )
        if r["fix_versions"]:
            md.append(f"Fix versions: {', '.join(r['fix_versions'])}")
        md.append("")
    truncated = len(rows) >= int(params["maxResults"])
    return {
        "markdown": "\n".join(md).rstrip(),
        "data": {"issues": rows, "truncated": truncated},
    }


# ---- Moodle Web Services tools (opt-in via MOODLE_URL + MOODLE_TOKEN) ----

async def tool_list_ws_functions(docs: MoodleDocs) -> dict[str, Any]:
    if not ws.configured():
        return {
            "markdown": (
                "Moodle WS not configured. Set `MOODLE_URL` and `MOODLE_TOKEN` "
                "environment variables to enable."
            ),
            "data": {"error": "not-configured"},
        }
    assert docs._client is not None
    try:
        functions = await ws.list_functions(docs._client)
    except (ws.WSConfigError, ws.WSCallError) as e:
        return {
            "markdown": f"WS error: {e}",
            "data": {"error": str(e)},
        }
    if not functions:
        return {
            "markdown": "No WS functions returned for this token.",
            "data": {"functions": []},
        }
    md = ["# Moodle WS functions available to this token", ""]
    md.append(f"Total: **{len(functions)}**")
    md.append("")
    md.append("| Function | Version |")
    md.append("| --- | --- |")
    for fn in functions[:200]:
        name = fn.get("name") or "?"
        version = fn.get("version") or ""
        md.append(f"| `{name}` | {version} |")
    if len(functions) > 200:
        md.append(f"\n_(showing first 200 of {len(functions)})_")
    return {"markdown": "\n".join(md), "data": {"functions": functions}}


async def tool_call_ws_function(
    docs: MoodleDocs, function: str, args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not ws.configured():
        return {
            "markdown": (
                "Moodle WS not configured. Set `MOODLE_URL` and `MOODLE_TOKEN` "
                "environment variables to enable."
            ),
            "data": {"error": "not-configured"},
        }
    assert docs._client is not None
    try:
        payload = await ws.call(docs._client, function, args or {})
    except (ws.WSConfigError, ws.WSCallError) as e:
        return {
            "markdown": f"WS call `{function}` failed: {e}",
            "data": {"error": str(e), "function": function},
        }
    summary = _summarize_ws_payload(payload)
    return {
        "markdown": f"# `{function}` result\n\n{summary}",
        "data": {"function": function, "result": payload},
    }


def _summarize_ws_payload(payload: Any) -> str:
    if payload is None:
        return "_null_"
    if isinstance(payload, list):
        return f"List of {len(payload)} item(s)."
    if isinstance(payload, dict):
        keys = ", ".join(f"`{k}`" for k in list(payload.keys())[:12])
        return f"Object with keys: {keys}"
    return f"Scalar: `{payload}`"


__all__ = [
    "DocHit",
    "tool_search_docs",
    "tool_fetch_page",
    "tool_hooks_listeners",
    "tool_capability_docs",
    "tool_xmldb",
    "tool_plugin_types",
    "tool_version_info",
    "tool_search_tracker",
    "tool_list_ws_functions",
    "tool_call_ws_function",
]
