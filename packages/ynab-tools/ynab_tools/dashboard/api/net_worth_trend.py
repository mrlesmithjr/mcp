"""GET /api/overview/net-worth-trend - net worth trajectory from snapshot history."""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException

from ynab_tools.db import get_connection

router = APIRouter(tags=["overview"])

_MAX_MONTHS = 12
_MAX_RAW_ROWS = 366  # one snapshot per day worst-case over 12 months; dedup loop controls final window


def _get_net_worth_trend(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT snapshot_date, net_worth
        FROM net_worth_snapshots
        ORDER BY snapshot_date DESC
        LIMIT ?
        """,
        (_MAX_RAW_ROWS,),
    ).fetchall()

    if not rows:
        return {
            "has_data": False,
            "current_net_worth": None,
            "snapshots": [],
            "months_of_history": 0,
            "delta_from_last": None,
            "delta_from_month": None,
        }

    # Newest first in DB result; present as month-labeled buckets
    snapshots = []
    for row in rows:
        snap_date = row["snapshot_date"]
        # Convert daily snapshot date to YYYY-MM label
        month_label = snap_date[:7]  # e.g. "2026-04"
        nw = float(row["net_worth"] or 0)
        snapshots.append({"month": month_label, "net_worth": round(nw, 2)})

    # De-duplicate: keep the most-recent snapshot per month (rows are DESC, so first seen wins)
    seen_months: set[str] = set()
    unique_snapshots = []
    for snap in snapshots:
        if snap["month"] not in seen_months:
            seen_months.add(snap["month"])
            unique_snapshots.append(snap)

    unique_snapshots = unique_snapshots[:_MAX_MONTHS]

    current_net_worth = unique_snapshots[0]["net_worth"] if unique_snapshots else None

    delta_from_last: float | None = None
    delta_from_month: str | None = None
    if len(unique_snapshots) >= 2:
        delta_from_last = round(unique_snapshots[0]["net_worth"] - unique_snapshots[1]["net_worth"], 2)
        # Indicate which month's snapshot delta_from_last is comparing against so the
        # caller knows when months are skipped (e.g. April had no snapshots).
        delta_from_month = unique_snapshots[1]["month"]

    # Return in ascending order for charting (oldest first)
    chart_snapshots = list(reversed(unique_snapshots))

    return {
        "has_data": True,
        "current_net_worth": current_net_worth,
        "snapshots": chart_snapshots,
        "months_of_history": len(unique_snapshots),
        "delta_from_last": delta_from_last,
        "delta_from_month": delta_from_month,
        "as_of": date.today().isoformat(),
    }


@router.get("/overview/net-worth-trend")
def net_worth_trend() -> dict[str, Any]:
    """Net worth trajectory from snapshot history (up to 12 months)."""
    conn = get_connection()
    try:
        return _get_net_worth_trend(conn)
    except sqlite3.OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database not ready. Run 'ynab sync' first. ({exc})",
        )
    finally:
        conn.close()
