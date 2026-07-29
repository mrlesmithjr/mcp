"""GET /api/paycheck-funding and POST /api/paycheck-funding/apply."""

from __future__ import annotations

import logging
import sqlite3
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ynab_tools.client import YNABClient
from ynab_tools.db import get_connection, init_db
from ynab_tools.reports.funding import _dollars_to_milliunits, _log_funding_change, _month_str
from ynab_tools.reports.paycheck_funding import generate_funding_plan

from ._common import get_credentials

logger = logging.getLogger(__name__)

router = APIRouter(tags=["paycheck-funding"])

TIER_DESCRIPTIONS: dict[int, str] = {
    1: (
        "Categories already overdrawn. Must be fixed first"
        " -- overspent amounts carry forward and distort your whole budget."
    ),
    2: "Planned expenses due within 14 days. Deadlines are close; fund these before the due date arrives.",
    3: (
        "Fixed monthly bills with recurring goals (loans, insurance, subscriptions)."
        " These autopay on a schedule -- they cannot wait."
    ),
    4: (
        "High-frequency spending categories (groceries, gas, dining) that need enough"
        " balance to last until your next paycheck."
    ),
    5: "Everything else underfunded. Fund these if your surplus allows after covering tiers 1-3.",
}


def _serialize_plan(plan: dict) -> dict[str, Any]:
    next_income = plan["next_income"]
    if next_income:
        ni = dict(next_income)
        if isinstance(ni.get("date"), date):
            ni["date"] = ni["date"].isoformat()
        next_income_out = ni
    else:
        next_income_out = None

    tiers = []
    for tier_num in range(1, 6):
        tier = plan["tiers"][tier_num]
        tiers.append(
            {
                "tier": tier_num,
                "label": tier["label"],
                "total": round(sum(i["amount_needed"] for i in tier["items"]), 2),
                "items": [
                    {
                        "id": item["id"],
                        "name": item["name"],
                        "group": item.get("group"),
                        "budgeted": round(item["budgeted"], 2),
                        "balance": round(item["balance"], 2),
                        "amount_needed": round(item["amount_needed"], 2),
                        "reason": item.get("reason", ""),
                    }
                    for item in tier["items"]
                ],
            }
        )

    total_needed = sum(t["total"] for t in tiers)
    protected_total = sum(t["total"] for t in tiers[:3])
    rta = round(plan["rta"], 2)

    return {
        "month": plan["month"][:7],
        "rta": rta,
        "next_income": next_income_out,
        "tiers": tiers,
        "total_needed": round(total_needed, 2),
        "protected_total": round(protected_total, 2),
        "is_protected_covered": rta >= round(protected_total, 2),
        "tier_descriptions": TIER_DESCRIPTIONS,
    }


@router.get("/paycheck-funding")
def get_paycheck_funding() -> dict[str, Any]:
    month = _month_str(None)
    conn = get_connection()
    try:
        init_db(conn)
        plan = generate_funding_plan(conn, month)
        return _serialize_plan(plan)
    except sqlite3.OperationalError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    finally:
        conn.close()


class ApplyFundingRequest(BaseModel):
    through_tier: int = Field(ge=1, le=5)


@router.post("/paycheck-funding/apply")
def apply_paycheck_funding(body: ApplyFundingRequest) -> dict[str, Any]:
    """Execute paycheck funding through a specified tier. Writes to YNAB."""
    month = _month_str(None)
    token, plan_id = get_credentials()
    conn = get_connection()
    try:
        init_db(conn)
        plan = generate_funding_plan(conn, month)
        rta = plan["rta"]
        client = YNABClient(token, plan_id)

        all_items: list[dict] = []
        for t in range(1, body.through_tier + 1):
            all_items.extend(plan["tiers"][t]["items"])

        if not all_items:
            return {"ok": True, "funded": [], "skipped": [], "total_funded": 0.0}

        remaining_rta = rta
        funded = []
        skipped = []

        for item in all_items:
            needed = item["amount_needed"]
            if remaining_rta <= 0:
                skipped.append({"name": item["name"], "amount_needed": round(needed, 2)})
                continue

            fund_amount = min(needed, remaining_rta)
            new_budgeted = item["budgeted"] + fund_amount

            try:
                result = client.update_category_budget(plan["month"], item["id"], _dollars_to_milliunits(new_budgeted))
                _log_funding_change(
                    conn,
                    item["id"],
                    item["name"],
                    item.get("group"),
                    plan["month"],
                    item["budgeted"],
                    new_budgeted,
                    "dashboard-paycheck-funding",
                )
                updated = result.get("category", {})
                if updated:
                    conn.execute(
                        """
                        UPDATE budget_categories
                        SET budgeted = ?, balance = ?, goal_under_funded = ?
                        WHERE id = ? AND budget_month = ?
                        """,
                        (
                            (updated.get("budgeted") or _dollars_to_milliunits(new_budgeted)) / 1000,
                            (updated.get("balance") or 0) / 1000,
                            (updated.get("goal_under_funded") or 0) / 1000,
                            item["id"],
                            plan["month"],
                        ),
                    )
                funded.append(
                    {
                        "name": item["name"],
                        "funded_amount": round(fund_amount, 2),
                        "partial": fund_amount < needed,
                    }
                )
                remaining_rta -= fund_amount
            except Exception as exc:
                logger.error("Failed to fund %s: %s", item["name"], exc)
                skipped.append({"name": item["name"], "amount_needed": round(needed, 2), "error": str(exc)})

        conn.commit()
        return {
            "ok": True,
            "funded": funded,
            "skipped": skipped,
            "total_funded": round(sum(f["funded_amount"] for f in funded), 2),
        }

    except sqlite3.OperationalError as exc:
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        conn.close()
