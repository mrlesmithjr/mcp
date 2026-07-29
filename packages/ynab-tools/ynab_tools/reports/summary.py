"""Monthly summary: income vs spending vs net over time."""

import logging
import sqlite3

from ..db import get_connection, init_db
from ..stats import should_skip_group

logger = logging.getLogger(__name__)


def _get_monthly_summary(conn: sqlite3.Connection, months: int = 6) -> list[dict]:
    """Get monthly income, activity, and RTA from budget_months."""
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT month, income, activity, to_be_budgeted
            FROM budget_months
            ORDER BY month DESC
            LIMIT ?
        """,
            (months,),
        ).fetchall()
    ]


def run_summary(months: int = 6) -> None:
    """Show monthly income vs spending vs net."""
    conn = get_connection()
    try:
        init_db(conn)

        rows = _get_monthly_summary(conn, months)

        if not rows:
            print("No budget data found. Run 'ynab sync' first.")
            return

        # Compute spending from non-infrastructure categories to avoid double-counting CC payments.
        month_list = [row["month"] for row in rows]
        placeholders = ",".join("?" for _ in month_list)
        cat_rows = conn.execute(
            f"""
            SELECT budget_month, category_group_name, activity
            FROM budget_categories
            WHERE budget_month IN ({placeholders})
              AND deleted = 0
              -- hidden intentionally omitted: INSERT OR REPLACE propagates the current
              -- hidden flag to all historical rows, so categories hidden after the fact
              -- (e.g. a paid-off loan) would lose their entire activity history.
            """,
            month_list,
        ).fetchall()
        spend_by_month: dict[str, float] = {}
        for r in cat_rows:
            if should_skip_group(r["category_group_name"] or ""):
                continue
            m = r["budget_month"]
            spend_by_month[m] = spend_by_month.get(m, 0.0) + float(r["activity"] or 0)

        # Display in chronological order
        rows.reverse()

        print(f"Monthly Summary (last {months} months)")
        print("=" * 64)
        print(f"  {'Month':<10} {'Income':>10} {'Spending':>12} {'Net':>12}   Status")
        print("  " + "-" * 58)

        total_income = 0.0
        total_spending = 0.0
        deficit_count = 0

        for row in rows:
            income = row["income"] or 0.0
            spending = abs(spend_by_month.get(row["month"], 0.0))
            net = income - spending
            status = "surplus" if net >= 0 else "DEFICIT"
            if net < 0:
                deficit_count += 1

            total_income += income
            total_spending += spending

            month_label = row["month"][:7]  # YYYY-MM
            net_str = f"+${net:>9,.2f}" if net >= 0 else f"-${abs(net):>9,.2f}"
            print(f"  {month_label:<10} ${income:>9,.2f} ${spending:>11,.2f}   {net_str}   {status}")

        print("  " + "-" * 58)

        # Totals
        total_net = total_income - total_spending
        total_net_str = f"+${total_net:>9,.2f}" if total_net >= 0 else f"-${abs(total_net):>9,.2f}"
        print(f"  {'Total':<10} ${total_income:>9,.2f} ${total_spending:>11,.2f}   {total_net_str}")

        # Averages
        n = len(rows)
        avg_income = total_income / n
        avg_spending = total_spending / n
        avg_net = total_income / n - total_spending / n
        avg_net_str = f"+${avg_net:>9,.2f}" if avg_net >= 0 else f"-${abs(avg_net):>9,.2f}"
        print(f"  {'Average':<10} ${avg_income:>9,.2f} ${avg_spending:>11,.2f}   {avg_net_str}")

        print()
        print(f"  Deficit months: {deficit_count} of {n}")
    finally:
        conn.close()
