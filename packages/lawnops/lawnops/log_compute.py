"""Backend-neutral report/list computations over lawn_log rows (issue #57).

Operates on the canonical row dicts returned by lawnops.log_store.read_table()
so the sqlite and markdown backends produce byte-for-byte identical MCP tool
output from the same underlying data. This mirrors the filtering, sorting,
and aggregation that used to live directly in lawnops.db.treatments,
lawnops.db.products, lawnops.db.mowing, lawnops.db.reports, and
lawnops.db.alerts -- those modules are still the sqlite CRUD lawnops.log_store
delegates writes to, but list/report shaping now lives here, once, so it does
not have to be reimplemented per backend.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from lawnops.matching import matches


def _year_filter(rows: list[dict], year: int) -> list[dict]:
    prefix = str(year)
    return [r for r in rows if str(r.get("date") or "").startswith(prefix)]


def list_treatments(rows: list[dict], year: int | None = None) -> tuple[list[dict], int]:
    """Mirrors lawnops.db.treatments.list_treatments. Returns (treatments, year)."""
    if year is None:
        year = datetime.now().year
    filtered = sorted(_year_filter(rows, year), key=lambda r: r.get("date") or "")
    treatments = [
        {
            "id": r.get("id"),
            "date": r.get("date"),
            "treatment_area": r.get("area"),
            "product": r.get("product"),
            "method": r.get("method"),
            "cost": r.get("cost"),
            "notes": r.get("notes"),
        }
        for r in filtered
    ]
    return treatments, year


def list_products(rows: list[dict]) -> list[dict]:
    """Mirrors lawnops.db.products.list_products."""
    projected = [
        {
            "name": r.get("name"),
            "category": r.get("category"),
            "qty_on_hand": r.get("qty_on_hand"),
            "unit": r.get("unit"),
            "last_ordered": r.get("last_ordered"),
            "cost_each": r.get("cost_each"),
            "source": r.get("source"),
        }
        for r in rows
    ]
    projected.sort(key=lambda p: (p["category"] or "", p["name"] or ""))
    return projected


def reorder_alerts(products_rows: list[dict], treatments_rows: list[dict]) -> list[dict]:
    """Mirrors lawnops.db.alerts.get_reorder_alerts.

    Excludes qty_on_hand=None explicitly (`p.get(...) or 0` would treat a
    NULL/unknown quantity as zero and fire a false reorder alert): the
    original `WHERE qty_on_hand <= 0` SQL never matches a NULL row either
    (issue #57 review MAJOR 4).
    """
    zero_stock = [p for p in products_rows if p.get("qty_on_hand") is not None and p.get("qty_on_hand") <= 0]
    alerts = []
    for p in zero_stock:
        used = [t for t in treatments_rows if matches(p.get("name") or "", t.get("product"))]
        alerts.append(
            {
                "name": p.get("name"),
                "category": p.get("category"),
                "unit": p.get("unit"),
                "cost_each": p.get("cost_each"),
                "last_ordered": p.get("last_ordered"),
                "source": p.get("source"),
                "treatment_count": len(used),
                "last_used": max((t.get("date") for t in used), default=None),
            }
        )
    alerts.sort(key=lambda a: (-a["treatment_count"], a["name"] or ""))
    return alerts


def list_equipment(rows: list[dict]) -> list[dict]:
    """Mirrors lawnops.db.equipment.list_equipment."""
    projected = [
        {
            "id": r.get("id"),
            "name": r.get("name"),
            "purchase_date": r.get("purchase_date"),
            "cost": r.get("cost"),
            "source": r.get("source"),
            "status": r.get("status"),
            "notes": r.get("notes"),
        }
        for r in rows
    ]
    projected.sort(key=lambda e: e.get("purchase_date") or "")
    return projected


def mowing_summary(rows: list[dict], year: int | None = None) -> tuple[list[dict], int, float, int]:
    """Mirrors lawnops.db.mowing.get_mowing_summary. Returns (visits, total_visits, total_cost, year)."""
    if year is None:
        year = datetime.now().year
    filtered = sorted(_year_filter(rows, year), key=lambda r: r.get("date") or "")
    total_visits = len(filtered)
    total_cost = sum((r.get("cost") or 0) for r in filtered)
    visits = [
        {
            "id": r.get("id"),
            "date": r.get("date"),
            "provider": r.get("provider"),
            "cost": r.get("cost"),
            "notes": r.get("notes"),
        }
        for r in filtered
    ]
    return visits, total_visits, total_cost, year


def mowing_gap(rows: list[dict], config: dict, year: int | None = None) -> dict | None:
    """Mirrors lawnops.db.mowing.get_mowing_gap."""
    if year is None:
        year = datetime.now().year
    dates = [r.get("date") for r in _year_filter(rows, year) if r.get("date")]
    if not dates:
        return None
    last_date = max(dates)

    if not config.get("mowing", {}).get("schedule_day"):
        return None
    expected_interval_days = 7

    try:
        last = datetime.strptime(last_date, "%Y-%m-%d").date()
    except ValueError:
        return None
    days_since = (datetime.now().date() - last).days

    return {
        "last_visit": last_date,
        "days_since_last": days_since,
        "expected_interval_days": expected_interval_days,
        "unlogged_suspected": days_since > expected_interval_days * 1.5,
    }


def spend_report(
    rows: list[dict], year: int | None = None, category: str | None = None
) -> tuple[list[dict], float, list[dict], int]:
    """Mirrors lawnops.db.reports.get_spend_report. Returns (categories, grand_total, items, year).

    A category's `total` is None when every purchase in it has cost=None,
    mirroring the original `SUM(cost)` SQL (no COALESCE at the per-category
    level -- SUM over an all-NULL group is NULL, not 0). The grand_total
    still defaults to 0 when there is nothing to sum, matching the original
    query's separate `COALESCE(SUM(cost), 0)` (issue #57 review MINOR 5).
    """
    if year is None:
        year = datetime.now().year
    filtered = _year_filter(rows, year)
    if category:
        filtered = [r for r in filtered if r.get("category") == category]

    # [sum_of_non_null_costs, row_count, saw_a_non_null_cost]
    totals: dict = defaultdict(lambda: [0.0, 0, False])
    for r in filtered:
        entry = totals[r.get("category")]
        cost = r.get("cost")
        entry[1] += 1
        if cost is not None:
            entry[0] += cost
            entry[2] = True
    category_rows = [{"category": cat, "total": (t[0] if t[2] else None), "count": t[1]} for cat, t in totals.items()]
    # SQLite's default ORDER BY total DESC puts NULL totals last (NULLs sort
    # low in ASC, so they end up last in DESC); non-null totals sort by
    # highest spend first, same as before.
    category_rows.sort(key=lambda c: (c["total"] is None, -(c["total"] or 0)))
    grand_total = sum(t[0] for t in totals.values())

    item_rows = [
        {
            "id": r.get("id"),
            "date": r.get("date"),
            "item": r.get("item"),
            "category": r.get("category"),
            "cost": r.get("cost"),
            "source": r.get("source"),
        }
        for r in sorted(filtered, key=lambda r: r.get("date") or "")
    ]
    return category_rows, grand_total, item_rows, year
