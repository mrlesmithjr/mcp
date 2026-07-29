"""Tests for the database connection and schema layer."""

from __future__ import annotations


def test_connect_creates_schema(tmp_path):
    """A fresh connection must auto-create all expected tables."""
    from obsidian_search_tools.db import connect

    db_path = tmp_path / "test.db"
    conn = connect(db_path)
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'shadow')").fetchall()
        }
        # FTS5 shadow tables appear; check chunks_fts virtual table exists.
        all_names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'shadow', 'trigger')"
            ).fetchall()
        }
        fts_tables = {n for n in all_names if "chunks_fts" in n}
        # vec0 virtual table.
        vec_tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE name LIKE 'chunks_vec%'").fetchall()
        }
    finally:
        conn.close()

    assert "chunks" in tables
    assert "chunk_tags" in tables
    assert "index_meta" in tables
    assert fts_tables, "FTS5 table chunks_fts should exist"
    assert vec_tables, "vec0 table chunks_vec should exist"


def test_connect_idempotent(tmp_path):
    """Calling connect() multiple times must not raise or corrupt the schema."""
    from obsidian_search_tools.db import connect

    db_path = tmp_path / "test.db"
    for _ in range(3):
        conn = connect(db_path)
        conn.close()


def test_chunks_table_structure(tmp_path):
    """chunks table must have required columns."""
    from obsidian_search_tools.db import connect

    conn = connect(tmp_path / "test.db")
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(chunks)").fetchall()}
    finally:
        conn.close()
    assert "id" in cols
    assert "path" in cols
    assert "section" in cols
    assert "heading_path" in cols
    assert "chunk_text" in cols
    assert "token_count" in cols


def test_index_meta_table_structure(tmp_path):
    from obsidian_search_tools.db import connect

    conn = connect(tmp_path / "test.db")
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(index_meta)").fetchall()}
    finally:
        conn.close()
    assert "key" in cols
    assert "value" in cols


def test_get_db_path_returns_path():
    from obsidian_search_tools.db import get_db_path

    path = get_db_path()
    assert path.name == "vault.db"
    assert "obsidian-search-tools" in str(path)


def test_indexed_files_table_structure(tmp_path):
    from obsidian_search_tools.db import connect

    conn = connect(tmp_path / "test.db")
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(indexed_files)").fetchall()}
    finally:
        conn.close()
    assert "path" in cols
    assert "mtime" in cols
    assert "indexed_at" in cols
