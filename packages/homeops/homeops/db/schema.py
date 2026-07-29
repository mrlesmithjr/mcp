"""Database schema definition and initialization."""

import os
import sqlite3

from homeops.db.connection import get_db_path

SCHEMA_VERSION = 2

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_info (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    category        TEXT NOT NULL,
    interval_days   INTEGER NOT NULL,
    last_done       TEXT,
    next_due        TEXT,
    notes           TEXT,
    active          INTEGER DEFAULT 1,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS task_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER NOT NULL REFERENCES tasks(id),
    date        TEXT NOT NULL,
    cost        REAL,
    provider    TEXT,
    notes       TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS pest_treatments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    area        TEXT NOT NULL,
    product     TEXT NOT NULL,
    method      TEXT,
    notes       TEXT,
    cost        REAL,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS providers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    category        TEXT NOT NULL,
    phone           TEXT,
    email           TEXT,
    typical_cost    REAL,
    notes           TEXT,
    active          INTEGER DEFAULT 1,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS appliances (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    name                    TEXT NOT NULL,
    category                TEXT NOT NULL,
    brand                   TEXT,
    model                   TEXT,
    purchase_date           TEXT,
    warranty_end            TEXT,
    expected_lifespan_years INTEGER,
    replacement_cost        REAL,
    location                TEXT,
    notes                   TEXT,
    created_at              TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS utility_bills (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_date   TEXT NOT NULL,
    type        TEXT NOT NULL,
    amount      REAL NOT NULL,
    usage       TEXT,
    notes       TEXT,
    created_at  TEXT DEFAULT (datetime('now')),
    UNIQUE(bill_date, type)
);

CREATE TABLE IF NOT EXISTS costs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    category    TEXT NOT NULL,
    amount      REAL NOT NULL,
    provider    TEXT,
    description TEXT,
    notes       TEXT,
    source      TEXT DEFAULT 'manual',
    source_id   INTEGER,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS hvac_snapshots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at         TEXT NOT NULL,
    zone                TEXT NOT NULL,
    hvac_mode           TEXT NOT NULL,
    current_temp        REAL,
    target_temp         REAL,
    target_temp_high    REAL,
    target_temp_low     REAL,
    humidity            REAL,
    preset              TEXT,
    outdoor_temp        REAL,
    outdoor_humidity    REAL,
    created_at          TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_hvac_snapshots_captured_at
    ON hvac_snapshots (captured_at);

CREATE INDEX IF NOT EXISTS idx_hvac_snapshots_zone_captured
    ON hvac_snapshots (zone, captured_at);
"""


def apply_schema(conn: sqlite3.Connection) -> None:
    """Apply the schema to an open connection (idempotent via CREATE TABLE IF NOT EXISTS)."""
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "INSERT OR IGNORE INTO schema_info (version) VALUES (?)",
        (SCHEMA_VERSION,),
    )
    conn.commit()


def init_db(config):
    """Create or migrate the database. Returns the database path.

    CLI-facing entry point. For auto-init on every connection use get_db(),
    which calls apply_schema() internally.
    """
    db_path = get_db_path(config)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    apply_schema(conn)
    conn.close()
    return db_path
