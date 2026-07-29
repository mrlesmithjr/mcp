"""GET /api/categories - list categories for the Add Planned Expense dropdown."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException

from ynab_tools.db import get_connection

router = APIRouter(tags=["categories"])


@router.get("/categories")
def list_categories() -> dict[str, Any]:
    """Return all active categories grouped for use in dropdowns."""
    conn = get_connection()
    try:
        latest_row = conn.execute("SELECT MAX(budget_month) AS m FROM budget_categories WHERE budgeted > 0").fetchone()
        latest_month = latest_row["m"] if latest_row and latest_row["m"] else None

        if not latest_month:
            return {"categories": []}

        rows = conn.execute(
            """
            SELECT id, name, category_group_name
            FROM budget_categories
            WHERE budget_month = ?
              AND deleted = 0 AND hidden = 0
            ORDER BY category_group_name, name
            """,
            (latest_month,),
        ).fetchall()

        return {
            "categories": [{"id": r["id"], "name": r["name"], "group": r["category_group_name"] or ""} for r in rows]
        }
    except sqlite3.OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database not ready. Run 'ynab sync' first. ({exc})",
        )
    finally:
        conn.close()
