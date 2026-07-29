"""Database connection helpers with sqlite-vec extension loading."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import sqlite_vec
from mcp_common.paths import data_dir

from .schema import init_schema


def get_db_path() -> Path:
    """Return the canonical database file path."""
    return data_dir("obsidian-search-tools") / "vault.db"


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a sqlite3 connection with sqlite-vec loaded and schema initialized.

    The sqlite-vec extension is loaded, then immediately disabled per the mandatory
    pattern. Schema initialisation runs on every call (idempotent via IF NOT EXISTS).
    """
    path = db_path or get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    # Load sqlite-vec; re-disable extension loading immediately after.
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn
