# obsidian-search-tools

Hybrid semantic and keyword search over an Obsidian vault via MCP. Notes are chunked, embedded with `BAAI/bge-small-en-v1.5`, and stored in a local SQLite database using FTS5 for BM25 keyword matching and sqlite-vec for vector KNN. Results from both indexes are fused via RRF before being returned.

## Configuration

Configuration is via environment variables only. No config file.

| Env var | Required | Description |
|---------|----------|-------------|
| `OBSIDIAN_VAULT_PATH` | Yes | Absolute path to the vault root directory |
| `OBSIDIAN_EXCLUDED_SECTIONS` | No | Comma-separated top-level subdirectory names to skip during indexing |
| `OBSIDIAN_REINDEX_TIMES` | No | Comma-separated 24-hour `HH:MM` fire times for the scheduled reindex LaunchAgent. Default: `06:00,12:00,18:00` (3x/day). Invalid values fall back to the default. |
| `OBSIDIAN_REINDEX_STALENESS_HOURS` | No | Hours the index must be younger than for `reindex --skip-if-fresh` to skip a rebuild. Default: `2`. |

Export these before calling any CLI command or MCP tool:

```bash
export OBSIDIAN_VAULT_PATH=~/Obsidian/Personal
export OBSIDIAN_EXCLUDED_SECTIONS=Archive,Templates
```

## MCP Tools (3)

| Tool | Description |
|------|-------------|
| `vault_search` | Hybrid FTS5 + vec0 KNN search with RRF fusion; accepts `query`, optional `section`, optional `tags`, optional `limit` |
| `vault_reindex` | Full rebuild of the search index from `OBSIDIAN_VAULT_PATH`; accepts optional `force` flag |
| `vault_status` | Returns note count, chunk count, last reindex time, model ID, sections, and DB path; does not require `OBSIDIAN_VAULT_PATH` |

## Setup

Install for development from the monorepo:

```bash
cd packages/obsidian-search-tools
uv tool install --editable .
```

This installs `obsidian-search-tools` (CLI) and `obsidian-search-tools-mcp` (MCP server).

Register with Claude Code:

```bash
claude mcp add -s user obsidian-search-tools obsidian-search-tools-mcp
```

Or add to Claude Desktop (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "obsidian-search-tools": {
      "command": "/absolute/path/to/obsidian-search-tools-mcp"
    }
  }
}
```

The path must be absolute — Claude Desktop does not inherit your shell `PATH`, so a
bare command name fails to start with no useful error. Find yours with
`which obsidian-search-tools-mcp`, then quit Desktop with **⌘Q** and reopen.

**First use:** `vault_reindex` downloads the `BAAI/bge-small-en-v1.5` ONNX model (~130 MB) to `~/.cache/fastembed/` on first run. Subsequent calls use the cached model.

**Allow the tools in Claude Code:** Claude Code defers MCP tool schemas by default -- tools in the deferred list require an extra lookup step and are unlikely to be used automatically. Add all three tools to `permissions.allow` in `~/.claude/settings.json` so their schemas are pre-loaded every session:

```json
"mcp__plugin_obsidian-search-tools_obsidian-search-tools__vault_search",
"mcp__plugin_obsidian-search-tools_obsidian-search-tools__vault_status",
"mcp__plugin_obsidian-search-tools_obsidian-search-tools__vault_reindex"
```

## CLI

### configure

Validates `OBSIDIAN_VAULT_PATH` and reports which sections will be indexed or skipped. Makes no changes.

```bash
obsidian-search-tools configure
```

Example output:

```
Vault path : /Users/you/Obsidian/Personal
Sections to index : Finance, House, Personal
Excluded sections : Archive, Templates
```

### reindex

Rebuilds the full search index. Run this after installing and whenever vault content changes.

```bash
obsidian-search-tools reindex
obsidian-search-tools reindex --force   # same effect; full rebuild always
```

Example output:

```
Indexing vault at: /Users/you/Obsidian/Personal
Done in 38.4s -- 1204 notes, 8917 chunks, sections: Finance, House, Personal
```

### status

Shows current index state without requiring `OBSIDIAN_VAULT_PATH`.

```bash
obsidian-search-tools status
```

Example output:

```
Note count     : 1204
Chunk count    : 8917
Last reindex   : 2026-06-27T14:32:01
Model          : BAAI/bge-small-en-v1.5
Sections       : Finance, House, Personal
DB path        : /Users/you/.local/share/obsidian-search-tools/vault.db
```

### schedule

Introspect and (indirectly) drive the reindex LaunchAgent's cadence.

```bash
obsidian-search-tools schedule show
```

```
Reindex times      : 06:00, 12:00, 18:00
Staleness threshold: 2.0h
Last reindex       : 2026-08-05T12:00:03+00:00
Currently stale    : False
```

`schedule render <plist>` is the installer's plumbing (called by
`launchagents/render.sh`, not normally run by hand): it fills in the
`StartCalendarInterval` block of an already-`__HOME__`-rendered plist from the
configured `OBSIDIAN_REINDEX_TIMES`.

## Scheduling (reindex LaunchAgent)

The plugin install ships `com.obsidian-search-tools.reindex`, a LaunchAgent
that runs `obsidian-search-tools reindex --skip-if-fresh` on a schedule plus
at login (`RunAtLoad`), so a Mac that sleeps through a scheduled fire still
catches up on wake/login instead of silently drifting stale (macOS drops a
missed plain interval timer; a missed *calendar* fire runs once on next
wake). `--skip-if-fresh` is the safety valve for the resulting overlap: if
`RunAtLoad` and a calendar fire land close together, the second call is a
no-op rather than a second ~130MB-model embedding run.

**Changing the cadence:** set `OBSIDIAN_REINDEX_TIMES` (comma-separated
24-hour `HH:MM`, default `06:00,12:00,18:00`) in
`~/.config/obsidian-search-tools/env`:

```bash
mkdir -p ~/.config/obsidian-search-tools
echo "OBSIDIAN_REINDEX_TIMES=05:00,11:00,17:00,23:00" >> ~/.config/obsidian-search-tools/env
echo "OBSIDIAN_REINDEX_STALENESS_HOURS=3" >> ~/.config/obsidian-search-tools/env
```

The next Claude Code session start re-renders and reloads the LaunchAgent
automatically -- the SessionStart hook re-renders it whenever a hash of that
env file changes, not only when the plugin's dependencies change, so no
manual `launchctl` step is required.

**Upgrade path for installs from before this cadence support (< 0.2.0):**
those installs have `com.obsidian-search-tools.reindex.plist` on disk with
the old `StartInterval`/`RunAtLoad=false` shape. Updating the plugin bumps
the version in `pyproject.toml`, which changes the dependency hash and
triggers the existing venv-rebuild path -- the LaunchAgent is re-rendered and
reloaded as part of that rebuild, with no separate action needed. If you want
to force it sooner (or you're on the dev checkout, not a plugin install), run
`scripts/install_launchagents.sh` from the repo root, or manually:

```bash
launchctl unload ~/Library/LaunchAgents/com.obsidian-search-tools.reindex.plist
# Restart a Claude Code session, or re-run the dev script above, to
# re-render and reload with the new StartCalendarInterval + RunAtLoad shape.
```

## License

MIT
