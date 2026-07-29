"""Reconcile account balances by creating adjustment transactions via YNAB API."""

import logging
from datetime import datetime

from ..client import YNABClient
from ..config import require_credentials
from ..db import find_account, get_connection, init_db

logger = logging.getLogger(__name__)


def _dollars_to_milliunits(dollars: float) -> int:
    """Convert dollar amount to YNAB milliunits."""
    return round(dollars * 1000)


def run_reconcile(account: str, balance: float, apply: bool = False) -> None:
    """Reconcile an account to a target balance by creating an adjustment transaction."""
    conn = get_connection()
    try:
        init_db(conn)

        acct = find_account(conn, account)
        if not acct:
            print(f"No account found matching '{account}'")
            return

        current = acct["balance"]
        delta = balance - current

        print(f"Account:  {acct['name']}")
        print(f"Type:     {acct['type']}")
        print(f"Current:  ${current:>12,.2f}")
        print(f"Target:   ${balance:>12,.2f}")
        print(f"Delta:    ${delta:>12,.2f}")

        if acct["last_reconciled_at"]:
            print(f"Last reconciled: {acct['last_reconciled_at'][:10]}")

        if abs(delta) < 0.01:
            print("\nBalance is already correct - no adjustment needed.")
            return

        print()

        if not apply:
            print("This is a preview. To apply:")
            print(f'  ynab reconcile "{account}" {balance} --apply')
            return

        today = datetime.now().strftime("%Y-%m-%d")
        txn = {
            "account_id": acct["id"],
            "date": today,
            "amount": _dollars_to_milliunits(delta),
            "payee_name": "Balance Adjustment (ynab-tools)",
            "memo": f"Reconciled from ${current:,.2f} to ${balance:,.2f}",
            "cleared": "reconciled",
            "approved": True,
        }

        token, plan_id = require_credentials()
        client = YNABClient(token, plan_id)

        print(f"Creating adjustment transaction for ${delta:,.2f}...")
        result = client.create_transaction(txn)

        if result:
            print(f"  Transaction created: {result.get('id', 'OK')}")
            print(f"  New balance: ${balance:,.2f}")
            print("\nRun 'ynab sync' to update local data.")
        else:
            print("  Failed to create transaction. Check logs.")

    finally:
        conn.close()


def run_reconcile_list() -> None:
    """Show accounts with stale reconciliation dates."""
    conn = get_connection()
    try:
        init_db(conn)

        rows = conn.execute("""
            SELECT name, type, balance, last_reconciled_at,
                   ROUND(julianday('now') - julianday(
                       COALESCE(last_reconciled_at, '2020-01-01')
                   )) AS days_stale
            FROM accounts
            WHERE deleted = 0 AND closed = 0 AND balance != 0
            ORDER BY days_stale DESC
        """).fetchall()

        print("Account Reconciliation Status")
        print("=" * 80)
        print(f"  {'Account':<40} {'Balance':>12} {'Last Reconciled':>17} {'Days':>6}")
        print(f"  {'-' * 40} {'-' * 12} {'-' * 17} {'-' * 6}")

        for r in rows:
            last = r["last_reconciled_at"][:10] if r["last_reconciled_at"] else "never"
            days = int(r["days_stale"]) if r["days_stale"] else 999
            marker = " !" if days > 30 else ""
            name = r["name"]
            if len(name) > 40:
                name = name[:37] + "..."
            print(f"  {name:<40} ${r['balance']:>11,.2f} {last:>17} {days:>5}{marker}")

    finally:
        conn.close()
