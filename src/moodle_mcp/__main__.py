"""CLI entrypoint: `moodle-mcp` runs the stdio MCP server.

Modes:
  (default)        run the stdio MCP server, ready for Claude/Cursor/Continue.
  --check          run a sitemap + sample-page fetch, print diagnostics, exit.
  --clear-cache    delete the on-disk cache (~/.cache/moodle-mcp) and exit.

Environment:
  MOODLE_URL, MOODLE_TOKEN        — enable Moodle Web Services tools
  MOODLE_DOCS_ALGOLIA_APP_ID/_API_KEY/_INDEX — Algolia DocSearch fast-path
  MOODLE_MCP_PAGE_TTL             — page cache TTL in seconds (default 86400)
  MOODLE_MCP_SITEMAP_TTL          — sitemap cache TTL (default 86400)
  MOODLE_MCP_MAX_CONCURRENT       — fetch concurrency (default 6)
  XDG_CACHE_HOME                  — override cache directory base

Examples:
  moodle-mcp --version
  MOODLE_MCP_PAGE_TTL=3600 moodle-mcp
  moodle-mcp --check --log-level DEBUG
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import sys

from . import __version__
from .cache import DiskCache, default_cache_dir
from .docs import MoodleDocs
from .server import run


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="moodle-mcp",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"moodle-mcp {__version__}")
    p.add_argument(
        "--check",
        action="store_true",
        help="Diagnostic mode — fetch the sitemap and a sample page, print result.",
    )
    p.add_argument(
        "--clear-cache",
        action="store_true",
        help="Delete the on-disk cache and exit.",
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO).",
    )
    return p


async def _diagnose() -> int:
    from .docs import SITEMAP_URL, parse_sitemap
    async with MoodleDocs() as docs:
        try:
            urls = await docs._load_sitemap()
        except Exception as e:
            print(f"sitemap fetch FAILED: {e}", file=sys.stderr)
            return 2
        print(f"sitemap OK — {len(urls)} URLs cached")
        if not urls:
            print("WARNING: 0 URLs parsed — dumping raw sitemap.xml diagnostic:",
                  file=sys.stderr)
            try:
                raw = await docs._get(SITEMAP_URL, ttl=0.0)
            except Exception as e:
                print(f"  could not refetch sitemap: {e}", file=sys.stderr)
                return 4
            pages, children = parse_sitemap(raw)
            print(f"  raw bytes: {len(raw)}", file=sys.stderr)
            print(f"  first 400 chars: {raw[:400]!r}", file=sys.stderr)
            print(f"  parsed pages: {len(pages)}  child sitemaps: {len(children)}",
                  file=sys.stderr)
            if children:
                print(f"  child sitemap example: {children[0]}", file=sys.stderr)
            return 4
        sample = urls[0]
        try:
            title, excerpt, headings = await docs._fetch_excerpt(sample)
            print(f"sample fetch OK — {sample}")
            print(f"  title: {title or '(none)'}")
            print(f"  headings: {len(headings)}")
            print(f"  excerpt: {excerpt[:120]}...")
        except Exception as e:
            print(f"sample fetch FAILED: {e}", file=sys.stderr)
            return 3
        return 0


def _clear_cache() -> int:
    cache = DiskCache()
    cache.clear()
    print(f"cleared cache at {default_cache_dir()}")
    return 0


def main() -> None:
    args = _build_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    if args.clear_cache:
        sys.exit(_clear_cache())
    if args.check:
        sys.exit(asyncio.run(_diagnose()))
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run())


if __name__ == "__main__":
    main()
