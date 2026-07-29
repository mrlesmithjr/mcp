"""Historical net worth snapshot tracker."""

import logging
import sqlite3
from datetime import UTC, datetime

from ..db import get_connection, init_db

logger = logging.getLogger(__name__)


def take_snapshot(conn: sqlite3.Connection) -> dict | None:
    """Capture current account balances as a net worth snapshot. Returns None if no accounts."""
    rows = conn.execute("""
        SELECT name, type, on_budget, balance
        FROM accounts
        WHERE closed = 0 AND deleted = 0
    """).fetchall()

    if not rows:
        return None

    total_assets = 0.0
    total_debt = 0.0
    on_budget_assets = 0.0
    on_budget_debt = 0.0
    off_budget_assets = 0.0
    off_budget_debt = 0.0
    details = []

    for row in rows:
        balance = row["balance"] or 0.0
        on_budget = row["on_budget"]

        details.append(f"{row['name']}|{row['type']}|{on_budget}|{balance:.2f}")

        if balance >= 0:
            total_assets += balance
            if on_budget:
                on_budget_assets += balance
            else:
                off_budget_assets += balance
        else:
            total_debt += balance
            if on_budget:
                on_budget_debt += balance
            else:
                off_budget_debt += balance

    net_worth = total_assets + total_debt
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now(UTC).isoformat()

    existing = conn.execute("SELECT id FROM net_worth_snapshots WHERE snapshot_date = ?", (today,)).fetchone()

    if existing:
        conn.execute(
            """
            UPDATE net_worth_snapshots SET
                total_assets = ?, total_debt = ?, net_worth = ?,
                on_budget_assets = ?, on_budget_debt = ?,
                off_budget_assets = ?, off_budget_debt = ?,
                account_details = ?, created_at = ?
            WHERE snapshot_date = ?
        """,
            (
                total_assets,
                total_debt,
                net_worth,
                on_budget_assets,
                on_budget_debt,
                off_budget_assets,
                off_budget_debt,
                "\n".join(details),
                now,
                today,
            ),
        )
        action = "Updated"
    else:
        conn.execute(
            """
            INSERT INTO net_worth_snapshots
                (snapshot_date, total_assets, total_debt, net_worth,
                 on_budget_assets, on_budget_debt,
                 off_budget_assets, off_budget_debt,
                 account_details, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                today,
                total_assets,
                total_debt,
                net_worth,
                on_budget_assets,
                on_budget_debt,
                off_budget_assets,
                off_budget_debt,
                "\n".join(details),
                now,
            ),
        )
        action = "Created"

    conn.commit()

    return {
        "date": today,
        "action": action,
        "total_assets": total_assets,
        "total_debt": total_debt,
        "net_worth": net_worth,
        "on_budget_assets": on_budget_assets,
        "on_budget_debt": on_budget_debt,
        "off_budget_assets": off_budget_assets,
        "off_budget_debt": off_budget_debt,
    }


def show_history(conn: sqlite3.Connection, months: int = 12):
    """Show net worth history."""
    rows = conn.execute(
        """
        SELECT snapshot_date, total_assets, total_debt, net_worth,
               on_budget_assets, off_budget_assets
        FROM net_worth_snapshots
        ORDER BY snapshot_date DESC
        LIMIT ?
    """,
        (months,),
    ).fetchall()

    if not rows:
        print("No snapshots found. Run 'ynab net-worth' to take the first snapshot.")
        return

    print("Net Worth History")
    print("=" * 80)
    print(f"{'Date':<12} {'Net Worth':>14} {'Assets':>14} {'Debt':>14} {'Change':>12}")
    print("-" * 80)

    prev_nw = None
    for row in reversed(rows):
        nw = row["net_worth"]
        change = ""
        if prev_nw is not None:
            diff = nw - prev_nw
            arrow = "+" if diff >= 0 else ""
            change = f"{arrow}${diff:,.0f}"
        prev_nw = nw

        print(
            f"{row['snapshot_date']:<12} "
            f"${nw:>13,.2f} "
            f"${row['total_assets']:>13,.2f} "
            f"${row['total_debt']:>13,.2f} "
            f"{change:>12}"
        )

    if len(rows) >= 2:
        first = rows[-1]
        last = rows[0]
        total_change = last["net_worth"] - first["net_worth"]
        arrow = "+" if total_change >= 0 else ""
        print(f"\n  Total change: {arrow}${total_change:,.2f}")
        print(f"  Period: {first['snapshot_date']} → {last['snapshot_date']}")


def show_detail(conn: sqlite3.Connection):
    """Show the latest snapshot with full account breakdown."""
    row = conn.execute("""
        SELECT * FROM net_worth_snapshots ORDER BY snapshot_date DESC LIMIT 1
    """).fetchone()

    if not row:
        print("No snapshots found.")
        return

    print(f"Net Worth Snapshot: {row['snapshot_date']}")
    print("=" * 60)

    print(f"\n  Net Worth:          ${row['net_worth']:>14,.2f}")
    print(f"  Total Assets:       ${row['total_assets']:>14,.2f}")
    print(f"  Total Debt:         ${row['total_debt']:>14,.2f}")
    print(f"\n  On-Budget Assets:   ${row['on_budget_assets']:>14,.2f}")
    print(f"  On-Budget Debt:     ${row['on_budget_debt']:>14,.2f}")
    print(f"  Off-Budget Assets:  ${row['off_budget_assets']:>14,.2f}")
    print(f"  Off-Budget Debt:    ${row['off_budget_debt']:>14,.2f}")

    details = row["account_details"]
    if details:
        print("\n  Account Breakdown:")
        print(f"  {'Account':<45} {'Type':<15} {'Balance':>12}")
        print("  " + "-" * 72)

        for line in details.split("\n"):
            parts = line.split("|")
            if len(parts) == 4:
                name, acct_type, _, balance = parts
                bal = float(balance)
                print(f"  {name:<45} {acct_type:<15} ${bal:>11,.2f}")


def run_snapshot(history: int | None = None, detail: bool = False) -> None:
    """Entry point for CLI. Takes snapshot or shows history/detail."""
    conn = get_connection()
    try:
        init_db(conn)

        if history:
            show_history(conn, history)
        elif detail:
            show_detail(conn)
        else:
            result = take_snapshot(conn)
            if result is None:
                print("No accounts found. Run 'ynab sync' first.")
                import sys

                sys.exit(1)
            print(f"Net Worth Snapshot ({result['action']}): {result['date']}")
            print(f"  Net Worth:     ${result['net_worth']:>14,.2f}")
            print(f"  Total Assets:  ${result['total_assets']:>14,.2f}")
            print(f"  Total Debt:    ${result['total_debt']:>14,.2f}")
            print(f"\n  On-Budget:     ${result['on_budget_assets'] + result['on_budget_debt']:>14,.2f}")
            print(f"  Off-Budget:    ${result['off_budget_assets'] + result['off_budget_debt']:>14,.2f}")
    finally:
        conn.close()
