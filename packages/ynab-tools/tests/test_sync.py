"""Tests for ynab_tools/sync.py - server knowledge, milliunits, and account sync."""

import sqlite3
from unittest.mock import MagicMock

import pytest

from ynab_tools.db import get_connection, init_db
from ynab_tools.sync import (
    backfill_category_group_names,
    get_server_knowledge,
    milliunits_to_dollars,
    save_server_knowledge,
    sync_accounts,
    sync_plan_export,
    sync_transactions,
)


def _setup_test_db(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_db(conn)
    conn.commit()
    return conn


class TestMilliunitsToDollars:
    def test_positive_value(self):
        assert milliunits_to_dollars(1000) == 1.0

    def test_negative_value(self):
        assert milliunits_to_dollars(-500) == -0.5

    def test_none_returns_none(self):
        assert milliunits_to_dollars(None) is None

    def test_zero(self):
        assert milliunits_to_dollars(0) == 0.0

    def test_large_value(self):
        assert milliunits_to_dollars(1_234_567) == pytest.approx(1234.567)

    def test_fractional_result(self):
        assert milliunits_to_dollars(2500) == 2.5


class TestGetServerKnowledge:
    def test_returns_none_when_no_row(self, tmp_path):
        conn = _setup_test_db(tmp_path)
        result = get_server_knowledge(conn, "accounts")
        assert result is None
        conn.close()

    def test_returns_stored_value(self, tmp_path):
        conn = _setup_test_db(tmp_path)
        save_server_knowledge(conn, "accounts", 42)
        result = get_server_knowledge(conn, "accounts")
        assert result == 42
        conn.close()

    def test_returns_none_when_table_missing(self, tmp_path):
        # Connect to a fresh DB with no schema at all
        db_path = tmp_path / "empty.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        result = get_server_knowledge(conn, "accounts")
        assert result is None
        conn.close()

    def test_different_endpoints_are_independent(self, tmp_path):
        conn = _setup_test_db(tmp_path)
        save_server_knowledge(conn, "accounts", 10)
        save_server_knowledge(conn, "transactions", 20)
        assert get_server_knowledge(conn, "accounts") == 10
        assert get_server_knowledge(conn, "transactions") == 20
        assert get_server_knowledge(conn, "payees") is None
        conn.close()


class TestSaveServerKnowledge:
    def test_persists_with_default_commit(self, tmp_path):
        conn = _setup_test_db(tmp_path)
        save_server_knowledge(conn, "accounts", 99)
        # Verify by querying directly
        row = conn.execute("SELECT server_knowledge FROM sync_state WHERE endpoint = ?", ("accounts",)).fetchone()
        assert row is not None
        assert row["server_knowledge"] == 99
        conn.close()

    def test_commit_false_not_persisted_after_rollback(self, tmp_path):
        conn = _setup_test_db(tmp_path)
        save_server_knowledge(conn, "accounts", 77, commit=False)
        # Without commit, rollback should discard the row
        conn.rollback()
        row = conn.execute("SELECT server_knowledge FROM sync_state WHERE endpoint = ?", ("accounts",)).fetchone()
        assert row is None
        conn.close()

    def test_idempotent_insert_or_replace(self, tmp_path):
        conn = _setup_test_db(tmp_path)
        save_server_knowledge(conn, "accounts", 1)
        save_server_knowledge(conn, "accounts", 2)
        # Should have only one row, with the latest value
        rows = conn.execute("SELECT server_knowledge FROM sync_state WHERE endpoint = ?", ("accounts",)).fetchall()
        assert len(rows) == 1
        assert rows[0]["server_knowledge"] == 2
        conn.close()

    def test_updates_timestamp(self, tmp_path):
        conn = _setup_test_db(tmp_path)
        save_server_knowledge(conn, "accounts", 5)
        row = conn.execute("SELECT updated_at FROM sync_state WHERE endpoint = ?", ("accounts",)).fetchone()
        assert row is not None
        assert row["updated_at"]  # non-empty timestamp
        conn.close()


def _make_account_dict(
    acct_id: str = "acct-1",
    name: str = "Checking",
    acct_type: str = "checking",
    balance: int = 5000000,
) -> dict:
    """Build a minimal account dict in YNAB milliunit format."""
    return {
        "id": acct_id,
        "name": name,
        "type": acct_type,
        "on_budget": True,
        "closed": False,
        "deleted": False,
        "balance": balance,
        "cleared_balance": balance,
        "uncleared_balance": 0,
        "note": None,
        "last_reconciled_at": None,
        "direct_import_linked": False,
        "debt_interest_rates": None,
        "debt_minimum_payments": None,
        "debt_original_balance": None,
        "debt_escrow_amounts": None,
    }


class TestSyncAccounts:
    def test_first_sync_inserts_accounts(self, tmp_path):
        conn = _setup_test_db(tmp_path)

        client = MagicMock()
        client.get_accounts.return_value = {
            "accounts": [_make_account_dict("acct-1", "Checking", balance=5_000_000)],
            "server_knowledge": 100,
        }

        count = sync_accounts(conn, client)
        assert count == 1

        row = conn.execute("SELECT * FROM accounts WHERE id = ?", ("acct-1",)).fetchone()
        assert row is not None
        assert row["name"] == "Checking"
        assert row["balance"] == pytest.approx(5000.0)
        conn.close()

    def test_first_sync_saves_server_knowledge(self, tmp_path):
        conn = _setup_test_db(tmp_path)

        client = MagicMock()
        client.get_accounts.return_value = {
            "accounts": [_make_account_dict()],
            "server_knowledge": 42,
        }

        sync_accounts(conn, client)
        assert get_server_knowledge(conn, "accounts") == 42
        conn.close()

    def test_delta_sync_no_changes_does_not_error(self, tmp_path):
        conn = _setup_test_db(tmp_path)

        # Seed initial knowledge so delta path is taken
        save_server_knowledge(conn, "accounts", 10)

        client = MagicMock()
        # Delta returns empty list - no changes
        client.get_accounts.return_value = {
            "accounts": [],
            "server_knowledge": 11,
        }

        count = sync_accounts(conn, client)
        assert count == 0
        # Knowledge should be updated to latest
        assert get_server_knowledge(conn, "accounts") == 11
        conn.close()

    def test_sync_called_with_existing_knowledge(self, tmp_path):
        conn = _setup_test_db(tmp_path)
        save_server_knowledge(conn, "accounts", 55)

        client = MagicMock()
        client.get_accounts.return_value = {"accounts": [], "server_knowledge": 56}

        sync_accounts(conn, client)
        # Verify get_accounts was called with the stored knowledge
        client.get_accounts.assert_called_once_with(server_knowledge=55)
        conn.close()

    def test_sync_multiple_accounts(self, tmp_path):
        conn = _setup_test_db(tmp_path)

        client = MagicMock()
        client.get_accounts.return_value = {
            "accounts": [
                _make_account_dict("a1", "Checking", balance=2_000_000),
                _make_account_dict("a2", "Savings", balance=10_000_000),
            ],
            "server_knowledge": 200,
        }

        count = sync_accounts(conn, client)
        assert count == 2

        rows = conn.execute("SELECT id, name, balance FROM accounts ORDER BY id").fetchall()
        assert len(rows) == 2
        names = [r["name"] for r in rows]
        assert "Checking" in names
        assert "Savings" in names
        conn.close()

    def test_sync_accounts_upsert_on_repeat(self, tmp_path):
        conn = _setup_test_db(tmp_path)

        client = MagicMock()
        client.get_accounts.return_value = {
            "accounts": [_make_account_dict("acct-1", "Checking", balance=1_000_000)],
            "server_knowledge": 1,
        }
        sync_accounts(conn, client)

        # Second call with updated balance
        client.get_accounts.return_value = {
            "accounts": [_make_account_dict("acct-1", "Checking", balance=2_000_000)],
            "server_knowledge": 2,
        }
        sync_accounts(conn, client)

        rows = conn.execute("SELECT balance FROM accounts WHERE id = ?", ("acct-1",)).fetchall()
        assert len(rows) == 1  # not duplicated
        assert rows[0]["balance"] == pytest.approx(2000.0)
        conn.close()


def _make_split_transaction(txn_id: str, sub_ids: list[str], amounts_milliunits: list[int]) -> dict:
    """Build a split transaction dict in YNAB milliunit format."""
    total = sum(amounts_milliunits)
    return {
        "id": txn_id,
        "date": "2026-06-01",
        "amount": total,
        "memo": None,
        "cleared": "cleared",
        "approved": True,
        "flag_color": None,
        "flag_name": None,
        "account_id": "acct-1",
        "account_name": "Checking",
        "payee_id": None,
        "payee_name": "Walmart",
        "category_id": None,
        "category_name": "Split",
        "transfer_account_id": None,
        "debt_transaction_type": None,
        "import_id": None,
        "import_payee_name": None,
        "import_payee_name_original": None,
        "matched_transaction_id": None,
        "deleted": False,
        "subtransactions": [
            {
                "id": sub_id,
                "transaction_id": txn_id,
                "amount": amt,
                "memo": None,
                "payee_id": None,
                "payee_name": None,
                "category_id": f"cat-{i}",
                "category_name": f"Category {i}",
                "transfer_account_id": None,
                "deleted": False,
            }
            for i, (sub_id, amt) in enumerate(zip(sub_ids, amounts_milliunits))
        ],
    }


class TestSubtransactionDedupSyncTransactions:
    """sync_transactions() must delete old subtransaction rows before inserting new ones."""

    def test_edited_split_does_not_duplicate_subtransactions(self, tmp_path):
        conn = _setup_test_db(tmp_path)

        txn = _make_split_transaction("txn-1", ["st-1", "st-2"], [-30_000, -20_000])

        client = MagicMock()
        client.get_transactions.return_value = {
            "transactions": [txn],
            "server_knowledge": 1,
        }
        sync_transactions(conn, client, months_back=12)

        # Simulate YNAB edit: same transaction, regenerated subtransaction IDs
        txn_edited = _make_split_transaction("txn-1", ["st-3", "st-4"], [-30_000, -20_000])
        client.get_transactions.return_value = {
            "transactions": [txn_edited],
            "server_knowledge": 2,
        }
        sync_transactions(conn, client, months_back=12)

        rows = conn.execute(
            "SELECT id, amount FROM subtransactions WHERE transaction_id = ? ORDER BY id",
            ("txn-1",),
        ).fetchall()
        assert len(rows) == 2, f"Expected 2 subtransactions, got {len(rows)}: {[r['id'] for r in rows]}"
        ids = {r["id"] for r in rows}
        assert ids == {"st-3", "st-4"}, f"Expected new IDs st-3/st-4, got {ids}"
        total = sum(r["amount"] for r in rows)
        assert total == pytest.approx(-50.0)

        conn.close()

    def test_unchanged_transaction_subtransactions_not_deleted(self, tmp_path):
        conn = _setup_test_db(tmp_path)
        txn = _make_split_transaction("txn-1", ["st-1", "st-2"], [-30_000, -20_000])
        client = MagicMock()
        client.get_transactions.return_value = {"transactions": [txn], "server_knowledge": 1}
        sync_transactions(conn, client, months_back=12)

        # Delta sync: only txn-2 in payload; txn-1 is absent (unchanged -- not in the delta)
        txn2 = _make_split_transaction("txn-2", ["st-5", "st-6"], [-10_000, -5_000])
        client.get_transactions.return_value = {"transactions": [txn2], "server_knowledge": 2}
        sync_transactions(conn, client, months_back=12)

        rows = conn.execute("SELECT id FROM subtransactions WHERE transaction_id = ?", ("txn-1",)).fetchall()
        assert len(rows) == 2, "txn-1 subtransactions must survive when not in delta payload"
        conn.close()

    def test_unsplit_transaction_removes_orphaned_subtransactions(self, tmp_path):
        """Regression for issue #28: converting a split back to regular must delete subtransaction rows."""
        conn = _setup_test_db(tmp_path)

        # First sync: split transaction with two subtransactions
        txn_split = _make_split_transaction("txn-1", ["st-1", "st-2"], [-30_000, -20_000])
        client = MagicMock()
        client.get_transactions.return_value = {"transactions": [txn_split], "server_knowledge": 1}
        sync_transactions(conn, client, months_back=12)

        rows = conn.execute("SELECT id FROM subtransactions WHERE transaction_id = ?", ("txn-1",)).fetchall()
        assert len(rows) == 2, "setup: expected 2 subtransaction rows after split sync"

        # Second sync: same transaction_id, but now a regular (non-split) transaction with empty subtransactions
        txn_regular = {
            "id": "txn-1",
            "date": "2026-06-01",
            "amount": -50_000,
            "memo": None,
            "cleared": "cleared",
            "approved": True,
            "flag_color": None,
            "flag_name": None,
            "account_id": "acct-1",
            "account_name": "Checking",
            "payee_id": None,
            "payee_name": "Walmart",
            "category_id": "cat-0",
            "category_name": "Groceries",
            "transfer_account_id": None,
            "debt_transaction_type": None,
            "import_id": None,
            "import_payee_name": None,
            "import_payee_name_original": None,
            "matched_transaction_id": None,
            "deleted": False,
            "subtransactions": [],
        }
        client.get_transactions.return_value = {"transactions": [txn_regular], "server_knowledge": 2}
        sync_transactions(conn, client, months_back=12)

        rows = conn.execute("SELECT id FROM subtransactions WHERE transaction_id = ?", ("txn-1",)).fetchall()
        assert len(rows) == 0, (
            f"Expected 0 subtransaction rows after un-splitting, got {len(rows)}: {[r['id'] for r in rows]}"
        )

        conn.close()


class TestSubtransactionDedupSyncPlanExport:
    """sync_plan_export() must delete old subtransaction rows before inserting new ones."""

    def _make_plan(self, txn_id: str, sub_ids: list[str], amounts_milliunits: list[int]) -> dict:
        total = sum(amounts_milliunits)
        subtransactions = [
            {
                "id": sub_id,
                "transaction_id": txn_id,
                "amount": amt,
                "memo": None,
                "payee_id": None,
                "category_id": f"cat-{i}",
                "transfer_account_id": None,
                "deleted": False,
            }
            for i, (sub_id, amt) in enumerate(zip(sub_ids, amounts_milliunits))
        ]
        return {
            "accounts": [],
            "payees": [],
            "months": [],
            "categories": [],
            "transactions": [
                {
                    "id": txn_id,
                    "date": "2026-06-01",
                    "amount": total,
                    "memo": None,
                    "cleared": "cleared",
                    "approved": True,
                    "flag_color": None,
                    "flag_name": None,
                    "account_id": None,
                    "payee_id": None,
                    "category_id": None,
                    "transfer_account_id": None,
                    "debt_transaction_type": None,
                    "import_id": None,
                    "import_payee_name": None,
                    "import_payee_name_original": None,
                    "matched_transaction_id": None,
                    "deleted": False,
                }
            ],
            "subtransactions": subtransactions,
        }

    def _make_client(self, plan: dict, knowledge: int) -> MagicMock:
        client = MagicMock()
        client.get_plan_detail.return_value = {"plan": plan, "server_knowledge": knowledge}
        client.get_categories.return_value = {"category_groups": []}
        return client

    def test_edited_split_does_not_duplicate_subtransactions(self, tmp_path):
        conn = _setup_test_db(tmp_path)

        plan1 = self._make_plan("txn-1", ["st-1", "st-2"], [-30_000, -20_000])
        client = self._make_client(plan1, knowledge=1)
        sync_plan_export(conn, client)

        # Simulate YNAB edit: same transaction, regenerated subtransaction IDs
        plan2 = self._make_plan("txn-1", ["st-3", "st-4"], [-30_000, -20_000])
        client = self._make_client(plan2, knowledge=2)
        sync_plan_export(conn, client)

        rows = conn.execute(
            "SELECT id, amount FROM subtransactions WHERE transaction_id = ? ORDER BY id",
            ("txn-1",),
        ).fetchall()
        assert len(rows) == 2, f"Expected 2 subtransactions, got {len(rows)}: {[r['id'] for r in rows]}"
        ids = {r["id"] for r in rows}
        assert ids == {"st-3", "st-4"}, f"Expected new IDs st-3/st-4, got {ids}"
        total = sum(r["amount"] for r in rows)
        assert total == pytest.approx(-50.0)

        conn.close()

    def test_unchanged_transaction_subtransactions_not_deleted(self, tmp_path):
        """Delta sync must not delete subtransactions for transactions not in payload."""
        conn = _setup_test_db(tmp_path)

        plan1 = self._make_plan("txn-1", ["st-1", "st-2"], [-30_000, -20_000])
        client = self._make_client(plan1, knowledge=1)
        sync_plan_export(conn, client)

        # Second sync: different transaction only (txn-1 is unchanged, not in payload)
        plan2 = self._make_plan("txn-2", ["st-5", "st-6"], [-10_000, -5_000])
        client = self._make_client(plan2, knowledge=2)
        sync_plan_export(conn, client)

        rows_txn1 = conn.execute(
            "SELECT id FROM subtransactions WHERE transaction_id = ?",
            ("txn-1",),
        ).fetchall()
        assert len(rows_txn1) == 2, "txn-1 subtransactions should be preserved when not in payload"

        conn.close()

    def test_unsplit_transaction_removes_orphaned_subtransactions(self, tmp_path):
        """Regression for issue #28: converting a split back to regular must delete subtransaction rows."""
        conn = _setup_test_db(tmp_path)

        # First sync: split transaction
        plan1 = self._make_plan("txn-1", ["st-1", "st-2"], [-30_000, -20_000])
        client = self._make_client(plan1, knowledge=1)
        sync_plan_export(conn, client)

        rows = conn.execute("SELECT id FROM subtransactions WHERE transaction_id = ?", ("txn-1",)).fetchall()
        assert len(rows) == 2, "setup: expected 2 subtransaction rows after split sync"

        # Second sync: same transaction_id, no subtransactions in the top-level array (un-split)
        plan2 = {
            "accounts": [],
            "payees": [],
            "months": [],
            "categories": [],
            "transactions": [
                {
                    "id": "txn-1",
                    "date": "2026-06-01",
                    "amount": -50_000,
                    "memo": None,
                    "cleared": "cleared",
                    "approved": True,
                    "flag_color": None,
                    "flag_name": None,
                    "account_id": None,
                    "payee_id": None,
                    "category_id": "cat-0",
                    "transfer_account_id": None,
                    "debt_transaction_type": None,
                    "import_id": None,
                    "import_payee_name": None,
                    "import_payee_name_original": None,
                    "matched_transaction_id": None,
                    "deleted": False,
                }
            ],
            # No subtransactions for txn-1: it was un-split
            "subtransactions": [],
        }
        client2 = self._make_client(plan2, knowledge=2)
        sync_plan_export(conn, client2)

        rows = conn.execute("SELECT id FROM subtransactions WHERE transaction_id = ?", ("txn-1",)).fetchall()
        assert len(rows) == 0, (
            f"Expected 0 subtransaction rows after un-splitting, got {len(rows)}: {[r['id'] for r in rows]}"
        )

        conn.close()


class TestSubtransactionDedupMigration:
    """_run_migrations() must clean up existing duplicate subtransaction rows."""

    def test_migration_removes_stale_rows(self, tmp_path):
        conn = _setup_test_db(tmp_path)

        # Insert a parent transaction
        conn.execute(
            """INSERT OR REPLACE INTO transactions
               (id, date, amount, cleared, approved, deleted, last_synced_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("txn-1", "2026-06-01", -50.0, "cleared", 1, 0, "2026-06-01T00:00:00+00:00"),
        )

        # Insert 4 subtransactions: 2 with old timestamp, 2 with newer timestamp
        old_ts = "2026-06-01T10:00:00+00:00"
        new_ts = "2026-06-15T10:00:00+00:00"
        for sub_id, ts in [("st-old-1", old_ts), ("st-old-2", old_ts), ("st-new-1", new_ts), ("st-new-2", new_ts)]:
            conn.execute(
                """INSERT INTO subtransactions
                   (id, transaction_id, amount, deleted, last_synced_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (sub_id, "txn-1", -25.0, 0, ts),
            )
        conn.commit()

        # Verify we have 4 rows before migration
        count_before = conn.execute(
            "SELECT COUNT(*) FROM subtransactions WHERE transaction_id = ?", ("txn-1",)
        ).fetchone()[0]
        assert count_before == 4

        # Re-run init_db to trigger _run_migrations() cleanup
        init_db(conn)

        rows = conn.execute(
            "SELECT id FROM subtransactions WHERE transaction_id = ? ORDER BY id",
            ("txn-1",),
        ).fetchall()
        assert len(rows) == 2, f"Expected 2 rows after migration, got {len(rows)}: {[r['id'] for r in rows]}"
        ids = {r["id"] for r in rows}
        assert ids == {"st-new-1", "st-new-2"}, f"Expected new-timestamp rows to survive, got {ids}"

        conn.close()


def _insert_budget_category(
    conn,
    cat_id: str,
    month: str,
    name: str = "Some Category",
    group_id: str = "grp-1",
    group_name: str = "Group",
    hidden: int = 0,
    deleted: int = 0,
) -> None:
    """Insert a minimal budget_categories row for reconciliation tests."""
    conn.execute(
        """INSERT OR REPLACE INTO budget_categories
           (id, budget_month, category_group_id, category_group_name, name, hidden, deleted, last_synced_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (cat_id, month, group_id, group_name, name, hidden, deleted, "2026-07-01T00:00:00+00:00"),
    )
    conn.commit()


def _make_categories_client(category_groups: list[dict]) -> MagicMock:
    client = MagicMock()
    client.get_categories.return_value = {"category_groups": category_groups}
    return client


class TestBackfillCategoryGroupNamesReconciliation:
    """Regression tests for issue #134: categories deleted/merged in YNAB without an
    explicit deleted=true tombstone must be reconciled locally, not left stale."""

    def test_category_missing_from_endpoint_is_marked_deleted(self, tmp_path):
        conn = _setup_test_db(tmp_path)

        # Two months' worth of rows for a category that has since been merged away in YNAB.
        _insert_budget_category(conn, "cat-gone", "2026-06-01", name="Concerts")
        _insert_budget_category(conn, "cat-gone", "2026-07-01", name="Concerts")
        # A category that is still alive should be untouched.
        _insert_budget_category(conn, "cat-alive", "2026-07-01", name="Entertainment")

        client = _make_categories_client(
            [
                {
                    "id": "grp-1",
                    "name": "Group",
                    "categories": [{"id": "cat-alive", "hidden": False, "deleted": False}],
                }
            ]
        )

        group_updated, deleted_count = backfill_category_group_names(conn, client)
        assert deleted_count == 2, "expected both stale-month rows for the merged-away category to flip deleted"

        rows = conn.execute(
            "SELECT budget_month, deleted FROM budget_categories WHERE id = ?", ("cat-gone",)
        ).fetchall()
        assert all(r["deleted"] == 1 for r in rows)

        alive = conn.execute("SELECT deleted FROM budget_categories WHERE id = ?", ("cat-alive",)).fetchone()
        assert alive["deleted"] == 0

        conn.close()

    def test_category_still_present_is_not_marked_deleted(self, tmp_path):
        conn = _setup_test_db(tmp_path)
        _insert_budget_category(conn, "cat-alive", "2026-07-01", name="Entertainment")

        client = _make_categories_client(
            [
                {
                    "id": "grp-1",
                    "name": "Group",
                    "categories": [{"id": "cat-alive", "hidden": False, "deleted": False}],
                }
            ]
        )

        _, deleted_count = backfill_category_group_names(conn, client)
        assert deleted_count == 0

        row = conn.execute("SELECT deleted FROM budget_categories WHERE id = ?", ("cat-alive",)).fetchone()
        assert row["deleted"] == 0
        conn.close()

    def test_hidden_flag_synced_from_categories_endpoint(self, tmp_path):
        conn = _setup_test_db(tmp_path)
        _insert_budget_category(conn, "cat-1", "2026-07-01", name="Old Category", hidden=0)

        client = _make_categories_client(
            [
                {
                    "id": "grp-1",
                    "name": "Group",
                    "categories": [{"id": "cat-1", "hidden": True, "deleted": False}],
                }
            ]
        )

        backfill_category_group_names(conn, client)

        row = conn.execute("SELECT hidden, deleted FROM budget_categories WHERE id = ?", ("cat-1",)).fetchone()
        assert row["hidden"] == 1
        assert row["deleted"] == 0
        conn.close()

    def test_explicit_deleted_tombstone_is_applied(self, tmp_path):
        conn = _setup_test_db(tmp_path)
        _insert_budget_category(conn, "cat-1", "2026-07-01", name="Old Category", deleted=0)

        client = _make_categories_client(
            [
                {
                    "id": "grp-1",
                    "name": "Group",
                    "categories": [{"id": "cat-1", "hidden": False, "deleted": True}],
                }
            ]
        )

        backfill_category_group_names(conn, client)

        row = conn.execute("SELECT deleted FROM budget_categories WHERE id = ?", ("cat-1",)).fetchone()
        assert row["deleted"] == 1
        conn.close()

    def test_empty_categories_response_does_not_wipe_existing_categories(self, tmp_path):
        """Safety guard: an empty/incomplete /categories response must never be
        interpreted as 'every category was deleted'."""
        conn = _setup_test_db(tmp_path)
        _insert_budget_category(conn, "cat-1", "2026-07-01", name="Groceries")
        _insert_budget_category(conn, "cat-2", "2026-07-01", name="Rent")

        client = _make_categories_client([])

        _, deleted_count = backfill_category_group_names(conn, client)
        assert deleted_count == 0

        rows = conn.execute("SELECT deleted FROM budget_categories").fetchall()
        assert all(r["deleted"] == 0 for r in rows)
        conn.close()

    def test_group_name_backfill_still_works_alongside_reconciliation(self, tmp_path):
        conn = _setup_test_db(tmp_path)
        _insert_budget_category(conn, "cat-1", "2026-07-01", name="Groceries", group_id="grp-1", group_name=None)

        client = _make_categories_client(
            [
                {
                    "id": "grp-1",
                    "name": "Everyday",
                    "categories": [{"id": "cat-1", "hidden": False, "deleted": False}],
                }
            ]
        )

        group_updated, _ = backfill_category_group_names(conn, client)
        assert group_updated == 1

        row = conn.execute("SELECT category_group_name FROM budget_categories WHERE id = ?", ("cat-1",)).fetchone()
        assert row["category_group_name"] == "Everyday"
        conn.close()

    def test_previously_deleted_category_reappearing_flips_back_to_alive(self, tmp_path):
        """Self-healing: a category that was previously (correctly or falsely) marked
        deleted must flip back to deleted=0 if a later /categories response explicitly
        shows it alive again. The explicit-status pass has no 'only less deleted' guard,
        so this must never get stuck."""
        conn = _setup_test_db(tmp_path)
        _insert_budget_category(conn, "cat-1", "2026-06-01", name="Concerts", deleted=1)
        _insert_budget_category(conn, "cat-1", "2026-07-01", name="Concerts", deleted=1)

        client = _make_categories_client(
            [
                {
                    "id": "grp-1",
                    "name": "Group",
                    "categories": [{"id": "cat-1", "hidden": False, "deleted": False}],
                }
            ]
        )

        backfill_category_group_names(conn, client)

        rows = conn.execute("SELECT budget_month, deleted FROM budget_categories WHERE id = ?", ("cat-1",)).fetchall()
        assert all(r["deleted"] == 0 for r in rows), "category should flip back to alive across all its month rows"
        conn.close()

    def test_partial_response_missing_too_many_categories_skips_reconciliation(self, tmp_path, capsys):
        """Safety guard: if a large fraction of previously-live categories are missing
        from a single /categories pull, treat it as a partial/incomplete API response
        rather than a wave of real deletions, and don't mark anything deleted."""
        conn = _setup_test_db(tmp_path)
        # 10 currently-live categories locally.
        for i in range(10):
            _insert_budget_category(conn, f"cat-{i}", "2026-07-01", name=f"Category {i}")

        # /categories only returns one of them - as if a whole group vanished from the
        # response due to a transient glitch, not real deletions.
        client = _make_categories_client(
            [
                {
                    "id": "grp-1",
                    "name": "Group",
                    "categories": [{"id": "cat-0", "hidden": False, "deleted": False}],
                }
            ]
        )

        _, deleted_count = backfill_category_group_names(conn, client)
        assert deleted_count == 0, "reconciliation should be skipped, not silently mass-delete 9 of 10 categories"

        rows = conn.execute("SELECT deleted FROM budget_categories").fetchall()
        assert all(r["deleted"] == 0 for r in rows)

        # Regression: the skip must be visible in captured stdout, not just logged to
        # stderr, since mcp_server.py's _capture (used by the sync_data MCP tool) only
        # redirects stdout. A logger-only warning is invisible to the actual caller.
        output = capsys.readouterr().out
        assert "WARNING" in output and "reconciliation skipped" in output

        conn.close()

    def test_reconciled_count_reaches_sync_plan_export_captured_output(self, tmp_path, capsys):
        """End-to-end regression: the reconciliation summary must surface through the
        exact call path sync_data's MCP tool uses (sync_plan_export, called with stdout
        captured), not just via a logger call that never reaches the caller."""
        conn = _setup_test_db(tmp_path)
        _insert_budget_category(conn, "cat-gone", "2026-07-01", name="Concerts")
        _insert_budget_category(conn, "cat-alive", "2026-07-01", name="Entertainment")

        plan = {
            "accounts": [],
            "payees": [],
            "months": [],
            "categories": [],
            "transactions": [],
            "subtransactions": [],
        }
        client = MagicMock()
        client.get_plan_detail.return_value = {"plan": plan, "server_knowledge": 2}
        # /categories only returns cat-alive - cat-gone was merged away without a
        # tombstone, exactly like the real Concerts/Sports merge in issue #134.
        client.get_categories.return_value = {
            "category_groups": [
                {
                    "id": "grp-1",
                    "name": "Group",
                    "categories": [{"id": "cat-alive", "hidden": False, "deleted": False}],
                }
            ]
        }

        sync_plan_export(conn, client)

        output = capsys.readouterr().out
        assert "1 categories reconciled as deleted" in output

        conn.close()

    def test_small_number_of_real_deletions_under_ceiling_still_applies(self, tmp_path):
        """A handful of genuine deletions/merges relative to the known-live set must
        still be applied -- the sanity ceiling should not block normal operation."""
        conn = _setup_test_db(tmp_path)
        for i in range(10):
            _insert_budget_category(conn, f"cat-{i}", "2026-07-01", name=f"Category {i}")

        # 9 of 10 categories still present; 1 (cat-9) was merged away - well under the
        # ceiling (max(5, 10 * 0.2) == 5).
        client = _make_categories_client(
            [
                {
                    "id": "grp-1",
                    "name": "Group",
                    "categories": [{"id": f"cat-{i}", "hidden": False, "deleted": False} for i in range(9)],
                }
            ]
        )

        _, deleted_count = backfill_category_group_names(conn, client)
        assert deleted_count == 1

        row = conn.execute("SELECT deleted FROM budget_categories WHERE id = ?", ("cat-9",)).fetchone()
        assert row["deleted"] == 1
        conn.close()
