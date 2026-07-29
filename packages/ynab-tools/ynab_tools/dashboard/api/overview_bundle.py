"""GET /api/overview-bundle - all Overview page data in a single request.

Calls the data functions from each individual API module. The existing
individual endpoints are preserved for use by other pages and the MCP server.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

router = APIRouter(tags=["overview"])

_log = logging.getLogger(__name__)


def _biggest_overspend_anomaly(category_name: str, current_month: str) -> dict[str, Any]:
    """Return z_score and anomaly_likely_one_time for the named category.

    Queries the prior 12 months of activity for the category (excluding the
    current month), then scores the current month via zscore_vs_history().
    """
    from ynab_tools.db import get_connection
    from ynab_tools.stats import zscore_vs_history

    conn = get_connection()
    try:
        # Build list of prior 12 month strings (oldest first)
        year = int(current_month[:4])
        mo = int(current_month[5:7])
        prior_months: list[str] = []
        y, m = year, mo
        for _ in range(12):
            m -= 1
            if m == 0:
                m = 12
                y -= 1
            prior_months.append(f"{y:04d}-{m:02d}-01")
        prior_months.reverse()  # oldest first

        placeholders = ",".join("?" for _ in prior_months)
        rows = conn.execute(
            f"""
            SELECT budget_month, activity
            FROM budget_categories
            WHERE name = ? AND budget_month IN ({placeholders})
              AND deleted = 0
              -- hidden intentionally omitted: INSERT OR REPLACE propagates the current
              -- hidden flag to all historical rows, so categories hidden after the fact
              -- (e.g. a paid-off loan) would lose their entire activity history.
            """,
            [category_name, *prior_months],
        ).fetchall()

        # refs #151: use standard sign-aware pattern - refund months (positive activity)
        # contribute 0.0, not a positive value that would inflate the z-score baseline.
        activity_by_month: dict[str, float] = {}
        for r in rows:
            v = float(r["activity"] or 0)
            activity_by_month[r["budget_month"]] = abs(v) if v < 0 else 0.0
        prior_actuals = [activity_by_month.get(m, 0.0) for m in prior_months]

        current_row = conn.execute(
            """
            SELECT activity FROM budget_categories
            WHERE name = ? AND budget_month = ? AND deleted = 0
            -- hidden intentionally omitted: see prior query
            """,
            (category_name, current_month),
        ).fetchone()
        _act = float(current_row["activity"] or 0) if current_row else 0.0
        current_actual = abs(_act) if _act < 0 else 0.0

        monthly_actuals = prior_actuals + [current_actual]
        z_score = zscore_vs_history(monthly_actuals)
    finally:
        conn.close()

    anomaly_likely_one_time = z_score is not None and z_score > 2.0
    return {
        "z_score": round(z_score, 2) if z_score is not None else None,
        "anomaly_likely_one_time": anomaly_likely_one_time,
    }


def _safe(fn, *args, **kwargs) -> Any:
    """Call fn, return None on HTTPException so a single failure does not abort the bundle."""
    try:
        return fn(*args, **kwargs)
    except HTTPException:
        return None
    except Exception as exc:
        _log.warning(
            "overview-bundle sub-call %s failed: %s",
            getattr(fn, "__name__", repr(fn)),
            exc,
            exc_info=True,
        )
        return None


@router.get("/overview-bundle")
def overview_bundle(
    month: str | None = Query(default=None, description="YYYY-MM-01 format, defaults to current month"),
) -> JSONResponse:
    """Return all data needed by the Overview page in a single response.

    Each key corresponds to one widget/section. A key is null when the
    underlying data source is unavailable (e.g. not yet synced).
    """
    from .account_health import account_health as _account_health
    from .budget_fit import budget_fit as _budget_fit
    from .calibration import calibration as _calibration
    from .churn import churn_analysis as _churn
    from .debt_trend import debt_trend as _debt_trend
    from .health_ratios import health_ratios as _health_ratios
    from .home_spending import home_spending as _home_spending
    from .income import get_income as _income
    from .months import months as _months
    from .needs_attention import needs_attention as _needs_attention
    from .net_worth_trend import net_worth_trend as _net_worth_trend
    from .overview import overview as _overview
    from .paycheck_funding_api import get_paycheck_funding as _paycheck_funding
    from .retirement import get_retirement as _retirement
    from .savings_progress import savings_progress as _savings_progress
    from .sinking_funds import sinking_funds as _sinking_funds
    from .subscriptions import get_subscriptions as _subscriptions
    from .trends import trends as _trends
    from .two_pot import get_two_pot as _two_pot
    from .upcoming import upcoming as _upcoming

    today = date.today()
    # Pass month explicitly - Query(default=None) FieldInfo objects become the
    # default when functions are called directly, not via FastAPI routing, so
    # positional/keyword None must be passed to avoid a silent TypeError.
    explicit_month: str | None = month

    overview_data = _safe(_overview, month=explicit_month)

    # Inject anomaly scoring for the biggest overspent category
    if overview_data is not None and isinstance(overview_data, dict):
        overspent = [c for c in (overview_data.get("running_hot") or []) if c.get("status") == "OVERSPENT"]
        if overspent:
            biggest = max(
                overspent,
                key=lambda c: c["spent"] - c["budgeted"] if c["budgeted"] > 0 else c["spent"],
            )
            anomaly = _safe(
                _biggest_overspend_anomaly,
                biggest["name"],
                overview_data.get("month", ""),
            )
            if anomaly:
                overview_data["biggest_overspend_z_score"] = anomaly.get("z_score")
                overview_data["biggest_overspend_anomaly_likely_one_time"] = anomaly.get(
                    "anomaly_likely_one_time", False
                )
                overview_data["biggest_overspend_name"] = biggest["name"]
            else:
                overview_data["biggest_overspend_z_score"] = None
                overview_data["biggest_overspend_anomaly_likely_one_time"] = False
                overview_data["biggest_overspend_name"] = biggest["name"]
        else:
            overview_data["biggest_overspend_z_score"] = None
            overview_data["biggest_overspend_anomaly_likely_one_time"] = False
            overview_data["biggest_overspend_name"] = None

    if explicit_month:
        hist_year = int(explicit_month[:4])
        hist_month = int(explicit_month[5:7])
        upcoming_call = _safe(_upcoming, year=hist_year, month=hist_month)
    else:
        upcoming_call = _safe(_upcoming, year=today.year, month=today.month)

    result: dict[str, Any] = {
        "overview": overview_data,
        "months": _safe(_months),
        "budget_fit": _safe(_budget_fit, months=12),
        "sinking_funds": _safe(_sinking_funds),
        "upcoming": upcoming_call,
        "subscriptions": _safe(_subscriptions),
        "trends": _safe(_trends, months=12),
        "calibration": _safe(_calibration, months=12),
        "retirement": _safe(_retirement),
        "paycheck_funding": _safe(_paycheck_funding),
        "income": _safe(_income, months=12),
        "churn": _safe(_churn, days=90),
        "two_pot": _safe(_two_pot),
        "net_worth_trend": _safe(_net_worth_trend),
        "savings_progress": _safe(_savings_progress),
        "home_spending": _safe(_home_spending),
        "account_health": _safe(_account_health),
        "health_ratios": _safe(_health_ratios),
        "debt_trend": _safe(_debt_trend),
        "needs_attention": _safe(_needs_attention),
    }

    return JSONResponse(content=result)
