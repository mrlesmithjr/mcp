"""Budget check: RTA, category status, overspent/near-limit, buffer tracking."""

import logging
import os
import sqlite3
from datetime import datetime

from ..config import load_env
from ..db import current_month, get_connection, init_db
from ..stats import anomaly_label, category_zscore_by_name, parse_category_input, should_skip_group, strip_emoji_prefix

logger = logging.getLogger(__name__)


def _month_offset(month_str: str, offset: int) -> str:
    """Return YYYY-MM-01 string offset by N months (negative = past)."""
    dt = datetime.strptime(month_str[:7], "%Y-%m")
    year = dt.year
    month = dt.month + offset
    while month < 1:
        month += 12
        year -= 1
    while month > 12:
        month -= 12
        year += 1
    return f"{year:04d}-{month:02d}-01"


def _get_rta(conn: sqlite3.Connection, months: int = 3, ending_month: str | None = None) -> list[dict]:
    """Get Ready to Assign (to_be_budgeted) for recent months.

    Args:
        months: Number of months to return.
        ending_month: If provided (YYYY-MM-01), return months ending at this month
                      instead of the most recent available.
    """
    if ending_month:
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT month, to_be_budgeted AS rta, income, budgeted, activity
                FROM budget_months
                WHERE month <= ?
                ORDER BY month DESC
                LIMIT ?
            """,
                (ending_month, months),
            ).fetchall()
        ]
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT month, to_be_budgeted AS rta, income, budgeted, activity
            FROM budget_months
            ORDER BY month DESC
            LIMIT ?
        """,
            (months,),
        ).fetchall()
    ]


def _get_categories(conn: sqlite3.Connection, month: str) -> list[dict]:
    """Get all active budget categories for a month."""
    # hidden intentionally omitted: INSERT OR REPLACE propagates current hidden flag to historical rows
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT name, category_group_name, budgeted, activity, balance,
                   goal_type, goal_target, goal_under_funded
            FROM budget_categories
            WHERE budget_month = ?
              AND deleted = 0
            ORDER BY category_group_name, name
        """,
            (month,),
        ).fetchall()
    ]


def run_budget_check(month: str | None = None) -> None:
    """Show budget status: RTA, overspent, near-limit, underfunded categories.

    Args:
        month: Optional month in YYYY-MM format. Defaults to current month.
    """
    conn = get_connection()
    try:
        init_db(conn)
        if month:
            month = month[:7] + "-01"  # Normalize YYYY-MM to YYYY-MM-01
        else:
            month = current_month()

        # Ready to Assign
        rta_rows = _get_rta(conn, 3, ending_month=month)
        if not rta_rows:
            print("No budget data found. Run 'ynab sync' first.")
            return

        print("Budget Check")
        print("=" * 60)
        print(f"Month: {month[:7]}\n")

        print("Ready to Assign (last 3 months):")
        for r in rta_rows:
            marker = " <<<" if r["rta"] and r["rta"] < 0 else ""
            rta_val = r["rta"] or 0
            print(f"  {r['month'][:7]}:  ${rta_val:>10,.2f}{marker}")
        print()

        # Category analysis
        categories = _get_categories(conn, month)
        if not categories:
            print(f"No categories found for {month[:7]}.")
            return

        overspent = []
        near_limit = []
        underfunded = []

        for c in categories:
            if should_skip_group(c.get("category_group_name") or ""):
                continue
            balance = c["balance"] or 0
            budgeted = c["budgeted"] or 0
            activity = c["activity"] or 0
            underfund = c["goal_under_funded"] or 0

            if balance < 0:
                overspent.append(c)
            elif budgeted > 0 and activity != 0 and abs(activity) / budgeted > 0.8 and balance > 0:
                near_limit.append(c)

            if underfund > 0:
                underfunded.append(c)

        # Overspent
        if overspent:
            total_over = sum(c["balance"] for c in overspent)
            print(f"OVERSPENT ({len(overspent)} categories, ${total_over:,.2f} total):")
            for c in sorted(overspent, key=lambda x: x["balance"]):
                z = category_zscore_by_name(conn, c["name"], month)
                label = anomaly_label(z)
                label_str = f"  {label}" if label else ""
                print(f"  {c['name']:<40} ${c['balance']:>10,.2f}{label_str}")
            print()

        # Near limit (>80% spent)
        if near_limit:
            print(f"NEAR LIMIT (>80% spent, {len(near_limit)} categories):")
            for c in sorted(
                near_limit, key=lambda x: abs(x["activity"] or 0) / max(x["budgeted"] or 1, 0.01), reverse=True
            ):
                budgeted = c["budgeted"] or 0
                activity = c["activity"] or 0
                pct = abs(activity) / budgeted * 100 if budgeted else 0
                print(f"  {c['name']:<40} ${c['balance']:>10,.2f} remaining  ({pct:.0f}% used)")
            print()

        # Underfunded goals
        if underfunded:
            total_underfund = sum(c["goal_under_funded"] for c in underfunded)
            print(f"UNDERFUNDED GOALS ({len(underfunded)} categories, ${total_underfund:,.2f} needed):")
            for c in sorted(underfunded, key=lambda x: -(x["goal_under_funded"] or 0))[:15]:
                print(f"  {c['name']:<40} ${c['goal_under_funded']:>10,.2f} needed")
            if len(underfunded) > 15:
                print(f"  ... and {len(underfunded) - 15} more")
            print()

        # Underfunding breakdown - separate overspending (urgent) from goal gaps (deferrable)
        # Also integrates current-month planned expense gaps as "must cover"
        from .planned import get_upcoming_plans

        upcoming = get_upcoming_plans(conn, days=30, reference_date=month)

        if overspent or underfunded or upcoming:
            # Categories that are overspent (negative balance) - must cover now or it eats next month's RTA
            underfunded_names = {c["name"] for c in underfunded}

            # Overspent with goal: appears in both lists
            overspend_with_goal = [c for c in underfunded if (c["balance"] or 0) < 0]
            # Overspent without goal: in overspent but not underfunded
            overspend_no_goal = [c for c in overspent if c["name"] not in underfunded_names]
            # Goal gaps only: underfunded but balance >= 0
            goal_gap_only = [c for c in underfunded if (c["balance"] or 0) >= 0]

            all_must_cover = overspend_with_goal + overspend_no_goal
            total_overspending = sum(abs(c["balance"] or 0) for c in all_must_cover)
            total_can_defer = sum(c["goal_under_funded"] or 0 for c in goal_gap_only)

            # Split planned expenses: current month = must cover, future months = can defer
            current_month_prefix = month[:7]  # e.g. "2026-03"
            current_month_plans = [p for p in upcoming if p["due_date"][:7] <= current_month_prefix and p["gap"] > 0]
            future_month_plans = [p for p in upcoming if p["due_date"][:7] > current_month_prefix and p["gap"] > 0]
            total_plan_must_cover = sum(p["gap"] for p in current_month_plans)
            total_plan_can_defer = sum(p["gap"] for p in future_month_plans)

            total_must_cover = total_overspending + total_plan_must_cover
            total_all_defer = total_can_defer + total_plan_can_defer

            print("UNDERFUNDING BREAKDOWN:")
            if all_must_cover:
                print(f"  MUST COVER - Overspending (${total_overspending:,.2f}):")
                for c in sorted(all_must_cover, key=lambda x: x["balance"] or 0):
                    bal = c["balance"] or 0
                    bud = c["budgeted"] or 0
                    act = c["activity"] or 0
                    goal_note = "" if c["name"] in underfunded_names else "  (no goal)"
                    print(
                        f"    {c['name']:<36} ${bal:>10,.2f}  (budget ${bud:,.0f}, spent ${abs(act):,.0f}){goal_note}"
                    )

            if current_month_plans:
                print(f"  MUST COVER - This Month's Planned Expenses (${total_plan_must_cover:,.2f}):")
                for p in current_month_plans:
                    due = "OVERDUE" if p["overdue"] else p["due_date"]
                    memo = f"  - {p['memo']}" if p.get("memo") else ""
                    print(f"    {p['category_name']:<36} ${p['gap']:>10,.2f} gap  ({due}){memo}")

            if goal_gap_only:
                print(f"  CAN WAIT - Goal Gaps (${total_can_defer:,.2f}):")
                for c in sorted(goal_gap_only, key=lambda x: -(x["goal_under_funded"] or 0))[:10]:
                    print(f"    {c['name']:<36} ${c['goal_under_funded']:>10,.2f} needed")
                if len(goal_gap_only) > 10:
                    print(f"    ... and {len(goal_gap_only) - 10} more")

            if future_month_plans:
                print(f"  CAN WAIT - Future Planned Expenses (${total_plan_can_defer:,.2f}):")
                for p in future_month_plans:
                    memo = f"  - {p['memo']}" if p.get("memo") else ""
                    print(f"    {p['category_name']:<36} ${p['gap']:>10,.2f} gap  (due {p['due_date']}){memo}")

            print()
            print(f"  Must fix now:  ${total_must_cover:>12,.2f}")
            print(f"  Can defer:     ${total_all_defer:>12,.2f}")
            print()

        # Upcoming planned expenses (already fetched above for breakdown)
        if upcoming:
            total_plan_gap = sum(p["gap"] for p in upcoming)
            print(f"UPCOMING PLANNED EXPENSES ({len(upcoming)} in next 30 days):")
            for p in upcoming:
                days_str = "OVERDUE" if p["overdue"] else f"in {p['days_until']}d"
                gap_str = f"${p['gap']:>9,.2f} gap" if p["gap"] > 0 else "Funded"
                memo_str = f"  - {p['memo']}" if p["memo"] else ""
                print(f"  {p['category_name']:<30} ${p['amount']:>9,.2f}  {days_str:<10} {gap_str}{memo_str}")
            if total_plan_gap > 0:
                print(f"  {'Total gap:':<30} ${total_plan_gap:>9,.2f}")
            print()

        # Summary
        total_budgeted = sum(
            c["budgeted"] or 0 for c in categories if not should_skip_group(c.get("category_group_name") or "")
        )
        total_activity = sum(
            c["activity"] or 0 for c in categories if not should_skip_group(c.get("category_group_name") or "")
        )
        total_balance = sum(c["balance"] or 0 for c in categories if (c["balance"] or 0) > 0)
        print("Summary:")
        print(f"  Total budgeted:   ${total_budgeted:>12,.2f}")
        print(f"  Total activity:   ${total_activity:>12,.2f}")
        print(f"  Available balance: ${total_balance:>11,.2f}")
        if not overspent:
            print("\n  No overspent categories!")

        _print_health_ratios(categories)
    finally:
        conn.close()


def _sum_budgeted(categories: list[dict], names: list[str]) -> float:
    """Sum budgeted amounts for categories matching any of the given name fragments."""
    total = 0.0
    for c in categories:
        cat_name = (c["name"] or "").lower()
        for name in names:
            if name.lower() in cat_name:
                total += c["budgeted"] or 0
                break
    return total


def _print_health_ratios(categories: list[dict]) -> None:
    """Print budget health ratios against gross income (Money Guy guidelines)."""
    load_env()
    gross_salary = os.environ.get("YNAB_GROSS_SALARY")
    if not gross_salary:
        return

    try:
        annual_base = float(gross_salary)
    except ValueError:
        return
    monthly_base = annual_base / 12

    gross_ote = os.environ.get("YNAB_GROSS_OTE")
    monthly_ote = None
    if gross_ote:
        try:
            monthly_ote = float(gross_ote) / 12
        except ValueError:
            pass

    retirement_annual = os.environ.get("YNAB_RETIREMENT_ANNUAL")
    monthly_retirement = None
    if retirement_annual:
        try:
            monthly_retirement = float(retirement_annual) / 12
        except ValueError:
            pass

    # Category mappings (configurable via env vars, comma-separated)
    housing_cats = os.environ.get("YNAB_RATIO_HOUSING", "Mortgage & Rent").split(",")
    auto_cats = os.environ.get("YNAB_RATIO_AUTO", "Auto Loan").split(",")
    debt_cats = os.environ.get(
        "YNAB_RATIO_DEBT",
        "Mortgage & Rent,Auto Loan,Student Loan",
    ).split(",")

    housing = _sum_budgeted(categories, [c.strip() for c in housing_cats])
    auto_loans = _sum_budgeted(categories, [c.strip() for c in auto_cats])
    debt_service = _sum_budgeted(categories, [c.strip() for c in debt_cats])

    # Build ratio rows: (label, monthly_amount, guideline_pct, direction)
    # direction: "below" means good if under, "above" means good if over
    ratios = [
        ("Housing", housing, 25.0, "below"),
        ("Auto (Loans)", auto_loans, 8.0, "below"),
        ("Debt Service", debt_service, 36.0, "below"),
    ]
    if monthly_retirement is not None:
        ratios.append(("Retirement", monthly_retirement, 25.0, "above"))

    print()
    print("BUDGET HEALTH RATIOS (vs Money Guy guidelines):")
    if monthly_ote:
        print(f"  {'Ratio':<20} {'Monthly':>10} {'Base%':>8} {'OTE%':>8}  {'Guideline':>10}")
        print("  " + "-" * 62)
    else:
        print(f"  {'Ratio':<20} {'Monthly':>10} {'Base%':>8}  {'Guideline':>10}")
        print("  " + "-" * 54)

    for label, amount, guideline, direction in ratios:
        base_pct = (amount / monthly_base) * 100 if monthly_base else 0

        if direction == "below":
            guideline_str = f"<{guideline:.0f}%"
            status = "OK" if base_pct < guideline else "HIGH"
        else:
            guideline_str = f"≥{guideline:.0f}%"
            status = "OK" if base_pct >= guideline else "LOW"

        if monthly_ote:
            ote_pct = (amount / monthly_ote) * 100 if monthly_ote else 0
            print(f"  {label:<20} ${amount:>9,.0f} {base_pct:>7.1f}% {ote_pct:>7.1f}%  {guideline_str:>8}  {status}")
        else:
            print(f"  {label:<20} ${amount:>9,.0f} {base_pct:>7.1f}%  {guideline_str:>8}  {status}")

    print()


def run_cc_audit() -> None:
    """Audit credit card payment categories vs actual account balances."""
    conn = get_connection()
    try:
        init_db(conn)
        month = current_month()

        rows = conn.execute(
            """
            SELECT a.name, a.balance AS owed,
                   bc.balance AS payment_available,
                   bc.goal_type
            FROM accounts a
            LEFT JOIN budget_categories bc
              ON bc.name = a.name AND bc.budget_month = ?
            WHERE a.deleted = 0 AND a.closed = 0
              AND a.type = 'creditCard'
            ORDER BY a.name
        """,
            (month,),
        ).fetchall()

        if not rows:
            print("No credit card accounts found.")
            return

        print("Credit Card Payment Audit")
        print("=" * 85)
        print(f"{'Card':<42} {'Owed':>10} {'Available':>10} {'Gap':>10}  Status")
        print("-" * 85)

        issues = 0
        for row in rows:
            owed = abs(row["owed"] or 0)
            available = row["payment_available"] or 0
            goal_type = row["goal_type"] or ""
            gap = available - owed

            # Cards with debt payoff goals are expected to not match
            is_payoff = goal_type == "TBD"

            if owed == 0 and available == 0:
                status = "OK"
            elif is_payoff:
                status = "Payoff plan"
            elif abs(gap) < 0.01:
                status = "OK"
            elif gap < 0:
                status = f"Short ${abs(gap):,.2f}"
                issues += 1
            else:
                status = f"Over ${gap:,.2f}"

            name = row["name"][:40]
            print(f"  {name:<40} ${owed:>9,.2f} ${available:>9,.2f} ${gap:>9,.2f}  {status}")

        print()
        if issues:
            print(f"  {issues} card(s) need attention.")
        else:
            print("  All credit cards look good.")
    finally:
        conn.close()


def run_balance(category: str) -> None:
    """Look up current balance, budgeted, and activity for a category."""
    conn = get_connection()
    try:
        init_db(conn)
        month = current_month()

        cat_name, group_filter = parse_category_input(category)

        def _apply_group_filter(rows, group):
            if group is None:
                return rows
            group_lower = strip_emoji_prefix(group).lower()
            return [
                r
                for r in rows
                if strip_emoji_prefix(r["category_group_name"]).lower() == group_lower
                or r["category_group_name"].lower() == group_lower
            ]

        # Try exact match first (case-insensitive).
        # hidden intentionally omitted: INSERT OR REPLACE propagates current hidden flag to historical rows
        rows = conn.execute(
            """
            SELECT name, category_group_name, balance, budgeted, activity,
                   goal_type, goal_target, goal_target_month, goal_under_funded, goal_overall_left
            FROM budget_categories
            WHERE budget_month = ?
              AND deleted = 0
              AND LOWER(name) = LOWER(?)
            ORDER BY name
        """,
            (month, cat_name),
        ).fetchall()

        if rows and group_filter:
            filtered = _apply_group_filter(rows, group_filter)
            if filtered:
                rows = filtered

        # Fall back to substring match
        if not rows:
            rows = conn.execute(
                """
                SELECT name, category_group_name, balance, budgeted, activity,
                       goal_type, goal_target, goal_target_month, goal_under_funded, goal_overall_left
                FROM budget_categories
                WHERE budget_month = ?
                  AND deleted = 0
                  AND LOWER(name) LIKE ?
                ORDER BY name
            """,
                (month, f"%{cat_name.lower()}%"),
            ).fetchall()

            if rows and group_filter:
                filtered = _apply_group_filter(rows, group_filter)
                if filtered:
                    rows = filtered

        if not rows:
            print(f"No category matching '{category}' for {month[:7]}.")
            return

        print(f"Category Balance ({month[:7]})")
        print("=" * 60)

        for row in rows:
            balance = row["balance"] or 0
            budgeted = row["budgeted"] or 0
            activity = row["activity"] or 0
            underfund = row["goal_under_funded"] or 0

            print(f"\n  {row['name']}  ({row['category_group_name']})")
            print(f"    Balance:    ${balance:>12,.2f}")
            print(f"    Budgeted:   ${budgeted:>12,.2f}")
            print(f"    Activity:   ${activity:>12,.2f}")

            if row["goal_type"]:
                goal_target = row["goal_target"] or 0
                overall_left = row["goal_overall_left"] or 0
                goal_target_month = row["goal_target_month"]
                # CC payoff goals (TBD on payment categories) store 0 in goal_target;
                # the remaining balance to pay off is in goal_overall_left. refs #208
                #
                # Fully-settled case: goal_target == 0 AND overall_left == 0.
                # Both being zero means the payoff goal is met; display a clear label
                # rather than a confusing "$0.00 (TBD)". refs #216
                is_fully_settled = row["goal_type"] == "TBD" and goal_target == 0 and overall_left == 0
                is_payoff = goal_target == 0 and overall_left > 0
                if is_fully_settled:
                    date_suffix = f", due {goal_target_month[:7]}" if goal_target_month else ""
                    print(f"    Goal:       paid off  ({row['goal_type']}{date_suffix})")
                else:
                    display_amount = overall_left if is_payoff else goal_target
                    date_suffix = f", due {goal_target_month[:7]}" if goal_target_month else ""
                    print(f"    Goal:       ${display_amount:>12,.2f}  ({row['goal_type']}{date_suffix})")
                if underfund > 0:
                    print(f"    Underfunded: ${underfund:>11,.2f}")

        if len(rows) > 1:
            total_balance = sum(r["balance"] or 0 for r in rows)
            print(f"\n  Total balance ({len(rows)} categories): ${total_balance:>,.2f}")
    finally:
        conn.close()


def classify_overspends(conn: sqlite3.Connection, month: str) -> list[dict]:
    """Classify overspent categories to help determine coverage sources.

    For each category with balance < 0 in the given month, classify it as:
    - STRUCTURAL: overspent in 3+ of the last 6 months
    - SEASONAL: overspent in the same calendar month last year
    - ONE-OFF: a single transaction accounts for >60% of total activity
    - TIMING FLOAT: category contains "Reimbursable" or has inflows next month
    - ONE-OFF (default): if none of the above match

    Returns list of dicts with: name, balance, budgeted, activity, classification, detail
    """
    # Get overspent categories for the given month.
    # hidden intentionally omitted: INSERT OR REPLACE propagates current hidden flag to historical rows
    overspent = conn.execute(
        """
        SELECT name, category_group_name, balance, budgeted, activity
        FROM budget_categories
        WHERE budget_month = ?
          AND deleted = 0
          AND balance < 0
        ORDER BY balance ASC
    """,
        (month,),
    ).fetchall()

    if not overspent:
        return []

    results = []
    for cat in overspent:
        if should_skip_group(cat["category_group_name"] or ""):
            continue
        name = cat["name"]
        balance = cat["balance"]
        budgeted = cat["budgeted"] or 0
        activity = cat["activity"] or 0
        classification = None
        detail = ""

        # --- Check TIMING FLOAT first (name-based is cheapest) ---
        if "reimbursable" in name.lower():
            # Check for inflows in the next month
            next_month = _month_offset(month, 1)
            next_month_end = _month_offset(month, 2)
            inflows = conn.execute(
                """
                SELECT SUM(amount) AS total_inflow
                FROM transactions
                WHERE category_name = ? AND deleted = 0
                  AND amount > 0
                  AND date >= ? AND date < ?
            """,
                (name, next_month[:10], next_month_end[:10]),
            ).fetchone()
            inflow_amt = (inflows["total_inflow"] or 0) if inflows else 0
            if inflow_amt > 0:
                detail = f"${inflow_amt:,.0f} reimbursement in {next_month[:7].split('-')[1]}"
            else:
                detail = "reimbursable category"
            classification = "TIMING FLOAT"

        # --- Check STRUCTURAL: overspent 3+ of last 6 months ---
        if classification is None:
            hist_months = []
            for i in range(1, 7):
                hist_months.append(_month_offset(month, -i))

            if hist_months:
                overspent_count_row = conn.execute(
                    f"""
                    SELECT COUNT(*) AS cnt
                    FROM budget_categories
                    WHERE name = ? AND deleted = 0
                      AND balance < 0
                      AND budget_month IN ({",".join("?" for _ in hist_months)})
                """,
                    [name, *hist_months],
                ).fetchone()
                overspent_count = overspent_count_row["cnt"] if overspent_count_row else 0

                if overspent_count >= 3:
                    classification = "STRUCTURAL"
                    detail = f"overspent {overspent_count}/6 months"

        # --- Check SEASONAL: same calendar month last year ---
        if classification is None:
            same_month_last_year = _month_offset(month, -12)
            last_year_row = conn.execute(
                """
                SELECT balance
                FROM budget_categories
                WHERE name = ? AND budget_month = ?
                  AND deleted = 0
            """,
                (name, same_month_last_year),
            ).fetchone()

            if last_year_row and (last_year_row["balance"] or 0) < 0:
                # Try to identify the spike transaction
                month_end = _month_offset(month, 1)
                top_txn = conn.execute(
                    """
                    SELECT payee_name, ABS(amount) AS abs_amt
                    FROM transactions
                    WHERE category_name = ? AND deleted = 0
                      AND amount < 0
                      AND date >= ? AND date < ?
                    ORDER BY amount ASC
                    LIMIT 1
                """,
                    (name, month[:10], month_end[:10]),
                ).fetchone()

                if top_txn:
                    detail = f"also overspent {same_month_last_year[:7]}; top txn {top_txn['payee_name']}"
                else:
                    detail = f"also overspent {same_month_last_year[:7]}"
                classification = "SEASONAL"

        # --- Check ONE-OFF: single txn > 60% of activity ---
        if classification is None:
            month_end = _month_offset(month, 1)
            txns = conn.execute(
                """
                SELECT payee_name, ABS(amount) AS abs_amt
                FROM transactions
                WHERE category_name = ? AND deleted = 0
                  AND amount < 0
                  AND date >= ? AND date < ?
                ORDER BY amount ASC
                LIMIT 10
            """,
                (name, month[:10], month_end[:10]),
            ).fetchall()

            total_activity = abs(activity) if activity else 0
            if txns and total_activity > 0:
                max_txn = txns[0]
                ratio = max_txn["abs_amt"] / total_activity
                if ratio > 0.6:
                    classification = "ONE-OFF"
                    detail = f"single txn ${max_txn['abs_amt']:,.0f} = {ratio:.0%} of activity"

        # --- Also check TIMING FLOAT for non-reimbursable categories with next-month inflows ---
        if classification is None:
            next_month = _month_offset(month, 1)
            next_month_end = _month_offset(month, 2)
            inflows = conn.execute(
                """
                SELECT SUM(amount) AS total_inflow
                FROM transactions
                WHERE category_name = ? AND deleted = 0
                  AND amount > 0
                  AND date >= ? AND date < ?
            """,
                (name, next_month[:10], next_month_end[:10]),
            ).fetchone()
            inflow_amt = (inflows["total_inflow"] or 0) if inflows else 0
            if inflow_amt > 0:
                # Determine month abbreviation for display
                month_names = [
                    "Jan",
                    "Feb",
                    "Mar",
                    "Apr",
                    "May",
                    "Jun",
                    "Jul",
                    "Aug",
                    "Sep",
                    "Oct",
                    "Nov",
                    "Dec",
                ]
                next_month_num = int(next_month[5:7])
                month_abbr = month_names[next_month_num - 1]
                classification = "TIMING FLOAT"
                detail = f"${inflow_amt:,.0f} inflow in {month_abbr}"

        # --- Default: ONE-OFF ---
        if classification is None:
            classification = "ONE-OFF"
            detail = "no recurring pattern detected"

        results.append(
            {
                "name": name,
                "balance": balance,
                "budgeted": budgeted,
                "activity": activity,
                "classification": classification,
                "detail": detail,
            }
        )

    return results


def run_overspend_plan(month: str | None = None) -> None:
    """Display overspend coverage plan with classifications.

    Args:
        month: Optional month in YYYY-MM format. Defaults to current month.
    """
    conn = get_connection()
    try:
        init_db(conn)
        if month:
            month = month[:7] + "-01"
        else:
            month = current_month()

        results = classify_overspends(conn, month)

        if not results:
            month_label = datetime.strptime(month[:7], "%Y-%m").strftime("%B %Y")
            print(f"No overspent categories for {month_label}.")
            return

        month_label = datetime.strptime(month[:7], "%Y-%m").strftime("%B %Y")
        print(f"OVERSPEND COVERAGE PLAN ({month_label})")
        print("=" * 90)
        print(f"{'Category':<28} {'Overspent':>10}  {'Classification':<15} Detail")
        print("-" * 90)

        for r in results:
            print(f"{r['name']:<28} ${r['balance']:>9,.2f}  {r['classification']:<15} {r['detail']}")

        total = sum(r["balance"] for r in results)
        print("-" * 90)
        print(f"{'Total':<28} ${total:>9,.2f}")

        # Summary by classification
        classifications = {}
        for r in results:
            cls = r["classification"]
            classifications.setdefault(cls, 0.0)
            classifications[cls] += r["balance"]

        print()
        print("By classification:")
        for cls in ["STRUCTURAL", "SEASONAL", "ONE-OFF", "TIMING FLOAT"]:
            if cls in classifications:
                count = sum(1 for r in results if r["classification"] == cls)
                print(f"  {cls:<15} ${classifications[cls]:>10,.2f}  ({count} categories)")
    finally:
        conn.close()
