"""GET /api/calibration - target calibration: compare avg spend vs budgeted targets."""

from __future__ import annotations

import sqlite3
import statistics
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ynab_tools.db import get_connection
from ynab_tools.stats import round_up_5
from ynab_tools.stats import spending_stats as _spending_stats
from ynab_tools.stats import zscore_vs_history as _zscore_vs_history

from ._common import month_starts, should_skip_group

router = APIRouter(tags=["calibration"])


def _count_anomaly_months(spend_values: list[float], window: int = 6) -> int:
    """Count months in the last `window` months where z-score > 2.0.

    Uses a rolling z-score: for each month i in the window, score it against
    all prior months in the full spend_values list. Requires at least 5 data
    points (4 prior + current) to produce a score for any month.

    spend_values is ordered oldest-first; we inspect the last `window` entries.
    """
    n = len(spend_values)
    if n < 5:
        return 0

    count = 0
    start = max(0, n - window)
    for i in range(start, n):
        z = _zscore_vs_history(spend_values[: i + 1])
        if z is not None and z > 2.0:
            count += 1
    return count


@router.get("/calibration")
def calibration(
    months: int = Query(default=12, ge=3, le=36, description="Number of complete months to analyze"),
) -> dict[str, Any]:
    """Compare average monthly spend vs budgeted targets to surface over/under-targeted categories."""
    conn = get_connection()
    try:
        month_list = month_starts(months, complete_only=True)
        # month_list[0] is the most recent complete month (current_month in response)
        current_month = month_list[0]

        # Find the most recent budget_categories month that has real (non-zero) budgeted data.
        # MAX(month) from budget_months can return a future placeholder month with no data set.
        latest_row = conn.execute("SELECT MAX(budget_month) AS m FROM budget_categories WHERE budgeted > 0").fetchone()
        latest_month = latest_row["m"] if latest_row and latest_row["m"] else current_month

        # Fetch current targets from the latest budget month
        target_rows = conn.execute(
            """
            SELECT id, category_group_name, name, budgeted, goal_type, goal_target,
                   goal_target_month, goal_under_funded
            FROM budget_categories
            WHERE budget_month = ? AND hidden = 0 AND deleted = 0
            """,
            (latest_month,),
        ).fetchall()

        # Build target map: (group, name) -> current_target
        # Uses the same resolution logic as budget_fit.py for consistency:
        #   NEED/MF (no date): goal_target IS the monthly amount
        #   NEED/TBD/DEBT (with date): monthly installment = budgeted + goal_under_funded
        #   TB (no date), no goal: use budgeted (goal_target is the full balance gap, not monthly)
        target_map: dict[tuple[str, str], float] = {}
        id_map: dict[tuple[str, str], str] = {}
        goal_type_map: dict[tuple[str, str], str] = {}
        goal_target_month_map: dict[tuple[str, str], str | None] = {}
        for row in target_rows:
            group = row["category_group_name"] or ""
            if should_skip_group(group):
                continue
            key = (group, row["name"])
            goal_type = row["goal_type"]
            goal_target = float(row["goal_target"] or 0)
            goal_target_month = row["goal_target_month"]
            goal_under_funded = float(row["goal_under_funded"] or 0)
            budgeted = float(row["budgeted"] or 0)
            id_map[key] = row["id"]
            if goal_type is not None:
                goal_type_map[key] = goal_type
                goal_target_month_map[key] = goal_target_month

            if goal_type in ("NEED", "MF") and not goal_target_month and goal_target > 0:
                target = goal_target
            elif goal_type in ("NEED", "TBD", "DEBT") and goal_target_month:
                target = max(budgeted + goal_under_funded, 0.0)
            elif goal_type is not None:
                target = budgeted
            else:
                target = 0.0  # no goal: UNBUDGETED if there is spend history

            if target > 0:
                target_map[key] = target

        # Fetch activity for all analysis months in one query
        placeholders = ",".join("?" for _ in month_list)
        activity_rows = conn.execute(
            f"""
            SELECT budget_month, category_group_name, name, budgeted, activity
            FROM budget_categories
            WHERE budget_month IN ({placeholders})
              AND deleted = 0
              -- hidden intentionally omitted: INSERT OR REPLACE propagates the current
              -- hidden flag to all historical rows, so categories hidden after the fact
              -- (e.g. a paid-off loan) would lose their entire activity history.
            ORDER BY category_group_name, name, budget_month
            """,
            month_list,
        ).fetchall()

        # Accumulate spend per (group, name)
        spend_data: dict[tuple[str, str], list[float]] = {}
        for row in activity_rows:
            group = row["category_group_name"] or ""
            if should_skip_group(group):
                continue
            budgeted = float(row["budgeted"] or 0)
            activity = float(row["activity"] or 0)
            # Exclude months where category didn't exist yet
            if budgeted == 0 and activity == 0:
                continue
            key = (group, row["name"])
            spend_val = abs(activity) if activity < 0 else 0.0
            spend_data.setdefault(key, []).append(spend_val)

        # Remove pure accumulation categories (funded monthly but never spent -
        # savings buckets, sinking funds that haven't been drawn yet, etc.)
        # Calibration is meaningless for these: median=0 would always flag UNDER_TARGET.
        spend_data = {k: v for k, v in spend_data.items() if sum(v) > 0}

        # Combine all category keys: those with targets and those with spend history
        all_keys = set(target_map.keys()) | set(spend_data.keys())

        categories: list[dict[str, Any]] = []

        for key in all_keys:
            group, name = key
            current_target = target_map.get(key, 0.0)
            spend_values = spend_data.get(key, [])

            months_active = len(spend_values)

            # Skip categories with no spend history (pure savings, undrawn sinking funds).
            if months_active == 0:
                continue

            avg_monthly_spend = round(sum(spend_values) / months_active, 2)
            median_monthly_spend = round(statistics.median(spend_values), 2)

            # Skip categories with no budget and no spend
            if current_target == 0 and avg_monthly_spend == 0:
                continue

            # Compute variance signals using the same CV/lumpy logic as funding_status
            stats = _spending_stats(spend_values)
            is_high_variance = stats["pattern"] in ("high_variance", "lumpy")

            # Use median as primary signal - resistant to outlier months.
            # For recommendations, prefer trimmed mean (drops min/max) when variance is high,
            # since median may be 0 for lumpy categories but trimmed mean captures typical cost.
            primary = median_monthly_spend
            rec_base = stats["trimmed_avg"] if is_high_variance and stats["trimmed_avg"] > 0 else primary
            recommended_target = round_up_5(rec_base) if rec_base > 0 else 0.0

            # variance_pct relative to current_target, based on median
            variance_pct = round((primary - current_target) / current_target * 100, 1) if current_target > 0 else None

            # months where actual spending exceeded the goal target
            months_over_target = sum(1 for s in spend_values if s > current_target) if current_target > 0 else 0

            # Status classification:
            # - UNDER_TARGET requires primary > 0 AND consistent spending pattern.
            # - OVER_TARGET suppressed for high-variance/lumpy: median may spike in one outlier
            #   month; surfacing OVER_TARGET would give misleading "raise your target" advice.
            # Both high-variance cases land in ON_TARGET instead.
            if current_target == 0 and primary > 0:
                status = "UNBUDGETED"
            elif primary > current_target * 1.10 and not is_high_variance:
                status = "OVER_TARGET"
            elif current_target > 0 and primary > 0 and primary < current_target * 0.80 and not is_high_variance:
                status = "UNDER_TARGET"
            else:
                status = "ON_TARGET"

            recent_anomaly_months = _count_anomaly_months(spend_values, window=6)

            # recommendation_confidence: how reliable the recommendation is given anomaly history.
            # We only know if the z_score computation was attempted when months_active >= 5
            # (same minimum required by zscore_vs_history). When < 5 months, the anomaly
            # count is always 0 (from _count_anomaly_months) and we cannot distinguish
            # "no anomalies" from "insufficient history".
            has_anomaly_history = months_active >= 5
            suggests_raise = recommended_target > current_target

            if not has_anomaly_history:
                recommendation_confidence = None
            elif recent_anomaly_months >= 4:
                recommendation_confidence = "high"
            elif recent_anomaly_months >= 1:
                recommendation_confidence = "moderate"
            elif suggests_raise:
                # Consistent overspend with no anomaly months = genuine structural underfunding
                recommendation_confidence = "moderate"
            else:
                # Zero anomaly months, under-target recommendation - clean reduction signal
                recommendation_confidence = "high"

            if not has_anomaly_history:
                anomaly_supports_raise = None
            elif recent_anomaly_months >= 3:
                anomaly_supports_raise = True
            else:
                anomaly_supports_raise = False

            categories.append(
                {
                    "id": id_map.get(key),
                    "group": group,
                    "name": name,
                    "current_target": current_target,
                    "median_monthly_spend": median_monthly_spend,
                    "avg_monthly_spend": avg_monthly_spend,
                    "recommended_target": recommended_target,
                    "variance_pct": variance_pct,
                    "months_active": months_active,
                    "months_over_target": months_over_target,
                    "status": status,
                    "spending_pattern": stats["pattern"],
                    "cv": stats["cv"],
                    "goal_type": goal_type_map.get(key),
                    "goal_target_month": goal_target_month_map.get(key),
                    "budget_month": latest_month,
                    "recent_anomaly_months": recent_anomaly_months,
                    "recommendation_confidence": recommendation_confidence,
                    "anomaly_supports_raise": anomaly_supports_raise,
                }
            )

        # Sort: OVER_TARGET desc variance, then ON_TARGET, then UNDER_TARGET asc variance
        def _sort_key(c: dict[str, Any]) -> tuple[int, float]:
            order = {"OVER_TARGET": 0, "ON_TARGET": 1, "UNDER_TARGET": 2, "UNBUDGETED": 3}
            rank = order[c["status"]]
            vp = c["variance_pct"] if c["variance_pct"] is not None else 0.0
            # OVER_TARGET: descending variance (negate for ascending sort)
            # UNDER_TARGET: ascending variance (already ascending)
            if c["status"] == "OVER_TARGET":
                return (rank, -vp)
            return (rank, vp)

        categories.sort(key=_sort_key)

        over_count = sum(1 for c in categories if c["status"] == "OVER_TARGET")
        under_count = sum(1 for c in categories if c["status"] == "UNDER_TARGET")
        on_count = sum(1 for c in categories if c["status"] == "ON_TARGET")
        unbudgeted_count = sum(1 for c in categories if c["status"] == "UNBUDGETED")

        # Aggregate calibration impact (same math as budget_fit calibration_summary)
        potential_savings = round(
            sum(
                max(0.0, c["current_target"] - c["recommended_target"])
                for c in categories
                if c["status"] == "UNDER_TARGET"
            ),
            2,
        )
        required_additions = round(
            sum(
                max(0.0, c["recommended_target"] - c["current_target"])
                for c in categories
                if c["status"] == "OVER_TARGET"
            ),
            2,
        )
        net_headroom_impact = round(potential_savings - required_additions, 2)

        return {
            "months_analyzed": months,
            "current_month": current_month,
            "categories": categories,
            "over_target_count": over_count,
            "under_target_count": under_count,
            "on_target_count": on_count,
            "unbudgeted_count": unbudgeted_count,
            "potential_savings": potential_savings,
            "required_additions": required_additions,
            "net_headroom_impact": net_headroom_impact,
        }

    except sqlite3.OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database not ready. Run 'ynab sync' first. ({exc})",
        )
    finally:
        conn.close()
