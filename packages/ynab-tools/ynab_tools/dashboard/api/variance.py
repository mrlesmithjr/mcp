"""GET /api/variance - budget variance: categories with high month-over-month budgeted volatility."""

from __future__ import annotations

import sqlite3
import statistics
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ynab_tools.db import get_connection

from ._common import month_starts, should_skip_group

router = APIRouter(tags=["variance"])

_VALID_MONTHS = {3, 6, 12}
_MIN_AVG_BUDGETED = 10.0
_MIN_MONTHS = 3
_VOLATILE_CV_THRESHOLD = 25.0


@router.get("/variance")
def budget_variance(
    months: int = Query(default=6, ge=3, le=12, description="Number of complete months to analyze"),
) -> dict[str, Any]:
    """Categories ranked by month-over-month budgeted volatility (coefficient of variation).

    Catches structural underfunding even when budget changes are made in the YNAB app directly,
    where the funding_log-based churn view has no visibility.
    """
    if months not in _VALID_MONTHS:
        raise HTTPException(status_code=422, detail=f"months must be one of {sorted(_VALID_MONTHS)}")

    month_list = month_starts(months, complete_only=True)

    conn = get_connection()
    try:
        rows = conn.execute(
            f"""
            SELECT category_group_name, name, budget_month, budgeted
            FROM budget_categories
            WHERE budget_month IN ({",".join("?" * len(month_list))})
              AND deleted = 0 AND budgeted > 0
              -- hidden intentionally omitted: INSERT OR REPLACE propagates the current
              -- hidden flag to all historical rows, so categories hidden after the fact
              -- (e.g. a paid-off loan) would lose their entire activity history.
            ORDER BY category_group_name, name, budget_month
            """,
            month_list,
        ).fetchall()

        # Group budgeted values per category
        groups: dict[tuple[str | None, str], list[float]] = defaultdict(list)
        for r in rows:
            group = r["category_group_name"]
            if should_skip_group(group or ""):
                continue
            groups[(group, r["name"])].append(r["budgeted"])

        result = []
        for (group, name), values in groups.items():
            if len(values) < 2:
                continue
            avg = statistics.mean(values)
            if avg < _MIN_AVG_BUDGETED:
                continue
            stddev = statistics.stdev(values)
            cv_pct = round(stddev / avg * 100, 1)
            months_tracked = len(values)
            result.append(
                {
                    "category_name": name,
                    "category_group": group,
                    "months_tracked": months_tracked,
                    "avg_budgeted": round(avg, 2),
                    "stddev": round(stddev, 2),
                    "cv_pct": cv_pct,
                    "min_budgeted": round(min(values), 2),
                    "max_budgeted": round(max(values), 2),
                    "is_volatile": cv_pct >= _VOLATILE_CV_THRESHOLD and months_tracked >= _MIN_MONTHS,
                }
            )

        result.sort(key=lambda r: (not r["is_volatile"], -r["cv_pct"]))
        return {"months": months, "rows": result}
    except sqlite3.OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database not ready. Run 'ynab sync' first. ({exc})",
        ) from exc
    finally:
        conn.close()
