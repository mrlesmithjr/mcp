"""Tests for task escalation (issue #146).

evaluate_task_escalation() is DB-only, so it's tested here directly against
a temp SQLite DB. The CLI command's read-only report output is tested
separately (issue #39 - Apple Reminders creation removed, so
`homeops task escalate` now prints its findings instead).
"""

from datetime import date, timedelta

import pytest
from homeops.cli.main import _cmd_task_escalate
from homeops.db.connection import get_db
from homeops.db.tasks import add_task, evaluate_task_escalation


@pytest.fixture()
def tmp_config(tmp_path):
    """Config pointing to a temp DB."""
    db_path = tmp_path / "homeops.db"
    return {
        "database": {"path": str(db_path)},
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
    def test_prints_report_when_qualifying_tasks_found(self, tmp_config, capsys):
        _add_overdue_task(tmp_config, "Check smoke detectors", "safety", 1)

        _cmd_task_escalate(tmp_config)

        out = capsys.readouterr().out
        assert "1 task qualify" in out
        assert "Check smoke detectors" in out
        assert "safety" in out

    def test_prints_no_qualifying_tasks(self, tmp_config, capsys):
        _add_overdue_task(tmp_config, "Change AC filter", "hvac", 5)

        _cmd_task_escalate(tmp_config)

        out = capsys.readouterr().out
        assert "no qualifying tasks" in out
