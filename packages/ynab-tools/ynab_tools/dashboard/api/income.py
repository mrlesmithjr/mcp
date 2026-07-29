"""GET /api/income - income breakdown: regular pay vs bonus, YTD."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException

from ynab_tools.config import load_env
from ynab_tools.dashboard.api._common import month_starts
from ynab_tools.db import get_connection, init_db

router = APIRouter(tags=["income"])

_AVG_LOOKBACK = 12  # complete months used for pace baseline


def _avg_monthly_net(conn: sqlite3.Connection) -> tuple[float | None, int]:
    """Return (avg_monthly_net, months_count) from last 12 complete months.

    Uses all non-transfer Income:/Inflow: transactions - same population as
    ytd_total - so the pace comparison is net vs. net.
    """
    complete = month_starts(_AVG_LOOKBACK, complete_only=True)
    if not complete:
        return None, 0
    oldest = complete[-1]  # YYYY-MM-01 for oldest month start
    # Upper bound: start of current month (exclusive) keeps partial month out.
    today = datetime.now()
    current_month_start = f"{today.year:04d}-{today.month:02d}-01"
    rows = conn.execute(
        """
        SELECT strftime('%Y-%m', t.date) AS month, SUM(t.amount) AS total
        FROM transactions t
        JOIN accounts a ON a.id = t.account_id
        WHERE t.deleted = 0 AND a.on_budget = 1 AND t.amount > 0
          AND (t.category_name LIKE 'Income:%' OR t.category_name LIKE 'Inflow:%')
          AND t.transfer_account_id IS NULL
          AND t.date >= ?
          AND t.date < ?
        GROUP BY strftime('%Y-%m', t.date)
        """,
        (oldest, current_month_start),
    ).fetchall()
    if not rows:
        return None, 0
    total = sum(r["total"] for r in rows)
    count = len(rows)
    return total / count, count


def _build_income(conn: sqlite3.Connection, months: int = 12) -> dict[str, Any]:
    load_env()
    regular_pay = float(os.environ.get("YNAB_REGULAR_PAY", "") or 0) or None
    bonus_threshold = float(os.environ.get("YNAB_BONUS_THRESHOLD", "") or 0) or None
    has_bonus_config = regular_pay is not None and bonus_threshold is not None
    gross_salary_raw = os.environ.get("YNAB_GROSS_SALARY", "").strip()
    gross_salary = float(gross_salary_raw) if gross_salary_raw else None
    gross_ote_raw = os.environ.get("YNAB_GROSS_OTE", "").strip()
    gross_ote = float(gross_ote_raw) if gross_ote_raw else None

    payee_config = os.environ.get("YNAB_PAYCHECK_PAYEES", "").strip()
    if payee_config:
        payee_names = [p.strip() for p in payee_config.split(",") if p.strip()]
        payee_clauses = " OR ".join(["t.payee_name LIKE ?"] * len(payee_names))
        payee_params = [f"%{p}%" for p in payee_names]
        paychecks = [
            dict(r)
            for r in conn.execute(
                f"""
                SELECT t.date, t.payee_name, t.amount
                FROM transactions t
                JOIN accounts a ON a.id = t.account_id
                WHERE t.deleted = 0 AND a.on_budget = 1 AND t.amount > 0
                  AND (t.category_name LIKE 'Income:%' OR t.category_name LIKE 'Inflow:%')
                  AND ({payee_clauses})
                  AND t.date >= date('now', ? || ' months')
                ORDER BY t.date DESC
                """,
                (*payee_params, f"-{months}"),
            ).fetchall()
        ]
    else:
        paychecks = [
            dict(r)
            for r in conn.execute(
                """
                SELECT t.date, t.payee_name, t.amount
                FROM transactions t
                JOIN accounts a ON a.id = t.account_id
                WHERE t.deleted = 0 AND a.on_budget = 1
                  AND t.category_name = 'Income: Paychecks'
                  AND t.date >= date('now', ? || ' months')
                ORDER BY t.date DESC
                """,
                (f"-{months}",),
            ).fetchall()
        ]

    all_income = [
        dict(r)
        for r in conn.execute(
            """
            SELECT t.date, t.payee_name, t.amount, t.category_name
            FROM transactions t
            JOIN accounts a ON a.id = t.account_id
            WHERE t.deleted = 0 AND a.on_budget = 1 AND t.amount > 0
              AND (t.category_name LIKE 'Income:%' OR t.category_name LIKE 'Inflow:%')
              AND t.transfer_account_id IS NULL
              AND t.date >= date('now', ? || ' months')
            ORDER BY t.date DESC
            """,
            (f"-{months}",),
        ).fetchall()
    ]

    paycheck_keys = {(p["date"], p["payee_name"], p["amount"]) for p in paychecks}
    other_income = [t for t in all_income if (t["date"], t["payee_name"], t["amount"]) not in paycheck_keys]

    # Build per-month breakdown
    month_map: dict[str, dict] = {}

    for p in paychecks:
        m = p["date"][:7]
        if m not in month_map:
            month_map[m] = {"regular": 0.0, "bonus": 0.0, "other": 0.0, "paychecks": []}
        if has_bonus_config and p["amount"] > bonus_threshold:
            bonus_portion = p["amount"] - regular_pay
            month_map[m]["regular"] += regular_pay
            month_map[m]["bonus"] += bonus_portion
            month_map[m]["paychecks"].append(
                {
                    "date": p["date"],
                    "payee": p["payee_name"],
                    "amount": round(p["amount"], 2),
                    "regular": round(regular_pay, 2),
                    "bonus": round(bonus_portion, 2),
                    "is_bonus": True,
                }
            )
        else:
            month_map[m]["regular"] += p["amount"]
            month_map[m]["paychecks"].append(
                {
                    "date": p["date"],
                    "payee": p["payee_name"],
                    "amount": round(p["amount"], 2),
                    "regular": round(p["amount"], 2),
                    "bonus": 0.0,
                    "is_bonus": False,
                }
            )

    for o in other_income:
        m = o["date"][:7]
        if m not in month_map:
            month_map[m] = {"regular": 0.0, "bonus": 0.0, "other": 0.0, "paychecks": []}
        month_map[m]["other"] += o["amount"]

    current_year = datetime.now().strftime("%Y")
    months_list = []
    ytd_regular = ytd_bonus = ytd_other = 0.0

    for m in sorted(month_map.keys(), reverse=True):
        d = month_map[m]
        total = d["regular"] + d["bonus"] + d["other"]
        months_list.append(
            {
                "month": m,
                "regular": round(d["regular"], 2),
                "bonus": round(d["bonus"], 2),
                "other": round(d["other"], 2),
                "total": round(total, 2),
                "paychecks": d["paychecks"],
            }
        )
        if m.startswith(current_year):
            ytd_regular += d["regular"]
            ytd_bonus += d["bonus"]
            ytd_other += d["other"]

    avg_net, avg_net_months = _avg_monthly_net(conn)

    result: dict[str, Any] = {
        "months": months_list,
        "ytd_regular": round(ytd_regular, 2),
        "ytd_bonus": round(ytd_bonus, 2),
        "ytd_other": round(ytd_other, 2),
        "ytd_total": round(ytd_regular + ytd_bonus + ytd_other, 2),
        "has_bonus_config": has_bonus_config,
        "regular_pay": regular_pay,
        "bonus_threshold": bonus_threshold,
        "gross_salary": gross_salary,
        "gross_ote": gross_ote,
        "current_year": current_year,
        "avg_monthly_net": round(avg_net, 2) if avg_net is not None else None,
        "avg_monthly_net_months": avg_net_months,
    }
    if not payee_config and not months_list:
        result["config_hint"] = (
            "No income transactions found. Income detection fell back to category-based "
            "lookup (categories matching 'Income:%'). "
            "Set YNAB_PAYCHECK_PAYEES to your paycheck payee names for more accurate detection."
        )
    elif not payee_config:
        result["config_hint"] = (
            "YNAB_PAYCHECK_PAYEES is not set. Paycheck detection is using category-based "
            "fallback (categories matching 'Income: Paychecks'). "
            "Set YNAB_PAYCHECK_PAYEES to your paycheck payee names for more accurate detection."
        )
    return result


@router.get("/income")
def get_income(months: int = 12) -> dict[str, Any]:
    conn = get_connection()
    try:
        init_db(conn)
        return _build_income(conn, months)
    except sqlite3.OperationalError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    finally:
        conn.close()
