"""GET /api/overview/debt-trend - total debt direction signal from current account balances."""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException

from ynab_tools.db import get_connection

router = APIRouter(tags=["overview"])

# Account types that represent debt (total liabilities view, including creditCard)
_DEBT_TYPES = (
    "autoLoan",
    "otherLiability",
    "mortgage",
    "personalLoan",
    "studentLoan",
    "medicalDebt",
    "otherDebt",
    "creditCard",
)


def _get_debt_trend(conn: sqlite3.Connection) -> dict[str, Any]:
    # Current debt from open accounts
    rows = conn.execute(
        """
        SELECT name, type, balance
        FROM accounts
        WHERE deleted = 0 AND closed = 0
          AND type IN ({})
        ORDER BY balance ASC
        """.format(",".join("?" * len(_DEBT_TYPES))),
        _DEBT_TYPES,
    ).fetchall()

    accounts = []
    total_current = 0.0
    for row in rows:
        balance = abs(float(row["balance"] or 0))
        if balance > 0:
            accounts.append(
                {
                    "name": row["name"],
                    "type": row["type"],
                    "balance_dollars": round(balance, 2),
                }
            )
            total_current += balance

    # Prior month delta: compare to the most recent snapshot from a prior calendar month.
    # Using the first day of the current month as the cutoff prevents daily snapshots
    # within the current month from being treated as "last month."
    # net_worth_snapshots stores total_debt as a negative number (sum of negative balances)
    today = date.today()
    first_of_month = today.replace(day=1).isoformat()
    prior_row = conn.execute(
        """
        SELECT snapshot_date, total_debt
        FROM net_worth_snapshots
        WHERE snapshot_date < ?
        ORDER BY snapshot_date DESC
        LIMIT 1
        """,
        (first_of_month,),
    ).fetchone()

    prior_month_dollars: float | None = None
    delta_dollars: float | None = None
    direction: str | None = None

    if prior_row is not None:
        # total_debt is negative; abs() gives us the debt amount
        prior_debt = abs(float(prior_row["total_debt"] or 0))
        prior_month_dollars = round(prior_debt, 2)
        delta_dollars = round(total_current - prior_debt, 2)

        if delta_dollars < -0.50:
            direction = "decreasing"
        elif delta_dollars > 0.50:
            direction = "increasing"
        else:
            direction = "flat"

    return {
        "as_of": date.today().isoformat(),
        "total_debt_dollars": round(total_current, 2),
        "prior_month_dollars": prior_month_dollars,
        "delta_dollars": delta_dollars,
        "direction": direction,
        "accounts": sorted(accounts, key=lambda a: a["balance_dollars"], reverse=True),
    }


@router.get("/overview/debt-trend")
def debt_trend() -> dict[str, Any]:
    """Total debt direction signal: current balance vs prior snapshot, top accounts."""
    conn = get_connection()
    try:
        return _get_debt_trend(conn)
    except sqlite3.OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database not ready. Run 'ynab sync' first. ({exc})",
        )
    finally:
        conn.close()
