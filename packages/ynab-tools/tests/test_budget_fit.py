"""Tests for GET /api/budget-fit endpoint."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ynab_tools.db import get_connection, init_db


def _setup_db(tmp_path: Path, months_of_history: int = 6) -> Path:
    """Seed a test DB with income, categories, and historical spending."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_db(conn)

    now_iso = datetime.now().isoformat()
    current_month = datetime.now().strftime("%Y-%m-01")

    # Current month income row
    conn.execute(
        """
        INSERT OR IGNORE INTO budget_months
        (month, income, budgeted, activity, to_be_budgeted, age_of_money, last_synced_at)
        VALUES (?, 6500, 5800, -5200, 700, 30, ?)
        """,
        (current_month, now_iso),
    )

    # Historical income rows
    for i in range(1, months_of_history + 1):
        hist_month = (datetime.now() - timedelta(days=31 * i)).strftime("%Y-%m-01")
        conn.execute(
            """
            INSERT OR IGNORE INTO budget_months
            (month, income, budgeted, activity, to_be_budgeted, age_of_money, last_synced_at)
            VALUES (?, 6000, 5500, -5000, 1000, 30, ?)
            """,
            (hist_month, now_iso),
        )

    # Current month categories:
    #   Bills group:
    #     Rent (NEED, no date) → uses goal_target=1500
    #     Groceries (NEED, no date) → uses goal_target=800
    #     Utilities (MF) → uses goal_target=200
    #     Annual Insurance (NEED, with date) → uses budgeted=100 NOT goal_target=1200
    #   Savings group:
    #     Emergency Fund (TB, goal_target=10000) → uses budgeted=500
    #     Vacation (None) → uses budgeted=300
    #   Credit Card Payments → skipped by should_skip_group
    #   Hidden / deleted → excluded
    categories = [
        # id, month, group, name, hidden, deleted, budgeted, activity, goal_type, goal_target, goal_target_month
        ("c1", current_month, "Bills", "Rent", 0, 0, 1400, -1500, "NEED", 1500, None),
        ("c2", current_month, "Bills", "Groceries", 0, 0, 750, -820, "NEED", 800, None),
        ("c3", current_month, "Bills", "Utilities", 0, 0, 200, -180, "MF", 200, None),
        ("c9", current_month, "Bills", "Annual Insurance", 0, 0, 100, 0, "NEED", 1200, "2026-12-01"),
        ("c4", current_month, "Savings", "Emergency Fund", 0, 0, 500, 0, "TB", 10000, None),
        ("c5", current_month, "Savings", "Vacation", 0, 0, 300, 0, None, None, None),
        ("c6", current_month, "Credit Card Payments", "Visa Payment", 0, 0, 200, -200, "NEED", 200, None),
        ("c7", current_month, "Bills", "Hidden Bill", 1, 0, 100, -100, "NEED", 100, None),
        ("c8", current_month, "Bills", "Deleted Bill", 0, 1, 100, -100, "NEED", 100, None),
    ]
    for c in categories:
        conn.execute(
            """
            INSERT INTO budget_categories
            (id, budget_month, category_group_name, name, hidden, deleted,
             budgeted, activity, goal_type, goal_target, goal_target_month)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            c,
        )

    # Historical activity for calibration impact
    for i in range(1, months_of_history + 1):
        hist_month = (datetime.now() - timedelta(days=31 * i)).strftime("%Y-%m-01")
        hist_cats = [
            (f"h1-{i}", hist_month, "Bills", "Rent", 0, 0, 1400, -1500, "NEED", 1500, None),
            (f"h2-{i}", hist_month, "Bills", "Groceries", 0, 0, 750, -820, "NEED", 800, None),
            (f"h3-{i}", hist_month, "Bills", "Utilities", 0, 0, 200, -100, "MF", 200, None),
        ]
        for c in hist_cats:
            conn.execute(
                """
                INSERT OR IGNORE INTO budget_categories
                (id, budget_month, category_group_name, name, hidden, deleted,
                 budgeted, activity, goal_type, goal_target, goal_target_month)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                c,
            )

    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def client(tmp_path):
    db_path = _setup_db(tmp_path)
    from ynab_tools.dashboard.server import app

    with patch("ynab_tools.db.DB_PATH", db_path):
        with TestClient(app) as c:
            yield c


class TestBudgetFitBasic:
    def test_returns_200(self, client):
        resp = client.get("/api/budget-fit")
        assert resp.status_code == 200

    def test_response_shape(self, client):
        data = client.get("/api/budget-fit").json()
        assert "avg_monthly_income" in data
        assert "total_current_targets" in data
        assert "headroom" in data
        assert "pct_committed" in data
        assert "groups" in data
        assert "analysis_months" in data
        assert "budget_month" in data
        cal = data["calibration_summary"]
        assert "potential_savings" in cal
        assert "required_additions" in cal
        assert "net_headroom_change" in cal
        assert "recommended_total" in cal
        assert "recommended_headroom" in cal
        assert "over_target_count" in cal
        assert "under_target_count" in cal
        assert "unbudgeted_avg_spend" in cal

    def test_months_param(self, client):
        data = client.get("/api/budget-fit?months=6").json()
        assert data["analysis_months"] == 6

    def test_months_validation_too_low(self, client):
        resp = client.get("/api/budget-fit?months=2")
        assert resp.status_code == 422

    def test_months_validation_too_high(self, client):
        resp = client.get("/api/budget-fit?months=37")
        assert resp.status_code == 422


class TestBudgetFitTargets:
    def test_need_goal_no_date_uses_goal_target(self, client):
        data = client.get("/api/budget-fit").json()
        # Rent (NEED, no date): goal_target=1500 used (not budgeted=1400)
        # Groceries (NEED, no date): goal_target=800 used (not budgeted=750)
        # Utilities (MF): goal_target=200 used
        # Annual Insurance (NEED, with date): budgeted=100 used (not goal_target=1200)
        bills = next(g for g in data["groups"] if g["name"] == "Bills")
        # Bills total = 1500 + 800 + 200 + 100 = 2600
        assert bills["total_target"] == pytest.approx(2600.0, abs=1)

    def test_need_goal_with_date_uses_budgeted_plus_underfunded(self, client):
        data = client.get("/api/budget-fit").json()
        # Annual Insurance: NEED goal with goal_target_month set, budgeted=100, goal_under_funded=0
        # Monthly installment = max(100 + 0, 0) = 100. NOT goal_target=1200.
        bills = next(g for g in data["groups"] if g["name"] == "Bills")
        # If goal_target were used: 1500+800+200+1200 = 3700. Correct = 2600.
        assert bills["total_target"] == pytest.approx(2600.0, abs=1)

    def test_need_with_date_includes_underfunded_amount(self, tmp_path):
        """NEED-with-date obligation = budgeted + goal_under_funded, not just budgeted."""
        db_path = tmp_path / "uf.db"
        conn = get_connection(db_path)
        init_db(conn)
        month = datetime.now().strftime("%Y-%m-01")
        conn.execute(
            "INSERT INTO budget_months (month, income, budgeted, activity, to_be_budgeted, last_synced_at) "
            "VALUES (?, 6000, 0, 0, 6000, datetime('now'))",
            (month,),
        )
        # NEED with date: budgeted=100, goal_under_funded=427 → obligation should be 527
        conn.execute(
            "INSERT INTO budget_categories "
            "(id, budget_month, category_group_name, name, hidden, deleted, "
            "budgeted, activity, goal_type, goal_target, goal_target_month, goal_under_funded) "
            "VALUES ('c1', ?, 'Bills', 'Property Taxes', 0, 0, 100, 0, 'NEED', 5700, '2026-12-01', 427)",
            (month,),
        )
        conn.commit()
        conn.close()

        from ynab_tools.dashboard.server import app

        with patch("ynab_tools.db.DB_PATH", db_path):
            with TestClient(app) as c:
                data = c.get("/api/budget-fit").json()
        bills = next(g for g in data["groups"] if g["name"] == "Bills")
        assert bills["total_target"] == pytest.approx(527.0, abs=1)

    def test_tb_goal_uses_budgeted(self, client):
        data = client.get("/api/budget-fit").json()
        # Emergency Fund: TB goal_target=10000 should NOT be used - use budgeted=500 instead
        savings = next(g for g in data["groups"] if g["name"] == "Savings")
        # Savings total = 500 (Emergency Fund budgeted) + 300 (Vacation budgeted, no goal) = 800
        assert savings["total_target"] == pytest.approx(800.0, abs=1)

    def test_excludes_hidden_categories(self, client):
        data = client.get("/api/budget-fit").json()
        # Hidden Bill (c7, 100) should not be included in Bills total
        bills = next(g for g in data["groups"] if g["name"] == "Bills")
        assert bills["total_target"] == pytest.approx(2600.0, abs=1)

    def test_excludes_deleted_categories(self, client):
        data = client.get("/api/budget-fit").json()
        # Deleted Bill (c8, 100) should not be included in Bills total
        bills = next(g for g in data["groups"] if g["name"] == "Bills")
        assert bills["total_target"] == pytest.approx(2600.0, abs=1)

    def test_skips_credit_card_payments_group(self, client):
        data = client.get("/api/budget-fit").json()
        group_names = {g["name"] for g in data["groups"]}
        assert "Credit Card Payments" not in group_names

    def test_total_targets_sum(self, client):
        data = client.get("/api/budget-fit").json()
        # Bills: 2600 (Rent 1500 + Groceries 800 + Utilities 200 + Annual Insurance 100)
        # Savings: 800 (Emergency Fund 500 + Vacation 300)
        # Total: 3400
        assert data["total_current_targets"] == pytest.approx(3400.0, abs=1)


class TestBudgetFitIncome:
    def test_headroom_formula(self, client):
        data = client.get("/api/budget-fit").json()
        expected = round(data["avg_monthly_income"] - data["total_current_targets"], 2)
        assert data["headroom"] == pytest.approx(expected, abs=0.01)

    def test_pct_committed_formula(self, client):
        data = client.get("/api/budget-fit").json()
        if data["avg_monthly_income"] > 0:
            expected = round(data["total_current_targets"] / data["avg_monthly_income"] * 100, 1)
            assert data["pct_committed"] == pytest.approx(expected, abs=0.1)

    def test_groups_sorted_by_target_descending(self, client):
        data = client.get("/api/budget-fit").json()
        totals = [g["total_target"] for g in data["groups"]]
        assert totals == sorted(totals, reverse=True)


class TestBudgetFitCalibrationSummary:
    def test_net_headroom_change_formula(self, client):
        data = client.get("/api/budget-fit").json()
        cal = data["calibration_summary"]
        expected = round(cal["potential_savings"] - cal["required_additions"], 2)
        assert cal["net_headroom_change"] == pytest.approx(expected, abs=0.01)

    def test_recommended_total_formula(self, client):
        data = client.get("/api/budget-fit").json()
        cal = data["calibration_summary"]
        expected = round(data["total_current_targets"] - cal["net_headroom_change"], 2)
        assert cal["recommended_total"] == pytest.approx(expected, abs=0.01)

    def test_recommended_headroom_formula(self, client):
        data = client.get("/api/budget-fit").json()
        cal = data["calibration_summary"]
        expected = round(data["avg_monthly_income"] - cal["recommended_total"], 2)
        assert cal["recommended_headroom"] == pytest.approx(expected, abs=0.01)

    def test_counts_non_negative(self, client):
        data = client.get("/api/budget-fit").json()
        cal = data["calibration_summary"]
        assert cal["over_target_count"] >= 0
        assert cal["under_target_count"] >= 0
        assert cal["potential_savings"] >= 0
        assert cal["required_additions"] >= 0
        assert cal["unbudgeted_avg_spend"] >= 0

    def test_utilities_under_target(self, client):
        # Utilities: NEED goal_target=200, but historical spend averages ~100/mo
        # Should be classified UNDER_TARGET (target 200 > spend*1.25 threshold for 80%)
        data = client.get("/api/budget-fit").json()
        cal = data["calibration_summary"]
        # At least Utilities should show up as under-target
        assert cal["under_target_count"] >= 1
        assert cal["potential_savings"] > 0


class TestBudgetFitEdgeCases:
    def test_no_income_data(self, tmp_path):
        db_path = tmp_path / "no_income.db"
        conn = get_connection(db_path)
        init_db(conn)
        month = datetime.now().strftime("%Y-%m-01")
        conn.execute(
            """
            INSERT INTO budget_categories
            (id, budget_month, category_group_name, name, hidden, deleted,
             budgeted, activity, goal_type, goal_target)
            VALUES ('c1', ?, 'Bills', 'Rent', 0, 0, 1500, -1500, 'NEED', 1500)
            """,
            (month,),
        )
        conn.commit()
        conn.close()

        from ynab_tools.dashboard.server import app

        with patch("ynab_tools.db.DB_PATH", db_path):
            with TestClient(app) as c:
                data = c.get("/api/budget-fit").json()
        assert data["avg_monthly_income"] == 0.0
        assert data["pct_committed"] == 0.0
        assert data["headroom"] < 0

    def test_all_unbudgeted(self, tmp_path):
        db_path = tmp_path / "unbudgeted.db"
        conn = get_connection(db_path)
        init_db(conn)
        month = datetime.now().strftime("%Y-%m-01")
        conn.execute(
            """
            INSERT INTO budget_months
            (month, income, budgeted, activity, to_be_budgeted, last_synced_at)
            VALUES (?, 5000, 0, 0, 5000, datetime('now'))
            """,
            (month,),
        )
        # Category with no goal and no budgeted amount
        conn.execute(
            """
            INSERT INTO budget_categories
            (id, budget_month, category_group_name, name, hidden, deleted,
             budgeted, activity, goal_type, goal_target)
            VALUES ('c1', ?, 'Misc', 'Unknown', 0, 0, 0, -50, NULL, NULL)
            """,
            (month,),
        )
        conn.commit()
        conn.close()

        from ynab_tools.dashboard.server import app

        with patch("ynab_tools.db.DB_PATH", db_path):
            with TestClient(app) as c:
                data = c.get("/api/budget-fit").json()
        assert data["total_current_targets"] == 0.0
