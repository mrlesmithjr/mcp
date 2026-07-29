"""GET /api/budget-fit - holistic view of total targets vs average monthly income."""

from __future__ import annotations

import sqlite3
import statistics
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ynab_tools.db import get_connection
from ynab_tools.stats import round_up_5
from ynab_tools.stats import spending_stats as _spending_stats

from ._common import month_starts, should_skip_group

router = APIRouter(tags=["budget-fit"])


@router.get("/budget-fit")
def budget_fit(
    months: int = Query(default=12, ge=3, le=36, description="Number of complete months to analyze"),
) -> dict[str, Any]:
    """Total of all budget targets vs average monthly income, with calibration impact summary."""
    conn = get_connection()
    try:
        complete_months = month_starts(months, complete_only=True)

        # --- Income averaging (complete months only, excludes current in-progress month) ---
        placeholders = ",".join("?" for _ in complete_months)
        # placeholders contains only '?' characters; complete_months values are bound as parameters
        income_rows = conn.execute(
            f"SELECT month, income FROM budget_months WHERE month IN ({placeholders}) ORDER BY month DESC",
            complete_months,
        ).fetchall()

        non_zero_incomes = [float(r["income"]) for r in income_rows if r["income"] and float(r["income"]) > 0]
        avg_monthly_income = round(sum(non_zero_incomes) / len(non_zero_incomes), 2) if non_zero_incomes else 0.0

        # Current month income (separate query - may be in-progress)
        from datetime import date

        current_month_str = date.today().strftime("%Y-%m-01")
        cur_row = conn.execute(
            "SELECT income FROM budget_months WHERE month = ?",
            (current_month_str,),
        ).fetchone()
        current_month_income = float(cur_row["income"] or 0) if cur_row else 0.0

        # --- Target resolution: use latest budget month with real data ---
        latest_row = conn.execute("SELECT MAX(budget_month) AS m FROM budget_categories WHERE budgeted > 0").fetchone()
        latest_month = latest_row["m"] if latest_row and latest_row["m"] else current_month_str

        target_rows = conn.execute(
            """
            SELECT id, category_group_name, name, budgeted, goal_type, goal_target,
                   goal_target_month, goal_under_funded
            FROM budget_categories
            WHERE budget_month = ? AND hidden = 0 AND deleted = 0
            """,
            (latest_month,),
        ).fetchall()

        # Build per-category monthly obligation map.
        #
        # The goal is "how much should flow through this category each month" - the true
        # monthly obligation - regardless of whether the user has funded it yet this month.
        #
        #   NEED (no date): recurring monthly spend goal.
        #       goal_target IS the monthly amount → use it.
        #   MF: recurring monthly contribution.
        #       goal_target IS the monthly amount → use it.
        #   NEED (with date): periodic/annual expense (e.g. property taxes, Roth IRA).
        #       goal_target is the full period total, not monthly. The monthly installment
        #       is budgeted + goal_under_funded (funded so far + still needed this month).
        #   TBD: savings goal with a target date (e.g. vacation fund due in 6 months).
        #       Same: monthly installment = budgeted + goal_under_funded.
        #   DEBT: debt payoff goal. Monthly payment = budgeted + goal_under_funded.
        #   TB: target balance goal with no date (e.g. emergency fund target $15k).
        #       goal_under_funded here can be the full remaining balance gap, not a
        #       monthly installment. Use just budgeted to avoid inflating the total.
        #   None: no goal → use whatever is allocated this month (budgeted).
        cat_targets: dict[tuple[str, str], float] = {}
        for row in target_rows:
            group = row["category_group_name"] or ""
            if should_skip_group(group):
                continue
            goal_type = row["goal_type"]
            goal_target = float(row["goal_target"] or 0)
            goal_target_month = row["goal_target_month"]
            goal_under_funded = float(row["goal_under_funded"] or 0)
            budgeted = float(row["budgeted"] or 0)

            if goal_type in ("NEED", "MF") and not goal_target_month and goal_target > 0:
                # Recurring monthly goal: declared amount is the monthly target.
                target = goal_target
            elif goal_type in ("NEED", "TBD", "DEBT") and goal_target_month:
                # Dated goal: monthly installment = what's funded + what's still needed.
                target = max(budgeted + goal_under_funded, 0.0)
            else:
                # TB (no date), MF/NEED with no goal_target, None:
                # use the current month's budgeted allocation.
                target = budgeted

            cat_targets[(group, row["name"])] = target

        total_current_targets = round(sum(cat_targets.values()), 2)
        headroom = round(avg_monthly_income - total_current_targets, 2)
        pct_committed = round(total_current_targets / avg_monthly_income * 100, 1) if avg_monthly_income > 0 else 0.0

        # --- Calibration impact: run spend analysis on complete historical months ---
        c_placeholders = ",".join("?" for _ in complete_months)
        # c_placeholders contains only '?' characters; complete_months values are bound as parameters
        activity_rows = conn.execute(
            f"""
            SELECT budget_month, category_group_name, name, budgeted, activity
            FROM budget_categories
            WHERE budget_month IN ({c_placeholders})
              AND deleted = 0
              -- hidden intentionally omitted: INSERT OR REPLACE propagates the current
              -- hidden flag to all historical rows, so categories hidden after the fact
              -- (e.g. a paid-off loan) would lose their entire activity history.
            ORDER BY category_group_name, name, budget_month
            """,
            complete_months,
        ).fetchall()

        spend_data: dict[tuple[str, str], list[float]] = {}
        for row in activity_rows:
            group = row["category_group_name"] or ""
            if should_skip_group(group):
                continue
            b = float(row["budgeted"] or 0)
            a = float(row["activity"] or 0)
            if b == 0 and a == 0:
                continue
            key = (group, row["name"])
            spend_val = abs(a) if a < 0 else 0.0
            spend_data.setdefault(key, []).append(spend_val)

        spend_data = {k: v for k, v in spend_data.items() if sum(v) > 0}

        potential_savings = 0.0
        required_additions = 0.0
        over_target_count = 0
        under_target_count = 0
        unbudgeted_avg_spend = 0.0
        group_over: dict[str, int] = defaultdict(int)
        group_under: dict[str, int] = defaultdict(int)

        for key, spend_values in spend_data.items():
            group, _name = key
            if not spend_values:
                continue
            current_target = cat_targets.get(key, 0.0)
            primary = round(statistics.median(spend_values), 2)
            stats = _spending_stats(spend_values)
            is_high_variance = stats["pattern"] in ("high_variance", "lumpy")
            rec_base = stats["trimmed_avg"] if is_high_variance and stats["trimmed_avg"] > 0 else primary
            recommended = round_up_5(rec_base) if rec_base > 0 else 0.0

            if current_target == 0 and primary > 0:
                avg_spend = round(sum(spend_values) / len(spend_values), 2)
                unbudgeted_avg_spend += avg_spend
            elif primary > current_target * 1.10 and not is_high_variance:
                # OVER_TARGET: spending exceeds target - target needs to be raised
                required_additions += max(0.0, recommended - current_target)
                over_target_count += 1
                group_over[group] += 1
            elif current_target > 0 and primary > 0 and primary < current_target * 0.80 and not is_high_variance:
                # UNDER_TARGET: target exceeds spending - target can be lowered
                potential_savings += max(0.0, current_target - recommended)
                under_target_count += 1
                group_under[group] += 1

        net_headroom_change = round(potential_savings - required_additions, 2)
        recommended_total = round(total_current_targets - net_headroom_change, 2)
        recommended_headroom = round(avg_monthly_income - recommended_total, 2)

        # --- Groups breakdown ---
        group_totals: dict[str, float] = defaultdict(float)
        group_counts: dict[str, int] = defaultdict(int)
        for (group, _name), target in cat_targets.items():
            group_totals[group] += target
            group_counts[group] += 1

        groups = []
        for group_name, g_total in group_totals.items():
            g_total_rounded = round(g_total, 2)
            groups.append(
                {
                    "name": group_name,
                    "total_target": g_total_rounded,
                    "pct_of_income": (
                        round(g_total_rounded / avg_monthly_income * 100, 1) if avg_monthly_income > 0 else 0.0
                    ),
                    "category_count": group_counts[group_name],
                    "over_target_count": group_over.get(group_name, 0),
                    "under_target_count": group_under.get(group_name, 0),
                }
            )

        groups.sort(key=lambda g: g["total_target"], reverse=True)

        return {
            "avg_monthly_income": avg_monthly_income,
            "current_month_income": current_month_income,
            "analysis_months": months,
            "budget_month": latest_month,
            "total_current_targets": total_current_targets,
            "headroom": headroom,
            "pct_committed": pct_committed,
            "groups": groups,
            "calibration_summary": {
                "potential_savings": round(potential_savings, 2),
                "required_additions": round(required_additions, 2),
                "net_headroom_change": net_headroom_change,
                "recommended_total": recommended_total,
                "recommended_headroom": recommended_headroom,
                "over_target_count": over_target_count,
                "under_target_count": under_target_count,
                "unbudgeted_avg_spend": round(unbudgeted_avg_spend, 2),
            },
        }

    except sqlite3.OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database not ready. Run 'ynab sync' first. ({exc})",
        )
    finally:
        conn.close()
