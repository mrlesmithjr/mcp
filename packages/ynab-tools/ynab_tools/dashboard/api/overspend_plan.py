"""GET /api/overspend-plan - current-month overspend coverage plan with donor waterfall."""

from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ynab_tools.config import load_env
from ynab_tools.db import get_connection
from ynab_tools.reports.budget import classify_overspends
from ynab_tools.reports.paycheck_breakdown import (
    _get_breakdown_config,
    _is_bonus_funded,
    _is_excluded,
)

from ._anomaly import anomaly_likely_one_time, category_zscore_by_name
from ._common import should_skip_group

router = APIRouter(tags=["overspend-plan"])

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])(-01)?$")

# Groups that are infrastructure / not useful donor candidates
_SKIP_DONOR_GROUPS = {
    "Internal Master Category",
    "Credit Card Payments",
}

_BONUS_FUNDED_GROUPS_DEFAULT = "Savings,Retirement,Holding,Investment,Emergency,Sinking Funds,Annual Expenses"


def _get_sinking_fund_substrings() -> set[str]:
    """Return lowercase group substrings that identify sinking fund / savings donors.

    Derived from YNAB_BONUS_FUNDED_GROUPS so users only need one config key.
    Uses the same substring-match logic as paycheck_breakdown._is_bonus_funded().
    """
    load_env()
    raw = os.environ.get("YNAB_BONUS_FUNDED_GROUPS", _BONUS_FUNDED_GROUPS_DEFAULT)
    return {g.strip().lower() for g in raw.split(",") if g.strip()}


def _is_sinking_fund(group: str, sinking_substrings: set[str]) -> bool:
    group_lower = group.lower()
    return any(sub in group_lower for sub in sinking_substrings)


def _get_donor_categories(
    conn: sqlite3.Connection,
    month: str,
    total_needed: float,
) -> list[dict[str, Any]]:
    """Return prioritized donor categories with suggested transfer amounts.

    Donors are discretionary categories with positive balances, ordered by:
    1. Non-sinking-fund groups first (more liquid / lower opportunity cost)
    2. Highest available balance first

    The waterfall allocates the needed amount greedily from top donors.
    """
    sinking_substrings = _get_sinking_fund_substrings()
    bonus_groups, bonus_cats, _, _, excluded_groups, excluded_cats = _get_breakdown_config()

    rows = conn.execute(
        """
        SELECT name, category_group_name, balance, budgeted, activity
        FROM budget_categories
        WHERE budget_month = ?
          AND deleted = 0 AND hidden = 0
          AND balance > 0
          AND budgeted > 0
        ORDER BY
            CASE WHEN category_group_name IN (
                'Internal Master Category', 'Credit Card Payments'
            ) THEN 1 ELSE 0 END,
            balance DESC
        """,
        (month,),
    ).fetchall()

    donors = []
    remaining = total_needed

    for row in rows:
        group = row["category_group_name"] or ""
        if group in _SKIP_DONOR_GROUPS:
            continue
        if should_skip_group(group):
            continue
        if _is_excluded(group, row["name"], excluded_groups, excluded_cats):
            continue
        if _is_bonus_funded(row["name"], group, bonus_groups, bonus_cats):
            continue

        balance = float(row["balance"] or 0)
        if balance <= 0:
            continue

        is_sinking = _is_sinking_fund(group, sinking_substrings)
        suggested = min(balance, max(remaining, 0))

        donors.append(
            {
                "name": row["name"],
                "group": group,
                "available": round(balance, 2),
                "suggested_transfer": round(suggested, 2),
                "is_sinking_fund": is_sinking,
            }
        )

        remaining -= suggested
        if remaining <= 0:
            break

    return donors


def _build_overspend_plan(conn: sqlite3.Connection, month: str) -> dict[str, Any]:
    month_label = datetime.strptime(month[:7], "%Y-%m").strftime("%B %Y")

    classified = classify_overspends(conn, month)

    # Enrich each overspend with z-score and coverage_suggestion.
    # Split into one-time (holding) vs structural (waterfall) before running the donor waterfall.
    one_time_items: list[dict[str, Any]] = []
    waterfall_items: list[dict[str, Any]] = []

    for r in classified:
        z = category_zscore_by_name(conn, r["name"], month)
        if z is None:
            coverage = None
        elif anomaly_likely_one_time(z):
            coverage = "holding"
        else:
            coverage = "waterfall"

        item = {
            "name": r["name"],
            "balance": round(r["balance"], 2),
            "budgeted": round(r["budgeted"], 2),
            "activity": round(r["activity"], 2),
            "overspent": round(abs(r["balance"]), 2),
            "classification": r["classification"],
            "detail": r["detail"],
            "z_score": round(z, 2) if z is not None else None,
            "coverage_suggestion": coverage,
        }

        if coverage == "holding":
            one_time_items.append(item)
        else:
            waterfall_items.append(item)

    # Donor waterfall only covers structural/ambiguous overspends
    waterfall_total = round(sum(i["overspent"] for i in waterfall_items), 2)
    total_overspent = round(sum(abs(r["balance"]) for r in classified), 2) if classified else 0.0

    donors = _get_donor_categories(conn, month, waterfall_total) if waterfall_items else []

    total_coverable = round(sum(d["suggested_transfer"] for d in donors), 2)
    gap = round(max(0.0, waterfall_total - total_coverable), 2)
    fully_coverable = gap == 0.0 and waterfall_total > 0.0

    # Classification breakdown (across all overspends for the legend)
    classification_summary: dict[str, dict[str, Any]] = {}
    for r in classified:
        cls = r["classification"]
        if cls not in classification_summary:
            classification_summary[cls] = {"count": 0, "total": 0.0}
        classification_summary[cls]["count"] += 1
        classification_summary[cls]["total"] = round(classification_summary[cls]["total"] + abs(r["balance"]), 2)

    return {
        "month": month[:7],
        "month_label": month_label,
        "overspent_count": len(classified),
        "total_overspent": total_overspent,
        "total_coverable": total_coverable,
        "gap": gap,
        "fully_coverable": fully_coverable,
        "overspent_categories": waterfall_items,
        "one_time_overspends": one_time_items,
        "donors": donors,
        "classification_summary": classification_summary,
    }


@router.get("/overspend-plan")
def overspend_plan(
    month: str | None = Query(
        default=None,
        description="YYYY-MM or YYYY-MM-01 format. Defaults to current month.",
    ),
) -> dict[str, Any]:
    """Current-month overspend coverage plan.

    Returns overspent categories with root-cause classification (STRUCTURAL,
    SEASONAL, ONE-OFF, TIMING FLOAT), a prioritized donor waterfall showing
    which categories to pull from and how much, and a coverage summary.
    """
    if month is not None and not _MONTH_RE.match(month):
        raise HTTPException(status_code=422, detail="month must be YYYY-MM or YYYY-MM-01 format")

    conn = get_connection()
    try:
        if month:
            target = month[:7] + "-01"
        else:
            from ynab_tools.db import current_month

            target = current_month()

        return _build_overspend_plan(conn, target)
    except sqlite3.OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database not ready. Run 'ynab sync' first. ({exc})",
        ) from exc
    finally:
        conn.close()
