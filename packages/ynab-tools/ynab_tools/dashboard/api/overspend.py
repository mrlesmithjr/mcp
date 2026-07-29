"""GET /api/overspend - overspend ratio: categories where spending routinely exceeds allocation."""

from __future__ import annotations

import sqlite3
import statistics
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ynab_tools.db import get_connection

from ._common import month_starts, should_skip_group

router = APIRouter(tags=["overspend"])

_VALID_MONTHS = {3, 6, 12}
_MIN_MONTHS = 2
_OVERSPENT_RATE_THRESHOLD = 0.5


@router.get("/overspend")
def overspend_analysis(
    months: int = Query(default=6, ge=3, le=12, description="Number of complete months to analyze"),
) -> dict[str, Any]:
    """Categories ranked by how often spending exceeds the budgeted allocation.

    A category with a high overspend rate needs its target raised, distinct from churn
    (funded-then-raided). Both signals together surface different root causes of structural
    underfunding.
    """
    if months not in _VALID_MONTHS:
        raise HTTPException(status_code=422, detail=f"months must be one of {sorted(_VALID_MONTHS)}")

    month_list = month_starts(months, complete_only=True)

    conn = get_connection()
    try:
        rows = conn.execute(
            f"""
            SELECT category_group_name, name, budget_month, budgeted, activity
            FROM budget_categories
            -- hidden intentionally omitted: INSERT OR REPLACE propagates current hidden flag
            -- to historical rows; categories hidden after the fact must remain in past-month data.
            WHERE budget_month IN ({",".join("?" * len(month_list))})
              AND deleted = 0 AND budgeted > 0
            ORDER BY category_group_name, name, budget_month
            """,
            month_list,
        ).fetchall()

        # Group per category: list of (budgeted, spending, ratio, is_over) per month
        groups: dict[tuple[str | None, str], list[tuple[float, float]]] = defaultdict(list)
        for r in rows:
            group = r["category_group_name"]
            if should_skip_group(group or ""):
                continue
            budgeted = r["budgeted"]
            # activity is negative in YNAB for spending; income categories have positive activity
            spending = abs(r["activity"]) if r["activity"] < 0 else 0.0
            groups[(group, r["name"])].append((budgeted, spending))

        result = []
        for (group, name), monthly in groups.items():
            months_tracked = len(monthly)
            if months_tracked < _MIN_MONTHS:
                continue

            budgeted_vals = [b for b, _ in monthly]
            spending_vals = [s for _, s in monthly]
            ratios = [s / b for b, s in monthly if b > 0]
            times_overspent = sum(1 for b, s in monthly if s > b)
            overspend_rate = times_overspent / months_tracked

            result.append(
                {
                    "category_name": name,
                    "category_group": group,
                    "months_tracked": months_tracked,
                    "times_overspent": times_overspent,
                    "overspend_rate": round(overspend_rate, 4),
                    "avg_budgeted": round(statistics.mean(budgeted_vals), 2),
                    "avg_spending": round(statistics.mean(spending_vals), 2),
                    "avg_ratio": round(statistics.mean(ratios), 4) if ratios else 0.0,
                    "is_overspent": overspend_rate >= _OVERSPENT_RATE_THRESHOLD,
                }
            )

        result.sort(key=lambda r: (not r["is_overspent"], -r["overspend_rate"]))
        return {"months": months, "rows": result}
    except sqlite3.OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database not ready. Run 'ynab sync' first. ({exc})",
        ) from exc
    finally:
        conn.close()
