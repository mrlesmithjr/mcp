"""Database schema for obsidian-search-tools.

Standard SQLite tables (chunks, chunk_tags, index_meta, chunks_fts) are created
with CREATE TABLE IF NOT EXISTS. The vec0 virtual table (chunks_vec) requires the
sqlite-vec extension to be loaded first -- handled in connection.py.
"""

from __future__ import annotations

import sqlite3

# BAAI/bge-small-en-v1.5 produces 384-dimensional embeddings.
EMBEDDING_DIM = 384

# Standard SQLite schema (no sqlite-vec extension required).
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS index_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    path         TEXT NOT NULL,
    section      TEXT NOT NULL,
    heading_path TEXT NOT NULL,
    chunk_text   TEXT NOT NULL,
    token_count  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS chunk_tags (
    chunk_id INTEGER NOT NULL REFERENCES chunks(id),
    tag      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS indexed_files (
    path       TEXT PRIMARY KEY,
    mtime      REAL NOT NULL,
    indexed_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_text,
    content='chunks',
    content_rowid='id'
);
"""

# vec0 DDL -- must be executed after sqlite-vec extension is loaded.
VEC0_DDL = f"CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(embedding FLOAT[{EMBEDDING_DIM}], section TEXT PARTITION KEY)"


def init_schema(conn: sqlite3.Connection) -> None:
    """Create all tables. Must be called after sqlite-vec is loaded into conn."""
    conn.executescript(_SCHEMA_SQL)
    conn.execute(VEC0_DDL)
    conn.commit()
