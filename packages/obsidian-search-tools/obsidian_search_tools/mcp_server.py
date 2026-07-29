"""FastMCP server for obsidian-search-tools.

Provides three tools:
- vault_search:  hybrid semantic + keyword search over the indexed vault.
- vault_reindex: rebuild the full search index from OBSIDIAN_VAULT_PATH.
- vault_status:  return index metadata (note/chunk counts, last reindex, sections).

Set OBSIDIAN_VAULT_PATH before calling vault_reindex.
Optionally set OBSIDIAN_EXCLUDED_SECTIONS (comma-separated subdir names to skip).
"""

from __future__ import annotations

import dataclasses
import json
import logging
import sys
from dataclasses import asdict

from fastembed import TextEmbedding
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from obsidian_search_tools.config import get_excluded_sections, get_vault_path
from obsidian_search_tools.db import connect, get_db_path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
logger = logging.getLogger(__name__)

mcp = FastMCP(
    "obsidian-search-tools",
    instructions=(
        "Use these tools to search your Obsidian vault. "
        "Set OBSIDIAN_VAULT_PATH before calling vault_reindex. "
        "The first reindex downloads a ~130 MB ONNX model to ~/.cache/fastembed/. "
        "vault_search accepts an optional section= to scope results to one vault section."
    ),
)

# Module-level model cache: loaded once, reused across tool calls within a session.
_model_cache: TextEmbedding | None = None


def _get_model() -> TextEmbedding:
    """Return the FastEmbed TextEmbedding model, loading it on first call."""
    global _model_cache
    if _model_cache is None:
        _model_cache = TextEmbedding("BAAI/bge-small-en-v1.5")
    return _model_cache


# ---------------------------------------------------------------------------
# Tool: vault_search
# ---------------------------------------------------------------------------


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def vault_search(
    query: str,
    section: str | None = None,
    tags: list[str] | None = None,
    limit: int = 10,
) -> str:
    """Search the indexed vault using hybrid semantic + keyword matching (FTS5 BM25 + vec0 KNN).

    Call vault_reindex first. section= scopes results to one top-level vault directory.
    """
    try:
        vault_path = get_vault_path()
        if vault_path is None:
            return json.dumps({"error": "OBSIDIAN_VAULT_PATH is not set. Export it before calling vault_search."})

        from obsidian_search_tools.searcher import search

        model = _get_model()
        conn = connect()
        try:
            chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            if chunk_count == 0:
                return json.dumps({"error": "Index is empty. Call vault_reindex first."})
            results = search(conn, model, query, section=section, tags=tags, limit=limit)
        finally:
            conn.close()

        return json.dumps(
            {
                "status": "ok",
                "count": len(results),
                "query": query,
                "section_filter": section,
                "results": [dataclasses.asdict(r) for r in results],
            }
        )
    except Exception as e:
        logger.exception("vault_search failed")
        return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# Tool: vault_reindex
# ---------------------------------------------------------------------------


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def vault_reindex(force: bool = False) -> str:
    """Rebuild the full search index from OBSIDIAN_VAULT_PATH. The first call downloads a ~130 MB ONNX model."""
    try:
        vault_path = get_vault_path()
        if vault_path is None:
            return json.dumps({"error": ("OBSIDIAN_VAULT_PATH is not set. Export it before calling vault_reindex.")})
        if not vault_path.is_dir():
            return json.dumps({"error": f"OBSIDIAN_VAULT_PATH does not exist or is not a directory: {vault_path}"})

        excluded = get_excluded_sections()

        from obsidian_search_tools.indexer import build_index

        report = build_index(vault_path, excluded, force=force, model=_get_model())
        return json.dumps({"status": "ok", **asdict(report)})
    except Exception as e:
        logger.exception("vault_reindex failed")
        return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# Tool: vault_status
# ---------------------------------------------------------------------------


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def vault_status() -> str:
    """Return index metadata: note count, chunk count, last reindex time, and sections.

    Does not require OBSIDIAN_VAULT_PATH -- reads directly from the index DB.
    Returns zeros / null if the index has never been built.
    """
    try:
        db_path = get_db_path()
        if not db_path.exists():
            return json.dumps(
                {
                    "status": "ok",
                    "note_count": 0,
                    "chunk_count": 0,
                    "last_reindex": None,
                    "model_id": None,
                    "indexed_sections": [],
                    "db_path": str(db_path),
                }
            )

        conn = connect()
        try:
            note_count = conn.execute("SELECT COUNT(DISTINCT path) FROM chunks").fetchone()[0]
            chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]

            def _meta(key: str) -> str | None:
                row = conn.execute("SELECT value FROM index_meta WHERE key = ?", (key,)).fetchone()
                return row[0] if row else None

            last_reindex = _meta("last_reindex")
            model_id = _meta("model_id")
            sections_raw = _meta("indexed_sections")
            indexed_sections = sections_raw.split(",") if sections_raw else []
        finally:
            conn.close()

        return json.dumps(
            {
                "status": "ok",
                "note_count": note_count,
                "chunk_count": chunk_count,
                "last_reindex": last_reindex,
                "model_id": model_id,
                "indexed_sections": indexed_sections,
                "db_path": str(db_path),
            }
        )
    except Exception as e:
        logger.exception("vault_status failed")
        return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the MCP server."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
