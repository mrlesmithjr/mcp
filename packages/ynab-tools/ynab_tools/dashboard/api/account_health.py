"""GET /api/overview/account-health - reconciliation staleness and CC payment health."""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException

from ynab_tools.db import get_connection

router = APIRouter(tags=["overview"])

_STALE_DAYS = 30


def _days_since(date_str: str | None) -> int | None:
    if not date_str:
        return None
    try:
        reconciled = date.fromisoformat(date_str[:10])
        return (date.today() - reconciled).days
    except ValueError:
        return None


def _get_account_health(conn: sqlite3.Connection) -> dict[str, Any]:
    today_str = date.today().isoformat()
    current_month = f"{date.today().year:04d}-{date.today().month:02d}-01"

    # Stale accounts: on-budget, open, not reconciled in 30+ days (or never reconciled)
    account_rows = conn.execute(
        """
        SELECT name, type, last_reconciled_at
        FROM accounts
        WHERE deleted = 0 AND closed = 0
          AND on_budget = 1
          AND type NOT IN ('creditCard')
        ORDER BY name
        """,
    ).fetchall()

    stale_accounts = []
    for row in account_rows:
        days = _days_since(row["last_reconciled_at"])
        # Never reconciled or reconciled 30+ days ago
        if days is None or days >= _STALE_DAYS:
            stale_accounts.append(
                {
                    "name": row["name"],
                    "last_reconciled_date": row["last_reconciled_at"],
                    "days_since": days,
                }
            )

    # CC underfunded: payment category balance < account balance (owed)
    cc_rows = conn.execute(
        """
        SELECT a.name, a.balance AS account_balance,
               bc.balance AS payment_category_balance
        FROM accounts a
        LEFT JOIN budget_categories bc
          ON bc.name = a.name AND bc.budget_month = ?
          AND bc.category_group_name = 'Credit Card Payments'
          AND bc.deleted = 0
        WHERE a.deleted = 0 AND a.closed = 0
          AND a.type = 'creditCard'
          AND a.on_budget = 1
        ORDER BY a.name
        """,
        (current_month,),
    ).fetchall()

    cc_underfunded = []
    for row in cc_rows:
        owed = abs(float(row["account_balance"] or 0))
        available = float(row["payment_category_balance"] or 0)
        gap = available - owed
        # Only flag if meaningfully underfunded (more than $0.01 short)
        if gap < -0.01 and owed > 0:
            cc_underfunded.append(
                {
                    "account_name": row["name"],
                    "balance_dollars": round(owed, 2),
                    "payment_category_balance_dollars": round(available, 2),
                    "gap_dollars": round(gap, 2),
                }
            )

    return {
        "as_of": today_str,
        "stale_accounts": stale_accounts,
        "stale_count": len(stale_accounts),
        "cc_underfunded": cc_underfunded,
        "cc_underfunded_count": len(cc_underfunded),
    }


@router.get("/overview/account-health")
def account_health() -> dict[str, Any]:
    """Reconciliation staleness and CC payment health for ambient awareness."""
    conn = get_connection()
    try:
        return _get_account_health(conn)
    except sqlite3.OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database not ready. Run 'ynab sync' first. ({exc})",
        )
    finally:
        conn.close()
