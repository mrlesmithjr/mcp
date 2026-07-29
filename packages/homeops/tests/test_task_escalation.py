"""Tests for task escalation (issue #146).

evaluate_task_escalation() is DB-only (no EventKit/Reminders import), so
it's tested here directly against a temp SQLite DB. The CLI's dedup call
into homeops.reminders is tested separately with RemindersManager mocked.
"""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest
from homeops.cli.main import _cmd_task_escalate
from homeops.db.connection import get_db
from homeops.db.tasks import add_task, evaluate_task_escalation


@pytest.fixture()
def tmp_config(tmp_path):
    """Config pointing to a temp DB, plus the default reminders section."""
    db_path = tmp_path / "homeops.db"
    return {
        "database": {"path": str(db_path)},
        "reminders": {"list": "Personal", "default_time": "10:00"},
    }


def _add_overdue_task(config, name, category, days_overdue):
    """Add a task and directly set its next_due to `days_overdue` days in the past."""
    add_task(config, name, category, "30d")
    next_due = (date.today() - timedelta(days=days_overdue)).isoformat()
    conn = get_db(config)
    conn.execute("UPDATE tasks SET next_due = ? WHERE name = ?", (next_due, name))
    conn.commit()
    conn.close()


class TestEvaluateTaskEscalation:
    def test_safety_task_overdue_one_day_fires(self, tmp_config):
        """Any safety-category task overdue at all qualifies."""
        _add_overdue_task(tmp_config, "Check smoke detectors", "safety", 1)
        qualifying = evaluate_task_escalation(tmp_config)
        assert len(qualifying) == 1
        assert qualifying[0]["name"] == "Check smoke detectors"
        assert qualifying[0]["reason"] == "safety"

    def test_non_safety_at_exactly_60_days_does_not_fire(self, tmp_config):
        """Boundary: exactly 60 days overdue must NOT fire (> not >=)."""
        _add_overdue_task(tmp_config, "Clean gutters", "gutters", 60)
        qualifying = evaluate_task_escalation(tmp_config)
        assert qualifying == []

    def test_non_safety_at_61_days_fires(self, tmp_config):
        """One day past the threshold fires."""
        _add_overdue_task(tmp_config, "Clean gutters", "gutters", 61)
        qualifying = evaluate_task_escalation(tmp_config)
        assert len(qualifying) == 1
        assert qualifying[0]["name"] == "Clean gutters"
        assert qualifying[0]["reason"] == "60+ days"
        assert qualifying[0]["days_overdue"] == 61

    def test_non_safety_at_59_days_does_not_fire(self, tmp_config):
        """Below the threshold never fires."""
        _add_overdue_task(tmp_config, "Clean gutters", "gutters", 59)
        qualifying = evaluate_task_escalation(tmp_config)
        assert qualifying == []

    def test_no_qualifying_tasks_returns_empty_list(self, tmp_config):
        _add_overdue_task(tmp_config, "Clean gutters", "gutters", 5)
        qualifying = evaluate_task_escalation(tmp_config)
        assert qualifying == []

    def test_multiple_qualifying_tasks_at_once(self, tmp_config):
        _add_overdue_task(tmp_config, "Check smoke detectors", "safety", 1)
        _add_overdue_task(tmp_config, "Clean gutters", "gutters", 90)
        _add_overdue_task(tmp_config, "Change AC filter", "hvac", 10)  # not qualifying

        qualifying = evaluate_task_escalation(tmp_config)
        names = {t["name"] for t in qualifying}
        assert names == {"Check smoke detectors", "Clean gutters"}
        assert len(qualifying) == 2


class TestCmdTaskEscalate:
    @patch("homeops.reminders.RemindersManager")
    def test_creates_reminder_when_qualifying_tasks_found(self, mock_manager_cls, tmp_config, capsys):
        mock_manager = MagicMock()
        mock_manager.search_reminders.return_value = []
        mock_manager.create_reminder.return_value = {"id": "new1"}
        mock_manager_cls.return_value = mock_manager

        _add_overdue_task(tmp_config, "Check smoke detectors", "safety", 1)

        _cmd_task_escalate(tmp_config)

        mock_manager.search_reminders.assert_called_once_with("overdue tasks need attention", list_name="Personal")
        mock_manager.create_reminder.assert_called_once()
        _, kwargs = mock_manager.create_reminder.call_args
        assert kwargs["priority"] == 1
        assert kwargs["due_time"] == "08:00"
        assert "Check smoke detectors" in kwargs["notes"]

        out = capsys.readouterr().out
        assert "reminder created" in out

    @patch("homeops.reminders.RemindersManager")
    def test_reports_already_pending_when_reminder_exists(self, mock_manager_cls, tmp_config, capsys):
        mock_manager = MagicMock()
        mock_manager.search_reminders.return_value = [{"id": "abc123", "title": "HomeOps: overdue tasks"}]
        mock_manager_cls.return_value = mock_manager

        _add_overdue_task(tmp_config, "Check smoke detectors", "safety", 1)

        _cmd_task_escalate(tmp_config)

        mock_manager.create_reminder.assert_not_called()
        out = capsys.readouterr().out
        assert "reminder already pending" in out

    @patch("homeops.reminders.RemindersManager")
    def test_no_reminder_created_when_nothing_qualifies(self, mock_manager_cls, tmp_config, capsys):
        mock_manager_cls.return_value = MagicMock()

        _add_overdue_task(tmp_config, "Change AC filter", "hvac", 5)

        _cmd_task_escalate(tmp_config)

        mock_manager_cls.return_value.search_reminders.assert_not_called()
        out = capsys.readouterr().out
        assert "no qualifying tasks" in out
