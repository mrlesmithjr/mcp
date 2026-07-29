"""GET /api/spending-pace - mid-month category spending pace."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException

from ynab_tools.dashboard.api._common import days_elapsed_in_month
from ynab_tools.db import get_connection
from ynab_tools.stats import anomaly_label, category_zscore_by_name, should_skip_group

router = APIRouter(tags=["spending-pace"])


def _build_spending_pace(conn: sqlite3.Connection) -> dict[str, Any]:
    now = datetime.now()
    year, mon = now.year, now.month
    current_month = f"{year:04d}-{mon:02d}-01"
    target_month = current_month
    days_elapsed, days_in_month = days_elapsed_in_month(year, mon)
    pct_elapsed = days_elapsed / days_in_month

    cats = conn.execute(
        """
        SELECT name, category_group_name, budgeted, activity
        FROM budget_categories
        WHERE budget_month = ? AND deleted = 0 AND hidden = 0
        ORDER BY name
        """,
        (target_month,),
    ).fetchall()

    if not cats:
        return {
            "month": target_month[:7],
            "days_elapsed": days_elapsed,
            "days_in_month": days_in_month,
            "pct_elapsed": round(pct_elapsed * 100, 1),
            "categories": [],
            "overspent_count": 0,
            "hot_count": 0,
            "on_track_count": 0,
            "under_count": 0,
            "projected_overspend": 0.0,
        }

    # 3-month trailing average per category
    prior_months = []
    for i in range(1, 4):
        m = mon - i
        y = year
        while m <= 0:
            m += 12
            y -= 1
        prior_months.append(f"{y:04d}-{m:02d}-01")

    ph = ",".join("?" * len(prior_months))
    trailing_rows = conn.execute(
        f"""
        SELECT name,
               CASE WHEN SUM(CASE WHEN activity < 0 THEN 1 ELSE 0 END) = 0 THEN 0.0
                    ELSE -SUM(CASE WHEN activity < 0 THEN activity ELSE 0 END)
                         / SUM(CASE WHEN activity < 0 THEN 1 ELSE 0 END)
               END AS avg_activity
        FROM budget_categories
        -- hidden intentionally omitted: INSERT OR REPLACE propagates current hidden flag
        -- to historical rows; categories hidden after the fact must remain in past-month data.
        WHERE budget_month IN ({ph}) AND deleted = 0
        GROUP BY name
        """,
        prior_months,
    ).fetchall()
    trailing_avgs = {r["name"]: r["avg_activity"] or 0.0 for r in trailing_rows}

    hot, on_track, under = [], [], []

    for row in cats:
        name = row["name"]
        budgeted = row["budgeted"] or 0.0
        activity = row["activity"] or 0.0

        if should_skip_group(row["category_group_name"] or ""):
            continue

        # Only treat negative activity (actual spending) as spend.
        # Positive activity (refunds/inflows) should not be flagged as overspent.
        spent = abs(activity) if activity < 0 else 0.0

        if budgeted == 0 and spent > 0:
            z = category_zscore_by_name(conn, name, current_month)
            entry = {
                "name": name,
                "budgeted": 0.0,
                "spent": round(spent, 2),
                "pct_used": 0.0,
                "pace": 0.0,
                "projected": round(spent, 2),
                "trailing_avg": round(trailing_avgs.get(name, 0.0), 2),
                "status": "overspent",
                "z_score": round(z, 2) if z is not None else None,
                "anomaly_label": anomaly_label(z),
            }
            hot.append(entry)
            continue

        if budgeted <= 0:
            continue
        pct_used = spent / budgeted
        pace = pct_used / pct_elapsed if pct_elapsed > 0 else 0.0
        projected = spent if pct_used >= 1.0 else (spent / max(days_elapsed, 1)) * days_in_month
        trailing_avg = trailing_avgs.get(name, 0.0)

        entry = {
            "name": name,
            "budgeted": round(budgeted, 2),
            "spent": round(spent, 2),
            "pct_used": round(pct_used * 100, 1),
            "pace": round(pace, 3),
            "projected": round(projected, 2),
            "trailing_avg": round(trailing_avg, 2),
        }

        if spent > budgeted:
            z = category_zscore_by_name(conn, name, current_month)
            entry["status"] = "overspent"
            entry["z_score"] = round(z, 2) if z is not None else None
            entry["anomaly_label"] = anomaly_label(z)
            hot.append(entry)
        elif pace > 1.15 and pct_elapsed >= 0.10 and projected > budgeted:
            z = category_zscore_by_name(conn, name, current_month)
            entry["status"] = "hot"
            entry["z_score"] = round(z, 2) if z is not None else None
            entry["anomaly_label"] = anomaly_label(z)
            hot.append(entry)
        elif pace < 0.5 and budgeted >= 50:
            entry["status"] = "under"
            entry["z_score"] = None
            entry["anomaly_label"] = None
            under.append(entry)
        else:
            entry["status"] = "on_track"
            entry["z_score"] = None
            entry["anomaly_label"] = None
            on_track.append(entry)

    hot.sort(key=lambda x: -x["pace"])
    on_track.sort(key=lambda x: -x["pct_used"])
    under.sort(key=lambda x: x["pct_used"])

    projected_overspend = round(sum(max(0, c["projected"] - c["budgeted"]) for c in hot), 2)
    hot_only_count = sum(1 for c in hot if c["status"] == "hot")
    overspent_count = sum(1 for c in hot if c["status"] == "overspent")

    return {
        "month": target_month[:7],
        "days_elapsed": days_elapsed,
        "days_in_month": days_in_month,
        "pct_elapsed": round(pct_elapsed * 100, 1),
        "categories": hot + on_track + under,
        "hot_count": hot_only_count,
        "overspent_count": overspent_count,
        "on_track_count": len(on_track),
        "under_count": len(under),
        "projected_overspend": projected_overspend,
    }


@router.get("/spending-pace")
def get_spending_pace() -> dict[str, Any]:
    conn = get_connection()
    try:
        return _build_spending_pace(conn)
    except sqlite3.OperationalError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    finally:
        conn.close()
