"""Tests for coordinator.rules, particularly the treatment staleness guard.

_handle_treatment's irrigation suspend is time-sensitive (24h window to
avoid product wash-off); the product-inventory/reorder check is not.
suspend_irrigation and reminders.* touch live Hydrawise/EventKit APIs, so
they are mocked throughout -- these are pure rule-logic unit tests.
"""

from datetime import date, timedelta
from unittest.mock import patch

import coordinator.rules as rules


def _treatment_payload(days_old=0, product="TestProduct", area="Front Lawn"):
    treatment_date = (date.today() - timedelta(days=days_old)).isoformat()
    return {"product": product, "area": area, "date": treatment_date}


class TestHandleTreatmentStaleness:
    @patch("coordinator.lawnops_tools.get_product", return_value=None)
    @patch("coordinator.lawnops_tools.suspend_irrigation", return_value=("2026-07-03T12:00:00", None))
    def test_fresh_treatment_still_suspends(self, mock_suspend, mock_get_product):
        payload = _treatment_payload(days_old=0)

        result = rules._handle_treatment(payload)

        mock_suspend.assert_called_once_with(24)
        assert any("irrigation suspended until" in s for s in result["steps"])
        assert result["action"] == "treatment_handled"

    @patch("coordinator.lawnops_tools.get_product")
    @patch("coordinator.lawnops_tools.suspend_irrigation")
    @patch("coordinator.reminders.search", return_value=[])
    @patch("coordinator.reminders.create")
    def test_stale_treatment_skips_suspend_but_still_checks_inventory(
        self, mock_create, mock_search, mock_suspend, mock_get_product
    ):
        mock_get_product.return_value = {"name": "TestProduct", "qty_on_hand": 0, "unit": "bag"}
        # 19 days old mirrors the real stale event (id 32) sitting in the live events.db.
        payload = _treatment_payload(days_old=19)

        result = rules._handle_treatment(payload)

        mock_suspend.assert_not_called()
        assert any("irrigation suspend skipped" in s for s in result["steps"])
        assert any("19 days old" in s for s in result["steps"])
        # Inventory check is not time-sensitive and still runs.
        mock_get_product.assert_called_once_with("TestProduct")
        mock_create.assert_called_once()

    @patch("coordinator.lawnops_tools.get_product", return_value=None)
    @patch("coordinator.lawnops_tools.suspend_irrigation", return_value=("2026-07-03T12:00:00", None))
    def test_malformed_date_falls_through_to_suspend_without_crashing(self, mock_suspend, mock_get_product):
        payload = {"product": "TestProduct", "area": "Front Lawn", "date": "not-a-date"}

        result = rules._handle_treatment(payload)

        mock_suspend.assert_called_once_with(24)
        assert any("irrigation suspended until" in s for s in result["steps"])

    @patch("coordinator.lawnops_tools.get_product", return_value=None)
    @patch("coordinator.lawnops_tools.suspend_irrigation", return_value=("2026-07-03T12:00:00", None))
    def test_missing_date_falls_through_to_suspend_without_crashing(self, mock_suspend, mock_get_product):
        payload = {"product": "TestProduct", "area": "Front Lawn"}

        result = rules._handle_treatment(payload)

        mock_suspend.assert_called_once_with(24)
        assert any("irrigation suspended until" in s for s in result["steps"])


class TestHandleTreatmentSuspendRetry:
    """suspend_irrigation retry-with-backoff (issue #74)."""

    @patch("coordinator.rules.time.sleep")
    @patch("coordinator.lawnops_tools.get_product", return_value=None)
    @patch("coordinator.lawnops_tools.suspend_irrigation")
    def test_transient_failure_then_success_retries_and_succeeds(self, mock_suspend, mock_get_product, mock_sleep):
        mock_suspend.side_effect = [RuntimeError("network blip"), ("2026-07-03T12:00:00", None)]
        payload = _treatment_payload(days_old=0)

        result = rules._handle_treatment(payload)

        assert mock_suspend.call_count == 2
        mock_sleep.assert_called_once()
        assert any("irrigation suspended until" in s for s in result["steps"])
        assert not any("irrigation suspend failed" in s for s in result["steps"])

    @patch("coordinator.rules.time.sleep")
    @patch("coordinator.lawnops_tools.get_product", return_value=None)
    @patch("coordinator.lawnops_tools.suspend_irrigation")
    def test_persistent_failure_exhausts_retries_logs_and_does_not_crash(
        self, mock_suspend, mock_get_product, mock_sleep
    ):
        mock_suspend.side_effect = RuntimeError("hydrawise down")
        payload = _treatment_payload(days_old=0)

        result = rules._handle_treatment(payload)

        assert mock_suspend.call_count == rules.SUSPEND_IRRIGATION_MAX_ATTEMPTS
        assert mock_sleep.call_count == rules.SUSPEND_IRRIGATION_MAX_ATTEMPTS - 1
        assert result["action"] == "treatment_handled"
        assert any("irrigation suspend failed after" in s for s in result["steps"])
        # Refs #74: the final-failure message must include the exception type
        # (RuntimeError), not just str(e), so a bare "hydrawise down" log line
        # doesn't lose which exception class actually failed.
        assert any("RuntimeError: hydrawise down" in s for s in result["steps"])


class TestTreatmentAgeIsStale:
    def test_fresh_date_is_not_stale(self):
        is_stale, age_days = rules._treatment_age_is_stale(date.today().isoformat())
        assert is_stale is False
        assert age_days == 0

    def test_19_day_old_date_is_stale(self):
        old_date = (date.today() - timedelta(days=19)).isoformat()
        is_stale, age_days = rules._treatment_age_is_stale(old_date)
        assert is_stale is True
        assert age_days == 19

    def test_boundary_at_exactly_stale_days_is_not_stale(self):
        boundary_date = (date.today() - timedelta(days=rules.TREATMENT_STALE_DAYS)).isoformat()
        is_stale, _ = rules._treatment_age_is_stale(boundary_date)
        assert is_stale is False

    def test_malformed_date_is_not_stale_with_none_age(self):
        is_stale, age_days = rules._treatment_age_is_stale("not-a-date")
        assert is_stale is False
        assert age_days is None

    def test_missing_date_is_not_stale_with_none_age(self):
        is_stale, age_days = rules._treatment_age_is_stale("")
        assert is_stale is False
        assert age_days is None


class TestCheckIrrigationBudget:
    """check_irrigation_budget's alert/dedup logic (refs #141)."""

    @staticmethod
    def _status(budget_status, **overrides):
        base = {
            "budget_status": budget_status,
            "pct_used": 82.5,
            "remaining": 10.0,
            "projected_cost": 190.0,
            "monthly_dollars_limit": 175,
        }
        base.update(overrides)
        return base

    @patch("coordinator.reminders.create")
    @patch("coordinator.reminders.search")
    @patch("coordinator.lawnops_tools.get_irrigation_budget_status")
    def test_ok_status_is_silent(self, mock_get_status, mock_search, mock_create):
        mock_get_status.return_value = self._status("ok")

        result = rules.check_irrigation_budget()

        assert result["action"] == "no_op"
        mock_search.assert_not_called()
        mock_create.assert_not_called()

    @patch("coordinator.reminders.create")
    @patch("coordinator.reminders.search")
    @patch("coordinator.lawnops_tools.get_irrigation_budget_status")
    def test_no_budget_status_is_silent(self, mock_get_status, mock_search, mock_create):
        mock_get_status.return_value = self._status("no_budget", pct_used=None, remaining=None)

        result = rules.check_irrigation_budget()

        assert result["action"] == "no_op"
        mock_search.assert_not_called()
        mock_create.assert_not_called()

    @patch("coordinator.reminders.create")
    @patch("coordinator.reminders.search", return_value=[])
    @patch("coordinator.lawnops_tools.get_irrigation_budget_status")
    def test_warning_status_creates_reminder(self, mock_get_status, mock_search, mock_create):
        mock_get_status.return_value = self._status("warning")

        result = rules.check_irrigation_budget()

        mock_search.assert_called_once_with("Irrigation budget")
        mock_create.assert_called_once()
        _, kwargs = mock_create.call_args
        assert kwargs["priority"] == 5
        assert "82.5" in kwargs["title"]
        assert result["action"] == "reminder_created"

    @patch("coordinator.reminders.create")
    @patch("coordinator.reminders.search", return_value=[])
    @patch("coordinator.lawnops_tools.get_irrigation_budget_status")
    def test_over_status_creates_high_priority_reminder(self, mock_get_status, mock_search, mock_create):
        mock_get_status.return_value = self._status("over", pct_used=104.0)

        result = rules.check_irrigation_budget()

        mock_create.assert_called_once()
        _, kwargs = mock_create.call_args
        assert kwargs["priority"] == 1
        assert result["budget_status"] == "over"

    @patch("coordinator.reminders.create")
    @patch("coordinator.reminders.search")
    @patch("coordinator.lawnops_tools.get_irrigation_budget_status")
    def test_existing_reminder_dedups_no_new_reminder(self, mock_get_status, mock_search, mock_create):
        mock_get_status.return_value = self._status("warning")
        mock_search.return_value = [{"id": "abc", "title": "Irrigation budget warning: 82.5% used"}]

        result = rules.check_irrigation_budget()

        mock_create.assert_not_called()
        assert result["action"] == "no_op"
