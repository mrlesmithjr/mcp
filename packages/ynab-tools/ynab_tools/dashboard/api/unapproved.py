"""GET /api/unapproved and POST /api/unapproved/approve."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ynab_tools.db import get_connection, log_audit

from ._common import get_credentials

logger = logging.getLogger(__name__)

router = APIRouter(tags=["unapproved"])


def _query_unapproved(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return unapproved transactions, verifying approval status against the YNAB API."""
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
          AND ((t.approved = 0 AND t.date >= date('now', '-90 days'))
               OR ((t.category_name IS NULL OR t.category_name = '' OR t.category_name = 'Uncategorized')
                   AND t.date >= date('now', '-30 days')))
          AND COALESCE(t.category_name, '') NOT IN ('Split', 'Split (Multiple Categories...)')
        ORDER BY t.date DESC, payee_name
        """,
    ).fetchall()

    if not rows:
        return {
            "needs_category": [],
            "ready_to_approve": [],
            "needs_category_count": 0,
            "ready_to_approve_count": 0,
            "total": 0,
        }

    # Verify approval status against YNAB API for locally-unapproved rows.
    # Delta sync can miss approvals made directly in YNAB.
    token, plan_id = get_credentials()
    from ynab_tools.client import YNABClient

    client = YNABClient(token, plan_id)
    approval_overrides: dict[str, bool] = {}
    stale_ids: set[str] = set()
    seen_ids: set[str] = set()

    for row in rows:
        txn_id = row["id"]
        if txn_id in seen_ids or row["approved"]:
            continue
        seen_ids.add(txn_id)
        try:
            api_txn = client.get_transaction(txn_id)
            if api_txn.get("approved"):
                stale_ids.add(txn_id)
                approval_overrides[txn_id] = True
                conn.execute(
                    "UPDATE transactions SET approved = 1 WHERE id = ?",
                    (txn_id,),
                )
        except Exception as exc:
            logger.debug("API approval check failed for %s: %s", txn_id, exc)

    if stale_ids:
        conn.commit()
        logger.info("Fixed %d stale approval(s) from API verification", len(stale_ids))

    needs_category: list[dict] = []
    ready_to_approve: list[dict] = []
    seen_display: set[str] = set()

    for row in rows:
        txn_id = row["id"]
        if txn_id in seen_display:
            continue
        seen_display.add(txn_id)

        cat = row["category"] or ""
        is_approved = row["approved"] or approval_overrides.get(txn_id, False)
        is_uncategorized = not cat or cat == "Uncategorized"

        item = {
            "id": txn_id,
            "date": row["date"],
            "payee": row["payee_name"] or "",
            "account": row["account_name"] or "",
            "category": cat if cat and not is_uncategorized else None,
            "amount": float(row["amount"] or 0),
            "memo": row["memo"] or None,
        }

        if is_uncategorized:
            needs_category.append(item)
        elif not is_approved:
            ready_to_approve.append(item)

    return {
        "needs_category": needs_category,
        "ready_to_approve": ready_to_approve,
        "needs_category_count": len(needs_category),
        "ready_to_approve_count": len(ready_to_approve),
        "total": len(needs_category) + len(ready_to_approve),
    }


@router.get("/unapproved")
def get_unapproved() -> dict[str, Any]:
    """List unapproved transactions (API-verified)."""
    conn = get_connection()
    try:
        return _query_unapproved(conn)
    except HTTPException:
        raise
    except sqlite3.OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database not ready. Run 'ynab sync' first. ({exc})",
        )
    finally:
        conn.close()


class ApproveRequest(BaseModel):
    transaction_id: str | None = None
    all_categorized: bool = False


@router.post("/unapproved/approve")
def approve_transactions(body: ApproveRequest) -> dict[str, Any]:
    """Approve one transaction by ID, or all categorized unapproved transactions."""
    conn = get_connection()
    try:
        token, plan_id = get_credentials()
        from ynab_tools.client import YNABClient

        client = YNABClient(token, plan_id)

        if body.all_categorized:
            rows = conn.execute(
                """
                SELECT t.id
                FROM transactions t
                JOIN accounts a ON a.id = t.account_id
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

            updates = [{"id": row["id"], "approved": True} for row in rows]
            result = client.bulk_update_transactions(updates)

            if result["success"] > 0:
                detail = f"Bulk approved {result['success']} transactions via dashboard inbox"
                log_audit(conn, "approve-transactions", "transaction", "bulk", "multiple", detail, "approve")
                conn.commit()

            return {
                "ok": result["failed"] == 0,
                "approved_count": result["success"],
                "failed_count": result["failed"],
            }

        if body.transaction_id:
            txn_rows = conn.execute(
                """
                SELECT t.id, t.date, COALESCE(t.payee_name, p.name) AS payee_name,
                       t.category_name, t.approved
                FROM transactions t
                LEFT JOIN payees p ON t.payee_id = p.id
                WHERE t.deleted = 0 AND t.id = ?
                """,
                (body.transaction_id,),
            ).fetchall()

            if not txn_rows:
                raise HTTPException(status_code=404, detail=f"Transaction not found: {body.transaction_id}")

            txn = dict(txn_rows[0])
            if txn["approved"]:
                return {"ok": True, "approved_count": 0, "failed_count": 0, "already_approved": True}

            ok = client.update_transaction(txn["id"], approved=True)
            if ok:
                cat = txn["category_name"] or "(uncategorized)"
                detail = f"{txn['date']} | {txn['payee_name']} | {cat}"
                log_audit(
                    conn,
                    "approve-transaction",
                    "transaction",
                    txn["id"],
                    txn["payee_name"],
                    detail,
                    "approve",
                )
                conn.commit()
                return {"ok": True, "approved_count": 1, "failed_count": 0}

            return {"ok": False, "approved_count": 0, "failed_count": 1}

        raise HTTPException(status_code=400, detail="Provide transaction_id or set all_categorized=true")

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Approve failed: {exc}")
    finally:
        conn.close()
