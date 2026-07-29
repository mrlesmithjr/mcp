"""Tests for planned expenses: CRUD, gap calculation, and budget integration."""

from datetime import datetime, timedelta

from ynab_tools.db import get_connection, init_db
from ynab_tools.reports.planned import (
    _find_category,
    _get_category_balance,
    get_upcoming_plans,
)


def _setup_test_db(tmp_path):
    """Create a test DB with sample data for planned expense tests."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_db(conn)

    month = datetime.now().strftime("%Y-%m-01")
    now_iso = datetime.now().isoformat()

    # Budget month
    conn.execute(
        """
        INSERT INTO budget_months (month, income, budgeted, activity, to_be_budgeted, age_of_money, last_synced_at)
        VALUES (?, 5000, 4500, -4200, 800, 30, ?)
    """,
        (month, now_iso),
    )

    # Budget categories
    categories = [
        ("cat1", month, "Personal Care", "Spa & Nails", 0, 0, 100, -39.36, 60.64, "MF", 100, 0),
        ("cat2", month, "Food", "Groceries", 0, 0, 900, -850, 50, "MF", 900, 0),
        ("cat3", month, "Savings", "Emergency Fund", 0, 0, 500, 0, 5000, "TB", 15000, 0),
    ]
    for c in categories:
        conn.execute(
            """
            INSERT INTO budget_categories
            (id, budget_month, category_group_name, name, hidden, deleted,
             budgeted, activity, balance, goal_type, goal_target, goal_under_funded)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            c,
        )

    conn.commit()
    return conn


class TestPlannedCRUD:
    """Test create, read, done, and remove operations."""

    def test_add_and_list(self, tmp_path):
        conn = _setup_test_db(tmp_path)
        due = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")

        conn.execute(
            """
            INSERT INTO planned_expenses (category_name, category_id, amount, due_date, memo)
            VALUES ('Spa & Nails', 'cat1', 165.00, ?, 'Appointment next month')
        """,
            (due,),
        )
        conn.commit()

        rows = conn.execute("SELECT * FROM planned_expenses WHERE status = 'active'").fetchall()
        assert len(rows) == 1
        assert rows[0]["category_name"] == "Spa & Nails"
        assert rows[0]["amount"] == 165.00
        assert rows[0]["memo"] == "Appointment next month"
        conn.close()

    def test_done_sets_status(self, tmp_path):
        conn = _setup_test_db(tmp_path)
        due = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")

        conn.execute(
            """
            INSERT INTO planned_expenses (category_name, category_id, amount, due_date)
            VALUES ('Groceries', 'cat2', 200.00, ?)
        """,
            (due,),
        )
        conn.commit()

        plan_id = conn.execute("SELECT id FROM planned_expenses").fetchone()["id"]

        now = datetime.now().isoformat()
        conn.execute(
            """
            UPDATE planned_expenses SET status = 'completed', completed_at = ?
            WHERE id = ?
        """,
            (now, plan_id),
        )
        conn.commit()

        row = conn.execute("SELECT status, completed_at FROM planned_expenses WHERE id = ?", (plan_id,)).fetchone()
        assert row["status"] == "completed"
        assert row["completed_at"] is not None
        conn.close()

    def test_remove_deletes(self, tmp_path):
        conn = _setup_test_db(tmp_path)
        due = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d")

        conn.execute(
            """
            INSERT INTO planned_expenses (category_name, category_id, amount, due_date)
            VALUES ('Groceries', 'cat2', 100.00, ?)
        """,
            (due,),
        )
        conn.commit()

        plan_id = conn.execute("SELECT id FROM planned_expenses").fetchone()["id"]
        conn.execute("DELETE FROM planned_expenses WHERE id = ?", (plan_id,))
        conn.commit()

        row = conn.execute("SELECT * FROM planned_expenses WHERE id = ?", (plan_id,)).fetchone()
        assert row is None
        conn.close()


class TestGapCalculation:
    """Test funding gap logic."""

    def test_gap_when_underfunded(self, tmp_path):
        conn = _setup_test_db(tmp_path)
        due = (datetime.now() + timedelta(days=15)).strftime("%Y-%m-%d")

        # Spa & Nails has $60.64 balance, need $165
        conn.execute(
            """
            INSERT INTO planned_expenses (category_name, category_id, amount, due_date)
            VALUES ('Spa & Nails', 'cat1', 165.00, ?)
        """,
            (due,),
        )
        conn.commit()

        plans = get_upcoming_plans(conn, days=30)
        assert len(plans) == 1
        assert plans[0]["amount"] == 165.00
        assert plans[0]["category_balance"] == 60.64
        assert abs(plans[0]["gap"] - 104.36) < 0.01
        conn.close()

    def test_gap_zero_when_funded(self, tmp_path):
        conn = _setup_test_db(tmp_path)
        due = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")

        # Groceries has $50 balance, plan for $40
        conn.execute(
            """
            INSERT INTO planned_expenses (category_name, category_id, amount, due_date)
            VALUES ('Groceries', 'cat2', 40.00, ?)
        """,
            (due,),
        )
        conn.commit()

        plans = get_upcoming_plans(conn, days=30)
        assert len(plans) == 1
        assert plans[0]["gap"] == 0
        conn.close()

    def test_gap_never_negative(self, tmp_path):
        conn = _setup_test_db(tmp_path)
        due = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d")

        # Emergency Fund has $5000, plan for $100
        conn.execute(
            """
            INSERT INTO planned_expenses (category_name, category_id, amount, due_date)
            VALUES ('Emergency Fund', 'cat3', 100.00, ?)
        """,
            (due,),
        )
        conn.commit()

        plans = get_upcoming_plans(conn, days=30)
        assert plans[0]["gap"] == 0
        conn.close()

    def test_excludes_completed(self, tmp_path):
        conn = _setup_test_db(tmp_path)
        due = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")

        conn.execute(
            """
            INSERT INTO planned_expenses (category_name, category_id, amount, due_date, status)
            VALUES ('Spa & Nails', 'cat1', 165.00, ?, 'completed')
        """,
            (due,),
        )
        conn.commit()

        plans = get_upcoming_plans(conn, days=30)
        assert len(plans) == 0
        conn.close()

    def test_excludes_beyond_window(self, tmp_path):
        conn = _setup_test_db(tmp_path)
        due = (datetime.now() + timedelta(days=60)).strftime("%Y-%m-%d")

        conn.execute(
            """
            INSERT INTO planned_expenses (category_name, category_id, amount, due_date)
            VALUES ('Spa & Nails', 'cat1', 165.00, ?)
        """,
            (due,),
        )
        conn.commit()

        plans = get_upcoming_plans(conn, days=30)
        assert len(plans) == 0
        conn.close()

    def test_includes_overdue(self, tmp_path):
        conn = _setup_test_db(tmp_path)
        due = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")

        conn.execute(
            """
            INSERT INTO planned_expenses (category_name, category_id, amount, due_date)
            VALUES ('Spa & Nails', 'cat1', 165.00, ?)
        """,
            (due,),
        )
        conn.commit()

        plans = get_upcoming_plans(conn, days=30)
        assert len(plans) == 1
        assert plans[0]["overdue"] is True
        conn.close()


class TestCategoryLookup:
    """Test category resolution helpers."""

    def test_find_category_partial_match(self, tmp_path):
        conn = _setup_test_db(tmp_path)
        matches = _find_category(conn, "spa")
        assert len(matches) == 1
        assert matches[0]["name"] == "Spa & Nails"
        conn.close()

    def test_find_category_no_match(self, tmp_path):
        conn = _setup_test_db(tmp_path)
        matches = _find_category(conn, "nonexistent")
        assert len(matches) == 0
        conn.close()

    def test_get_category_balance(self, tmp_path):
        conn = _setup_test_db(tmp_path)
        balance = _get_category_balance(conn, "Spa & Nails")
        assert balance == 60.64
        conn.close()

    def test_get_category_balance_missing(self, tmp_path):
        conn = _setup_test_db(tmp_path)
        balance = _get_category_balance(conn, "Nonexistent")
        assert balance is None
        conn.close()


class TestBudgetIntegration:
    """Test that planned expenses appear in budget check."""

    def test_get_upcoming_plans_sorted_by_date(self, tmp_path):
        conn = _setup_test_db(tmp_path)
        due_later = (datetime.now() + timedelta(days=20)).strftime("%Y-%m-%d")
        due_sooner = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d")

        conn.execute(
            """
            INSERT INTO planned_expenses (category_name, category_id, amount, due_date)
            VALUES ('Spa & Nails', 'cat1', 165.00, ?)
        """,
            (due_later,),
        )
        conn.execute(
            """
            INSERT INTO planned_expenses (category_name, category_id, amount, due_date)
            VALUES ('Groceries', 'cat2', 200.00, ?)
        """,
            (due_sooner,),
        )
        conn.commit()

        plans = get_upcoming_plans(conn, days=30)
        assert len(plans) == 2
        assert plans[0]["due_date"] == due_sooner
        assert plans[1]["due_date"] == due_later
        conn.close()

    def test_multiple_plans_gap_totals(self, tmp_path):
        conn = _setup_test_db(tmp_path)
        due = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")

        # Spa & Nails: $60.64 balance, need $165 → gap $104.36
        conn.execute(
            """
            INSERT INTO planned_expenses (category_name, category_id, amount, due_date)
            VALUES ('Spa & Nails', 'cat1', 165.00, ?)
        """,
            (due,),
        )
        # Groceries: $50 balance, need $200 → gap $150
        conn.execute(
            """
            INSERT INTO planned_expenses (category_name, category_id, amount, due_date)
            VALUES ('Groceries', 'cat2', 200.00, ?)
        """,
            (due,),
        )
        conn.commit()

        plans = get_upcoming_plans(conn, days=30)
        total_gap = sum(p["gap"] for p in plans)
        assert abs(total_gap - 254.36) < 0.01
        conn.close()
