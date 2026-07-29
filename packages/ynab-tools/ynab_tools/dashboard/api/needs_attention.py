"""GET /api/needs-attention and POST /api/approve-all."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException

from ynab_tools.db import get_connection, log_audit

from ._common import get_credentials

router = APIRouter(tags=["needs-attention"])


def _query_needs_attention(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT t.id, t.date, COALESCE(t.payee_name, p.name) AS payee_name,
               t.account_name, t.category_name AS category, t.amount, t.approved, t.memo
        FROM transactions t
        JOIN accounts a ON a.id = t.account_id
        LEFT JOIN payees p ON t.payee_id = p.id
        WHERE t.deleted = 0
          AND t.transfer_account_id IS NULL
          AND a.on_budget = 1
          AND NOT EXISTS (
              SELECT 1 FROM subtransactions st
              WHERE st.transaction_id = t.id AND st.deleted = 0
          )
          AND (t.approved = 0
               OR ((t.category_name IS NULL OR t.category_name = '' OR t.category_name = 'Uncategorized')
                   AND t.date >= date('now', '-30 days')))
          AND COALESCE(t.category_name, '') NOT IN ('Split', 'Split (Multiple Categories...)')
        ORDER BY t.date DESC, payee_name
        """,
    ).fetchall()

    needs_category: list[dict] = []
    ready_to_approve: list[dict] = []
    seen: set[str] = set()

    for row in rows:
        txn_id = row["id"]
        if txn_id in seen:
            continue
        seen.add(txn_id)

        cat = row["category"] or ""
        is_uncategorized = not cat or cat == "Uncategorized"

        item = {
            "id": txn_id,
            "date": row["date"],
            "payee": row["payee_name"] or "",
            "account": row["account_name"] or "",
            "category": cat if cat else None,
            "amount": float(row["amount"] or 0),
            "memo": row["memo"] or None,
        }

        if is_uncategorized:
            needs_category.append(item)
        elif not row["approved"]:
            ready_to_approve.append(item)

    # Uncategorized overspend: query the Uncategorized budget category for the current month
    from datetime import date as _date

    current_month = f"{_date.today().year:04d}-{_date.today().month:02d}-01"
    unc_row = conn.execute(
        """
        SELECT activity, balance
        FROM budget_categories
        WHERE budget_month = ? AND LOWER(name) = 'uncategorized'
          AND deleted = 0
        """,
        (current_month,),
    ).fetchone()
    # balance is negative when overspent (more spent than budgeted)
    uncategorized_overspent_dollars: float | None = None
    if unc_row is not None:
        bal = float(unc_row["balance"] or 0)
        if bal < -0.01:
            uncategorized_overspent_dollars = round(abs(bal), 2)

    return {
        "needs_category": needs_category,
        "ready_to_approve": ready_to_approve,
        "needs_category_count": len(needs_category),
        "ready_to_approve_count": len(ready_to_approve),
        "total": len(needs_category) + len(ready_to_approve),
        "uncategorized_overspent_dollars": uncategorized_overspent_dollars,
    }


@router.get("/needs-attention")
def needs_attention() -> dict[str, Any]:
    """Transactions needing review: uncategorized (last 30 days) or unapproved."""
    conn = get_connection()
    try:
        return _query_needs_attention(conn)
    except sqlite3.OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database not ready. Run 'ynab sync' first. ({exc})",
        )
    finally:
        conn.close()


@router.post("/approve-all")
def approve_all() -> dict[str, Any]:
    """Bulk approve all categorized unapproved transactions."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT t.id
            FROM transactions t
            JOIN accounts a ON t.account_id = a.id
            WHERE t.deleted = 0 AND t.approved = 0
              AND a.on_budget = 1
              AND t.category_name IS NOT NULL
              AND t.category_name NOT IN ('', 'Uncategorized',
                  'Split', 'Split (Multiple Categories...)')
            ORDER BY t.date DESC
            """,
        ).fetchall()

        if not rows:
            return {"ok": True, "approved_count": 0, "failed_count": 0}

        token, plan_id = get_credentials()
        from ynab_tools.client import YNABClient

        client = YNABClient(token, plan_id)
        updates = [{"id": row["id"], "approved": True} for row in rows]
        result = client.bulk_update_transactions(updates)

        if result["success"] > 0:
            detail = f"Bulk approved {result['success']} transactions via dashboard"
            log_audit(conn, "approve-transactions", "transaction", "bulk", "multiple", detail, "approve")
            conn.commit()

        return {
            "ok": result["failed"] == 0,
            "approved_count": result["success"],
            "failed_count": result["failed"],
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Approve failed: {exc}")
    finally:
        conn.close()
