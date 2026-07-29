"""Tests for coordinator.daemon's irrigation budget check wall-clock gate.

_check_irrigation_budget_daily is time-gated (fires once/day at/after
IRRIGATION_BUDGET_CHECK_HOUR:MINUTE), not cycle-count gated like the YNAB
poller -- these are pure gating-logic unit tests, check_irrigation_budget
itself is mocked (see test_rules.py::TestCheckIrrigationBudget for its logic).
"""

from datetime import datetime
from unittest.mock import patch

import coordinator.daemon as daemon


def _at(hour, minute):
    return datetime(2026, 7, 22, hour, minute)


class TestCheckIrrigationBudgetDailyGating:
    @patch("coordinator.daemon.check_irrigation_budget")
    @patch("coordinator.daemon.datetime")
    def test_before_threshold_does_not_check(self, mock_datetime, mock_check):
        mock_datetime.now.return_value = _at(
            daemon.IRRIGATION_BUDGET_CHECK_HOUR, daemon.IRRIGATION_BUDGET_CHECK_MINUTE - 1
        )

        result = daemon._check_irrigation_budget_daily(None)

        assert result is None
        mock_check.assert_not_called()

    @patch("coordinator.daemon.check_irrigation_budget")
    @patch("coordinator.daemon.datetime")
    def test_at_threshold_checks_and_returns_today(self, mock_datetime, mock_check):
        now = _at(daemon.IRRIGATION_BUDGET_CHECK_HOUR, daemon.IRRIGATION_BUDGET_CHECK_MINUTE)
        mock_datetime.now.return_value = now
        mock_check.return_value = {"action": "no_op"}

        result = daemon._check_irrigation_budget_daily(None)

        assert result == now.date().isoformat()
        mock_check.assert_called_once()

    @patch("coordinator.daemon.check_irrigation_budget")
    @patch("coordinator.daemon.datetime")
    def test_already_checked_today_short_circuits(self, mock_datetime, mock_check):
        now = _at(23, 0)
        mock_datetime.now.return_value = now
        today = now.date().isoformat()

        result = daemon._check_irrigation_budget_daily(today)

        assert result == today
        mock_check.assert_not_called()

    @patch("coordinator.daemon.check_irrigation_budget")
    @patch("coordinator.daemon.datetime")
    def test_first_ever_run_with_none_last_check_after_threshold_checks(self, mock_datetime, mock_check):
        now = _at(22, 0)  # daemon started late in the day, never checked before
        mock_datetime.now.return_value = now
        mock_check.return_value = {"action": "reminder_created"}

        result = daemon._check_irrigation_budget_daily(None)

        assert result == now.date().isoformat()
        mock_check.assert_called_once()

    @patch("coordinator.daemon.check_irrigation_budget")
    @patch("coordinator.daemon.datetime")
    def test_check_failure_logs_and_does_not_crash(self, mock_datetime, mock_check):
        now = _at(daemon.IRRIGATION_BUDGET_CHECK_HOUR, daemon.IRRIGATION_BUDGET_CHECK_MINUTE)
        mock_datetime.now.return_value = now
        mock_check.side_effect = RuntimeError("no configuration found")

        result = daemon._check_irrigation_budget_daily(None)

        # Still marked checked today (retried tomorrow, not hammered same day).
        assert result == now.date().isoformat()
