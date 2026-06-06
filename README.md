# moodle-mcp

> Model Context Protocol server for Moodle development. Plug into Claude Desktop, Cursor, Continue, Cline, or any MCP-capable assistant — get live `moodledev.io` documentation lookups right in your editor.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![MCP](https://img.shields.io/badge/MCP-compatible-8A2BE2.svg)](https://modelcontextprotocol.io)

Companion to [`claude-moodle-dev`](https://github.com/SaadRahman01/claude-moodle-dev) — that plugin teaches AI assistants the Moodle conventions; this server gives them live access to the canonical docs.

## What you get

Eight MCP tools, hardened to survive site redesigns:

| Tool | Purpose |
|------|---------|
| `search_moodle_docs(query, limit=5, offset=0)` | Search `moodledev.io`. Synonym expansion (`cap`→`capability`, `ws`→`webservice`, `hook`↔`listener`), quoted-phrase boost, BM25 scoring, pagination. |
| `fetch_moodle_page(url)` | Pull a single page in full — body text + headings — for follow-up after a search. |
| `get_hooks_api_listeners()` | Surface the core Hooks API index + detected hook classes. |
| `get_capability_docs(component?)` | Access API lookup with a `RISK_*` quick-reference card; optional component scope. |
| `lookup_db_xmldb(query)` | Focused XMLDB / database schema search. |
| `list_plugin_types()` | Table of every Moodle plugin type with one-line hints + docs URL. |
| `get_version_info()` | Current Moodle versions parsed from `/general/releases`. |
| `search_tracker(query, limit=5)` | Issue search against `tracker.moodle.org` (Jira). |

Plus MCP **resources** (`moodle://docs/apis/...` for one-click page reads) and **prompts** for plugin scaffolding, capability review, and hooks migration.

No API keys. No third-party search service. Pulls the public sitemap, caches it on disk (`~/.cache/moodle-mcp/`), scores against your query, fetches top pages concurrently with retry + conditional GET.

## CLI

```bash
moodle-mcp --version
moodle-mcp --check        # diagnostic: fetch sitemap + sample page, exit
moodle-mcp --log-level DEBUG
```

## Docker

```bash
docker build -t moodle-mcp .
docker run --rm -i moodle-mcp
```

## Install

### From PyPI (once published)

```bash
pipx install moodle-mcp
```

### From source

```bash
git clone https://github.com/SaadRahman01/moodle-mcp
cd moodle-mcp
pipx install .
```

## Wire it up to your assistant

### Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "moodle": {
      "command": "moodle-mcp"
    }
  }
}
```

Restart Claude Desktop. Ask: *"Search the Moodle docs for how to add a capability."*

### Claude Code

```bash
claude mcp add moodle moodle-mcp
```

### Cursor

`~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "moodle": { "command": "moodle-mcp" }
  }
}
```

### Continue.dev

`~/.continue/config.yaml`:

```yaml
mcpServers:
  - name: moodle
    command: moodle-mcp
```

### Cline / Windsurf / any MCP client

Spawn `moodle-mcp` as a stdio MCP server. No extra arguments required.

## How it works

```
   query
     │
     ▼
┌─────────────────────────────────────────┐
│  fetch sitemap.xml  (cached per session)│
└─────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────┐
│  score URLs by slug-token overlap       │
└─────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────┐
│  fetch top N pages → extract main text  │
└─────────────────────────────────────────┘
     │
     ▼
   markdown bundle (title, URL, excerpt)
```

No external search index. No API key. The only network dependency is `https://moodledev.io` being reachable.

## Roadmap

- `lookup_capability(name)` — resolve capability → metadata + risk bitmask
- `lookup_hook(name)` — Hooks API 4.4+ listener signatures
- `validate_version_php(path)` — local lint
- `lookup_db_field(table, column)` — XMLDB metadata
- Algolia DocSearch fast-path (when available)

Vote / request: [open an issue](https://github.com/SaadRahman01/moodle-mcp/issues).

## Development

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check .
```

## Contributing

PRs welcome. Add a new tool by:

1. Implementing the backend in `src/moodle_mcp/<your_module>.py`.
2. Registering the tool in `src/moodle_mcp/server.py`.
3. Adding tests in `tests/`.

## License

MIT.

## Credits

Built by [Saad Rahman](https://github.com/SaadRahman01). Companion of [`claude-moodle-dev`](https://github.com/SaadRahman01/claude-moodle-dev). Powered by the [Model Context Protocol](https://modelcontextprotocol.io) and the [Moodle Developer Documentation](https://moodledev.io).
