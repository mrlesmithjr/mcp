"""GET /api/churn - funding churn analysis: categories funded and raided repeatedly."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ynab_tools.db import get_connection
from ynab_tools.stats import should_skip_group

router = APIRouter(tags=["churn"])

_VALID_DAYS = {30, 60, 90, 180}


@router.get("/churn")
def churn_analysis(
    days: int = Query(default=90),
) -> dict[str, Any]:
    """Categories ranked by funding churn - repeated add/remove cycles signal structural underfunding."""
    if days not in _VALID_DAYS:
        raise HTTPException(status_code=422, detail=f"days must be one of {sorted(_VALID_DAYS)}")

    cutoff = f"-{days} days"

    conn = get_connection()
    try:
        from datetime import datetime as _dt

        current_month = _dt.now().strftime("%Y-%m-01")

        rows = conn.execute(
            """
            WITH adds AS (
                SELECT to_category_id   AS category_id,
                       to_category_name AS category_name,
                       amount           AS delta
                FROM money_movements
                WHERE replace(moved_at, 'Z', '') >= datetime('now', ?)
                  AND deleted = 0
                  AND to_category_id IS NOT NULL
                  AND to_category_name IS NOT NULL
            ),
            removes AS (
                SELECT from_category_id   AS category_id,
                       from_category_name AS category_name,
                       -amount            AS delta
                FROM money_movements
                WHERE replace(moved_at, 'Z', '') >= datetime('now', ?)
                  AND deleted = 0
                  AND from_category_id IS NOT NULL
                  AND from_category_name IS NOT NULL
            ),
            combined AS (SELECT * FROM adds UNION ALL SELECT * FROM removes)
            SELECT
                combined.category_id,
                combined.category_name,
                bc.category_group_name                                                  AS category_group,
                COUNT(*)                                                                AS total_moves,
                SUM(CASE WHEN combined.delta > 0 THEN 1 ELSE 0 END)                   AS times_added,
                SUM(CASE WHEN combined.delta < 0 THEN 1 ELSE 0 END)                   AS times_removed,
                ROUND(SUM(CASE WHEN combined.delta > 0 THEN combined.delta  ELSE 0 END), 2) AS total_added,
                ROUND(SUM(CASE WHEN combined.delta < 0 THEN ABS(combined.delta) ELSE 0 END), 2) AS total_removed,
                ROUND(SUM(combined.delta), 2)                                           AS net_change
            FROM combined
            LEFT JOIN budget_categories bc
                   ON bc.id = combined.category_id
                  AND bc.budget_month = (
                      SELECT MAX(budget_month) FROM budget_categories WHERE id = combined.category_id
                  )
            GROUP BY combined.category_id, combined.category_name
            HAVING times_added > 0
            ORDER BY total_moves DESC
            """,
            (cutoff, cutoff),
        ).fetchall()

        # Fetch current-month budgeted amounts for all category names in the result
        category_names = [dict(r)["category_name"] for r in rows]
        current_targets: dict[str, float] = {}
        if category_names:
            placeholders = ",".join("?" * len(category_names))
            target_rows = conn.execute(
                f"""
                SELECT name, budgeted
                FROM budget_categories
                WHERE budget_month = ? AND name IN ({placeholders}) AND deleted = 0
                """,
                [current_month, *category_names],
            ).fetchall()
            for tr in target_rows:
                current_targets[tr["name"]] = round(float(tr["budgeted"]), 2)

        result = []
        for r in rows:
            d = dict(r)
            if should_skip_group(d.get("category_group") or ""):
                continue
            times_added = d["times_added"] or 0
            times_removed = d["times_removed"] or 0
            d["is_churning"] = times_added > 0 and (times_removed / times_added) > 0.4
            d["current_target"] = current_targets.get(d["category_name"])
            result.append(d)

        return {"days": days, "rows": result}

    except sqlite3.OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database not ready. Run 'ynab sync' first. ({exc})",
        )
    finally:
        conn.close()
