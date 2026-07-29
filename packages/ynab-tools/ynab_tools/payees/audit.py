"""Payee mismatch detection, fixing, validation, and budget listing."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import sys
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..client import YNABClient

from ..config import RULES_FILE
from ..db import get_mismatches
from .backup import bulk_apply, create_backup
from .names import filter_mismatches

logger = logging.getLogger(__name__)


def load_rules() -> list[dict]:
    """Load correction rules from payee_rules.json."""
    if not RULES_FILE.exists():
        logger.warning("Rules file not found: %s", RULES_FILE)
        return []
    try:
        with open(RULES_FILE) as f:
            return json.load(f).get("rules", [])
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to load rules file: %s", e)
        return []


def match_rule(original_import: str, rules: list[dict]) -> dict | None:
    """Find a rule that matches the original import payee name."""
    for rule in rules:
        pattern = rule.get("import_pattern", "")
        if re.match(pattern, original_import, re.IGNORECASE):
            return rule
    return None


def apply_rules(mismatches: list[dict], rules: list[dict]) -> list[dict]:
    """Apply correction rules to mismatches, return list of fixes."""
    fixes = []
    for txn in mismatches:
        rule = match_rule(txn["original_import"], rules)
        fixes.append(
            {
                **txn,
                "correct_payee": rule["correct_payee"] if rule else None,
                "correct_category": rule.get("correct_category") if rule else None,
            }
        )
    return fixes


def get_impacted_months(fixes: list[dict]) -> dict[str, int]:
    """Get transaction counts by month."""
    months: dict[str, int] = {}
    for f in fixes:
        month = (f.get("date") or "")[:7]
        if month:
            months[month] = months.get(month, 0) + 1
    return dict(sorted(months.items()))


def print_impacted_months(months: dict[str, int]):
    """Print the impacted months report."""
    if not months:
        return
    print("\nImpacted Months:")
    for month, count in months.items():
        print(f"  - {month} ({count} transactions)")
    print("\nThese months may need budget funding adjustments in YNAB.")


def cmd_audit(conn: sqlite3.Connection) -> None:
    """Find and report all payee mismatches."""
    print("YNAB Payee Mismatch Audit Report")
    print("=" * 40)
    print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print()

    all_mismatches = get_mismatches(conn)
    print(f"Total renamed transactions: {len(all_mismatches)}")

    mismatches = filter_mismatches(all_mismatches)
    print(f"Actual mismatches (bad renames): {len(mismatches)}")
    print()

    if not mismatches:
        print("No mismatches found!")
        return

    by_payee: dict[str, list] = {}
    for txn in mismatches:
        by_payee.setdefault(txn["payee_name"], []).append(txn)

    for payee, txns in sorted(by_payee.items(), key=lambda x: -len(x[1])):
        total = sum(abs(t["amount"]) for t in txns)
        print(f"\n## '{payee}' ({len(txns)} transactions, ${total:.2f})")

        by_original: dict[str, list] = {}
        for txn in txns:
            by_original.setdefault(txn["original_import"], []).append(txn)

        for orig, otxns in sorted(by_original.items(), key=lambda x: -len(x[1])):
            ototal = sum(abs(t["amount"]) for t in otxns)
            print(f"  - {orig}: {len(otxns)} txns (${ototal:.2f})")


def cmd_preview(conn: sqlite3.Connection) -> None:
    """Preview fixes that would be applied."""
    print("YNAB Payee Fix Preview")
    print("=" * 40)
    print()

    all_mismatches = get_mismatches(conn)
    mismatches = filter_mismatches(all_mismatches)

    if not mismatches:
        print("No mismatches to fix!")
        return

    rules = load_rules()
    if not rules:
        print("Warning: No rules loaded, cannot determine fixes")
        return

    fixes = apply_rules(mismatches, rules)
    with_fix = [f for f in fixes if f["correct_payee"]]
    without_fix = [f for f in fixes if not f["correct_payee"]]

    print(f"Fixes available: {len(with_fix)}")
    print(f"No rule matched: {len(without_fix)}")
    print()

    if with_fix:
        print("## Proposed Fixes:")
        print()
        by_correction: dict[tuple, list] = {}
        for f in with_fix:
            key = (f["payee_name"], f["correct_payee"])
            by_correction.setdefault(key, []).append(f)

        for (current, correct), txns in sorted(by_correction.items(), key=lambda x: -len(x[1])):
            total = sum(abs(t["amount"]) for t in txns)
            print(f"  {current} → {correct}: {len(txns)} transactions (${total:.2f})")

    if without_fix:
        print("\n## No Rule Matched:")
        print()
        by_original: dict[str, list] = {}
        for f in without_fix:
            by_original.setdefault(f["original_import"], []).append(f)
        for orig, txns in sorted(by_original.items(), key=lambda x: -len(x[1])):
            print(f"  - {orig}: {len(txns)} transactions")

    if with_fix:
        print_impacted_months(get_impacted_months(with_fix))


def cmd_fix(conn: sqlite3.Connection, client: YNABClient) -> None:
    """Apply fixes with confirmation."""
    print("YNAB Payee Fixer")
    print("=" * 40)
    print()

    all_mismatches = get_mismatches(conn)
    mismatches = filter_mismatches(all_mismatches)

    if not mismatches:
        print("No mismatches to fix!")
        return

    rules = load_rules()
    if not rules:
        print("Error: No rules loaded")
        return

    fixes = apply_rules(mismatches, rules)
    with_fix = [f for f in fixes if f["correct_payee"]]

    if not with_fix:
        print("No fixes to apply (no rules matched)")
        return

    print(f"Ready to fix {len(with_fix)} transactions:")
    print()

    by_correction: dict[tuple, list] = {}
    for f in with_fix:
        key = (f["payee_name"], f["correct_payee"])
        by_correction.setdefault(key, []).append(f)

    for (current, correct), txns in sorted(by_correction.items(), key=lambda x: -len(x[1])):
        print(f"  {current} → {correct}: {len(txns)} transactions")

    print_impacted_months(get_impacted_months(with_fix))

    confirm = input("\nApply these fixes? [y/N]: ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        return

    print("\nCreating backup...")
    backup_file = create_backup(with_fix)
    print(f"  Backup saved: backups/{backup_file}")

    updates = [{"id": f["ynab_transaction_id"], "payee_name": f["correct_payee"]} for f in with_fix]

    print("\nApplying fixes...")
    bulk_apply(client, updates)
    print("\nDone! Run 'ynab sync' to update local data.")


def cmd_validate() -> None:
    """Validate that all rules use canonical payee names."""
    print("YNAB Payee Rules Validation")
    print("=" * 40)
    print()

    if not RULES_FILE.exists():
        print(f"Rules file not found: {RULES_FILE}", file=sys.stderr)
        return

    try:
        with open(RULES_FILE) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"Error reading rules file: {e}", file=sys.stderr)
        return

    canonical = data.get("canonical_payees", {})
    rules = data.get("rules", [])
    errors = []

    if not canonical:
        errors.append("No canonical_payees defined in rules file")

    for i, rule in enumerate(rules):
        payee = rule.get("correct_payee", "")
        if payee and payee not in canonical:
            errors.append(f"Rule {i + 1}: '{payee}' is not in canonical_payees")

    if not errors:
        print("All rules are valid!")
        print()
        if canonical:
            print("Canonical Payees:")
            for payee, info in sorted(canonical.items()):
                cat = info.get("category", "")
                print(f"  - {payee}" + (f" → {cat}" if cat else ""))
    else:
        print("Validation errors found:")
        print()
        for error in errors:
            print(f"  - {error}")
        print()
        print("Fix these errors before running fix")


def cmd_plans(client: YNABClient) -> None:
    """List available YNAB plans."""
    print("YNAB Plans")
    print("=" * 40)
    print()

    plans = client.get_plans()

    if not plans:
        print("No plans found.")
        return

    for p in plans:
        print(f"  - {p['name']}")
        print(f"    ID: {p['id']}")
        print()
