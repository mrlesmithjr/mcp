"""Paycheck breakdown: split categories into regular-paycheck vs bonus-funded buckets."""

import logging
import os
import sqlite3
from datetime import datetime

from ..config import load_env
from ..db import get_connection, init_db
from .funding import _month_str

logger = logging.getLogger(__name__)

_DEFAULT_BONUS_FUNDED_GROUPS = "Savings,Retirement,Holding,Investment,Emergency"


def _get_breakdown_config() -> tuple[set[str], set[str], float, int, set[str], set[str]]:
    load_env()
    groups_str = os.environ.get("YNAB_BONUS_FUNDED_GROUPS", _DEFAULT_BONUS_FUNDED_GROUPS)
    cats_str = os.environ.get("YNAB_BONUS_FUNDED_CATEGORIES", "")
    regular_pay_str = os.environ.get("YNAB_REGULAR_PAY", "").strip()
    excluded_str = os.environ.get("YNAB_EXCLUDED_GROUPS", "Credit Card Payments,Internal Master Category")
    excluded_cats_str = os.environ.get("YNAB_EXCLUDED_CATEGORIES", "")

    groups = {g.strip() for g in groups_str.split(",") if g.strip()}
    cats = {c.strip() for c in cats_str.split(",") if c.strip()}
    regular_pay = float(regular_pay_str) if regular_pay_str else None
    excluded = {g.strip() for g in excluded_str.split(",") if g.strip()}
    excluded_cats = {c.strip() for c in excluded_cats_str.split(",") if c.strip()}
    return groups, cats, regular_pay, 2, excluded, excluded_cats


def _is_bonus_funded(name: str, group: str | None, bonus_groups: set[str], bonus_cats: set[str]) -> bool:
    if name in bonus_cats:
        return True
    name_lower = name.lower()
    group_lower = (group or "").lower()
    for bg in bonus_groups:
        bg_lower = bg.lower()
        if bg_lower in group_lower or bg_lower in name_lower:
            return True
    return False


def _get_3mo_avgs(conn: sqlite3.Connection, month: str) -> dict[str, float]:
    t = datetime.strptime(month, "%Y-%m-01")
    prior_months = []
    for i in range(1, 4):
        m = t.month - i
        y = t.year
        while m <= 0:
            m += 12
            y -= 1
        prior_months.append(f"{y:04d}-{m:02d}-01")

    placeholders = ",".join("?" for _ in prior_months)
    rows = conn.execute(
        f"""
        SELECT name, AVG(ABS(activity)) AS avg_spend
        FROM budget_categories
        WHERE budget_month IN ({placeholders})
          AND deleted = 0
          -- hidden intentionally omitted: INSERT OR REPLACE propagates the current
          -- hidden flag to all historical rows, so categories hidden after the fact
          -- (e.g. a paid-off loan) would lose their entire activity history.
          AND activity < 0
        GROUP BY name
        """,
        prior_months,
    ).fetchall()
    return {row["name"]: row["avg_spend"] or 0.0 for row in rows}


def _is_excluded(group: str, name: str, excluded_groups: set[str], excluded_cats: set[str]) -> bool:
    if name in excluded_cats:
        return True
    group_lower = group.lower()
    return any(ex.lower() in group_lower for ex in excluded_groups)


def _collect_categories(conn: sqlite3.Connection, month: str) -> tuple[list[dict], list[dict]]:
    bonus_groups, bonus_cats, regular_pay, checks, excluded, excluded_cats = _get_breakdown_config()
    avgs = _get_3mo_avgs(conn, month)

    rows = conn.execute(
        """
        SELECT name, category_group_name, budgeted, activity,
               goal_type, goal_target, goal_cadence
        FROM budget_categories
        WHERE budget_month = ?
          AND deleted = 0 AND hidden = 0
          AND (goal_target > 0 OR budgeted > 0 OR ABS(activity) > 0)
        ORDER BY category_group_name, name
        """,
        (month,),
    ).fetchall()

    regular, bonus = [], []
    for row in rows:
        r = dict(row)
        name = r["name"]
        group = r["category_group_name"] or ""
        cadence_val = r.get("goal_cadence")
        cadence = cadence_val if cadence_val is not None else 1
        raw_goal = r["goal_target"] or 0.0
        if raw_goal and cadence == 13:
            monthly_goal = raw_goal / 12
        elif raw_goal and 1 < cadence < 13:
            monthly_goal = raw_goal / cadence
        elif cadence == 0:
            monthly_goal = r["budgeted"] or 0.0
        else:
            monthly_goal = raw_goal
        target = monthly_goal or r["budgeted"] or 0.0
        avg = avgs.get(name, 0.0)
        is_hot = avg > 0 and target > 0 and avg > target * 1.05

        entry = {"name": name, "group": group, "target": target, "avg_3mo": avg, "is_hot": is_hot}

        if _is_excluded(group, name, excluded, excluded_cats):
            continue
        if _is_bonus_funded(name, group, bonus_groups, bonus_cats):
            bonus.append(entry)
        else:
            regular.append(entry)

    return regular, bonus


def run_paycheck_breakdown(month: str | None = None) -> None:
    month = _month_str(month)
    conn = get_connection()
    try:
        init_db(conn)

        _, _, regular_pay, checks, _, _ = _get_breakdown_config()
        if regular_pay is None:
            print("Error: regular pay amount not configured.")
            print("Set YNAB_REGULAR_PAY in your .env to use this report.")
            return
        monthly_income = regular_pay * checks

        regular_rows, bonus_rows = _collect_categories(conn, month)

        if not regular_rows and not bonus_rows:
            print(f"No budget data for {month[:7]}. Run 'ynab sync' first.")
            return

        print(f"Paycheck Budget Breakdown ({month[:7]})")
        print(f"Regular paycheck income: ${monthly_income:,.0f}/mo ({checks} x ${regular_pay:,.0f})")
        print("=" * 80)
        print()

        def _print_bucket(rows: list[dict], header: str, show_headroom: bool = False) -> None:
            target_total = sum(r["target"] for r in rows)
            avg_total = sum(r["avg_3mo"] for r in rows)

            print(header)
            print(f"  {'Category':<36} {'Target':>9}  {'3mo Avg':>9}  {'Notes'}")
            print(f"  {'-' * 66}")

            current_group = None
            for r in rows:
                if r["group"] != current_group:
                    current_group = r["group"]
                    print(f"\n  {current_group}")
                tgt = f"${r['target']:>8,.0f}" if r["target"] else "         -"
                avg = f"${r['avg_3mo']:>8,.0f}" if r["avg_3mo"] else "         -"
                note = "HOT" if r["is_hot"] else ""
                print(f"    {r['name']:<34} {tgt}  {avg}  {note}")

            print(f"\n  {'-' * 66}")
            print(f"  {'Total (targets):':<36} ${target_total:>9,.0f}")
            print(f"  {'Total (3mo avg):':<36} ${avg_total:>9,.0f}")

            if show_headroom:
                print(f"  {'Regular income:':<36} ${monthly_income:>9,.0f}")
                ht = monthly_income - target_total
                ha = monthly_income - avg_total
                sign_t = "+" if ht >= 0 else ""
                sign_a = "+" if ha >= 0 else ""
                print(f"  {'Headroom (vs targets):':<36} {sign_t}${ht:>8,.0f}")
                print(f"  {'Headroom (vs 3mo avg):':<36} {sign_a}${ha:>8,.0f}   <- real headroom")
            else:
                print("  (Funded from bonus paychecks, not regular pay)")

            print()

        _print_bucket(
            regular_rows,
            f"REGULAR PAYCHECK BUCKET (must fit within ${monthly_income:,.0f})",
            show_headroom=True,
        )
        _print_bucket(bonus_rows, "BONUS-FUNDED BUCKET (funded from bonus only)")

    finally:
        conn.close()
