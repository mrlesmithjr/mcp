"""Tests for run_unapproved: single filtered API fetch instead of per-row calls (issue #77)."""

from datetime import datetime
from unittest.mock import MagicMock, patch

from ynab_tools.db import get_connection as _real_get_connection
from ynab_tools.db import init_db
from ynab_tools.reports.transactions import run_unapproved


def _setup_test_db(tmp_path, unapproved_count: int) -> tuple:
    db_path = tmp_path / "test.db"
    conn = _real_get_connection(db_path)
    init_db(conn)

    conn.execute(
        "INSERT INTO accounts (id, name, type, on_budget, closed, deleted, balance) "
        "VALUES ('acct1', 'Checking', 'checking', 1, 0, 0, 5000)"
    )

    today = datetime.now().strftime("%Y-%m-%d")
    for i in range(unapproved_count):
        conn.execute(
            """
            INSERT INTO transactions
            (id, date, amount, memo, cleared, approved, account_id, account_name,
             payee_id, payee_name, category_id, category_name, transfer_account_id,
             import_payee_name, import_payee_name_original, deleted)
            VALUES (?, ?, -25.00, NULL, 'cleared', 0, 'acct1', 'Checking',
                    NULL, ?, NULL, 'Groceries', NULL, NULL, NULL, 0)
            """,
            (f"t{i}", today, f"Payee {i}"),
        )
    conn.commit()
    conn.close()
    return db_path


class TestRunUnapprovedApiCallCount:
    def test_single_fetch_regardless_of_row_count(self, tmp_path, capsys):
        """20 unapproved rows must not translate into 20 client calls."""
        db_path = _setup_test_db(tmp_path, unapproved_count=20)
        mock_client = MagicMock()
        mock_client.get_transactions.return_value = {"transactions": []}

        with (
            patch(
                "ynab_tools.reports.transactions.get_connection",
                side_effect=lambda: _real_get_connection(db_path),
            ),
            patch("ynab_tools.reports.transactions.require_credentials", return_value=("tok", "plan")),
            patch("ynab_tools.reports.transactions.YNABClient", return_value=mock_client),
        ):
            run_unapproved()

        # Constant number of calls (exactly one), never one per unapproved row.
        mock_client.get_transactions.assert_called_once()
        assert mock_client.get_transaction.call_count == 0

    def test_no_api_call_when_all_rows_already_approved(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = _real_get_connection(db_path)
        init_db(conn)
        conn.execute(
            "INSERT INTO accounts (id, name, type, on_budget, closed, deleted, balance) "
            "VALUES ('acct1', 'Checking', 'checking', 1, 0, 0, 5000)"
        )
        today = datetime.now().strftime("%Y-%m-%d")
        conn.execute(
            """
            INSERT INTO transactions
            (id, date, amount, memo, cleared, approved, account_id, account_name,
             payee_id, payee_name, category_id, category_name, transfer_account_id,
             import_payee_name, import_payee_name_original, deleted)
            VALUES ('t1', ?, -25.00, NULL, 'cleared', 1, 'acct1', 'Checking',
                    NULL, 'Payee', NULL, NULL, NULL, NULL, NULL, 0)
            """,
            (today,),
        )
        conn.commit()
        conn.close()

        mock_client = MagicMock()

        with (
            patch(
                "ynab_tools.reports.transactions.get_connection",
                side_effect=lambda: _real_get_connection(db_path),
            ),
            patch("ynab_tools.reports.transactions.require_credentials", return_value=("tok", "plan")),
            patch("ynab_tools.reports.transactions.YNABClient", return_value=mock_client),
        ):
            run_unapproved()

        # Row is approved but has no category -> still surfaced as needing category,
        # but no API verification call should be made since it's already approved.
        mock_client.get_transactions.assert_not_called()

    def test_stale_approval_is_updated_from_filtered_fetch(self, tmp_path):
        """A row that YNAB reports as approved (delta sync missed it) is fixed
        locally from the single get_transactions() response, without any
        get_transaction() per-id call."""
        db_path = _setup_test_db(tmp_path, unapproved_count=1)
        mock_client = MagicMock()
        mock_client.get_transactions.return_value = {
            "transactions": [{"id": "t0", "approved": True}],
        }

        with (
            patch(
                "ynab_tools.reports.transactions.get_connection",
                side_effect=lambda: _real_get_connection(db_path),
            ),
            patch("ynab_tools.reports.transactions.require_credentials", return_value=("tok", "plan")),
            patch("ynab_tools.reports.transactions.YNABClient", return_value=mock_client),
        ):
            run_unapproved()

        mock_client.get_transactions.assert_called_once()
        mock_client.get_transaction.assert_not_called()

        conn = _real_get_connection(db_path, init=False)
        row = conn.execute("SELECT approved FROM transactions WHERE id='t0'").fetchone()
        conn.close()
        assert row["approved"] == 1
