"""GET /api/group-trend - per-category-group spending totals across N months."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ynab_tools.db import get_connection

from ._common import month_starts, should_skip_group

group_trend_router = APIRouter(tags=["trends"])


@group_trend_router.get("/group-trend")
def group_trend(
    months: int = Query(default=6, ge=2, le=24, description="Number of months to return"),
) -> dict[str, Any]:
    """Per-category-group spending totals for each of the last N months."""
    conn = get_connection()
    try:
        month_list = month_starts(months)
        sorted_months = sorted(month_list)
        placeholders = ",".join("?" for _ in sorted_months)

        rows = conn.execute(
            f"""
            SELECT category_group_name, budget_month,
                   -SUM(CASE WHEN activity < 0 THEN activity ELSE 0 END) AS total
            FROM budget_categories
            WHERE budget_month IN ({placeholders})
              AND deleted = 0
              -- hidden intentionally omitted: INSERT OR REPLACE propagates the current
              -- hidden flag to all historical rows, so categories hidden after the fact
              -- (e.g. a paid-off loan) would lose their entire activity history.
            GROUP BY category_group_name, budget_month
            ORDER BY category_group_name, budget_month
            """,
            sorted_months,
        ).fetchall()

        # Build a nested dict: group -> month -> total
        group_month_totals: dict[str, dict[str, float]] = {}
        for row in rows:
            group = row["category_group_name"] or ""
            if should_skip_group(group):
                continue
            bm = row["budget_month"]
            total = float(row["total"] or 0)
            if group not in group_month_totals:
                group_month_totals[group] = {}
            group_month_totals[group][bm] = total

        # Build aligned totals arrays and compute grand totals for sorting
        group_entries: list[dict[str, Any]] = []
        for group, month_map in group_month_totals.items():
            totals = [round(month_map.get(m, 0.0), 2) for m in sorted_months]
            grand_total = sum(totals)
            group_entries.append(
                {
                    "group": group,
                    "totals": totals,
                    "_grand_total": grand_total,
                }
            )

        # Sort by total spending descending (most expensive groups first)
        group_entries.sort(key=lambda x: x["_grand_total"], reverse=True)

        # Strip the internal sort key before returning
        groups = [{"group": e["group"], "totals": e["totals"]} for e in group_entries]

        return {"months": sorted_months, "groups": groups}
    except sqlite3.OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database not ready. Run 'ynab sync' first. ({exc})",
        )
    finally:
        conn.close()
