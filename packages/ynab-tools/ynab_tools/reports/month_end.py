"""Month-end closeout report: consolidated view of budget health for a completed month."""

import logging
import os
import sqlite3
from calendar import monthrange
from datetime import datetime

from ..db import get_connection, init_db
from ..stats import should_skip_group

logger = logging.getLogger(__name__)


def _get_protected_config() -> tuple[set[str], set[str]]:
    """Load protected category configuration from env vars.

    Returns (protected_groups, protected_names).

    Configure via environment variables:
      YNAB_PROTECTED_GROUPS  - comma-separated group name fragments (case-insensitive match)
      YNAB_PROTECTED_NAMES   - comma-separated exact category names

    Defaults are generic terms likely to match common savings/reserve categories.
    """
    groups_str = os.environ.get(
        "YNAB_PROTECTED_GROUPS",
        "Emergency,Savings,Retirement,Holding,Investment",
    )
    names_str = os.environ.get("YNAB_PROTECTED_NAMES", "")
    groups = {g.strip() for g in groups_str.split(",") if g.strip()}
    names = {n.strip() for n in names_str.split(",") if n.strip()}
    return groups, names


def _resolve_month(month: str | None) -> str:
    """Resolve target month to YYYY-MM-01 format.

    If month is None: auto-detect based on day of month.
    Days 1-5 default to previous month; otherwise current month.
    """
    if month:
        # Accept YYYY-MM or YYYY-MM-01
        return month[:7] + "-01"

    now = datetime.now()
    if now.day <= 5:
        # Roll back to previous month
        if now.month == 1:
            return f"{now.year - 1}-12-01"
        return f"{now.year}-{now.month - 1:02d}-01"
    return now.strftime("%Y-%m-01")


def _end_of_month(month_str: str) -> str:
    """Return the last day of the month as YYYY-MM-DD."""
    year = int(month_str[:4])
    month = int(month_str[5:7])
    last_day = monthrange(year, month)[1]
    return f"{year}-{month:02d}-{last_day:02d}"


def _is_protected(name: str, group: str | None) -> bool:
    """Check if a category is in a protected group or has a protected name.

    Matches protected group fragments against both the category group name
    AND the category name itself, since YNAB doesn't always populate group names.
    """
    protected_groups, protected_names = _get_protected_config()
    if name in protected_names:
        return True
    name_lower = name.lower()
    group_lower = group.lower() if group else ""
    for pg in protected_groups:
        pg_lower = pg.lower()
        if pg_lower in name_lower or pg_lower in group_lower:
            return True
    return False


def run_month_end(month: str | None = None) -> None:
    """Produce a consolidated month-end closeout report.

    Args:
        month: Optional month in YYYY-MM format. Auto-detects if None
               (days 1-5 default to previous month, otherwise current).
    """
    conn = get_connection()
    try:
        init_db(conn)
        target = _resolve_month(month)
        label = target[:7]
        eom = _end_of_month(target)

        print("Month-End Closeout Report")
        print("=" * 65)
        print(f"Month: {label}")
        print()

        _section_income(conn, target, label)
        _section_spending_vs_budget(conn, target)
        _section_overspent(conn, target)
        _section_uncategorized_unapproved(conn, target, eom)
        _section_pending(conn, target, eom)
        _section_surplus(conn, target)
        _section_planned(conn, eom)

    finally:
        conn.close()


# ── Section 1: Income Summary ──


def _section_income(conn: sqlite3.Connection, target: str, label: str) -> None:
    row = conn.execute(
        "SELECT income FROM budget_months WHERE month = ?",
        (target,),
    ).fetchone()

    if not row:
        print("INCOME SUMMARY")
        print("-" * 40)
        print(f"  No budget data for {label}. Run 'ynab sync' first.")
        print()
        return

    income = row["income"] or 0

    # Prior month
    year = int(target[:4])
    mo = int(target[5:7])
    if mo == 1:
        prior = f"{year - 1}-12-01"
    else:
        prior = f"{year}-{mo - 1:02d}-01"

    prior_row = conn.execute(
        "SELECT income FROM budget_months WHERE month = ?",
        (prior,),
    ).fetchone()
    prior_income = (prior_row["income"] or 0) if prior_row else None

    print("INCOME SUMMARY")
    print("-" * 40)
    print(f"  {label} income:  ${income:>12,.2f}")
    if prior_income is not None:
        delta = income - prior_income
        direction = "+" if delta >= 0 else ""
        print(f"  Prior month:    ${prior_income:>12,.2f}")
        print(f"  Change:         ${direction}{delta:>11,.2f}")
    print()


# ── Section 2: Spending vs Budget ──


def _section_spending_vs_budget(conn: sqlite3.Connection, target: str) -> None:
    row = conn.execute(
        "SELECT income, budgeted FROM budget_months WHERE month = ?",
        (target,),
    ).fetchone()

    if not row:
        return

    income = row["income"] or 0
    budgeted = row["budgeted"] or 0

    # Sum spending from non-infrastructure categories to avoid double-counting CC payments.
    # hidden intentionally omitted: INSERT OR REPLACE propagates current hidden flag to historical rows
    cat_rows = conn.execute(
        """
        SELECT category_group_name, activity
        FROM budget_categories
        WHERE budget_month = ? AND deleted = 0
        """,
        (target,),
    ).fetchall()
    activity = sum(float(r["activity"] or 0) for r in cat_rows if not should_skip_group(r["category_group_name"] or ""))
    net = income + activity  # activity is negative for spending

    print("SPENDING VS BUDGET")
    print("-" * 40)
    print(f"  Total budgeted:  ${budgeted:>12,.2f}")
    print(f"  Total activity:  ${activity:>12,.2f}")
    print(f"  Net (income + activity): ${net:>6,.2f}")
    print()


# ── Section 3: Overspent Categories ──


def _section_overspent(conn: sqlite3.Connection, target: str) -> None:
    # Get overspent categories for the target month.
    # hidden intentionally omitted: INSERT OR REPLACE propagates current hidden flag to historical rows
    overspent = [
        dict(r)
        for r in conn.execute(
            """
            SELECT name, category_group_name, budgeted, activity, balance
            FROM budget_categories
            WHERE budget_month = ? AND deleted = 0
              AND balance < 0
            ORDER BY balance
        """,
            (target,),
        ).fetchall()
    ]

    print("OVERSPENT CATEGORIES")
    print("-" * 65)

    if not overspent:
        print("  None -- all categories have positive or zero balances.")
        print()
        return

    # Check structural overspending: overspent in 3+ of last 6 months
    year = int(target[:4])
    mo = int(target[5:7])
    prior_months = []
    for i in range(1, 7):
        pm = mo - i
        py = year
        while pm <= 0:
            pm += 12
            py -= 1
        prior_months.append(f"{py}-{pm:02d}-01")

    structural_names: set[str] = set()
    for cat in overspent:
        count = 0
        for pm in prior_months:
            row = conn.execute(
                """
                SELECT balance FROM budget_categories
                WHERE budget_month = ? AND name = ? AND deleted = 0
            """,
                (pm, cat["name"]),
            ).fetchone()
            if row and (row["balance"] or 0) < 0:
                count += 1
        if count >= 3:
            structural_names.add(cat["name"])

    total_over = sum(c["balance"] for c in overspent)
    print(f"  {len(overspent)} categories, ${total_over:,.2f} total")
    print()
    print(f"  {'Category':<32} {'Budgeted':>10} {'Activity':>10} {'Balance':>10}  Flag")
    print("  " + "-" * 78)
    for c in overspent:
        budgeted = c["budgeted"] or 0
        activity = c["activity"] or 0
        balance = c["balance"] or 0
        flag = "STRUCTURAL" if c["name"] in structural_names else ""
        print(f"  {c['name']:<32} ${budgeted:>9,.2f} ${activity:>9,.2f} ${balance:>9,.2f}  {flag}")
    print()


# ── Section 4: Uncategorized / Unapproved ──


def _section_uncategorized_unapproved(conn: sqlite3.Connection, target: str, eom: str) -> None:
    month_start = target
    # Count uncategorized
    uncat = conn.execute(
        """
        SELECT COUNT(*) AS cnt FROM transactions t
        JOIN accounts a ON a.id = t.account_id
        WHERE t.deleted = 0
          AND a.on_budget = 1
          AND t.date >= ? AND t.date <= ?
          AND (t.category_name IS NULL OR t.category_name = '')
          AND t.transfer_account_id IS NULL
          AND t.payee_name != 'Split (Multiple Categories...)'
          AND t.payee_name NOT LIKE 'Transfer%'
          AND NOT EXISTS (
              SELECT 1 FROM subtransactions
              WHERE transaction_id = t.id
                AND deleted = 0
          )
    """,
        (month_start, eom),
    ).fetchone()["cnt"]

    # Count unapproved
    unapp = conn.execute(
        """
        SELECT COUNT(*) AS cnt FROM transactions
        WHERE deleted = 0
          AND date >= ? AND date <= ?
          AND approved = 0
    """,
        (month_start, eom),
    ).fetchone()["cnt"]

    print("UNCATEGORIZED / UNAPPROVED")
    print("-" * 40)
    print(f"  Uncategorized: {uncat}")
    print(f"  Unapproved:    {unapp}")
    if uncat > 0 or unapp > 0:
        print("  --> Run 'ynab unapproved' to review")
    print()


# ── Section 5: Pending Transactions ──


def _section_pending(conn: sqlite3.Connection, target: str, eom: str) -> None:
    pending = conn.execute(
        """
        SELECT COUNT(*) AS cnt FROM transactions t
        JOIN accounts a ON a.id = t.account_id
        WHERE t.deleted = 0
          AND a.on_budget = 1
          AND t.date >= ? AND t.date <= ?
          AND t.cleared != 'cleared'
          AND t.cleared != 'reconciled'
    """,
        (target, eom),
    ).fetchone()["cnt"]

    print("PENDING TRANSACTIONS")
    print("-" * 40)
    print(f"  Uncleared: {pending}")
    print()


# ── Section 6: Category Surplus ──


def _section_surplus(conn: sqlite3.Connection, target: str) -> None:
    # hidden intentionally omitted: INSERT OR REPLACE propagates current hidden flag to historical rows
    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT name, category_group_name, balance
            FROM budget_categories
            WHERE budget_month = ? AND deleted = 0
              AND balance > 0
            ORDER BY balance DESC
        """,
            (target,),
        ).fetchall()
    ]

    # Filter out protected categories and infrastructure/excluded groups
    non_protected = [
        r
        for r in rows
        if not _is_protected(r["name"], r["category_group_name"])
        and not should_skip_group(r["category_group_name"] or "")
    ]

    print("CATEGORY SURPLUS (top 10, non-protected)")
    print("-" * 50)

    if not non_protected:
        print("  No non-protected categories with positive balance.")
        print()
        return

    for r in non_protected[:10]:
        print(f"  {r['name']:<36} ${r['balance']:>10,.2f}")
    if len(non_protected) > 10:
        remaining = sum(r["balance"] for r in non_protected[10:])
        print(f"  ... {len(non_protected) - 10} more (${remaining:,.2f} total)")
    print()


# ── Section 7: Planned Expenses ──


def _section_planned(conn: sqlite3.Connection, eom: str) -> None:
    from .planned import get_upcoming_plans

    # Show plans due within the target month (use end-of-month as reference
    # with enough lookahead days so the whole month is covered)
    month_start = eom[:7] + "-01"
    plans = get_upcoming_plans(conn, days=31, reference_date=month_start)

    # Filter to only plans due within the target month
    plans = [p for p in plans if p["due_date"][:7] == eom[:7]]

    print("PLANNED EXPENSES")
    print("-" * 55)

    if not plans:
        print("  No planned expenses due this month.")
        print()
        return

    for p in plans:
        gap_str = f"${p['gap']:>9,.2f} gap" if p["gap"] > 0 else "Funded"
        overdue_str = " (OVERDUE)" if p["overdue"] else ""
        memo_str = f"  -- {p['memo']}" if p.get("memo") else ""
        print(f"  {p['category_name']:<30} ${p['amount']:>9,.2f}  {gap_str}{overdue_str}{memo_str}")
    total_gap = sum(p["gap"] for p in plans)
    if total_gap > 0:
        print(f"  {'Total gap:':<30} ${total_gap:>9,.2f}")
    print()
