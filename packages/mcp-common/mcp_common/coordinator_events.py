"""Emit events onto homeops-coordinator's shared event bus.

homeops and lawnops db-layer functions call `emit_coordinator_event` after a
state change (task done, treatment added, etc.) so the coordinator's polling
loop can react: complete Apple Reminders, suspend irrigation, check budget
balances. This module is intentionally single-purpose, one DB, one schema,
a handful of known callers, not a general pub/sub event-bus abstraction.

Self-healing: creates the data directory and the `events` table on first
use via `CREATE TABLE IF NOT EXISTS`, so it works even if the coordinator's
data dir was wiped or has never existed.

Never raises: a failed event emission must never break the primary
homeops/lawnops action it's attached to. Every failure path is caught,
logged as a warning, and reported via the boolean return value.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3

logger = logging.getLogger(__name__)

# Hardcoded to match coordinator/events.py's existing COORDINATOR_DB constant.
COORDINATOR_DB = os.path.expanduser("~/.local/share/coordinator/events.db")

_EVENTS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source       TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    payload      TEXT NOT NULL,
    created_at   TEXT DEFAULT (datetime('now')),
    processed_at TEXT
);
"""

_EVENTS_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_events_processed ON events (processed_at, created_at);
"""


def emit_coordinator_event(source: str, event_type: str, payload: dict) -> bool:
    """Write one event onto the coordinator's events.db bus.

    Parameters
    ----------
    source:
        Emitting tool identifier, e.g. "homeops" or "lawnops".
    event_type:
        Event name, e.g. "task_done", "treatment_add", "utility_add".
    payload:
        JSON-serializable dict describing the event. Stored as a JSON
        string; the coordinator's rule table parses it back on read.

    Returns
    -------
    bool
        True if the event was written. False on any failure (unwritable
        data dir, locked/corrupt db, serialization error, etc.) -- this
        function never raises, so callers can invoke it fire-and-forget.
    """
    try:
        os.makedirs(os.path.dirname(COORDINATOR_DB), exist_ok=True)
        conn = sqlite3.connect(COORDINATOR_DB)
        try:
            # This write now happens synchronously from user-facing tool
            # calls and could race the daemon's own batched UPDATE.
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute(_EVENTS_TABLE_DDL)
            conn.execute(_EVENTS_INDEX_DDL)
            conn.execute(
                "INSERT INTO events (source, event_type, payload) VALUES (?, ?, ?)",
                (source, event_type, json.dumps(payload)),
            )
            conn.commit()
        finally:
            conn.close()
        return True
    except Exception as e:
        logger.warning("Failed to emit coordinator event %s/%s: %s", source, event_type, e)
        return False
