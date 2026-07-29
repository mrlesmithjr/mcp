"""Shared statistical helpers for spending analysis."""

from __future__ import annotations

import math
import os
import sqlite3

import numpy as np


def spending_stats(amounts: list[float]) -> dict:
    """Calculate spending statistics and variance signals from a list of monthly amounts.

    Used by both the funding-status report and the calibration API to ensure
    consistent CV thresholds, lumpy detection, and pattern labeling across all
    target-recommendation surfaces.

    Args:
        amounts: Monthly spend amounts (positive floats; zero = no activity that month).

    Returns dict with:
        avg           -- for lumpy categories: full average including zero months (amortized cost);
                         for non-lumpy: average of nonzero months only
        trimmed_avg   -- trimmed mean of nonzero months, dropping min/max when 4+ months
                         available; equals full_avg for lumpy categories so amortized cost
                         drives recommendations rather than the active-month peak
        std_dev       -- sample std dev of nonzero months
        cv            -- coefficient of variation (std_dev / nonzero_avg); computed on
                         nonzero months only so infrequent-spend categories aren't penalized
        months        -- number of nonzero months (data points with actual spend)
        total_months  -- total months in sample (including zero months)
        lumpy         -- True when >25% of months are zero AND >=3 nonzero months exist
        pattern       -- "consistent" | "moderate_variance" | "lumpy" | "high_variance"
                         | "insufficient_data"
    """
    if not amounts:
        return {
            "avg": 0.0,
            "trimmed_avg": 0.0,
            "std_dev": 0.0,
            "cv": 0.0,
            "months": 0,
            "total_months": 0,
            "lumpy": False,
            "pattern": "insufficient_data",
        }

    total_months = len(amounts)
    zero_months = sum(1 for a in amounts if a == 0)
    nonzero = [a for a in amounts if a > 0]

    full_avg = sum(amounts) / total_months

    # Lumpy: >25% zero-spend months with at least 3 nonzero months
    lumpy = zero_months > total_months * 0.25 and len(nonzero) >= 3

    if lumpy:
        # For lumpy categories, use full average (zeros are real amortized cost, not gaps)
        avg = full_avg
        trimmed_avg = full_avg
    else:
        if not nonzero:
            return {
                "avg": 0.0,
                "trimmed_avg": 0.0,
                "std_dev": 0.0,
                "cv": 0.0,
                "months": 0,
                "total_months": total_months,
                "lumpy": False,
                "pattern": "insufficient_data",
            }
        avg = sum(nonzero) / len(nonzero)
        if len(nonzero) >= 4:
            trimmed = sorted(nonzero)[1:-1]
            trimmed_avg = sum(trimmed) / len(trimmed)
        else:
            trimmed_avg = avg

    # Std dev and CV on nonzero amounts only
    n = len(nonzero)
    if n >= 2:
        nz_avg = sum(nonzero) / n
        variance = sum((x - nz_avg) ** 2 for x in nonzero) / (n - 1)
        std_dev = variance**0.5
        cv = std_dev / nz_avg if nz_avg > 0 else 0.0
    else:
        std_dev = 0.0
        cv = 0.0

    # Pattern label: lumpy takes priority over high_variance since they require
    # different handling (lumpy = use amortized avg; high_variance = no recommendation)
    if lumpy:
        pattern = "lumpy"
    elif cv > 0.6:
        pattern = "high_variance"
    elif cv > 0.35:
        pattern = "moderate_variance"
    else:
        pattern = "consistent"

    return {
        "avg": round(avg, 2),
        "trimmed_avg": round(trimmed_avg, 2),
        "std_dev": round(std_dev, 2),
        "cv": round(cv, 3),
        "months": n,
        "total_months": total_months,
        "lumpy": lumpy,
        "pattern": pattern,
    }


def recommend_target(stats: dict) -> tuple[float | None, str]:
    """Generate a budget target recommendation from spending_stats output.

    Returns (recommended_amount, confidence_note).
    recommended_amount is None when data is insufficient or too volatile.
    """
    if stats["months"] < 3:
        return None, "insufficient data"

    if stats["pattern"] == "high_variance":
        return None, "high variance"

    raw = stats["trimmed_avg"]
    rounded = round_up_5(raw)

    if stats["lumpy"]:
        return rounded, "lumpy spending"
    if stats["pattern"] == "moderate_variance":
        return rounded, "moderate variance"
    return rounded, ""


def round_up_5(value: float) -> float:
    """Round up to the nearest $5."""
    return float(math.ceil(value / 5) * 5)


# Private alias kept for internal use
_round_up_5 = round_up_5


def zscore_vs_history(monthly_actuals: list[float]) -> float | None:
    """Z-score of the most recent month vs its prior months.

    Uses sample std dev (ddof=1) so that small baselines are not artificially
    narrowed. Returns None when fewer than 5 data points are provided (need at
    least 4 prior months plus the current month to form a meaningful baseline).
    Returns 0.0 when the baseline std dev is 0 (perfectly consistent history).

    Args:
        monthly_actuals: Spending amounts ordered oldest-first. The last element
                         is the month being scored; all preceding elements are
                         the baseline.
    """
    if len(monthly_actuals) < 5:
        return None

    current = monthly_actuals[-1]
    history = monthly_actuals[:-1]

    arr = np.array(history, dtype=float)
    mean = float(np.mean(arr))
    std = float(np.std(arr, ddof=1))

    if std == 0.0:
        return 0.0

    return float((current - mean) / std)


_INFRA_GROUPS = {"Credit Card Payments", "Internal Master Category"}


def _user_excluded_groups() -> frozenset[str]:
    raw = os.environ.get("YNAB_EXCLUDED_GROUPS", "")
    return frozenset(g.strip() for g in raw.split(",") if g.strip())


def should_skip_group(group: str) -> bool:
    """Return True for infrastructure or user-configured groups to exclude from spending analysis."""
    if group in _INFRA_GROUPS:
        return True
    if group in _user_excluded_groups():
        return True
    g = group.lower()
    return "income" in g or "holding" in g


def strip_emoji_prefix(s: str) -> str:
    """Strip leading non-word characters (emoji, symbols) from a string."""
    import re

    return re.sub(r"^[^\w]+", "", s, flags=re.UNICODE).strip()


def parse_category_input(name: str) -> tuple[str, str | None]:
    """Parse 'Group: Category' or 'Category (Group)' into (category_name, group_filter).

    Paren format is tried first so category names containing colons (e.g. "Birthday: Kim")
    are not mis-parsed when wrapped with a group qualifier.
    """
    import re

    paren_match = re.match(r"^(.+?)\s+\((.+)\)$", name)
    if paren_match:
        return paren_match.group(1).strip(), paren_match.group(2).strip()

    colon_match = re.match(r"^(.+?):\s*(.+)$", name)
    if colon_match:
        return colon_match.group(2).strip(), colon_match.group(1).strip()

    return name, None


def _build_prior_months(current_month: str, n: int = 12) -> list[str]:
    """Return list of N prior month strings (YYYY-MM-01) before current_month, oldest first."""
    year = int(current_month[:4])
    mo = int(current_month[5:7])
    prior_months: list[str] = []
    y, m = year, mo
    for _ in range(n):
        m -= 1
        if m == 0:
            m = 12
            y -= 1
        prior_months.append(f"{y:04d}-{m:02d}-01")
    prior_months.reverse()
    return prior_months


def category_zscore(conn: sqlite3.Connection, category_id: str, current_month: str) -> float | None:
    """Return z-score of current_month's activity for category_id vs 12 prior months.

    Args:
        conn: Active SQLite connection.
        category_id: The budget_categories.id for the category.
        current_month: YYYY-MM-01 format month to score.

    Returns:
        Z-score (float) or None when fewer than 5 data points are available.
    """
    prior_months = _build_prior_months(current_month)
    placeholders = ",".join("?" for _ in prior_months)
    rows = conn.execute(
        f"""
        SELECT budget_month, activity
        FROM budget_categories
        WHERE id = ? AND budget_month IN ({placeholders})
          AND deleted = 0
          -- hidden intentionally omitted: INSERT OR REPLACE propagates the current
          -- hidden flag to all historical rows, so categories hidden after the fact
          -- (e.g. a paid-off loan) would lose their entire activity history.
        """,
        [category_id, *prior_months],
    ).fetchall()

    activity_by_month: dict[str, float] = {r["budget_month"]: abs(float(r["activity"] or 0)) for r in rows}
    prior_actuals = [activity_by_month.get(m, 0.0) for m in prior_months]

    current_row = conn.execute(
        """
        SELECT activity FROM budget_categories
        WHERE id = ? AND budget_month = ? AND deleted = 0
        -- hidden intentionally omitted: see prior query
        """,
        (category_id, current_month),
    ).fetchone()
    current_actual = abs(float(current_row["activity"] or 0)) if current_row else 0.0

    return zscore_vs_history(prior_actuals + [current_actual])


def category_zscore_by_name(conn: sqlite3.Connection, category_name: str, current_month: str) -> float | None:
    """Return z-score of current_month's activity for a category looked up by name.

    Used by CLI reports and dashboard endpoints that work with category names
    rather than IDs.
    """
    prior_months = _build_prior_months(current_month)
    placeholders = ",".join("?" for _ in prior_months)
    rows = conn.execute(
        f"""
        SELECT budget_month, activity
        FROM budget_categories
        WHERE name = ? AND budget_month IN ({placeholders})
          AND deleted = 0
          -- hidden intentionally omitted: see category_zscore for rationale
        """,
        [category_name, *prior_months],
    ).fetchall()

    activity_by_month: dict[str, float] = {r["budget_month"]: abs(float(r["activity"] or 0)) for r in rows}
    prior_actuals = [activity_by_month.get(m, 0.0) for m in prior_months]

    current_row = conn.execute(
        """
        SELECT activity FROM budget_categories
        WHERE name = ? AND budget_month = ? AND deleted = 0
        -- hidden intentionally omitted: see category_zscore for rationale
        """,
        (category_name, current_month),
    ).fetchone()
    current_actual = abs(float(current_row["activity"] or 0)) if current_row else 0.0

    return zscore_vs_history(prior_actuals + [current_actual])


def anomaly_label(z_score: float | None) -> str | None:
    """Return a human-readable anomaly label for a z-score, or None.

    Returns:
        "likely one-time" when z_score > 2.0
        "worth watching" when 1.0 < z_score <= 2.0
        None otherwise (including when z_score is None or below 1.0)
    """
    if z_score is None:
        return None
    if z_score > 2.0:
        return "likely one-time"
    if z_score > 1.0:
        return "worth watching"
    return None


def anomaly_likely_one_time(z_score: float | None) -> bool:
    """Return True when z_score > 2.0 (spend is a statistical outlier vs history)."""
    return z_score is not None and z_score > 2.0
