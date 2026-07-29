"""GET /api/audit - unified audit log combining audit_log and funding_log."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ynab_tools.db import get_connection

router = APIRouter(tags=["audit"])

_VALID_SOURCES = {"all", "cli", "dashboard"}


@router.get("/audit")
def audit_log(
    limit: int = Query(default=100, ge=1, le=500),
    source: str = Query(default="all"),
) -> dict[str, Any]:
    """Unified audit log: goal changes, planned expense ops, and budget funding changes."""
    if source not in _VALID_SOURCES:
        raise HTTPException(status_code=422, detail=f"source must be one of {sorted(_VALID_SOURCES)}")

    conn = get_connection()
    try:
        # Source filter clause - "dashboard" matches source values containing "dashboard",
        # "cli" matches everything else (fund, fund-goals, categorize, etc.)
        if source == "dashboard":
            audit_source_filter = "AND source = 'dashboard'"
            funding_source_filter = "AND source LIKE 'dashboard%'"
        elif source == "cli":
            audit_source_filter = "AND source != 'dashboard'"
            funding_source_filter = "AND source NOT LIKE 'dashboard%'"
        else:
            audit_source_filter = ""
            funding_source_filter = ""

        audit_rows = conn.execute(
            f"""
            SELECT timestamp, action, entity_name AS name, details,
                   source, 'audit' AS log_type
            FROM audit_log
            WHERE 1=1 {audit_source_filter}
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        funding_rows = conn.execute(
            f"""
            SELECT timestamp,
                   source AS action,
                   category_name AS name,
                   printf('$%.2f → $%.2f (%+.2f)', old_budgeted, new_budgeted, delta) AS details,
                   source,
                   delta,
                   'funding' AS log_type
            FROM funding_log
            WHERE 1=1 {funding_source_filter}
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        # money_movements captures ALL budget moves (CLI + YNAB app); only include in "all" view.
        # Exclude records already covered by funding_log (CLI-sourced moves) to avoid duplicates.
        # Match on category name, amount (within $0.01), and timestamp (within 60 seconds).
        if source == "all":
            movement_rows = conn.execute(
                """
                SELECT moved_at AS timestamp,
                       'move' AS action,
                       COALESCE(to_category_name, 'RTA') AS name,
                       printf('%s → %s: $%.2f',
                           COALESCE(from_category_name, 'RTA'),
                           COALESCE(to_category_name, 'RTA'),
                           amount) AS details,
                       'money_movements' AS source,
                       NULL AS delta,
                       'move' AS log_type
                FROM money_movements mm
                WHERE deleted = 0
                  AND NOT EXISTS (
                      SELECT 1 FROM funding_log fl
                      WHERE fl.category_name = mm.to_category_name
                        AND ABS(fl.delta - mm.amount) < 0.01
                        AND ABS(
                            strftime('%s', replace(mm.moved_at, 'Z', '')) -
                            strftime('%s', substr(fl.timestamp, 1, 19))
                        ) < 60
                  )
                ORDER BY moved_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        else:
            movement_rows = []

        entries = [dict(r) for r in audit_rows] + [dict(r) for r in funding_rows] + [dict(r) for r in movement_rows]
        entries.sort(key=lambda e: e["timestamp"], reverse=True)
        entries = entries[:limit]

        return {"entries": entries, "count": len(entries)}

    except sqlite3.OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database not ready. Run 'ynab sync' first. ({exc})",
        )
    finally:
        conn.close()
