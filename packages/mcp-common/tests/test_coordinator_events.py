"""Tests for mcp_common.coordinator_events."""

import json
import sqlite3

from mcp_common import coordinator_events


class TestEmitCoordinatorEvent:
    def test_happy_path_writes_row_and_returns_true(self, monkeypatch, tmp_path):
        db_path = tmp_path / "coordinator" / "events.db"
        monkeypatch.setattr(coordinator_events, "COORDINATOR_DB", str(db_path))

        result = coordinator_events.emit_coordinator_event("homeops", "task_done", {"name": "Test Task"})

        assert result is True
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM events").fetchone()
        conn.close()

        assert row["source"] == "homeops"
        assert row["event_type"] == "task_done"
        assert json.loads(row["payload"]) == {"name": "Test Task"}
        assert row["processed_at"] is None
        assert row["created_at"] is not None

    def test_unwritable_path_returns_false_and_does_not_raise(self, monkeypatch, tmp_path):
        readonly_dir = tmp_path / "readonly"
        readonly_dir.mkdir(mode=0o500)
        # Nested path forces os.makedirs to attempt a write inside readonly_dir.
        monkeypatch.setattr(coordinator_events, "COORDINATOR_DB", str(readonly_dir / "sub" / "events.db"))

        try:
            result = coordinator_events.emit_coordinator_event("homeops", "task_done", {"name": "Test Task"})
            assert result is False
        finally:
            readonly_dir.chmod(0o700)  # restore so tmp_path teardown can remove it

    def test_idempotent_table_creation_on_repeated_calls(self, monkeypatch, tmp_path):
        db_path = tmp_path / "coordinator" / "events.db"
        monkeypatch.setattr(coordinator_events, "COORDINATOR_DB", str(db_path))

        first = coordinator_events.emit_coordinator_event("homeops", "task_done", {"name": "A"})
        second = coordinator_events.emit_coordinator_event("lawnops", "treatment_add", {"product": "B"})

        assert first is True
        assert second is True

        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        conn.close()
        assert count == 2
