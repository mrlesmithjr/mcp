"""Tests for database schema and helpers."""

import sqlite3

from ynab_tools.db import get_connection, init_db


class TestDatabase:
    def test_init_creates_tables(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = get_connection(db_path)
        init_db(conn)

        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        table_names = {row["name"] for row in tables}

        assert "accounts" in table_names
        assert "transactions" in table_names
        assert "subtransactions" in table_names
        assert "payees" in table_names
        assert "budget_months" in table_names
        assert "budget_categories" in table_names
        assert "sync_state" in table_names
        assert "sync_log" in table_names
        assert "net_worth_snapshots" in table_names
        conn.close()

    def test_init_idempotent(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = get_connection(db_path)
        init_db(conn)
        init_db(conn)  # should not raise
        conn.close()

    def test_row_factory(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = get_connection(db_path)
        assert conn.row_factory == sqlite3.Row
        conn.close()


class TestDatabaseFilePermissions:
    """The budget DB must not be created world-readable (see get_connection)."""

    def test_new_database_is_user_only(self, tmp_path):
        import stat as _stat

        from ynab_tools.db import get_connection

        db = tmp_path / "ynab.db"
        get_connection(db).close()

        assert db.exists()
        assert _stat.S_IMODE(db.stat().st_mode) == 0o600

    def test_existing_world_readable_database_is_tightened(self, tmp_path):
        import stat as _stat

        from ynab_tools.db import get_connection

        db = tmp_path / "ynab.db"
        get_connection(db).close()
        db.chmod(0o644)

        get_connection(db).close()

        assert _stat.S_IMODE(db.stat().st_mode) == 0o600
