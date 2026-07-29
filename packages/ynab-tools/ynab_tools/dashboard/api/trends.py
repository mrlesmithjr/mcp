"""GET /api/trends - income vs spending trend with rolling averages and streaks."""

from __future__ import annotations

import os
import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ynab_tools.config import load_env
from ynab_tools.db import get_connection

from ._common import month_starts, should_skip_group

router = APIRouter(tags=["trends"])


def _rolling_avg(spending_list: list[float], i: int) -> float:
    """3-month rolling average ending at index i, averaging only non-zero months."""
    window = spending_list[max(0, i - 2) : i + 1]
    non_zero = [v for v in window if v != 0.0]
    if not non_zero:
        return 0.0
    return round(sum(non_zero) / len(non_zero), 2)


def _build_insight(
    data: list[dict[str, Any]],
    streak_type: str,
    streak_count: int,
    avg_income: float,
    avg_spending: float,
    gross_monthly: float | None = None,
) -> str:
    if not data:
        return ""
    recent = data[-3:]  # last 3 months newest last
    rolling_avgs = [e["rolling_avg_spending"] for e in recent if e["rolling_avg_spending"] > 0]
    spending_rising = len(rolling_avgs) >= 2 and rolling_avgs[-1] > rolling_avgs[0] * 1.03

    if streak_type == "deficit" and streak_count >= 2:
        surplus_list = [abs(e["net"]) for e in data[-streak_count:] if e["net"] < 0]
        avg_overage = round(sum(surplus_list) / len(surplus_list), 0) if surplus_list else 0
        s = "month" if streak_count == 1 else "months"
        return f"Spending has exceeded income for {streak_count} straight {s} (avg overage ${avg_overage:,.0f}/mo)."
    if streak_type == "surplus" and streak_count >= 3:
        surplus_list = [e["net"] for e in data[-streak_count:] if e["net"] > 0]
        avg_surplus = round(sum(surplus_list) / len(surplus_list), 0) if surplus_list else 0
        total = round(sum(surplus_list), 0)
        return (
            f"Strong run: {streak_count} consecutive surplus months, avg ${avg_surplus:,.0f}/mo (${total:,.0f} total)."
        )
    if spending_rising and avg_income > 0:
        delta = round(rolling_avgs[-1] - rolling_avgs[0], 0)
        return f"Spending trend is rising: 3-month rolling average up ${delta:,.0f}/mo."
    if avg_income > 0 and avg_spending > 0:
        if gross_monthly:
            pct = round((avg_spending / gross_monthly) * 100, 1)
            income_label = "gross salary"
        else:
            pct = round((avg_spending / avg_income) * 100, 1)
            income_label = "budget income (pre-tax deductions excluded)"
        if pct > 95:
            return f"Tight margin: spending averages {pct}% of {income_label} over this period."
        if pct < 70:
            return f"Healthy margin: spending averages {pct}% of {income_label} over this period."
    return ""


@router.get("/trends")
def trends(
    months: int = Query(default=12, ge=3, le=36, description="Number of months to return"),
) -> dict[str, Any]:
    """Income vs spending trend with rolling averages, streaks, and summary stats."""
    conn = get_connection()
    try:
        month_list = month_starts(months)
        placeholders = ",".join("?" for _ in month_list)
        rows = conn.execute(
            f"""
            SELECT month, income
            FROM budget_months
            WHERE month IN ({placeholders})
            ORDER BY month ASC
            """,
            month_list,
        ).fetchall()

        # Compute spending from non-infrastructure categories to avoid double-counting CC payments.
        cat_rows = conn.execute(
            f"""
            SELECT budget_month, category_group_name, activity
            FROM budget_categories
            WHERE budget_month IN ({placeholders})
              AND deleted = 0
              -- hidden intentionally omitted: INSERT OR REPLACE propagates the current
              -- hidden flag to all historical rows, so categories hidden after the fact
              -- (e.g. a paid-off loan) would lose their entire activity history.
            """,
            month_list,
        ).fetchall()
        spend_by_month: dict[str, float] = {}
        for r in cat_rows:
            if should_skip_group(r["category_group_name"] or ""):
                continue
            m = r["budget_month"]
            v = float(r["activity"] or 0)
            spend_by_month[m] = spend_by_month.get(m, 0.0) + (v if v < 0 else 0.0)

        month_map = {row["month"]: row for row in rows}
        sorted_months = sorted(month_list)

        # Build ordered data aligned to sorted month list
        incomes: list[float] = []
        spendings: list[float] = []
        for m in sorted_months:
            row = month_map.get(m)
            incomes.append(float(row["income"] or 0) if row else 0.0)
            spendings.append(-spend_by_month.get(m, 0.0))

        # Build per-month output with rolling avg
        data: list[dict[str, Any]] = []
        for i, m in enumerate(sorted_months):
            inc = incomes[i]
            spend = spendings[i]
            net = round(inc - spend, 2)
            data.append(
                {
                    "month": m,
                    "income": inc,
                    "spending": spend,
                    "net": net,
                    "surplus": inc >= spend,
                    "rolling_avg_spending": _rolling_avg(spendings, i),
                }
            )

        # Streak: count consecutive months (newest first) with same surplus/deficit state
        streak_count = 0
        streak_type = "surplus"
        if data:
            # Walk from newest (last in sorted list) backwards
            current_surplus = data[-1]["surplus"]
            streak_type = "surplus" if current_surplus else "deficit"
            for entry in reversed(data):
                if entry["surplus"] == current_surplus:
                    streak_count += 1
                else:
                    break

        # Best and worst month by net
        nets = [(entry["net"], entry["month"]) for entry in data]
        if all(n == 0.0 for n, _ in nets):
            best_month = {"month": sorted_months[-1], "net": 0.0}
            worst_month = {"month": sorted_months[-1], "net": 0.0}
        else:
            best_net, best_m = max(nets)
            worst_net, worst_m = min(nets)
            best_month = {"month": best_m, "net": best_net}
            worst_month = {"month": worst_m, "net": worst_net}

        # Averages over non-zero months only
        non_zero_income = [v for v in incomes if v != 0.0]
        non_zero_spending = [v for v in spendings if v != 0.0]
        avg_income = round(sum(non_zero_income) / len(non_zero_income), 2) if non_zero_income else 0.0
        avg_spending = round(sum(non_zero_spending) / len(non_zero_spending), 2) if non_zero_spending else 0.0

        # Use gross salary as income denominator when configured - budget income excludes
        # pre-tax retirement contributions and off-budget transfers.
        load_env()
        _salary_raw = os.environ.get("YNAB_GROSS_SALARY", "").strip()
        gross_monthly = round(float(_salary_raw) / 12, 2) if _salary_raw else None

        # Actionable insight string
        insight = _build_insight(data, streak_type, streak_count, avg_income, avg_spending, gross_monthly)

        return {
            "months": data,
            "streak": {"type": streak_type, "count": streak_count},
            "best_month": best_month,
            "worst_month": worst_month,
            "avg_monthly_income": avg_income,
            "gross_monthly_income": gross_monthly,
            "avg_monthly_spending": avg_spending,
            "insight": insight,
        }
    except sqlite3.OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database not ready. Run 'ynab sync' first. ({exc})",
        )
    finally:
        conn.close()
