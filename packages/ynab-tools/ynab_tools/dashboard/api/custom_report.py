"""GET /api/report - ad-hoc custom report: group by category group, category, or payee."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from ynab_tools.db import get_connection

from ._common import month_starts, should_skip_group

router = APIRouter(tags=["report"])

_GROUP_BY_VALUES = {"category_group", "category", "payee"}
_METRIC_VALUES = {"spending", "budgeted"}

_GROUP_BY_COLS = {"category_group": "category_group_name", "category": "name"}
_METRIC_EXPRS = {
    "spending": "-SUM(CASE WHEN activity < 0 THEN activity ELSE 0 END)",
    "budgeted": "SUM(budgeted)",
}
_METRIC_WHERE = {"spending": "AND activity < 0", "budgeted": "AND budgeted > 0"}


@router.get("/report")
def custom_report(
    group_by: str = Query(default="category_group", description="Dimension: category_group, category, or payee"),
    metric: str = Query(default="spending", description="Metric: spending or budgeted"),
    months: int = Query(default=12, ge=3, le=24, description="Number of months to include"),
) -> Any:
    """Ad-hoc report: totals and monthly averages for the chosen dimension and metric."""
    if group_by not in _GROUP_BY_VALUES:
        return JSONResponse(status_code=422, content={"detail": f"group_by must be one of {sorted(_GROUP_BY_VALUES)}"})
    if metric not in _METRIC_VALUES:
        return JSONResponse(status_code=422, content={"detail": f"metric must be one of {sorted(_METRIC_VALUES)}"})
    if group_by == "payee" and metric == "budgeted":
        return JSONResponse(status_code=422, content={"detail": "metric=budgeted is not supported with group_by=payee"})

    conn = get_connection()
    try:
        month_list = month_starts(months, complete_only=True)
        placeholders = ",".join("?" for _ in month_list)
        rows: list[dict[str, Any]] = []

        if group_by == "payee":
            # Payee net spending from transactions table - SUM(amount) is negative for
            # net outflow; returns (positive amounts) naturally offset charges.
            db_rows = conn.execute(
                f"""
                SELECT payee_name AS label, SUM(amount) AS net
                FROM transactions
                WHERE strftime('%Y-%m', date) IN ({placeholders})
                  AND deleted = 0
                  AND transfer_account_id IS NULL
                  AND payee_name IS NOT NULL
                  AND payee_name NOT LIKE '%Balance Adjustment%'
                  AND payee_name != 'Starting Balance'
                  AND payee_name != 'Wire Transfer'
                GROUP BY payee_name
                HAVING net < 0
                ORDER BY net ASC
                LIMIT 25
                """,
                [m[:7] for m in month_list],  # "YYYY-MM-01" -> "YYYY-MM"
            ).fetchall()
            rows = [{"label": r["label"], "total": round(abs(float(r["net"] or 0)), 2)} for r in db_rows]

        else:
            # Category group or category from budget_categories (amounts already in dollars)
            label_col = _GROUP_BY_COLS[group_by]
            value_expr = _METRIC_EXPRS[metric]
            where_extra = _METRIC_WHERE[metric]

            db_rows = conn.execute(
                f"""
                SELECT {label_col} AS label, category_group_name AS grp, {value_expr} AS total
                FROM budget_categories
                WHERE budget_month IN ({placeholders})
                  AND deleted = 0
                  -- hidden intentionally omitted: INSERT OR REPLACE propagates the current
                  -- hidden flag to all historical rows, so categories hidden after the fact
                  -- (e.g. a paid-off loan) would lose their entire activity history.
                  {where_extra}
                GROUP BY {label_col}, category_group_name
                ORDER BY total DESC
                """,
                month_list,
            ).fetchall()

            # Aggregate by label (a category name can appear in only one group, but
            # a group name appears once per row so we sum across group duplicates when
            # group_by=category_group and two rows share the same group name)
            totals: dict[str, float] = {}
            for r in db_rows:
                if should_skip_group(r["grp"] or ""):
                    continue
                lbl = r["label"] or ""
                totals[lbl] = totals.get(lbl, 0.0) + float(r["total"] or 0)

            rows = [
                {"label": lbl, "total": round(total, 2)}
                for lbl, total in sorted(totals.items(), key=lambda x: x[1], reverse=True)
            ]

        for r in rows:
            r["monthly_avg"] = round(r["total"] / len(month_list), 2) if month_list else 0.0

        return {
            "group_by": group_by,
            "metric": metric,
            "months": months,
            "rows": rows,
        }

    except sqlite3.OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database not ready. Run 'ynab sync' first. ({exc})",
        )
    finally:
        conn.close()
