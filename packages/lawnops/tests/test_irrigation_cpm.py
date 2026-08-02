"""Tests for config-authoritative cost-per-minute (issue #42).

`hydrawise.budget.cost_per_minute` must be authoritative for cost projections
whenever it is set in config: `irrigation_budget`, `irrigation_pace`, and the
ET/cost projections should use it directly and report `cpm_source: "config"`.
The bill-derived weighted CPM is retained only as a diagnostic
`observed_cpm_from_bills` field and must never feed a cost projection.
"""

from unittest.mock import patch

import pytest
from lawnops.db.irrigation_analytics import _compute_dynamic_cpm, irrigation_budget, irrigation_pace


def _usage_report(months):
    return {"months": months, "baseline_avg": None, "avg_cost_per_minute": None}


def _qualifying_months(cpm=0.10, count=3):
    """Three qualifying bill months (irrigation_minutes > 30, not current month)."""
    return [{"month": f"2025-{m:02d}", "cost_per_minute": cpm, "irrigation_minutes": 500} for m in range(1, count + 1)]


@pytest.fixture()
def tmp_config(tmp_path):
    db_path = tmp_path / "lawnops.db"
    return {"database": {"path": str(db_path)}, "hydrawise": {"budget": {}}}


class TestComputeDynamicCpm:
    @patch("lawnops.db.irrigation_analytics.get_water_usage_report")
    def test_config_value_is_authoritative_over_bills(self, mock_usage, tmp_config):
        tmp_config["hydrawise"]["budget"]["cost_per_minute"] = 0.25
        mock_usage.return_value = _usage_report(_qualifying_months(cpm=0.10))

        result = _compute_dynamic_cpm(tmp_config)

        assert result["cpm"] == 0.25
        assert result["source"] == "config"
        # Bill-derived value is surfaced only as a diagnostic, and differs from
        # the authoritative config value - proving it was not blended in.
        assert result["observed_cpm_from_bills"] == 0.10

    @patch("lawnops.db.irrigation_analytics.get_water_usage_report")
    def test_config_value_authoritative_even_with_insufficient_bills(self, mock_usage, tmp_config):
        tmp_config["hydrawise"]["budget"]["cost_per_minute"] = 0.25
        mock_usage.return_value = _usage_report([])

        result = _compute_dynamic_cpm(tmp_config)

        assert result["cpm"] == 0.25
        assert result["source"] == "config"
        assert result["observed_cpm_from_bills"] is None

    @patch("lawnops.db.irrigation_analytics.get_water_usage_report")
    def test_falls_back_to_bills_when_config_absent(self, mock_usage, tmp_config):
        # No cost_per_minute key at all under hydrawise.budget.
        mock_usage.return_value = _usage_report(_qualifying_months(cpm=0.10))

        result = _compute_dynamic_cpm(tmp_config)

        assert result["source"] == "computed"
        assert result["cpm"] == 0.10
        assert result["observed_cpm_from_bills"] == 0.10

    @patch("lawnops.db.irrigation_analytics.get_water_usage_report")
    def test_falls_back_to_hardcoded_default_when_neither_available(self, mock_usage, tmp_config):
        mock_usage.return_value = _usage_report([])

        result = _compute_dynamic_cpm(tmp_config)

        assert result["source"] == "default"
        assert result["cpm"] == 0.12
        assert result["observed_cpm_from_bills"] is None

    @patch("lawnops.db.irrigation_analytics.get_water_usage_report")
    def test_zero_config_value_is_rejected_falls_back_to_computed(self, mock_usage, tmp_config):
        tmp_config["hydrawise"]["budget"]["cost_per_minute"] = 0
        mock_usage.return_value = _usage_report(_qualifying_months(cpm=0.10))

        result = _compute_dynamic_cpm(tmp_config)

        assert result["source"] == "computed"
        assert result["cpm"] == 0.10
        assert result["warning"] is not None
        assert "cost_per_minute" in result["warning"]

    @patch("lawnops.db.irrigation_analytics.get_water_usage_report")
    def test_zero_config_value_is_rejected_falls_back_to_default(self, mock_usage, tmp_config):
        tmp_config["hydrawise"]["budget"]["cost_per_minute"] = 0
        mock_usage.return_value = _usage_report([])

        result = _compute_dynamic_cpm(tmp_config)

        assert result["source"] == "default"
        assert result["cpm"] == 0.12
        assert result["warning"] is not None
        assert "cost_per_minute" in result["warning"]

    @patch("lawnops.db.irrigation_analytics.get_water_usage_report")
    def test_negative_config_value_is_rejected_falls_back_to_computed(self, mock_usage, tmp_config):
        tmp_config["hydrawise"]["budget"]["cost_per_minute"] = -0.05
        mock_usage.return_value = _usage_report(_qualifying_months(cpm=0.10))

        result = _compute_dynamic_cpm(tmp_config)

        assert result["source"] == "computed"
        assert result["cpm"] == 0.10
        assert result["warning"] is not None
        assert "cost_per_minute" in result["warning"]

    @patch("lawnops.db.irrigation_analytics.get_water_usage_report")
    def test_negative_config_value_is_rejected_falls_back_to_default(self, mock_usage, tmp_config):
        tmp_config["hydrawise"]["budget"]["cost_per_minute"] = -0.05
        mock_usage.return_value = _usage_report([])

        result = _compute_dynamic_cpm(tmp_config)

        assert result["source"] == "default"
        assert result["cpm"] == 0.12
        assert result["warning"] is not None
        assert "cost_per_minute" in result["warning"]

    @patch("lawnops.db.irrigation_analytics.get_water_usage_report")
    def test_config_value_survives_bill_lookup_failure(self, mock_usage, tmp_config):
        tmp_config["hydrawise"]["budget"]["cost_per_minute"] = 0.25
        mock_usage.side_effect = RuntimeError("YNAB database not found")

        result = _compute_dynamic_cpm(tmp_config)

        assert result["cpm"] == 0.25
        assert result["source"] == "config"
        assert result["observed_cpm_from_bills"] is None
        assert result["warning"] == "YNAB database not found"


class TestIrrigationPaceAndBudgetUseAuthoritativeCpm:
    @patch("lawnops.db.irrigation_analytics.get_water_usage_report")
    @patch("lawnops.db.irrigation_analytics._get_water_bills")
    def test_irrigation_pace_uses_config_cpm(self, mock_bills, mock_usage, tmp_config):
        tmp_config["hydrawise"]["budget"]["cost_per_minute"] = 0.25
        mock_usage.return_value = _usage_report(_qualifying_months(cpm=0.10))
        mock_bills.return_value = {}

        result = irrigation_pace(tmp_config)

        assert result["cpm_source"] == "config"
        assert result["thresholds"]["cost_per_minute"] == 0.25

    @patch("lawnops.db.irrigation_analytics.get_water_usage_report")
    @patch("lawnops.db.irrigation_analytics._get_water_bills")
    def test_irrigation_budget_uses_config_cpm(self, mock_bills, mock_usage, tmp_config):
        tmp_config["hydrawise"]["budget"]["cost_per_minute"] = 0.25
        mock_usage.return_value = _usage_report(_qualifying_months(cpm=0.10))
        mock_bills.return_value = {}

        result = irrigation_budget(tmp_config)

        assert result["cpm_source"] == "config"
        assert result["cost_per_minute"] == 0.25
        assert result["observed_cpm_from_bills"] == 0.10
