"""GET /api/two-pot - two-pot rule compliance: did the bonus fund forward or backwards?"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException

from ynab_tools.config import load_env
from ynab_tools.db import get_connection, init_db
from ynab_tools.reports.paycheck_breakdown import (
    _get_breakdown_config,
    _is_bonus_funded,
    _is_excluded,
)
from ynab_tools.reports.two_pot_report import _get_paychecks, _holding_balance, _prior_month

router = APIRouter(tags=["two-pot"])


def _build_two_pot(conn: sqlite3.Connection) -> dict[str, Any]:
    load_env()
    bonus_groups, bonus_cats, regular_pay, checks, excluded, excluded_cats = _get_breakdown_config()
    bonus_threshold = float(os.environ.get("YNAB_BONUS_THRESHOLD", "") or 0) or None

    if not regular_pay or not bonus_threshold:
        return {
            "config_ok": False,
            "regular_pay": regular_pay,
            "bonus_threshold": bonus_threshold,
            "months": [],
        }

    monthly_regular = regular_pay * checks
    paychecks = _get_paychecks(conn)
    bonus_months: dict[str, dict] = {}
    for p in paychecks:
        if p["amount"] > bonus_threshold:
            month = p["date"][:7]
            # Keep only the first (most recent) bonus paycheck per month
            if month not in bonus_months:
                bonus_months[month] = {
                    "month": month,
                    "bonus_paycheck_date": p["date"],
                    "bonus_paycheck_amount": round(p["amount"], 2),
                    "bonus_portion": round(p["amount"] - regular_pay, 2),
                }

    result_months = []
    for month_key in sorted(bonus_months.keys(), reverse=True):
        bm = bonus_months[month_key]
        month_str = month_key + "-01"

        # Use naive ISO timestamp to match naive funding_log.timestamp strings.
        # UTC-aware isoformat() produces '+00:00' suffix which breaks lexicographic
        # comparison against stored naive timestamps. refs #170
        bonus_date_ts = datetime.strptime(bm["bonus_paycheck_date"], "%Y-%m-%d").isoformat()

        # Funding entries AFTER the bonus paycheck in this budget month (adds only)
        entries = conn.execute(
            """
            SELECT to_category_name AS category_name,
                   bc.category_group_name AS category_group,
                   amount AS delta
            FROM money_movements mm
            LEFT JOIN budget_categories bc
                   ON bc.id = mm.to_category_id
                  AND bc.budget_month = mm.month
            WHERE mm.month = ? AND mm.moved_at >= ? AND mm.deleted = 0
              AND mm.to_category_id IS NOT NULL
            ORDER BY mm.moved_at
            """,
            (month_str, bonus_date_ts),
        ).fetchall()

        # Aggregate by category, classify correct vs backwards
        correct_map: dict[str, dict] = {}
        backwards_map: dict[str, dict] = {}
        for e in entries:
            name = e["category_name"]
            group = e["category_group"]
            delta = e["delta"]
            if _is_excluded(group or "", name, excluded, excluded_cats):
                continue
            if _is_bonus_funded(name, group, bonus_groups, bonus_cats):
                dest = correct_map
            else:
                dest = backwards_map
            if name not in dest:
                dest[name] = {"category_name": name, "group": group, "delta": 0.0}
            dest[name]["delta"] = round(dest[name]["delta"] + delta, 2)

        correct = sorted(correct_map.values(), key=lambda x: -x["delta"])
        backwards = sorted(backwards_map.values(), key=lambda x: -x["delta"])

        # Holding: Next Month trajectory
        holding_end = _holding_balance(conn, month_str)
        holding_start = _holding_balance(conn, _prior_month(month_str))
        holding_delta = round((holding_end or 0.0) - (holding_start or 0.0), 2)
        holding_delta_warning = holding_delta < 0

        # Pull all regular-pot categories for this bonus month.
        # Do not filter by hidden: the INSERT OR REPLACE sync pattern propagates the
        # current hidden flag back to historical rows, so a paid-off loan shows hidden=1
        # even in months when it had a non-zero budgeted amount.
        target_rows = conn.execute(
            """
            SELECT name, category_group_name, budgeted
            FROM budget_categories
            WHERE budget_month = ? AND deleted = 0
            """,
            (month_str,),
        ).fetchall()

        # Structural backwards: sum of actual budgeted amounts for regular-pot categories
        # minus the expected regular monthly income. Uses budgeted (not goal_target) so
        # it reflects what was actually allocated regardless of funding source.
        regular_pot_budgeted = sum(
            (r["budgeted"] or 0.0)
            for r in target_rows
            if not _is_excluded(r["category_group_name"] or "", r["name"], excluded, excluded_cats)
            and not _is_bonus_funded(r["name"], r["category_group_name"], bonus_groups, bonus_cats)
        )
        structural_backwards = round(regular_pot_budgeted - monthly_regular, 2)

        # TBB from budget_months; when TBB = 0 the structural number is exact
        tbb_row = conn.execute("SELECT to_be_budgeted FROM budget_months WHERE month = ?", (month_str,)).fetchone()
        # Guard against NULL field value in addition to missing row. refs #155
        _tbb_val = tbb_row["to_be_budgeted"] if tbb_row else None
        tbb = round(float(_tbb_val), 2) if _tbb_val is not None else None
        structural_is_exact = tbb == 0.0

        result_months.append(
            {
                "month": month_key,
                "bonus_paycheck_date": bm["bonus_paycheck_date"],
                "bonus_paycheck_amount": bm["bonus_paycheck_amount"],
                "bonus_portion": bm["bonus_portion"],
                "holding_start": holding_start,
                "holding_end": holding_end,
                "holding_delta": holding_delta,
                "holding_delta_warning": holding_delta_warning,
                "correct": correct,
                "backwards": backwards,
                "total_correct": round(sum(c["delta"] for c in correct), 2),
                # workflow_gap: gross adds for regular-pot categories after the bonus date
                # (captures all YNAB budget moves via money_movements API)
                "workflow_gap": round(sum(b["delta"] for b in backwards), 2),
                "structural_backwards": structural_backwards,
                "tbb": tbb,
                "structural_is_exact": structural_is_exact,
                "structural_gap": {
                    "regular_pot_budgeted": round(regular_pot_budgeted, 2),
                    "monthly_regular_pay": round(monthly_regular, 2),
                    "headroom": round(monthly_regular - regular_pot_budgeted, 2),
                },
            }
        )

    return {
        "config_ok": True,
        "regular_pay": regular_pay,
        "bonus_threshold": bonus_threshold,
        "months": result_months,
    }


@router.get("/two-pot")
def get_two_pot() -> dict[str, Any]:
    """Two-pot compliance: which categories received bonus funding they shouldn't have."""
    conn = get_connection()
    try:
        init_db(conn)
        return _build_two_pot(conn)
    except sqlite3.OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database not ready. Run 'ynab sync' first. ({exc})",
        )
    finally:
        conn.close()
