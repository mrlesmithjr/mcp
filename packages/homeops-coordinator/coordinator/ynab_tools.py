"""Read-only access to the ynab-tools local SQLite cache."""

import logging
import os
import sqlite3

logger = logging.getLogger(__name__)

_YNAB_DB = os.path.expanduser("~/.local/share/ynab-tools/ynab.db")


def get_category_balance(category_name, bill_date=None):
    """Get balance for a category in the current or specified month.

    Args:
        category_name: Exact or partial category name (LIKE match)
        bill_date: YYYY-MM string. Defaults to the most recent synced month.

    Returns dict with name, balance, budgeted, budget_month, or None if not found.
    """
    if not os.path.exists(_YNAB_DB):
        logger.warning("ynab.db not found at %s", _YNAB_DB)
        return None

    conn = sqlite3.connect(_YNAB_DB)
    conn.row_factory = sqlite3.Row

    if bill_date:
        budget_month = bill_date + "-01"
        row = conn.execute(
            "SELECT name, balance, budgeted, budget_month FROM budget_categories "
            "WHERE name LIKE ? AND budget_month = ? AND deleted = 0 LIMIT 1",
            (f"%{category_name}%", budget_month),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT name, balance, budgeted, budget_month FROM budget_categories "
            "WHERE name LIKE ? AND deleted = 0 "
            "ORDER BY budget_month DESC LIMIT 1",
            (f"%{category_name}%",),
        ).fetchone()

    conn.close()
    return dict(row) if row else None


def poll_planned_expenses(lookback_days=45, tolerance=0.05):
    """Find active planned expenses matched by a single cleared transaction.

    Matching criteria:
    - Same category_name
    - abs(transaction.amount) within ±tolerance of planned amount
    - transaction.date >= (due_date - lookback_days)
    - cleared = 'cleared' AND approved = 1

    Returns list of dicts: {plan_id, category_name, amount, payee_name, transaction_date}
    """
    if not os.path.exists(_YNAB_DB):
        logger.warning("ynab.db not found at %s", _YNAB_DB)
        return []

    conn = sqlite3.connect(_YNAB_DB)
    conn.row_factory = sqlite3.Row

    plans = conn.execute(
        "SELECT id, category_name, amount, due_date FROM planned_expenses WHERE status = 'active'",
    ).fetchall()

    matches = []
    for plan in plans:
        low = plan["amount"] * (1 - tolerance)
        high = plan["amount"] * (1 + tolerance)
        window_start = _date_minus_days(plan["due_date"], lookback_days)

        row = conn.execute(
            "SELECT payee_name, date, amount FROM transactions "
            "WHERE category_name = ? "
            "  AND abs(amount) BETWEEN ? AND ? "
            "  AND date >= ? "
            "  AND cleared IN ('cleared', 'reconciled') AND approved = 1 AND deleted = 0 "
            "ORDER BY abs(abs(amount) - ?) ASC LIMIT 1",
            (plan["category_name"], low, high, window_start, plan["amount"]),
        ).fetchone()

        if row:
            matches.append(
                {
                    "plan_id": plan["id"],
                    "category_name": plan["category_name"],
                    "amount": plan["amount"],
                    "payee_name": row["payee_name"],
                    "transaction_date": row["date"],
                }
            )

    conn.close()
    return matches


def complete_planned_expense(plan_id):
    """Mark a planned expense complete in ynab.db. Returns True if updated."""
    if not os.path.exists(_YNAB_DB):
        return False

    from datetime import datetime

    conn = sqlite3.connect(_YNAB_DB)
    result = conn.execute(
        "UPDATE planned_expenses SET status = 'completed', completed_at = ? WHERE id = ? AND status = 'active'",
        (datetime.now().isoformat(), plan_id),
    )
    conn.commit()
    conn.close()
    return result.rowcount > 0


def _date_minus_days(date_str, days):
    """Return YYYY-MM-DD string for date_str minus N days."""
    from datetime import date, timedelta

    d = date.fromisoformat(date_str)
    return (d - timedelta(days=days)).isoformat()
