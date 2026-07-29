"""GET /api/sync-status -- last successful sync timestamp and auto-sync state."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter

from ynab_tools.db import get_connection

router = APIRouter(tags=["sync-status"])


@router.get("/sync-status")
def sync_status() -> dict[str, Any]:
    """Return the timestamp of the most recent manual sync and auto-sync state."""
    # Deferred import breaks the circular dependency: server imports this router,
    # so a top-level import here would create a cycle. sys.modules cache makes
    # the per-request cost negligible.
    from ynab_tools.dashboard.server import _last_auto_sync_at, _sync_interval_minutes

    conn = get_connection()
    try:
        row = conn.execute("SELECT synced_at FROM sync_log ORDER BY synced_at DESC LIMIT 1").fetchone()
        last_manual = row["synced_at"] if row else None
    except sqlite3.OperationalError:
        last_manual = None
    finally:
        conn.close()

    return {
        "last_synced_at": last_manual,
        "last_auto_sync_at": _last_auto_sync_at,
        "auto_sync_interval_minutes": _sync_interval_minutes,
    }
