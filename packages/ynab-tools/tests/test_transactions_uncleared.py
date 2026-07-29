"""Regression tests for run_recent(uncleared=True) mishandling 'reconciled' status (issue #127).

YNAB's cleared field has three states: 'cleared', 'uncleared', 'reconciled'. Only
'uncleared' represents a genuinely pending transaction -- 'reconciled' is a fully
cleared, locked state (more final than plain 'cleared'). The uncleared filter must
match only 'uncleared', not everything that isn't literally 'cleared'.
"""

from datetime import datetime
from unittest.mock import patch

from ynab_tools.db import get_connection as _real_get_connection
from ynab_tools.db import init_db
from ynab_tools.reports.transactions import run_recent


def _insert_txn(conn, txn_id, payee, cleared, category="Groceries", account="acct1", account_name="Checking"):
    today = datetime.now().strftime("%Y-%m-%d")
    conn.execute(
        """
        INSERT INTO transactions
        (id, date, amount, memo, cleared, approved, account_id, account_name,
         payee_id, payee_name, category_id, category_name, transfer_account_id,
         import_payee_name, import_payee_name_original, deleted)
        VALUES (?, ?, -25.00, NULL, ?, 1, ?, ?, NULL, ?, NULL, ?, NULL, NULL, NULL, 0)
        """,
        (txn_id, today, cleared, account, account_name, payee, category),
    )


def _insert_subtransaction(conn, sub_id, transaction_id, category, amount=-140.00, payee_name=None):
    conn.execute(
        """
        INSERT INTO subtransactions
        (id, transaction_id, amount, memo, payee_id, payee_name, category_id,
         category_name, transfer_account_id, deleted)
        VALUES (?, ?, ?, NULL, NULL, ?, NULL, ?, NULL, 0)
        """,
        (sub_id, transaction_id, amount, payee_name, category),
    )


def _setup_test_db(tmp_path):
    db_path = tmp_path / "test.db"
    conn = _real_get_connection(db_path)
    init_db(conn)
    conn.execute(
        "INSERT INTO accounts (id, name, type, on_budget, closed, deleted, balance) "
        "VALUES ('acct1', 'Checking', 'checking', 1, 0, 0, 5000)"
    )
    _insert_txn(conn, "t_reconciled", "Merchant A", "reconciled")
    _insert_txn(conn, "t_cleared", "Walmart", "cleared")
    _insert_txn(conn, "t_uncleared", "Pending Merchant", "uncleared")
    conn.commit()
    conn.close()
    return db_path


def _setup_split_test_db(tmp_path):
    """Parent transaction reconciled, with 3 subtransactions split across categories."""
    db_path = tmp_path / "test_split.db"
    conn = _real_get_connection(db_path)
    init_db(conn)
    conn.execute(
        "INSERT INTO accounts (id, name, type, on_budget, closed, deleted, balance) "
        "VALUES ('acct1', 'Checking', 'checking', 1, 0, 0, 5000)"
    )
    _insert_txn(conn, "t_split_reconciled", "Costco", "reconciled", category="Split")
    _insert_subtransaction(conn, "s1", "t_split_reconciled", "Groceries", amount=-280.00)
    _insert_subtransaction(conn, "s2", "t_split_reconciled", "Person A: Misc", amount=-70.00)
    _insert_subtransaction(conn, "s3", "t_split_reconciled", "Household Goods", amount=-70.00)
    conn.commit()
    conn.close()
    return db_path


class TestUnclearedFilter:
    def test_reconciled_transactions_excluded_from_uncleared(self, tmp_path, capsys):
        db_path = _setup_test_db(tmp_path)
        with patch(
            "ynab_tools.reports.transactions.get_connection",
            side_effect=lambda: _real_get_connection(db_path),
        ):
            run_recent(uncleared=True, limit=50)
        output = capsys.readouterr().out
        assert "Merchant A" not in output

    def test_cleared_transactions_excluded_from_uncleared(self, tmp_path, capsys):
        db_path = _setup_test_db(tmp_path)
        with patch(
            "ynab_tools.reports.transactions.get_connection",
            side_effect=lambda: _real_get_connection(db_path),
        ):
            run_recent(uncleared=True, limit=50)
        output = capsys.readouterr().out
        assert "Walmart" not in output

    def test_genuinely_uncleared_transaction_still_shown(self, tmp_path, capsys):
        db_path = _setup_test_db(tmp_path)
        with patch(
            "ynab_tools.reports.transactions.get_connection",
            side_effect=lambda: _real_get_connection(db_path),
        ):
            run_recent(uncleared=True, limit=50)
        output = capsys.readouterr().out
        assert "Pending Merchant" in output

    def test_unfiltered_shows_all_three(self, tmp_path, capsys):
        db_path = _setup_test_db(tmp_path)
        with patch(
            "ynab_tools.reports.transactions.get_connection",
            side_effect=lambda: _real_get_connection(db_path),
        ):
            run_recent(limit=50)
        output = capsys.readouterr().out
        assert "Merchant A" in output
        assert "Walmart" in output
        assert "Pending Merchant" in output

    def test_reconciled_split_subtransaction_excluded_from_uncleared(self, tmp_path, capsys):
        """A split transaction's cleared status lives on the parent row, not the
        subtransaction. A reconciled parent must exclude ALL of its split lines
        from the uncleared filter, even though each line has its own category.
        """
        db_path = _setup_split_test_db(tmp_path)
        with patch(
            "ynab_tools.reports.transactions.get_connection",
            side_effect=lambda: _real_get_connection(db_path),
        ):
            run_recent(uncleared=True, limit=50, category="Person A: Misc")
        output = capsys.readouterr().out
        assert "Costco" not in output
        assert "No transactions found" in output
