"""Orphaned payee detection."""

import sqlite3

from ..db import get_payee_ids_with_transactions
from .names import SYSTEM_PAYEES


def cmd_list_orphaned(conn: sqlite3.Connection, client: object, use_api: bool = False) -> None:
    """List payees with zero transactions."""
    print("YNAB Orphaned Payees")
    print("=" * 40)
    print()

    print("Fetching payees from YNAB...")
    raw_payees = client.get_payees()["payees"]
    system_set = set(SYSTEM_PAYEES)
    user_payees = [
        {"id": p["id"], "name": p["name"]}
        for p in raw_payees
        if not p.get("deleted", False) and not p["name"].startswith("Transfer") and p["name"] not in system_set
    ]
    print(f"Total user payees: {len(user_payees)}")

    if use_api:
        print("Fetching all transactions from YNAB API...")
        transactions = client.get_transactions()["transactions"]
        payee_ids_with_txns = {t["payee_id"] for t in transactions if not t.get("deleted", False) and t.get("payee_id")}
        print(f"Payees with transactions: {len(payee_ids_with_txns)}")
    else:
        print("Checking transactions in local database...")
        payee_ids_with_txns = get_payee_ids_with_transactions(conn)
        print(f"Payees with transactions: {len(payee_ids_with_txns)}")

    orphaned = [p for p in user_payees if p["id"] not in payee_ids_with_txns]

    print()
    print("-" * 40)

    if not orphaned:
        print("No orphaned payees found!")
        return

    print(f"Found {len(orphaned)} orphaned payees:")
    print()
    for p in sorted(orphaned, key=lambda x: x["name"].lower()):
        print(f"  - {p['name']}")

    print(f"\n{'-' * 40}")
    print("These payees have no transactions and can be deleted.")
    print("\nTo clean up in YNAB:")
    print("  1. Open your budget → any account")
    print("  2. Click the payee dropdown → 'Manage Payees'")
    print("  3. Find and delete the orphaned payees")
    print("\nNote: The YNAB API does not support deleting payees.")
