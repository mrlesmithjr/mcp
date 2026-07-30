"""Sync YNAB budget data to local SQLite database."""

import json
import logging
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .client import YNABClient
from .config import DB_PATH
from .db import init_db

logger = logging.getLogger(__name__)


def milliunits_to_dollars(milliunits: int | None) -> float | None:
    """Convert YNAB milliunits to dollars."""
    if milliunits is None:
        return None
    return milliunits / 1000.0


def _normalize_goal_type(goal_type: str | None, goal_target_month: str | None) -> str | None:
    """Normalize YNAB goal_type for local storage.

    The YNAB API returns goal_type='TB' for regular savings goals even when a
    target date is set (goal_target_month is populated). Only CC 'pay off by
    date' goals come back as 'TBD' from the API. Normalize TB+date → TBD so
    downstream queries and displays treat dated savings goals correctly.
    """
    if goal_type == "TB" and goal_target_month:
        return "TBD"
    return goal_type


def get_server_knowledge(conn: sqlite3.Connection, endpoint: str) -> int | None:
    """Get stored server_knowledge for an endpoint, or None for first sync."""
    try:
        row = conn.execute(
            "SELECT server_knowledge FROM sync_state WHERE endpoint = ?",
            (endpoint,),
        ).fetchone()
        return row["server_knowledge"] if row else None
    except sqlite3.OperationalError:
        return None


def save_server_knowledge(conn: sqlite3.Connection, endpoint: str, knowledge: int, *, commit: bool = True) -> None:
    """Save server_knowledge for an endpoint after successful sync."""
    conn.execute(
        """INSERT OR REPLACE INTO sync_state (endpoint, server_knowledge, updated_at)
           VALUES (?, ?, ?)""",
        (endpoint, knowledge, datetime.now(UTC).isoformat()),
    )
    if commit:
        conn.commit()


def sync_accounts(conn: sqlite3.Connection, client: YNABClient) -> int:
    """Sync accounts from YNAB (delta if prior knowledge exists)."""
    knowledge = get_server_knowledge(conn, "accounts")
    data = client.get_accounts(server_knowledge=knowledge)
    accounts = data["accounts"]
    now = datetime.now(UTC).isoformat()

    if knowledge and not accounts:
        logger.info("Accounts: no changes since last sync")
        save_server_knowledge(conn, "accounts", data["server_knowledge"])
        return 0

    for a in accounts:
        # Serialize debt detail dicts as JSON (rates/payments are in milliunits)
        interest_rates = a.get("debt_interest_rates") or {}
        min_payments = a.get("debt_minimum_payments") or {}
        escrow_amounts = a.get("debt_escrow_amounts") or {}

        conn.execute(
            """INSERT OR REPLACE INTO accounts
               (id, name, type, on_budget, closed, deleted, balance,
                cleared_balance, uncleared_balance, note,
                last_reconciled_at, direct_import_linked,
                debt_interest_rates, debt_minimum_payments,
                debt_original_balance, debt_escrow_amounts,
                last_synced_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                a["id"],
                a["name"],
                a["type"],
                int(a.get("on_budget", False)),
                int(a.get("closed", False)),
                int(a.get("deleted", False)),
                milliunits_to_dollars(a.get("balance")),
                milliunits_to_dollars(a.get("cleared_balance")),
                milliunits_to_dollars(a.get("uncleared_balance")),
                a.get("note"),
                a.get("last_reconciled_at"),
                int(a.get("direct_import_linked", False)),
                json.dumps(interest_rates) if interest_rates else None,
                json.dumps(min_payments) if min_payments else None,
                milliunits_to_dollars(a.get("debt_original_balance")),
                json.dumps(escrow_amounts) if escrow_amounts else None,
                now,
            ),
        )
    conn.commit()
    save_server_knowledge(conn, "accounts", data["server_knowledge"])
    return len(accounts)


def sync_budget_months(conn: sqlite3.Connection, client: YNABClient, months_back: int) -> tuple[int, int]:
    """Sync budget month summaries and their category breakdowns."""
    all_months = client.get_months()
    now = datetime.now(UTC).isoformat()

    cutoff = (datetime.now() - timedelta(days=months_back * 31)).strftime("%Y-%m-01")
    recent_months = [m for m in all_months if m["month"] >= cutoff]

    categories_synced = 0
    for month_summary in recent_months:
        month_str = month_summary["month"]
        detail = client.get_month_detail(month_str)

        conn.execute(
            """INSERT OR REPLACE INTO budget_months
               (month, income, budgeted, activity, to_be_budgeted, age_of_money, last_synced_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                month_str,
                milliunits_to_dollars(detail.get("income")),
                milliunits_to_dollars(detail.get("budgeted")),
                milliunits_to_dollars(detail.get("activity")),
                milliunits_to_dollars(detail.get("to_be_budgeted")),
                detail.get("age_of_money"),
                now,
            ),
        )

        for cat in detail.get("categories", []):
            conn.execute(
                """INSERT OR REPLACE INTO budget_categories
                   (id, budget_month, category_group_id, category_group_name,
                    name, hidden, deleted, budgeted, activity, balance,
                    goal_type, goal_target, goal_target_month,
                    goal_cadence, goal_cadence_frequency, goal_months_to_budget,
                    goal_percentage_complete, goal_under_funded,
                    goal_overall_funded, goal_overall_left, last_synced_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cat["id"],
                    month_str,
                    cat.get("category_group_id"),
                    cat.get("category_group_name"),
                    cat["name"],
                    int(cat.get("hidden", False)),
                    int(cat.get("deleted", False)),
                    milliunits_to_dollars(cat.get("budgeted")),
                    milliunits_to_dollars(cat.get("activity")),
                    milliunits_to_dollars(cat.get("balance")),
                    _normalize_goal_type(cat.get("goal_type"), cat.get("goal_target_month")),
                    milliunits_to_dollars(cat.get("goal_target")),
                    cat.get("goal_target_month"),
                    cat.get("goal_cadence"),
                    cat.get("goal_cadence_frequency"),
                    cat.get("goal_months_to_budget"),
                    cat.get("goal_percentage_complete"),
                    milliunits_to_dollars(cat.get("goal_under_funded")),
                    milliunits_to_dollars(cat.get("goal_overall_funded")),
                    milliunits_to_dollars(cat.get("goal_overall_left")),
                    now,
                ),
            )
            categories_synced += 1

    conn.commit()
    return len(recent_months), categories_synced


def sync_transactions(conn: sqlite3.Connection, client: YNABClient, months_back: int) -> tuple[int, int]:
    """Sync transactions and subtransactions from YNAB (delta if prior knowledge exists)."""
    knowledge = get_server_knowledge(conn, "transactions")
    now = datetime.now(UTC).isoformat()

    if knowledge:
        data = client.get_transactions(server_knowledge=knowledge)
    else:
        since_date = (datetime.now() - timedelta(days=months_back * 31)).strftime("%Y-%m-%d")
        data = client.get_transactions(since_date=since_date)

    transactions = data["transactions"]

    if knowledge and not transactions:
        logger.info("Transactions: no changes since last sync")
        save_server_knowledge(conn, "transactions", data["server_knowledge"])
        return 0, 0

    subtxn_count = 0
    for t in transactions:
        conn.execute(
            """INSERT OR REPLACE INTO transactions
               (id, date, amount, memo, cleared, approved,
                flag_color, flag_name,
                account_id, account_name, payee_id, payee_name,
                category_id, category_name, transfer_account_id,
                debt_transaction_type, import_id,
                import_payee_name, import_payee_name_original,
                matched_transaction_id, deleted, last_synced_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                t["id"],
                t["date"],
                milliunits_to_dollars(t.get("amount")),
                t.get("memo"),
                t.get("cleared"),
                int(t.get("approved", False)),
                t.get("flag_color"),
                t.get("flag_name"),
                t.get("account_id"),
                t.get("account_name"),
                t.get("payee_id"),
                t.get("payee_name"),
                t.get("category_id"),
                t.get("category_name"),
                t.get("transfer_account_id"),
                t.get("debt_transaction_type"),
                t.get("import_id"),
                t.get("import_payee_name"),
                t.get("import_payee_name_original"),
                t.get("matched_transaction_id"),
                int(t.get("deleted", False)),
                now,
            ),
        )

        subs = t.get("subtransactions", [])
        conn.execute("DELETE FROM subtransactions WHERE transaction_id = ?", (t["id"],))
        for st in subs:
            conn.execute(
                """INSERT OR REPLACE INTO subtransactions
                   (id, transaction_id, amount, memo, payee_id, payee_name,
                    category_id, category_name, transfer_account_id, deleted,
                    last_synced_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    st["id"],
                    t["id"],
                    milliunits_to_dollars(st.get("amount")),
                    st.get("memo"),
                    st.get("payee_id"),
                    st.get("payee_name"),
                    st.get("category_id"),
                    st.get("category_name"),
                    st.get("transfer_account_id"),
                    int(st.get("deleted", False)),
                    now,
                ),
            )
            subtxn_count += 1

    conn.commit()
    save_server_knowledge(conn, "transactions", data["server_knowledge"])
    return len(transactions), subtxn_count


def sync_payees(conn: sqlite3.Connection, client: YNABClient) -> int:
    """Sync payees from YNAB (delta if prior knowledge exists)."""
    knowledge = get_server_knowledge(conn, "payees")
    data = client.get_payees(server_knowledge=knowledge)
    payees = data["payees"]
    now = datetime.now(UTC).isoformat()

    if knowledge and not payees:
        logger.info("Payees: no changes since last sync")
        save_server_knowledge(conn, "payees", data["server_knowledge"])
        return 0

    for p in payees:
        conn.execute(
            """INSERT OR REPLACE INTO payees
               (id, name, transfer_account_id, deleted, last_synced_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                p["id"],
                p["name"],
                p.get("transfer_account_id"),
                int(p.get("deleted", False)),
                now,
            ),
        )
    conn.commit()
    save_server_knowledge(conn, "payees", data["server_knowledge"])
    return len(payees)


def sync_money_movements(conn: sqlite3.Connection, client: YNABClient) -> int:
    """Sync money movements from YNAB (delta if prior knowledge exists)."""
    knowledge = get_server_knowledge(conn, "money_movements")
    data = client.get_money_movements(server_knowledge=knowledge)
    movements = data["money_movements"]
    now = datetime.now(UTC).isoformat()

    if knowledge and not movements:
        logger.info("Money movements: no changes since last sync")
        save_server_knowledge(conn, "money_movements", data["server_knowledge"])
        return 0

    for m in movements:
        # Insert new records only - preserve existing rows on conflict so previously
        # resolved category names are not wiped by delta sync re-sends.
        conn.execute(
            """INSERT OR IGNORE INTO money_movements
               (id, month, moved_at, note, money_movement_group_id,
                performed_by_user_id, from_category_id, from_category_name,
                to_category_id, to_category_name,
                amount_milliunits, amount, deleted, last_synced_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                m["id"],
                m["month"],
                m["moved_at"],
                m.get("note"),
                m.get("money_movement_group_id"),
                m.get("performed_by_user_id"),
                m.get("from_category_id"),
                None,
                m.get("to_category_id"),
                None,
                m.get("amount"),
                m.get("amount_currency"),
                int(m.get("deleted", False)),
                now,
            ),
        )
        # Update non-name fields for existing records (handles deleted flag changes etc.)
        conn.execute(
            """UPDATE money_movements
               SET moved_at = ?, note = ?, deleted = ?, last_synced_at = ?
               WHERE id = ?""",
            (m["moved_at"], m.get("note"), int(m.get("deleted", False)), now, m["id"]),
        )
    conn.commit()

    # Resolve category names from budget_categories using most recent budget month
    # as anchor - robust to old movements predating the sync window.
    conn.execute("""
        UPDATE money_movements
        SET from_category_name = (
            SELECT bc.name FROM budget_categories bc
            WHERE bc.id = money_movements.from_category_id
            ORDER BY bc.budget_month DESC
            LIMIT 1
        )
        WHERE from_category_name IS NULL AND from_category_id IS NOT NULL
    """)
    conn.execute("""
        UPDATE money_movements
        SET to_category_name = (
            SELECT bc.name FROM budget_categories bc
            WHERE bc.id = money_movements.to_category_id
            ORDER BY bc.budget_month DESC
            LIMIT 1
        )
        WHERE to_category_name IS NULL AND to_category_id IS NOT NULL
    """)
    conn.commit()

    save_server_knowledge(conn, "money_movements", data["server_knowledge"])
    return len(movements)


def _build_lookup(items: list[dict], key: str = "id") -> dict[str, dict]:
    """Build a lookup dict from a list of dicts by a key field."""
    return {item[key]: item for item in items}


def backfill_category_group_names(conn: sqlite3.Connection, client: YNABClient) -> tuple[int, int]:
    """Reconcile group id, group name, and category name from /categories (authoritative
    per category id), and reconcile deleted/merged categories that YNAB stops returning
    without a deleted=true tombstone (refs #134, #35).

    Group membership and category name are current attributes of the category, not
    per-month, so a moved or renamed category is corrected across all its month rows --
    matching what `--full` already does by rewriting every month from scratch.

    Returns (group_names_updated, categories_marked_deleted).
    """
    data = client.get_categories()
    groups = data["category_groups"]

    # Build the authoritative id -> (group_id, group_name, cat_name) mapping for every
    # non-deleted category YNAB currently knows about, plus id -> (hidden, deleted)
    # status for every category regardless of deleted state. Deleted categories are
    # excluded from live_category (their name/group is irrelevant once gone) but still
    # recorded in live_status so the hidden/deleted reconciliation pass below can apply
    # an explicit deleted=true tombstone for them.
    # cat_name is Optional[str]: the real /categories endpoint always includes it, but
    # it is kept optional here so a caller/response without a "name" field (e.g. an
    # older test fixture) degrades to reconciling group id/name only, rather than
    # overwriting the local name with a missing value.
    live_category: dict[str, tuple[str, str, str | None]] = {}
    live_status: dict[str, tuple[bool, bool]] = {}
    for group in groups:
        for cat in group.get("categories", []):
            deleted = bool(cat.get("deleted", False))
            live_status[cat["id"]] = (bool(cat.get("hidden", False)), deleted)
            if not deleted:
                live_category[cat["id"]] = (group["id"], group["name"], cat.get("name"))

    # Reconcile group id, group name, and (when known) name for every live category,
    # keyed on the category's own id so a move (new category_group_id) or rename (new
    # name) is corrected across all of that category's month rows, not just rows still
    # matching the old group id.
    total_updated = 0
    for cat_id, (group_id, group_name, cat_name) in live_category.items():
        if cat_name is not None:
            result = conn.execute(
                """UPDATE budget_categories
                   SET category_group_id = ?, category_group_name = ?, name = ?
                   WHERE id = ?
                     AND (category_group_id != ? OR category_group_name IS NULL
                          OR category_group_name != ? OR name != ?)""",
                (group_id, group_name, cat_name, cat_id, group_id, group_name, cat_name),
            )
        else:
            result = conn.execute(
                """UPDATE budget_categories
                   SET category_group_id = ?, category_group_name = ?
                   WHERE id = ?
                     AND (category_group_id != ? OR category_group_name IS NULL
                          OR category_group_name != ?)""",
                (group_id, group_name, cat_id, group_id, group_name),
            )
        total_updated += result.rowcount

    # Apply explicit hidden/deleted flags for categories YNAB still returns (covers
    # hide/unhide toggles and any category that does come back with deleted=true).
    for cat_id, (hidden, deleted) in live_status.items():
        conn.execute(
            """UPDATE budget_categories SET hidden = ?, deleted = ?
               WHERE id = ? AND (hidden != ? OR deleted != ?)""",
            (int(hidden), int(deleted), cat_id, int(hidden), int(deleted)),
        )

    # Categories missing entirely from the authoritative endpoint have been
    # removed/merged away without a tombstone -- mark them deleted, but only if the
    # response looks complete (non-empty, and not missing an implausibly large slice
    # of known-live categories, which would suggest a partial/incomplete API response
    # rather than real deletions).
    deleted_count = 0
    if live_status:
        local_ids = {
            row["id"] for row in conn.execute("SELECT DISTINCT id FROM budget_categories WHERE deleted = 0").fetchall()
        }
        missing_ids = local_ids - live_status.keys()
        max_plausible_deletions = max(5, int(len(local_ids) * 0.2))

        if len(missing_ids) > max_plausible_deletions:
            skip_msg = (
                f"WARNING: category reconciliation skipped -- {len(missing_ids)} of {len(local_ids)} "
                f"known-live categories are missing from /categories in this sync, which exceeds the "
                f"{max_plausible_deletions} sanity ceiling. Treating this as a partial/incomplete API "
                f"response rather than real deletions; nothing was marked deleted this run."
            )
            logger.warning(skip_msg)
            # Also print: logger output goes to stderr and never reaches sync_data's
            # MCP tool response (mcp_server.py's _capture only redirects stdout), so
            # this must be printed too or the skip is invisible to the actual caller.
            print(skip_msg)
        else:
            for cat_id in missing_ids:
                result = conn.execute(
                    "UPDATE budget_categories SET deleted = 1 WHERE id = ? AND deleted = 0",
                    (cat_id,),
                )
                deleted_count += result.rowcount

    conn.commit()
    return total_updated, deleted_count


def sync_plan_export(conn: sqlite3.Connection, client: YNABClient) -> dict:
    """Sync all data via single plan export call (delta-aware).

    Returns counts dict: {accounts, months, categories, transactions, subtransactions, payees}.
    """
    knowledge = get_server_knowledge(conn, "plan")
    result = client.get_plan_detail(server_knowledge=knowledge)
    plan = result["plan"]
    new_knowledge = result["server_knowledge"]
    now = datetime.now(UTC).isoformat()

    counts = {"accounts": 0, "months": 0, "categories": 0, "transactions": 0, "subtransactions": 0, "payees": 0}

    # ── Accounts ──
    accounts = plan.get("accounts", [])
    for a in accounts:
        interest_rates = a.get("debt_interest_rates") or {}
        min_payments = a.get("debt_minimum_payments") or {}
        escrow_amounts = a.get("debt_escrow_amounts") or {}
        conn.execute(
            """INSERT OR REPLACE INTO accounts
               (id, name, type, on_budget, closed, deleted, balance,
                cleared_balance, uncleared_balance, note,
                last_reconciled_at, direct_import_linked,
                debt_interest_rates, debt_minimum_payments,
                debt_original_balance, debt_escrow_amounts,
                last_synced_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                a["id"],
                a["name"],
                a["type"],
                int(a.get("on_budget", False)),
                int(a.get("closed", False)),
                int(a.get("deleted", False)),
                milliunits_to_dollars(a.get("balance")),
                milliunits_to_dollars(a.get("cleared_balance")),
                milliunits_to_dollars(a.get("uncleared_balance")),
                a.get("note"),
                a.get("last_reconciled_at"),
                int(a.get("direct_import_linked", False)),
                json.dumps(interest_rates) if interest_rates else None,
                json.dumps(min_payments) if min_payments else None,
                milliunits_to_dollars(a.get("debt_original_balance")),
                json.dumps(escrow_amounts) if escrow_amounts else None,
                now,
            ),
        )
    counts["accounts"] = len(accounts)

    # ── Payees ──
    payees = plan.get("payees", [])
    for p in payees:
        conn.execute(
            """INSERT OR REPLACE INTO payees
               (id, name, transfer_account_id, deleted, last_synced_at)
               VALUES (?, ?, ?, ?, ?)""",
            (p["id"], p["name"], p.get("transfer_account_id"), int(p.get("deleted", False)), now),
        )
    counts["payees"] = len(payees)

    # ── Budget months + categories ──
    months = plan.get("months", [])
    for detail in months:
        month_str = detail["month"]
        conn.execute(
            """INSERT OR REPLACE INTO budget_months
               (month, income, budgeted, activity, to_be_budgeted, age_of_money, last_synced_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                month_str,
                milliunits_to_dollars(detail.get("income")),
                milliunits_to_dollars(detail.get("budgeted")),
                milliunits_to_dollars(detail.get("activity")),
                milliunits_to_dollars(detail.get("to_be_budgeted")),
                detail.get("age_of_money"),
                now,
            ),
        )
        for cat in detail.get("categories", []):
            conn.execute(
                """INSERT OR REPLACE INTO budget_categories
                   (id, budget_month, category_group_id, category_group_name,
                    name, hidden, deleted, budgeted, activity, balance,
                    goal_type, goal_target, goal_target_month,
                    goal_cadence, goal_cadence_frequency, goal_months_to_budget,
                    goal_percentage_complete, goal_under_funded,
                    goal_overall_funded, goal_overall_left, last_synced_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cat["id"],
                    month_str,
                    cat.get("category_group_id"),
                    cat.get("category_group_name"),
                    cat["name"],
                    int(cat.get("hidden", False)),
                    int(cat.get("deleted", False)),
                    milliunits_to_dollars(cat.get("budgeted")),
                    milliunits_to_dollars(cat.get("activity")),
                    milliunits_to_dollars(cat.get("balance")),
                    _normalize_goal_type(cat.get("goal_type"), cat.get("goal_target_month")),
                    milliunits_to_dollars(cat.get("goal_target")),
                    cat.get("goal_target_month"),
                    cat.get("goal_cadence"),
                    cat.get("goal_cadence_frequency"),
                    cat.get("goal_months_to_budget"),
                    cat.get("goal_percentage_complete"),
                    milliunits_to_dollars(cat.get("goal_under_funded")),
                    milliunits_to_dollars(cat.get("goal_overall_funded")),
                    milliunits_to_dollars(cat.get("goal_overall_left")),
                    now,
                ),
            )
            counts["categories"] += 1
    counts["months"] = len(months)

    # ── Transactions (TransactionSummary - no account_name/payee_name/category_name) ──
    # Build lookup dicts to resolve names from IDs
    account_lookup = _build_lookup(accounts)
    payee_lookup = _build_lookup(payees)
    # Categories are a top-level array in the plan export
    cat_lookup = _build_lookup(plan.get("categories", []))

    transactions = plan.get("transactions", [])
    # Subtransactions are a separate top-level array; index by transaction_id
    raw_subtxns = plan.get("subtransactions", [])
    subtxn_by_txn: dict[str, list[dict]] = {}
    for st in raw_subtxns:
        subtxn_by_txn.setdefault(st["transaction_id"], []).append(st)

    for t in transactions:
        acct = account_lookup.get(t.get("account_id"), {})
        payee = payee_lookup.get(t.get("payee_id"), {})
        cat = cat_lookup.get(t.get("category_id"), {})

        conn.execute(
            """INSERT OR REPLACE INTO transactions
               (id, date, amount, memo, cleared, approved,
                flag_color, flag_name,
                account_id, account_name, payee_id, payee_name,
                category_id, category_name, transfer_account_id,
                debt_transaction_type, import_id,
                import_payee_name, import_payee_name_original,
                matched_transaction_id, deleted, last_synced_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                t["id"],
                t["date"],
                milliunits_to_dollars(t.get("amount")),
                t.get("memo"),
                t.get("cleared"),
                int(t.get("approved", False)),
                t.get("flag_color"),
                t.get("flag_name"),
                t.get("account_id"),
                acct.get("name"),
                t.get("payee_id"),
                payee.get("name"),
                t.get("category_id"),
                cat.get("name"),
                t.get("transfer_account_id"),
                t.get("debt_transaction_type"),
                t.get("import_id"),
                t.get("import_payee_name"),
                t.get("import_payee_name_original"),
                t.get("matched_transaction_id"),
                int(t.get("deleted", False)),
                now,
            ),
        )

        subtxns = subtxn_by_txn.get(t["id"], [])
        conn.execute("DELETE FROM subtransactions WHERE transaction_id = ?", (t["id"],))
        for st in subtxns:
            st_payee = payee_lookup.get(st.get("payee_id"), {})
            st_cat = cat_lookup.get(st.get("category_id"), {})
            conn.execute(
                """INSERT OR REPLACE INTO subtransactions
                   (id, transaction_id, amount, memo, payee_id, payee_name,
                    category_id, category_name, transfer_account_id, deleted,
                    last_synced_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    st["id"],
                    t["id"],
                    milliunits_to_dollars(st.get("amount")),
                    st.get("memo"),
                    st.get("payee_id"),
                    st_payee.get("name"),
                    st.get("category_id"),
                    st_cat.get("name"),
                    st.get("transfer_account_id"),
                    int(st.get("deleted", False)),
                    now,
                ),
            )
            counts["subtransactions"] += 1

    counts["transactions"] = len(transactions)
    conn.commit()

    # ── Backfill NULL names from reference tables ──
    # The plan export returns TransactionSummary objects without denormalized
    # names.  We resolve them from lookup dicts above, but those dicts can be
    # incomplete (the plan export doesn't always include every payee/account/
    # category referenced by transactions).  Patch any remaining NULLs from
    # the authoritative reference tables already in the DB.
    conn.execute(
        """UPDATE transactions SET payee_name = (
               SELECT p.name FROM payees p WHERE p.id = transactions.payee_id
           ) WHERE payee_name IS NULL AND payee_id IS NOT NULL"""
    )
    conn.execute(
        """UPDATE transactions SET account_name = (
               SELECT a.name FROM accounts a WHERE a.id = transactions.account_id
           ) WHERE account_name IS NULL AND account_id IS NOT NULL"""
    )
    conn.execute(
        """UPDATE transactions SET category_name = (
               SELECT bc.name FROM budget_categories bc
               WHERE bc.id = transactions.category_id
               ORDER BY bc.budget_month DESC LIMIT 1
           ) WHERE category_name IS NULL AND category_id IS NOT NULL"""
    )
    conn.execute(
        """UPDATE subtransactions SET category_name = (
               SELECT bc.name FROM budget_categories bc
               WHERE bc.id = subtransactions.category_id
               ORDER BY bc.budget_month DESC LIMIT 1
           ) WHERE category_name IS NULL AND category_id IS NOT NULL"""
    )
    conn.execute(
        """UPDATE subtransactions SET payee_name = (
               SELECT p.name FROM payees p WHERE p.id = subtransactions.payee_id
           ) WHERE payee_name IS NULL AND payee_id IS NOT NULL"""
    )

    # ── Backfill category_group_name + reconcile deleted/merged categories ──
    # The plan export only provides category_group_id on month categories - the
    # group name is only available from the dedicated /categories endpoint. That
    # same call is also the only reliable signal for categories removed in YNAB.
    group_name_count, reconciled_deleted_count = backfill_category_group_names(conn, client)
    logger.info(f"Category group names backfilled: {group_name_count} rows updated")
    if reconciled_deleted_count:
        msg = f"{reconciled_deleted_count} categories reconciled as deleted (removed from YNAB)"
        logger.info(msg)
        # Also print: logger output goes to stderr, which _capture in mcp_server.py
        # never redirects, so sync_data's MCP tool response would otherwise miss this.
        print(msg)

    save_server_knowledge(conn, "plan", new_knowledge, commit=False)
    conn.commit()
    return counts


def show_status(db_path: Path | None = None) -> None:
    """Show current database status."""
    from .db import get_connection

    path = db_path or DB_PATH
    conn = get_connection(path)

    tables = {
        "accounts": "SELECT COUNT(*) FROM accounts WHERE closed = 0 AND deleted = 0",
        "budget_months": "SELECT COUNT(*) FROM budget_months",
        "budget_categories": "SELECT COUNT(DISTINCT budget_month) FROM budget_categories",
        "transactions": "SELECT COUNT(*) FROM transactions WHERE deleted = 0",
        "subtransactions": "SELECT COUNT(*) FROM subtransactions WHERE deleted = 0",
        "payees": "SELECT COUNT(*) FROM payees WHERE deleted = 0",
        "money_movements": "SELECT COUNT(*) FROM money_movements WHERE deleted = 0",
    }

    print(f"\nDatabase: {path}")
    print(f"Size: {path.stat().st_size / 1024:.0f} KB\n")

    for table, query in tables.items():
        try:
            count = conn.execute(query).fetchone()[0]
            print(f"  {table}: {count}")
        except sqlite3.OperationalError:
            print(f"  {table}: (not synced yet)")

    try:
        row = conn.execute("SELECT synced_at, duration_seconds FROM sync_log ORDER BY id DESC LIMIT 1").fetchone()
        if row:
            print(f"\n  Last sync: {row[0]} ({row[1]:.1f}s)")
    except sqlite3.OperationalError:
        pass

    try:
        states = conn.execute(
            "SELECT endpoint, server_knowledge, updated_at FROM sync_state ORDER BY endpoint"
        ).fetchall()
        if states:
            print("\n  Delta sync state:")
            for s in states:
                print(f"    {s['endpoint']}: knowledge={s['server_knowledge']} (updated {s['updated_at']})")
    except sqlite3.OperationalError:
        pass
    print()
    conn.close()


def run_sync(months: int = 12, full: bool = False, db_path: Path | None = None) -> None:
    """Run a full or delta sync. Called by CLI."""
    from .config import require_credentials
    from .db import get_connection

    token, plan_id = require_credentials()
    path = db_path or DB_PATH

    conn = get_connection(path)
    init_db(conn)

    client = YNABClient(token, plan_id)
    start = time.time()

    if full:
        conn.execute("DELETE FROM sync_state")
        conn.commit()
        logger.info("Cleared delta sync state - forcing full sync")

    # Use plan export for all non-full syncs (1 API call vs 4+N).
    # --full uses multi-call to support --months filtering on initial load.
    if not full:
        has_plan_knowledge = get_server_knowledge(conn, "plan") is not None
        sync_mode = "delta" if has_plan_knowledge else "full"
        logger.info(f"Syncing YNAB data to {path} ({sync_mode}, plan export)")

        # Clean up orphaned sync_state rows from pre-plan-export era
        conn.execute("DELETE FROM sync_state WHERE endpoint NOT IN ('plan', 'money_movements')")
        conn.commit()

        counts = sync_plan_export(conn, client)
        account_count = counts["accounts"]
        cat_count = counts["categories"]
        txn_count = counts["transactions"]
        subtxn_count = counts["subtransactions"]
        month_count = counts["months"]
        payee_count = counts["payees"]

        mm_count = sync_money_movements(conn, client)
        logger.info(f"Money movements: {mm_count}")
    else:
        # Multi-call sync for --full (supports --months filtering)
        logger.info(f"Syncing YNAB data to {path} (full sync, multi-call)")
        logger.info(f"History: {months} months")

        account_count = sync_accounts(conn, client)
        month_count, cat_count = sync_budget_months(conn, client, months)
        txn_count, subtxn_count = sync_transactions(conn, client, months)
        payee_count = sync_payees(conn, client)
        group_name_count, reconciled_deleted_count = backfill_category_group_names(conn, client)
        logger.info(f"Category group names backfilled: {group_name_count} rows updated")
        if reconciled_deleted_count:
            msg = f"{reconciled_deleted_count} categories reconciled as deleted (removed from YNAB)"
            logger.info(msg)
            print(msg)
        mm_count = sync_money_movements(conn, client)
        logger.info(f"Money movements: {mm_count}")

    logger.info(f"Accounts: {account_count}")
    logger.info(f"Budget months: {month_count} ({cat_count} category entries)")
    logger.info(f"Transactions: {txn_count} ({subtxn_count} subtransactions)")
    logger.info(f"Payees: {payee_count}")

    duration = time.time() - start

    conn.execute(
        """INSERT INTO sync_log (synced_at, accounts_count, categories_count,
           transactions_count, months_count, duration_seconds)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (datetime.now(UTC).isoformat(), account_count, cat_count, txn_count, month_count, duration),
    )
    conn.commit()

    logger.info(f"Sync complete in {duration:.1f}s")

    from .reports.net_worth import take_snapshot

    try:
        snap = take_snapshot(conn)
        if snap:
            print(f"  Net worth snapshot ({snap['action'].lower()}): ${snap['net_worth']:,.0f}")
    except Exception as e:
        logger.warning(f"Net worth snapshot skipped: {e}")

    conn.close()
    show_status(path)
