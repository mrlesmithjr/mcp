"""SQLite storage for routes and price snapshots."""

import json
import os
import sqlite3
from datetime import datetime, timezone

_SCHEMA = """
CREATE TABLE IF NOT EXISTS routes (
    id                INTEGER PRIMARY KEY,
    origin            TEXT NOT NULL,
    destination       TEXT NOT NULL,
    travel_date       TEXT NOT NULL,
    return_date       TEXT,
    passengers        INTEGER NOT NULL DEFAULT 2,
    target_price      REAL,
    preferred_airline TEXT,
    nonstop_only      INTEGER NOT NULL DEFAULT 0,
    label             TEXT,
    active            INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS price_snapshots (
    id               INTEGER PRIMARY KEY,
    route_id         INTEGER NOT NULL REFERENCES routes(id),
    fetched_at       TEXT NOT NULL,
    price_total      REAL NOT NULL,
    price_per_person REAL NOT NULL,
    currency         TEXT NOT NULL DEFAULT 'USD',
    airline          TEXT,
    stops            INTEGER,
    duration         TEXT,
    departure        TEXT,
    arrival          TEXT,
    price_level      TEXT,
    raw_json         TEXT
);

CREATE TABLE IF NOT EXISTS search_log (
    id           INTEGER PRIMARY KEY,
    searched_at  TEXT NOT NULL,
    origin       TEXT NOT NULL,
    destination  TEXT NOT NULL,
    search_type  TEXT NOT NULL,
    passengers   INTEGER NOT NULL DEFAULT 1,
    result_count INTEGER NOT NULL DEFAULT 0
);
"""


def _db_path() -> str:
    path = os.environ.get("FLIGHTOPS_DB_PATH", os.path.expanduser("~/.local/share/flightops/flightops.db"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def _init_db(conn: sqlite3.Connection) -> None:
    """Create schema and run migrations against an open connection (idempotent)."""
    conn.executescript(_SCHEMA)
    _migrate(conn)
    conn.commit()


def get_db() -> sqlite3.Connection:
    """Open a sqlite3 connection and auto-init the schema on every call (idempotent)."""
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _init_db(conn)
    return conn


def _migrate(conn: sqlite3.Connection):
    """Add columns introduced after initial schema."""
    existing_route_cols = {row[1] for row in conn.execute("PRAGMA table_info(routes)").fetchall()}
    existing_snap_cols = {row[1] for row in conn.execute("PRAGMA table_info(price_snapshots)").fetchall()}

    if "preferred_airline" not in existing_route_cols:
        conn.execute("ALTER TABLE routes ADD COLUMN preferred_airline TEXT")
    if "nonstop_only" not in existing_route_cols:
        conn.execute("ALTER TABLE routes ADD COLUMN nonstop_only INTEGER NOT NULL DEFAULT 0")

    for col in ("departure", "arrival", "price_level"):
        if col not in existing_snap_cols:
            # Safe: col comes from a hardcoded tuple, never from external input
            conn.execute(f"ALTER TABLE price_snapshots ADD COLUMN {col} TEXT")


# --- routes ---


def add_route(
    origin,
    destination,
    travel_date,
    return_date=None,
    passengers=2,
    target_price=None,
    preferred_airline=None,
    nonstop_only=False,
    label=None,
) -> int:
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """INSERT INTO routes
           (origin, destination, travel_date, return_date, passengers,
            target_price, preferred_airline, nonstop_only, label, active, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,1,?)""",
        (
            origin.upper(),
            destination.upper(),
            travel_date,
            return_date,
            passengers,
            target_price,
            preferred_airline,
            int(nonstop_only),
            label,
            now,
        ),
    )
    conn.commit()
    route_id = cur.lastrowid
    conn.close()
    return route_id


def list_routes(active_only=True) -> list:
    conn = get_db()
    query = "SELECT * FROM routes"
    if active_only:
        query += " WHERE active = 1"
    query += " ORDER BY travel_date, id"
    rows = conn.execute(query).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_route(route_id: int) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM routes WHERE id = ?", (route_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_route(route_id: int, **fields):
    """Update one or more columns on a route by keyword argument."""
    allowed = {"target_price", "preferred_airline", "nonstop_only", "label", "passengers"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"Unknown route fields: {unknown}")
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    cols = ", ".join(f"{k} = ?" for k in updates)
    conn = get_db()
    conn.execute(f"UPDATE routes SET {cols} WHERE id = ?", (*updates.values(), route_id))
    conn.commit()
    conn.close()


def remove_route(route_id: int):
    conn = get_db()
    conn.execute("UPDATE routes SET active = 0 WHERE id = ?", (route_id,))
    conn.commit()
    conn.close()


# --- price snapshots ---


def add_snapshot(
    route_id: int,
    price_total: float,
    price_per_person: float,
    currency: str = "USD",
    airline: str = None,
    stops: int = None,
    duration: str = None,
    departure: str = None,
    arrival: str = None,
    price_level: str = None,
    raw: dict = None,
):
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO price_snapshots
           (route_id, fetched_at, price_total, price_per_person, currency,
            airline, stops, duration, departure, arrival, price_level, raw_json)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            route_id,
            now,
            price_total,
            price_per_person,
            currency,
            airline,
            stops,
            duration,
            departure,
            arrival,
            price_level,
            json.dumps(raw) if raw else None,
        ),
    )
    conn.commit()
    conn.close()


def get_history(route_id: int, limit: int | None = None, desc: bool = False) -> list:
    conn = get_db()
    order = "DESC" if desc else "ASC"
    query = f"SELECT * FROM price_snapshots WHERE route_id = ? ORDER BY fetched_at {order}"
    params: tuple = (route_id,)
    if limit is not None:
        query += " LIMIT ?"
        params = (route_id, limit)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_latest_snapshot(route_id: int) -> dict | None:
    conn = get_db()
    row = conn.execute(
        """SELECT * FROM price_snapshots
           WHERE route_id = ?
           ORDER BY fetched_at DESC LIMIT 1""",
        (route_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_stats(route_id: int) -> dict | None:
    conn = get_db()
    row = conn.execute(
        """SELECT
               COUNT(*)             AS snapshot_count,
               MIN(price_per_person) AS min_price,
               MAX(price_per_person) AS max_price,
               AVG(price_per_person) AS avg_price,
               MIN(fetched_at)       AS first_seen,
               MAX(fetched_at)       AS last_seen
           FROM price_snapshots
           WHERE route_id = ?""",
        (route_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# --- search log ---


def log_search(origin: str, destination: str, search_type: str, passengers: int, result_count: int):
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO search_log
           (searched_at, origin, destination, search_type, passengers, result_count)
           VALUES (?,?,?,?,?,?)""",
        (now, origin.upper(), destination.upper(), search_type, passengers, result_count),
    )
    conn.commit()
    conn.close()


def get_search_counts() -> dict:
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM search_log").fetchone()[0]
    this_month = conn.execute(
        "SELECT COUNT(*) FROM search_log WHERE strftime('%Y-%m', searched_at) = strftime('%Y-%m', 'now')"
    ).fetchone()[0]
    by_type = {
        row[0]: row[1]
        for row in conn.execute("SELECT search_type, COUNT(*) FROM search_log GROUP BY search_type").fetchall()
    }
    recent = [dict(r) for r in conn.execute("SELECT * FROM search_log ORDER BY searched_at DESC LIMIT 10").fetchall()]
    conn.close()
    return {
        "total": total,
        "this_month": this_month,
        "by_type": by_type,
        "recent": recent,
    }
