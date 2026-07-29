"""Tests for Tier 4 daily-spend bridge logic (issue #209).

Guards against two bugs fixed in #209:
1. Daily-spend categories in Tier 3 (with a NEED goal shortfall) were being
   added to seen_ids and silently skipped from Tier 4 bridge detection. A
   category like Auto: Fuel that hits $0 mid-month only got its small goal
   shortfall funded, not the full bridge needed to reach the next paycheck.

2. The frequency-based bridge detector captured lumpy event-driven categories
   (Gifts & Holidays, Vacations) that had high transaction counts during a
   spending spike. These are not true daily-spend categories and produce
   inflated, misleading bridge amounts.

The fixes:
- _get_daily_spending_categories() now applies a consistency filter (spend in
  at least 2 of 3 lookback months) and a lumpiness filter (max monthly spend
  must not exceed 2.5x the average monthly spend).
- generate_funding_plan() now applies a bridge top-up to Tier 3 items that
  are also daily-spend categories: when the post-goal-funding balance would
  still be below the bridge target, the delta is added to amount_needed.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from ynab_tools.db import get_connection, init_db


def _current_month() -> str:
    return datetime.now().strftime("%Y-%m-01")


def _insert_month(conn, month: str, rta: float = 500.0) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO budget_months
           (month, income, budgeted, activity, to_be_budgeted, age_of_money, last_synced_at)
           VALUES (?, 5000, 4500, -4000, ?, 30, ?)""",
        (month, rta, datetime.now().isoformat()),
    )


def _insert_category(
    conn,
    cat_id: str,
    month: str,
    name: str,
    group: str,
    budgeted: float,
    balance: float,
    goal_type: str | None = None,
    goal_under_funded: float = 0.0,
) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO budget_categories
           (id, budget_month, category_group_name, name, hidden, deleted,
            budgeted, activity, balance,
            goal_type, goal_target, goal_under_funded)
           VALUES (?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?)""",
        (
            cat_id,
            month,
            group,
            name,
            budgeted,
            balance - budgeted,
            balance,
            goal_type,
            goal_type and 100.0 or None,
            goal_under_funded,
        ),
    )


def _insert_transactions(
    conn,
    category_name: str,
    dates_amounts: list[tuple[str, float]],
) -> None:
    """Insert spending transactions for a category. amounts should be negative."""
    for i, (txn_date, amount) in enumerate(dates_amounts):
        conn.execute(
            """INSERT OR IGNORE INTO transactions
               (id, date, amount, cleared, approved, account_id, account_name,
                category_name, deleted)
               VALUES (?, ?, ?, 'cleared', 1, 'acct1', 'Checking', ?, 0)""",
            (f"txn-{category_name}-{i}", txn_date, amount, category_name),
        )


def _days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).strftime("%Y-%m-%d")


# ── lumpiness filter ──────────────────────────────────────────────────────────


_INSERT_ACCOUNT = (
    "INSERT OR IGNORE INTO accounts"
    " (id, name, type, on_budget, closed, deleted, balance)"
    " VALUES ('acct1', 'Checking', 'checking', 1, 0, 0, 5000)"
)


class TestDailySpendingCategoryDetection:
    def _build_db(self, tmp_path: Path) -> Path:
        db_path = tmp_path / "bridge_test.db"
        conn = get_connection(db_path)
        init_db(conn)
        conn.execute(_INSERT_ACCOUNT)
        conn.commit()
        conn.close()
        return db_path

    def _add_regular_txns(self, conn, cat_name: str, count_per_month: int = 10) -> None:
        """Add evenly-spread transactions across 3 months with consistent amounts."""
        for month_offset in range(3):
            for txn_num in range(count_per_month):
                day_offset = month_offset * 30 + txn_num * 2
                txn_date = _days_ago(day_offset + 1)
                conn.execute(
                    """INSERT OR IGNORE INTO transactions
                       (id, date, amount, cleared, approved, account_id, account_name,
                        category_name, deleted)
                       VALUES (?, ?, -50.0, 'cleared', 1, 'acct1', 'Checking', ?, 0)""",
                    (f"txn-{cat_name}-{month_offset}-{txn_num}", txn_date, cat_name),
                )

    def test_consistent_category_is_detected(self, tmp_path):
        db_path = self._build_db(tmp_path)
        conn = get_connection(db_path)
        init_db(conn)
        self._add_regular_txns(conn, "Groceries")
        conn.commit()

        from ynab_tools.reports.paycheck_funding import _get_daily_spending_categories

        result = _get_daily_spending_categories(conn)
        conn.close()

        assert "Groceries" in result
        assert result["Groceries"] > 0

    def test_lumpy_category_is_excluded(self, tmp_path):
        """A category with a spending spike > 2.5x average must be excluded.

        Construction rationale:
        - 10 txns/month across 3 months → avg_monthly_txns = 30/3 = 10 >= 8
          (_DAILY_SPEND_THRESHOLD), so the frequency gate is passed.
        - Month 1 (days 61-90): 10 txns * $20 = $200
        - Month 2 (days 31-60): 10 txns * $20 = $200
        - Spike month (days 1-10): 10 txns * $250 = $2500
        - avg_monthly = ($200+$200+$2500) / 3 = $966.67
        - ratio = $2500 / $966.67 = 2.59 → exceeds 2.5x threshold → EXCLUDED
        The test therefore exercises the lumpiness filter specifically, not the
        frequency gate.
        """
        db_path = self._build_db(tmp_path)
        conn = get_connection(db_path)
        init_db(conn)

        # 10 txns/month passes frequency gate (avg_monthly_txns=10 >= 8)
        # Two normal months at $20/txn ($200 total each)
        normal_txns = [(_days_ago(60 + i * 3), -20.0) for i in range(10)]  # month 1: $200
        normal_txns2 = [(_days_ago(30 + i * 3), -20.0) for i in range(10)]  # month 2: $200
        # Spike month: $250/txn x 10 = $2500 → ratio = 2500/966.67 = 2.59 → excluded
        spike_txns = [(_days_ago(i + 1), -250.0) for i in range(10)]  # spike: $2500

        _insert_transactions(conn, "Gifts & Holidays", normal_txns)
        _insert_transactions(conn, "Gifts & Holidays", normal_txns2)
        _insert_transactions(conn, "Gifts & Holidays", spike_txns)
        conn.commit()

        from ynab_tools.reports.paycheck_funding import _get_daily_spending_categories

        result = _get_daily_spending_categories(conn)
        conn.close()

        assert "Gifts & Holidays" not in result, (
            "Lumpy category (spike > 2.5x average monthly) must be excluded from bridge detection"
        )

    def test_single_month_category_excluded(self, tmp_path):
        """Categories with spend in only 1 of the 3 lookback months must be excluded."""
        db_path = self._build_db(tmp_path)
        conn = get_connection(db_path)
        init_db(conn)

        # 40 transactions all clustered within a single calendar month (days 1-28,
        # which is well inside one month for any month, guaranteeing only 1 YYYY-MM
        # appears in the group-by). The frequency count is high (40/3 = 13.3/mo) so
        # it passes the frequency threshold but must fail the consistency filter.
        today = date.today()
        txns = [(date(today.year, today.month, min(i + 1, 28)).strftime("%Y-%m-%d"), -100.0) for i in range(40)]
        _insert_transactions(conn, "Vacation Splurge", txns)
        conn.commit()

        from ynab_tools.reports.paycheck_funding import _get_daily_spending_categories

        result = _get_daily_spending_categories(conn)
        conn.close()

        assert "Vacation Splurge" not in result, (
            "Category with spend in only 1 month must be excluded (consistency filter)"
        )


# ── bridge top-up for Tier 3 daily-spend items ───────────────────────────────


class TestTier3BridgeTopUp:
    def _build_db(self, tmp_path: Path, days_until_paycheck: int = 10) -> tuple[Path, str]:
        db_path = tmp_path / "bridge_topup.db"
        conn = get_connection(db_path)
        init_db(conn)
        month = _current_month()

        conn.execute(_INSERT_ACCOUNT)
        _insert_month(conn, month, rta=200.0)

        # Fuel: NEED goal with $25 shortfall, balance=$0 (hit $0 mid-month)
        _insert_category(
            conn,
            "fuel-id",
            month,
            "Auto: Fuel",
            "Transportation",
            budgeted=275.0,
            balance=0.0,
            goal_type="NEED",
            goal_under_funded=25.0,
        )

        # Add 27 fuel transactions spread evenly across 3 months (9/month at $40).
        # COUNT(*) / 3.0 = 9.0 >= _DAILY_SPEND_THRESHOLD of 8, and consistent
        # amounts across months pass the lumpiness filter (max/avg ratio = 1.0).
        fuel_txns = []
        for month_offset in range(3):
            for txn_num in range(9):
                day_offset = month_offset * 30 + txn_num * 3 + 1
                fuel_txns.append((_days_ago(day_offset), -40.0))
        _insert_transactions(conn, "Auto: Fuel", fuel_txns)

        # Payees table (required for some queries)
        conn.commit()
        conn.close()
        return db_path, month

    def test_fuel_gets_bridge_topup_in_tier3(self, tmp_path):
        """Auto: Fuel with $0 balance and NEED goal gets bridge top-up added to Tier 3."""
        db_path, month = self._build_db(tmp_path)
        conn = get_connection(db_path)
        init_db(conn)

        # Patch next income to be 10 days away so bridge math is deterministic
        from unittest.mock import patch

        from ynab_tools.reports.paycheck_funding import generate_funding_plan

        mock_next = {
            "date": date.today() + timedelta(days=10),
            "amount": 5000,
            "days_away": 10,
            "is_bonus": False,
        }
        with patch("ynab_tools.reports.paycheck_funding._get_next_income", return_value=mock_next):
            plan = generate_funding_plan(conn, month)

        conn.close()

        tier3_names = [i["name"] for i in plan["tiers"][3]["items"]]
        assert "Auto: Fuel" in tier3_names, "Auto: Fuel must appear in Tier 3"

        fuel_item = next(i for i in plan["tiers"][3]["items"] if i["name"] == "Auto: Fuel")
        # amount_needed must exceed the raw goal shortfall ($25) due to bridge top-up
        assert fuel_item["amount_needed"] > 25.0, (
            f"Tier 3 amount_needed ({fuel_item['amount_needed']:.2f}) should exceed "
            f"goal shortfall ($25.00) due to bridge top-up"
        )
        assert "bridge" in fuel_item["reason"], "Bridge top-up must be reflected in the reason string"

    def test_fuel_not_duplicated_in_tier4(self, tmp_path):
        """Auto: Fuel must not appear in both Tier 3 and Tier 4."""
        db_path, month = self._build_db(tmp_path)
        conn = get_connection(db_path)
        init_db(conn)

        from unittest.mock import patch

        from ynab_tools.reports.paycheck_funding import generate_funding_plan

        mock_next = {
            "date": date.today() + timedelta(days=10),
            "amount": 5000,
            "days_away": 10,
            "is_bonus": False,
        }
        with patch("ynab_tools.reports.paycheck_funding._get_next_income", return_value=mock_next):
            plan = generate_funding_plan(conn, month)

        conn.close()

        tier4_names = [i["name"] for i in plan["tiers"][4]["items"]]
        assert "Auto: Fuel" not in tier4_names, "Auto: Fuel must not appear in Tier 4 when already handled in Tier 3"

    def test_bridge_reason_includes_daily_rate_and_days(self, tmp_path):
        """Bridge reason string must mention daily rate and days until paycheck."""
        db_path, month = self._build_db(tmp_path)
        conn = get_connection(db_path)
        init_db(conn)

        from unittest.mock import patch

        from ynab_tools.reports.paycheck_funding import generate_funding_plan

        mock_next = {
            "date": date.today() + timedelta(days=10),
            "amount": 5000,
            "days_away": 10,
            "is_bonus": False,
        }
        with patch("ynab_tools.reports.paycheck_funding._get_next_income", return_value=mock_next):
            plan = generate_funding_plan(conn, month)

        conn.close()

        fuel_items = [i for i in plan["tiers"][3]["items"] if i["name"] == "Auto: Fuel"]
        assert fuel_items, "Auto: Fuel must be in Tier 3"
        reason = fuel_items[0]["reason"]
        assert "10d" in reason, f"Reason must mention days (10d): {reason}"
        assert "/day" in reason, f"Reason must mention daily rate: {reason}"

    def test_no_bridge_topup_when_balance_covers_bridge_target(self, tmp_path):
        """If a Tier 3 item's post-funding balance already covers the bridge target,
        no top-up should be added."""
        db_path = tmp_path / "no_topup.db"
        conn = get_connection(db_path)
        init_db(conn)
        month = _current_month()

        conn.execute(_INSERT_ACCOUNT)
        _insert_month(conn, month, rta=500.0)

        # Fuel with $25 goal shortfall but balance=$500 (plenty left)
        _insert_category(
            conn,
            "fuel-id",
            month,
            "Auto: Fuel",
            "Transportation",
            budgeted=300.0,
            balance=500.0,
            goal_type="NEED",
            goal_under_funded=25.0,
        )

        # Add consistent fuel transactions
        fuel_txns = [(_days_ago(i * 3 + 1), -40.0) for i in range(25)]
        _insert_transactions(conn, "Auto: Fuel", fuel_txns)

        conn.commit()

        from unittest.mock import patch

        from ynab_tools.reports.paycheck_funding import generate_funding_plan

        mock_next = {
            "date": date.today() + timedelta(days=5),
            "amount": 5000,
            "days_away": 5,
            "is_bonus": False,
        }
        with patch("ynab_tools.reports.paycheck_funding._get_next_income", return_value=mock_next):
            plan = generate_funding_plan(conn, month)

        conn.close()

        fuel_items = [i for i in plan["tiers"][3]["items"] if i["name"] == "Auto: Fuel"]
        if fuel_items:
            assert fuel_items[0]["amount_needed"] == pytest.approx(25.0, abs=0.01), (
                "When post-funding balance covers bridge target, amount_needed must equal goal shortfall only"
            )
            assert "bridge" not in fuel_items[0]["reason"], (
                "No bridge top-up should appear in reason when balance is sufficient"
            )
