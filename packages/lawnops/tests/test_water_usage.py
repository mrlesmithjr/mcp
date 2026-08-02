"""Tests for get_water_usage_report deriving cost from the authoritative
config cost-per-minute instead of a YNAB water-bill regression (issue #44).

`_get_water_bills` and `_get_irrigation_monthly` are mocked so these tests
exercise `get_water_usage_report`'s own cost logic directly, independent of
SQLite or a YNAB database.
"""

from unittest.mock import patch

import pytest
from lawnops.db.water_usage import get_water_usage_report


@pytest.fixture()
def tmp_config(tmp_path):
    db_path = tmp_path / "lawnops.db"
    return {
        "database": {"path": str(db_path)},
        "hydrawise": {"budget": {"cost_per_minute": 0.20}},
    }


@patch("lawnops.db.water_usage._get_irrigation_monthly")
@patch("lawnops.db.water_usage._get_water_bills")
def test_lagged_zero_bill_month_gets_nonzero_cpm_based_cost(mock_bills, mock_irrigation, tmp_config):
    """The motivating bug: a month with real runtime but a $0 (billing-lag)
    bill must not get $0.00 estimated_irrigation_cost -- it should be
    minutes * cpm, same as any other month."""
    mock_bills.return_value = {"2026-02": 80.0, "2026-03": 0.0}
    mock_irrigation.return_value = {
        "2026-02": {"minutes": 0, "runs": 0, "estimated_gallons": 0.0},
        "2026-03": {"minutes": 500, "runs": 10, "estimated_gallons": 1250.0},
    }

    result = get_water_usage_report(tmp_config, 2026)

    march = next(m for m in result["months"] if m["month"] == "2026-03")
    assert march["water_bill"] == 0.0
    assert march["estimated_irrigation_cost"] == pytest.approx(500 * 0.20)
    assert march["estimated_irrigation_cost"] != 0
    assert march["cost_per_minute"] == 0.20


@patch("lawnops.db.water_usage._get_irrigation_monthly")
@patch("lawnops.db.water_usage._get_water_bills")
def test_missing_bill_month_also_gets_nonzero_cpm_based_cost(mock_bills, mock_irrigation, tmp_config):
    """Same billing-lag scenario, but the month has no bill entry at all
    (rather than an explicit $0) -- cost still must not depend on it."""
    mock_bills.return_value = {"2026-02": 80.0}
    mock_irrigation.return_value = {
        "2026-02": {"minutes": 0, "runs": 0, "estimated_gallons": 0.0},
        "2026-04": {"minutes": 300, "runs": 6, "estimated_gallons": 750.0},
    }

    result = get_water_usage_report(tmp_config, 2026)

    april = next(m for m in result["months"] if m["month"] == "2026-04")
    assert april["water_bill"] is None
    assert april["estimated_irrigation_cost"] == pytest.approx(300 * 0.20)
    assert april["cost_per_minute"] == 0.20


@patch("lawnops.db.water_usage._get_irrigation_monthly")
@patch("lawnops.db.water_usage._get_water_bills")
def test_every_runtime_month_uses_config_cpm_consistently(mock_bills, mock_irrigation, tmp_config):
    """Per-month cost_per_minute and the report-level avg_cost_per_minute must
    all equal the single configured cpm, and cpm_source must read "config"."""
    mock_bills.return_value = {"2026-02": 80.0}
    mock_irrigation.return_value = {
        "2026-02": {"minutes": 0, "runs": 0, "estimated_gallons": 0.0},
        "2026-03": {"minutes": 500, "runs": 10, "estimated_gallons": 1250.0},
        "2026-04": {"minutes": 300, "runs": 6, "estimated_gallons": 750.0},
    }

    result = get_water_usage_report(tmp_config, 2026)

    assert result["cpm_source"] == "config"
    assert result["avg_cost_per_minute"] == 0.20

    runtime_months = [m for m in result["months"] if m["irrigation_minutes"] > 0]
    assert runtime_months, "expected at least one month with runtime"
    for m in runtime_months:
        assert m["cost_per_minute"] == 0.20
        assert m["estimated_irrigation_cost"] == round(m["irrigation_minutes"] * 0.20, 2)


@patch("lawnops.db.water_usage._get_irrigation_monthly")
@patch("lawnops.db.water_usage._get_water_bills")
def test_zero_runtime_month_is_baseline_with_null_cost(mock_bills, mock_irrigation, tmp_config):
    """A zero-runtime month is flagged is_baseline with no cost figures --
    not an error, and not zeroed-out irrigation cost."""
    mock_bills.return_value = {"2026-02": 80.0}
    mock_irrigation.return_value = {
        "2026-02": {"minutes": 0, "runs": 0, "estimated_gallons": 0.0},
    }

    result = get_water_usage_report(tmp_config, 2026)

    feb = next(m for m in result["months"] if m["month"] == "2026-02")
    assert feb["is_baseline"] is True
    assert feb["estimated_irrigation_cost"] is None
    assert feb["cost_per_minute"] is None
