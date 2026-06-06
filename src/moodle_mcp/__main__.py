"""CLI entrypoint: `moodle-mcp` runs the stdio MCP server.

Flags:
  --version   print version and exit
  --check     run a sitemap + sample page fetch, print diagnostics, exit
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import sys

from . import __version__
from .docs import MoodleDocs
from .server import run


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="moodle-mcp", description=__doc__)
    p.add_argument("--version", action="version", version=f"moodle-mcp {__version__}")
    p.add_argument(
        "--check",
        action="store_true",
        help="Diagnostic mode — fetch the sitemap and a sample page, print result.",
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO).",
    )
    return p


async def _diagnose() -> int:
    async with MoodleDocs() as docs:
        try:
            urls = await docs._load_sitemap()
        except Exception as e:
            print(f"sitemap fetch FAILED: {e}", file=sys.stderr)
            return 2
        print(f"sitemap OK — {len(urls)} URLs cached")
        if urls:
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


def main() -> None:
    args = _build_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    if args.check:
        sys.exit(asyncio.run(_diagnose()))
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run())


if __name__ == "__main__":
    main()
