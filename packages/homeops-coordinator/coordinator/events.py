"""Read and acknowledge events from the coordinator event bus."""

import os
import sqlite3

COORDINATOR_DB = os.path.expanduser("~/.local/share/coordinator/events.db")


def get_db():
    """Return a connection to events.db, or None if it doesn't exist yet."""
    if not os.path.exists(COORDINATOR_DB):
        return None
    conn = sqlite3.connect(COORDINATOR_DB)
    conn.row_factory = sqlite3.Row
    return conn


def get_pending_events(limit=50):
    """Return up to limit unprocessed events, oldest first."""
    conn = get_db()
    if conn is None:
        return []
    rows = conn.execute(
        "SELECT * FROM events WHERE processed_at IS NULL ORDER BY created_at LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_processed(event_ids):
    """Mark a list of event IDs as processed."""
    if not event_ids:
        return
    conn = get_db()
    if conn is None:
        return
    placeholders = ",".join("?" * len(event_ids))
    conn.execute(
        f"UPDATE events SET processed_at = datetime('now') WHERE id IN ({placeholders})",
        event_ids,
    )
    conn.commit()
    conn.close()
