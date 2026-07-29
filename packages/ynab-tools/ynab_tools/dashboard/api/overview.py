"""GET /api/overview - current month budget snapshot with spending pace."""

from __future__ import annotations

import re
import sqlite3
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ynab_tools.db import get_connection

from ._common import days_elapsed_in_month, should_skip_group

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])-01$")

router = APIRouter(tags=["overview"])


def _build_priorities(categories: list[dict], running_hot: list[dict]) -> dict[str, Any]:
    overspent = [c for c in categories if c["status"] == "OVERSPENT"]
    hot = [c for c in running_hot if c["status"] == "RUNNING_HOT"]
    return {
        "overspent_count": len(overspent),
        "running_hot_count": len(hot),
        "all_clear": len(overspent) == 0 and len(hot) == 0,
    }


def _current_month() -> str:
    t = date.today()
    return f"{t.year:04d}-{t.month:02d}-01"


def _get_overview(conn: sqlite3.Connection, month: str) -> dict[str, Any]:
    month_date = date.fromisoformat(month)
    days_elapsed, days_in_month = days_elapsed_in_month(month_date.year, month_date.month)
    pct_elapsed = round(days_elapsed / days_in_month * 100, 1)

    bm = conn.execute(
        "SELECT income, to_be_budgeted FROM budget_months WHERE month = ?",
        (month,),
    ).fetchone()

    income = float(bm["income"] or 0) if bm else 0.0
    rta = float(bm["to_be_budgeted"] or 0) if bm else 0.0

    rows = conn.execute(
        """
        SELECT name, category_group_name, budgeted, activity, balance
        FROM budget_categories
        WHERE budget_month = ? AND hidden = 0 AND deleted = 0
        ORDER BY category_group_name, name
        """,
        (month,),
    ).fetchall()

    # Sum spending from non-infrastructure categories only. budget_months.activity includes
    # Credit Card Payment activity, which double-counts charges already recorded in spending
    # categories when the CC bill is paid.
    # Per-row sign check: positive activity (refunds/inflows) must not reduce the total.
    # refs #154
    spending = 0.0
    for r in rows:
        if should_skip_group(r["category_group_name"] or ""):
            continue
        v = float(r["activity"] or 0)
        spending += abs(v) if v < 0 else 0.0

    categories: list[dict] = []
    running_hot: list[dict] = []

    for row in rows:
        group = row["category_group_name"] or ""
        if should_skip_group(group):
            continue

        budgeted = float(row["budgeted"] or 0)
        activity_val = float(row["activity"] or 0)
        spent = abs(activity_val) if activity_val < 0 else 0.0
        balance = float(row["balance"] or 0)

        pct_used = round(spent / budgeted * 100, 1) if budgeted > 0 else 0.0
        pace_ratio = round(pct_used / pct_elapsed, 2) if pct_elapsed > 0 and budgeted > 0 else 0.0

        if budgeted > 0 and spent > budgeted:
            status = "OVERSPENT"
        elif pace_ratio > 1.15 and budgeted > 0 and spent < budgeted and pct_elapsed >= 10:
            status = "RUNNING_HOT"
        elif budgeted == 0 and spent > 0:
            status = "OVERSPENT"
        elif budgeted > 0 and pct_elapsed > 70 and pct_used < 40:
            # flag likely-unused budget in the final third of the month
            status = "UNDERSPENT"
        else:
            status = "ON_TRACK"

        categories.append(
            {
                "group": group,
                "name": row["name"],
                "budgeted": budgeted,
                "spent": spent,
                "balance": balance,
                "pct_used": pct_used,
                "pace_ratio": pace_ratio,
                "status": status,
            }
        )

        if status in ("RUNNING_HOT", "OVERSPENT") and (budgeted > 0 or spent > 0):
            projected = round(spent / (pct_elapsed / 100), 2) if pct_elapsed > 0 else spent
            running_hot.append(
                {
                    "name": row["name"],
                    "group": group,
                    "budgeted": budgeted,
                    "spent": spent,
                    "pct_used": pct_used,
                    "pace_ratio": pace_ratio,
                    "projected": projected,
                    "status": status,
                }
            )

    priorities = _build_priorities(categories, running_hot)

    return {
        "month": month,
        "rta": rta,
        "income": income,
        "spending": spending,
        "net": round(income - spending, 2),
        "days_elapsed": days_elapsed,
        "days_in_month": days_in_month,
        "pct_elapsed": pct_elapsed,
        "running_hot": sorted(running_hot, key=lambda x: x["pace_ratio"], reverse=True),
        "categories": categories,
        "priorities": priorities,
    }


@router.get("/overview")
def overview(
    month: str | None = Query(default=None, description="YYYY-MM-01 format, defaults to current month"),
) -> dict[str, Any]:
    """Current month budget snapshot: RTA, income, spending, pace by category."""
    if month is not None and not _MONTH_RE.match(month):
        raise HTTPException(status_code=422, detail="month must be YYYY-MM-01 format")
    conn = get_connection()
    try:
        return _get_overview(conn, month or _current_month())
    except sqlite3.OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database not ready. Run 'ynab sync' first. ({exc})",
        )
    finally:
        conn.close()
