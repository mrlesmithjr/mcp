"""Shared helpers for dashboard API endpoints."""

from __future__ import annotations

import calendar
from datetime import date

from fastapi import HTTPException

from ynab_tools.config import require_credentials
from ynab_tools.stats import should_skip_group  # noqa: F401


def get_credentials() -> tuple[str, str]:
    """Return (token, plan_id), raising HTTP 503 instead of sys.exit() on missing creds."""
    try:
        return require_credentials()
    except SystemExit:
        raise HTTPException(
            status_code=503,
            detail="YNAB credentials not configured. Run 'ynab configure' first.",
        )


def days_elapsed_in_month(year: int, month: int) -> tuple[int, int]:
    """Return (days_elapsed, days_in_month) for pace calculations.

    For the current month, days_elapsed is today's day number (minimum 1).
    For past months, days_elapsed equals days_in_month (full month completed).
    """
    today = date.today()
    days_in_month = calendar.monthrange(year, month)[1]
    if year == today.year and month == today.month:
        return max(today.day, 1), days_in_month
    return days_in_month, days_in_month


def month_starts(n: int, complete_only: bool = False) -> list[str]:
    """Return n month start strings (YYYY-MM-01), newest first.

    Args:
        n: Number of months to return.
        complete_only: If True, start from the most recent *complete* month (skip the
                       current in-progress month). Used by calibration where partial-month
                       data would skew averages. If False, include the current month.
    """
    today = date.today()
    y, m = today.year, today.month
    if complete_only:
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    months = []
    for _ in range(n):
        months.append(f"{y:04d}-{m:02d}-01")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return months
