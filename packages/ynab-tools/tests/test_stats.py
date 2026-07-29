"""Tests for ynab_tools/stats.py - edge cases and new shared functions."""

import sqlite3

from ynab_tools.stats import (
    category_zscore,
    category_zscore_by_name,
    parse_category_input,
    recommend_target,
    should_skip_group,
    spending_stats,
    zscore_vs_history,
)


def _make_db() -> sqlite3.Connection:
    """In-memory DB with the budget_categories schema subset needed for z-score tests."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE budget_categories (
            id TEXT NOT NULL,
            budget_month TEXT NOT NULL,
            name TEXT NOT NULL,
            activity REAL,
            deleted INTEGER DEFAULT 0,
            hidden INTEGER DEFAULT 0,
            PRIMARY KEY (id, budget_month)
        )
        """
    )
    return conn


class TestCategoryZscoreHiddenFilter:
    """Regression tests: hidden categories must not be excluded from z-score history.

    The INSERT OR REPLACE sync pattern writes the current hidden flag to all
    historical rows. A category hidden after the fact (e.g. paid-off loan) must
    still contribute its historical activity to the baseline.
    """

    def _insert_months(self, conn, cat_id, name, months_activity: dict, hidden: int = 0):
        for month, activity in months_activity.items():
            conn.execute(
                "INSERT INTO budget_categories"
                " (id, budget_month, name, activity, deleted, hidden) VALUES (?,?,?,?,0,?)",
                (cat_id, month, name, activity, hidden),
            )

    def test_hidden_category_included_in_zscore_by_id(self):
        conn = _make_db()
        data = {
            "2025-06-01": -100.0,
            "2025-07-01": -105.0,
            "2025-08-01": -98.0,
            "2025-09-01": -102.0,
            "2025-10-01": -99.0,
            "2025-11-01": -101.0,
            "2025-12-01": -103.0,
            "2026-01-01": -100.0,
            "2026-02-01": -104.0,
            "2026-03-01": -97.0,
            "2026-04-01": -102.0,
            "2026-05-01": -300.0,  # current month spike
        }
        # All rows hidden=1 simulating post-payoff sync propagation
        self._insert_months(conn, "cat-1", "Auto: Explorer Payment", data, hidden=1)

        result = category_zscore(conn, "cat-1", "2026-05-01")
        assert result is not None, "hidden category must not be excluded from z-score"
        assert result > 2.0, "spike month should produce high z-score"

    def test_hidden_category_included_in_zscore_by_name(self):
        conn = _make_db()
        data = {
            "2025-06-01": -100.0,
            "2025-07-01": -105.0,
            "2025-08-01": -98.0,
            "2025-09-01": -102.0,
            "2025-10-01": -99.0,
            "2025-11-01": -101.0,
            "2025-12-01": -103.0,
            "2026-01-01": -100.0,
            "2026-02-01": -104.0,
            "2026-03-01": -97.0,
            "2026-04-01": -102.0,
            "2026-05-01": -300.0,
        }
        self._insert_months(conn, "cat-1", "Auto: Explorer Payment", data, hidden=1)

        result = category_zscore_by_name(conn, "Auto: Explorer Payment", "2026-05-01")
        assert result is not None, "hidden category must not be excluded from z-score"
        assert result > 2.0

    def test_deleted_category_excluded_from_zscore(self):
        """deleted=1 rows must still be excluded regardless of the hidden fix."""
        conn = _make_db()
        data = {
            m: -100.0
            for m in [
                "2025-06-01",
                "2025-07-01",
                "2025-08-01",
                "2025-09-01",
                "2025-10-01",
                "2025-11-01",
                "2025-12-01",
                "2026-01-01",
                "2026-02-01",
                "2026-03-01",
                "2026-04-01",
                "2026-05-01",
            ]
        }
        for month, activity in data.items():
            conn.execute(
                "INSERT INTO budget_categories"
                " (id, budget_month, name, activity, deleted, hidden) VALUES (?,?,?,?,1,0)",
                ("cat-del", month, "Deleted Category", activity),
            )
        result = category_zscore(conn, "cat-del", "2026-05-01")
        # Deleted rows are excluded; missing months fill to 0.0; std dev = 0 → 0.0 not None.
        assert result == 0.0, "deleted rows produce all-zero history, zscore_vs_history returns 0.0"


class TestSpendingStatsEdgeCases:
    def test_all_zero_list_returns_insufficient_data(self):
        amounts = [0.0] * 12
        result = spending_stats(amounts)
        assert result["pattern"] == "insufficient_data"
        assert result["months"] == 0
        assert result["total_months"] == 12
        assert result["lumpy"] is False

    def test_all_zero_avg_is_zero(self):
        amounts = [0.0] * 12
        result = spending_stats(amounts)
        assert result["avg"] == 0.0
        assert result["trimmed_avg"] == 0.0
        assert result["std_dev"] == 0.0
        assert result["cv"] == 0.0

    def test_exactly_three_nonzero_months_gives_recommendation(self):
        # 3 months is the boundary - recommend_target should return a value (not None)
        amounts = [100.0, 120.0, 110.0] + [0.0] * 9
        stats = spending_stats(amounts)
        # lumpy: 9 zero months > 12 * 0.25=3 AND 3 nonzero >= 3 → lumpy=True
        assert stats["lumpy"] is True
        assert stats["pattern"] == "lumpy"
        recommended, note = recommend_target(stats)
        assert recommended is not None
        assert "lumpy" in note

    def test_three_nonzero_no_zeros_gives_recommendation(self):
        # Exactly 3 nonzero months, no zeros - consistent/moderate_variance
        amounts = [100.0, 110.0, 105.0]
        stats = spending_stats(amounts)
        assert stats["months"] == 3
        assert stats["lumpy"] is False
        recommended, note = recommend_target(stats)
        assert recommended is not None

    def test_single_nonzero_month_many_zeros(self):
        # [100, 0, 0, ...] - only 1 nonzero, so lumpy=False (needs >=3 nonzero)
        # With 1 nonzero month: cv=0 (no std dev), so pattern="consistent"
        # but recommend_target returns None because months < 3
        amounts = [100.0] + [0.0] * 11
        result = spending_stats(amounts)
        assert result["months"] == 1  # only 1 nonzero
        assert result["total_months"] == 12
        assert result["lumpy"] is False  # needs >=3 nonzero for lumpy
        # 1 nonzero with cv=0 → consistent (not insufficient_data; that only triggers
        # when there are zero nonzero months in a non-lumpy path)
        assert result["pattern"] == "consistent"

    def test_single_nonzero_recommend_target_returns_none(self):
        amounts = [100.0] + [0.0] * 11
        stats = spending_stats(amounts)
        recommended, note = recommend_target(stats)
        assert recommended is None
        assert "insufficient" in note

    def test_empty_list(self):
        result = spending_stats([])
        assert result["pattern"] == "insufficient_data"
        assert result["months"] == 0
        assert result["total_months"] == 0

    def test_lumpy_requires_three_nonzero(self):
        # 2 nonzero with many zeros: lumpy=False even if >25% zero
        amounts = [100.0, 200.0] + [0.0] * 10
        result = spending_stats(amounts)
        assert result["lumpy"] is False

    def test_lumpy_true_with_enough_nonzero(self):
        # >25% zeros (4 of 12) and >=3 nonzero (8)
        amounts = [100.0] * 8 + [0.0] * 4
        result = spending_stats(amounts)
        assert result["lumpy"] is True
        assert result["pattern"] == "lumpy"


class TestShouldSkipGroup:
    def test_credit_card_payments(self):
        assert should_skip_group("Credit Card Payments") is True

    def test_internal_master_category(self):
        assert should_skip_group("Internal Master Category") is True

    def test_income_exact(self):
        assert should_skip_group("Income") is True

    def test_income_case_insensitive_substring(self):
        assert should_skip_group("Monthly Income") is True
        assert should_skip_group("income for taxes") is True
        assert should_skip_group("INCOME") is True

    def test_holding_case_insensitive_substring(self):
        assert should_skip_group("Holding Account") is True
        assert should_skip_group("holding") is True
        assert should_skip_group("Tax Holding") is True

    def test_bills_not_skipped(self):
        assert should_skip_group("Bills") is False

    def test_groceries_not_skipped(self):
        assert should_skip_group("Groceries") is False

    def test_empty_string_not_skipped(self):
        assert should_skip_group("") is False

    def test_savings_not_skipped(self):
        assert should_skip_group("Savings") is False

    def test_user_excluded_group_skipped(self, monkeypatch):
        monkeypatch.setenv("YNAB_EXCLUDED_GROUPS", "Business - Methodical Cloud,Business - Quirkywerks")
        assert should_skip_group("Business - Methodical Cloud") is True
        assert should_skip_group("Business - Quirkywerks") is True

    def test_user_excluded_group_not_in_env_not_skipped(self, monkeypatch):
        monkeypatch.delenv("YNAB_EXCLUDED_GROUPS", raising=False)
        assert should_skip_group("Business - Methodical Cloud") is False

    def test_user_excluded_group_whitespace_trimmed(self, monkeypatch):
        monkeypatch.setenv("YNAB_EXCLUDED_GROUPS", " Business - Quirkywerks , Savings ")
        assert should_skip_group("Business - Quirkywerks") is True
        assert should_skip_group("Savings") is True


class TestParseCategoryInput:
    def test_plain_name_returns_none_group(self):
        name, group = parse_category_input("Groceries")
        assert name == "Groceries"
        assert group is None

    def test_colon_format(self):
        name, group = parse_category_input("Bills: Rent")
        assert name == "Rent"
        assert group == "Bills"

    def test_paren_format(self):
        name, group = parse_category_input("Rent (Bills)")
        assert name == "Rent"
        assert group == "Bills"

    def test_paren_format_ampersand(self):
        name, group = parse_category_input("Licenses & Fees (Business)")
        assert name == "Licenses & Fees"
        assert group == "Business"

    def test_colon_format_ampersand(self):
        name, group = parse_category_input("Business: Licenses & Fees")
        assert name == "Licenses & Fees"
        assert group == "Business"

    def test_spaces_in_name_no_colon_or_paren(self):
        name, group = parse_category_input("Category with spaces")
        assert name == "Category with spaces"
        assert group is None

    def test_colon_strips_whitespace(self):
        name, group = parse_category_input("Bills:Rent")
        assert name == "Rent"
        assert group == "Bills"


class TestZscoreVsHistory:
    def test_insufficient_data_returns_none_with_less_than_5(self):
        assert zscore_vs_history([]) is None
        assert zscore_vs_history([100.0]) is None
        assert zscore_vs_history([100.0, 110.0]) is None
        assert zscore_vs_history([100.0, 110.0, 105.0]) is None
        assert zscore_vs_history([100.0, 110.0, 105.0, 108.0]) is None

    def test_exactly_5_points_returns_float(self):
        result = zscore_vs_history([100.0, 100.0, 100.0, 100.0, 150.0])
        assert result is not None
        assert isinstance(result, float)

    def test_normal_spending_low_zscore(self):
        # 4 prior months around 100, current month = 102 (within baseline)
        # z-score is positive but below the 2.0 anomaly threshold
        result = zscore_vs_history([100.0, 98.0, 102.0, 100.0, 102.0])
        assert result is not None
        assert result < 2.0

    def test_single_outlier_high_zscore(self):
        # 4 prior months tightly around 100, current = 300 (large spike)
        result = zscore_vs_history([100.0, 100.0, 101.0, 99.0, 300.0])
        assert result is not None
        assert result > 2.0

    def test_std_zero_returns_zero(self):
        # All prior months identical - std dev = 0, return 0.0
        result = zscore_vs_history([100.0, 100.0, 100.0, 100.0, 100.0])
        assert result == 0.0

    def test_all_zero_history_returns_zero(self):
        # All zeros: std dev = 0 → return 0.0
        result = zscore_vs_history([0.0, 0.0, 0.0, 0.0, 0.0])
        assert result == 0.0

    def test_current_below_baseline_negative_zscore(self):
        # Current month much lower than baseline - negative z-score
        # Prior history has variance so std dev > 0
        result = zscore_vs_history([195.0, 205.0, 200.0, 198.0, 50.0])
        assert result is not None
        assert result < 0.0

    def test_result_is_symmetric_and_scaled(self):
        # Verify z-score formula: z = (x - mean) / std
        import numpy as np

        history = [100.0, 110.0, 90.0, 105.0]
        current = 200.0
        arr = np.array(history)
        expected = (current - float(np.mean(arr))) / float(np.std(arr, ddof=1))
        result = zscore_vs_history(history + [current])
        assert result is not None
        assert abs(result - expected) < 1e-9
