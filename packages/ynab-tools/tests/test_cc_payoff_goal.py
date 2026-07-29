"""Tests for CC payment category payoff goal handling (issue #208).

Guards against two bugs:
1. category_balance shows $0 for CC payoff goals because goal_target is 0 and
   the real remaining balance is in goal_overall_left.
2. paycheck_funding excludes CC payment categories with explicit payoff goals
   from Tier 3 (Monthly Bills) even though they require monthly funding.

The fix: run_balance falls back to goal_overall_left when goal_target is 0.
         generate_funding_plan allows CC payment categories with goal_under_funded > 0
         in Tier 3 despite the category being in the Credit Card Payments group.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from ynab_tools.db import get_connection, init_db


def _current_month() -> str:
    return datetime.now().strftime("%Y-%m-01")


def _build_db(tmp_path: Path) -> Path:
    """Create a minimal DB with:
    - One CC payment category that has an explicit payoff goal (goal_target=0,
      goal_overall_left=635.92, goal_under_funded=211.98).
    - One regular CC payment category (no goal, no goal_under_funded).
    - RTA of $500 so the funding plan has something to work with.
    """
    db_path = tmp_path / "payoff_goal_test.db"
    conn = get_connection(db_path)
    init_db(conn)
    now_iso = datetime.now().isoformat()
    month = _current_month()

    conn.execute(
        """INSERT OR IGNORE INTO budget_months
           (month, income, budgeted, activity, to_be_budgeted, age_of_money, last_synced_at)
           VALUES (?, 5000, 3000, -2500, 500, 30, ?)""",
        (month, now_iso),
    )

    # CC payment category with explicit "Pay Off Balance by Date" goal.
    # YNAB stores these as goal_type=TBD, goal_target=0, goal_overall_left=remaining balance.
    conn.execute(
        """INSERT OR IGNORE INTO budget_categories
           (id, budget_month, category_group_name, name, hidden, deleted,
            budgeted, activity, balance,
            goal_type, goal_target, goal_target_month, goal_under_funded, goal_overall_left)
           VALUES ('cc-payoff', ?, 'Credit Card Payments', 'Store Card – 1234', 0, 0,
                   0, 0, 0,
                   'TBD', 0.0, ?, 211.98, 635.92)""",
        (month, f"{datetime.now().year + 1}-01-01"),
    )

    # Plain CC payment category - no goal, no monthly obligation.
    conn.execute(
        """INSERT OR IGNORE INTO budget_categories
           (id, budget_month, category_group_name, name, hidden, deleted,
            budgeted, activity, balance,
            goal_type, goal_target, goal_target_month, goal_under_funded, goal_overall_left)
           VALUES ('cc-plain', ?, 'Credit Card Payments', 'Visa – 5678', 0, 0,
                   0, -100, 0,
                   NULL, NULL, NULL, NULL, NULL)""",
        (month,),
    )

    # A regular spending category with a goal, to confirm tier 3 still works normally.
    conn.execute(
        """INSERT OR IGNORE INTO budget_categories
           (id, budget_month, category_group_name, name, hidden, deleted,
            budgeted, activity, balance,
            goal_type, goal_target, goal_target_month, goal_under_funded, goal_overall_left)
           VALUES ('sub-regular', ?, 'Subscriptions', 'Streaming Service', 0, 0,
                   0, 0, 0,
                   'MF', 15.99, NULL, 15.99, NULL)""",
        (month,),
    )

    conn.commit()
    conn.close()
    return db_path


# ── category_balance: goal_overall_left fallback ──────────────────────────────


class TestCategoryBalancePayoffGoalDisplay:
    def test_shows_overall_left_when_goal_target_is_zero(self, tmp_path, capsys):
        db_path = _build_db(tmp_path)
        from ynab_tools.reports.budget import run_balance

        with patch("ynab_tools.db.DB_PATH", db_path):
            run_balance("Store Card")

        out = capsys.readouterr().out
        # Should display goal_overall_left (635.92), not goal_target (0.0)
        assert "635.92" in out
        assert "Goal:       $         0.00" not in out

    def test_shows_target_date_for_payoff_goal(self, tmp_path, capsys):
        db_path = _build_db(tmp_path)
        from ynab_tools.reports.budget import run_balance

        with patch("ynab_tools.db.DB_PATH", db_path):
            run_balance("Store Card")

        out = capsys.readouterr().out
        # Target month should appear in the goal line
        year = datetime.now().year + 1
        assert f"due {year}-01" in out

    def test_shows_underfunded_amount(self, tmp_path, capsys):
        db_path = _build_db(tmp_path)
        from ynab_tools.reports.budget import run_balance

        with patch("ynab_tools.db.DB_PATH", db_path):
            run_balance("Store Card")

        out = capsys.readouterr().out
        # goal_under_funded (211.98) should appear as the monthly amount needed
        assert "211.98" in out


# ── paycheck_funding: CC payoff goal appears in Tier 3 ────────────────────────


class TestPaycheckFundingCCPayoffGoal:
    def test_cc_payoff_goal_included_in_tier3(self, tmp_path):
        db_path = _build_db(tmp_path)
        conn = get_connection(db_path)
        init_db(conn)

        from ynab_tools.reports.paycheck_funding import generate_funding_plan

        plan = generate_funding_plan(conn, _current_month())
        conn.close()

        tier3_names = [i["name"] for i in plan["tiers"][3]["items"]]
        assert "Store Card – 1234" in tier3_names, (
            "CC payment category with payoff goal must appear in Tier 3 (Monthly Bills)"
        )

    def test_cc_payoff_goal_amount_is_under_funded(self, tmp_path):
        db_path = _build_db(tmp_path)
        conn = get_connection(db_path)
        init_db(conn)

        from ynab_tools.reports.paycheck_funding import generate_funding_plan

        plan = generate_funding_plan(conn, _current_month())
        conn.close()

        tier3_items = {i["name"]: i for i in plan["tiers"][3]["items"]}
        payoff_item = tier3_items.get("Store Card – 1234")
        assert payoff_item is not None
        assert payoff_item["amount_needed"] == pytest.approx(211.98, abs=0.01)

    def test_plain_cc_category_excluded_from_all_tiers(self, tmp_path):
        """A CC payment category with no goal and no underfunding must remain excluded."""
        db_path = _build_db(tmp_path)
        conn = get_connection(db_path)
        init_db(conn)

        from ynab_tools.reports.paycheck_funding import generate_funding_plan

        plan = generate_funding_plan(conn, _current_month())
        conn.close()

        all_names = [i["name"] for tier in plan["tiers"].values() for i in tier["items"]]
        assert "Visa – 5678" not in all_names, "Plain CC payment category without a payoff goal must stay excluded"

    def test_regular_goal_category_still_in_tier3(self, tmp_path):
        """Regression: non-CC categories with MF goals must still appear in Tier 3."""
        db_path = _build_db(tmp_path)
        conn = get_connection(db_path)
        init_db(conn)

        from ynab_tools.reports.paycheck_funding import generate_funding_plan

        plan = generate_funding_plan(conn, _current_month())
        conn.close()

        tier3_names = [i["name"] for i in plan["tiers"][3]["items"]]
        assert "Streaming Service" in tier3_names, "Non-CC categories with MF goals must still appear in Tier 3"


# ── category_balance: fully-settled CC payoff goal display (issue #216) ───────


def _build_settled_db(tmp_path: Path) -> Path:
    """DB with a CC payoff category that is fully paid off (goal_target=0, goal_overall_left=0)."""
    db_path = tmp_path / "settled_goal_test.db"
    conn = get_connection(db_path)
    init_db(conn)
    now_iso = datetime.now().isoformat()
    month = _current_month()

    conn.execute(
        """INSERT OR IGNORE INTO budget_months
           (month, income, budgeted, activity, to_be_budgeted, age_of_money, last_synced_at)
           VALUES (?, 5000, 3000, -2500, 500, 30, ?)""",
        (month, now_iso),
    )

    # Fully-settled CC payoff goal: goal_target=0, goal_overall_left=0 - balance paid off.
    conn.execute(
        """INSERT OR IGNORE INTO budget_categories
           (id, budget_month, category_group_name, name, hidden, deleted,
            budgeted, activity, balance,
            goal_type, goal_target, goal_target_month, goal_under_funded, goal_overall_left)
           VALUES ('cc-settled', ?, 'Credit Card Payments', 'Settled Card – 9999', 0, 0,
                   0, 0, 0,
                   'TBD', 0.0, ?, 0.0, 0.0)""",
        (month, f"{datetime.now().year}-12-01"),
    )

    conn.commit()
    conn.close()
    return db_path


class TestCategoryBalanceFullySettledPayoffGoal:
    """Tests for the fully-settled CC payoff goal display (issue #216)."""

    def test_shows_paid_off_label(self, tmp_path, capsys):
        """When goal_target=0 and goal_overall_left=0, display 'paid off' not '$0.00'."""
        db_path = _build_settled_db(tmp_path)
        from ynab_tools.reports.budget import run_balance

        with patch("ynab_tools.db.DB_PATH", db_path):
            run_balance("Settled Card")

        out = capsys.readouterr().out
        assert "paid off" in out

    def test_does_not_show_zero_dollar_goal(self, tmp_path, capsys):
        """Fully-settled payoff goal must not display '$0.00' as the goal amount."""
        db_path = _build_settled_db(tmp_path)
        from ynab_tools.reports.budget import run_balance

        with patch("ynab_tools.db.DB_PATH", db_path):
            run_balance("Settled Card")

        out = capsys.readouterr().out
        # The bare "$0.00  (TBD)" pattern must not appear
        assert "Goal:       $          0.00" not in out

    def test_shows_tbd_type_label(self, tmp_path, capsys):
        """Goal type label (TBD) should still appear in the settled output."""
        db_path = _build_settled_db(tmp_path)
        from ynab_tools.reports.budget import run_balance

        with patch("ynab_tools.db.DB_PATH", db_path):
            run_balance("Settled Card")

        out = capsys.readouterr().out
        assert "TBD" in out

    def test_shows_due_date_when_present(self, tmp_path, capsys):
        """Due date should appear in the settled goal line when goal_target_month is set."""
        db_path = _build_settled_db(tmp_path)
        from ynab_tools.reports.budget import run_balance

        with patch("ynab_tools.db.DB_PATH", db_path):
            run_balance("Settled Card")

        out = capsys.readouterr().out
        year = datetime.now().year
        assert f"due {year}-12" in out
