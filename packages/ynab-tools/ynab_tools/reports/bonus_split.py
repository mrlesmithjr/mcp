"""Bonus split: separate the bonus portion of a large paycheck before paycheck_funding runs."""

import logging
import os
import sqlite3
from datetime import UTC, datetime, timedelta

from ..db import get_connection, init_db
from .funding import _dollars_to_milliunits, _find_category, _log_funding_change, _month_str
from .paycheck import _detect_income_sources, _get_paycheck_config, _get_recurring_inflows

logger = logging.getLogger(__name__)

# Amount above regular pay that triggers bonus detection.
BONUS_THRESHOLD_BUFFER = 1000.00

# How many days back to look for the most recent paycheck deposit.
_PAYCHECK_LOOKBACK_DAYS = 45

_HOLDING_DEFAULT = "Holding: Next Month"

# How many hours back to look when checking if Holding was recently funded.
_HOLDING_LOOKBACK_HOURS = 24


def _holding_name() -> str:
    from ..config import load_env

    load_env()
    return os.environ.get("YNAB_HOLDING_CATEGORY", _HOLDING_DEFAULT).strip() or _HOLDING_DEFAULT


def _get_recent_income_transactions(conn: sqlite3.Connection, days: int = _PAYCHECK_LOOKBACK_DAYS) -> list[dict]:
    """Get recent positive inflow transactions excluding transfers."""
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT id, date, amount, payee_name, memo, account_name, approved
            FROM transactions
            WHERE deleted = 0
              AND amount > 0
              AND transfer_account_id IS NULL
              AND date >= date('now', ? || ' days')
            ORDER BY date DESC, amount DESC
            """,
            (f"-{days}",),
        ).fetchall()
    ]


def _find_latest_paycheck(
    conn: sqlite3.Connection,
    regular_pay: float,
) -> dict | None:
    """Find the most recent paycheck deposit, including bonus-sized ones.

    Uses the same payee detection as paycheck_funding to identify the income source,
    then looks for the most recent transaction from that payee.

    Returns a transaction dict or None if nothing detected.
    """
    override_payees = _get_paycheck_config()
    inflows = _get_recurring_inflows(conn, months=12)
    if not inflows:
        return None

    sources = _detect_income_sources(inflows, override_payees)
    if not sources:
        return None

    primary = sources[0]
    payee = primary["payee"]

    # Get all recent transactions from this payee (including large bonus deposits)
    recent = _get_recent_income_transactions(conn, _PAYCHECK_LOOKBACK_DAYS)
    payee_lower = payee.lower()

    for txn in recent:
        txn_payee = (txn["payee_name"] or "").lower()
        if txn_payee and payee_lower in txn_payee or txn_payee in payee_lower:
            return txn

    # Fallback: any recent large inflow above regular pay threshold
    threshold = regular_pay - BONUS_THRESHOLD_BUFFER
    for txn in recent:
        if txn["amount"] >= threshold:
            return txn

    return None


def _check_holding_prefunded(
    conn: sqlite3.Connection,
    month: str,
    expected_bonus: float,
    hours: int = _HOLDING_LOOKBACK_HOURS,
) -> bool:
    """Check if Holding: Next Month was recently funded with the expected bonus amount.

    Returns True if a money_movements entry exists within the lookback window
    with an amount close to the expected bonus amount (within 10%).
    """
    # Strip timezone suffix before comparing: moved_at stores 'Z'-suffixed UTC
    # timestamps; Python isoformat() with UTC produces '+00:00'. Strip both to
    # naive UTC format so lexicographic comparison is correct.
    cutoff_dt = datetime.now(UTC) - timedelta(hours=hours)
    cutoff = cutoff_dt.strftime("%Y-%m-%dT%H:%M:%S")
    tolerance = max(expected_bonus * 0.10, 50.00)

    row = conn.execute(
        """
        SELECT id, amount, moved_at
        FROM money_movements
        WHERE to_category_name = ?
          AND month = ?
          AND amount >= ?
          AND replace(moved_at, 'Z', '') >= ?
          AND deleted = 0
        ORDER BY moved_at DESC
        LIMIT 1
        """,
        (_holding_name(), month, expected_bonus - tolerance, cutoff),
    ).fetchone()

    return row is not None


def _find_holding_category(conn: sqlite3.Connection, month: str) -> dict | None:
    """Find the Holding: Next Month category for the given budget month."""
    matches = _find_category(conn, _holding_name(), month)
    if matches:
        return matches[0]
    return None


def run_bonus_split(
    regular_pay: float | None = None,
    paycheck_amount: float | None = None,
    month: str | None = None,
    apply: bool = False,
) -> None:
    """Detect a bonus paycheck and move the bonus portion to Holding: Next Month.

    Steps:
    1. Auto-detect (or accept) the most recent large paycheck deposit
    2. Calculate bonus = total - regular_pay
    3. Show a preview of what will be moved
    4. Fund Holding: Next Month with the bonus amount (if apply=True)
    """
    if regular_pay is None:
        from ..config import load_env

        load_env()
        pay_str = os.environ.get("YNAB_REGULAR_PAY", "").strip()
        if not pay_str:
            print("Error: regular pay amount not configured.")
            print("Set YNAB_REGULAR_PAY in your .env or pass --regular-pay.")
            return
        regular_pay = float(pay_str)

    month = _month_str(month)
    conn = get_connection()
    try:
        init_db(conn)

        # Determine the paycheck total
        if paycheck_amount is not None:
            total = paycheck_amount
            source_label = f"${total:,.2f} (manually specified)"
        else:
            txn = _find_latest_paycheck(conn, regular_pay)
            if txn is None:
                print("No recent paycheck deposit detected.")
                print("Use --paycheck-amount to specify the total manually.")
                return
            total = txn["amount"]
            source_label = f"${total:,.2f} on {txn['date']} from {txn['payee_name'] or 'unknown payee'}"

        bonus_threshold = regular_pay + BONUS_THRESHOLD_BUFFER
        if total <= bonus_threshold:
            print(f"Paycheck detected: {source_label}")
            print(f"Regular pay threshold: ${bonus_threshold:,.2f}")
            print(f"This appears to be a normal paycheck (${total:,.2f}). No bonus split needed.")
            return

        bonus_portion = total - regular_pay

        print("Bonus Paycheck Detected")
        print("=" * 60)
        print(f"  Paycheck total:   ${total:,.2f}")
        print(f"  Regular pay:      ${regular_pay:,.2f}")
        print(f"  Bonus portion:    ${bonus_portion:,.2f}")
        print()
        print(f"  Action: Fund '{_holding_name()}' with ${bonus_portion:,.2f}")
        print(f"  Remaining for paycheck_funding: ${regular_pay:,.2f}")
        print()

        # Find the Holding category
        holding = _find_holding_category(conn, month)
        if holding is None:
            print(f"ERROR: Category '{_holding_name()}' not found for {month[:7]}.")
            print("Ensure the category exists in YNAB and run 'ynab sync' first.")
            print("To use a different category name, set YNAB_HOLDING_CATEGORY.")
            return

        current_budgeted = holding["budgeted"] or 0.0
        new_budgeted = current_budgeted + bonus_portion

        print(f"  {holding['name']}  ({holding['category_group_name']})")
        print(f"  {month[:7]}:  ${current_budgeted:,.2f} -> ${new_budgeted:,.2f}  (+${bonus_portion:,.2f})")
        print()

        if not apply:
            try:
                confirm = input("Apply bonus split? [y/N] ").strip().lower()
            except EOFError:
                confirm = "n"
            if confirm != "y":
                print("Cancelled. Use --apply to skip confirmation.")
                return

        from ..client import YNABClient
        from ..config import require_credentials

        token, plan_id = require_credentials()
        client = YNABClient(token, plan_id)

        result = client.update_category_budget(month, holding["id"], _dollars_to_milliunits(new_budgeted))

        _log_funding_change(
            conn,
            holding["id"],
            holding["name"],
            holding["category_group_name"],
            month,
            current_budgeted,
            new_budgeted,
            "bonus-split",
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
                    holding["id"],
                    month,
                ),
            )
        conn.commit()

        print(f"Done. Bonus split applied: ${bonus_portion:,.2f} -> '{_holding_name()}'")
        print(f"Run paycheck_funding to allocate the regular pay (${regular_pay:,.2f}).")

    finally:
        conn.close()


def check_bonus_paycheck_guard(
    conn: sqlite3.Connection,
    month: str,
    regular_pay: float | None = None,
) -> dict:
    """Check for a bonus-sized paycheck and whether the bonus was already split.

    Returns a dict:
      {
        "is_bonus": bool,         # True if a bonus-sized deposit was detected
        "total": float,           # Total paycheck amount (0 if no paycheck found)
        "regular_pay": float,     # Regular pay amount
        "bonus_portion": float,   # Calculated bonus portion (0 if not bonus)
        "holding_prefunded": bool, # True if Holding was recently funded
        "payee": str,             # Detected payee name
        "date": str,              # Transaction date
      }
    """
    if regular_pay is None:
        from ..config import load_env

        load_env()
        pay_str = os.environ.get("YNAB_REGULAR_PAY", "").strip()
        regular_pay = float(pay_str) if pay_str else 0.0

    bonus_threshold = regular_pay + BONUS_THRESHOLD_BUFFER

    txn = _find_latest_paycheck(conn, regular_pay)
    if txn is None:
        return {
            "is_bonus": False,
            "total": 0.0,
            "regular_pay": regular_pay,
            "bonus_portion": 0.0,
            "holding_prefunded": False,
            "payee": "",
            "date": "",
        }

    total = txn["amount"]
    is_bonus = total > bonus_threshold
    bonus_portion = (total - regular_pay) if is_bonus else 0.0
    holding_prefunded = False

    if is_bonus:
        holding_prefunded = _check_holding_prefunded(conn, month, bonus_portion)

    return {
        "is_bonus": is_bonus,
        "total": total,
        "regular_pay": regular_pay,
        "bonus_portion": bonus_portion,
        "holding_prefunded": holding_prefunded,
        "payee": txn.get("payee_name") or "",
        "date": txn.get("date") or "",
    }
