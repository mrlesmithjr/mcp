"""GET /api/sinking-funds - goal category status: funded, underfunded, negative."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException

from ynab_tools.db import get_connection
from ynab_tools.reports.paycheck_breakdown import (
    _get_breakdown_config,
    _is_bonus_funded,
    _is_excluded,
)

from ._common import should_skip_group

router = APIRouter(tags=["sinking-funds"])

_GOAL_TYPE_LABELS: dict[str, str] = {
    "TB": "Target Balance",
    "TBD": "Target by Date",
    "MF": "Monthly Funding",
    "NEED": "Needed for Spending",
    "DEBT": "Debt Payment",
}

# YNAB auto-manages DEBT goals for CC/loan tracking - not user sinking funds
_SKIP_GOAL_TYPES = {"DEBT"}


def _add_months(year: int, month: int, n: int) -> tuple[int, int]:
    """Add n months to (year, month), returning (new_year, new_month)."""
    total = month - 1 + n
    return year + total // 12, total % 12 + 1


def _next_due_date(now: datetime, goal_months_to_budget: int | None) -> str | None:
    """Compute the next due date for a recurring goal using goal_months_to_budget.

    goal_months_to_budget is the number of months from the current month (inclusive)
    to the target month (inclusive), so offset = goal_months_to_budget - 1.
    """
    if goal_months_to_budget is None or goal_months_to_budget <= 0:
        return None
    y, m = _add_months(now.year, now.month, goal_months_to_budget - 1)
    return f"{y:04d}-{m:02d}-01"


def _get_sinking_funds(conn: sqlite3.Connection) -> dict[str, Any]:
    now = datetime.now()
    month = now.strftime("%Y-%m-01")

    bonus_groups, bonus_cats, _pay, _checks, excluded_groups, excluded_cats = _get_breakdown_config()

    rows = conn.execute(
        """
        SELECT name, category_group_name, goal_type, goal_target, goal_target_month,
               goal_cadence, goal_cadence_frequency, goal_months_to_budget,
               balance, budgeted, goal_under_funded
        FROM budget_categories
        WHERE budget_month = ?
          AND goal_type IS NOT NULL AND goal_type != ''
          AND deleted = 0 AND hidden = 0
        ORDER BY category_group_name, name
        """,
        (month,),
    ).fetchall()

    consistently_set: set[str] = set()
    for row in conn.execute(
        """
        SELECT name
        FROM budget_categories
        -- hidden intentionally omitted: INSERT OR REPLACE propagates current hidden flag
        -- to historical rows; categories hidden after the fact must remain in past-month data.
        WHERE budget_month >= date(?, '-2 months')
          AND budget_month <= ?
          AND goal_under_funded > 0
          AND deleted = 0
        GROUP BY name
        HAVING COUNT(*) >= 3
        """,
        (month, month),
    ).fetchall():
        consistently_set.add(row["name"])

    categories: list[dict[str, Any]] = []
    funded_count = 0
    underfunded_count = 0
    negative_count = 0
    total_needed = 0.0
    total_balance = 0.0

    for row in rows:
        goal_type = row["goal_type"] or ""
        if goal_type in _SKIP_GOAL_TYPES:
            continue
        group = row["category_group_name"] or ""
        name = row["name"]
        if should_skip_group(group):
            continue
        if _is_excluded(group, name, excluded_groups, excluded_cats):
            continue
        if not _is_bonus_funded(name, group, bonus_groups, bonus_cats):
            continue

        balance = float(row["balance"] or 0)
        budgeted = float(row["budgeted"] or 0)
        under_funded = float(row["goal_under_funded"] or 0)
        goal_target = float(row["goal_target"]) if row["goal_target"] else None

        if under_funded > 0:
            status = "UNDERFUNDED"
            underfunded_count += 1
            total_needed += under_funded
        elif balance < 0:
            status = "NEGATIVE"
            negative_count += 1
        else:
            status = "FUNDED"
            funded_count += 1

        total_balance += balance

        cadence = row["goal_cadence"]
        is_recurring = cadence is not None and cadence != 0
        months_to_budget = row["goal_months_to_budget"]
        raw_target_month = row["goal_target_month"]

        if is_recurring:
            next_due = _next_due_date(now, months_to_budget)
            is_stale = False
        else:
            next_due = raw_target_month
            is_stale = raw_target_month is not None and raw_target_month < month

        categories.append(
            {
                "name": name,
                "group": group,
                "goal_type": goal_type,
                "goal_type_label": _GOAL_TYPE_LABELS.get(goal_type, goal_type),
                "goal_target": goal_target,
                "goal_target_month": raw_target_month,
                "goal_cadence": cadence,
                "is_recurring": is_recurring,
                "next_due_date": next_due,
                "is_stale": is_stale,
                "balance": round(balance, 2),
                "budgeted": round(budgeted, 2),
                "goal_under_funded": round(under_funded, 2),
                "status": status,
                "consistently_underfunded": row["name"] in consistently_set,
            }
        )

    return {
        "month": month,
        "categories": categories,
        "summary": {
            "funded_count": funded_count,
            "underfunded_count": underfunded_count,
            "negative_count": negative_count,
            "total_needed": round(total_needed, 2),
            "total_balance": round(total_balance, 2),
        },
    }


@router.get("/sinking-funds")
def sinking_funds() -> dict[str, Any]:
    """Goal category status: funded, underfunded, and negative balance goals."""
    conn = get_connection()
    try:
        return _get_sinking_funds(conn)
    except sqlite3.OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database not ready. Run 'ynab sync' first. ({exc})",
        )
    finally:
        conn.close()
