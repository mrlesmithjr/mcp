"""Tests for irrigation check (issue #146).

evaluate_irrigation_check() is DB-only (no EventKit/Reminders import); it
calls irrigation_budget() and et_recommendations() and combines their
qualifying signals. Both underlying functions are patched at module level
here rather than exercised against a live Hydrawise connection or synthetic
water-bill history - the boundary math for each lives in its own function
and is tested separately. The CLI handler's dedup call into lawnops.reminders
is tested separately with RemindersManager mocked.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from lawnops.cli.main import _cmd_irrigation_check
from lawnops.db.irrigation_analytics import evaluate_irrigation_check


def _budget(status, current_cost=100.0, projected_cost=140.0, recommendations=None):
    return {
        "budget_status": status,
        "current_cost": current_cost,
        "projected_cost": projected_cost,
        "recommendations": recommendations or [],
    }


def _et_issue(month="2026-06", detail="2026-06: reduce ET by ~15%"):
    return [
        {
            "month": month,
            "current_cpm": 0.20,
            "avg_cpm": 0.12,
            "expense_level": "high",
            "suggested_et_reduction_pct": 15,
            "potential_monthly_savings": 20.0,
            "detail": detail,
        }
    ]


@pytest.fixture()
def tmp_config(tmp_path):
    db_path = tmp_path / "lawnops.db"
    return {"database": {"path": str(db_path)}}


class TestEvaluateIrrigationCheck:
    @patch("lawnops.db.irrigation_analytics.et_recommendations")
    @patch("lawnops.db.irrigation_analytics.irrigation_budget")
    def test_budget_warning_alone_qualifies(self, mock_budget, mock_et, tmp_config):
        mock_budget.return_value = _budget("warning")
        mock_et.return_value = {"recommendations": []}

        result = evaluate_irrigation_check(tmp_config)

        assert result["qualifies"] is True
        assert result["budget_issue"]["budget_status"] == "warning"
        assert result["et_issue"] == []

    @patch("lawnops.db.irrigation_analytics.et_recommendations")
    @patch("lawnops.db.irrigation_analytics.irrigation_budget")
    def test_budget_over_alone_qualifies(self, mock_budget, mock_et, tmp_config):
        mock_budget.return_value = _budget("over")
        mock_et.return_value = {"recommendations": []}

        result = evaluate_irrigation_check(tmp_config)

        assert result["qualifies"] is True
        assert result["budget_issue"]["budget_status"] == "over"

    @patch("lawnops.db.irrigation_analytics.et_recommendations")
    @patch("lawnops.db.irrigation_analytics.irrigation_budget")
    def test_budget_ok_and_no_et_issue_does_not_qualify(self, mock_budget, mock_et, tmp_config):
        mock_budget.return_value = _budget("ok")
        mock_et.return_value = {"recommendations": []}

        result = evaluate_irrigation_check(tmp_config)

        assert result["qualifies"] is False
        assert result["budget_issue"] is None
        assert result["et_issue"] == []

    @patch("lawnops.db.irrigation_analytics.et_recommendations")
    @patch("lawnops.db.irrigation_analytics.irrigation_budget")
    def test_et_issue_alone_qualifies_even_with_budget_ok(self, mock_budget, mock_et, tmp_config):
        mock_budget.return_value = _budget("ok")
        mock_et.return_value = {"recommendations": _et_issue()}

        result = evaluate_irrigation_check(tmp_config)

        assert result["qualifies"] is True
        assert result["budget_issue"] is None
        assert len(result["et_issue"]) == 1

    @patch("lawnops.db.irrigation_analytics.et_recommendations")
    @patch("lawnops.db.irrigation_analytics.irrigation_budget")
    def test_both_budget_and_et_issues_qualify_together(self, mock_budget, mock_et, tmp_config):
        mock_budget.return_value = _budget("warning")
        mock_et.return_value = {"recommendations": _et_issue()}

        result = evaluate_irrigation_check(tmp_config)

        assert result["qualifies"] is True
        assert result["budget_issue"]["budget_status"] == "warning"
        assert len(result["et_issue"]) == 1

    @patch("lawnops.db.irrigation_analytics.et_recommendations")
    @patch("lawnops.db.irrigation_analytics.irrigation_budget")
    def test_et_recommendations_called_with_current_year(self, mock_budget, mock_et, tmp_config):
        """Confirm the current-year-not-last-year decision: et_recommendations
        defaults to last year, so the check must pass this year explicitly."""
        mock_budget.return_value = _budget("ok")
        mock_et.return_value = {"recommendations": []}

        evaluate_irrigation_check(tmp_config)

        mock_et.assert_called_once_with(tmp_config, year=datetime.now().year)


class TestCmdIrrigationCheck:
    @patch("lawnops.reminders.RemindersManager")
    @patch("lawnops.db.evaluate_irrigation_check")
    def test_creates_reminder_when_budget_and_et_qualify(self, mock_eval, mock_manager_cls, tmp_config, capsys):
        mock_manager = MagicMock()
        mock_manager.search_reminders.return_value = []
        mock_manager.create_reminder.return_value = {"id": "new1"}
        mock_manager_cls.return_value = mock_manager

        mock_eval.return_value = {
            "qualifies": True,
            "budget_issue": _budget("over"),
            "et_issue": _et_issue(),
        }

        _cmd_irrigation_check(tmp_config)

        mock_manager.search_reminders.assert_called_once_with("Irrigation: action needed", list_name="Personal")
        mock_manager.create_reminder.assert_called_once()
        args, kwargs = mock_manager.create_reminder.call_args
        assert args[0] == "Irrigation: action needed: budget + ET"
        assert kwargs["priority"] == 5
        assert kwargs["due_time"] == "08:00"

        out = capsys.readouterr().out
        assert "reminder created" in out

    @patch("lawnops.reminders.RemindersManager")
    @patch("lawnops.db.evaluate_irrigation_check")
    def test_reports_already_pending_when_reminder_exists(self, mock_eval, mock_manager_cls, tmp_config, capsys):
        mock_manager = MagicMock()
        mock_manager.search_reminders.return_value = [{"id": "abc", "title": "Irrigation: action needed"}]
        mock_manager_cls.return_value = mock_manager

        mock_eval.return_value = {
            "qualifies": True,
            "budget_issue": _budget("warning"),
            "et_issue": [],
        }

        _cmd_irrigation_check(tmp_config)

        mock_manager.create_reminder.assert_not_called()
        out = capsys.readouterr().out
        assert "reminder already pending" in out

    @patch("lawnops.reminders.RemindersManager")
    @patch("lawnops.db.evaluate_irrigation_check")
    def test_no_reminder_created_when_nothing_qualifies(self, mock_eval, mock_manager_cls, tmp_config, capsys):
        mock_eval.return_value = {"qualifies": False, "budget_issue": None, "et_issue": []}

        _cmd_irrigation_check(tmp_config)

        mock_manager_cls.return_value.search_reminders.assert_not_called()
        out = capsys.readouterr().out
        assert "no qualifying issues" in out

    @patch("lawnops.reminders.RemindersManager")
    @patch("lawnops.db.evaluate_irrigation_check")
    def test_search_query_is_stable_prefix_not_dynamic_title(self, mock_eval, mock_manager_cls, tmp_config):
        """The dedup search_query must stay the stable prefix regardless of
        what the dynamic title summary says (issue #146 design notes)."""
        mock_manager = MagicMock()
        mock_manager.search_reminders.return_value = []
        mock_manager.create_reminder.return_value = {"id": "new1"}
        mock_manager_cls.return_value = mock_manager

        mock_eval.return_value = {
            "qualifies": True,
            "budget_issue": None,
            "et_issue": _et_issue(),
        }

        _cmd_irrigation_check(tmp_config)

        search_args, _ = mock_manager.search_reminders.call_args
        assert search_args[0] == "Irrigation: action needed"
        create_args, _ = mock_manager.create_reminder.call_args
        assert create_args[0] == "Irrigation: action needed: ET reduction recommended"
