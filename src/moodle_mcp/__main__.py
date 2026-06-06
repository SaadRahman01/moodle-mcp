"""CLI entrypoint: `moodle-mcp` runs the stdio MCP server."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import sys

from .server import run


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run())


if __name__ == "__main__":
    main()
