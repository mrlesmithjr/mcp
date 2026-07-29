"""Spending analysis: current month breakdown, category trends."""

import calendar
import json
import logging
import sqlite3
from datetime import datetime

from ..config import DATA_DIR
from ..db import get_connection, init_db
from ..stats import anomaly_label, category_zscore_by_name, should_skip_group

logger = logging.getLogger(__name__)


def _current_month_start() -> str:
    return datetime.now().strftime("%Y-%m-01")


def _month_starts(months: int) -> list[str]:
    """Return month-start strings for the last N months (most recent first)."""
    now = datetime.now()
    result = []
    for i in range(months):
        y = now.year
        m = now.month - i
        while m <= 0:
            m += 12
            y -= 1
        result.append(f"{y:04d}-{m:02d}-01")
    return result


def _get_spending_by_category(conn: sqlite3.Connection, date_from: str, date_to: str | None = None) -> list[dict]:
    """Get split-aware spending by category for a date range."""
    where_clause = "AND t.date >= ?"
    params: list = [date_from]
    if date_to:
        where_clause += " AND t.date < ?"
        params.append(date_to)

    return [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT COALESCE(st.category_name, t.category_name) AS category,
                   SUM(COALESCE(st.amount, t.amount)) AS total,
                   COUNT(*) AS txn_count
            FROM transactions t
            LEFT JOIN subtransactions st ON st.transaction_id = t.id AND st.deleted = 0
            WHERE t.deleted = 0
              {where_clause}
              AND COALESCE(st.category_name, t.category_name) NOT IN
                  ('Split', 'Split (Multiple Categories...)', 'Uncategorized', '')
              AND COALESCE(st.category_name, t.category_name) IS NOT NULL
            GROUP BY category
            ORDER BY total ASC
        """,
            params,
        ).fetchall()
    ]


def _get_budget_targets(conn: sqlite3.Connection, month: str) -> dict[str, float]:
    """Get budgeted amounts by category name for a month."""
    # hidden intentionally omitted: INSERT OR REPLACE propagates current hidden flag to historical rows
    rows = conn.execute(
        """
        SELECT name, budgeted FROM budget_categories
        WHERE budget_month = ? AND deleted = 0
    """,
        (month,),
    ).fetchall()
    return {row["name"]: row["budgeted"] or 0 for row in rows}


def _get_budget_month_data(conn: sqlite3.Connection, month: str) -> list[dict]:
    """Get per-category budget data (budgeted, activity, balance) for a month."""
    # hidden intentionally omitted: INSERT OR REPLACE propagates current hidden flag to historical rows
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT name, category_group_name, budgeted, activity, balance
            FROM budget_categories
            WHERE budget_month = ? AND deleted = 0
            ORDER BY name
        """,
            (month,),
        ).fetchall()
    ]


def _print_single_month(
    spending: list[dict],
    targets: dict[str, float],
    month_start: str,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Print the original single-month spending report."""
    print("Monthly Spending Report")
    print("=" * 80)
    print(f"Month: {month_start[:7]}\n")

    income_cats = []
    expense_cats = []
    for s in spending:
        if s["total"] > 0:
            income_cats.append(s)
        else:
            expense_cats.append(s)

    # Income
    if income_cats:
        total_income = sum(s["total"] for s in income_cats)
        print(f"INCOME (${total_income:,.2f}):")
        for s in sorted(income_cats, key=lambda x: -x["total"]):
            print(f"  {s['category']:<40} ${s['total']:>10,.2f}  ({s['txn_count']} txns)")
        print()

    # Expenses
    if expense_cats:
        total_expense = sum(s["total"] for s in expense_cats)
        print(f"{'Category':<40} {'Spent':>10}  {'Budget':>10}  {'Left':>10}  {'%':>5}")
        print("-" * 80)

        for s in expense_cats:
            cat = s["category"]
            spent = abs(s["total"])
            budget = targets.get(cat, 0)
            left = budget - spent if budget else 0
            pct = spent / budget * 100 if budget else 0

            marker = ""
            if budget and spent > budget:
                marker = " ***"
            elif budget and pct > 80:
                marker = " *"

            budget_str = f"${budget:>9,.2f}" if budget else "      -   "
            left_str = f"${left:>9,.2f}" if budget else "      -   "
            pct_str = f"{pct:>4.0f}%" if budget else "    -"

            # Append anomaly label for overspent rows when history is available
            anomaly_str = ""
            if conn is not None and budget and spent > budget:
                z = category_zscore_by_name(conn, cat, month_start)
                label = anomaly_label(z)
                if label:
                    anomaly_str = f"  {label}"

            print(f"  {cat:<38} ${spent:>9,.2f}  {budget_str}  {left_str}  {pct_str}{marker}{anomaly_str}")

        print("-" * 80)
        total_budgeted = sum(targets.get(s["category"], 0) for s in expense_cats)
        print(f"  {'Total':<38} ${abs(total_expense):>9,.2f}  ${total_budgeted:>9,.2f}")
        print()

        # Top 5 spending
        print("Top 5 Spending Categories:")
        for s in expense_cats[:5]:
            print(f"  {s['category']:<40} ${abs(s['total']):>10,.2f}")
        print()

        # Net
        total_income = sum(s["total"] for s in income_cats) if income_cats else 0
        net = total_income + total_expense
        print(f"Net: ${net:>,.2f}")
        if marker_cats := [
            s for s in expense_cats if targets.get(s["category"], 0) and abs(s["total"]) > targets.get(s["category"], 0)
        ]:
            print(f"\n*** = over budget ({len(marker_cats)} categories)")
        print("*   = >80% of budget")


def _print_multi_month(conn: sqlite3.Connection, month_list: list[str]) -> None:
    """Print a multi-month spending report using budget_categories data."""
    # month_list is most-recent-first; reverse for chronological display
    months_chrono = list(reversed(month_list))

    # ── Monthly Summary ──
    print("Multi-Month Spending Report")
    print("=" * 80)
    print(f"Period: {months_chrono[0][:7]} to {months_chrono[-1][:7]} ({len(months_chrono)} months)\n")

    print(f"{'Month':<10} {'Budgeted':>11} {'Spent':>11} {'Net':>11}")
    print("-" * 46)

    sum_budgeted = 0.0
    sum_activity = 0.0
    month_data: dict[str, list[dict]] = {}
    for m in months_chrono:
        cats = _get_budget_month_data(conn, m)
        month_data[m] = cats
        budgeted = sum(c["budgeted"] or 0 for c in cats if not should_skip_group(c.get("category_group_name") or ""))
        activity = sum(c["activity"] or 0 for c in cats if not should_skip_group(c.get("category_group_name") or ""))
        net = budgeted + activity  # activity is negative for spending
        sum_budgeted += budgeted
        sum_activity += activity
        print(f"  {m[:7]:<8} ${budgeted:>10,.2f} ${abs(activity):>10,.2f} ${net:>10,.2f}")

    print("-" * 46)
    sum_net = sum_budgeted + sum_activity
    print(f"  {'Total':<8} ${sum_budgeted:>10,.2f} ${abs(sum_activity):>10,.2f} ${sum_net:>10,.2f}")
    n = len(months_chrono)
    print(f"  {'Average':<8} ${sum_budgeted / n:>10,.2f} ${abs(sum_activity) / n:>10,.2f} ${sum_net / n:>10,.2f}")
    print()

    # ── Per-Category Breakdown ──
    # Collect all category names across all months, compute totals (excluding infra groups)
    cat_totals: dict[str, dict] = {}
    for m in months_chrono:
        for c in month_data[m]:
            if should_skip_group(c.get("category_group_name") or ""):
                continue
            name = c["name"]
            if name not in cat_totals:
                cat_totals[name] = {"budgeted": 0.0, "activity": 0.0, "months_active": 0}
            cat_totals[name]["budgeted"] += c["budgeted"] or 0
            cat_totals[name]["activity"] += c["activity"] or 0
            if c["activity"]:
                cat_totals[name]["months_active"] += 1

    # Split into expense categories (negative activity) and income
    expense_cats = {k: v for k, v in cat_totals.items() if v["activity"] < 0}
    income_cats = {k: v for k, v in cat_totals.items() if v["activity"] > 0}

    if income_cats:
        total_income = sum(v["activity"] for v in income_cats.values())
        print(f"INCOME (${total_income:,.2f} total):")
        for name in sorted(income_cats, key=lambda k: -income_cats[k]["activity"]):
            v = income_cats[name]
            print(f"  {name:<40} ${v['activity']:>10,.2f}")
        print()

    if expense_cats:
        print(f"{'Category':<34} {'Total Spent':>11} {'Avg/Mo':>10} {'Budget/Mo':>10} {'Avg %':>6}")
        print("-" * 80)

        for name in sorted(expense_cats, key=lambda k: expense_cats[k]["activity"]):
            v = expense_cats[name]
            spent = abs(v["activity"])
            avg_spent = spent / n
            avg_budget = v["budgeted"] / n
            pct = avg_spent / avg_budget * 100 if avg_budget else 0

            marker = ""
            if avg_budget and avg_spent > avg_budget:
                marker = " ***"
            elif avg_budget and pct > 80:
                marker = " *"

            budget_str = f"${avg_budget:>9,.2f}" if avg_budget else "      -   "
            pct_str = f"{pct:>4.0f}%" if avg_budget else "    -"

            print(f"  {name:<32} ${spent:>10,.2f} ${avg_spent:>9,.2f} {budget_str} {pct_str}{marker}")

        print("-" * 80)
        total_spent = abs(sum(v["activity"] for v in expense_cats.values()))
        total_budget = sum(v["budgeted"] for v in expense_cats.values())
        print(f"  {'Total':<32} ${total_spent:>10,.2f} ${total_spent / n:>9,.2f} ${total_budget / n:>9,.2f}")
        print()

        # Top 5 by total spending
        top5 = sorted(expense_cats.items(), key=lambda kv: kv[1]["activity"])[:5]
        print("Top 5 Spending Categories (by total):")
        for name, v in top5:
            print(f"  {name:<40} ${abs(v['activity']):>10,.2f}")
        print()

        over_budget = [
            k for k, v in expense_cats.items() if v["budgeted"] and abs(v["activity"]) / n > v["budgeted"] / n
        ]
        if over_budget:
            print(f"*** = avg spending over avg budget ({len(over_budget)} categories)")
        print("*   = >80% of avg budget")


def run_spending(months: int = 1) -> None:
    """Show spending by category with budget comparison.

    When months=1, shows the current month with transaction-level detail.
    When months>1, shows a multi-month summary using budget_categories data.
    """
    conn = get_connection()
    try:
        init_db(conn)

        if months <= 1:
            month_start = _current_month_start()

            spending = _get_spending_by_category(conn, month_start)
            if not spending:
                print("No transactions this month. Run 'ynab sync' first.")
                return

            targets = _get_budget_targets(conn, month_start)
            _print_single_month(spending, targets, month_start, conn=conn)
        else:
            month_list = _month_starts(months)
            # Check we have data for at least one month
            placeholders = ",".join("?" for _ in month_list)
            count = conn.execute(
                f"SELECT COUNT(*) AS n FROM budget_categories WHERE budget_month IN ({placeholders})",
                month_list,
            ).fetchone()["n"]
            if not count:
                print(f"No budget data for the last {months} months. Run 'ynab sync' first.")
                return
            _print_multi_month(conn, month_list)
    finally:
        conn.close()


def run_category_trend(category: str, months: int = 6) -> None:
    """Show spending trend for a specific category over N months."""
    conn = get_connection()
    try:
        init_db(conn)

        # Find matching category - exact match first, then substring.
        # hidden intentionally omitted: INSERT OR REPLACE propagates current hidden flag to historical rows
        all_cats = conn.execute(
            """
            SELECT DISTINCT name FROM budget_categories
            WHERE deleted = 0
              AND LOWER(name) = LOWER(?)
            ORDER BY name
        """,
            (category,),
        ).fetchall()

        if not all_cats:
            all_cats = conn.execute(
                """
                SELECT DISTINCT name FROM budget_categories
                WHERE deleted = 0
                  AND LOWER(name) LIKE ?
                ORDER BY name
            """,
                (f"%{category.lower()}%",),
            ).fetchall()

        if not all_cats:
            print(f"No category matching '{category}' found.")
            return

        if len(all_cats) > 1:
            print(f"Multiple matches for '{category}':")
            for c in all_cats:
                print(f"  - {c['name']}")
            cat_name = all_cats[0]["name"]
            print(f"\nUsing: {cat_name}\n")
        else:
            cat_name = all_cats[0]["name"]

        # Budget data for last N months
        rows = conn.execute(
            """
            SELECT budget_month, budgeted, activity, balance
            FROM budget_categories
            WHERE name = ? AND deleted = 0
            ORDER BY budget_month DESC
            LIMIT ?
        """,
            (cat_name, months),
        ).fetchall()

        if not rows:
            print(f"No budget data for '{cat_name}'.")
            return

        print(f"Category Trend: {cat_name}")
        print("=" * 70)
        print(f"{'Month':<10} {'Budgeted':>10} {'Activity':>10} {'Balance':>10} {'% Used':>8}")
        print("-" * 70)

        activities = []
        for row in reversed(rows):
            budgeted = row["budgeted"] or 0
            activity = row["activity"] or 0
            balance = row["balance"] or 0
            pct = abs(activity) / budgeted * 100 if budgeted else 0
            activities.append(abs(activity))

            marker = ""
            if balance < 0:
                marker = " ***"
            elif budgeted and pct > 80:
                marker = " *"

            cols = f"${budgeted:>9,.2f} ${activity:>9,.2f} ${balance:>9,.2f} {pct:>6.0f}%{marker}"
            print(f"  {row['budget_month'][:7]:<8} {cols}")

        avg = sum(activities) / len(activities) if activities else 0
        print("-" * 70)
        print(f"  {'Average':<8} {'':>11} ${avg:>9,.2f}")

        if len(activities) >= 3:
            recent = sum(activities[-3:]) / 3
            older = sum(activities[:-3]) / max(len(activities) - 3, 1) if len(activities) > 3 else avg
            if older > 0:
                change = (recent - older) / older * 100
                direction = "UP" if change > 5 else "DOWN" if change < -5 else "STABLE"
                print(f"  Trend: {direction} ({change:+.0f}% recent 3mo vs prior)")

        # Current month transactions for this category
        month_start = _current_month_start()
        txns = conn.execute(
            """
            SELECT t.date, COALESCE(t.payee_name, p.name) AS payee_name,
                   COALESCE(st.amount, t.amount) AS amount,
                   COALESCE(st.memo, t.memo) AS memo
            FROM transactions t
            LEFT JOIN subtransactions st ON st.transaction_id = t.id AND st.deleted = 0
            LEFT JOIN payees p ON t.payee_id = p.id
            WHERE t.deleted = 0
              AND t.date >= ?
              AND COALESCE(st.category_name, t.category_name) = ?
            ORDER BY t.date DESC
        """,
            (month_start, cat_name),
        ).fetchall()

        if txns:
            print(f"\nCurrent Month Transactions ({len(txns)}):")
            for t in txns[:10]:
                memo = f" ({t['memo']})" if t["memo"] else ""
                print(f"  {t['date']}  {t['payee_name']:<30} ${t['amount']:>10,.2f}{memo}")
            if len(txns) > 10:
                print(f"  ... and {len(txns) - 10} more")
    finally:
        conn.close()


def _normalize_month(month: str | None) -> str:
    """Normalize a YYYY-MM or YYYY-MM-DD string to YYYY-MM-01. Defaults to current month."""
    if month is None:
        return datetime.now().strftime("%Y-%m-01")
    parts = month.strip().split("-")
    if len(parts) >= 2:
        return f"{parts[0]}-{parts[1]}-01"
    return datetime.now().strftime("%Y-%m-01")


def _trailing_avg_by_category(conn: sqlite3.Connection, target_month: str, n: int = 3) -> dict[str, float]:
    """Return average absolute activity per category for the N months prior to target_month.

    Uses budget_categories.activity (which includes subtransaction data already aggregated
    by YNAB). Returns a dict of {category_name: avg_spend} where avg_spend is a positive
    dollar amount. Categories with no activity in the window are omitted.
    """
    # Build a list of N prior month-start strings
    t = datetime.strptime(target_month, "%Y-%m-01")
    prior_months = []
    for i in range(1, n + 1):
        m = t.month - i
        y = t.year
        while m <= 0:
            m += 12
            y -= 1
        prior_months.append(f"{y:04d}-{m:02d}-01")

    if not prior_months:
        return {}

    # hidden intentionally omitted: INSERT OR REPLACE propagates current hidden flag to historical rows
    placeholders = ",".join("?" for _ in prior_months)
    rows = conn.execute(
        f"""
        SELECT name,
               AVG(ABS(activity)) AS avg_activity,
               COUNT(*) AS month_count
        FROM budget_categories
        WHERE budget_month IN ({placeholders})
          AND deleted = 0
          AND activity < 0
        GROUP BY name
        """,
        prior_months,
    ).fetchall()

    return {row["name"]: row["avg_activity"] or 0.0 for row in rows if row["avg_activity"]}


def run_spending_pace(month: str | None = None) -> None:
    """Show mid-month spending pace per category vs budget and 3-month trailing average."""
    conn = get_connection()
    try:
        init_db(conn)

        target_month = _normalize_month(month)
        now = datetime.now()

        year = int(target_month[:4])
        mon = int(target_month[5:7])
        days_in_month = calendar.monthrange(year, mon)[1]

        # Days elapsed - for the target month. If target month is in the past, treat as full.
        # If it's in the future, treat as 0.
        target_dt = datetime(year, mon, 1)
        if now.year == year and now.month == mon:
            days_elapsed = max(now.day, 1)
        elif now > target_dt:
            days_elapsed = days_in_month
        else:
            days_elapsed = 1

        pct_elapsed = days_elapsed / days_in_month

        # Load current-month budget categories
        cats = conn.execute(
            """
            SELECT name, category_group_name, budgeted, activity, balance, goal_type, goal_target
            FROM budget_categories
            WHERE budget_month = ? AND deleted = 0 AND hidden = 0
            ORDER BY name
            """,
            (target_month,),
        ).fetchall()

        if not cats:
            print(f"No budget data for {target_month[:7]}. Run 'ynab sync' first.")
            return

        # Load 3-month trailing averages
        trailing_avgs = _trailing_avg_by_category(conn, target_month, n=3)

        # Classify each category
        hot = []
        on_track = []
        under_budget = []
        skipped = 0

        for row in cats:
            name = row["name"]
            budgeted = row["budgeted"] or 0.0
            activity = row["activity"] or 0.0  # negative = spending

            # Skip infrastructure/excluded groups (business categories, internal, etc.)
            if should_skip_group(row["category_group_name"] or ""):
                skipped += 1
                continue

            # Skip categories with $0 budgeted
            if budgeted <= 0:
                skipped += 1
                continue

            # Skip income / inflow / transfer / credit card payment categories
            lname = name.lower()
            if any(
                lname.startswith(prefix)
                for prefix in (
                    "inflow:",
                    "income:",
                    "transfer",
                    "credit card",
                    "internal master category",
                )
            ):
                skipped += 1
                continue

            # Only count negative activity (actual spending); refunds/inflows should not flag.
            spent = abs(activity) if activity < 0 else 0.0
            pct_used = spent / budgeted if budgeted else 0.0
            pace = pct_used / pct_elapsed if pct_elapsed > 0 else 0.0
            # If already at or over 100% of budget, don't extrapolate linearly.
            # Fixed obligations (mortgage, car payment, CC payment) legitimately
            # hit 100% on day 1 - projecting 2.5x overspend is false signal.
            # Cap at actual spent; if spent > budgeted it still flags correctly.
            if pct_used >= 1.0:
                projected = spent
            else:
                projected = (spent / max(days_elapsed, 1)) * days_in_month
            trailing_avg = trailing_avgs.get(name, 0.0)

            entry = {
                "name": name,
                "budgeted": budgeted,
                "spent": spent,
                "pct_used": pct_used,
                "pace": pace,
                "projected": projected,
                "trailing_avg": trailing_avg,
            }

            # Two-tier HOT classification (unified with dashboard):
            # (a) Already overspent: spent > budgeted
            # (b) Pace-projected to overspend: pace > 1.2 AND projected > budgeted
            if spent > budgeted or (pace > 1.2 and projected > budgeted):
                hot.append(entry)
            elif pace < 0.5:
                # Only show under-budget if the category has meaningful budget
                if budgeted >= 50:
                    under_budget.append(entry)
                else:
                    skipped += 1
            else:
                on_track.append(entry)

        # Sort sections
        hot.sort(key=lambda x: -x["pace"])
        on_track.sort(key=lambda x: -x["pct_used"])
        under_budget.sort(key=lambda x: x["pct_used"])

        # ── Header ──
        pct_elapsed_display = round(pct_elapsed * 100)
        header = f"Spending Pace ({target_month[:7]}, day {days_elapsed} of {days_in_month}"
        print(f"{header} - {pct_elapsed_display}% elapsed)")
        print("=" * 80)

        col_header = (
            f"  {'Category':<32} {'Budgeted':>9} {'Spent':>9} {'%Used':>6} {'Pace':>5} {'Proj':>8} {'3mo Avg':>8}"
        )
        row_sep = "  " + "-" * 77

        def _print_section(title: str, entries: list[dict]) -> None:
            print(f"\n{title} ({len(entries)} categories):")
            if not entries:
                print("  (none)")
                return
            print(col_header)
            print(row_sep)
            for e in entries:
                avg_str = f"${e['trailing_avg']:>7,.0f}" if e["trailing_avg"] else "       -"
                print(
                    f"  {e['name']:<32}"
                    f" ${e['budgeted']:>8,.2f}"
                    f" ${e['spent']:>8,.2f}"
                    f" {e['pct_used']:>5.0%}"
                    f" {e['pace']:>4.1f}x"
                    f" ${e['projected']:>7,.0f}"
                    f" {avg_str}"
                )

        _print_section("RUNNING HOT", hot)
        _print_section("ON TRACK", on_track)
        _print_section("UNDER BUDGET", under_budget)

        # ── Summary ──
        total_hot = len(hot)
        total_on_track = len(on_track)
        total_under = len(under_budget)

        overspend_cats = [(e["name"], e["projected"] - e["budgeted"]) for e in hot]
        total_projected_over = sum(v for _, v in overspend_cats if v > 0)
        over_count = sum(1 for _, v in overspend_cats if v > 0)

        print(f"\nSummary: {total_hot} hot, {total_on_track} on track, {total_under} under budget")
        if over_count:
            print(
                f"  Projected overspend if pace continues: ${total_projected_over:,.0f} across {over_count} categories"
            )
        else:
            print("  No categories projected to overspend at current pace.")
    finally:
        conn.close()


def _load_classification(conn: sqlite3.Connection) -> dict[str, set[str]]:
    """Load category classification from JSON config, with heuristic fallback.

    If ynab_tools/data/category_classification.json exists, use it. Otherwise, auto-classify
    based on spending variance: categories with <15% coefficient of variation over
    the last 6 months are likely fixed bills; the rest are discretionary.
    """
    # User copy takes precedence over example
    for filename in ("category_classification.json", "category_classification.example.json"):
        config_path = DATA_DIR / filename
        if config_path.exists():
            with open(config_path) as f:
                raw = json.load(f)
            return {k: set(v) for k, v in raw.items()}

    # Heuristic fallback: classify by spending variance.
    # hidden intentionally omitted: INSERT OR REPLACE propagates current hidden flag to historical rows
    rows = conn.execute(
        """
        SELECT name,
               AVG(ABS(activity)) AS avg_spend,
               COUNT(*) AS months_active
        FROM budget_categories
        WHERE budget_month >= date('now', '-6 months')
          AND deleted = 0
          AND ABS(activity) > 0
        GROUP BY name
        HAVING months_active >= 3
        """
    ).fetchall()

    fixed = set()
    discretionary = set()
    for row in rows:
        name = row["name"]
        avg = row["avg_spend"] or 0
        if avg == 0:
            continue
        # Check variance
        var_rows = conn.execute(
            """
            SELECT ABS(activity) AS spend FROM budget_categories
            WHERE name = ? AND budget_month >= date('now', '-6 months')
              AND deleted = 0 AND ABS(activity) > 0
            """,
            (name,),
        ).fetchall()
        spends = [r["spend"] for r in var_rows]
        if len(spends) < 3:
            continue
        mean = sum(spends) / len(spends)
        if mean == 0:
            continue
        variance = sum((x - mean) ** 2 for x in spends) / len(spends)
        cv = (variance**0.5) / mean
        if cv < 0.15:
            fixed.add(name)
        else:
            discretionary.add(name)

    return {"fixed": fixed, "discretionary": discretionary, "savings": set()}


def _classify_category(name: str, classification: dict[str, set[str]]) -> str:
    """Classify a category name into fixed/discretionary/savings/other."""
    for cls, names in classification.items():
        if name in names:
            return cls
    return "other"


def run_spending_breakdown(months: int = 6) -> None:
    """Show fixed vs discretionary vs savings spending breakdown by month."""
    conn = get_connection()
    try:
        init_db(conn)
        classification = _load_classification(conn)
        month_list = _month_starts(months)

        # Collect per-month totals by type
        month_data: list[dict] = []
        # hidden intentionally omitted: INSERT OR REPLACE propagates current hidden flag to historical rows
        for month_start in reversed(month_list):
            rows = conn.execute(
                """
                SELECT name, activity
                FROM budget_categories
                WHERE budget_month = ?
                  AND deleted = 0
                  AND activity < 0
                  AND name NOT LIKE 'Inflow:%'
                  AND name NOT LIKE 'Income:%'
                  AND name NOT LIKE 'Transfer%'
                  AND name NOT LIKE '%Credit Card%'
                  AND name NOT LIKE '%Credit –%'
                  AND name NOT LIKE '%Rewards%'
                  AND name NOT LIKE 'Holding:%'
                """,
                (month_start,),
            ).fetchall()

            if not rows:
                continue

            totals = {"fixed": 0.0, "discretionary": 0.0, "savings": 0.0, "other": 0.0}
            for row in rows:
                activity = abs(row["activity"] or 0)
                cls = _classify_category(row["name"], classification)
                totals[cls] += activity

            month_data.append({"month": month_start[:7], **totals})

        if not month_data:
            print("No spending data found. Run 'ynab sync' first.")
            return

        print(f"Spending Breakdown (last {months} months)")
        print("=" * 90)
        print(f"  {'Month':<10} {'Fixed':>12} {'Discretionary':>14} {'Savings':>12} {'Other':>12} {'Total':>12}")
        print("  " + "-" * 86)

        sum_fixed = sum_disc = sum_sav = sum_other = 0.0
        for md in month_data:
            total = md["fixed"] + md["discretionary"] + md["savings"] + md["other"]
            sum_fixed += md["fixed"]
            sum_disc += md["discretionary"]
            sum_sav += md["savings"]
            sum_other += md["other"]
            print(
                f"  {md['month']:<10}"
                f" ${md['fixed']:>10,.0f}"
                f" ${md['discretionary']:>12,.0f}"
                f" ${md['savings']:>10,.0f}"
                f" ${md['other']:>10,.0f}"
                f" ${total:>10,.0f}"
            )

        n = len(month_data)
        sum_total = sum_fixed + sum_disc + sum_sav + sum_other
        avg_total = sum_total / n if n else 0
        print("  " + "-" * 86)
        print(
            f"  {'Average':<10}"
            f" ${sum_fixed / n:>10,.0f}"
            f" ${sum_disc / n:>12,.0f}"
            f" ${sum_sav / n:>10,.0f}"
            f" ${sum_other / n:>10,.0f}"
            f" ${avg_total:>10,.0f}"
        )

        # Percentage row
        if sum_total > 0:
            print(
                f"  {'% Total':<10}"
                f" {sum_fixed / sum_total:>11.0%}"
                f" {sum_disc / sum_total:>13.0%}"
                f" {sum_sav / sum_total:>11.0%}"
                f" {sum_other / sum_total:>11.0%}"
            )
    finally:
        conn.close()
