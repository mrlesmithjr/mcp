"""Mutation endpoints: apply calibration target and fund sinking fund goals."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, Field, field_validator

from ynab_tools.client import YNABClient
from ynab_tools.db import get_connection, log_audit
from ynab_tools.reports.funding import _log_funding_change

from ._common import get_credentials

router = APIRouter(tags=["fund"])


def _validate_month(v: str) -> str:
    try:
        datetime.strptime(v, "%Y-%m-%d")
    except ValueError:
        raise ValueError("must be YYYY-MM-DD")
    return v


class ApplyTargetRequest(BaseModel):
    new_target: float = Field(gt=0)
    category_name: str
    category_group: str
    goal_type: str
    goal_target_month: str | None = None
    budget_month: str

    @field_validator("budget_month")
    @classmethod
    def validate_budget_month(cls, v: str) -> str:
        return _validate_month(v)


@router.patch("/calibration/{category_id}/target")
def apply_calibration_target(
    category_id: str = Path(...),
    body: ApplyTargetRequest = ...,
) -> dict[str, Any]:
    """Apply a recommended target to a category goal."""
    token, plan_id = get_credentials()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT goal_target, budgeted FROM budget_categories WHERE id = ? AND budget_month = ?",
            (category_id, body.budget_month),
        ).fetchone()
        old_target = float(row["goal_target"] or 0) if row else 0.0
        current_budgeted = float(row["budgeted"] or 0) if row else 0.0

        client = YNABClient(token, plan_id)
        ok = client.update_category_goal(
            category_id,
            body.goal_type,
            int(round(body.new_target * 1000)),
            body.goal_target_month,
        )
        if not ok:
            raise HTTPException(status_code=502, detail="YNAB API call failed.")

        # Only estimate goal_under_funded for simple monthly goals (NEED/MF with no target date).
        # For date-bound goals (TBD, NEED+target month), new_target is the full savings goal,
        # not the monthly installment, so new_target - budgeted would be wildly inflated.
        # In those cases, preserve the existing value and let the next sync recompute it. refs #182
        is_simple_monthly = body.goal_type in ("NEED", "MF") and body.goal_target_month is None
        if is_simple_monthly:
            new_under_funded = max(0.0, round(body.new_target - current_budgeted, 2))
            # Scope UPDATE to budget_month - budget_categories has composite (id, budget_month) rows
            conn.execute(
                "UPDATE budget_categories SET goal_target = ?, goal_under_funded = ? WHERE id = ? AND budget_month = ?",
                (body.new_target, new_under_funded, category_id, body.budget_month),
            )
        else:
            # Date-bound goal: update goal_target only; leave goal_under_funded for sync to recompute
            conn.execute(
                "UPDATE budget_categories SET goal_target = ? WHERE id = ? AND budget_month = ?",
                (body.new_target, category_id, body.budget_month),
            )

        log_audit(
            conn,
            action="set-goal",
            entity_type="category",
            entity_id=category_id,
            entity_name=body.category_name,
            details=f"goal_target {old_target} -> {body.new_target} (dashboard calibration)",
            source="dashboard",
        )

        conn.commit()
        return {"ok": True, "new_target": body.new_target}

    except sqlite3.OperationalError as exc:
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        conn.close()


class FundGoalsRequest(BaseModel):
    budget_month: str
    dry_run: bool = False

    @field_validator("budget_month")
    @classmethod
    def validate_budget_month(cls, v: str) -> str:
        return _validate_month(v)


@router.post("/sinking-funds/fund-goals")
def fund_sinking_goals(body: FundGoalsRequest) -> dict[str, Any]:
    """Preview or apply funding for all underfunded goal categories."""
    conn = get_connection()
    try:
        rta_row = conn.execute(
            "SELECT to_be_budgeted FROM budget_months WHERE month = ?",
            (body.budget_month,),
        ).fetchone()
        rta = float(rta_row["to_be_budgeted"] or 0) if rta_row else 0.0

        rows = [
            dict(r)
            for r in conn.execute(
                """
                SELECT id, name, category_group_name, budgeted, goal_under_funded
                FROM budget_categories
                WHERE budget_month = ?
                  AND deleted = 0 AND hidden = 0
                  AND goal_under_funded > 0
                ORDER BY category_group_name, name
                """,
                (body.budget_month,),
            ).fetchall()
        ]

        if not rows:
            return {
                "ok": True,
                "dry_run": body.dry_run,
                "funded_count": 0,
                "total_funded": 0.0,
                "rta": rta,
                "rta_insufficient": False,
                "categories": [],
                "skipped": [],
                "failures": [],
            }

        # Skip manually reduced categories (same logic as CLI fund --goals).
        # Include both 'fund' (CLI) and 'dashboard-fund-goals' as reduction sources
        # so dashboard-initiated defunds are also respected.
        reduced_rows = conn.execute(
            """
            SELECT category_name, SUM(delta) AS net_delta
            FROM funding_log
            WHERE budget_month = ?
              AND source IN ('fund', 'dashboard-fund-goals')
            GROUP BY category_name
            HAVING SUM(delta) < 0
            """,
            (body.budget_month,),
        ).fetchall()
        reduced = {r["category_name"] for r in reduced_rows}

        eligible = [r for r in rows if r["name"] not in reduced]
        skipped = [r["name"] for r in rows if r["name"] in reduced]
        total_needed = sum(r["goal_under_funded"] for r in eligible)

        preview = [
            {
                "name": r["name"],
                "group": r["category_group_name"],
                "current_budgeted": round(float(r["budgeted"] or 0), 2),
                "new_budgeted": round(float(r["budgeted"] or 0) + float(r["goal_under_funded"]), 2),
                "amount": round(float(r["goal_under_funded"]), 2),
            }
            for r in eligible
        ]

        rta_insufficient = total_needed > rta

        if body.dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "funded_count": len(eligible),
                "total_funded": round(total_needed, 2),
                "rta": round(rta, 2),
                "rta_insufficient": rta_insufficient,
                "categories": preview,
                "skipped": skipped,
                "failures": [],
            }

        token, plan_id = get_credentials()
        client = YNABClient(token, plan_id)

        success = 0
        total_actually_funded = 0.0
        failures: list[dict[str, str]] = []
        for r in eligible:
            current = float(r["budgeted"] or 0)
            new_amount = current + float(r["goal_under_funded"])
            try:
                result = client.update_category_budget(body.budget_month, r["id"], int(round(new_amount * 1000)))
                _log_funding_change(
                    conn,
                    r["id"],
                    r["name"],
                    r["category_group_name"],
                    body.budget_month,
                    current,
                    new_amount,
                    "dashboard-fund-goals",
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
                            updated.get("budgeted", int(round(new_amount * 1000))) / 1000,
                            (updated.get("balance") or 0) / 1000,
                            (updated.get("goal_under_funded") or 0) / 1000,
                            r["id"],
                            body.budget_month,
                        ),
                    )
                success += 1
                total_actually_funded += float(r["goal_under_funded"])
            except Exception as exc:
                failures.append({"name": r["name"], "error": str(exc)})

        conn.commit()
        return {
            "ok": True,
            "dry_run": False,
            "funded_count": success,
            "total_funded": round(total_actually_funded, 2),
            "rta": round(rta, 2),
            "rta_insufficient": rta_insufficient,
            "categories": preview,
            "skipped": skipped,
            "failures": failures,
        }

    except sqlite3.OperationalError as exc:
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        conn.close()
