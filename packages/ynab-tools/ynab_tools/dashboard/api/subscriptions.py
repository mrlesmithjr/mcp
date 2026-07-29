"""Subscriptions analytics: recurring charges in subscription categories."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import statistics
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException

from ynab_tools.config import RULES_FILE
from ynab_tools.db import get_connection

router = APIRouter(tags=["subscriptions"])

_DETECTION_MONTHS = 24


def _subscription_categories() -> tuple[str, ...]:
    raw = os.environ.get(
        "YNAB_SUBSCRIPTION_CATEGORIES",
        "Subscriptions (Personal),Subscriptions (Business)",
    )
    return tuple(c.strip() for c in raw.split(",") if c.strip())


def _non_subscription_payees() -> frozenset[str]:
    raw = os.environ.get("YNAB_NON_SUBSCRIPTION_PAYEES", "")
    return frozenset(p.strip() for p in raw.split(",") if p.strip())


def _payee_prefix_overrides() -> list[tuple[str, str]]:
    # CANVA* is a near-universal bank mangling for Canva.com charges.
    raw = os.environ.get("YNAB_PAYEE_PREFIX_OVERRIDES", "CANVA*|Canva")
    result = []
    for entry in raw.split(","):
        if "|" in entry:
            prefix, canonical = entry.split("|", 1)
            result.append((prefix.strip(), canonical.strip()))
    return result


def _payee_name_overrides() -> dict[str, str]:
    # Universal default: YNAB's own truncated import payee name.
    base: dict[str, str] = {"YOU NEED A BUDGET LLC HTTPSWWW.Y": "YNAB"}
    raw = os.environ.get("YNAB_PAYEE_NAME_OVERRIDES", "")
    for entry in raw.split(","):
        if "|" in entry:
            raw_name, canonical = entry.split("|", 1)
            base[raw_name.strip()] = canonical.strip()
    return base


# Mtime-aware cache: (mtime, rules). Reloads when the file changes on disk
# so payee rule updates via `ynab payee fix` are visible without a server restart.
# refs #156
_payee_rules_cache: tuple[float, list[tuple[re.Pattern, str]]] | None = None


def _load_payee_rules() -> list[tuple[re.Pattern, str]]:
    """Return compiled (pattern, correct_payee) pairs from payee_rules.json.

    Reloads from disk whenever the file modification time changes.
    """
    global _payee_rules_cache
    try:
        mtime = RULES_FILE.stat().st_mtime
    except FileNotFoundError:
        return []
    if _payee_rules_cache is not None and _payee_rules_cache[0] == mtime:
        return _payee_rules_cache[1]
    try:
        data = json.loads(RULES_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    rules = [
        (re.compile(r["import_pattern"], re.IGNORECASE), r["correct_payee"])
        for r in data.get("rules", [])
        if "import_pattern" in r and "correct_payee" in r
    ]
    _payee_rules_cache = (mtime, rules)
    return rules


def _canonical_payee(payee_name: str | None, import_original: str | None) -> str:
    """Return the canonical payee name, normalizing via rules where possible."""
    if import_original:
        for pattern, correct_payee in _load_payee_rules():
            if pattern.match(import_original):
                return correct_payee
    raw = payee_name or "Unknown"
    return _payee_name_overrides().get(raw, raw)


# (min_days, max_days, label, months_per_period)
_FREQ_BANDS = [
    (330, 400, "annual", 12),
    (85, 100, "quarterly", 3),
    (25, 35, "monthly", 1),
]

# How many days without a charge before a subscription is considered gone.
# Keyed by detected frequency.
_HIDE_AFTER: dict[str, int] = {
    "monthly": 90,
    "quarterly": 180,
    "annual": 425,
    "irregular": 400,
}

# Multiplier applied to median_interval to derive threshold_days (beyond which
# status becomes "check"). Annual uses a tighter 1.1 to flag missed renewals
# quickly; monthly keeps the looser 1.5 to tolerate billing-date drift.
_STATUS_MULTIPLIER: dict[str, float] = {
    "monthly": 1.5,
    "quarterly": 1.3,
    "annual": 1.1,
    "irregular": 1.5,
}


def _classify_frequency(median_interval: float) -> tuple[str, int]:
    for lo, hi, label, months in _FREQ_BANDS:
        if lo <= median_interval <= hi:
            return label, months
    return "irregular", 1


def _collapse_charges(txns: list[dict]) -> list[dict]:
    """Merge charges within 3 days into a single entry to handle split billing."""
    if not txns:
        return []
    sorted_txns = sorted(txns, key=lambda t: t["date"])
    collapsed: list[dict] = []
    group = [sorted_txns[0]]
    for txn in sorted_txns[1:]:
        gap = (date.fromisoformat(txn["date"]) - date.fromisoformat(group[-1]["date"])).days
        if gap <= 3:
            group.append(txn)
        else:
            collapsed.append({**group[0], "amount": sum(t["amount"] for t in group)})
            group = [txn]
    collapsed.append({**group[0], "amount": sum(t["amount"] for t in group)})
    return collapsed


def _build_subscriptions(conn: sqlite3.Connection) -> dict[str, Any]:
    categories = _subscription_categories()
    placeholders = ", ".join("?" * len(categories))
    rows = conn.execute(
        f"""
        SELECT payee_name, import_payee_name_original, category_name, date, ABS(amount) AS amount
        FROM transactions
        WHERE deleted = 0
          AND amount < 0
          AND transfer_account_id IS NULL
          AND category_name IN ({placeholders})
          AND date >= date('now', '-{_DETECTION_MONTHS} months')
        ORDER BY payee_name, date
        """,
        categories,
    ).fetchall()

    # Group by original payee_name. Also track the first available import_original
    # per payee for canonical name lookup after frequency detection.
    subscription_cat_set = set(categories)
    prefix_overrides = _payee_prefix_overrides()
    non_subscription = _non_subscription_payees()
    by_payee: dict[str, list[dict]] = {}
    payee_import_orig: dict[str, str | None] = {}
    for row in rows:
        # Enforce category scope: skip any transaction not in configured categories.
        # Belt-and-suspenders guard against category renames or stale local data.
        if (row["category_name"] or "") not in subscription_cat_set:
            continue
        payee = row["payee_name"] or "Unknown"
        for prefix, canonical in prefix_overrides:
            if payee.upper().startswith(prefix.upper()):
                payee = canonical
                break
        if payee in non_subscription:
            continue
        by_payee.setdefault(payee, []).append(dict(row))
        if payee not in payee_import_orig or payee_import_orig[payee] is None:
            payee_import_orig[payee] = row["import_payee_name_original"]

    today = date.today()
    subscriptions = []

    for payee, txns in by_payee.items():
        collapsed = _collapse_charges(txns)

        # Use last 3 collapsed charges for median amount to reflect current pricing.
        median_amt = statistics.median([t["amount"] for t in collapsed[-3:]])

        dates = sorted(t["date"] for t in collapsed)
        date_objs = [date.fromisoformat(d) for d in dates]
        intervals = [(date_objs[i + 1] - date_objs[i]).days for i in range(len(date_objs) - 1)]

        if intervals:
            median_interval = statistics.median(intervals)
            frequency, months_per_period = _classify_frequency(median_interval)
            if frequency == "irregular":
                annual_cost_est = median_amt * (365.0 / median_interval)
                monthly_cost = annual_cost_est / 12
            else:
                monthly_cost = median_amt / months_per_period
            next_expected = (date_objs[-1] + timedelta(days=round(median_interval))).isoformat()
            threshold_days = round(median_interval * _STATUS_MULTIPLIER.get(frequency, 1.5))

            # Detect monthly-to-annual billing switch: last charge >=8x the median of
            # prior charges indicates the user switched from monthly to an annual plan.
            if frequency != "annual" and len(collapsed) >= 3:
                prior_amounts = [t["amount"] for t in collapsed[:-1]]
                prior_median = statistics.median(prior_amounts)
                if prior_median > 0 and collapsed[-1]["amount"] / prior_median >= 8:
                    frequency = "annual"
                    monthly_cost = collapsed[-1]["amount"] / 12
                    next_expected = (date_objs[-1] + timedelta(days=365)).isoformat()
                    threshold_days = round(365 * _STATUS_MULTIPLIER["annual"])
        else:
            # Single charge: assume annual -- a subscription with only one recorded
            # charge in 24 months is almost certainly billed yearly.
            median_interval = 365.0
            frequency = "annual"
            monthly_cost = median_amt / 12
            next_expected = (date_objs[-1] + timedelta(days=365)).isoformat()
            threshold_days = round(365 * _STATUS_MULTIPLIER["annual"])

        days_since = (today - date_objs[-1]).days

        # Skip entries that stopped charging beyond the frequency-specific window.
        hide_after = _HIDE_AFTER.get(frequency, 400)
        if days_since > hide_after:
            continue

        status = "active" if days_since <= threshold_days else "check"

        # Use raw txns (uncollapsed) for monthly_data -- summing by calendar month
        # naturally handles split charges without needing collapse logic here.
        twelve_months_ago = (today - timedelta(days=365)).isoformat()[:7]
        monthly: dict[str, float] = {}
        for t in txns:
            month = t["date"][:7]
            if month >= twelve_months_ago:
                monthly[month] = monthly.get(month, 0) + t["amount"]
        monthly_data = [{"month": m, "total": round(v, 2)} for m, v in sorted(monthly.items())]

        subscriptions.append(
            {
                "payee": payee,
                "category": txns[0]["category_name"],
                "frequency": frequency,
                "median_amount": round(median_amt, 2),
                "last_charged": dates[-1],
                "next_expected": next_expected,
                "monthly_cost": round(monthly_cost, 2),
                "annual_cost": round(monthly_cost * 12, 2),
                "status": status,
                "transaction_count": len(txns),
                "monthly_data": monthly_data,
            }
        )

    # Apply canonical name normalization post-hide. Group by canonical name and
    # keep only the entry with the most recent last_charged per group. This avoids
    # merging transactions before frequency detection (which breaks interval math)
    # while still deduplicating entries that represent the same service under
    # different YNAB payee names.
    canonical_groups: dict[str, list[dict]] = {}
    for sub in subscriptions:
        import_orig = payee_import_orig.get(sub["payee"])
        canonical = _canonical_payee(sub["payee"], import_orig)
        canonical_groups.setdefault(canonical, []).append(sub)

    merged: list[dict] = []
    for canonical, group in canonical_groups.items():
        primary = max(group, key=lambda s: s["last_charged"])
        combined: dict[str, float] = {}
        for sub in group:
            for m in sub.get("monthly_data", []):
                combined[m["month"]] = combined.get(m["month"], 0) + m["total"]
        primary["monthly_data"] = [{"month": k, "total": round(v, 2)} for k, v in sorted(combined.items())]
        primary["payee"] = canonical
        merged.append(primary)

    merged.sort(key=lambda x: x["monthly_cost"], reverse=True)
    subscriptions = merged

    active = [s for s in subscriptions if s["status"] == "active"]
    monthly_total = round(sum(s["monthly_cost"] for s in active), 2)
    annual_total = round(monthly_total * 12, 2)

    # YoY change: avg monthly spend (last 3 months) vs same window 12 months prior
    recent_row = conn.execute(
        f"""
        SELECT AVG(monthly_total) AS avg
        FROM (
            SELECT strftime('%Y-%m', date) AS month, SUM(ABS(amount)) AS monthly_total
            FROM transactions
            WHERE deleted = 0
              AND amount < 0
              AND transfer_account_id IS NULL
              AND category_name IN ({placeholders})
              AND date >= date('now', '-3 months')
            GROUP BY month
        )
        """,
        categories,
    ).fetchone()
    prior_row = conn.execute(
        f"""
        SELECT AVG(monthly_total) AS avg
        FROM (
            SELECT strftime('%Y-%m', date) AS month, SUM(ABS(amount)) AS monthly_total
            FROM transactions
            WHERE deleted = 0
              AND amount < 0
              AND transfer_account_id IS NULL
              AND category_name IN ({placeholders})
              AND date >= date('now', '-15 months')
              AND date < date('now', '-12 months')
            GROUP BY month
        )
        """,
        categories,
    ).fetchone()

    recent_avg = recent_row["avg"] or 0.0
    prior_avg = prior_row["avg"]
    yoy_change = round(recent_avg - prior_avg, 2) if prior_avg is not None else None

    result: dict[str, Any] = {
        "subscriptions": subscriptions,
        "monthly_total": monthly_total,
        "annual_total": annual_total,
        "active_count": len(active),
        "check_count": len([s for s in subscriptions if s["status"] == "check"]),
        "yoy_change": yoy_change,
        "categories_searched": list(categories),
        "detection_months": _DETECTION_MONTHS,
    }
    if not subscriptions:
        has_data = conn.execute("SELECT 1 FROM transactions WHERE deleted = 0 LIMIT 1").fetchone() is not None
        if has_data:
            cats_str = ", ".join(categories)
            result["config_hint"] = (
                f"No subscription transactions found in: {cats_str}. "
                "Set YNAB_SUBSCRIPTION_CATEGORIES if your subscriptions use different category names."
            )
    return result


@router.get("/subscriptions")
def get_subscriptions() -> dict[str, Any]:
    conn = get_connection()
    try:
        return _build_subscriptions(conn)
    except sqlite3.OperationalError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    finally:
        conn.close()
