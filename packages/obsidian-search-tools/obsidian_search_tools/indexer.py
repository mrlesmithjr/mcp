"""Index builder for obsidian-search-tools.

Walks the vault, chunks each markdown file, embeds chunks with FastEmbed, and
stores vectors in sqlite-vec (vec0) + FTS5. Supports both full rebuild (force=True)
and mtime-based incremental indexing (force=False, the default).
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import sqlite_vec
from fastembed import TextEmbedding

from .chunker import Chunk, chunk_file
from .db import connect, get_db_path

logger = logging.getLogger(__name__)

_MODEL_ID = "BAAI/bge-small-en-v1.5"


@dataclass
class IndexReport:
    notes_indexed: int
    notes_unchanged: int
    notes_deleted: int
    chunks_indexed: int
    sections: list[str]
    elapsed_seconds: float
    model_id: str
    db_path: str


def _discover_sections(vault_path: Path, excluded: set[str]) -> list[str]:
    """Return sorted list of top-level subdir names that are not excluded."""
    return sorted(
        d.name for d in vault_path.iterdir() if d.is_dir() and not d.name.startswith(".") and d.name not in excluded
    )


def _collect_md_files(vault_path: Path, sections: list[str]) -> list[Path]:
    """Return all .md files under the given section subdirs.

    Files directly in vault_path (not under a section) are intentionally excluded --
    Obsidian vaults typically have only a root README or index file there.
    """
    files: list[Path] = []
    for section in sections:
        section_dir = vault_path / section
        if section_dir.is_dir():
            files.extend(section_dir.rglob("*.md"))
    return sorted(files)


def _clear_index(conn: sqlite3.Connection) -> None:
    """Wipe all index data for a full rebuild."""
    conn.execute("DELETE FROM indexed_files")
    conn.execute("DELETE FROM chunk_tags")
    conn.execute("DELETE FROM chunks_vec")
    conn.execute("DELETE FROM chunks")
    conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('delete-all')")
    conn.execute("DELETE FROM index_meta")
    conn.commit()


def _delete_file_chunks(conn: sqlite3.Connection, path: str) -> None:
    """Remove all chunks for a given file path, cleaning up all related tables."""
    rows = conn.execute("SELECT id, chunk_text FROM chunks WHERE path = ?", (path,)).fetchall()
    for row in rows:
        chunk_id, chunk_text = row[0], row[1]
        # FTS delete must happen before the chunks row is deleted (needs chunk_text).
        conn.execute(
            "INSERT INTO chunks_fts(chunks_fts, rowid, chunk_text) VALUES ('delete', ?, ?)",
            (chunk_id, chunk_text),
        )
        conn.execute("DELETE FROM chunk_tags WHERE chunk_id = ?", (chunk_id,))
        conn.execute("DELETE FROM chunks_vec WHERE rowid = ?", (chunk_id,))
    conn.execute("DELETE FROM chunks WHERE path = ?", (path,))
    conn.execute("DELETE FROM indexed_files WHERE path = ?", (path,))


def build_index(
    vault_path: Path,
    excluded: set[str],
    db_path: Path | None = None,
    force: bool = False,
    model: TextEmbedding | None = None,
) -> IndexReport:
    """Build the search index from vault_path.

    When force=True, performs a full rebuild. When force=False (the default),
    performs mtime-based incremental indexing: only new or modified files are
    re-embedded; deleted files are removed from the index.

    The optional model parameter allows callers to pass a pre-loaded TextEmbedding
    instance to avoid loading the model twice (e.g. from the MCP server cache).
    """
    t0 = time.monotonic()
    resolved_db_path = db_path or get_db_path()

    sections = _discover_sections(vault_path, excluded)
    if not sections:
        logger.warning("No sections found in %s (excluded: %s)", vault_path, excluded)

    md_files = _collect_md_files(vault_path, sections)
    logger.info("Indexing %d files across sections: %s", len(md_files), sections)

    if force:
        # Full rebuild path (original behavior).
        all_chunks: list[Chunk] = []
        seen_paths: set[str] = set()
        for md_file in md_files:
            file_chunks = chunk_file(md_file, vault_path)
            all_chunks.extend(file_chunks)
            for c in file_chunks:
                seen_paths.add(c.path)

        logger.info("Produced %d chunks from %d notes", len(all_chunks), len(seen_paths))

        _model = model if model is not None else TextEmbedding(_MODEL_ID)
        texts = [c.chunk_text for c in all_chunks]
        embeddings = list(_model.embed(texts)) if texts else []

        conn = connect(resolved_db_path)
        try:
            _clear_index(conn)

            for chunk, emb in zip(all_chunks, embeddings):
                emb_bytes = sqlite_vec.serialize_float32(emb.tolist())
                cursor = conn.execute(
                    "INSERT INTO chunks (path, section, heading_path, chunk_text, token_count) VALUES (?, ?, ?, ?, ?)",
                    (chunk.path, chunk.section, chunk.heading_path, chunk.chunk_text, chunk.token_count),
                )
                chunk_id: int = cursor.lastrowid  # type: ignore[assignment]

                conn.execute(
                    "INSERT INTO chunks_vec (rowid, embedding, section) VALUES (?, ?, ?)",
                    (chunk_id, emb_bytes, chunk.section),
                )

                tags = chunk.frontmatter.get("tags", [])
                if isinstance(tags, str):
                    tags = [tags]
                for tag in tags:
                    conn.execute(
                        "INSERT INTO chunk_tags (chunk_id, tag) VALUES (?, ?)",
                        (chunk_id, str(tag)),
                    )

            # Rebuild FTS index from the chunks content table.
            conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")

            now_str = datetime.now(tz=timezone.utc).isoformat()
            for rel_path in seen_paths:
                mtime = (vault_path / rel_path).stat().st_mtime
                conn.execute(
                    "INSERT OR REPLACE INTO indexed_files (path, mtime, indexed_at) VALUES (?, ?, ?)",
                    (rel_path, mtime, now_str),
                )

            conn.execute("INSERT OR REPLACE INTO index_meta (key, value) VALUES (?, ?)", ("last_reindex", now_str))
            conn.execute("INSERT OR REPLACE INTO index_meta (key, value) VALUES (?, ?)", ("model_id", _MODEL_ID))
            conn.execute(
                "INSERT OR REPLACE INTO index_meta (key, value) VALUES (?, ?)",
                ("indexed_sections", ",".join(sections)),
            )
            conn.commit()
        finally:
            conn.close()

        elapsed = time.monotonic() - t0
        return IndexReport(
            notes_indexed=len(seen_paths),
            notes_unchanged=0,
            notes_deleted=0,
            chunks_indexed=len(all_chunks),
            sections=sections,
            elapsed_seconds=round(elapsed, 2),
            model_id=_MODEL_ID,
            db_path=str(resolved_db_path),
        )

    # Incremental path: detect new, changed, and deleted files via mtime.

    # Step 1: Collect filesystem state.
    fs_files: dict[str, tuple[Path, float]] = {}
    for md_file in md_files:
        rel = str(md_file.relative_to(vault_path))
        fs_files[rel] = (md_file, md_file.stat().st_mtime)

    # Step 2: Load indexed_files from DB.
    conn = connect(resolved_db_path)
    try:
        db_files: dict[str, float] = {
            row[0]: row[1] for row in conn.execute("SELECT path, mtime FROM indexed_files").fetchall()
        }

        # Step 3: Classify files.
        fs_set = set(fs_files)
        db_set = set(db_files)
        deleted = db_set - fs_set
        new_paths = fs_set - db_set
        changed = {p for p in fs_set & db_set if fs_files[p][1] != db_files[p]}
        unchanged = (fs_set & db_set) - changed

        logger.info(
            "Incremental: %d new, %d changed, %d deleted, %d unchanged",
            len(new_paths),
            len(changed),
            len(deleted),
            len(unchanged),
        )

        # Step 4: Delete removed and stale files.
        for path in deleted | changed:
            _delete_file_chunks(conn, path)

        # Step 5: Chunk files to index.
        to_index = sorted(new_paths | changed)
        all_chunks = []
        seen_paths = set()
        for rel_path in to_index:
            file_chunks = chunk_file(fs_files[rel_path][0], vault_path)
            all_chunks.extend(file_chunks)
            seen_paths.add(rel_path)

        # Step 6: Embed and insert (per-row FTS, not full rebuild).
        if all_chunks:
            _model = model if model is not None else TextEmbedding(_MODEL_ID)
            embeddings = list(_model.embed([c.chunk_text for c in all_chunks]))
            for chunk, emb in zip(all_chunks, embeddings):
                emb_bytes = sqlite_vec.serialize_float32(emb.tolist())
                cursor = conn.execute(
                    "INSERT INTO chunks (path, section, heading_path, chunk_text, token_count) VALUES (?, ?, ?, ?, ?)",
                    (chunk.path, chunk.section, chunk.heading_path, chunk.chunk_text, chunk.token_count),
                )
                chunk_id = cursor.lastrowid  # type: ignore[assignment]
                # Per-row FTS insert (not rebuild).
                conn.execute(
                    "INSERT INTO chunks_fts(rowid, chunk_text) VALUES (?, ?)",
                    (chunk_id, chunk.chunk_text),
                )
                conn.execute(
                    "INSERT INTO chunks_vec (rowid, embedding, section) VALUES (?, ?, ?)",
                    (chunk_id, emb_bytes, chunk.section),
                )
                tags = chunk.frontmatter.get("tags", [])
                if isinstance(tags, str):
                    tags = [tags]
                for tag in tags:
                    conn.execute("INSERT INTO chunk_tags (chunk_id, tag) VALUES (?, ?)", (chunk_id, str(tag)))

        # Step 7: Update indexed_files for processed paths.
        now_str = datetime.now(tz=timezone.utc).isoformat()
        for rel_path in to_index:
            conn.execute(
                "INSERT OR REPLACE INTO indexed_files (path, mtime, indexed_at) VALUES (?, ?, ?)",
                (rel_path, fs_files[rel_path][1], now_str),
            )

        # Step 8: Update index_meta.
        conn.execute("INSERT OR REPLACE INTO index_meta (key, value) VALUES (?, ?)", ("last_reindex", now_str))
        conn.execute("INSERT OR REPLACE INTO index_meta (key, value) VALUES (?, ?)", ("model_id", _MODEL_ID))
        conn.execute(
            "INSERT OR REPLACE INTO index_meta (key, value) VALUES (?, ?)",
            ("indexed_sections", ",".join(sections)),
        )
        conn.commit()
    finally:
        conn.close()

    elapsed = time.monotonic() - t0
    return IndexReport(
        notes_indexed=len(seen_paths),
        notes_unchanged=len(unchanged),
        notes_deleted=len(deleted),
        chunks_indexed=len(all_chunks),
        sections=sections,
        elapsed_seconds=round(elapsed, 2),
        model_id=_MODEL_ID,
        db_path=str(resolved_db_path),
    )
