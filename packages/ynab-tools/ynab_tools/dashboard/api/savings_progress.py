"""GET /api/overview/savings-progress - 2026 YTD savings progress vs targets."""

from __future__ import annotations

import calendar
import os
import sqlite3
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException

from ynab_tools.config import load_env
from ynab_tools.db import get_connection

router = APIRouter(tags=["overview"])

# Annual Roth IRA contribution limit: use the goal_target from the category
# when available; fall back to this value if the category has no goal set.
# Falls back to 2026 IRS under-50 IRA limit when YNAB category has no goal target set.
_ROTH_ANNUAL_FALLBACK = 7_500.0
_EMERGENCY_FUND_TARGET_DEFAULT = 15000.0

_SAVINGS_GENERAL_DEFAULT = "Savings: General"
_SAVINGS_LONG_TERM_DEFAULT = "Savings: Long-Term"
_EMERGENCY_FUND_DEFAULT = "Savings: Emergency Fund"
# Roth IRA: find any category whose name contains "Roth IRA" or "Backdoor Roth"
_ROTH_PATTERNS = ("Roth IRA", "Backdoor Roth", "roth ira", "backdoor roth")


def _find_roth_category(conn: sqlite3.Connection, budget_month: str) -> str | None:
    rows = conn.execute(
        """
        SELECT DISTINCT name FROM budget_categories
        WHERE budget_month = ? AND deleted = 0 AND hidden = 0
          AND (name LIKE '%Roth IRA%' OR name LIKE '%Backdoor Roth%')
        ORDER BY name
        LIMIT 1
        """,
        (budget_month,),
    ).fetchall()
    return rows[0]["name"] if rows else None


def _months_remaining_in_year(year: int) -> int:
    today = date.today()
    if today.year != year:
        return 0
    return 12 - today.month + 1


def _get_savings_progress(conn: sqlite3.Connection) -> dict[str, Any]:
    """Build YTD savings progress payload for Emergency Fund, Roth IRA, and general savings."""
    load_env()
    savings_general = (
        os.environ.get("YNAB_SAVINGS_GENERAL_CATEGORY", _SAVINGS_GENERAL_DEFAULT).strip() or _SAVINGS_GENERAL_DEFAULT
    )
    savings_long_term = (
        os.environ.get("YNAB_SAVINGS_LONG_TERM_CATEGORY", _SAVINGS_LONG_TERM_DEFAULT).strip()
        or _SAVINGS_LONG_TERM_DEFAULT
    )
    emergency_fund = (
        os.environ.get("YNAB_EMERGENCY_FUND_CATEGORY", _EMERGENCY_FUND_DEFAULT).strip() or _EMERGENCY_FUND_DEFAULT
    )

    today = date.today()
    year = today.year
    current_month = f"{year:04d}-{today.month:02d}-01"
    year_elapsed_pct = round((today.timetuple().tm_yday / (366 if calendar.isleap(year) else 365)) * 100, 1)
    months_remaining = _months_remaining_in_year(year)

    items: list[dict[str, Any]] = []

    # --- Emergency Fund: balance vs target ---
    ef_row = conn.execute(
        """
        SELECT balance, goal_target
        FROM budget_categories
        WHERE budget_month = ? AND name = ? AND deleted = 0
        """,
        (current_month, emergency_fund),
    ).fetchone()
    if ef_row is not None:
        balance = round(float(ef_row["balance"] or 0), 2)
        target = round(float(ef_row["goal_target"] or 0) or _EMERGENCY_FUND_TARGET_DEFAULT, 2)
        pct = round(min(balance / target * 100, 100.0), 1) if target > 0 else 0.0
        needs_funding = months_remaining > 0 and balance < target
        monthly_needed = round((target - balance) / months_remaining, 2) if needs_funding else 0.0
        items.append(
            {
                "name": emergency_fund,
                "type": "balance_goal",
                "current_balance": balance,
                "target": target,
                "pct_complete": pct,
                "monthly_needed": monthly_needed,
                "year_elapsed_pct": year_elapsed_pct,
            }
        )

    # --- Backdoor Roth IRA: budget balance vs annual goal ---
    roth_name = _find_roth_category(conn, current_month)
    if roth_name:
        roth_row = conn.execute(
            """
            SELECT balance, goal_target
            FROM budget_categories
            WHERE budget_month = ? AND name = ? AND deleted = 0 AND hidden = 0
            """,
            (current_month, roth_name),
        ).fetchone()
        if roth_row is not None:
            # The Roth IRA category accumulates budgeted amounts; balance = YTD funded
            balance = round(float(roth_row["balance"] or 0), 2)
            annual_limit = round(float(roth_row["goal_target"] or 0) or _ROTH_ANNUAL_FALLBACK, 2)
            pct = round(min(balance / annual_limit * 100, 100.0), 1) if annual_limit > 0 else 0.0
            items.append(
                {
                    "name": roth_name,
                    "type": "annual_contribution",
                    "ytd_contributed": balance,
                    "annual_limit": annual_limit,
                    "pct_complete": pct,
                    "months_remaining": months_remaining,
                    "year_elapsed_pct": year_elapsed_pct,
                }
            )

    # --- Savings: General and Savings: Long-Term ---
    # goal_type=NEED => monthly contribution target; show YTD budgeted vs YTD target.
    # goal_type=TB   => target balance; show current balance vs target (same as balance_goal).
    months_elapsed = today.month  # Jan=1 through current month
    for cat_name in (savings_general, savings_long_term):
        cur_row = conn.execute(
            """
            SELECT goal_target, goal_type, balance
            FROM budget_categories
            WHERE budget_month = ? AND name = ? AND deleted = 0
            """,
            (current_month, cat_name),
        ).fetchone()
        if cur_row is None:
            continue
        goal_type = cur_row["goal_type"] or ""
        goal_target_val = round(float(cur_row["goal_target"] or 0), 2)

        if goal_type in ("TB", "TBD"):
            # Target Balance: treat like Emergency Fund (balance vs target)
            bal = round(float(cur_row["balance"] or 0), 2)
            pct = round(min(bal / goal_target_val * 100, 100.0), 1) if goal_target_val > 0 else 0.0
            needs_funding = months_remaining > 0 and bal < goal_target_val
            mo_needed = round((goal_target_val - bal) / months_remaining, 2) if needs_funding else 0.0
            items.append(
                {
                    "name": cat_name,
                    "type": "balance_goal",
                    "current_balance": bal,
                    "target": goal_target_val,
                    "pct_complete": pct,
                    "monthly_needed": mo_needed,
                    "year_elapsed_pct": year_elapsed_pct,
                }
            )
        else:
            # NEED or no goal: monthly contribution target
            # YTD actual = sum of budgeted amounts across all months this year
            ytd_rows = conn.execute(
                """
                SELECT SUM(budgeted) AS ytd_budgeted
                FROM budget_categories
                WHERE name = ? AND deleted = 0
                  AND budget_month >= ? AND budget_month <= ?
                  -- hidden intentionally omitted: INSERT OR REPLACE propagates current hidden flag to historical rows
                """,
                (cat_name, f"{year}-01-01", current_month),
            ).fetchone()
            ytd_actual = round(float(ytd_rows["ytd_budgeted"] or 0), 2) if ytd_rows else 0.0
            ytd_target = round(goal_target_val * months_elapsed, 2)
            pct = round(min(ytd_actual / ytd_target * 100, 100.0), 1) if ytd_target > 0 else 0.0

            items.append(
                {
                    "name": cat_name,
                    "type": "monthly_target",
                    "ytd_actual": ytd_actual,
                    "ytd_target": ytd_target,
                    "monthly_goal": goal_target_val,
                    "pct_complete": pct,
                    "year_elapsed_pct": year_elapsed_pct,
                }
            )

    result: dict[str, Any] = {
        "year": year,
        "year_elapsed_pct": year_elapsed_pct,
        "months_elapsed": months_elapsed,
        "months_remaining": months_remaining,
        "items": items,
    }
    if not items:
        result["message"] = (
            "No configured savings categories found in the budget. "
            "Set YNAB_SAVINGS_GENERAL_CATEGORY, YNAB_SAVINGS_LONG_TERM_CATEGORY, "
            "or YNAB_EMERGENCY_FUND_CATEGORY to match your category names."
        )
    return result


@router.get("/overview/savings-progress")
def savings_progress() -> dict[str, Any]:
    """YTD savings progress for Emergency Fund, Roth IRA, and monthly savings targets."""
    conn = get_connection()
    try:
        return _get_savings_progress(conn)
    except sqlite3.OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database not ready. Run 'ynab sync' first. ({exc})",
        )
    finally:
        conn.close()
