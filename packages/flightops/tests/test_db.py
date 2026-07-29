"""Tests for flightops.db auto-init behavior."""

import sqlite3

import flightops.db as db_module
import pytest


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Point FLIGHTOPS_DB_PATH at a fresh temp file (does not exist yet)."""
    db_file = tmp_path / "flightops.db"
    monkeypatch.setenv("FLIGHTOPS_DB_PATH", str(db_file))
    yield db_file, db_module


def test_get_db_creates_file_and_schema(tmp_db):
    """get_db() on a missing DB file must create the file and initialize the schema."""
    db_file, db = tmp_db
    assert not db_file.exists(), "DB file should not exist before first call"

    conn = db.get_db()
    conn.close()

    assert db_file.exists(), "get_db() must create the DB file"

    # Verify schema via integrity check
    result = sqlite3.connect(str(db_file)).execute("PRAGMA integrity_check").fetchone()[0]
    assert result == "ok"


def test_get_db_tables_present(tmp_db):
    """All three tables must exist after get_db() on a fresh DB."""
    db_file, db = tmp_db
    conn = db.get_db()
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert {"routes", "price_snapshots", "search_log"} <= tables


def test_list_routes_on_fresh_db_returns_empty(tmp_db):
    """list_routes() on a fresh DB must return an empty list, not raise."""
    db_file, db = tmp_db
    result = db.list_routes()
    assert result == []


def test_add_and_retrieve_route(tmp_db):
    """add_route() followed by list_routes() must round-trip on a fresh DB."""
    db_file, db = tmp_db
    route_id = db.add_route(
        origin="ATL",
        destination="YVR",
        travel_date="2026-12-01",
        passengers=2,
    )
    assert isinstance(route_id, int)
    routes = db.list_routes()
    assert len(routes) == 1
    assert routes[0]["origin"] == "ATL"
    assert routes[0]["destination"] == "YVR"


def test_init_db_is_idempotent(tmp_db):
    """Calling get_db() multiple times must not error or duplicate tables."""
    db_file, db = tmp_db
    for _ in range(3):
        conn = db.get_db()
        conn.close()

    conn = db.get_db()
    count = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='routes'").fetchone()[0]
    conn.close()
    assert count == 1
