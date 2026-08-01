"""Tests for irrigation check (issue #146).

evaluate_irrigation_check() is DB-only; it calls irrigation_budget() and
et_recommendations() and combines their qualifying signals. Both underlying
functions are patched at module level here rather than exercised against a
live Hydrawise connection or synthetic water-bill history - the boundary
math for each lives in its own function and is tested separately. The CLI
handler's read-only report output is tested separately (issue #39 - Apple
Reminders creation removed, so `lawnops irrigation check` now prints its
findings instead).
"""

from datetime import datetime
from unittest.mock import patch

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
    @patch("lawnops.db.evaluate_irrigation_check")
    def test_prints_report_when_budget_and_et_qualify(self, mock_eval, tmp_config, capsys):
        mock_eval.return_value = {
            "qualifies": True,
            "budget_issue": _budget("over"),
            "et_issue": _et_issue(),
        }

        _cmd_irrigation_check(tmp_config)

        out = capsys.readouterr().out
        assert "Irrigation check: budget + ET" in out
        assert "Budget over: $100.00 spent, $140.00 projected" in out
        assert "2026-06: reduce ET by ~15%" in out

    @patch("lawnops.db.evaluate_irrigation_check")
    def test_prints_report_when_budget_alone_qualifies(self, mock_eval, tmp_config, capsys):
        mock_eval.return_value = {
            "qualifies": True,
            "budget_issue": _budget("warning"),
            "et_issue": [],
        }

        _cmd_irrigation_check(tmp_config)

        out = capsys.readouterr().out
        assert "Irrigation check: budget warning" in out

    @patch("lawnops.db.evaluate_irrigation_check")
    def test_no_qualifying_issues(self, mock_eval, tmp_config, capsys):
        mock_eval.return_value = {"qualifies": False, "budget_issue": None, "et_issue": []}

        _cmd_irrigation_check(tmp_config)

        out = capsys.readouterr().out
        assert "no qualifying issues" in out

    @patch("lawnops.db.evaluate_irrigation_check")
    def test_et_issue_alone_prints_reduction_summary(self, mock_eval, tmp_config, capsys):
        mock_eval.return_value = {
            "qualifies": True,
            "budget_issue": None,
            "et_issue": _et_issue(),
        }

        _cmd_irrigation_check(tmp_config)

        out = capsys.readouterr().out
        assert "Irrigation check: ET reduction recommended" in out
        assert "2026-06: reduce ET by ~15%" in out
