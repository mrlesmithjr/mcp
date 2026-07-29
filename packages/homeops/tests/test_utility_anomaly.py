"""Tests for utility anomaly detection (issue #146).

evaluate_utility_anomalies() is DB-only (no EventKit/Reminders import), so
it's tested here directly against a temp SQLite DB. reminders.py's dedup
logic is tested separately with RemindersManager mocked.
"""

from unittest.mock import MagicMock, patch

import pytest
from homeops.db.utilities import add_utility_bill, evaluate_utility_anomalies
from homeops.reminders import create_reminder_if_missing


@pytest.fixture()
def tmp_config(tmp_path):
    """Config pointing to a temp DB, plus the default reminders section."""
    db_path = tmp_path / "homeops.db"
    return {
        "database": {"path": str(db_path)},
        "reminders": {"list": "Personal", "default_time": "10:00"},
    }


def _add_months(config, utility_type, amounts, start="2026-01"):
    """Add sequential monthly bills for a utility type, oldest first."""
    year, month = (int(p) for p in start.split("-"))
    for amount in amounts:
        bill_date = f"{year:04d}-{month:02d}"
        add_utility_bill(config, bill_date, utility_type, amount)
        month += 1
        if month > 12:
            month = 1
            year += 1


class TestEvaluateUtilityAnomalies:
    def test_insufficient_history_does_not_fire(self, tmp_config):
        """Fewer than 7 bills on record: skip, no crash, no false positive."""
        _add_months(tmp_config, "electric", [100, 100, 100, 100, 100, 100])  # only 6
        anomalies = evaluate_utility_anomalies(tmp_config)
        assert anomalies == []

    def test_exact_threshold_boundary_does_not_fire(self, tmp_config):
        """Latest bill exactly 20.0% over baseline avg must NOT fire (> not >=)."""
        # Baseline avg = 100; latest = 120.00 is exactly +20%.
        _add_months(tmp_config, "electric", [100, 100, 100, 100, 100, 100, 120.0])
        anomalies = evaluate_utility_anomalies(tmp_config)
        assert anomalies == []

    def test_one_unit_above_threshold_fires(self, tmp_config):
        """Latest bill just over 20% above baseline avg fires."""
        _add_months(tmp_config, "electric", [100, 100, 100, 100, 100, 100, 120.01])
        anomalies = evaluate_utility_anomalies(tmp_config)
        assert len(anomalies) == 1
        result = anomalies[0]
        assert result["type"] == "electric"
        assert result["latest_amount"] == 120.01
        assert result["baseline_avg"] == 100.0
        assert result["pct_over"] > 20.0

    def test_below_threshold_does_not_fire(self, tmp_config):
        """Latest bill well below threshold never fires."""
        _add_months(tmp_config, "electric", [100, 100, 100, 100, 100, 100, 105])
        anomalies = evaluate_utility_anomalies(tmp_config)
        assert anomalies == []

    def test_baseline_excludes_latest_bill(self, tmp_config):
        """Baseline average must be computed from the 6 prior bills only,
        not smoothed by the spike candidate itself."""
        # Baseline (first 6) avg = 100. Latest = 200 (+100%).
        _add_months(tmp_config, "electric", [100, 100, 100, 100, 100, 100, 200])
        anomalies = evaluate_utility_anomalies(tmp_config)
        assert len(anomalies) == 1
        assert anomalies[0]["baseline_avg"] == 100.0
        assert anomalies[0]["latest_amount"] == 200

    def test_multiple_utilities_anomalous_at_once(self, tmp_config):
        """Multiple utility types can be flagged in a single evaluation."""
        _add_months(tmp_config, "electric", [100, 100, 100, 100, 100, 100, 200])
        _add_months(tmp_config, "water", [50, 50, 50, 50, 50, 50, 100])
        _add_months(tmp_config, "gas", [80, 80, 80, 80, 80, 80, 82])  # not anomalous

        anomalies = evaluate_utility_anomalies(tmp_config)
        flagged_types = {a["type"] for a in anomalies}
        assert flagged_types == {"electric", "water"}
        assert len(anomalies) == 2


class TestCreateReminderIfMissing:
    @patch("homeops.reminders.RemindersManager")
    def test_does_not_create_when_existing_match_found(self, mock_manager_cls, tmp_config):
        mock_manager = MagicMock()
        mock_manager.search_reminders.return_value = [{"id": "abc123", "title": "Utility anomaly: bill spike"}]
        mock_manager_cls.return_value = mock_manager

        result = create_reminder_if_missing(
            tmp_config,
            title="Utility anomaly: bill spike detected",
            search_query="utility anomaly",
            notes="electric: latest $200 vs baseline $100 (100% over)",
            due_date="2026-07-26",
            priority=5,
        )

        mock_manager.search_reminders.assert_called_once_with("utility anomaly", list_name="Personal")
        mock_manager.create_reminder.assert_not_called()
        assert result["created"] is False

    @patch("homeops.reminders.RemindersManager")
    def test_creates_with_expected_args_when_no_match(self, mock_manager_cls, tmp_config):
        mock_manager = MagicMock()
        mock_manager.search_reminders.return_value = []
        mock_manager.create_reminder.return_value = {"id": "new1", "title": "Utility anomaly: bill spike detected"}
        mock_manager_cls.return_value = mock_manager

        result = create_reminder_if_missing(
            tmp_config,
            title="Utility anomaly: bill spike detected",
            search_query="utility anomaly",
            notes="electric: latest $200 vs baseline $100 (100% over)",
            due_date="2026-07-26",
            priority=5,
        )

        mock_manager.create_reminder.assert_called_once_with(
            "Utility anomaly: bill spike detected",
            list_name="Personal",
            due_date="2026-07-26",
            due_time="10:00",
            notes="electric: latest $200 vs baseline $100 (100% over)",
            priority=5,
        )
        assert result["created"] is True
        assert result["reminder"]["id"] == "new1"

    @patch("homeops.reminders.RemindersManager")
    def test_uses_explicit_list_and_due_time_over_config_defaults(self, mock_manager_cls, tmp_config):
        mock_manager = MagicMock()
        mock_manager.search_reminders.return_value = []
        mock_manager.create_reminder.return_value = {"id": "new2"}
        mock_manager_cls.return_value = mock_manager

        create_reminder_if_missing(
            tmp_config,
            title="Some title",
            search_query="some query",
            list_name="Work",
            due_time="08:00",
        )

        mock_manager.search_reminders.assert_called_once_with("some query", list_name="Work")
        mock_manager.create_reminder.assert_called_once_with(
            "Some title",
            list_name="Work",
            due_date=None,
            due_time="08:00",
            notes=None,
            priority=0,
        )
