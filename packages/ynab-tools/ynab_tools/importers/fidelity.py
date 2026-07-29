"""Fidelity portfolio positions importer - extracts current balances for reconciliation."""

import csv
import json
import logging
import os
import sys
from pathlib import Path

from ..config import load_env

logger = logging.getLogger(__name__)


def _get_account_map() -> dict[str, str]:
    """Load Fidelity account number to YNAB account name mapping from env.

    Set in .env as JSON:
      YNAB_FIDELITY_ACCOUNTS={"ACCT123": "Roth IRA", "ACCT456": "Taxable Brokerage"}
    """
    load_env()
    raw = os.environ.get("YNAB_FIDELITY_ACCOUNTS", "").strip()
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Invalid JSON in YNAB_FIDELITY_ACCOUNTS, using account names from CSV")
    return {}


def parse_positions(content: str) -> dict[str, dict]:
    """Parse Fidelity Portfolio Positions CSV.

    Returns: {account_number: {"name": str, "total_value": float,
              "holdings": [{"symbol": str, "value": float, ...}]}}
    """
    lines = content.split("\n")
    # Find the header row
    header_idx = None
    for i, line in enumerate(lines):
        if "Account Number" in line and "Current Value" in line:
            header_idx = i
            break

    if header_idx is None:
        raise ValueError("Could not find header row in Fidelity positions CSV")

    reader = csv.DictReader(lines[header_idx:])
    reader.fieldnames = [f.strip() for f in reader.fieldnames]

    accounts: dict[str, dict] = {}

    for row in reader:
        acct_num = (row.get("Account Number") or "").strip()
        # Fidelity account numbers are short alphanumeric strings
        # Skip disclaimer text and other non-account rows
        if not acct_num or len(acct_num) > 20 or not acct_num[0].isalnum():
            continue

        if acct_num not in accounts:
            accounts[acct_num] = {
                "name": (row.get("Account Name") or "").strip(),
                "total_value": 0.0,
                "holdings": [],
            }

        value_str = (row.get("Current Value") or "").replace("$", "").replace(",", "").strip()
        try:
            value = float(value_str)
        except (ValueError, TypeError):
            value = 0.0

        symbol = (row.get("Symbol") or "").strip().rstrip("*")
        cost_str = (row.get("Cost Basis Total") or "").replace("$", "").replace(",", "").strip()
        try:
            cost = float(cost_str)
        except (ValueError, TypeError):
            cost = None

        gain_str = (row.get("Total Gain/Loss Dollar") or "").replace("$", "").replace(",", "").replace("+", "").strip()
        try:
            gain = float(gain_str)
        except (ValueError, TypeError):
            gain = None

        accounts[acct_num]["total_value"] += value
        if symbol:
            accounts[acct_num]["holdings"].append(
                {
                    "symbol": symbol,
                    "value": value,
                    "cost_basis": cost,
                    "gain_loss": gain,
                    "pct_of_account": (row.get("Percent Of Account") or "").strip(),
                }
            )

    return accounts


def run_positions(file: str, reconcile: bool = False, apply: bool = False) -> None:
    """Parse Fidelity positions CSV and show balances or reconcile."""
    path = Path(file)
    if not path.exists():
        print(f"Error: File not found: {path}", file=sys.stderr)
        sys.exit(1)

    content = path.read_text(encoding="utf-8-sig")
    accounts = parse_positions(content)

    if not accounts:
        print("No positions found in file.")
        return

    print("Fidelity Portfolio Positions")
    print("=" * 70)

    total_all = 0.0
    reconcile_targets = []

    account_map = _get_account_map()

    for acct_num, data in sorted(accounts.items()):
        ynab_search = account_map.get(acct_num, data["name"] or acct_num)
        total = data["total_value"]
        total_all += total

        print(f'\n  {data["name"]} ({acct_num}) → YNAB: "{ynab_search}"')
        print(f"  {'Symbol':<10} {'Value':>12} {'Cost':>12} {'Gain/Loss':>12} {'%':>8}")
        print(f"  {'-' * 10} {'-' * 12} {'-' * 12} {'-' * 12} {'-' * 8}")

        for h in data["holdings"]:
            cost_str = f"${h['cost_basis']:>11,.2f}" if h["cost_basis"] is not None else f"{'':>12}"
            gain_str = f"${h['gain_loss']:>11,.2f}" if h["gain_loss"] is not None else f"{'':>12}"
            print(f"  {h['symbol']:<10} ${h['value']:>11,.2f} {cost_str} {gain_str} {h['pct_of_account']:>8}")

        print(f"  {'-' * 10} {'-' * 12}")
        print(f"  {'Total':<10} ${total:>11,.2f}")

        reconcile_targets.append((ynab_search, total))

    print(f"\n  Grand Total: ${total_all:>,.2f}")

    if reconcile:
        print("\n" + "=" * 70)
        print("Reconciliation Commands:")
        print()
        for search, balance in reconcile_targets:
            flag = " --apply" if apply else ""
            print(f'  ynab reconcile "{search}" {balance:.2f}{flag}')

        if not apply:
            print("\n  Add --apply to execute: ynab import positions FILE --reconcile --apply")
        else:
            # Actually run the reconciliation
            from ..reports.reconcile import run_reconcile

            print()
            for search, balance in reconcile_targets:
                print(f"--- Reconciling {search} ---")
                run_reconcile(account=search, balance=balance, apply=True)
                print()
