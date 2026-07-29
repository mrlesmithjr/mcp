"""Income analysis: monthly breakdown, regular pay vs bonus, YTD comparison."""

import logging
import os
import sqlite3
from datetime import datetime

from ..config import load_env
from ..db import get_connection, init_db

logger = logging.getLogger(__name__)


def _get_income_config() -> tuple[float | None, float | None]:
    """Load income configuration from env vars.

    Set in .env:
      YNAB_REGULAR_PAY=5000.00     # Your typical semi-monthly paycheck
      YNAB_BONUS_THRESHOLD=8000.00 # Paychecks above this are flagged as bonus
    """
    load_env()
    regular = os.environ.get("YNAB_REGULAR_PAY", "").strip()
    threshold = os.environ.get("YNAB_BONUS_THRESHOLD", "").strip()
    regular_pay = float(regular) if regular else None
    bonus_threshold = float(threshold) if threshold else None
    return regular_pay, bonus_threshold


def _get_monthly_income(conn: sqlite3.Connection, months: int = 12) -> list[dict]:
    """Get monthly income totals from budget_months."""
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT month, income
            FROM budget_months
            ORDER BY month DESC
            LIMIT ?
        """,
            (months,),
        ).fetchall()
    ]


def _get_paychecks(conn: sqlite3.Connection, months: int = 12) -> list[dict]:
    """Get individual paycheck transactions.

    Matches transactions in any income/inflow category where the payee
    looks like an employer (configured via YNAB_PAYCHECK_PAYEES env var,
    comma-separated, partial match). Falls back to the 'Income: Paychecks'
    category if no payee config is set.
    """
    load_env()
    payee_config = os.environ.get("YNAB_PAYCHECK_PAYEES", "").strip()

    if payee_config:
        # Match by payee name against configured employer names
        payee_names = [p.strip() for p in payee_config.split(",") if p.strip()]
        payee_clauses = " OR ".join(["t.payee_name LIKE ?"] * len(payee_names))
        payee_params = [f"%{p}%" for p in payee_names]
        return [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT t.date, t.payee_name, t.amount, t.memo
                FROM transactions t
                JOIN accounts a ON a.id = t.account_id
                WHERE t.deleted = 0
                  AND a.on_budget = 1
                  AND t.amount > 0
                  AND (t.category_name LIKE 'Income:%'
                       OR t.category_name LIKE 'Inflow:%')
                  AND ({payee_clauses})
                  AND t.date >= date('now', ? || ' months')
                ORDER BY t.date DESC
            """,
                (*payee_params, f"-{months}"),
            ).fetchall()
        ]

    # Fallback: match by category only
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT t.date, t.payee_name, t.amount, t.memo
            FROM transactions t
            JOIN accounts a ON a.id = t.account_id
            WHERE t.deleted = 0
              AND a.on_budget = 1
              AND t.category_name = 'Income: Paychecks'
              AND t.date >= date('now', ? || ' months')
            ORDER BY t.date DESC
        """,
            (f"-{months}",),
        ).fetchall()
    ]


def _get_other_income(conn: sqlite3.Connection, months: int = 12, paycheck_ids: set | None = None) -> list[dict]:
    """Get non-paycheck income transactions (interest, refunds, rewards, etc.)."""
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT t.date, t.payee_name, t.amount, t.category_name, t.memo
            FROM transactions t
            JOIN accounts a ON a.id = t.account_id
            WHERE t.deleted = 0
              AND a.on_budget = 1
              AND t.amount > 0
              AND (t.category_name LIKE 'Income:%'
                   OR t.category_name LIKE 'Inflow:%')
              AND t.transfer_account_id IS NULL
              AND t.date >= date('now', ? || ' months')
            ORDER BY t.date DESC
        """,
            (f"-{months}",),
        ).fetchall()
    ]


def run_income(months: int = 12) -> None:
    """Show income breakdown: regular pay, bonus portions, other income."""
    regular_pay, bonus_threshold = _get_income_config()
    has_bonus_config = regular_pay is not None and bonus_threshold is not None

    conn = get_connection()
    try:
        init_db(conn)

        monthly = _get_monthly_income(conn, months)
        paychecks = _get_paychecks(conn, months)
        all_income = _get_other_income(conn, months)
        # Exclude paycheck transactions from "other" by matching date+payee+amount
        paycheck_keys = {(p["date"], p["payee_name"], p["amount"]) for p in paychecks}
        other = [t for t in all_income if (t["date"], t["payee_name"], t["amount"]) not in paycheck_keys]

        if not monthly:
            print("No budget data found. Run 'ynab sync' first.")
            return

        print("Income Report")
        print("=" * 80)

        # ── Individual paychecks with bonus detection ──
        if paychecks and has_bonus_config:
            bonus_checks = [p for p in paychecks if p["amount"] > bonus_threshold]
            if bonus_checks:
                print(f"\nBonus Paychecks ({len(bonus_checks)} found):")
                print(f"  {'Date':<12} {'Payee':<30} {'Total':>10} {'Regular':>10} {'Bonus':>10}")
                print("  " + "-" * 74)
                for p in bonus_checks:
                    bonus = p["amount"] - regular_pay
                    memo = f"  ({p['memo']})" if p["memo"] else ""
                    pay = f"${p['amount']:>9,.2f} ${regular_pay:>9,.2f} ${bonus:>9,.2f}"
                    print(f"  {p['date']:<12} {p['payee_name']:<30} {pay}{memo}")
                print()

        # ── Monthly summary ──
        # Build per-month breakdown from transactions
        month_data: dict[str, dict] = {}
        for p in paychecks:
            m = p["date"][:7]
            if m not in month_data:
                month_data[m] = {"regular": 0.0, "bonus": 0.0, "other": 0.0}
            if has_bonus_config and p["amount"] > bonus_threshold:
                month_data[m]["regular"] += regular_pay
                month_data[m]["bonus"] += p["amount"] - regular_pay
            else:
                month_data[m]["regular"] += p["amount"]

        for o in other:
            m = o["date"][:7]
            if m not in month_data:
                month_data[m] = {"regular": 0.0, "bonus": 0.0, "other": 0.0}
            month_data[m]["other"] += o["amount"]

        print("Monthly Income Breakdown:")
        print(f"  {'Month':<10} {'Regular':>10} {'Bonus':>10} {'Other':>10} {'Total':>10}")
        print("  " + "-" * 54)

        sorted_months = sorted(month_data.keys(), reverse=True)
        ytd_regular = 0.0
        ytd_bonus = 0.0
        ytd_other = 0.0
        current_year = datetime.now().strftime("%Y")

        for m in sorted_months:
            d = month_data[m]
            total = d["regular"] + d["bonus"] + d["other"]
            other_str = f"${d['other']:>9,.2f}" if d["other"] else "         -"
            bonus_str = f"${d['bonus']:>9,.2f}" if d["bonus"] else "         -"
            print(f"  {m:<10} ${d['regular']:>9,.2f} {bonus_str} {other_str} ${total:>9,.2f}")

            if m.startswith(current_year):
                ytd_regular += d["regular"]
                ytd_bonus += d["bonus"]
                ytd_other += d["other"]

        print("  " + "-" * 54)

        # Averages
        if sorted_months:
            avg_regular = sum(month_data[m]["regular"] for m in sorted_months) / len(sorted_months)
            avg_total = sum(
                month_data[m]["regular"] + month_data[m]["bonus"] + month_data[m]["other"] for m in sorted_months
            ) / len(sorted_months)
            print(f"  {'Average':<10} ${avg_regular:>9,.2f} {'':>11} {'':>11} ${avg_total:>9,.2f}")

        # YTD
        ytd_total = ytd_regular + ytd_bonus + ytd_other
        if ytd_total > 0:
            ytd_line = f"${ytd_regular:>9,.2f} ${ytd_bonus:>9,.2f} ${ytd_other:>9,.2f} ${ytd_total:>9,.2f}"
            print(f"\n  {current_year} YTD:    {ytd_line}")

            # Compare to prior year same period
            prior_year = str(int(current_year) - 1)
            current_month = int(datetime.now().strftime("%m"))
            prior_ytd = 0.0
            for m in sorted_months:
                if m.startswith(prior_year) and int(m[5:7]) <= current_month:
                    d = month_data[m]
                    prior_ytd += d["regular"] + d["bonus"] + d["other"]

            if prior_ytd > 0:
                change = ytd_total - prior_ytd
                pct = change / prior_ytd * 100
                direction = "UP" if change > 0 else "DOWN"
                print(f"  {prior_year} YTD:    {'':>11} {'':>11} {'':>11} ${prior_ytd:>9,.2f}")
                print(f"  Change:     {'':>11} {'':>11} {'':>11} ${change:>+9,.2f} ({direction} {abs(pct):.1f}%)")

        # ── Bonus pattern detection ──
        if not has_bonus_config:
            if not regular_pay or not bonus_threshold:
                print("\n  Set YNAB_REGULAR_PAY and YNAB_BONUS_THRESHOLD in .env for bonus detection")
            return
        bonus_months = sorted(set(p["date"][:7] for p in paychecks if p["amount"] > bonus_threshold))
        if len(bonus_months) >= 2:
            print("\nBonus Pattern:")
            print(f"  Months with bonuses: {', '.join(bonus_months)}")
            # Detect quarterly pattern
            bonus_month_nums = [int(m[5:7]) for m in bonus_months]
            if len(set(bonus_month_nums)) >= 2:
                # Find most common month-of-quarter pattern
                quarters = [((n - 1) % 3) + 1 for n in bonus_month_nums]
                from collections import Counter

                common_pos = Counter(quarters).most_common(1)[0][0]
                month_names = {1: "Jan/Apr/Jul/Oct", 2: "Feb/May/Aug/Nov", 3: "Mar/Jun/Sep/Dec"}
                if len(set(quarters)) <= 2:
                    qtr_name = month_names.get(common_pos, "")
                    print(f"  Quarterly pattern detected: typically month {common_pos} of quarter ({qtr_name})")

                # Next expected
                now = datetime.now()
                for offset in range(1, 5):
                    future_month = (now.month - 1 + offset) % 12 + 1
                    future_pos = ((future_month - 1) % 3) + 1
                    if future_pos == common_pos:
                        future_year = now.year + ((now.month - 1 + offset) // 12)
                        print(f"  Next expected bonus: {future_year}-{future_month:02d}")
                        break
    finally:
        conn.close()
