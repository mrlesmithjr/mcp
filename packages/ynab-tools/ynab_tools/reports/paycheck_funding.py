"""Paycheck funding plan: prioritized category funding order when income arrives."""

import logging
import sqlite3
from datetime import date, datetime, timedelta

from ..db import get_connection, init_db
from .funding import (
    _dollars_to_milliunits,
    _log_funding_change,
    _month_str,
)
from .paycheck import (
    _detect_income_sources,
    _get_paycheck_config,
    _get_recurring_inflows,
    _project_next_dates,
)
from .paycheck_breakdown import _get_breakdown_config, _is_bonus_funded, _is_excluded

logger = logging.getLogger(__name__)

# How many days out to look for "due soon" planned expenses
_DUE_SOON_DAYS = 14

_ORDINAL_SUFFIXES = {1: "st", 2: "nd", 3: "rd"}


def _ordinal(n: int) -> str:
    """Return ordinal string for a day number (1st, 2nd, 3rd, 4th, ...)."""
    if 11 <= n % 100 <= 13:
        return f"{n}th"
    return f"{n}{_ORDINAL_SUFFIXES.get(n % 10, 'th')}"


# Minimum avg monthly transactions to consider a category "daily spending"
_DAILY_SPEND_THRESHOLD = 8


def _get_rta(conn: sqlite3.Connection, month: str) -> float:
    """Get Ready to Assign for the given budget month."""
    row = conn.execute("SELECT to_be_budgeted FROM budget_months WHERE month = ?", (month,)).fetchone()
    return (row["to_be_budgeted"] or 0) if row else 0


def _get_next_income(conn: sqlite3.Connection) -> dict | None:
    """Detect the next expected paycheck date and amount.

    Returns dict with date (date object), amount, days_away, or None if undetectable.
    """
    override_payees = _get_paycheck_config()
    inflows = _get_recurring_inflows(conn, months=12)
    if not inflows:
        return None

    sources = _detect_income_sources(inflows, override_payees)
    if not sources:
        return None

    primary = sources[0]
    projections = _project_next_dates(primary, count=3)
    if not projections:
        return None

    next_p = projections[0]
    today = date.today()
    return {
        "date": next_p["date"],
        "amount": next_p["amount"],
        "days_away": (next_p["date"] - today).days,
        "is_bonus": next_p["is_bonus"],
    }


def _get_tier1_overspent(conn: sqlite3.Connection, month: str) -> list[dict]:
    """Tier 1: Categories with negative available balance (overspent)."""
    rows = conn.execute(
        """
        SELECT id, name, category_group_name, budgeted, balance, activity,
               goal_type, goal_target, goal_under_funded
        FROM budget_categories
        WHERE budget_month = ?
          AND deleted = 0 AND hidden = 0
          AND balance < 0
        ORDER BY balance ASC
        """,
        (month,),
    ).fetchall()

    items = []
    for row in rows:
        r = dict(row)
        needed = abs(r["balance"])
        items.append(
            {
                "id": r["id"],
                "name": r["name"],
                "group": r["category_group_name"],
                "budgeted": r["budgeted"] or 0,
                "balance": r["balance"],
                "amount_needed": needed,
                "new_budgeted": (r["budgeted"] or 0) + needed,
                "reason": f"overspent ${needed:,.2f}",
            }
        )
    return items


def _get_tier2_due_soon(conn: sqlite3.Connection, month: str) -> list[dict]:
    """Tier 2: Planned expenses due within 14 days that have a funding gap."""
    today = datetime.now()
    cutoff = (today + timedelta(days=_DUE_SOON_DAYS)).strftime("%Y-%m-%d")

    rows = conn.execute(
        """
        SELECT pe.id as plan_id, pe.category_name, pe.category_id,
               pe.amount, pe.due_date, pe.memo
        FROM planned_expenses pe
        WHERE pe.status = 'active' AND pe.due_date <= ?
        ORDER BY pe.due_date
        """,
        (cutoff,),
    ).fetchall()

    items = []
    for row in rows:
        r = dict(row)
        # Get current category balance
        cat_row = conn.execute(
            """
            SELECT id, category_group_name, budgeted, balance
            FROM budget_categories
            WHERE budget_month = ? AND name = ? AND deleted = 0 AND hidden = 0
            """,
            (month, r["category_name"]),
        ).fetchone()

        if cat_row is None:
            continue

        balance = cat_row["balance"] or 0
        budgeted = cat_row["budgeted"] or 0

        # Skip if the category is already at zero or positive - a balanced
        # category doesn't need RTA pre-funding. Incoming reimbursements will
        # make it positive on their own.
        if balance >= 0:
            continue

        gap = max(0, r["amount"] - balance)
        if gap <= 0:
            continue  # Already funded

        days_until = (datetime.strptime(r["due_date"], "%Y-%m-%d") - today).days
        due_label = r["due_date"][5:]  # MM-DD
        memo_hint = f" ({r['memo']})" if r.get("memo") else ""

        items.append(
            {
                "id": cat_row["id"],
                "name": r["category_name"],
                "group": cat_row["category_group_name"],
                "budgeted": budgeted,
                "balance": balance,
                "amount_needed": gap,
                "new_budgeted": budgeted + gap,
                "reason": f"due {due_label} ({days_until}d){memo_hint}",
                "due_date": r["due_date"],
            }
        )
    return items


def _get_daily_spending_categories(conn: sqlite3.Connection) -> dict[str, float]:
    """Detect high-frequency spending categories and their avg daily spend.

    Returns {category_name: daily_spend_estimate}.

    Lumpiness filter: categories where any single month's spend exceeds 2.5x
    the average monthly spend are excluded. These are event-driven categories
    (gifts, holidays, vacations) that spike when purchased, not true daily-spend
    categories. Bridging them produces inflated, misleading amounts.

    Consistency filter: categories must have spend in at least 2 of the 3
    looked-back months to qualify as recurring daily spend.
    """
    rows = conn.execute(
        """
        SELECT category_name, COUNT(*) / 3.0 as avg_monthly_txns
        FROM transactions
        WHERE deleted = 0
          AND date >= date('now', '-3 months')
          AND amount < 0
          AND category_name IS NOT NULL
          AND category_name != 'Split'
        GROUP BY category_name
        HAVING avg_monthly_txns >= ?
        """,
        (_DAILY_SPEND_THRESHOLD,),
    ).fetchall()

    daily_map: dict[str, float] = {}
    for row in rows:
        cat_name = row["category_name"]

        # Per-month spend breakdown for lumpiness and consistency checks
        monthly_rows = conn.execute(
            """
            SELECT strftime('%Y-%m', date) as month_key,
                   ABS(SUM(amount)) as monthly_spend
            FROM transactions
            WHERE deleted = 0
              AND date >= date('now', '-3 months')
              AND amount < 0
              AND category_name = ?
            GROUP BY month_key
            """,
            (cat_name,),
        ).fetchall()

        monthly_amounts = [r["monthly_spend"] for r in monthly_rows]
        months_with_spend = len(monthly_amounts)

        # Consistency: must have spend in at least 2 calendar months
        if months_with_spend < 2:
            continue

        total_3mo = sum(monthly_amounts)
        avg_monthly = total_3mo / 3.0  # always divide by lookback window, not months present
        max_monthly = max(monthly_amounts)

        # Lumpiness: skip if any month is more than 2.5x the average -
        # that spike pattern indicates event-driven spending, not daily flow.
        if avg_monthly > 0 and (max_monthly / avg_monthly) > 2.5:
            continue

        daily_map[cat_name] = total_3mo / 90.0

    return daily_map


def _get_tier3_bridge(
    conn: sqlite3.Connection,
    month: str,
    days_until_income: int,
) -> list[dict]:
    """Tier 3: Daily spending categories that are near $0 and need bridging."""
    if days_until_income <= 0:
        return []

    daily_map = _get_daily_spending_categories(conn)
    if not daily_map:
        return []

    items = []
    for cat_name, daily_rate in daily_map.items():
        if daily_rate <= 0:
            continue

        cat_row = conn.execute(
            """
            SELECT id, name, category_group_name, budgeted, balance
            FROM budget_categories
            WHERE budget_month = ? AND name = ? AND deleted = 0 AND hidden = 0
            """,
            (month, cat_name),
        ).fetchone()

        if cat_row is None:
            continue

        balance = cat_row["balance"] or 0
        budgeted = cat_row["budgeted"] or 0

        # Bridge target: cover daily spend for days until income
        bridge_target = daily_rate * days_until_income

        # Only bridge if balance is below target (near zero)
        if balance >= bridge_target:
            continue

        needed = bridge_target - balance
        if needed <= 0.50:  # Skip trivial amounts
            continue

        items.append(
            {
                "id": cat_row["id"],
                "name": cat_name,
                "group": cat_row["category_group_name"],
                "budgeted": budgeted,
                "balance": balance,
                "amount_needed": needed,
                "new_budgeted": budgeted + needed,
                "reason": (f"~${daily_rate:.0f}/day x {days_until_income}d (bal ${balance:,.2f})"),
                "daily_rate": daily_rate,
            }
        )

    # Sort by daily rate descending (highest frequency first)
    items.sort(key=lambda x: x.get("daily_rate", 0), reverse=True)
    return items


def _get_typical_bill_day(conn: sqlite3.Connection, category_name: str) -> int | None:
    """Find the most common day-of-month a category is charged, over last 6 months."""
    rows = conn.execute(
        """
        SELECT CAST(strftime('%d', date) AS INTEGER) as day_of_month, COUNT(*) as cnt
        FROM transactions
        WHERE deleted = 0
          AND date >= date('now', '-6 months')
          AND amount < 0
          AND category_name = ?
        GROUP BY day_of_month
        ORDER BY cnt DESC
        LIMIT 1
        """,
        (category_name,),
    ).fetchone()

    if rows and rows["cnt"] >= 2:
        return rows["day_of_month"]
    return None


def _get_tier4_monthly_bills(conn: sqlite3.Connection, month: str) -> list[dict]:
    """Tier 4: Recurring bills (goal categories) not yet funded this month."""
    rows = conn.execute(
        """
        SELECT id, name, category_group_name, budgeted, balance,
               goal_type, goal_target, goal_under_funded
        FROM budget_categories
        WHERE budget_month = ?
          AND deleted = 0 AND hidden = 0
          AND goal_type IS NOT NULL
          AND (budgeted = 0 OR goal_under_funded > 0)
        ORDER BY category_group_name, name
        """,
        (month,),
    ).fetchall()

    items = []
    for row in rows:
        r = dict(row)
        under = r["goal_under_funded"] or 0
        budgeted = r["budgeted"] or 0

        # Use goal_under_funded as the monthly amount needed - this is what
        # YNAB calculates as the gap for this month, not the cumulative target.
        if under <= 0:
            continue

        typical_day = _get_typical_bill_day(conn, r["name"])
        day_label = f", typically {_ordinal(typical_day)}" if typical_day else ""

        items.append(
            {
                "id": r["id"],
                "name": r["name"],
                "group": r["category_group_name"],
                "budgeted": budgeted,
                "balance": r["balance"] or 0,
                "amount_needed": under,
                "new_budgeted": budgeted + under,
                "reason": f"goal ({r['goal_type']}){day_label}",
                "typical_day": typical_day,
                "goal_type": r["goal_type"],
            }
        )

    # Sort: categories with earlier typical payment day first, then by name
    items.sort(key=lambda x: (x.get("typical_day") or 99, x["name"]))
    return items


def _get_tier5_remaining_goals(conn: sqlite3.Connection, month: str) -> list[dict]:
    """Tier 5: All other underfunded categories (no goal type but underfunded, or savings goals)."""
    rows = conn.execute(
        """
        SELECT id, name, category_group_name, budgeted, balance,
               goal_type, goal_target, goal_under_funded
        FROM budget_categories
        WHERE budget_month = ?
          AND deleted = 0 AND hidden = 0
          AND goal_under_funded > 0
        ORDER BY category_group_name, name
        """,
        (month,),
    ).fetchall()

    # Collect IDs already covered by tier 4
    tier4_ids = set()
    tier4_rows = conn.execute(
        """
        SELECT id FROM budget_categories
        WHERE budget_month = ?
          AND deleted = 0 AND hidden = 0
          AND goal_type IS NOT NULL
          AND (budgeted = 0 OR goal_under_funded > 0)
        """,
        (month,),
    ).fetchall()
    for r in tier4_rows:
        tier4_ids.add(r["id"])

    items = []
    for row in rows:
        r = dict(row)
        if r["id"] in tier4_ids:
            continue

        under = r["goal_under_funded"] or 0
        if under <= 0:
            continue

        items.append(
            {
                "id": r["id"],
                "name": r["name"],
                "group": r["category_group_name"],
                "budgeted": r["budgeted"] or 0,
                "balance": r["balance"] or 0,
                "amount_needed": under,
                "new_budgeted": (r["budgeted"] or 0) + under,
                "reason": f"underfunded ${under:,.2f}",
            }
        )
    return items


def generate_funding_plan(conn: sqlite3.Connection, month: str) -> dict:
    """Build a complete prioritized funding plan for the given month.

    Returns a structured plan dict with tiers, RTA, and next income info.
    """
    rta = _get_rta(conn, month)
    next_income = _get_next_income(conn)

    days_until_income = next_income["days_away"] if next_income else 0

    bonus_groups, bonus_cats, _, _, excluded_groups, excluded_cats = _get_breakdown_config()
    _INFRA_GROUPS = {"Internal Master Category", "Credit Card Payments"}

    def _is_bonus(name: str, group: str | None) -> bool:
        return _is_bonus_funded(name, group, bonus_groups, bonus_cats)

    def _is_infra(group: str | None) -> bool:
        g = (group or "").lower()
        return any(ig.lower() in g for ig in _INFRA_GROUPS)

    def _should_skip(name: str, group: str | None) -> bool:
        """Return True if a category must be excluded from all tiers."""
        g = group or ""
        return _is_excluded(g, name, excluded_groups, excluded_cats) or _is_infra(group) or _is_bonus(name, group)

    tier1 = [i for i in _get_tier1_overspent(conn, month) if not _should_skip(i["name"], i["group"])]

    # Track IDs seen in earlier tiers to prevent cross-tier duplicates.
    seen_ids: set[str] = {i["id"] for i in tier1}

    tier2_raw = _get_tier2_due_soon(conn, month)
    tier2 = [i for i in tier2_raw if not _should_skip(i["name"], i["group"]) and i["id"] not in seen_ids]
    seen_ids.update(i["id"] for i in tier2)

    # Tier 3: Fixed recurring bills (loans, insurance, subscriptions) - must be
    # funded BEFORE daily spending bridge. A car payment or insurance autopay
    # cannot go unfunded because Groceries got the money first.
    # Only REGULAR-pot categories; BONUS-pot categories are funded at bonus time.
    # Exception: CC payment categories with an explicit payoff goal (amount_needed > 0)
    # are NOT infra - they represent a deliberate monthly payment obligation. refs #208
    def _should_skip_tier3(item: dict) -> bool:
        name = item["name"]
        group = item["group"]
        # Bonus-funded categories are always excluded regardless of group.
        if _is_bonus(name, group):
            return True
        # CC payment categories with an explicit payoff goal (amount_needed > 0)
        # represent a deliberate monthly payment obligation. Allow them into Tier 3
        # even though their group ("Credit Card Payments") is normally infra-excluded.
        # This must run before _is_excluded because Credit Card Payments is in the
        # default excluded groups, and the goal exception should override that. refs #208
        is_cc_payoff = _is_infra(group) and item.get("goal_type") and item.get("amount_needed", 0) > 0
        if is_cc_payoff:
            return False
        # Explicitly excluded categories (env-configured) are excluded.
        if _is_excluded(group, name, excluded_groups, excluded_cats):
            return True
        return _is_infra(group)

    tier3 = [i for i in _get_tier4_monthly_bills(conn, month) if not _should_skip_tier3(i) and i["id"] not in seen_ids]

    # Bridge top-up for Tier 3 daily-spend categories: if a category has both a
    # monthly goal shortfall (placing it in Tier 3) and a balance that would still
    # be below the bridge target after goal funding, augment the Tier 3 item's
    # amount_needed to cover the bridge delta. This keeps one consolidated line per
    # category rather than splitting goal-funding and bridge across two tiers. refs #209
    if days_until_income > 0:
        daily_map = _get_daily_spending_categories(conn)
        for item in tier3:
            daily_rate = daily_map.get(item["name"])
            if daily_rate is None or daily_rate <= 0:
                continue
            bridge_target = daily_rate * days_until_income
            # Balance after funding the goal shortfall
            post_fund_balance = item["balance"] + item["amount_needed"]
            if post_fund_balance < bridge_target:
                bridge_delta = bridge_target - post_fund_balance
                if bridge_delta > 0.50:
                    item["amount_needed"] += bridge_delta
                    item["new_budgeted"] = item["budgeted"] + item["amount_needed"]
                    goal_reason = item["reason"]
                    item["reason"] = f"{goal_reason} + bridge ~${daily_rate:.0f}/day x {days_until_income}d"

    seen_ids.update(i["id"] for i in tier3)

    # Tier 4: Daily spending bridge - only funded after all payment obligations
    # are covered. It is acceptable to leave this at a partial bridge if RTA runs
    # short; it is NOT acceptable to leave a fixed bill unpaid.
    tier4 = [
        i
        for i in _get_tier3_bridge(conn, month, days_until_income)
        if not _should_skip(i["name"], i["group"]) and i["id"] not in seen_ids
    ]
    seen_ids.update(i["id"] for i in tier4)

    tier5 = [
        i
        for i in _get_tier5_remaining_goals(conn, month)
        if not _should_skip(i["name"], i["group"]) and i["id"] not in seen_ids
    ]

    return {
        "month": month,
        "rta": rta,
        "next_income": next_income,
        "tiers": {
            1: {"label": "Cover Overspent", "items": tier1},
            2: {"label": "Due Soon", "items": tier2},
            3: {"label": "Monthly Bills", "items": tier3},
            4: {"label": "Bridge Daily Spending", "items": tier4},
            5: {"label": "Remaining Goals", "items": tier5},
        },
    }


def _format_plan(plan: dict) -> str:
    """Format a funding plan for display. Returns multi-line string."""
    lines = []
    month_label = plan["month"][:7]
    lines.append(f"Paycheck Funding Plan ({month_label})")
    lines.append("=" * 64)

    rta = plan["rta"]
    lines.append(f"RTA Available: ${rta:,.2f}")

    next_income = plan["next_income"]
    if next_income:
        d = next_income["date"]
        amt = next_income["amount"]
        days = next_income["days_away"]
        label = "Paycheck + Bonus" if next_income["is_bonus"] else "~Paycheck"
        days_str = f"{days} day{'s' if days != 1 else ''}" if days > 0 else "today"
        lines.append(f"Next income: ~${amt:,.0f} on {d} ({days_str}) [{label}]")
    else:
        lines.append("Next income: unknown (no recurring income detected)")

    lines.append("")

    running = 0.0
    exhausted = False

    for tier_num in range(1, 6):
        tier = plan["tiers"][tier_num]
        items = tier["items"]
        tier_total = sum(i["amount_needed"] for i in items)

        if tier_num == 4 and next_income:
            d = next_income["date"]
            days = next_income["days_away"]
            days_str = f"to {d.month}/{d.day}" if days > 0 else "today"
            tier_label = f"TIER {tier_num} - {tier['label']} {days_str} (${tier_total:,.2f}):"
        else:
            tier_label = f"TIER {tier_num} - {tier['label']} (${tier_total:,.2f}):"

        if not items:
            lines.append(tier_label)
            lines.append("  (nothing)")
            lines.append("")
            continue

        lines.append(tier_label)

        for item in items:
            needed = item["amount_needed"]
            prev_running = running
            running += needed

            if not exhausted and prev_running < rta <= running:
                # Mark the point where RTA is exhausted mid-tier
                lines.append(f"  {'':40}  running: ${prev_running:>9,.2f}")
                lines.append(f"  >>> RTA exhausted at ${rta:,.2f} - remaining tiers need next paycheck")
                exhausted = True

            name_col = item["name"][:38]
            reason = item.get("reason", "")
            if reason:
                display_name = f"{name_col} ({reason})"
            else:
                display_name = name_col

            # Truncate combined display to 50 chars
            display_name = display_name[:50]

            if exhausted and running > rta:
                suffix = "  NEEDS PAYCHECK"
            else:
                suffix = f"  running: ${running:>9,.2f}"

            lines.append(f"  {display_name:<52} ${needed:>9,.2f}{suffix}")

        if not exhausted and running > rta:
            lines.append(f"  >>> RTA exhausted at ${rta:,.2f} - remaining tiers need next paycheck")
            exhausted = True

        lines.append("")

    # Summary
    # Sum of protected tiers 1-3 (overspent + due soon + fixed bills)
    protected_tiers = sum(sum(i["amount_needed"] for i in plan["tiers"][t]["items"]) for t in [1, 2, 3])
    lines.append("Summary:")
    lines.append(f"  Can fund now (RTA ${rta:,.2f}):  ${min(rta, running):,.0f}")
    lines.append(f"  Protected tiers (1-3):          ${protected_tiers:,.0f}")
    needs_paycheck = max(0, running - rta)
    if needs_paycheck > 0:
        lines.append(f"  Needs next paycheck:            ${needs_paycheck:,.0f}")

    return "\n".join(lines)


def run_paycheck_funding(month: str | None = None) -> None:
    """Generate a prioritized funding plan based on available RTA."""
    month = _month_str(month)
    conn = get_connection()
    try:
        init_db(conn)
        plan = generate_funding_plan(conn, month)
        print(_format_plan(plan))
    finally:
        conn.close()


def run_paycheck_funding_apply(month: str | None = None, through_tier: int = 2) -> None:
    """Execute the paycheck funding plan through a specified tier. Writes to YNAB."""
    from ..client import YNABClient
    from ..config import require_credentials

    month = _month_str(month)
    conn = get_connection()
    try:
        init_db(conn)
        plan = generate_funding_plan(conn, month)

        rta = plan["rta"]
        month_label = plan["month"][:7]

        if through_tier < 1 or through_tier > 5:
            print(f"Invalid tier {through_tier}. Must be 1-5.")
            return

        print(f"Paycheck Funding - Apply Through Tier {through_tier} ({month_label})")
        print("=" * 64)
        print(f"RTA Available: ${rta:,.2f}")
        print()

        # Collect items across tiers 1..through_tier
        all_items: list[dict] = []
        for t in range(1, through_tier + 1):
            all_items.extend(plan["tiers"][t]["items"])

        if not all_items:
            print(f"Nothing to fund through tier {through_tier}.")
            return

        token, plan_id = require_credentials()
        client = YNABClient(token, plan_id)

        remaining_rta = rta
        funded = []
        skipped = []

        for item in all_items:
            needed = item["amount_needed"]
            if remaining_rta <= 0:
                skipped.append(item)
                continue

            # Cap at remaining RTA
            fund_amount = min(needed, remaining_rta)
            new_budgeted = item["budgeted"] + fund_amount
            old_budgeted = item["budgeted"]

            try:
                result = client.update_category_budget(plan["month"], item["id"], _dollars_to_milliunits(new_budgeted))
                _log_funding_change(
                    conn,
                    item["id"],
                    item["name"],
                    item.get("group"),
                    plan["month"],
                    old_budgeted,
                    new_budgeted,
                    "paycheck-funding",
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
                            (updated.get("budgeted") or _dollars_to_milliunits(new_budgeted)) / 1000,
                            (updated.get("balance") or 0) / 1000,
                            (updated.get("goal_under_funded") or 0) / 1000,
                            item["id"],
                            plan["month"],
                        ),
                    )

                partial = " (partial)" if fund_amount < needed else ""
                print(f"  Funded: {item['name']:<38} +${fund_amount:>8,.2f}{partial}")
                funded.append({**item, "funded_amount": fund_amount})
                remaining_rta -= fund_amount

            except Exception as e:
                logger.error(f"Failed to fund {item['name']}: {e}")
                skipped.append(item)

        conn.commit()

        total_funded = sum(f["funded_amount"] for f in funded)
        print()
        print(f"Funded {len(funded)} categories, ${total_funded:,.2f} total.")
        if skipped:
            print(f"Skipped {len(skipped)} (RTA exhausted or API error):")
            for s in skipped[:5]:
                print(f"  - {s['name']} (needed ${s['amount_needed']:,.2f})")
            if len(skipped) > 5:
                print(f"  ... and {len(skipped) - 5} more")

    finally:
        conn.close()
