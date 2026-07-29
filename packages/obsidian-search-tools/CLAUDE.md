# CLAUDE.md

## Project Overview

Semantic and keyword search over an Obsidian vault via MCP.

## MCP Tools (3)

| Tool | Description |
|------|-------------|
| `vault_search` | Hybrid FTS5 + vec0 KNN search with RRF fusion over indexed vault chunks |
| `vault_reindex` | Full rebuild of the search index from OBSIDIAN_VAULT_PATH |
| `vault_status` | Returns note count, chunk count, last reindex time, model, and sections |

## Configuration (env vars only)

| Env var | Required | Description |
|---------|----------|-------------|
| `OBSIDIAN_VAULT_PATH` | Yes | Absolute path to vault root |
| `OBSIDIAN_EXCLUDED_SECTIONS` | No | Comma-separated top-level subdirs to skip |

No YAML config. No hardcoded paths or section names.

## Key Files

| File | Purpose |
|------|---------|
| `obsidian_search_tools/mcp_server.py` | FastMCP server with 3 tools |
| `obsidian_search_tools/chunker.py` | Token-packed markdown splitter (512 tokens, 64-token overlap) |
| `obsidian_search_tools/indexer.py` | FastEmbed + sqlite-vec + FTS5 index builder |
| `obsidian_search_tools/searcher.py` | Hybrid BM25+KNN search with RRF k=60 fusion |
| `obsidian_search_tools/config.py` | Env var reader |
| `obsidian_search_tools/cli.py` | CLI: configure, reindex, status, db init |
| `obsidian_search_tools/db/schema.py` | Schema: chunks, chunk_tags, index_meta, chunks_fts, chunks_vec |
| `obsidian_search_tools/db/connection.py` | sqlite-vec extension loading + connection factory |

## Architecture Notes

- **Embedding**: FastEmbed `BAAI/bge-small-en-v1.5` (384-dim, ~130MB ONNX download on first use)
- **Query prefix**: `"Represent this sentence for searching relevant passages: "` at query time only (never at index time)
- **Storage**: `~/.local/share/obsidian-search-tools/vault.db` (via `mcp_common.paths.data_dir`)
- **Chunking**: Greedy paragraph packing, 512-token budget. Oversized paragraphs split at sentence boundaries with 64-token overlap.
- **Section filter**: `section TEXT PARTITION KEY` in vec0 (NOT `+section TEXT` which is an auxiliary column and doesn't support WHERE filtering in KNN queries)
- **Search**: FTS5 BM25 top 50 + vec0 KNN top 50, fused via RRF k=60, max 2 chunks per note in results
- **Indexing**: mtime-based incremental by default (`force=False`); only new/changed files are re-embedded. `force=True` does a full rebuild.
- **LaunchAgent**: `com.obsidian-search-tools.reindex` fires every 4 hours; sources `~/.config/obsidian-search-tools/env` for `OBSIDIAN_VAULT_PATH`; logs to `~/.local/share/obsidian-search-tools/reindex.log`

## Common Commands

```bash
# Install for development
cd packages/obsidian-search-tools && uv sync

# Register with Claude
claude mcp add -s user obsidian-search-tools obsidian-search-tools-mcp

# Validate environment
export OBSIDIAN_VAULT_PATH=/path/to/vault
obsidian-search-tools configure

# Build index
obsidian-search-tools reindex

# Check index status
obsidian-search-tools status

# Lint
ruff check .
ruff format .

# Tests
uv run pytest packages/obsidian-search-tools -v
```

## Adding Tools

Add `@mcp.tool()` decorated functions to `mcp_server.py`. All tools must:
- Return `str` (JSON via `json.dumps`)
- Wrap body in `try/except Exception as e` returning `json.dumps({"error": str(e)})`
- Use `X | None` union syntax (not `Optional[X]`)
- Declare `ToolAnnotations` per the workspace convention

## Entry Points

```
obsidian-search-tools      obsidian_search_tools.cli:main
obsidian-search-tools-mcp  obsidian_search_tools.mcp_server:main
```
