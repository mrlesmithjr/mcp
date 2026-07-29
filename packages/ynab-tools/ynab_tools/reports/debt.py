"""Debt status: account balances, payments, payoff estimates."""

import json
import logging
import math
import sqlite3

from ..db import get_connection, init_db

logger = logging.getLogger(__name__)

# Account types that represent debt
DEBT_ACCOUNT_TYPES = (
    "autoLoan",
    "otherLiability",
    "mortgage",
    "personalLoan",
    "studentLoan",
    "medicalDebt",
    "otherDebt",
)


def _get_debt_accounts(conn: sqlite3.Connection) -> list[dict]:
    """Get all open debt accounts (loans, mortgage, etc.) with debt details."""
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT name, type, balance, debt_interest_rates,
                   debt_minimum_payments, debt_original_balance,
                   debt_escrow_amounts, last_reconciled_at
            FROM accounts
            WHERE deleted = 0 AND closed = 0
              AND type IN ({})
            ORDER BY balance ASC
        """.format(",".join("?" * len(DEBT_ACCOUNT_TYPES))),
            DEBT_ACCOUNT_TYPES,
        ).fetchall()
    ]


def _get_credit_cards_with_balance(conn: sqlite3.Connection) -> list[dict]:
    """Get credit cards carrying a balance (not paid in full)."""
    return [
        dict(row)
        for row in conn.execute("""
            SELECT name, balance, debt_interest_rates
            FROM accounts
            WHERE deleted = 0 AND closed = 0
              AND type = 'creditCard'
              AND balance < 0
            ORDER BY balance ASC
        """).fetchall()
    ]


def _get_avg_monthly_payments(conn: sqlite3.Connection) -> dict[str, float]:
    """Get average monthly payment by account over last 6 months."""
    rows = conn.execute("""
        SELECT account_name,
               AVG(monthly_total) AS avg_payment
        FROM (
            SELECT account_name,
                   strftime('%Y-%m', date) AS month,
                   SUM(amount) AS monthly_total
            FROM transactions
            WHERE deleted = 0
              AND amount > 0
              AND date >= date('now', '-6 months')
            GROUP BY account_name, month
        )
        GROUP BY account_name
    """).fetchall()
    return {row["account_name"]: row["avg_payment"] for row in rows}


def _get_interest_charges(conn: sqlite3.Connection, months: int = 6) -> dict[str, float]:
    """Get total interest charges by account over recent months."""
    rows = conn.execute(
        """
        SELECT account_name,
               SUM(ABS(amount)) AS total_interest,
               COUNT(*) AS charge_count
        FROM transactions
        WHERE deleted = 0
          AND debt_transaction_type = 'interest'
          AND date >= date('now', ? || ' months')
        GROUP BY account_name
    """,
        (f"-{months}",),
    ).fetchall()
    return {row["account_name"]: row["total_interest"] for row in rows}


def _parse_latest_rate(rates_json: str | None) -> float | None:
    """Extract the most recent interest rate from YNAB's JSON format.

    YNAB stores rates as {"YYYY-MM-DD": rate_in_millipercent, ...}.
    Rate 8400 = 8.400% APR.
    """
    if not rates_json:
        return None
    try:
        rates = json.loads(rates_json)
        if not rates:
            return None
        latest_date = max(rates.keys())
        rate_milli = rates[latest_date]
        return rate_milli / 1000.0  # Convert millipercent to percent
    except (json.JSONDecodeError, ValueError):
        return None


def _parse_latest_payment(payments_json: str | None) -> float | None:
    """Extract the most recent minimum payment from YNAB's JSON format.

    YNAB stores payments as {"YYYY-MM-DD": amount_in_milliunits, ...}.
    """
    if not payments_json:
        return None
    try:
        payments = json.loads(payments_json)
        if not payments:
            return None
        latest_date = max(payments.keys())
        return payments[latest_date] / 1000.0  # milliunits to dollars
    except (json.JSONDecodeError, ValueError):
        return None


def _estimate_payoff_months(balance: float, monthly_payment: float, apr: float | None) -> float | None:
    """Estimate months to payoff with interest.

    Uses amortization formula when APR is known, otherwise simple division.
    """
    if monthly_payment <= 0:
        return None

    if apr is None or apr == 0:
        return balance / monthly_payment

    monthly_rate = apr / 100.0 / 12.0
    # If payment doesn't cover interest, it'll never pay off
    interest_only = balance * monthly_rate
    if monthly_payment <= interest_only:
        return None

    # Standard amortization: n = -log(1 - r*P/M) / log(1+r)
    try:
        n = -math.log(1 - monthly_rate * balance / monthly_payment) / math.log(1 + monthly_rate)
        return n
    except (ValueError, ZeroDivisionError):
        return balance / monthly_payment


def run_debt_status() -> None:
    """Show all debt accounts with balances, payments, and payoff estimates."""
    conn = get_connection()
    try:
        init_db(conn)

        debts = _get_debt_accounts(conn)
        credit_cards = _get_credit_cards_with_balance(conn)
        payments = _get_avg_monthly_payments(conn)
        interest = _get_interest_charges(conn)

        if not debts and not credit_cards:
            print("No debt accounts found!")
            return

        print("Debt Status Report")
        print("=" * 85)
        print()

        total_debt = 0.0
        total_monthly = 0.0

        if debts:
            print(f"  {'Account':<33} {'Balance':>11} {'Payment':>10} {'APR':>7} {'Months':>7} {'Interest':>9}")
            print(f"  {'-' * 33} {'-' * 11} {'-' * 10} {'-' * 7} {'-' * 7} {'-' * 9}")

            for d in debts:
                balance = abs(d["balance"] or 0)
                total_debt += balance

                apr = _parse_latest_rate(d.get("debt_interest_rates"))
                min_pmt = _parse_latest_payment(d.get("debt_minimum_payments"))
                avg_pmt = payments.get(d["name"], 0)
                pmt = avg_pmt if avg_pmt > 0 else (min_pmt or 0)
                total_monthly += pmt

                months_left = _estimate_payoff_months(balance, pmt, apr)
                int_paid = interest.get(d["name"], 0)

                # Format fields
                apr_str = f"{apr:.2f}%" if apr else "-"
                months_str = f"{months_left:.0f}" if months_left else "-"
                if months_left and months_left <= 6:
                    months_str += " !"
                int_str = f"${int_paid:,.0f}" if int_paid > 0 else "-"

                name = d["name"]
                if len(name) > 33:
                    name = name[:30] + "..."

                print(f"  {name:<33} ${balance:>9,.2f}  ${pmt:>8,.2f}  {apr_str:>7}  {months_str:>6}  {int_str:>9}")

            print(f"  {'-' * 33} {'-' * 11} {'-' * 10}")
            print(f"  {'Total Loans':<33} ${total_debt:>9,.2f}  ${total_monthly:>8,.2f}")
            print()

        if credit_cards:
            print("Credit Cards with Balance:")
            print(f"  {'-' * 33} {'-' * 11} {'-' * 7}")
            cc_total = 0.0
            for cc in credit_cards:
                balance = abs(cc["balance"] or 0)
                cc_total += balance
                apr = _parse_latest_rate(cc.get("debt_interest_rates"))
                apr_str = f"{apr:.2f}%" if apr else "-"
                name = cc["name"]
                if len(name) > 33:
                    name = name[:30] + "..."
                print(f"  {name:<33} ${balance:>9,.2f}  {apr_str:>7}")
            print(f"  {'-' * 33} {'-' * 11}")
            print(f"  {'Total CC Balance':<33} ${cc_total:>9,.2f}")
            total_debt += cc_total
            print()

        print(f"Total Debt: ${total_debt:>,.2f}")
        if total_monthly > 0:
            print(f"Total Monthly Debt Service: ${total_monthly:>,.2f}")

        # Total interest paid
        total_interest = sum(interest.values())
        if total_interest > 0:
            print(f"Interest Paid (last 6 months): ${total_interest:>,.2f}")

        # Flag upcoming payoffs
        near_payoff = []
        for d in debts:
            balance = abs(d["balance"] or 0)
            apr = _parse_latest_rate(d.get("debt_interest_rates"))
            avg_pmt = payments.get(d["name"], 0)
            min_pmt = _parse_latest_payment(d.get("debt_minimum_payments"))
            pmt = avg_pmt if avg_pmt > 0 else (min_pmt or 0)
            months = _estimate_payoff_months(balance, pmt, apr)
            if months and months <= 6:
                near_payoff.append((d["name"], months, pmt))

        if near_payoff:
            print("\nUpcoming Payoffs (<6 months):")
            for name, months_left, pmt in near_payoff:
                print(f"  {name}: ~{months_left:.0f} months (frees ${pmt:,.2f}/mo)")
    finally:
        conn.close()


def _format_type(acct_type: str) -> str:
    """Format YNAB account type for display."""
    type_map = {
        "autoLoan": "Auto Loan",
        "mortgage": "Mortgage",
        "otherLiability": "Other",
        "personalLoan": "Personal",
        "studentLoan": "Student",
        "medicalDebt": "Medical",
        "otherDebt": "Other",
    }
    return type_map.get(acct_type, acct_type)
