"""Tests for run_fund: set amount, delta adjustment, invalid amount, API budgeted=0."""

from unittest.mock import MagicMock, patch

from ynab_tools.db import get_connection as _real_get_connection
from ynab_tools.db import init_db
from ynab_tools.reports.funding import run_fund


def _setup_test_db(tmp_path):
    from datetime import datetime

    db_path = tmp_path / "test.db"
    conn = _real_get_connection(db_path)
    init_db(conn)

    month = datetime.now().strftime("%Y-%m-01")
    now_iso = datetime.now().isoformat()

    conn.execute(
        "INSERT INTO budget_months (month, income, budgeted, activity, to_be_budgeted, age_of_money, last_synced_at) "
        "VALUES (?, 5000, 4500, -4200, 800, 30, ?)",
        (month, now_iso),
    )
    conn.execute(
        "INSERT INTO budget_categories "
        "(id, budget_month, category_group_name, name, hidden, deleted,"
        " budgeted, activity, balance, goal_type, goal_target, goal_under_funded) "
        "VALUES ('cat1', ?, 'Bills', 'Groceries', 0, 0, 900, -850, 50, 'MF', 900, 0)",
        (month,),
    )
    conn.commit()
    conn.close()
    return db_path, month


def _mock_api_response(budgeted_mu: int) -> dict:
    return {"category": {"budgeted": budgeted_mu, "balance": 0, "goal_under_funded": 0}}


class TestRunFund:
    def test_set_absolute_amount(self, tmp_path):
        db_path, month = _setup_test_db(tmp_path)
        mock_client = MagicMock()
        mock_client.update_category_budget.return_value = _mock_api_response(1_000_000)

        with (
            patch("ynab_tools.reports.funding.get_connection", side_effect=lambda: _real_get_connection(db_path)),
            patch("ynab_tools.reports.funding.require_credentials", return_value=("tok", "plan")),
            patch("ynab_tools.reports.funding.YNABClient", return_value=mock_client),
        ):
            run_fund("Groceries", "1000", month=month, apply=True)

        conn = _real_get_connection(db_path, init=False)
        row = conn.execute(
            "SELECT budgeted FROM budget_categories WHERE id='cat1' AND budget_month=?",
            (month,),
        ).fetchone()
        conn.close()
        assert row["budgeted"] == 1000.0
        mock_client.update_category_budget.assert_called_once_with(month, "cat1", 1_000_000)

    def test_delta_adjustment(self, tmp_path):
        db_path, month = _setup_test_db(tmp_path)
        mock_client = MagicMock()
        mock_client.update_category_budget.return_value = _mock_api_response(1_000_000)

        with (
            patch("ynab_tools.reports.funding.get_connection", side_effect=lambda: _real_get_connection(db_path)),
            patch("ynab_tools.reports.funding.require_credentials", return_value=("tok", "plan")),
            patch("ynab_tools.reports.funding.YNABClient", return_value=mock_client),
        ):
            run_fund("Groceries", "+100", month=month, apply=True)

        mock_client.update_category_budget.assert_called_once_with(month, "cat1", 1_000_000)

    def test_invalid_amount_does_not_call_api(self, tmp_path, capsys):
        db_path, month = _setup_test_db(tmp_path)
        mock_client = MagicMock()

        with (
            patch("ynab_tools.reports.funding.get_connection", side_effect=lambda: _real_get_connection(db_path)),
            patch("ynab_tools.reports.funding.require_credentials", return_value=("tok", "plan")),
            patch("ynab_tools.reports.funding.YNABClient", return_value=mock_client),
        ):
            run_fund("Groceries", "not-a-number", month=month, apply=True)

        out = capsys.readouterr().out
        assert "Invalid amount" in out
        mock_client.update_category_budget.assert_not_called()

    def test_api_returns_budgeted_zero_trusts_api(self, tmp_path):
        """When API echoes budgeted=0, local DB should reflect 0 (trusting API over fallback)."""
        db_path, month = _setup_test_db(tmp_path)
        mock_client = MagicMock()
        mock_client.update_category_budget.return_value = _mock_api_response(0)

        with (
            patch("ynab_tools.reports.funding.get_connection", side_effect=lambda: _real_get_connection(db_path)),
            patch("ynab_tools.reports.funding.require_credentials", return_value=("tok", "plan")),
            patch("ynab_tools.reports.funding.YNABClient", return_value=mock_client),
        ):
            run_fund("Groceries", "1000", month=month, apply=True)

        conn = _real_get_connection(db_path, init=False)
        row = conn.execute(
            "SELECT budgeted FROM budget_categories WHERE id='cat1' AND budget_month=?",
            (month,),
        ).fetchone()
        conn.close()
        assert row["budgeted"] == 0.0
