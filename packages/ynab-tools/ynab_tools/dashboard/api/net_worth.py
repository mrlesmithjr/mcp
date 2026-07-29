"""GET /api/net-worth - current net worth, history, and asset/liability breakdown.

Queries the net_worth_snapshots table (populated on each sync) and the accounts
table for the current breakdown by account type.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException

from ynab_tools.db import get_connection

router = APIRouter(tags=["net-worth"])

_HISTORY_MONTHS = 24
_MAX_RAW_ROWS = 750  # daily snapshots over 24 months


def _get_net_worth_data(conn: sqlite3.Connection) -> dict[str, Any]:
    # Pull snapshot history (newest first)
    rows = conn.execute(
        """
        SELECT snapshot_date, total_assets, total_debt, net_worth,
               on_budget_assets, on_budget_debt, off_budget_assets, off_budget_debt
        FROM net_worth_snapshots
        ORDER BY snapshot_date DESC
        LIMIT ?
        """,
        (_MAX_RAW_ROWS,),
    ).fetchall()

    if not rows:
        return {
            "has_data": False,
            "as_of": date.today().isoformat(),
            "current": None,
            "history": [],
            "mom_change": None,
            "mom_from_month": None,
            "yoy_change": None,
            "yoy_from_month": None,
            "breakdown": None,
        }

    # De-duplicate: keep the most-recent snapshot per month (rows are DESC)
    seen_months: set[str] = set()
    monthly: list[dict[str, Any]] = []
    for row in rows:
        month_label = row["snapshot_date"][:7]  # "YYYY-MM"
        if month_label not in seen_months:
            seen_months.add(month_label)
            monthly.append(
                {
                    "month": month_label,
                    "net_worth": round(float(row["net_worth"] or 0), 2),
                    "total_assets": round(float(row["total_assets"] or 0), 2),
                    "total_debt": round(float(row["total_debt"] or 0), 2),
                    "on_budget_assets": round(float(row["on_budget_assets"] or 0), 2),
                    "on_budget_debt": round(float(row["on_budget_debt"] or 0), 2),
                    "off_budget_assets": round(float(row["off_budget_assets"] or 0), 2),
                    "off_budget_debt": round(float(row["off_budget_debt"] or 0), 2),
                }
            )

    monthly = monthly[:_HISTORY_MONTHS]

    current = monthly[0]  # newest

    # Month-over-month delta
    mom_change: float | None = None
    mom_from_month: str | None = None
    if len(monthly) >= 2:
        mom_change = round(current["net_worth"] - monthly[1]["net_worth"], 2)
        mom_from_month = monthly[1]["month"]

    # Year-over-year delta (12 months back)
    yoy_change: float | None = None
    yoy_from_month: str | None = None
    if len(monthly) >= 13:
        yoy_change = round(current["net_worth"] - monthly[12]["net_worth"], 2)
        yoy_from_month = monthly[12]["month"]

    # Current account breakdown from the accounts table
    acct_rows = conn.execute(
        """
        SELECT name, type, on_budget, balance
        FROM accounts
        WHERE closed = 0 AND deleted = 0
        ORDER BY on_budget DESC, balance DESC
        """,
    ).fetchall()

    # Classify accounts into display groups
    investments = 0.0
    liquid = 0.0
    mortgage = 0.0
    other_debt = 0.0
    account_details: list[dict[str, Any]] = []

    INVESTMENT_TYPES = {"otherAsset", "investmentAccount"}
    MORTGAGE_TYPES = {"mortgage"}
    LIABILITY_TYPES = {
        "autoLoan",
        "studentLoan",
        "personalLoan",
        "medicalDebt",
        "otherDebt",
        "creditCard",
        "lineOfCredit",
    }

    for acct in acct_rows:
        bal = float(acct["balance"] or 0)
        acct_type = acct["type"] or ""
        on_budget = bool(acct["on_budget"])
        name = acct["name"]

        account_details.append(
            {
                "name": name,
                "type": acct_type,
                "on_budget": on_budget,
                "balance": round(bal, 2),
            }
        )

        if acct_type in INVESTMENT_TYPES or (not on_budget and bal > 0):
            investments += bal
        elif acct_type in MORTGAGE_TYPES:
            mortgage += bal
        elif acct_type in LIABILITY_TYPES:
            other_debt += bal
        elif bal >= 0:
            liquid += bal
        else:
            other_debt += bal

    # Mortgage is the largest liability - show ex-mortgage NW separately
    net_worth_ex_mortgage = round(current["net_worth"] - mortgage, 2)

    breakdown = {
        "investments": round(investments, 2),
        "liquid": round(liquid, 2),
        "mortgage": round(mortgage, 2),
        "other_debt": round(other_debt, 2),
        "net_worth_ex_mortgage": net_worth_ex_mortgage,
        "accounts": account_details,
    }

    # Chart history oldest-first
    # NOTE: ex-mortgage in the snapshot history uses off_budget_debt as a proxy for
    # the mortgage balance (mortgage accounts are off-budget liabilities). This is an
    # approximation; the current breakdown uses the actual account type classification.
    def _ex_mortgage_approx(snap: dict[str, Any]) -> float:
        ob_debt = snap.get("off_budget_debt", 0) or 0
        return round(snap["net_worth"] - ob_debt, 2)

    chart_history = [
        {
            "month": s["month"],
            "net_worth": s["net_worth"],
            "net_worth_ex_mortgage": _ex_mortgage_approx(s),
        }
        for s in reversed(monthly)
    ]

    return {
        "has_data": True,
        "as_of": date.today().isoformat(),
        "current": {
            "net_worth": current["net_worth"],
            "net_worth_ex_mortgage": net_worth_ex_mortgage,
            "total_assets": current["total_assets"],
            "total_debt": current["total_debt"],
        },
        "history": chart_history,
        "mom_change": mom_change,
        "mom_from_month": mom_from_month,
        "yoy_change": yoy_change,
        "yoy_from_month": yoy_from_month,
        "breakdown": breakdown,
    }


@router.get("/net-worth")
def net_worth() -> dict[str, Any]:
    """Current net worth, 24-month history, and asset/liability breakdown."""
    conn = get_connection()
    try:
        return _get_net_worth_data(conn)
    except sqlite3.OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database not ready. Run 'ynab sync' first. ({exc})",
        )
    finally:
        conn.close()
