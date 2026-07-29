"""GET /api/months - list available budget months for the month picker."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException

from ynab_tools.db import get_connection

router = APIRouter(tags=["months"])


@router.get("/months")
def months() -> dict[str, Any]:
    """Return all budget months present in the database, newest first."""
    conn = get_connection()
    try:
        rows = conn.execute("SELECT month FROM budget_months ORDER BY month DESC").fetchall()
        return {"months": [row["month"] for row in rows], "count": len(rows)}
    except sqlite3.OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database not ready. Run 'ynab sync' first. ({exc})",
        )
    finally:
        conn.close()
