"""Database schema definition and initialization."""

import sqlite3

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_info (
    version INTEGER PRIMARY KEY,
    applied_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS daily_observations (
    date            TEXT PRIMARY KEY,
    soil_min_f      REAL,
    soil_max_f      REAL,
    soil_avg_f      REAL,
    air_min_f       REAL,
    air_max_f       REAL,
    air_avg_f       REAL,
    precip_in       REAL DEFAULT 0,
    wind_max_mph    REAL,
    advisory_level  TEXT,
    source          TEXT DEFAULT 'open-meteo',
    logged_at       TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS treatments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL,
    treatment_area  TEXT NOT NULL,
    product         TEXT NOT NULL,
    method          TEXT,
    amount          TEXT,
    soil_temp_f     REAL,
    cost            REAL,
    notes           TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS products (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    category        TEXT,
    qty_on_hand     REAL DEFAULT 0,
    unit            TEXT DEFAULT 'bag',
    last_ordered    TEXT,
    cost_each       REAL,
    source          TEXT,
    notes           TEXT,
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS equipment (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    purchase_date   TEXT,
    cost            REAL,
    source          TEXT,
    status          TEXT DEFAULT 'active',
    notes           TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS irrigation_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL,
    zone_number     INTEGER NOT NULL,
    zone_name       TEXT,
    start_time      TEXT,
    duration_min    REAL,
    status          TEXT,
    source          TEXT DEFAULT 'hydrawise',
    logged_at       TEXT DEFAULT (datetime('now')),
    UNIQUE(date, zone_number, start_time)
);

CREATE TABLE IF NOT EXISTS mowing_visits (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL,
    provider        TEXT,
    cost            REAL,
    notes           TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS purchases (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL,
    item            TEXT NOT NULL,
    category        TEXT,
    qty             REAL DEFAULT 1,
    cost            REAL,
    source          TEXT,
    notes           TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS seasonal_tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    season          TEXT NOT NULL,
    task_name       TEXT NOT NULL,
    timing          TEXT,
    product         TEXT,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS pollen_counts (
    date            TEXT PRIMARY KEY,
    total_count     INTEGER NOT NULL,
    category        TEXT NOT NULL,
    trees           TEXT,
    grasses         TEXT,
    weeds           TEXT,
    molds           TEXT,
    source          TEXT DEFAULT 'atlanta_allergy',
    logged_at       TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS irrigation_skips (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    expected_date   TEXT NOT NULL,
    zone_number     INTEGER NOT NULL,
    zone_name       TEXT,
    program_id      INTEGER,
    program_name    TEXT,
    inferred_reason TEXT,
    precip_3day_in  REAL,
    wind_max_mph    REAL,
    air_min_f       REAL,
    synced_at       TEXT DEFAULT (datetime('now')),
    UNIQUE(expected_date, zone_number)
);
"""


def init_db(conn: sqlite3.Connection) -> None:
    """Execute the schema against an open connection (idempotent via CREATE TABLE IF NOT EXISTS)."""
    # executescript issues an implicit COMMIT before running; call only from connect()
    # before any open transaction.
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "INSERT OR IGNORE INTO schema_info (version) VALUES (?)",
        (SCHEMA_VERSION,),
    )
    conn.commit()
