"""Bank of America CSV to YNAB converter.

BoA export format (illustrative rows, not real transactions):
  Posted Date,Reference Number,Payee,Address,Amount
  06/02/2026,00000000000000000000000,"STATEMENT CREDIT","",100.00
  06/02/2026,00000000000000000000001,"EXAMPLE MERCHANT 800-555-0100","800-555-0100  CO ",-35.00

Amount is signed: negative = charge, positive = credit/refund.
"""

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
class BoATransaction:
    date: datetime
    reference: str
    payee: str
    address: str
    amount: float


def _parse_date(date_str: str) -> datetime | None:
    date_str = date_str.strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None


def parse_boa(content: str) -> list[BoATransaction]:
    """Parse a Bank of America transaction CSV export."""
    content = content.lstrip("﻿")
    reader = csv.DictReader(io.StringIO(content))
    # Normalize headers: strip whitespace
    if reader.fieldnames:
        reader.fieldnames = [f.strip() for f in reader.fieldnames]

    expected = {"Posted Date", "Reference Number", "Payee", "Address", "Amount"}
    if not reader.fieldnames or not expected.issubset(set(reader.fieldnames)):
        got = set(reader.fieldnames or [])
        raise ValueError(
            f"Unrecognized Bank of America CSV format. Expected columns: {sorted(expected)}. Got: {sorted(got)}"
        )

    transactions = []
    for row in reader:
        date_str = (row.get("Posted Date") or "").strip()
        date = _parse_date(date_str)
        if not date:
            if date_str:
                logger.warning("Skipping row with unparseable date: %r", date_str)
            continue

        amount_str = (row.get("Amount") or "").strip()
        try:
            amount = float(amount_str)
        except ValueError:
            logger.warning("Skipping row with unparseable amount: %r", amount_str)
            continue

        transactions.append(
            BoATransaction(
                date=date,
                reference=(row.get("Reference Number") or "").strip(),
                payee=(row.get("Payee") or "").strip(),
                address=(row.get("Address") or "").strip(),
                amount=amount,
            )
        )

    return transactions


def to_ynab_csv(transactions: list[BoATransaction]) -> str:
    """Convert BoA transactions to YNAB CSV format (Date,Payee,Memo,Amount)."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "Payee", "Memo", "Amount"])
    for tx in transactions:
        writer.writerow(
            [
                tx.date.strftime("%m/%d/%Y"),
                tx.payee,
                tx.address,
                f"{tx.amount:.2f}",
            ]
        )
    return output.getvalue()


def _generate_import_id(tx: BoATransaction) -> str:
    """Generate a deterministic import_id for YNAB deduplication (max 36 chars).

    Uses the last 8 digits of the BoA Reference Number, which is already globally
    unique per transaction. Falls back to an 8-char md5 when the reference is absent.
    Format: YNAB:{amount_mu}:{date}:{ref8} -- stays within the 36-char YNAB limit.
    """
    amount_mu = round(tx.amount * 1000)
    date_str = tx.date.strftime("%Y-%m-%d")
    if tx.reference:
        ref_suffix = tx.reference[-8:]
    else:
        ref_suffix = hashlib.md5(f"{date_str}:{amount_mu}".encode()).hexdigest()[:8]
    return f"YNAB:{amount_mu}:{date_str}:{ref_suffix}"


def _find_ynab_account(conn, search: str) -> dict | None:
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
    logger.warning("Multiple accounts match '%s': %s", search, ", ".join(r["name"] for r in rows))
    return None


def run_import(
    file: str,
    output: str | None = None,
    preview: bool = False,
    push: bool = False,
    account: str | None = None,
) -> None:
    """Entry point for CLI. Converts BoA CSV to YNAB format or pushes via API."""
    path = Path(file)
    if not path.exists():
        print(f"Error: File not found: {path}", file=sys.stderr)
        sys.exit(1)

    content = path.read_text(encoding="utf-8-sig")
    transactions = parse_boa(content)

    if not transactions:
        print("No transactions found.", file=sys.stderr)
        sys.exit(1)

    dates = [t.date for t in transactions]
    charges = [t for t in transactions if t.amount < 0]
    credits = [t for t in transactions if t.amount >= 0]

    print("Bank: Bank of America", file=sys.stderr)
    print(
        f"Transactions: {len(transactions)} ({len(charges)} charges, {len(credits)} credits/refunds)", file=sys.stderr
    )
    print(f"Date range: {min(dates).strftime('%Y-%m-%d')} to {max(dates).strftime('%Y-%m-%d')}", file=sys.stderr)
    print(f"Net amount: ${sum(t.amount for t in transactions):,.2f}", file=sys.stderr)

    if preview:
        print(f"\n{'Date':<12} {'Amount':>10}  {'Payee'}", file=sys.stderr)
        print("-" * 60, file=sys.stderr)
        for t in transactions[:25]:
            print(
                f"{t.date.strftime('%Y-%m-%d'):<12} ${t.amount:>9,.2f}  {t.payee}",
                file=sys.stderr,
            )
        if len(transactions) > 25:
            print(f"  ... and {len(transactions) - 25} more", file=sys.stderr)
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


def _push_to_ynab(transactions: list[BoATransaction], account_search: str | None) -> None:
    """Push transactions directly to YNAB via API, skipping already-imported dates."""
    from ..client import YNABClient
    from ..config import require_credentials
    from ..db import get_connection, init_db

    if not account_search:
        print("Error: --account is required with --push", file=sys.stderr)
        print('  Example: ynab import boa FILE --push --account "BofA Visa"', file=sys.stderr)
        sys.exit(1)

    conn = get_connection()
    init_db(conn)

    acct = _find_ynab_account(conn, account_search)
    if not acct:
        print(f"Error: No account found matching '{account_search}'", file=sys.stderr)
        conn.close()
        sys.exit(1)

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
        original_count = len(transactions)
        transactions = [t for t in transactions if t.date.strftime("%Y-%m-%d") > last_date]
        skipped = original_count - len(transactions)
        if skipped > 0:
            print(f"\nSkipping {skipped} transactions on or before {last_date} (already in YNAB)")

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

        ynab_txn = {
            "account_id": acct["id"],
            "date": tx.date.strftime("%Y-%m-%d"),
            "amount": amount_mu,
            "payee_name": tx.payee,
            "memo": tx.address or None,
            "cleared": "cleared",
            "approved": True,
            "import_id": import_id,
        }

        result = client.create_transaction(ynab_txn)
        if result:
            success += 1
        else:
            failed += 1

    print(f"\n  Pushed: {success}  Failed: {failed}")
    if success > 0:
        print("  Run 'ynab sync' to update local data.")
    conn.close()
