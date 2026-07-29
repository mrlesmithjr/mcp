"""GET /api/overview/health-ratios - budget health ratios vs Money Guy benchmarks."""

from __future__ import annotations

import os
import sqlite3
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException

from ynab_tools.config import load_env
from ynab_tools.db import get_connection
from ynab_tools.stats import should_skip_group

router = APIRouter(tags=["overview"])


def _current_month() -> str:
    t = date.today()
    return f"{t.year:04d}-{t.month:02d}-01"


def _sum_budgeted_for_patterns(conn: sqlite3.Connection, month: str, patterns: list[str]) -> float:
    """Sum budgeted amounts for categories whose names contain any of the given patterns (case-insensitive)."""
    total = 0.0
    rows = conn.execute(
        """
        SELECT name, budgeted, category_group_name
        FROM budget_categories
        WHERE budget_month = ? AND deleted = 0 AND hidden = 0
        """,
        (month,),
    ).fetchall()
    for row in rows:
        if should_skip_group(row["category_group_name"] or ""):
            continue
        cat_name = (row["name"] or "").lower()
        for pat in patterns:
            if pat.lower() in cat_name:
                total += float(row["budgeted"] or 0)
                break
    return total


def _get_health_ratios(conn: sqlite3.Connection) -> dict[str, Any]:
    load_env()

    gross_salary_str = os.environ.get("YNAB_GROSS_SALARY")
    if not gross_salary_str:
        return {"configured": False}

    try:
        annual_base = float(gross_salary_str)
    except ValueError:
        return {"configured": False}

    monthly_base = annual_base / 12

    gross_ote_str = os.environ.get("YNAB_GROSS_OTE")
    monthly_ote: float | None = None
    if gross_ote_str:
        try:
            monthly_ote = float(gross_ote_str) / 12
        except ValueError:
            pass

    retirement_annual_str = os.environ.get("YNAB_RETIREMENT_ANNUAL")
    monthly_retirement: float | None = None
    if retirement_annual_str:
        try:
            monthly_retirement = float(retirement_annual_str) / 12
        except ValueError:
            pass

    month = _current_month()

    housing_cats = [c.strip() for c in os.environ.get("YNAB_RATIO_HOUSING", "Mortgage & Rent").split(",")]
    auto_cats = [c.strip() for c in os.environ.get("YNAB_RATIO_AUTO", "Auto Loan").split(",")]
    debt_cats = [
        c.strip() for c in os.environ.get("YNAB_RATIO_DEBT", "Mortgage & Rent,Auto Loan,Student Loan").split(",")
    ]

    housing = _sum_budgeted_for_patterns(conn, month, housing_cats)
    auto_loans = _sum_budgeted_for_patterns(conn, month, auto_cats)
    debt_service = _sum_budgeted_for_patterns(conn, month, debt_cats)

    def _make_ratio(label: str, monthly_amount: float, guideline_pct: float, direction: str) -> dict[str, Any]:
        actual_base_pct = round((monthly_amount / monthly_base) * 100, 1) if monthly_base else 0.0
        actual_ote_pct = round((monthly_amount / monthly_ote) * 100, 1) if monthly_ote else None

        if direction == "below":
            if actual_base_pct < guideline_pct:
                status = "ok"
            elif actual_base_pct < guideline_pct * 1.1:
                status = "warning"
            else:
                status = "over"
        else:
            # "above" means good if we meet or exceed the guideline
            if actual_base_pct >= guideline_pct:
                status = "ok"
            elif actual_base_pct >= guideline_pct * 0.8:
                status = "warning"
            else:
                status = "under"

        entry: dict[str, Any] = {
            "label": label,
            "monthly_amount": round(monthly_amount, 2),
            "actual_pct": actual_base_pct,
            "actual_ote_pct": actual_ote_pct,
            "guideline_pct": guideline_pct,
            "direction": direction,
            "status": status,
        }
        return entry

    ratios = [
        _make_ratio("Housing", housing, 25.0, "below"),
        _make_ratio("Auto (Loans)", auto_loans, 8.0, "below"),
        _make_ratio("Debt Service", debt_service, 36.0, "below"),
    ]
    if monthly_retirement is not None:
        ratios.append(_make_ratio("Retirement", monthly_retirement, 25.0, "above"))

    return {
        "configured": True,
        "gross_salary": annual_base,
        "gross_ote": float(gross_ote_str) if gross_ote_str else None,
        "monthly_base": round(monthly_base, 2),
        "monthly_ote": round(monthly_ote, 2) if monthly_ote else None,
        "month": month,
        "ratios": ratios,
    }


@router.get("/overview/health-ratios")
def health_ratios() -> dict[str, Any]:
    """Budget health ratios vs Money Guy guidelines (Housing, Auto, Debt, Retirement)."""
    conn = get_connection()
    try:
        return _get_health_ratios(conn)
    except sqlite3.OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database not ready. Run 'ynab sync' first. ({exc})",
        )
    finally:
        conn.close()
