"""Fund categories: set/adjust budgeted amounts and fund underfunded goals."""

import logging
import sqlite3
from datetime import UTC, datetime

from ..client import YNABClient
from ..config import require_credentials
from ..db import current_month, get_connection, init_db
from ..stats import parse_category_input, recommend_target, should_skip_group, spending_stats, strip_emoji_prefix

logger = logging.getLogger(__name__)


def _month_str(month: str | None) -> str:
    """Normalize month input to YYYY-MM-01 format."""
    if not month:
        return current_month()
    # Accept YYYY-MM or YYYY-MM-DD
    parts = month.split("-")
    if len(parts) == 2:
        return f"{month}-01"
    return month


def _last_synced(conn: sqlite3.Connection, month: str) -> str | None:
    """Get the last sync timestamp for a budget month."""
    row = conn.execute("SELECT last_synced_at FROM budget_months WHERE month = ?", (month,)).fetchone()
    return row["last_synced_at"] if row else None


def _print_sync_age(conn: sqlite3.Connection, month: str) -> None:
    """Print how long ago data was synced, with a warning if stale."""
    synced_at = _last_synced(conn, month)
    if not synced_at:
        print("  Last synced: never - run 'ynab sync' first")
        return

    synced_dt = datetime.fromisoformat(synced_at)
    age = datetime.now(UTC) - synced_dt
    hours = age.total_seconds() / 3600

    if hours < 1:
        age_str = f"{int(age.total_seconds() / 60)} minutes ago"
    elif hours < 24:
        age_str = f"{int(hours)} hours ago"
    else:
        age_str = f"{int(hours / 24)} days ago"

    warning = "  ⚠ Data may be stale - run 'ynab sync'" if hours > 4 else ""
    print(f"  Last synced: {synced_at[:16]} ({age_str}){warning}")


def _find_category(conn: sqlite3.Connection, name: str, month: str) -> list[dict]:
    """Find categories matching a name. Tries exact match first, then substring.

    Accepts "Group: Category" or "Category (Group)" to filter by group when multiple
    categories share the same name. Group comparison strips leading emoji/symbol
    prefixes so callers do not need to include decorative characters.
    """
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
            SELECT id, name, category_group_name, budgeted, balance,
                   goal_type, goal_target, goal_under_funded
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
            SELECT id, name, category_group_name, budgeted, balance,
                   goal_type, goal_target, goal_under_funded
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


def _log_funding_change(
    conn: sqlite3.Connection,
    category_id: str,
    category_name: str,
    category_group: str | None,
    month: str,
    old_budgeted: float,
    new_budgeted: float,
    source: str,
) -> None:
    """Record a funding change in the audit log."""
    conn.execute(
        """
        INSERT INTO funding_log
            (timestamp, category_id, category_name, category_group,
             budget_month, old_budgeted, new_budgeted, delta, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
            datetime.now(UTC).isoformat(),
            category_id,
            category_name,
            category_group,
            month,
            old_budgeted,
            new_budgeted,
            new_budgeted - old_budgeted,
            source,
        ),
    )


def _get_manually_reduced(conn: sqlite3.Connection, month: str) -> dict[str, float]:
    """Find categories that were manually reduced this month.

    Returns {category_name: total_negative_delta} for categories where
    money was deliberately moved out via 'fund' (not 'fund-goals').
    These should not be auto-restored by fund --goals.
    """
    rows = conn.execute(
        """
        SELECT category_name, SUM(delta) AS net_delta
        FROM funding_log
        WHERE budget_month = ?
          AND source = 'fund'
        GROUP BY category_name
        HAVING SUM(delta) < 0
    """,
        (month,),
    ).fetchall()
    return {row["category_name"]: row["net_delta"] for row in rows}


def _dollars_to_milliunits(amount: float) -> int:
    return int(round(amount * 1000))


def _avg_monthly_income(conn: sqlite3.Connection, months: int = 12) -> float:
    """Rolling N-month average of non-zero income values from complete months."""
    from datetime import date

    today = date.today()
    y, m = today.year, today.month
    m -= 1
    if m == 0:
        m, y = 12, y - 1
    month_list = []
    for _ in range(months):
        month_list.append(f"{y:04d}-{m:02d}-01")
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    placeholders = ",".join("?" for _ in month_list)
    # placeholders contains only '?' characters; month_list values are bound as parameters
    rows = conn.execute(
        f"SELECT income FROM budget_months WHERE month IN ({placeholders})",
        month_list,
    ).fetchall()
    non_zero = [float(r["income"]) for r in rows if r["income"] and float(r["income"]) > 0]
    return round(sum(non_zero) / len(non_zero), 2) if non_zero else 0.0


def _total_budgeted_for_month(conn: sqlite3.Connection, month: str) -> float:
    """Sum of all non-infrastructure category budgeted amounts for a month."""
    rows = conn.execute(
        # hidden intentionally omitted: INSERT OR REPLACE propagates the current
        # hidden flag to all historical rows, so categories hidden after the fact
        # (e.g. a paid-off loan) would lose their entire activity history.
        "SELECT category_group_name, budgeted FROM budget_categories WHERE budget_month = ? AND deleted = 0",
        (month,),
    ).fetchall()
    return sum(float(r["budgeted"] or 0) for r in rows if not should_skip_group(r["category_group_name"] or ""))


def run_fund(category: str, amount_str: str, month: str | None = None, apply: bool = False) -> None:
    """Set or adjust the budgeted amount for a category."""
    month = _month_str(month)
    conn = get_connection()
    try:
        init_db(conn)
        matches = _find_category(conn, category, month)

        if not matches:
            print(f"No category matching '{category}' for {month[:7]}.")
            return

        if len(matches) > 1:
            print(f"Multiple categories match '{category}':")
            for m in matches:
                print(f"  {m['name']}  (group: {m['category_group_name']})")
            print("\nDisambiguate with 'Group: Category' or 'Category (Group)' format.")
            print("Example: 'Business: Licenses & Fees' or 'Licenses & Fees (Business)'")
            return

        cat = matches[0]
        current = cat["budgeted"] or 0

        # Parse amount: "100" sets absolute, "+100" or "-50" adjusts relative, "=600" also sets absolute
        try:
            if amount_str.startswith("="):
                new_amount = float(amount_str[1:])
            elif amount_str.startswith("+") or amount_str.startswith("-"):
                new_amount = current + float(amount_str)
            else:
                new_amount = float(amount_str)
        except ValueError:
            print(f"Invalid amount '{amount_str}': must be a number like 100, +100, -50, or =600.")
            return

        if new_amount < 0 and cat["category_group_name"] != "Credit Card Payments":
            # Allow negative budgeted if category has accumulated balance to cover it.
            # Balance includes carryover from prior months, so the resulting balance
            # after changing budgeted is: current_balance + (new_budgeted - old_budgeted)
            cat_balance = cat.get("balance", 0) or 0
            resulting_balance = cat_balance + (new_amount - current)
            if resulting_balance < 0:
                print(
                    f"Error: resulting balance (${resulting_balance:,.2f}) would"
                    f" overdraw category (current balance ${cat_balance:,.2f})."
                )
                return

        if new_amount == current:
            print(f"{cat['name']} is already at ${current:,.2f}.")
            return

        # Show what will change and confirm
        delta_display = new_amount - current
        sign = "+" if delta_display >= 0 else ""
        print(f"  {cat['name']}  ({cat['category_group_name']})")
        print(f"  {month[:7]}:  ${current:,.2f} → ${new_amount:,.2f}  ({sign}${delta_display:,.2f})")

        avg_income = _avg_monthly_income(conn)
        if avg_income > 0:
            total_bud = _total_budgeted_for_month(conn, month)
            old_pct = total_bud / avg_income * 100
            new_total = total_bud - current + new_amount
            new_pct = new_total / avg_income * 100
            committed_line = (
                f"  Budget:   ${total_bud:,.2f} → ${new_total:,.2f} committed"
                f"  ({old_pct:.1f}% → {new_pct:.1f}% of ${avg_income:,.0f} avg income)"
            )
            print(committed_line)
            if new_total > avg_income:
                over = new_total - avg_income
                print(f"  WARNING: Total targets would exceed average monthly income by ${over:,.2f}.")
        print()

        if not apply:
            try:
                confirm = input("Apply? [y/N] ").strip().lower()
            except EOFError:
                confirm = "n"
            if confirm != "y":
                print("Cancelled. Use --apply to skip confirmation.")
                return

        token, plan_id = require_credentials()
        client = YNABClient(token, plan_id)

        result = client.update_category_budget(month, cat["id"], _dollars_to_milliunits(new_amount))

        # Log and update local DB to reflect the change
        _log_funding_change(
            conn,
            cat["id"],
            cat["name"],
            cat["category_group_name"],
            month,
            current,
            new_amount,
            "fund",
        )

        updated = result.get("category", {})
        if updated:
            conn.execute(
                """
                UPDATE budget_categories
                SET budgeted = ?, balance = ?, goal_under_funded = ?
                WHERE id = ? AND budget_month = ?
            """,
                (
                    updated.get("budgeted", _dollars_to_milliunits(new_amount)) / 1000,
                    (updated.get("balance") or 0) / 1000,
                    (updated.get("goal_under_funded") or 0) / 1000,
                    cat["id"],
                    month,
                ),
            )

        conn.commit()
        print(f"Updated {cat['name']} to ${new_amount:,.2f}.")

    finally:
        conn.close()


def run_fund_goals(month: str | None = None, apply: bool = False) -> None:
    """Fund all underfunded goal categories."""
    month = _month_str(month)
    conn = get_connection()
    try:
        init_db(conn)

        # Get RTA
        rta_row = conn.execute("SELECT to_be_budgeted FROM budget_months WHERE month = ?", (month,)).fetchone()
        rta = (rta_row["to_be_budgeted"] or 0) if rta_row else 0

        # Get underfunded categories
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT id, name, category_group_name, budgeted,
                       goal_type, goal_target, goal_under_funded
                FROM budget_categories
                WHERE budget_month = ?
                  AND deleted = 0 AND hidden = 0
                  AND goal_under_funded > 0
                ORDER BY category_group_name, name
            """,
                (month,),
            ).fetchall()
        ]

        if not rows:
            print(f"No underfunded goals for {month[:7]}.")
            return

        # Check for categories that were manually reduced this month
        reduced = _get_manually_reduced(conn, month)
        skipped = []
        eligible = []
        for r in rows:
            if r["name"] in reduced:
                skipped.append(r)
            else:
                eligible.append(r)

        total_needed = sum(r["goal_under_funded"] for r in eligible)
        total_skipped = sum(r["goal_under_funded"] for r in skipped)

        print(f"Underfunded Goals ({month[:7]})")
        print("=" * 60)
        _print_sync_age(conn, month)
        print(f"  RTA available: ${rta:>12,.2f}")
        print(f"  Total needed:  ${total_needed:>12,.2f}")
        if total_needed > rta > 0:
            print(f"  Shortfall:     ${total_needed - rta:>12,.2f}")
        print()

        for r in eligible:
            current = r["budgeted"] or 0
            needed = r["goal_under_funded"]
            new_amount = current + needed
            print(f"  {r['name']:<40} ${current:>9,.2f} → ${new_amount:>9,.2f}  (+${needed:,.2f})")

        print(f"\n  {len(eligible)} categories, ${total_needed:,.2f} total")

        if skipped:
            print(f"\n  SKIPPED ({len(skipped)} manually reduced this month):")
            for r in skipped:
                delta = reduced[r["name"]]
                print(
                    f"    {r['name']:<38} underfunded ${r['goal_under_funded']:,.2f}  (reduced by ${abs(delta):,.2f})"
                )
            print(f"    Total skipped: ${total_skipped:,.2f}")

        rows = eligible  # only fund eligible categories

        if not apply:
            print("\nDry run. Use --apply to push changes to YNAB.")
            return

        if total_needed > rta > 0:
            confirm = (
                input(f"\nRTA (${rta:,.2f}) is less than needed (${total_needed:,.2f}). Continue? [y/N] ")
                .strip()
                .lower()
            )
            if confirm != "y":
                print("Cancelled.")
                return

        token, plan_id = require_credentials()
        client = YNABClient(token, plan_id)

        success = 0
        failed = 0
        for r in rows:
            current = r["budgeted"] or 0
            needed = r["goal_under_funded"]
            new_amount = current + needed
            try:
                result = client.update_category_budget(month, r["id"], _dollars_to_milliunits(new_amount))
                _log_funding_change(
                    conn,
                    r["id"],
                    r["name"],
                    r["category_group_name"],
                    month,
                    current,
                    new_amount,
                    "fund-goals",
                )
                updated = result.get("category", {})
                if updated:
                    conn.execute(
                        """
                        UPDATE budget_categories
                        SET budgeted = ?, balance = ?, goal_under_funded = ?
                        WHERE id = ? AND budget_month = ?
                    """,
                        (
                            updated.get("budgeted", _dollars_to_milliunits(new_amount)) / 1000,
                            (updated.get("balance") or 0) / 1000,
                            (updated.get("goal_under_funded") or 0) / 1000,
                            r["id"],
                            month,
                        ),
                    )
                success += 1
            except Exception as e:
                logger.error(f"Failed to fund {r['name']}: {e}")
                failed += 1

        conn.commit()
        if failed:
            print(f"\nFunded {success} categories. {failed} failed.")
            print("Run 'ynab sync' before retrying to refresh goal balances.")
        else:
            print(f"\nFunded {success} categories.")

    finally:
        conn.close()


def _get_monthly_spending(conn: sqlite3.Connection, category_name: str, months: int = 12) -> list[float]:
    """Get monthly spending amounts for a category (as positive values). Excludes current month.

    Returns ALL months including zeros to preserve the true spending pattern.
    """
    rows = conn.execute(
        """
        SELECT activity
        FROM budget_categories
        WHERE name = ? AND deleted = 0
          AND budget_month < ?
        ORDER BY budget_month DESC
        LIMIT ?
    """,
        (category_name, current_month(), months),
    ).fetchall()

    # activity is negative for spending; return as positive values (0 for no-spend months)
    return [abs(row["activity"]) if row["activity"] and row["activity"] < 0 else 0.0 for row in rows]


def run_fund_status(month: str | None = None) -> None:
    """Show funding status: target, average spend, recommendation, and budgeted per category."""
    month = _month_str(month)
    conn = get_connection()
    try:
        init_db(conn)

        rta_row = conn.execute("SELECT to_be_budgeted FROM budget_months WHERE month = ?", (month,)).fetchone()
        rta = (rta_row["to_be_budgeted"] or 0) if rta_row else 0

        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT name, category_group_name, budgeted, activity, balance,
                       goal_type, goal_target, goal_under_funded
                FROM budget_categories
                WHERE budget_month = ?
                  AND deleted = 0 AND hidden = 0
                  AND (goal_type IS NOT NULL OR budgeted > 0)
                ORDER BY category_group_name, name
            """,
                (month,),
            ).fetchall()
        ]

        if not rows:
            print(f"No budget data for {month[:7]}. Run 'ynab sync' first.")
            return

        print(f"Funding Status ({month[:7]})")
        print("=" * 115)
        _print_sync_age(conn, month)
        print(f"  Ready to Assign: ${rta:>12,.2f}\n")

        print(f"  {'Category':<34} {'Target':>9}  {'Avg Spend':>9}  {'Recommend':>9}  {'Budgeted':>9}  {'Note'}")
        print(f"  {'-' * 109}")

        current_group = None
        total_budgeted = 0
        total_underfunded = 0
        adjustments = []

        for r in rows:
            if r["category_group_name"] != current_group:
                current_group = r["category_group_name"]
                print(f"\n  {current_group}")

            budgeted = r["budgeted"] or 0
            underfund = r["goal_under_funded"] or 0
            goal_target = r["goal_target"] or 0
            total_budgeted += budgeted
            total_underfunded += underfund

            amounts = _get_monthly_spending(conn, r["name"])
            stats = spending_stats(amounts)
            rec_amount, rec_note = recommend_target(stats)

            # Target column
            target_str = f"${goal_target:>8,.2f}" if goal_target else "       -  "

            # Avg column
            avg_str = f"${stats['avg']:>8,.2f}" if stats["months"] else "       -  "

            # Recommend column
            if rec_amount is not None:
                rec_str = f"${rec_amount:>8,.2f}"
            else:
                rec_str = "       -  "

            # Note column
            note = ""
            if underfund > 0:
                note = f"need ${underfund:,.2f}"
            elif rec_note == "insufficient data":
                note = "<3mo data"
            elif rec_note == "high variance":
                note = "high variance"
            elif rec_note == "lumpy spending":
                if rec_amount and budgeted > 0 and rec_amount > budgeted * 1.05:
                    note = f"under by ${rec_amount - budgeted:,.0f} (lumpy)"
                    adjustments.append((r["name"], budgeted, rec_amount, rec_note))
                else:
                    note = "lumpy spending"
            elif rec_note == "moderate variance":
                if rec_amount and budgeted > 0 and rec_amount > budgeted * 1.05:
                    note = f"under by ${rec_amount - budgeted:,.0f} (moderate variance)"
                    adjustments.append((r["name"], budgeted, rec_amount, rec_note))
                else:
                    note = "moderate variance"
            elif rec_amount and budgeted > 0:
                if rec_amount > budgeted * 1.05:
                    note = f"under by ${rec_amount - budgeted:,.0f}"
                    adjustments.append((r["name"], budgeted, rec_amount, rec_note))
                elif rec_amount < budgeted * 0.85:
                    note = f"over by ${budgeted - rec_amount:,.0f}"
                    adjustments.append((r["name"], budgeted, rec_amount, rec_note))
                elif r["goal_type"] and budgeted > 0:
                    note = "funded"

            print(f"    {r['name']:<32} {target_str}  {avg_str}  {rec_str}  ${budgeted:>8,.2f}  {note}")

        print(f"\n  {'-' * 109}")
        print(f"  Total budgeted:    ${total_budgeted:>12,.2f}")
        if total_underfunded > 0:
            print(f"  Total underfunded: ${total_underfunded:>12,.2f}")

        avg_income = _avg_monthly_income(conn)
        if avg_income > 0:
            pct = total_budgeted / avg_income * 100
            headroom = avg_income - total_budgeted
            if pct < 85:
                fit_status = "OK"
            elif pct < 95:
                fit_status = "APPROACHING"
            elif pct < 100:
                fit_status = "TIGHT"
            else:
                fit_status = "OVER"
            print(f"\n  BUDGET FIT: {fit_status}")
            print(f"    Committed:  ${total_budgeted:>12,.2f}  ({pct:.1f}% of avg income)")
            print(f"    Avg income: ${avg_income:>12,.2f}")
            print(f"    Headroom:   ${headroom:>12,.2f}")
            if fit_status in ("TIGHT", "OVER"):
                print("    Consider cutting categories flagged as 'decrease' in RECOMMENDED ADJUSTMENTS below.")

        if adjustments:
            print(f"\n  RECOMMENDED ADJUSTMENTS ({len(adjustments)} categories):")
            for name, budgeted, rec, note in sorted(adjustments, key=lambda x: abs(x[2] - x[1]), reverse=True):
                diff = rec - budgeted
                direction = "increase" if diff > 0 else "decrease"
                variance_flag = " *" if note in ("moderate variance", "lumpy spending") else ""
                print(
                    f"    {name:<34} ${budgeted:>8,.2f} → ${rec:>8,.2f}  ({direction} ${abs(diff):,.0f}){variance_flag}"
                )
            total_diff = sum(r - b for _, b, r, _ in adjustments)
            if total_diff > 0:
                print(f"\n    Net change: +${total_diff:,.0f}/mo")
            else:
                print(f"\n    Net change: -${abs(total_diff):,.0f}/mo")
            print("    * = moderate variance, use judgment")

    finally:
        conn.close()


def run_fund_log(limit: int = 20, month: str | None = None) -> None:
    """Show the funding audit log."""
    conn = get_connection()
    try:
        init_db(conn)

        if month:
            month = _month_str(month)
            rows = conn.execute(
                """
                SELECT timestamp, category_name, category_group, budget_month,
                       old_budgeted, new_budgeted, delta, source
                FROM funding_log
                WHERE budget_month = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """,
                (month, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT timestamp, category_name, category_group, budget_month,
                       old_budgeted, new_budgeted, delta, source
                FROM funding_log
                ORDER BY timestamp DESC
                LIMIT ?
            """,
                (limit,),
            ).fetchall()

        if not rows:
            print("No funding changes logged yet.")
            return

        filter_label = f" for {month[:7]}" if month else ""
        print(f"Funding Log (last {limit}{filter_label})")
        print("=" * 100)
        print(f"  {'Timestamp':<20} {'Category':<32} {'Old':>9}  {'New':>9}  {'Delta':>9}  {'Source'}")
        print(f"  {'-' * 96}")

        for r in rows:
            ts = r["timestamp"][:16].replace("T", " ")
            delta = r["delta"]
            sign = "+" if delta >= 0 else ""
            print(
                f"  {ts:<20} {r['category_name']:<32} "
                f"${r['old_budgeted']:>8,.2f}  ${r['new_budgeted']:>8,.2f}  "
                f"{sign}${delta:>8,.2f}  {r['source']}"
            )

        total_delta = sum(r["delta"] for r in rows)
        sign = "+" if total_delta >= 0 else ""
        print(f"\n  Net change shown: {sign}${total_delta:,.2f}")

    finally:
        conn.close()
