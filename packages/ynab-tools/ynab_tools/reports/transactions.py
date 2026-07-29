"""Transaction reports: recent, large expenses, sinking funds, add."""

import hashlib
import logging
import sqlite3
from datetime import datetime

from ..client import YNABClient
from ..config import require_credentials
from ..db import current_month, find_account, get_connection, init_db, log_audit
from ..stats import parse_category_input

logger = logging.getLogger(__name__)


def run_recent(
    limit: int = 25,
    account: str | None = None,
    payee: str | None = None,
    category: str | None = None,
    memo: str | None = None,
    uncleared: bool = False,
    month: str | None = None,
) -> None:
    """Show recent transactions with splits resolved."""
    conn = get_connection()
    try:
        init_db(conn)

        # When --month is specified, use a higher default limit to get all month transactions
        effective_limit = limit if not month or limit != 25 else 500

        where_clauses = [
            "t.deleted = 0",
            # NULL-safe Split exclusion: COALESCE to '' so NULL != 'Split' doesn't drop uncategorized rows
            "COALESCE(st.category_name, t.category_name, '') NOT IN ('Split', 'Split (Multiple Categories...)')",
        ]
        params: list = []

        if account:
            where_clauses.append("LOWER(t.account_name) LIKE LOWER(?)")
            params.append(f"%{account}%")

        if payee:
            where_clauses.append("LOWER(COALESCE(t.payee_name, p.name)) LIKE LOWER(?)")
            params.append(f"%{payee}%")

        if category:
            where_clauses.append("LOWER(COALESCE(st.category_name, t.category_name)) LIKE LOWER(?)")
            params.append(f"%{category}%")

        if memo:
            where_clauses.append("LOWER(COALESCE(st.memo, t.memo)) LIKE LOWER(?)")
            params.append(f"%{memo}%")

        if uncleared:
            # "reconciled" is a fully-cleared, locked state (more final than "cleared"),
            # so only "uncleared" should count as pending here. See #127.
            where_clauses.append("t.cleared = 'uncleared'")

        if month:
            # Parse YYYY-MM and calculate first day of next month
            try:
                month_start = datetime.strptime(month, "%Y-%m").replace(day=1)
                # Calculate first day of next month
                if month_start.month == 12:
                    next_month = month_start.replace(year=month_start.year + 1, month=1)
                else:
                    next_month = month_start.replace(month=month_start.month + 1)
                where_clauses.append("t.date >= ?")
                params.append(month_start.strftime("%Y-%m-%d"))
                where_clauses.append("t.date < ?")
                params.append(next_month.strftime("%Y-%m-%d"))
            except ValueError:
                print(f"Invalid month format: {month}. Use YYYY-MM (e.g. 2026-03).")
                return

        params.append(effective_limit + 10)
        where_sql = " AND ".join(where_clauses)

        rows = conn.execute(
            f"""
            SELECT t.id, t.date, COALESCE(t.payee_name, p.name) AS payee_name, t.account_name,
                   COALESCE(st.category_name, t.category_name) AS category,
                   COALESCE(st.amount, t.amount) AS amount,
                   COALESCE(st.memo, t.memo) AS memo,
                   t.approved, t.cleared
            FROM transactions t
            LEFT JOIN subtransactions st ON st.transaction_id = t.id AND st.deleted = 0
            LEFT JOIN payees p ON t.payee_id = p.id
            WHERE {where_sql}
            ORDER BY t.date DESC, payee_name
            LIMIT ?
        """,
            params,
        ).fetchall()  # fetch extra to account for splits expanding

        if not rows:
            print("No transactions found. Run 'ynab sync' first.")
            return

        filters = []
        if account:
            filters.append(f"account={account}")
        if payee:
            filters.append(f"payee={payee}")
        if category:
            filters.append(f"category={category}")
        if memo:
            filters.append(f"memo={memo}")
        if uncleared:
            filters.append("uncleared only")
        if month:
            filters.append(f"month={month}")
        filter_str = f" - {', '.join(filters)}" if filters else ""
        header_count = f"month {month}" if month else f"last {effective_limit}"
        print(f"Recent Transactions ({header_count}){filter_str}")
        print("=" * 130)
        print(f"{'ID':<10} {'Date':<12} {'Payee':<25} {'Category':<30} {'Amount':>10} {'Account':<15} {'Memo'}")
        print("-" * 130)

        shown = 0
        uncategorized = 0
        unapproved = 0

        for row in rows:
            if shown >= effective_limit:
                break

            cat = row["category"] or ""
            amount = row["amount"] or 0
            flags = ""

            if not cat or cat == "Uncategorized":
                flags += " [!]"
                uncategorized += 1
            if not row["approved"]:
                flags += " [?]"
                unapproved += 1

            payee = (row["payee_name"] or "")[:23]
            cat_display = cat[:28] if cat else "(uncategorized)"
            account = (row["account_name"] or "")[:13]

            memo = (row["memo"] or "")[:30]
            tid = row["id"][:8]
            line = f"  {tid:<10}{row['date']:<10} {payee:<25} {cat_display:<30}"
            print(f"{line} ${amount:>9,.2f} {account:<15}{flags} {memo}")
            shown += 1

        print()
        if uncategorized:
            print(f"  [!] = uncategorized ({uncategorized})")
        if unapproved:
            print(f"  [?] = unapproved ({unapproved})")
    finally:
        conn.close()


def run_large(threshold: float = 500, months: int = 6) -> None:
    """Show large expenses over a threshold."""
    conn = get_connection()
    try:
        init_db(conn)

        rows = conn.execute(
            """
            SELECT t.date, COALESCE(t.payee_name, p.name) AS payee_name,
                   COALESCE(st.category_name, t.category_name) AS category,
                   COALESCE(st.amount, t.amount) AS amount,
                   t.account_name,
                   COALESCE(st.memo, t.memo) AS memo
            FROM transactions t
            LEFT JOIN subtransactions st ON st.transaction_id = t.id AND st.deleted = 0
            LEFT JOIN payees p ON t.payee_id = p.id
            WHERE t.deleted = 0
              AND t.date >= date('now', ? || ' months')
              AND COALESCE(st.amount, t.amount) < ?
              AND COALESCE(t.payee_name, p.name) NOT LIKE 'Transfer :%'
              AND COALESCE(st.category_name, t.category_name, '') NOT IN ('Split', 'Split (Multiple Categories...)')
            ORDER BY t.date DESC
        """,
            (f"-{months}", -threshold),
        ).fetchall()

        if not rows:
            print(f"No expenses over ${threshold:,.0f} in the last {months} months.")
            return

        total = sum(abs(row["amount"] or 0) for row in rows)
        print(f"Large Expenses (>${threshold:,.0f}, last {months} months)")
        print("=" * 90)
        print(f"Found {len(rows)} transactions totaling ${total:,.2f}\n")

        # Group by month
        by_month: dict[str, list] = {}
        for row in rows:
            month_key = row["date"][:7]
            by_month.setdefault(month_key, []).append(row)

        for month_key in sorted(by_month.keys(), reverse=True):
            month_rows = by_month[month_key]
            month_total = sum(abs(r["amount"] or 0) for r in month_rows)
            print(f"## {month_key} ({len(month_rows)} transactions, ${month_total:,.2f})")

            for r in month_rows:
                cat = (r["category"] or "")[:25]
                payee = (r["payee_name"] or "")[:25]
                memo = f" ({r['memo']})" if r["memo"] else ""
                if len(memo) > 30:
                    memo = memo[:27] + "...)"
                print(f"  {r['date']}  {payee:<25} {cat:<25} ${abs(r['amount']):>10,.2f}{memo}")
            print()
    finally:
        conn.close()


def run_sinking_funds() -> None:
    """Show sinking fund balances and underfunded status."""
    conn = get_connection()
    try:
        init_db(conn)
        month = current_month()

        # hidden intentionally omitted: INSERT OR REPLACE propagates current hidden flag to historical rows
        rows = conn.execute(
            """
            SELECT name, balance, goal_type, goal_target, goal_under_funded,
                   budgeted, category_group_name
            FROM budget_categories
            WHERE budget_month = ?
              AND goal_type IS NOT NULL AND goal_type != ''
              AND deleted = 0
            ORDER BY category_group_name, name
        """,
            (month,),
        ).fetchall()

        if not rows:
            print("No categories with goals found. Sync budget data first.")
            return

        underfunded = []
        negative = []
        funded = []

        for row in rows:
            entry = dict(row)
            balance = entry["balance"] or 0
            underfund = entry["goal_under_funded"] or 0

            if underfund > 0:
                underfunded.append(entry)
            elif balance < 0:
                negative.append(entry)
            else:
                funded.append(entry)

        print("Sinking Funds & Goal Status")
        print("=" * 70)
        print(f"Month: {month[:7]}\n")

        if underfunded:
            total_needed = sum(c["goal_under_funded"] or 0 for c in underfunded)
            print(f"UNDERFUNDED ({len(underfunded)} categories, ${total_needed:,.2f} needed):")
            print(f"  {'Category':<40} {'Balance':>10} {'Needed':>10}")
            print("  " + "-" * 62)
            for c in sorted(underfunded, key=lambda x: -(x["goal_under_funded"] or 0)):
                print(f"  {c['name']:<40} ${c['balance'] or 0:>9,.2f} ${c['goal_under_funded']:>9,.2f}")
            print()

        if negative:
            print(f"NEGATIVE BALANCE ({len(negative)} categories):")
            for c in sorted(negative, key=lambda x: x["balance"] or 0):
                print(f"  {c['name']:<40} ${c['balance']:>10,.2f}")
            print()

        if funded:
            print(f"FULLY FUNDED ({len(funded)} categories):")
            for c in sorted(funded, key=lambda x: -(x["balance"] or 0)):
                goal_str = _format_goal_type(c["goal_type"])
                print(f"  {c['name']:<40} ${c['balance'] or 0:>10,.2f}  ({goal_str})")
            print()

        # Summary
        total_balance = sum(c["balance"] or 0 for c in [*underfunded, *negative, *funded])
        print(f"Total across all goal categories: ${total_balance:>,.2f}")
        print(f"  Funded: {len(funded)}  |  Underfunded: {len(underfunded)}  |  Negative: {len(negative)}")

        # Check for consistently underfunded (last 3 months)
        consistently = conn.execute(
            """
            SELECT name, COUNT(*) AS months_underfunded
            FROM budget_categories
            WHERE budget_month >= date(?, '-2 months')
              AND goal_under_funded > 0
              AND deleted = 0
            GROUP BY name
            HAVING COUNT(*) >= 3
            ORDER BY name
        """,
            (month,),
        ).fetchall()

        if consistently:
            print("\nConsistently Underfunded (3+ months):")
            for c in consistently:
                print(f"  - {c['name']}")
    finally:
        conn.close()


def run_transfers(days: int = 30) -> None:
    """Show recent transfers, flagging uncategorized ones."""
    conn = get_connection()
    try:
        init_db(conn)

        rows = conn.execute(
            """
            SELECT t.date, COALESCE(t.payee_name, p.name) AS payee_name, t.amount,
                   t.category_name, t.account_name, t.cleared
            FROM transactions t
            LEFT JOIN payees p ON t.payee_id = p.id
            WHERE t.deleted = 0
              AND t.transfer_account_id IS NOT NULL
              AND t.date >= date('now', ? || ' days')
            ORDER BY t.date DESC
        """,
            (f"-{days}",),
        ).fetchall()

        if not rows:
            print(f"No transfers in the last {days} days.")
            return

        # Identify uncategorized transfers
        uncategorized = [r for r in rows if not r["category_name"] or r["category_name"] in ("", "Uncategorized")]

        print(f"Transfers (last {days} days)")
        print("=" * 90)
        print(f"Found {len(rows)} transfers, {len(uncategorized)} uncategorized\n")

        print(f"  {'Date':<12} {'Payee':<30} {'Amount':>10} {'Category':<25} {'Account':<15}")
        print("  " + "-" * 88)

        for r in rows:
            cat = r["category_name"] or ""
            flag = ""
            if not cat or cat == "Uncategorized":
                flag = " [!]"
                cat = "(uncategorized)"

            payee = (r["payee_name"] or "")[:28]
            cat_display = cat[:23]
            account = (r["account_name"] or "")[:13]
            # "reconciled" is a fully-cleared, locked state (more final than "cleared"),
            # so only "uncleared" should count as pending here. See #127.
            cleared = " (pending)" if r["cleared"] == "uncleared" else ""

            print(f"  {r['date']:<12} {payee:<30} ${r['amount']:>9,.2f} {cat_display:<25} {account}{flag}{cleared}")

        print()
        print("Note: On-budget transfers are normally 'Uncategorized' in YNAB.")
        print("      Only debt account payments without a category need attention.")
    finally:
        conn.close()


def _generate_import_id(date: str, amount_mu: int, payee: str) -> str:
    """Generate a deterministic import_id for YNAB deduplication."""
    unique = hashlib.md5(f"{payee}:{date}".encode()).hexdigest()[:8]
    return f"YNAB:{amount_mu}:{date}:{unique}"


def _find_category(conn: sqlite3.Connection, search: str) -> dict | None:
    """Find a category by name for the current month.

    Resolution order:
      1. Exact match on the full search string (case-insensitive) - catches "Person: Reimbursables"
         where YNAB stores the full group-prefixed name as the category name.
      2. Exact match on parsed short name, filtered by group qualifier.
      3. Substring match on short name, filtered by group qualifier.

    Accepts "Group: Category" or "Category (Group)" to disambiguate when multiple
    categories share the same short name.
    """
    month = current_month()

    # Step 1: exact match on the raw search string before any parsing.
    # This handles categories whose stored name already contains a colon
    # (e.g. "Person: Reimbursables") so the colon-split heuristic does not
    # incorrectly strip the group prefix and fall into fuzzy matching.
    # hidden intentionally omitted: INSERT OR REPLACE propagates current hidden flag to historical rows
    full_exact = conn.execute(
        """
        SELECT id, name, category_group_name FROM budget_categories
        WHERE budget_month = ?
          AND deleted = 0
          AND LOWER(name) = LOWER(?)
        ORDER BY name
    """,
        (month, search),
    ).fetchall()

    if len(full_exact) == 1:
        return dict(full_exact[0])
    if len(full_exact) > 1:
        print(f"Multiple categories match '{search}':")
        for r in full_exact:
            print(f"  {r['name']}  (group: {r['category_group_name']})")
        print("\nDisambiguate with 'Group: Category' or 'Category (Group)' format.")
        print("Example: 'Business: Licenses & Fees' or 'Licenses & Fees (Business)'")
        return None

    # Step 2 & 3: parse the search string and try short-name matching.
    cat_name, group_filter = parse_category_input(search)

    def _apply_group_filter(rows, group):
        if group is None:
            return rows
        return [r for r in rows if r["category_group_name"].lower() == group.lower()]

    # Exact match on short name (case-insensitive)
    exact = conn.execute(
        """
        SELECT id, name, category_group_name FROM budget_categories
        WHERE budget_month = ?
          AND deleted = 0
          AND LOWER(name) = LOWER(?)
        ORDER BY name
    """,
        (month, cat_name),
    ).fetchall()

    if exact:
        filtered = _apply_group_filter(exact, group_filter)
        candidates = filtered if filtered else exact
        if len(candidates) == 1:
            return dict(candidates[0])
        print(f"Multiple categories match '{search}':")
        for r in candidates:
            print(f"  {r['name']}  (group: {r['category_group_name']})")
        print("\nDisambiguate with 'Group: Category' or 'Category (Group)' format.")
        print("Example: 'Business: Licenses & Fees' or 'Licenses & Fees (Business)'")
        return None

    # Substring match on short name
    rows = conn.execute(
        """
        SELECT id, name, category_group_name FROM budget_categories
        WHERE budget_month = ?
          AND deleted = 0
          AND LOWER(name) LIKE LOWER(?)
        ORDER BY name
    """,
        (month, f"%{cat_name}%"),
    ).fetchall()

    if not rows:
        print(f"No category found matching '{search}'")
        return None

    filtered = _apply_group_filter(rows, group_filter)
    candidates = filtered if filtered else rows

    if len(candidates) == 1:
        return dict(candidates[0])

    print(f"Multiple categories match '{search}':")
    for r in candidates:
        print(f"  {r['name']}  (group: {r['category_group_name']})")
    print("\nDisambiguate with 'Group: Category' or 'Category (Group)' format.")
    print("Example: 'Business: Licenses & Fees' or 'Licenses & Fees (Business)'")
    return None


def run_add(
    account: str,
    amount: float,
    payee: str,
    date: str | None = None,
    memo: str | None = None,
    cleared: str = "cleared",
    inflow: bool = False,
    apply: bool = False,
    category: str | None = None,
) -> None:
    """Create a new transaction in YNAB."""
    conn = get_connection()
    try:
        init_db(conn)

        # Resolve account
        acct = find_account(conn, account)
        if not acct:
            print(f"No account found matching '{account}'")
            return

        # Resolve category
        cat_id = None
        cat_name = None
        if category:
            cat = _find_category(conn, category)
            if not cat:
                return
            cat_id = cat["id"]
            cat_name = cat["name"]

        # Default date to today
        if not date:
            date = datetime.now().strftime("%Y-%m-%d")

        # Convert to milliunits; --inflow flips the sign for convenience
        if inflow:
            amount = abs(amount)
        else:
            amount = -abs(amount)
        amount_mu = round(amount * 1000)

        import_id = _generate_import_id(date, amount_mu, payee)

        # Preview
        sign = "+" if amount >= 0 else "-"
        print(f"Account:  {acct['name']}")
        print(f"Date:     {date}")
        print(f"Payee:    {payee}")
        print(f"Amount:   {sign}${abs(amount):,.2f}")
        if cat_name:
            print(f"Category: {cat_name}")
        if memo:
            print(f"Memo:     {memo}")
        print(f"Status:   {cleared}")
        print()

        if not apply:
            try:
                confirm = input("Create transaction? [y/N] ").strip().lower()
            except EOFError:
                confirm = "n"
            if confirm != "y":
                print("Cancelled. Use --apply to skip confirmation.")
                return

        # Push to YNAB
        token, plan_id = require_credentials()
        client = YNABClient(token, plan_id)

        txn = {
            "account_id": acct["id"],
            "date": date,
            "amount": amount_mu,
            "payee_name": payee,
            "cleared": cleared,
            "approved": True,
            "import_id": import_id,
        }
        if cat_id:
            txn["category_id"] = cat_id
        if memo:
            txn["memo"] = memo

        result = client.create_transaction(txn)
        if result:
            sign = "+" if amount >= 0 else "-"
            detail = f"{date} | {payee} | {sign}${abs(amount):,.2f} | {acct['name']}"
            if cat_name:
                detail += f" | {cat_name}"
            if memo:
                detail += f" | {memo}"
            log_audit(conn, "create-transaction", "transaction", result.get("id"), payee, detail, "add")
            conn.commit()
            print("Transaction created successfully.")
            print("Run 'ynab sync' to update local data.")
        else:
            print("Failed to create transaction. Check logs.")
    finally:
        conn.close()


def run_update(
    transaction_id: str,
    category: str | None = None,
    memo: str | None = None,
    splits: list[dict] | None = None,
    apply: bool = False,
) -> None:
    """Update an existing transaction's category, memo, or split it across categories.

    splits: list of {"category": str, "amount": float} dicts (amounts as positive dollars for outflows).
    """
    if category is None and memo is None and not splits:
        print("Nothing to update. Provide --category, --memo, and/or --split.")
        return

    if splits and category:
        print("Cannot use --split together with --category. Use --split to set per-split categories.")
        return

    conn = get_connection()
    try:
        init_db(conn)

        # Look up transaction by ID (prefix match)
        rows = conn.execute(
            """
            SELECT t.id, t.date, COALESCE(t.payee_name, p.name) AS payee_name,
                   t.amount, t.category_name, t.category_id, t.memo, t.account_name
            FROM transactions t
            LEFT JOIN payees p ON t.payee_id = p.id
            WHERE t.deleted = 0 AND t.id LIKE ?
            ORDER BY t.date DESC
            """,
            (f"{transaction_id}%",),
        ).fetchall()

        if not rows:
            print(f"No transaction found matching '{transaction_id}'")
            return
        if len(rows) > 1:
            print(f"Multiple transactions match '{transaction_id}':")
            for r in rows:
                cat = r["category_name"] or "(uncategorized)"
                print(f"  {r['id'][:12]}  {r['date']}  {r['payee_name']:<30} ${abs(r['amount']):>10,.2f}  {cat}")
            print("\nUse a longer ID prefix to be more specific.")
            return

        txn = dict(rows[0])

        # ── Split mode ──
        if splits:
            _run_update_split(conn, txn, splits, apply)
            return

        # ── Simple update (category / memo) ──
        fields = {}
        cat_name = None
        _UNCATEGORIZE_SENTINELS = {"", "uncategorized"}
        if category is not None:
            if category.strip().lower() in _UNCATEGORIZE_SENTINELS:
                # Caller wants to clear the category (YNAB API: category_id = null)
                if txn["category_id"] is None:
                    print("Transaction is already uncategorized. Nothing to change.")
                    return
                fields["category_id"] = None
                cat_name = None  # signals uncategorize in preview/audit below
            else:
                cat = _find_category(conn, category)
                if not cat:
                    return
                if cat["id"] == txn["category_id"]:
                    print(f"Transaction is already categorized as '{cat['name']}'. Nothing to change.")
                    return
                fields["category_id"] = cat["id"]
                cat_name = cat["name"]

        if memo is not None:
            fields["memo"] = memo

        # Determine whether a category change is pending (includes the uncategorize case)
        _clearing_category = "category_id" in fields and fields["category_id"] is None

        # Preview
        sign = "-" if txn["amount"] < 0 else "+"
        print(
            f"  Transaction: {txn['date']} | {txn['payee_name']}"
            f" | {sign}${abs(txn['amount']):,.2f} | {txn['account_name']}"
        )
        if cat_name:
            print(f"  Category:    {txn['category_name'] or '(uncategorized)'} → {cat_name}")
        elif _clearing_category:
            print(f"  Category:    {txn['category_name'] or '(uncategorized)'} → (uncategorized)")
        if memo is not None:
            print(f"  Memo:        {txn['memo'] or '(none)'} → {memo}")
        print()

        if not apply:
            try:
                confirm = input("Update transaction? [y/N] ").strip().lower()
            except EOFError:
                confirm = "n"
            if confirm != "y":
                print("Cancelled. Use --apply to skip confirmation.")
                return

        # Push to YNAB
        token, plan_id = require_credentials()
        client = YNABClient(token, plan_id)

        ok = client.update_transaction(txn["id"], **fields)
        if ok:
            # Update local DB immediately so tools see the change without sync
            local_updates = []
            local_params = []
            if cat_name:
                local_updates.extend(["category_id = ?", "category_name = ?"])
                local_params.extend([fields["category_id"], cat_name])
            elif _clearing_category:
                local_updates.extend(["category_id = ?", "category_name = ?"])
                local_params.extend([None, None])
            if memo is not None:
                local_updates.append("memo = ?")
                local_params.append(memo)
            if local_updates:
                local_params.append(txn["id"])
                # local_updates contains only hardcoded column=? strings, not user input
                conn.execute(
                    f"UPDATE transactions SET {', '.join(local_updates)} WHERE id = ?",
                    local_params,
                )

            changes = []
            if cat_name:
                changes.append(f"category: {txn['category_name'] or '(uncategorized)'} → {cat_name}")
            elif _clearing_category:
                changes.append(f"category: {txn['category_name'] or '(uncategorized)'} → (uncategorized)")
            if memo is not None:
                changes.append(f"memo: {txn['memo'] or '(none)'} → {memo}")
            detail = f"{txn['date']} | {txn['payee_name']} | {'; '.join(changes)}"
            log_audit(conn, "update-transaction", "transaction", txn["id"], txn["payee_name"], detail, "update")
            conn.commit()
            print("Transaction updated successfully.")
        else:
            print("Failed to update transaction. Check logs.")
    finally:
        conn.close()


def _run_update_split(conn: sqlite3.Connection, txn: dict, splits: list[dict], apply: bool) -> None:
    """Apply a split to an existing transaction via YNAB API.

    splits: [{"category": str, "amount": float}, ...] - amounts as positive dollars for outflows.
    """
    import uuid

    # Resolve each split's category name → ID
    resolved: list[dict] = []
    for item in splits:
        cat_search = item.get("category") or ""
        amount_dollars = item.get("amount")
        if not cat_search or amount_dollars is None:
            print(f"Invalid split entry: {item}. Need 'category' and 'amount'.")
            return
        if float(amount_dollars) <= 0:
            print(f"Invalid split amount {amount_dollars}: must be a positive number.")
            return
        cat = _find_category(conn, cat_search)
        if not cat:
            return  # _find_category already printed error
        resolved.append(
            {"category_id": cat["id"], "category_name": cat["name"], "amount_dollars": float(amount_dollars)}
        )

    # Validate: split amounts must sum to the transaction total (outflows are negative)
    txn_amount = txn["amount"]  # already in dollars (negative for outflows)
    is_outflow = txn_amount < 0
    # User supplies positive dollar amounts; match sign of transaction
    sign = -1 if is_outflow else 1
    split_total = sum(r["amount_dollars"] for r in resolved)
    expected_total = abs(txn_amount)

    if abs(split_total - expected_total) > 0.005:
        print(f"Split amounts ${split_total:,.2f} do not match transaction total ${expected_total:,.2f}.")
        print("The split amounts must add up exactly to the transaction amount.")
        return

    # Preview
    sign_char = "-" if is_outflow else "+"
    print(
        f"  Transaction: {txn['date']} | {txn['payee_name']}"
        f" | {sign_char}${abs(txn_amount):,.2f} | {txn['account_name']}"
    )
    print(f"  Split into {len(resolved)} categories:")
    for r in resolved:
        print(f"    {sign_char}${r['amount_dollars']:>9,.2f}  →  {r['category_name']}")
    print()

    if not apply:
        try:
            confirm = input("Apply split? [y/N] ").strip().lower()
        except EOFError:
            confirm = "n"
        if confirm != "y":
            print("Cancelled. Use --apply to skip confirmation.")
            return

    # Build YNAB subtransactions payload (milliunits, negative for outflows)
    subtransactions = [
        {
            "amount": round(sign * r["amount_dollars"] * 1000),
            "category_id": r["category_id"],
        }
        for r in resolved
    ]

    token, plan_id = require_credentials()
    client = YNABClient(token, plan_id)

    ok = client.split_transaction(txn["id"], subtransactions)
    if ok:
        # Update local DB: mark parent as split, delete old subtransactions, insert new ones
        conn.execute(
            "UPDATE transactions SET category_id = NULL, category_name = 'Split' WHERE id = ?",
            (txn["id"],),
        )
        conn.execute("DELETE FROM subtransactions WHERE transaction_id = ?", (txn["id"],))
        now_iso = datetime.now().isoformat()
        for i, r in enumerate(resolved):
            fake_id = str(uuid.uuid4())
            conn.execute(
                """INSERT OR REPLACE INTO subtransactions
                   (id, transaction_id, amount, memo, payee_id, payee_name,
                    category_id, category_name, transfer_account_id, deleted, last_synced_at)
                   VALUES (?, ?, ?, NULL, NULL, NULL, ?, ?, NULL, 0, ?)""",
                (
                    fake_id,
                    txn["id"],
                    round(sign * r["amount_dollars"], 2),  # column stores dollars, not milliunits
                    r["category_id"],
                    r["category_name"],
                    now_iso,
                ),
            )

        split_detail = "; ".join(f"{r['category_name']} ${r['amount_dollars']:,.2f}" for r in resolved)
        detail = f"{txn['date']} | {txn['payee_name']} | split: {split_detail}"
        log_audit(conn, "split-transaction", "transaction", txn["id"], txn["payee_name"], detail, "update")
        conn.commit()
        print("Transaction split successfully.")
        print("Run 'ynab sync' to refresh local data with YNAB-assigned subtransaction IDs.")
    else:
        print("Failed to split transaction. Check logs.")


def run_delete(transaction_id: str, apply: bool = False) -> None:
    """Delete a transaction from YNAB."""
    conn = get_connection()
    try:
        init_db(conn)

        # Look up transaction by ID (prefix match)
        rows = conn.execute(
            """
            SELECT t.id, t.date, COALESCE(t.payee_name, p.name) AS payee_name,
                   t.amount, t.category_name, t.memo, t.account_name
            FROM transactions t
            LEFT JOIN payees p ON t.payee_id = p.id
            WHERE t.deleted = 0 AND t.id LIKE ?
            ORDER BY t.date DESC
            """,
            (f"{transaction_id}%",),
        ).fetchall()

        if not rows:
            print(f"No transaction found matching '{transaction_id}'")
            return
        if len(rows) > 1:
            print(f"Multiple transactions match '{transaction_id}':")
            for r in rows:
                cat = r["category_name"] or "(uncategorized)"
                print(f"  {r['id'][:12]}  {r['date']}  {r['payee_name']:<30} ${abs(r['amount']):>10,.2f}  {cat}")
            print("\nUse a longer ID prefix to be more specific.")
            return

        txn = dict(rows[0])

        # Preview
        sign = "-" if txn["amount"] < 0 else "+"
        print(
            f"  Transaction: {txn['date']} | {txn['payee_name']}"
            f" | {sign}${abs(txn['amount']):,.2f} | {txn['account_name']}"
        )
        print(f"  Category:    {txn['category_name'] or '(uncategorized)'}")
        if txn["memo"]:
            print(f"  Memo:        {txn['memo']}")
        print()

        if not apply:
            try:
                confirm = input("DELETE this transaction? This cannot be undone. [y/N] ").strip().lower()
            except EOFError:
                confirm = "n"
            if confirm != "y":
                print("Cancelled. Use --apply to skip confirmation.")
                return

        # Push to YNAB
        token, plan_id = require_credentials()
        client = YNABClient(token, plan_id)

        ok = client.delete_transaction(txn["id"])
        if ok:
            detail = f"{txn['date']} | {txn['payee_name']} | {sign}${abs(txn['amount']):,.2f}"
            log_audit(conn, "delete-transaction", "transaction", txn["id"], txn["payee_name"], detail, "delete")
            conn.execute("UPDATE transactions SET deleted = 1 WHERE id = ?", (txn["id"],))
            conn.commit()
            print("Transaction deleted successfully.")
        else:
            print("Failed to delete transaction. Check logs.")
    finally:
        conn.close()


def run_unapproved() -> None:
    """Show unapproved transactions that need review."""
    conn = get_connection()
    try:
        init_db(conn)

        # Find transactions needing attention: unapproved OR missing category.
        # Exclude: transfers, split parents, tracking/loan accounts (no categories).
        rows = conn.execute(
            """
            SELECT t.id, t.date, COALESCE(t.payee_name, p.name) AS payee_name, t.account_name,
                   t.category_name AS category,
                   t.amount,
                   t.memo,
                   t.approved
            FROM transactions t
            JOIN accounts a ON a.id = t.account_id
            LEFT JOIN payees p ON t.payee_id = p.id
            WHERE t.deleted = 0
              AND t.transfer_account_id IS NULL
              AND a.type NOT IN ('otherAsset', 'otherLiability', 'autoLoan',
                                 'studentLoan', 'personalLoan', 'medicalDebt',
                                 'mortgage')
              AND NOT EXISTS (
                  SELECT 1 FROM subtransactions st
                  WHERE st.transaction_id = t.id AND st.deleted = 0
              )
              AND (t.approved = 0
                   OR ((t.category_name IS NULL OR t.category_name = '' OR t.category_name = 'Uncategorized')
                       AND t.date >= date('now', '-30 days')))
              AND COALESCE(t.category_name, '') NOT IN ('Split', 'Split (Multiple Categories...)')
            ORDER BY t.date DESC, payee_name
            """,
        ).fetchall()

        if not rows:
            print("No unapproved transactions found.")
            return

        # Verify approval status against YNAB API for unapproved rows
        # (delta sync can miss approvals). Uses a single filtered get_transactions()
        # call scoped to the earliest unapproved date, rather than one
        # get_transaction() call per row - keeps API call count constant
        # regardless of how many unapproved transactions exist (issue #77).
        stale_ids = set()
        approval_overrides: dict[str, bool] = {}
        seen_txn_ids = set()
        unapproved_ids: set[str] = set()
        min_date: str | None = None
        for row in rows:
            txn_id = row["id"]
            if txn_id in seen_txn_ids:
                continue  # Skip duplicate rows from split joins
            seen_txn_ids.add(txn_id)
            if row["approved"]:
                continue  # Already approved locally, no need to verify
            unapproved_ids.add(txn_id)
            if min_date is None or row["date"] < min_date:
                min_date = row["date"]

        if unapproved_ids:
            token, plan_id = require_credentials()
            client = YNABClient(token, plan_id)
            try:
                api_transactions = client.get_transactions(since_date=min_date)["transactions"]
            except Exception as exc:
                logger.debug("API approval verification fetch failed: %s", exc)
                api_transactions = []
            api_by_id = {t["id"]: t for t in api_transactions}
            for txn_id in unapproved_ids:
                api_txn = api_by_id.get(txn_id)
                if api_txn and api_txn.get("approved"):
                    stale_ids.add(txn_id)
                    approval_overrides[txn_id] = True
                    conn.execute(
                        "UPDATE transactions SET approved = 1 WHERE id = ?",
                        (txn_id,),
                    )

        if stale_ids:
            conn.commit()
            logger.info(f"Fixed {len(stale_ids)} stale approval(s) from API verification")

        # Separate into: needs category vs unapproved-but-categorized
        needs_category = []
        ready_to_approve = []
        seen_for_display = set()
        for row in rows:
            txn_id = row["id"]
            if txn_id in seen_for_display:
                continue
            seen_for_display.add(txn_id)
            cat = row["category"] or ""
            is_approved = row["approved"] or approval_overrides.get(txn_id, False)
            if not cat or cat == "Uncategorized":
                needs_category.append(row)
            elif not is_approved:
                ready_to_approve.append(row)
            # else: approved with a category - nothing to do

        total = len(needs_category) + len(ready_to_approve)
        if not total:
            print("No unapproved transactions found.")
            return

        print(f"Transactions Needing Review ({total} total)")
        print("=" * 110)

        if needs_category:
            print(f"\nNEEDS CATEGORY ({len(needs_category)}):")
            print(f"  {'Date':<12} {'Payee':<25} {'Category':<30} {'Amount':>10} {'Account':<15}")
            print("  " + "-" * 108)
            for row in needs_category:
                payee = (row["payee_name"] or "")[:23]
                account = (row["account_name"] or "")[:13]
                amount = row["amount"] or 0
                print(f"  {row['date']:<10} {payee:<25} {'(uncategorized)':<30} ${amount:>9,.2f} {account}")

        if ready_to_approve:
            print(f"\nREADY TO APPROVE ({len(ready_to_approve)}):")
            print(f"  {'Date':<12} {'Payee':<25} {'Category':<30} {'Amount':>10} {'Account':<15}")
            print("  " + "-" * 108)
            for row in ready_to_approve:
                payee = (row["payee_name"] or "")[:23]
                cat = (row["category"] or "")[:28]
                account = (row["account_name"] or "")[:13]
                amount = row["amount"] or 0
                print(f"  {row['date']:<10} {payee:<25} {cat:<30} ${amount:>9,.2f} {account}")

        print()
        if ready_to_approve:
            print(f"Run 'ynab approve --all' to approve {len(ready_to_approve)} categorized transactions.")
        if needs_category:
            print(f"Run 'ynab categorize' to suggest categories for {len(needs_category)} uncategorized transactions.")
    finally:
        conn.close()


def run_approve(
    transaction_id: str | None = None,
    all_categorized: bool = False,
    apply: bool = False,
) -> None:
    """Approve unapproved transactions."""
    conn = get_connection()
    try:
        init_db(conn)

        if all_categorized:
            # Find all unapproved transactions that have a category
            rows = conn.execute(
                """
                SELECT t.id, t.date, COALESCE(t.payee_name, p.name) AS payee_name,
                       t.amount, t.category_name, t.account_name
                FROM transactions t
                LEFT JOIN payees p ON t.payee_id = p.id
                WHERE t.deleted = 0 AND t.approved = 0
                  AND t.category_name IS NOT NULL
                  AND t.category_name NOT IN ('', 'Uncategorized',
                      'Split', 'Split (Multiple Categories...)')
                ORDER BY t.date DESC
                """,
            ).fetchall()

            if not rows:
                print("No categorized unapproved transactions to approve.")
                return

            print(f"Approving {len(rows)} categorized transactions:")
            print(f"  {'Date':<12} {'Payee':<25} {'Category':<30} {'Amount':>10}")
            print("  " + "-" * 78)
            for row in rows:
                payee = (row["payee_name"] or "")[:23]
                cat = (row["category_name"] or "")[:28]
                print(f"  {row['date']:<10} {payee:<25} {cat:<30} ${row['amount']:>9,.2f}")
            print()

            if not apply:
                try:
                    confirm = input(f"Approve all {len(rows)} transactions? [y/N] ").strip().lower()
                except EOFError:
                    confirm = "n"
                if confirm != "y":
                    print("Cancelled. Use --apply to skip confirmation.")
                    return

            # Bulk approve via API
            token, plan_id = require_credentials()
            client = YNABClient(token, plan_id)

            updates = [{"id": row["id"], "approved": True} for row in rows]
            result = client.bulk_update_transactions(updates)

            if result["success"] > 0:
                detail = f"Bulk approved {result['success']} transactions"
                log_audit(conn, "approve-transactions", "transaction", "bulk", "multiple", detail, "approve")
                conn.commit()
                print(f"Approved {result['success']} transactions.")
                if result["failed"] > 0:
                    print(f"  {result['failed']} failed - check logs.")
                print("Run 'ynab sync' to update local data.")
            else:
                print("Failed to approve transactions. Check logs.")

        elif transaction_id:
            # Approve a single transaction by ID prefix
            rows = conn.execute(
                """
                SELECT t.id, t.date, COALESCE(t.payee_name, p.name) AS payee_name,
                       t.amount, t.category_name, t.account_name, t.approved
                FROM transactions t
                LEFT JOIN payees p ON t.payee_id = p.id
                WHERE t.deleted = 0 AND t.id LIKE ?
                ORDER BY t.date DESC
                """,
                (f"{transaction_id}%",),
            ).fetchall()

            if not rows:
                print(f"No transaction found matching '{transaction_id}'")
                return
            if len(rows) > 1:
                print(f"Multiple transactions match '{transaction_id}':")
                for r in rows:
                    cat = r["category_name"] or "(uncategorized)"
                    status = "approved" if r["approved"] else "unapproved"
                    print(
                        f"  {r['id'][:12]}  {r['date']}  {r['payee_name']:<30}"
                        f" ${abs(r['amount']):>10,.2f}  {cat}  [{status}]"
                    )
                print("\nUse a longer ID prefix to be more specific.")
                return

            txn = dict(rows[0])

            if txn["approved"]:
                print(f"Transaction is already approved: {txn['date']} | {txn['payee_name']}")
                return

            cat = txn["category_name"] or "(uncategorized)"
            sign = "-" if txn["amount"] < 0 else "+"
            print(
                f"  Transaction: {txn['date']} | {txn['payee_name']}"
                f" | {sign}${abs(txn['amount']):,.2f} | {txn['account_name']}"
            )
            print(f"  Category:    {cat}")
            print()

            if not apply:
                try:
                    confirm = input("Approve this transaction? [y/N] ").strip().lower()
                except EOFError:
                    confirm = "n"
                if confirm != "y":
                    print("Cancelled. Use --apply to skip confirmation.")
                    return

            token, plan_id = require_credentials()
            client = YNABClient(token, plan_id)

            ok = client.update_transaction(txn["id"], approved=True)
            if ok:
                detail = f"{txn['date']} | {txn['payee_name']} | {cat}"
                log_audit(conn, "approve-transaction", "transaction", txn["id"], txn["payee_name"], detail, "approve")
                conn.commit()
                print("Transaction approved.")
                print("Run 'ynab sync' to update local data.")
            else:
                print("Failed to approve transaction. Check logs.")
        else:
            print("Provide a transaction ID or use --all to approve all categorized transactions.")
    finally:
        conn.close()


def _format_goal_type(goal_type: str) -> str:
    """Format YNAB goal type for display."""
    type_map = {
        "TB": "Target Balance",
        "TBD": "Target by Date",
        "MF": "Monthly Funding",
        "NEED": "Needed for Spending",
        "DEBT": "Debt Payment",
    }
    return type_map.get(goal_type, goal_type or "")
