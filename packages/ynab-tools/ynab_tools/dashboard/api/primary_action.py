"""GET /api/overview/primary-action - synthesized next-action recommendation.

Priority evaluation order (first match wins):
1. unapproved    - unapproved on-budget transactions exist
2. fund_rta      - RTA > 0 and non-excluded categories have goal_under_funded > 0
3. fund_goals    - RTA > 0 and any sinking-fund goal is underfunded
4. structural_overspend - categories spent more than budgeted with no RTA to cover
5. all_good      - nothing actionable

refs #187
"""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException

from ynab_tools.db import get_connection

from ._common import should_skip_group

router = APIRouter(tags=["overview"])


def _current_month() -> str:
    t = date.today()
    return f"{t.year:04d}-{t.month:02d}-01"


def _get_primary_action(conn: sqlite3.Connection) -> dict[str, Any]:
    month = _current_month()

    # ------------------------------------------------------------------
    # Breakdown config - used by checks 2, 3, and 4
    # _get_breakdown_config() returns a 6-tuple, not a dict.
    # ------------------------------------------------------------------
    try:
        from ynab_tools.reports.paycheck_breakdown import (
            _get_breakdown_config,
            _is_bonus_funded,
            _is_excluded,
        )

        cfg_tuple = _get_breakdown_config()
        bonus_groups = cfg_tuple[0]
        bonus_cats = cfg_tuple[1]
        excluded_groups = cfg_tuple[4]
        excluded_cats = cfg_tuple[5]
    except Exception:
        bonus_groups = set()
        bonus_cats = set()
        excluded_groups = set()
        excluded_cats = set()

        def _is_bonus_funded(nm, grp, bg, bc):  # noqa: E731
            return False

        def _is_excluded(grp, nm, eg, ec):  # noqa: E731
            return False

    # ------------------------------------------------------------------
    # 1. Unapproved on-budget transactions
    # ------------------------------------------------------------------
    unapproved_count = conn.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM transactions t
        JOIN accounts a ON a.id = t.account_id
        WHERE t.approved = 0
          AND t.deleted = 0
          AND a.on_budget = 1
        """,
    ).fetchone()["cnt"]

    if unapproved_count > 0:
        noun = "transaction" if unapproved_count == 1 else "transactions"
        return {
            "priority": "unapproved",
            "message": f"You have {unapproved_count} unapproved {noun} waiting for review.",
            "detail": "Review and approve transactions to keep your budget accurate.",
            "action_path": "/unapproved",
        }

    # ------------------------------------------------------------------
    # RTA - used by checks 2, 3, and 4
    # ------------------------------------------------------------------
    bm_row = conn.execute(
        "SELECT to_be_budgeted FROM budget_months WHERE month = ?",
        (month,),
    ).fetchone()
    rta = float(bm_row["to_be_budgeted"] or 0) if bm_row else 0.0

    # ------------------------------------------------------------------
    # 2. fund_rta - RTA > 0 and non-excluded, non-bonus regular-pot
    #    categories have goal_under_funded > 0
    # ------------------------------------------------------------------
    if rta > 0:
        underfunded_rows = conn.execute(
            """
            SELECT name, category_group_name, goal_under_funded
            FROM budget_categories
            WHERE budget_month = ?
              AND goal_under_funded > 0
              AND deleted = 0
              AND hidden = 0
            """,
            (month,),
        ).fetchall()

        regular_underfunded: list[dict] = []
        for row in underfunded_rows:
            grp = row["category_group_name"] or ""
            nm = row["name"] or ""
            if should_skip_group(grp):
                continue
            if _is_excluded(grp, nm, excluded_groups, excluded_cats):
                continue
            if _is_bonus_funded(nm, grp, bonus_groups, bonus_cats):
                continue
            regular_underfunded.append({"name": nm, "group": grp, "needed": float(row["goal_under_funded"] or 0)})

        if regular_underfunded:
            total_needed = sum(r["needed"] for r in regular_underfunded)
            count = len(regular_underfunded)
            cat_noun = "category needs" if count == 1 else "categories need"
            return {
                "priority": "fund_rta",
                "message": (f"You have {formatcur(rta)} ready to assign and {count} essential {cat_noun} funding."),
                "detail": f"{formatcur(rta)} RTA · {formatcur(total_needed)} needed across T1-T3",
                "action_path": "/paycheck-funding",
            }

    # ------------------------------------------------------------------
    # 3. fund_goals - RTA > 0 and sinking fund goals underfunded
    # ------------------------------------------------------------------
    if rta > 0:
        goal_rows = conn.execute(
            """
            SELECT name, category_group_name, goal_under_funded
            FROM budget_categories
            WHERE budget_month = ?
              AND goal_under_funded > 0
              AND deleted = 0
              AND hidden = 0
              AND goal_type IS NOT NULL
              AND goal_type != ''
            """,
            (month,),
        ).fetchall()

        underfunded_goals: list[dict] = []
        for row in goal_rows:
            grp = row["category_group_name"] or ""
            nm = row["name"] or ""
            if should_skip_group(grp):
                continue
            if _is_excluded(grp, nm, excluded_groups, excluded_cats):
                continue
            if _is_bonus_funded(nm, grp, bonus_groups, bonus_cats):
                continue
            underfunded_goals.append({"name": nm, "group": grp, "needed": float(row["goal_under_funded"] or 0)})

        if underfunded_goals:
            total_needed = sum(r["needed"] for r in underfunded_goals)
            count = len(underfunded_goals)
            goal_noun = "goal needs" if count == 1 else "goals need"
            plural_s = "" if count == 1 else "s"
            return {
                "priority": "fund_goals",
                "message": (f"You have {formatcur(rta)} ready to assign and {count} {goal_noun} funding."),
                "detail": f"{count} underfunded goal{plural_s} · {formatcur(total_needed)} needed",
                "action_path": "/sinking-funds",
            }

    # ------------------------------------------------------------------
    # 4. structural_overspend - categories overspent and RTA <= 0
    # ------------------------------------------------------------------
    overspent_rows = conn.execute(
        """
        SELECT name, category_group_name, balance
        FROM budget_categories
        WHERE budget_month = ?
          AND balance < -0.01
          AND deleted = 0
          AND hidden = 0
        """,
        (month,),
    ).fetchall()

    structural_overspend: list[dict] = []
    for row in overspent_rows:
        grp = row["category_group_name"] or ""
        nm = row["name"] or ""
        if should_skip_group(grp):
            continue
        if _is_excluded(grp, nm, excluded_groups, excluded_cats):
            continue
        if _is_bonus_funded(nm, grp, bonus_groups, bonus_cats):
            continue
        structural_overspend.append({"name": nm, "overspent": abs(float(row["balance"] or 0))})

    if structural_overspend:
        total_overspent = sum(r["overspent"] for r in structural_overspend)
        count = len(structural_overspend)
        return {
            "priority": "structural_overspend",
            "message": f"{count} categor{'y is' if count == 1 else 'ies are'} overspent with no RTA to cover.",
            "detail": f"{formatcur(total_overspent)} total overspend · review your overspend plan",
            "action_path": "/overspend-plan",
        }

    # ------------------------------------------------------------------
    # 5. all_good
    # ------------------------------------------------------------------
    return {
        "priority": "all_good",
        "message": "Budget is in good shape.",
        "detail": "No immediate action needed.",
        "action_path": None,
    }


def formatcur(amount: float) -> str:
    """Format a dollar amount as $1,234 (no cents)."""
    return f"${amount:,.0f}"


@router.get("/overview/primary-action")
def primary_action() -> dict[str, Any]:
    """Synthesized next-action recommendation for the Overview banner."""
    conn = get_connection()
    try:
        return _get_primary_action(conn)
    except sqlite3.OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database not ready. Run 'ynab sync' first. ({exc})",
        )
    finally:
        conn.close()
