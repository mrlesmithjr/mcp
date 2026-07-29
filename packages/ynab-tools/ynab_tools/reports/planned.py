"""Planned expenses: track upcoming large expenses with funding gaps."""

import logging
import sqlite3
from datetime import datetime, timedelta

from ..db import current_month, get_connection, init_db
from ..stats import parse_category_input, strip_emoji_prefix

logger = logging.getLogger(__name__)


def _find_category(conn: sqlite3.Connection, name: str) -> list[dict]:
    """Find categories matching a name. Tries exact match first, then substring.

    Accepts "Group: Category" or "Category (Group)" to filter by group when multiple
    categories share the same name.
    """
    month = current_month()
    cat_name, group_filter = parse_category_input(name)

    def _apply_group_filter(rows: list[dict], group: str | None) -> list[dict]:
        if group is None:
            return rows
        group_lower = strip_emoji_prefix(group).lower()
        return [
            r
            for r in rows
            if strip_emoji_prefix(r["category_group_name"]).lower() == group_lower
            or r["category_group_name"].lower() == group_lower
        ]

    # Try exact match first (case-insensitive)
    exact = [
        dict(row)
        for row in conn.execute(
            """
            SELECT id, name, category_group_name, balance
            FROM budget_categories
            WHERE budget_month = ?
              AND deleted = 0 AND hidden = 0
              AND LOWER(name) = LOWER(?)
            ORDER BY name
        """,
            (month, cat_name),
        ).fetchall()
    ]
    if exact:
        filtered = _apply_group_filter(exact, group_filter)
        return filtered if filtered else exact

    # Fall back to substring match
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT id, name, category_group_name, balance
            FROM budget_categories
            WHERE budget_month = ?
              AND deleted = 0 AND hidden = 0
              AND LOWER(name) LIKE ?
            ORDER BY name
        """,
            (month, f"%{cat_name.lower()}%"),
        ).fetchall()
    ]
    if rows and group_filter:
        filtered = _apply_group_filter(rows, group_filter)
        return filtered if filtered else rows
    return rows


def _get_category_balance(conn: sqlite3.Connection, category_name: str) -> float | None:
    """Get the current balance for a category by exact name."""
    month = current_month()
    row = conn.execute(
        """
        SELECT balance FROM budget_categories
        WHERE budget_month = ? AND name = ? AND deleted = 0 AND hidden = 0
    """,
        (month, category_name),
    ).fetchone()
    return row["balance"] if row else None


def get_upcoming_plans(conn: sqlite3.Connection, days: int = 30, reference_date: str | None = None) -> list[dict]:
    """Get active planned expenses due within N days, with funding gap info.

    This is the integration function used by budget.py.

    Args:
        days: Number of days from reference_date to look ahead.
        reference_date: If provided (YYYY-MM-01 or YYYY-MM-DD), use as the
                        reference point instead of today. For past months, shows
                        plans that were due around that time.
    """
    if reference_date:
        ref = datetime.strptime(reference_date[:10], "%Y-%m-%d")
    else:
        ref = datetime.now()
    cutoff = (ref + timedelta(days=days)).strftime("%Y-%m-%d")
    today = ref.strftime("%Y-%m-%d")

    rows = conn.execute(
        """
        SELECT id, category_name, category_id, amount, due_date, memo
        FROM planned_expenses
        WHERE status = 'active' AND due_date <= ?
        ORDER BY due_date
    """,
        (cutoff,),
    ).fetchall()

    results = []
    for row in rows:
        r = dict(row)
        balance = _get_category_balance(conn, r["category_name"])
        r["category_balance"] = balance if balance is not None else 0
        r["gap"] = max(0, r["amount"] - r["category_balance"])
        r["days_until"] = (datetime.strptime(r["due_date"], "%Y-%m-%d") - ref).days
        r["overdue"] = r["due_date"] < today
        results.append(r)

    return results


def run_plan_add(category: str, amount: float, by_date: str, memo: str | None = None) -> None:
    """Add a planned expense."""
    conn = get_connection()
    try:
        init_db(conn)

        # Resolve category
        matches = _find_category(conn, category)
        if not matches:
            print(f"No category matching '{category}'. Run 'ynab sync' if needed.")
            return
        if len(matches) > 1:
            print(f"Multiple categories match '{category}':")
            for m in matches:
                print(f"  {m['name']}  (group: {m['category_group_name']})")
            print("\nDisambiguate with 'Group: Category' or 'Category (Group)' format.")
            print("Example: 'Business: Licenses & Fees' or 'Licenses & Fees (Business)'")
            return

        cat = matches[0]
        balance = cat["balance"] or 0
        gap = max(0, amount - balance)

        conn.execute(
            """
            INSERT INTO planned_expenses (category_name, category_id, amount, due_date, memo)
            VALUES (?, ?, ?, ?, ?)
        """,
            (cat["name"], cat["id"], amount, by_date, memo),
        )
        conn.commit()

        print("Planned expense added:")
        print(f"  Category:  {cat['name']}")
        print(f"  Amount:    ${amount:,.2f}")
        print(f"  Due:       {by_date}")
        if memo:
            print(f"  Memo:      {memo}")
        print(f"  Balance:   ${balance:,.2f}")
        if gap > 0:
            print(f"  Gap:       ${gap:,.2f} still needed")
        else:
            print("  Funded!")
    finally:
        conn.close()


def run_plan_list() -> None:
    """List active planned expenses."""
    conn = get_connection()
    try:
        init_db(conn)

        rows = conn.execute("""
            SELECT id, category_name, amount, due_date, memo, status, created_at
            FROM planned_expenses
            WHERE status = 'active'
            ORDER BY due_date
        """).fetchall()

        if not rows:
            print("No active planned expenses.")
            return

        today = datetime.now().strftime("%Y-%m-%d")

        print("Planned Expenses")
        print("=" * 80)
        print(f"  {'#':<4} {'Category':<30} {'Amount':>10} {'Due':>12} {'Balance':>10} {'Gap':>10}")
        print("-" * 80)

        total_gap = 0
        for row in rows:
            r = dict(row)
            balance = _get_category_balance(conn, r["category_name"])
            bal = balance if balance is not None else 0
            gap = max(0, r["amount"] - bal)
            total_gap += gap

            overdue = "!" if r["due_date"] < today else " "
            days = (datetime.strptime(r["due_date"], "%Y-%m-%d") - datetime.now()).days

            due_str = f"{r['due_date']} ({days}d)"
            if r["due_date"] < today:
                due_str = f"{r['due_date']} (OVERDUE)"

            print(
                f"{overdue} {r['id']:<4} {r['category_name']:<30} ${r['amount']:>9,.2f} {due_str:>16} ${bal:>9,.2f} ",
                end="",
            )
            if gap > 0:
                print(f"${gap:>9,.2f}")
            else:
                print(f"{'Funded':>10}")

            if r["memo"]:
                print(f"       {r['memo']}")

        if total_gap > 0:
            print(f"\n  Total funding gap: ${total_gap:,.2f}")
        else:
            print("\n  All planned expenses fully funded!")
    finally:
        conn.close()


def run_plan_done(plan_id: int) -> None:
    """Mark a planned expense as completed."""
    conn = get_connection()
    try:
        init_db(conn)

        row = conn.execute(
            "SELECT id, category_name, amount, status FROM planned_expenses WHERE id = ?",
            (plan_id,),
        ).fetchone()

        if not row:
            print(f"No planned expense with ID {plan_id}.")
            return

        if row["status"] != "active":
            print(f"Planned expense #{plan_id} is already {row['status']}.")
            return

        now = datetime.now().isoformat()
        conn.execute(
            """
            UPDATE planned_expenses SET status = 'completed', completed_at = ?
            WHERE id = ?
        """,
            (now, plan_id),
        )
        conn.commit()

        print(f"Marked #{plan_id} as completed: {row['category_name']} ${row['amount']:,.2f}")
    finally:
        conn.close()


def run_plan_edit(
    plan_id: int,
    amount: float | None = None,
    by_date: str | None = None,
    memo: str | None = None,
    category: str | None = None,
) -> None:
    """Edit an existing planned expense. Only provided fields are updated."""
    conn = get_connection()
    try:
        init_db(conn)

        row = conn.execute(
            "SELECT id, category_name, category_id, amount, due_date, memo, status FROM planned_expenses WHERE id = ?",
            (plan_id,),
        ).fetchone()

        if not row:
            print(f"No planned expense with ID {plan_id}.")
            return

        if row["status"] != "active":
            print(f"Planned expense #{plan_id} is already {row['status']}.")
            return

        updates = {}
        if amount is not None:
            updates["amount"] = amount
        if by_date is not None:
            updates["due_date"] = by_date
        if memo is not None:
            updates["memo"] = memo
        if category is not None:
            matches = _find_category(conn, category)
            if not matches:
                print(f"No category matching '{category}'. Run 'ynab sync' if needed.")
                return
            if len(matches) > 1:
                print(f"Multiple categories match '{category}':")
                for m in matches:
                    print(f"  {m['name']}  (group: {m['category_group_name']})")
                print("\nDisambiguate with 'Group: Category' or 'Category (Group)' format.")
                print("Example: 'Business: Licenses & Fees' or 'Licenses & Fees (Business)'")
                return
            updates["category_name"] = matches[0]["name"]
            updates["category_id"] = matches[0]["id"]

        if not updates:
            print("Nothing to update. Provide --amount, --by, --memo, or --category.")
            return

        _allowed = {"amount", "due_date", "memo", "category_name", "category_id"}
        if not set(updates).issubset(_allowed):
            raise ValueError(f"Unexpected update fields: {set(updates) - _allowed}")
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [plan_id]
        conn.execute(f"UPDATE planned_expenses SET {set_clause} WHERE id = ?", values)
        conn.commit()

        # Show updated record
        updated = conn.execute(
            "SELECT category_name, amount, due_date, memo FROM planned_expenses WHERE id = ?",
            (plan_id,),
        ).fetchone()

        balance = _get_category_balance(conn, updated["category_name"])
        bal = balance if balance is not None else 0
        gap = max(0, updated["amount"] - bal)

        print(f"Updated planned expense #{plan_id}:")
        print(f"  Category:  {updated['category_name']}")
        print(f"  Amount:    ${updated['amount']:,.2f}")
        print(f"  Due:       {updated['due_date']}")
        if updated["memo"]:
            print(f"  Memo:      {updated['memo']}")
        print(f"  Balance:   ${bal:,.2f}")
        if gap > 0:
            print(f"  Gap:       ${gap:,.2f} still needed")
        else:
            print("  Funded!")
    finally:
        conn.close()


def run_plan_remove(plan_id: int) -> None:
    """Remove a planned expense (hard delete)."""
    conn = get_connection()
    try:
        init_db(conn)

        row = conn.execute(
            "SELECT id, category_name, amount FROM planned_expenses WHERE id = ?",
            (plan_id,),
        ).fetchone()

        if not row:
            print(f"No planned expense with ID {plan_id}.")
            return

        conn.execute("DELETE FROM planned_expenses WHERE id = ?", (plan_id,))
        conn.commit()

        print(f"Removed #{plan_id}: {row['category_name']} ${row['amount']:,.2f}")
    finally:
        conn.close()
