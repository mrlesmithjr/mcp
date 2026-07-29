"""Payee normalization: find and merge duplicate payee names."""

import logging
from datetime import datetime

from ..config import BACKUPS_DIR, atomic_write_json
from .names import find_duplicate_payees

logger = logging.getLogger(__name__)


def cmd_normalize(client: object, apply: bool = False) -> None:
    """Find and optionally fix duplicate/inconsistent payee names."""
    print("YNAB Payee Normalization")
    print("=" * 40)
    print()

    print("Fetching payees...")
    raw_payees = client.get_payees()["payees"]
    payees = [{"id": p["id"], "name": p["name"]} for p in raw_payees if not p.get("deleted", False)]
    print(f"Total payees: {len(payees)}")

    print("Analyzing for duplicates...")
    duplicates = find_duplicate_payees(payees)

    if not duplicates:
        print("\nNo duplicate payees found!")
        return

    print(f"Found {len(duplicates)} groups with duplicates")
    print()

    sorted_groups = sorted(duplicates.items(), key=lambda x: -len(x[1]))
    total_duplicates = 0

    print("Duplicate Payee Groups:")
    print("-" * 40)
    for canonical, payee_list in sorted_groups[:50]:
        total_duplicates += len(payee_list) - 1
        print(f"\n  → {canonical}")
        for p in payee_list:
            marker = "  " if p["name"] == canonical else "  *"
            print(f"    {marker} {p['name']}")

    if len(sorted_groups) > 50:
        print(f"\n  ... and {len(sorted_groups) - 50} more groups")

    print(f"\n{'-' * 40}")
    print(f"Total duplicate entries: {total_duplicates}")
    print(f"Potential payees to merge: {len(duplicates)} groups")

    if not apply:
        print("\nThis was a preview. To apply, run:")
        print("  ynab payee normalize --apply")
        return

    confirm = input("\nApply payee normalization? [y/N]: ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        return

    # Collect renames: (old_name, old_id) → canonical
    renames = []
    for canonical, payee_list in sorted_groups:
        for payee in payee_list:
            if payee["name"] != canonical:
                renames.append({"id": payee["id"], "old_name": payee["name"], "new_name": canonical})

    if not renames:
        print("\nNo payees need renaming.")
        return

    print(f"\nPayee renames to apply ({len(renames)} API calls):")
    for r in renames:
        print(f"  {r['old_name']} → {r['new_name']}")

    confirm2 = input(f"\nProceed with renaming {len(renames)} payees? [y/N]: ").strip().lower()
    if confirm2 != "y":
        print("Aborted.")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_data = {
        "created": datetime.now().isoformat(),
        "type": "normalization",
        "payee_count": len(renames),
        "renames": [{"payee_id": r["id"], "old_name": r["old_name"], "new_name": r["new_name"]} for r in renames],
    }
    backup_file = BACKUPS_DIR / f"normalize_backup_{timestamp}.json"
    try:
        # Atomic write (temp-file + os.replace): a crash mid-write must never
        # leave a truncated/invalid backup file (issue #78).
        atomic_write_json(backup_file, backup_data, dir_mode=0o700)
    except OSError as e:
        print(f"Error: Failed to write backup: {e}")
        return
    print(f"\nBackup saved: {backup_file.name}")

    print("\nRenaming payees...")
    success = 0
    failed = 0
    for r in renames:
        if client.update_payee(r["id"], r["new_name"]):
            print(f"  ✓ {r['old_name']} → {r['new_name']}")
            success += 1
        else:
            print(f"  ✗ {r['old_name']} - failed")
            failed += 1

    print(f"\nDone: {success} renamed, {failed} failed.")
    print("Run 'ynab sync' to update local data.")
