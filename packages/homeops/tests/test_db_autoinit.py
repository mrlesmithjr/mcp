"""Tests for DB auto-init on connect (issue #16 backport)."""

import sqlite3

import pytest
from homeops.db.appliances import list_appliances
from homeops.db.connection import get_db
from homeops.db.costs import get_cost_summary
from homeops.db.hvac import get_hvac_trend
from homeops.db.pest import list_pest_history
from homeops.db.providers import list_providers
from homeops.db.tasks import add_task, list_tasks
from homeops.db.utilities import get_utility_summary


@pytest.fixture()
def tmp_config(tmp_path):
    """Config pointing to a temp DB that does not exist yet."""
    db_path = tmp_path / "homeops.db"
    # Do NOT create the file -- the point of this test is auto-init
    return {"database": {"path": str(db_path)}}


def test_get_db_creates_file_when_absent(tmp_config, tmp_path):
    """get_db() must create and return a connection even when the DB file is absent."""
    db_path = tmp_path / "homeops.db"
    assert not db_path.exists(), "precondition: DB must not exist before get_db()"

    conn = get_db(tmp_config)
    assert conn is not None
    conn.close()

    assert db_path.exists(), "get_db() must create the DB file"


def test_get_db_schema_is_valid(tmp_config):
    """Schema tables must exist after auto-init."""
    conn = get_db(tmp_config)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()

    expected = {
        "schema_info",
        "tasks",
        "task_log",
        "pest_treatments",
        "providers",
        "appliances",
        "utility_bills",
        "costs",
        "hvac_snapshots",
    }
    assert expected.issubset(tables), f"Missing tables: {expected - tables}"


def test_get_db_idempotent(tmp_config):
    """Calling get_db() twice on an existing DB must not error."""
    conn1 = get_db(tmp_config)
    conn1.close()
    conn2 = get_db(tmp_config)
    conn2.close()


def test_list_tasks_empty_on_fresh_db(tmp_config):
    """list_tasks() on a fresh auto-inited DB returns [] not an error."""
    result = list_tasks(tmp_config)
    assert result == []


def test_list_pest_history_empty_on_fresh_db(tmp_config):
    """list_pest_history() on a fresh auto-inited DB returns []."""
    result = list_pest_history(tmp_config)
    assert result == []


def test_get_cost_summary_empty_on_fresh_db(tmp_config):
    """get_cost_summary() on a fresh auto-inited DB returns []."""
    result = get_cost_summary(tmp_config)
    assert result == []


def test_list_providers_empty_on_fresh_db(tmp_config):
    """list_providers() on a fresh auto-inited DB returns []."""
    result = list_providers(tmp_config)
    assert result == []


def test_list_appliances_empty_on_fresh_db(tmp_config):
    """list_appliances() on a fresh auto-inited DB returns []."""
    result = list_appliances(tmp_config)
    assert result == []


def test_get_utility_summary_empty_on_fresh_db(tmp_config):
    """get_utility_summary() on a fresh auto-inited DB returns []."""
    result = get_utility_summary(tmp_config)
    assert result == []


def test_get_hvac_trend_empty_on_fresh_db(tmp_config):
    """get_hvac_trend() on a fresh auto-inited DB returns []."""
    result = get_hvac_trend(tmp_config)
    assert result == []


def test_add_task_on_fresh_db(tmp_config):
    """Write path: add_task() works on a fresh auto-inited DB without needing db init first."""
    result = add_task(tmp_config, "Test filter change", "hvac", "90d")
    assert result["name"] == "Test filter change"

    tasks = list_tasks(tmp_config)
    assert len(tasks) == 1
    assert tasks[0]["name"] == "Test filter change"


def test_integrity_check(tmp_config, tmp_path):
    """PRAGMA integrity_check returns ok after auto-init."""
    conn = get_db(tmp_config)
    conn.close()

    db_path = tmp_path / "homeops.db"
    check_conn = sqlite3.connect(str(db_path))
    result = check_conn.execute("PRAGMA integrity_check;").fetchone()[0]
    check_conn.close()
    assert result == "ok"
