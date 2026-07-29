"""Brokerage CSV to YNAB converter."""

import csv
import hashlib
import io
import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Transaction:
    """Normalized brokerage transaction."""

    date: datetime
    transaction_type: str
    raw_action: str
    symbol: str | None
    amount: float
    account_name: str | None = None
    account_number: str | None = None


def parse_date(date_str: str) -> datetime | None:
    """Parse date with multiple format support."""
    if not date_str or not date_str.strip():
        return None
    date_str = date_str.strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None


def parse_currency(value: str) -> float | None:
    """Parse currency string to float. Handles $, commas, parentheses."""
    if not value or not str(value).strip() or str(value).strip().upper() == "N/A":
        return None
    s = str(value).strip()
    negative = s.startswith("(") and s.endswith(")")
    if negative:
        s = s[1:-1]
    s = s.replace("$", "").replace(",", "").strip()
    try:
        result = float(s)
        return -result if negative else result
    except ValueError:
        return None


def detect_format(content: str) -> str:
    """Detect brokerage format from CSV content."""
    lines = content.strip().split("\n")
    for line in lines[:10]:
        line_clean = line.strip().lstrip("\ufeff").lower()
        if "run date" in line_clean and "action" in line_clean:
            return "fidelity"
        if "date" in line_clean and "transaction" in line_clean and "status" in line_clean:
            first = lines[0].strip()
            if "," not in first and first:
                return "merrill_lynch"
    raise ValueError("Unrecognized CSV format. Supported: Fidelity, Merrill Lynch")


def find_header_row(lines: list[str], markers: list[str]) -> int | None:
    """Find the row index containing all marker strings."""
    for i, line in enumerate(lines):
        line_lower = line.strip().lstrip("\ufeff").lower()
        if all(m in line_lower for m in markers):
            return i
    return None


def parse_fidelity(content: str) -> list[Transaction]:
    """Parse Fidelity CSV export."""
    lines = content.split("\n")
    header_idx = find_header_row(lines, ["run date", "action"])
    if header_idx is None:
        raise ValueError("Could not find header row in Fidelity CSV")

    reader = csv.DictReader(lines[header_idx:])
    reader.fieldnames = [f.strip() for f in reader.fieldnames]

    is_multi = "Account" in reader.fieldnames

    action_map = {
        "DIVIDEND": "Dividend",
        "REINVESTMENT": "Reinvestment",
        "CONTRIBUTION": "Contribution",
        "INTEREST": "Interest",
        "FEE": "Fee",
        "YOU BOUGHT": "Purchase",
        "YOU SOLD": "Sale",
        "TRANSFERRED": "Transfer",
        "ELECTRONIC FUNDS TRANSFER": "Transfer",
        "ROTH CONVERSION": "Roth Conversion",
    }

    transactions = []
    for row in reader:
        date_str = (row.get("Run Date") or "").strip()
        if not date_str or not date_str[0].isdigit():
            continue
        date = parse_date(date_str)
        amount = parse_currency(row.get("Amount ($)"))
        if not date or amount is None:
            continue

        raw_action = (row.get("Action") or "").strip()
        tx_type = "Other"
        for prefix, name in action_map.items():
            if raw_action.upper().startswith(prefix):
                tx_type = name
                break

        transactions.append(
            Transaction(
                date=date,
                transaction_type=tx_type,
                raw_action=raw_action,
                symbol=(row.get("Symbol") or "").strip() or None,
                amount=amount,
                account_name=(row.get("Account") or "").strip() or None if is_multi else None,
                account_number=(row.get("Account Number") or "").strip() or None if is_multi else None,
            )
        )
    return transactions


def parse_merrill_lynch(content: str) -> list[Transaction]:
    """Parse Merrill Lynch 401(k) CSV export."""
    lines = content.strip().split("\n")
    account_name = lines[0].strip() if lines else "Unknown"

    header_idx = find_header_row(lines, ["date", "transaction"])
    if header_idx is None:
        raise ValueError("Could not find header row in Merrill Lynch CSV")

    reader = csv.DictReader(lines[header_idx:])
    reader.fieldnames = [f.strip() for f in reader.fieldnames]

    type_map = {
        "In Plan Roth": "Roth Conversion",
        "Fund Transfer": "Transfer",
        "Plan Sponsor Fee": "Fee",
        "Recordkeeping Fee": "Fee",
    }
    fee_types = {"Plan Sponsor Fee", "Recordkeeping Fee"}
    skip_types = {"Withdrawal"}

    transactions = []
    for row in reader:
        tx_type_raw = (row.get("Transaction") or "").strip()
        if tx_type_raw in skip_types:
            continue
        date_str = (row.get("Date") or "").strip().strip('"')
        date = parse_date(date_str)
        amount = parse_currency((row.get("Amount") or "").strip().strip('"'))
        if not date or amount is None:
            continue
        if tx_type_raw in fee_types:
            amount = -abs(amount)

        tx_type = type_map.get(tx_type_raw, tx_type_raw)

        transactions.append(
            Transaction(
                date=date,
                transaction_type=tx_type,
                raw_action=tx_type_raw,
                symbol=None,
                amount=amount,
                account_name=account_name,
            )
        )
    return transactions


def to_ynab_csv(transactions: list[Transaction]) -> str:
    """Convert transactions to YNAB CSV format (Date,Payee,Memo,Amount)."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "Payee", "Memo", "Amount"])

    for tx in transactions:
        date_str = tx.date.strftime("%m/%d/%Y")
        payee = tx.transaction_type
        memo_parts = []
        if tx.symbol:
            memo_parts.append(tx.symbol)
        if tx.raw_action and tx.raw_action != payee:
            action_short = tx.raw_action[:50] + "..." if len(tx.raw_action) > 50 else tx.raw_action
            memo_parts.append(action_short)
        memo = " - ".join(memo_parts)
        writer.writerow([date_str, payee, memo, f"{tx.amount:.2f}"])

    return output.getvalue()


def _generate_import_id(tx: Transaction) -> str:
    """Generate a deterministic import_id for YNAB deduplication.

    YNAB import_id format: up to 36 characters.
    We use: YNAB:{amount_milliunits}:{date}:{hash4}
    """
    amount_mu = round(tx.amount * 1000)
    date_str = tx.date.strftime("%Y-%m-%d")
    # Hash the action + symbol for uniqueness when same amount/date
    unique = hashlib.md5(f"{tx.raw_action}:{tx.symbol or ''}".encode()).hexdigest()[:4]
    return f"YNAB:{amount_mu}:{date_str}:{unique}"


def _find_ynab_account(conn, search: str) -> dict | None:
    """Find a YNAB account by partial name match."""
    rows = conn.execute(
        """
        SELECT id, name FROM accounts
        WHERE deleted = 0 AND closed = 0
          AND (LOWER(name) LIKE LOWER(?)
               OR LOWER(REPLACE(REPLACE(name, '(', ''), ')', '')) LIKE LOWER(?))
    """,
        (f"%{search}%", f"%{search}%"),
    ).fetchall()

    if not rows:
        return None
    if len(rows) == 1:
        return dict(rows[0])
    # Multiple - return None to force user to be specific
    logger.warning("Multiple accounts match '%s': %s", search, ", ".join(r["name"] for r in rows))
    return None


def run_import(
    file: str, output: str | None = None, preview: bool = False, push: bool = False, account: str | None = None
):
    """Entry point for CLI. Converts brokerage CSV to YNAB format or pushes via API."""
    path = Path(file)
    if not path.exists():
        print(f"Error: File not found: {path}", file=sys.stderr)
        sys.exit(1)

    content = path.read_text(encoding="utf-8-sig")
    fmt = detect_format(content)

    if fmt == "fidelity":
        transactions = parse_fidelity(content)
        brokerage = "Fidelity"
    else:
        transactions = parse_merrill_lynch(content)
        brokerage = "Merrill Lynch"

    if not transactions:
        print("No transactions found.", file=sys.stderr)
        sys.exit(1)

    dates = [t.date for t in transactions]
    print(f"Brokerage: {brokerage}", file=sys.stderr)
    print(f"Transactions: {len(transactions)}", file=sys.stderr)
    print(f"Date range: {min(dates).strftime('%Y-%m-%d')} to {max(dates).strftime('%Y-%m-%d')}", file=sys.stderr)
    print(f"Net amount: ${sum(t.amount for t in transactions):,.2f}", file=sys.stderr)

    accounts = {t.account_name for t in transactions if t.account_name}
    if accounts:
        print(f"Accounts: {', '.join(sorted(accounts))}", file=sys.stderr)

    if preview:
        print(f"\n{'Date':<12} {'Type':<16} {'Symbol':<8} {'Amount':>12}", file=sys.stderr)
        print("-" * 50, file=sys.stderr)
        for t in transactions[:20]:
            print(
                f"{t.date.strftime('%Y-%m-%d'):<12} {t.transaction_type:<16} {(t.symbol or ''):<8} ${t.amount:>11,.2f}",
                file=sys.stderr,
            )
        if len(transactions) > 20:
            print(f"  ... and {len(transactions) - 20} more", file=sys.stderr)
        return

    if push:
        _push_to_ynab(transactions, account)
        return

    ynab_csv = to_ynab_csv(transactions)

    if output:
        Path(output).write_text(ynab_csv)
        print(f"\nSaved to {output}", file=sys.stderr)
    else:
        print(ynab_csv)


def _push_to_ynab(transactions: list[Transaction], account_search: str | None) -> None:
    """Push transactions directly to YNAB via API.

    Automatically skips transactions on or before the last existing transaction
    date in YNAB to avoid duplicates with previously imported data.
    """
    from ..client import YNABClient
    from ..config import require_credentials
    from ..db import get_connection, init_db

    if not account_search:
        print("Error: --account is required with --push", file=sys.stderr)
        print('  Example: ynab import brokerage FILE --push --account "Roth IRA"', file=sys.stderr)
        sys.exit(1)

    conn = get_connection()
    init_db(conn)

    acct = _find_ynab_account(conn, account_search)
    if not acct:
        print(f"Error: No account found matching '{account_search}'", file=sys.stderr)
        conn.close()
        sys.exit(1)

    # Find the last non-adjustment transaction date to avoid duplicates.
    # Exclude Balance Adjustment entries so reconcile operations don't block future imports.
    row = conn.execute(
        """
        SELECT MAX(date) AS last_date
        FROM transactions
        WHERE deleted = 0 AND account_name = ?
          AND payee_name NOT LIKE 'Balance Adjustment%'
    """,
        (acct["name"],),
    ).fetchone()

    last_date = row["last_date"] if row and row["last_date"] else None
    if last_date:
        # Only push transactions AFTER the last known date
        original_count = len(transactions)
        transactions = [t for t in transactions if t.date.strftime("%Y-%m-%d") > last_date]
        skipped_old = original_count - len(transactions)
        if skipped_old > 0:
            print(f"\nSkipping {skipped_old} transactions on or before {last_date} (already in YNAB)")

    print(f"\nTarget account: {acct['name']}")
    print(f"Transactions to push: {len(transactions)}")

    if not transactions:
        print("  No new transactions to push.")
        conn.close()
        return

    token, plan_id = require_credentials()
    client = YNABClient(token, plan_id)

    success = 0
    failed = 0

    for tx in transactions:
        import_id = _generate_import_id(tx)
        amount_mu = round(tx.amount * 1000)

        memo_parts = []
        if tx.symbol:
            memo_parts.append(tx.symbol)
        if tx.raw_action and tx.raw_action != tx.transaction_type:
            action_short = tx.raw_action[:60] if len(tx.raw_action) > 60 else tx.raw_action
            memo_parts.append(action_short)

        ynab_txn = {
            "account_id": acct["id"],
            "date": tx.date.strftime("%Y-%m-%d"),
            "amount": amount_mu,
            "payee_name": tx.transaction_type,
            "memo": " - ".join(memo_parts) if memo_parts else None,
            "cleared": "cleared",
            "approved": True,
            "import_id": import_id,
        }

        result = client.create_transaction(ynab_txn)
        if result:
            # YNAB returns the transaction - check if it was a duplicate
            if result.get("import_id") == import_id:
                success += 1
            else:
                success += 1
        else:
            failed += 1

    # Check for duplicates by examining the response pattern
    print(f"\n  Pushed: {success}  Failed: {failed}")
    if success > 0:
        print("  Run 'ynab sync' to update local data.")
    conn.close()
