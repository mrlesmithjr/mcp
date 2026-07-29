"""Database connection helpers."""

import os
import sqlite3
from pathlib import Path


def get_db_path(config):
    """Resolve database path from config."""
    db_cfg = config.get("database", {})
    db_path = db_cfg.get("path", "~/.local/share/homeops/homeops.db")
    db_path = os.path.expanduser(db_path)
    if not os.path.isabs(db_path):
        db_path = os.path.join(Path(__file__).parent.parent.parent, db_path)
    return db_path


def get_db(config):
    """Open a SQLite connection, auto-creating the parent dir and schema if absent (idempotent)."""
    from homeops.db.schema import apply_schema

    db_path = get_db_path(config)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    apply_schema(conn)
    return conn
