"""GET /api/month-end - consolidated month-end closeout view."""

from __future__ import annotations

import re
import sqlite3
from calendar import monthrange
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ynab_tools.db import get_connection
from ynab_tools.stats import should_skip_group

from ._anomaly import anomaly_label as _anomaly_label_fn
from ._anomaly import anomaly_likely_one_time, category_zscore_by_name

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])(-01)?$")

router = APIRouter(tags=["month-end"])


def _resolve_month(month: str | None) -> str:
    """Resolve target month to YYYY-MM-01 format.

    If month is None: auto-detect based on day of month.
    Days 1-5 default to previous month; otherwise current month.
    """
    if month:
        return month[:7] + "-01"

    now = datetime.now()
    if now.day <= 5:
        if now.month == 1:
            return f"{now.year - 1}-12-01"
        return f"{now.year}-{now.month - 1:02d}-01"
    return now.strftime("%Y-%m-01")


def _end_of_month(month_str: str) -> str:
    year = int(month_str[:4])
    month = int(month_str[5:7])
    last_day = monthrange(year, month)[1]
    return f"{year}-{month:02d}-{last_day:02d}"


def _prior_month(target: str) -> str:
    year = int(target[:4])
    mo = int(target[5:7])
    if mo == 1:
        return f"{year - 1}-12-01"
    return f"{year}-{mo - 1:02d}-01"


def _prior_months_list(target: str, n: int = 6) -> list[str]:
    months = []
    year = int(target[:4])
    mo = int(target[5:7])
    for _ in range(n):
        mo -= 1
        if mo == 0:
            mo = 12
            year -= 1
        months.append(f"{year}-{mo:02d}-01")
    return months


def _get_income_section(conn: sqlite3.Connection, target: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT income FROM budget_months WHERE month = ?",
        (target,),
    ).fetchone()

    income = float(row["income"] or 0) if row else 0.0
    prior = _prior_month(target)
    prior_row = conn.execute(
        "SELECT income FROM budget_months WHERE month = ?",
        (prior,),
    ).fetchone()
    prior_income = float(prior_row["income"] or 0) if prior_row else None

    return {
        "income": income,
        "prior_income": prior_income,
        "delta": round(income - prior_income, 2) if prior_income is not None else None,
    }


def _get_spending_section(conn: sqlite3.Connection, target: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT income, budgeted, to_be_budgeted FROM budget_months WHERE month = ?",
        (target,),
    ).fetchone()

    if not row:
        return {"rta": 0.0, "income": 0.0, "budgeted": 0.0, "activity": 0.0, "net": 0.0}

    income = float(row["income"] or 0)
    budgeted = float(row["budgeted"] or 0)
    rta = float(row["to_be_budgeted"] or 0)

    # Sum spending from budget_categories rather than budget_months.activity to avoid
    # double-counting CC payments. budget_months.activity includes Credit Card Payment
    # group activity, but the original charge already posted in the spending category.
    # hidden intentionally omitted: INSERT OR REPLACE propagates current hidden flag to historical rows
    cat_rows = conn.execute(
        """
        SELECT category_group_name, activity
        FROM budget_categories
        WHERE budget_month = ? AND deleted = 0 AND activity < 0
        """,
        (target,),
    ).fetchall()
    activity = sum(float(r["activity"] or 0) for r in cat_rows if not should_skip_group(r["category_group_name"] or ""))
    net = round(income + activity, 2)

    return {
        "rta": rta,
        "income": income,
        "budgeted": budgeted,
        "activity": round(-activity, 2),
        "net": net,
    }


def _category_zscore(conn: sqlite3.Connection, category_name: str, target: str) -> float | None:
    """Return z-score of current month's activity for a category vs 12 prior months."""
    return category_zscore_by_name(conn, category_name, target)


def _anomaly_label(z_score: float | None) -> str | None:
    """Return a human-readable anomaly label for a z-score, or None."""
    return _anomaly_label_fn(z_score)


def _get_overspent_section(conn: sqlite3.Connection, target: str) -> dict[str, Any]:
    # hidden intentionally omitted: INSERT OR REPLACE propagates current hidden flag to historical rows
    rows = conn.execute(
        """
        SELECT name, category_group_name, budgeted, activity, balance
        FROM budget_categories
        WHERE budget_month = ? AND deleted = 0
          AND balance < 0
        ORDER BY balance
        """,
        (target,),
    ).fetchall()

    if not rows:
        return {"count": 0, "total_overspent": 0.0, "categories": []}

    prior_months = _prior_months_list(target, 6)

    categories = []
    for row in rows:
        if should_skip_group(row["category_group_name"] or ""):
            continue
        name = row["name"]
        count = 0
        for pm in prior_months:
            pm_row = conn.execute(
                """
                SELECT balance FROM budget_categories
                WHERE budget_month = ? AND name = ? AND deleted = 0
                """,
                (pm, name),
            ).fetchone()
            if pm_row and (pm_row["balance"] or 0) < 0:
                count += 1

        z = _category_zscore(conn, name, target)
        if z is None:
            coverage = None
        elif anomaly_likely_one_time(z):
            coverage = "holding"
        else:
            coverage = "waterfall"
        categories.append(
            {
                "name": name,
                "group": row["category_group_name"],
                "budgeted": float(row["budgeted"] or 0),
                "activity": float(row["activity"] or 0),
                "balance": float(row["balance"] or 0),
                "structural": count >= 3,
                "z_score": round(z, 2) if z is not None else None,
                "anomaly_label": _anomaly_label(z),
                "coverage_suggestion": coverage,
            }
        )

    total_overspent = round(sum(abs(c["balance"]) for c in categories), 2)

    return {
        "count": len(categories),
        "total_overspent": total_overspent,
        "categories": categories,
    }


def _get_uncategorized_unapproved(conn: sqlite3.Connection, target: str, eom: str) -> dict[str, Any]:
    uncat = conn.execute(
        """
        SELECT COUNT(*) AS cnt FROM transactions t
        JOIN accounts a ON a.id = t.account_id
        WHERE t.deleted = 0
          AND a.on_budget = 1
          AND t.date >= ? AND t.date <= ?
          AND (t.category_name IS NULL OR t.category_name = '')
          AND t.transfer_account_id IS NULL
          AND t.payee_name != 'Split (Multiple Categories...)'
          AND t.payee_name NOT LIKE 'Transfer%'
          AND NOT EXISTS (
              SELECT 1 FROM subtransactions
              WHERE transaction_id = t.id AND deleted = 0
          )
        """,
        (target, eom),
    ).fetchone()["cnt"]

    unapp = conn.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM transactions t
        JOIN accounts a ON a.id = t.account_id
        WHERE t.deleted = 0 AND t.date >= ? AND t.date <= ?
          AND t.approved = 0
          AND a.on_budget = 1
        """,
        (target, eom),
    ).fetchone()["cnt"]

    pending = conn.execute(
        """
        SELECT COUNT(*) AS cnt FROM transactions t
        JOIN accounts a ON a.id = t.account_id
        WHERE t.deleted = 0 AND a.on_budget = 1
          AND t.date >= ? AND t.date <= ?
          AND t.cleared NOT IN ('cleared', 'reconciled')
        """,
        (target, eom),
    ).fetchone()["cnt"]

    return {
        "uncategorized": uncat,
        "unapproved": unapp,
        "pending": pending,
    }


def _get_credit_cards(conn: sqlite3.Connection, target: str) -> list[dict[str, Any]]:
    """Return CC accounts with balance and payment category budgeted amount."""
    cc_accounts = conn.execute(
        """
        SELECT a.id, a.name, a.balance
        FROM accounts a
        WHERE a.deleted = 0 AND a.type = 'creditCard' AND a.closed = 0
        ORDER BY a.balance
        """,
    ).fetchall()

    results = []
    for acc in cc_accounts:
        acc_name = acc["name"]
        # refs #153: filter by group on the first query to avoid matching a non-CC
        # category with the same name as the credit card account.
        payment_row = conn.execute(
            """
            SELECT budgeted, balance
            FROM budget_categories
            WHERE budget_month = ?
              AND category_group_name = 'Credit Card Payments'
              AND name = ?
              AND deleted = 0
            """,
            (target, acc_name),
        ).fetchone()

        payment_budgeted = float(payment_row["budgeted"] or 0) if payment_row else 0.0
        payment_balance = float(payment_row["balance"] or 0) if payment_row else 0.0
        balance = float(acc["balance"] or 0)

        results.append(
            {
                "name": acc_name,
                "balance": balance,
                "payment_budgeted": payment_budgeted,
                "payment_balance": payment_balance,
            }
        )

    return results


def _get_planned_expenses(conn: sqlite3.Connection, eom: str) -> list[dict[str, Any]]:
    month_str = eom[:7]

    rows = conn.execute(
        """
        SELECT id, category_name, amount, due_date, memo
        FROM planned_expenses
        WHERE status = 'active'
          AND strftime('%Y-%m', due_date) = ?
        ORDER BY due_date
        """,
        (month_str,),
    ).fetchall()

    latest_row = conn.execute("SELECT MAX(budget_month) AS m FROM budget_categories WHERE budgeted > 0").fetchone()
    latest_month = latest_row["m"] if latest_row and latest_row["m"] else None

    balance_map: dict[str, float] = {}
    # hidden intentionally omitted: INSERT OR REPLACE propagates current hidden flag to historical rows
    if latest_month:
        bal_rows = conn.execute(
            "SELECT name, balance FROM budget_categories WHERE budget_month = ? AND deleted = 0",
            (latest_month,),
        ).fetchall()
        for r in bal_rows:
            balance_map[r["name"]] = float(r["balance"] or 0)

    plans = []
    for row in rows:
        amount = float(row["amount"])
        cat_balance = balance_map.get(row["category_name"], 0.0)
        gap = max(0.0, amount - cat_balance)
        today = date.today().isoformat()
        plans.append(
            {
                "id": row["id"],
                "category_name": row["category_name"],
                "amount": round(amount, 2),
                "due_date": row["due_date"],
                "gap": round(gap, 2),
                "funded": gap == 0.0,
                "overdue": row["due_date"] < today,
                "memo": row["memo"],
            }
        )

    return plans


def _get_month_end(conn: sqlite3.Connection, target: str) -> dict[str, Any]:
    label = target[:7]
    eom = _end_of_month(target)

    income = _get_income_section(conn, target)
    spending = _get_spending_section(conn, target)
    overspent = _get_overspent_section(conn, target)
    attention = _get_uncategorized_unapproved(conn, target, eom)
    credit_cards = _get_credit_cards(conn, target)
    planned = _get_planned_expenses(conn, eom)

    total_cc_balance = round(sum(c["balance"] for c in credit_cards), 2)
    planned_total_gap = round(sum(p["gap"] for p in planned), 2)

    return {
        "month": label,
        "target": target,
        "income": income,
        "spending": spending,
        "overspent": overspent,
        "attention": attention,
        "credit_cards": credit_cards,
        "credit_card_total_balance": total_cc_balance,
        "planned_expenses": planned,
        "planned_total_gap": planned_total_gap,
    }


@router.get("/month-end")
def month_end(
    month: str | None = Query(
        default=None,
        description="YYYY-MM or YYYY-MM-01 format. Auto-detects if omitted (days 1-5 use prior month).",
    ),
) -> dict[str, Any]:
    """Consolidated month-end closeout: RTA, overspent, credit cards, planned expenses."""
    if month is not None and not _MONTH_RE.match(month):
        raise HTTPException(status_code=422, detail="month must be YYYY-MM or YYYY-MM-01 format")

    conn = get_connection()
    try:
        target = _resolve_month(month)
        return _get_month_end(conn, target)
    except sqlite3.OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database not ready. Run 'ynab sync' first. ({exc})",
        )
    finally:
        conn.close()
