"""Tests verifying CC payment activity is excluded from all spending totals.

These tests guard against the double-counting bug where budget_months.activity
(which includes Credit Card Payment category activity) was used directly for
spending calculations. Each CC payment was counted twice: once when charged to
a spending category and again when the CC bill was paid.

The fix: sum budget_categories.activity filtered by should_skip_group(), which
excludes the Credit Card Payments and Internal Master Category groups.

Covered by these tests:
  - dashboard/api/summary.py    GET /api/summary
  - dashboard/api/trends.py     GET /api/trends
  - reports/summary.py          run_summary() / ynab summary
  - reports/spending.py         _print_multi_month() / ynab spending --months N
  - reports/month_end.py        _section_spending_vs_budget() / ynab month-end
  - reports/budget.py           run_budget_check() summary block / ynab budget
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ynab_tools.db import get_connection, init_db

# ── Test constants ─────────────────────────────────────────────────────────────

INCOME = 5000.0
GROCERIES = 500.0  # Bills group - included in spending
DINING = 200.0  # Bills group - included in spending
CC_PAYMENT = 600.0  # Credit Card Payments group - must be excluded

EXPECTED_SPENDING = GROCERIES + DINING  # 700  - correct
INFLATED_SPENDING = EXPECTED_SPENDING + CC_PAYMENT  # 1300 - what the bug produces

EXPECTED_NET = INCOME - EXPECTED_SPENDING  # 4300


# ── Fixture helpers ────────────────────────────────────────────────────────────


def _offset_month(offset: int) -> str:
    """Return YYYY-MM-01 string for today minus `offset` months."""
    dt = datetime.now()
    m = dt.month - offset
    y = dt.year
    while m <= 0:
        m += 12
        y -= 1
    return f"{y:04d}-{m:02d}-01"


def _setup_cc_db(tmp_path: Path, months: int = 1) -> Path:
    """Create a DB with CC payment activity alongside regular spending.

    budget_months.activity is set to the INFLATED total (includes CC) so that
    any code still reading it directly produces the wrong answer. The correct
    answer comes from summing the non-CC budget_categories rows.

    Per month:
      Groceries   -$500  (Bills group)
      Dining Out  -$200  (Bills group)
      Visa Payment -$600 (Credit Card Payments group)

    Correct spending = $700. Double-counted spending = $1,300.
    """
    db_path = tmp_path / "cc_test.db"
    conn = get_connection(db_path)
    init_db(conn)
    now_iso = datetime.now().isoformat()

    for i in range(months):
        month = _offset_month(i)
        conn.execute(
            """INSERT OR IGNORE INTO budget_months
               (month, income, budgeted, activity, to_be_budgeted, age_of_money, last_synced_at)
               VALUES (?, ?, 1300, ?, 200, 30, ?)""",
            (month, INCOME, -INFLATED_SPENDING, now_iso),
        )
        conn.execute(
            """INSERT OR IGNORE INTO budget_categories
               (id, budget_month, category_group_name, name, hidden, deleted,
                budgeted, activity, balance, goal_type, goal_target, goal_under_funded)
               VALUES (?, ?, 'Bills', 'Groceries', 0, 0, 500, ?, 0, 'MF', 500, 0)""",
            (f"gro-{i}", month, -GROCERIES),
        )
        conn.execute(
            """INSERT OR IGNORE INTO budget_categories
               (id, budget_month, category_group_name, name, hidden, deleted,
                budgeted, activity, balance, goal_type, goal_target, goal_under_funded)
               VALUES (?, ?, 'Bills', 'Dining Out', 0, 0, 200, ?, 0, 'MF', 200, 0)""",
            (f"din-{i}", month, -DINING),
        )
        conn.execute(
            """INSERT OR IGNORE INTO budget_categories
               (id, budget_month, category_group_name, name, hidden, deleted,
                budgeted, activity, balance, goal_type, goal_target, goal_under_funded)
               VALUES (?, ?, 'Credit Card Payments', 'Visa Payment', 0, 0, 600, ?, 0, 'MF', 600, 0)""",
            (f"cc-{i}", month, -CC_PAYMENT),
        )

    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def cc_client(tmp_path):
    """FastAPI TestClient with 6 months of CC-inclusive test data."""
    db_path = _setup_cc_db(tmp_path, months=6)
    from ynab_tools.dashboard.server import app

    with patch("ynab_tools.db.DB_PATH", db_path):
        with TestClient(app) as c:
            yield c


# ── Dashboard: GET /api/summary ────────────────────────────────────────────────


class TestDashboardSummaryExcludesCC:
    def test_spending_per_month_excludes_cc(self, cc_client):
        data = cc_client.get("/api/summary?months=6").json()
        for m in data["months"]:
            if m["spending"] > 0:
                assert m["spending"] == pytest.approx(EXPECTED_SPENDING, abs=1.0), (
                    f"Month {m['month']}: spending {m['spending']} should be "
                    f"{EXPECTED_SPENDING}, not {INFLATED_SPENDING}"
                )

    def test_net_uses_correct_spending(self, cc_client):
        data = cc_client.get("/api/summary?months=6").json()
        for m in data["months"]:
            if m["income"] > 0:
                assert m["net"] == pytest.approx(EXPECTED_NET, abs=1.0)

    def test_surplus_flag_reflects_correct_net(self, cc_client):
        data = cc_client.get("/api/summary?months=6").json()
        for m in data["months"]:
            if m["income"] > 0:
                assert m["surplus"] is True


# ── Dashboard: GET /api/trends ─────────────────────────────────────────────────


class TestDashboardTrendsExcludesCC:
    def test_per_month_spending_excludes_cc(self, cc_client):
        data = cc_client.get("/api/trends?months=6").json()
        for m in data["months"]:
            if m["spending"] > 0:
                assert m["spending"] == pytest.approx(EXPECTED_SPENDING, abs=1.0), (
                    f"Month {m['month']}: spending {m['spending']} should be "
                    f"{EXPECTED_SPENDING}, not {INFLATED_SPENDING}"
                )

    def test_avg_monthly_spending_excludes_cc(self, cc_client):
        data = cc_client.get("/api/trends?months=6").json()
        assert data["avg_monthly_spending"] == pytest.approx(EXPECTED_SPENDING, abs=1.0)

    def test_streak_and_net_based_on_correct_spending(self, cc_client):
        # income ($5,000) > correct spending ($700) → surplus every month
        data = cc_client.get("/api/trends?months=6").json()
        assert data["streak"]["type"] == "surplus"
        for m in data["months"]:
            if m["income"] > 0:
                assert m["surplus"] is True
                assert m["net"] == pytest.approx(EXPECTED_NET, abs=1.0)


# ── CLI: ynab summary ──────────────────────────────────────────────────────────


class TestCliSummaryExcludesCC:
    def test_spending_column_excludes_cc(self, tmp_path, capsys):
        db_path = _setup_cc_db(tmp_path, months=2)
        from ynab_tools.reports.summary import run_summary

        with patch("ynab_tools.db.DB_PATH", db_path):
            run_summary(months=2)

        out = capsys.readouterr().out
        # Correct spending (700) should appear; inflated (1,300) should not
        assert "700.00" in out
        assert "1,300.00" not in out

    def test_net_reflects_correct_spending(self, tmp_path, capsys):
        db_path = _setup_cc_db(tmp_path, months=1)
        from ynab_tools.reports.summary import run_summary

        with patch("ynab_tools.db.DB_PATH", db_path):
            run_summary(months=1)

        out = capsys.readouterr().out
        # net = 5000 - 700 = 4300 → "+$4,300.00"
        assert "4,300.00" in out
        # bug net = 5000 - 1300 = 3700 → "+$3,700.00"
        assert "3,700.00" not in out


# ── CLI: ynab spending --months N ─────────────────────────────────────────────


class TestCliSpendingMultiMonthExcludesCC:
    def test_monthly_totals_exclude_cc(self, tmp_path, capsys):
        db_path = _setup_cc_db(tmp_path, months=2)
        from ynab_tools.reports.spending import run_spending

        with patch("ynab_tools.db.DB_PATH", db_path):
            run_spending(months=2)

        out = capsys.readouterr().out
        # Per-month spent column: $700.00; average row also $700.00
        assert "700.00" in out
        assert "1,300.00" not in out

    def test_cc_payment_category_absent_from_breakdown(self, tmp_path, capsys):
        db_path = _setup_cc_db(tmp_path, months=2)
        from ynab_tools.reports.spending import run_spending

        with patch("ynab_tools.db.DB_PATH", db_path):
            run_spending(months=2)

        out = capsys.readouterr().out
        # CC Payment category must not appear in per-category breakdown or Top 5
        assert "Visa Payment" not in out
        assert "Credit Card Payments" not in out


# ── CLI: ynab month-end (spending vs budget section) ──────────────────────────


class TestCliMonthEndExcludesCC:
    def test_activity_line_excludes_cc(self, tmp_path, capsys):
        db_path = _setup_cc_db(tmp_path, months=1)
        conn = get_connection(db_path)
        from ynab_tools.reports.month_end import _section_spending_vs_budget

        _section_spending_vs_budget(conn, _offset_month(0))
        conn.close()

        out = capsys.readouterr().out
        # activity should be -700, not -1,300
        activity_lines = [line for line in out.splitlines() if "Total activity" in line]
        assert len(activity_lines) == 1
        assert "-700.00" in activity_lines[0]
        assert "-1,300.00" not in activity_lines[0]

    def test_net_reflects_correct_activity(self, tmp_path, capsys):
        db_path = _setup_cc_db(tmp_path, months=1)
        conn = get_connection(db_path)
        from ynab_tools.reports.month_end import _section_spending_vs_budget

        _section_spending_vs_budget(conn, _offset_month(0))
        conn.close()

        out = capsys.readouterr().out
        # net = income (5000) + activity (-700) = 4300
        assert "4,300.00" in out
        # bug net = 5000 + (-1300) = 3700
        assert "3,700.00" not in out


# ── CLI: ynab budget (summary block) ──────────────────────────────────────────


class TestCliBudgetSummaryExcludesCC:
    def test_total_activity_excludes_cc(self, tmp_path, capsys):
        db_path = _setup_cc_db(tmp_path, months=1)
        from ynab_tools.reports.budget import run_budget_check

        with patch("ynab_tools.db.DB_PATH", db_path):
            run_budget_check()

        out = capsys.readouterr().out
        activity_lines = [line for line in out.splitlines() if "Total activity" in line]
        assert len(activity_lines) == 1
        assert "-700.00" in activity_lines[0]
        assert "-1,300.00" not in activity_lines[0]

    def test_total_budgeted_excludes_cc(self, tmp_path, capsys):
        db_path = _setup_cc_db(tmp_path, months=1)
        from ynab_tools.reports.budget import run_budget_check

        with patch("ynab_tools.db.DB_PATH", db_path):
            run_budget_check()

        out = capsys.readouterr().out
        budgeted_lines = [line for line in out.splitlines() if "Total budgeted" in line]
        assert len(budgeted_lines) == 1
        # budgeted = groceries (500) + dining (200) = 700; CC payment (600) excluded
        assert "700.00" in budgeted_lines[0]
        assert "1,300.00" not in budgeted_lines[0]
