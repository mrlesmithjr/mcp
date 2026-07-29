"""Database connection helpers."""

import os
import sqlite3
from pathlib import Path

from .schema import init_db

_DEFAULT_DB_DIR = Path.home() / ".local" / "share" / "lawnops"
_DEFAULT_DB_PATH = _DEFAULT_DB_DIR / "lawnops.db"


def get_db_path(config: dict | None = None) -> Path:
    """Resolve database path from config or the default location."""
    if config:
        db_cfg = config.get("database", {})
        db_path_str = db_cfg.get("path", "")
        if db_path_str:
            return Path(os.path.expanduser(db_path_str))
    return _DEFAULT_DB_PATH


def connect(config: dict | None = None) -> sqlite3.Connection:
    """Open a sqlite3 connection, auto-creating the schema on every call (idempotent)."""
    db_path = get_db_path(config)
    os.makedirs(db_path.parent, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


# Alias kept so existing callers that import get_db continue to work.
get_db = connect
