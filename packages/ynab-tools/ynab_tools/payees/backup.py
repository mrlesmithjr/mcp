"""Backup and restore for payee audit fixes."""

import json
import logging
import sys
from datetime import datetime

from ..config import BACKUPS_DIR, atomic_write_json

logger = logging.getLogger(__name__)


def create_backup(fixes: list[dict]) -> str:
    """Create a backup file before applying fixes. Returns the filename."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"backup_{timestamp}.json"

    backup_data = {
        "created": datetime.now().isoformat(),
        "transaction_count": len(fixes),
        "transactions": [
            {
                "ynab_transaction_id": f["ynab_transaction_id"],
                "date": f["date"],
                "amount": f["amount"],
                "original_payee": f["payee_name"],
                "new_payee": f["correct_payee"],
                "original_category": f["category_name"],
                "new_category": f.get("correct_category"),
            }
            for f in fixes
        ],
    }

    # Atomic write (temp-file + os.replace): a crash mid-write must never
    # leave a truncated/invalid backup file (issue #78).
    atomic_write_json(BACKUPS_DIR / filename, backup_data, dir_mode=0o700)
    return filename


def list_backups() -> list[dict]:
    """List all available backup files."""
    if not BACKUPS_DIR.exists():
        return []

    backups = []
    for filepath in sorted(BACKUPS_DIR.glob("*.json"), reverse=True):
        try:
            with open(filepath) as fh:
                data = json.load(fh)
            backups.append(
                {
                    "filename": filepath.name,
                    "created": data.get("created", "unknown"),
                    "count": data.get("transaction_count", 0),
                }
            )
        except (json.JSONDecodeError, KeyError):
            backups.append({"filename": filepath.name, "created": "error", "count": 0})
    return backups


def load_backup(filename: str) -> dict:
    """Load a backup file by name."""
    filepath = (BACKUPS_DIR / filename).resolve()
    if not filepath.is_relative_to(BACKUPS_DIR.resolve()):
        raise ValueError(f"Invalid backup filename: {filename}")
    if not filepath.exists():
        raise FileNotFoundError(f"Backup not found: {filename}")
    try:
        with open(filepath) as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise ValueError(f"Corrupt backup file {filename}: {e}") from e


def bulk_apply(client: object, updates: list[dict]) -> None:
    """Apply updates in batches of 1000."""
    success_total = 0
    failed_total = 0

    for i in range(0, len(updates), 1000):
        batch = updates[i : i + 1000]
        result = client.bulk_update_transactions(batch)
        success_total += result["success"]
        failed_total += result["failed"]
        if len(updates) > 1000:
            print(f"  Batch {i // 1000 + 1}: {result['success']} success, {result['failed']} failed")

    print(f"  Success: {success_total}, Failed: {failed_total}")


def cmd_list_backups() -> None:
    """Print all available backup files."""
    print("Available Backups")
    print("=" * 40)
    print()

    backups = list_backups()
    if not backups:
        print("No backups found.")
        print(f"Backup location: {BACKUPS_DIR}")
        return

    for b in backups:
        print(f"  {b['filename']}")
        print(f"    Created: {b['created']}")
        print(f"    Transactions: {b['count']}")
        print()


def cmd_restore(backup_file: str, client: object) -> None:
    """Restore transactions from a backup file via the YNAB API."""
    print("YNAB Payee Restore")
    print("=" * 40)
    print()

    try:
        backup = load_backup(backup_file)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        print("\nAvailable backups:")
        for b in list_backups():
            print(f"  - {b['filename']}")
        return

    transactions = backup.get("transactions", [])
    if not transactions:
        print("Backup contains no transactions.")
        return

    print(f"Backup: {backup_file}")
    print(f"Created: {backup.get('created', 'unknown')}")
    print(f"Transactions: {len(transactions)}")
    print()

    by_restore: dict[tuple, list] = {}
    for t in transactions:
        key = (t["new_payee"], t["original_payee"])
        by_restore.setdefault(key, []).append(t)

    print("This will restore original payee names:")
    for (new, original), txns in sorted(by_restore.items(), key=lambda x: -len(x[1])):
        print(f"  {new} → {original}: {len(txns)} transactions")

    print()
    confirm = input("Restore these transactions? [y/N]: ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        return

    updates = [{"id": t["ynab_transaction_id"], "payee_name": t["original_payee"]} for t in transactions]

    print("\nRestoring...")
    bulk_apply(client, updates)
    print("\nRestore complete! Run 'ynab sync' to update local data.")
