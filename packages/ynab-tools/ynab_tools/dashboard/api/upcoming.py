"""GET /api/upcoming - planned expenses, sinking fund goal deadlines, and recurring bills for a given month."""

from __future__ import annotations

import sqlite3
import statistics
from collections import defaultdict
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ynab_tools.dashboard.api._common import month_starts
from ynab_tools.db import get_connection

router = APIRouter(tags=["upcoming"])

_RECUR_MIN_MONTHS = 3  # payee must appear in at least this many of the 4 prior months
_RECUR_LOOKBACK = 4  # number of complete months to look back
_RECUR_MAX_CV = 0.20  # max coefficient of variation (stdev/median) for amount consistency
_RECUR_MAX_DAY_STD = 5  # max stdev of transaction day for date consistency
_RECUR_MIN_AMOUNT = 5.0  # minimum median amount in dollars


def _infer_recurring_bills(conn: sqlite3.Connection, target_month: str) -> list[dict[str, Any]]:
    """Infer recurring bills from transaction history.

    Looks at the last 4 complete months before target_month. Payees that appear
    in at least 3 of those 4 months with consistent outflow are returned as
    recurring bill candidates.

    Returns a list of dicts with keys:
        payee_name, expected_amount (positive dollars), expected_day, source
    """
    # month_starts(5, complete_only=True): index 0 = last complete month, 1-4 = prior 4
    # We want months that are complete and before target_month.
    # Use month_starts with complete_only to get prior complete months.
    all_complete = month_starts(_RECUR_LOOKBACK + 1, complete_only=True)
    # Filter to only months strictly before target_month
    target_prefix = target_month[:7]  # YYYY-MM
    prior_months = [m for m in all_complete if m[:7] < target_prefix][:_RECUR_LOOKBACK]

    if len(prior_months) < _RECUR_MIN_MONTHS:
        return []

    placeholders = ",".join("?" * len(prior_months))

    rows = conn.execute(
        f"""
        SELECT
            t.payee_id,
            COALESCE(t.payee_name, p.name) AS payee_name,
            t.amount,
            strftime('%Y-%m', t.date) AS tx_month,
            CAST(strftime('%d', t.date) AS INTEGER) AS tx_day,
            t.transfer_account_id
        FROM transactions t
        JOIN accounts a ON t.account_id = a.id
        LEFT JOIN payees p ON t.payee_id = p.id
        WHERE a.on_budget = 1
          AND t.deleted = 0
          AND t.amount < 0
          AND strftime('%Y-%m-01', t.date) IN ({placeholders})
          AND t.transfer_account_id IS NULL
          AND (p.transfer_account_id IS NULL OR t.payee_id IS NULL)
        """,
        prior_months,
    ).fetchall()

    # Group by payee_id (fall back to payee_name when id is null).
    # Track per-month occurrence counts to filter out multi-visit payees (grocery stores, etc.).
    by_payee: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"name": "", "months": set(), "month_counts": defaultdict(int), "amounts": [], "days": []}
    )

    for row in rows:
        payee_name = row["payee_name"] or ""
        if not payee_name:
            continue
        if "transfer" in payee_name.lower():
            continue

        key = row["payee_id"] or payee_name
        entry = by_payee[key]
        entry["name"] = payee_name
        entry["months"].add(row["tx_month"])
        entry["month_counts"][row["tx_month"]] += 1
        entry["amounts"].append(abs(float(row["amount"])))
        entry["days"].append(row["tx_day"])

    results: list[dict[str, Any]] = []
    for _key, entry in by_payee.items():
        if len(entry["months"]) < _RECUR_MIN_MONTHS:
            continue

        # A true recurring bill appears exactly once per month.
        # Payees with multiple transactions in any month are discretionary spending.
        if any(count > 1 for count in entry["month_counts"].values()):
            continue

        median_amount = statistics.median(entry["amounts"])
        median_day = round(statistics.median(entry["days"]))

        if median_amount < _RECUR_MIN_AMOUNT:
            continue

        # Amount consistency: coefficient of variation must be low.
        if len(entry["amounts"]) >= 2:
            cv = statistics.stdev(entry["amounts"]) / median_amount if median_amount > 0 else 1.0
            if cv >= _RECUR_MAX_CV:
                continue

        # Date consistency: transaction day must cluster tightly.
        if len(entry["days"]) >= 2 and statistics.stdev(entry["days"]) >= _RECUR_MAX_DAY_STD:
            continue

        results.append(
            {
                "payee_name": entry["name"],
                "expected_amount": round(median_amount, 2),
                "expected_day": median_day,
                "source": "recurring",
            }
        )

    results.sort(key=lambda x: x["expected_day"])
    return results


@router.get("/upcoming")
def upcoming(
    year: int = Query(default=0, description="Year (defaults to current year)"),
    month: int = Query(default=0, ge=0, le=12, description="Month 1-12 (defaults to current month)"),
) -> dict[str, Any]:
    """Planned expenses and TBD sinking fund goals due in the requested month."""
    today = date.today()
    if year == 0:
        year = today.year
    if month == 0:
        month = today.month

    month_str = f"{year}-{month:02d}"

    conn = get_connection()
    try:
        items: list[dict[str, Any]] = []
        target_month = month_str + "-01"

        # --- Planned expenses ---
        planned_rows = conn.execute(
            """
            SELECT id, category_name, category_id, amount, due_date, memo
            FROM planned_expenses
            WHERE status = 'active'
              AND strftime('%Y-%m', due_date) = ?
            ORDER BY due_date
            """,
            (month_str,),
        ).fetchall()

        # Build category balance lookup (latest month with any budgeted data)
        latest_row = conn.execute("SELECT MAX(budget_month) AS m FROM budget_categories WHERE budgeted > 0").fetchone()
        latest_month = latest_row["m"] if latest_row and latest_row["m"] else None

        balance_map: dict[str, float] = {}
        group_map: dict[str, str] = {}
        if latest_month:
            bal_rows = conn.execute(
                "SELECT name, balance, category_group_name"
                " FROM budget_categories WHERE budget_month = ? AND hidden = 0 AND deleted = 0",
                (latest_month,),
            ).fetchall()
            for r in bal_rows:
                balance_map[r["name"]] = float(r["balance"] or 0)
                group_map[r["name"]] = r["category_group_name"] or ""

        for r in planned_rows:
            cat_balance = balance_map.get(r["category_name"], 0.0)
            amount = float(r["amount"])
            gap = max(0.0, amount - cat_balance)
            items.append(
                {
                    "id": r["id"],
                    "date": r["due_date"],
                    "label": r["category_name"],
                    "amount": round(amount, 2),
                    "type": "planned",
                    "group": group_map.get(r["category_name"]),
                    "funded": gap == 0.0,
                    "gap": round(gap, 2),
                    "memo": r["memo"],
                }
            )

        # --- Sinking fund TBD goals ---
        if latest_month:
            goal_rows = conn.execute(
                """
                SELECT name, category_group_name AS grp,
                       goal_target, goal_target_month,
                       balance, goal_under_funded
                FROM budget_categories
                WHERE goal_type = 'TBD'
                  AND goal_target_month LIKE ?
                  AND hidden = 0
                  AND deleted = 0
                  AND budget_month = ?
                """,
                (f"{month_str}-%", latest_month),
            ).fetchall()

            for r in goal_rows:
                gap = float(r["goal_under_funded"] or 0)
                items.append(
                    {
                        "date": r["goal_target_month"],
                        "label": r["name"],
                        "amount": round(float(r["goal_target"] or 0), 2),
                        "type": "goal",
                        "group": r["grp"],
                        "funded": gap == 0.0,
                        "gap": round(gap, 2),
                        "memo": None,
                    }
                )

        items.sort(key=lambda x: x["date"])

        # Infer recurring bills, then de-duplicate against planned/goal items
        # by payee name (case-insensitive match against item labels).
        recurring_raw = _infer_recurring_bills(conn, target_month)
        planned_labels_lower = {i["label"].lower() for i in items}
        recurring_bills = [r for r in recurring_raw if r["payee_name"].lower() not in planned_labels_lower]

        total_amount = round(sum(i["amount"] for i in items), 2)
        total_gap = round(sum(i["gap"] for i in items), 2)
        unfunded_count = sum(1 for i in items if not i["funded"])

        return {
            "year": year,
            "month": month,
            "items": items,
            "recurring_bills": recurring_bills,
            "total_amount": total_amount,
            "total_gap": total_gap,
            "unfunded_count": unfunded_count,
        }

    except sqlite3.OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database not ready. Run 'ynab sync' first. ({exc})",
        )
    finally:
        conn.close()
