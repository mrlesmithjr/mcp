"""Planned expense mutation endpoints: add and mark done."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, Field, field_validator

from ynab_tools.db import get_connection, log_audit

router = APIRouter(tags=["planned"])


class AddPlannedRequest(BaseModel):
    category_name: str
    category_id: str
    amount: float = Field(gt=0)
    due_date: str
    memo: str = ""

    @field_validator("due_date")
    @classmethod
    def validate_due_date(cls, v: str) -> str:
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            raise ValueError("due_date must be YYYY-MM-DD")
        return v


@router.post("/planned-expenses")
def add_planned_expense(body: AddPlannedRequest) -> dict[str, Any]:
    """Add a planned expense entry."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            INSERT INTO planned_expenses (category_name, category_id, amount, due_date, memo)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                body.category_name,
                body.category_id,
                body.amount,
                body.due_date,
                body.memo or None,
            ),
        )
        log_audit(
            conn,
            action="add-planned",
            entity_type="planned_expense",
            entity_id=str(cursor.lastrowid),
            entity_name=body.category_name,
            details=f"amount={body.amount} due={body.due_date}",
            source="dashboard",
        )
        conn.commit()
        return {"ok": True, "id": cursor.lastrowid}
    except sqlite3.OperationalError as exc:
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")
    finally:
        conn.close()


@router.patch("/planned-expenses/{expense_id}/done")
def mark_planned_done(expense_id: int = Path(...)) -> dict[str, Any]:
    """Mark a planned expense as completed."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, category_name FROM planned_expenses WHERE id = ? AND status = 'active'",
            (expense_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Planned expense not found or already completed.")

        conn.execute(
            "UPDATE planned_expenses SET status = 'completed', completed_at = ? WHERE id = ?",
            (datetime.now(UTC).isoformat(), expense_id),
        )
        log_audit(
            conn,
            action="complete-planned",
            entity_type="planned_expense",
            entity_id=str(expense_id),
            entity_name=row["category_name"],
            details="marked done via dashboard",
            source="dashboard",
        )
        conn.commit()
        return {"ok": True}
    except sqlite3.OperationalError as exc:
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")
    except HTTPException:
        raise
    finally:
        conn.close()
