"""GET /api/summary - monthly income vs spending trend for the bar chart."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ynab_tools.db import get_connection

from ._common import month_starts, should_skip_group

router = APIRouter(tags=["summary"])


@router.get("/summary")
def summary(months: int = Query(default=6, ge=1, le=24, description="Number of months to return")) -> dict[str, Any]:
    """Monthly income vs spending for the last N months, oldest first (for charting)."""
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
            -- hidden intentionally omitted: INSERT OR REPLACE propagates current hidden flag
            -- to historical rows; categories hidden after the fact must remain in past-month data.
            WHERE budget_month IN ({placeholders})
              AND deleted = 0
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
        data = []
        for m in sorted(month_list):
            row = month_map.get(m)
            inc = float(row["income"] or 0) if row else 0.0
            spend = -spend_by_month.get(m, 0.0)
            data.append(
                {
                    "month": m,
                    "income": inc,
                    "spending": spend,
                    "net": round(inc - spend, 2),
                    "surplus": inc >= spend,
                }
            )

        return {"months": data, "count": len(data)}
    except sqlite3.OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database not ready. Run 'ynab sync' first. ({exc})",
        )
    finally:
        conn.close()
