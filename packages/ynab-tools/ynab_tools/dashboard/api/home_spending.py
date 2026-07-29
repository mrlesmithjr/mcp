"""GET /api/overview/home-spending - current and trailing home category spending."""

from __future__ import annotations

import os
import sqlite3
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException

from ynab_tools.config import load_env
from ynab_tools.db import get_connection

router = APIRouter(tags=["overview"])

_HOME_NAME_PREFIX_DEFAULT = "Home:"
# Categories to exclude from the home-spending roll-up (insurance is a fixed bill, not variable home spend)
_EXCLUDE_CATEGORIES_DEFAULT = {"Home: Insurance"}


def _get_home_prefix() -> str:
    load_env()
    return os.environ.get("YNAB_HOME_CATEGORY_PREFIX", _HOME_NAME_PREFIX_DEFAULT).strip() or _HOME_NAME_PREFIX_DEFAULT


def _current_month() -> str:
    t = date.today()
    return f"{t.year:04d}-{t.month:02d}-01"


def _prior_months(n: int) -> list[str]:
    """Return n months before current, newest first."""
    t = date.today()
    y, m = t.year, t.month
    result = []
    for _ in range(n):
        m -= 1
        if m == 0:
            m = 12
            y -= 1
        result.append(f"{y:04d}-{m:02d}-01")
    return result


def _home_categories(conn: sqlite3.Connection) -> list[str]:
    """Discover home category names from budget_categories using the configured prefix."""
    prefix = _get_home_prefix()
    rows = conn.execute(
        """
        SELECT DISTINCT name
        FROM budget_categories
        WHERE deleted = 0
          AND name LIKE ?
        ORDER BY name
        """,
        (f"{prefix}%",),
    ).fetchall()
    exclude = {c.replace(_HOME_NAME_PREFIX_DEFAULT, prefix) for c in _EXCLUDE_CATEGORIES_DEFAULT}
    return [r["name"] for r in rows if r["name"] not in exclude]


def _spending_for_month(
    conn: sqlite3.Connection,
    month: str,
    categories: list[str],
) -> dict[str, float]:
    """Return {category_name: abs_spent} for the given month via budget_categories.activity.

    Uses budget_categories.activity (YNAB server-computed) rather than summing
    transactions/subtransactions directly. This avoids overcounting from duplicate
    subtransaction rows that can appear as sync artifacts.
    """
    if not categories:
        return {}

    placeholders = ",".join("?" * len(categories))
    rows = conn.execute(
        f"""
        SELECT name AS cat, activity
        FROM budget_categories
        WHERE budget_month = ?
          AND deleted = 0
          AND name IN ({placeholders})
        """,
        (month, *categories),
    ).fetchall()
    # activity is negative for spending in YNAB; flip to positive.
    # Ignore positive activity (refunds/inflows) so we only report real spend.
    return {r["cat"]: round(abs(float(r["activity"] or 0)), 2) for r in rows if (r["activity"] or 0) < 0}


def _get_home_spending(conn: sqlite3.Connection) -> dict[str, Any]:
    categories = _home_categories(conn)
    current = _current_month()
    prior = _prior_months(2)  # 2 prior months

    current_by_cat = _spending_for_month(conn, current, categories)
    prior_data = []
    all_prior_totals: list[float] = []
    for m in prior:
        by_cat = _spending_for_month(conn, m, categories)
        total = round(sum(by_cat.values()), 2)
        prior_data.append({"month": m[:7], "total_dollars": total})
        all_prior_totals.append(total)

    current_total = round(sum(current_by_cat.values()), 2)

    # Include current month in 3-month average
    three_month_totals = [current_total] + all_prior_totals
    three_month_avg = round(sum(three_month_totals) / len(three_month_totals), 2) if three_month_totals else 0.0

    # Top categories by spend this month (descending)
    top_cats = sorted(
        [{"name": k, "spent": v} for k, v in current_by_cat.items() if v > 0],
        key=lambda x: -x["spent"],
    )

    result: dict[str, Any] = {
        "current_month": {
            "month": current[:7],
            "total_dollars": current_total,
            "categories": top_cats,
        },
        "prior_months": prior_data,
        "three_month_avg": three_month_avg,
        "categories_tracked": categories,
    }
    if not categories:
        has_data = conn.execute("SELECT 1 FROM budget_categories WHERE deleted = 0 LIMIT 1").fetchone() is not None
        if has_data:
            prefix = _get_home_prefix()
            result["config_hint"] = (
                f"No categories found matching prefix '{prefix}'. "
                "Set YNAB_HOME_CATEGORY_PREFIX to match your home expense category names."
            )
    return result


@router.get("/overview/home-spending")
def home_spending() -> dict[str, Any]:
    """Current and trailing 2-month home-category spending with 3-month average."""
    conn = get_connection()
    try:
        return _get_home_spending(conn)
    except sqlite3.OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database not ready. Run 'ynab sync' first. ({exc})",
        )
    finally:
        conn.close()
