"""Paycheck forecast: auto-detect income sources, frequency, and project forward."""

import logging
import os
import sqlite3
import statistics
from collections import Counter
from datetime import date, datetime, timedelta

from ..config import load_env
from ..db import get_connection, init_db

logger = logging.getLogger(__name__)

# Frequency detection thresholds (days between paychecks)
_FREQ_RANGES = {
    "weekly": (5, 9),
    "biweekly": (12, 16),
    "semimonthly": (13, 17),
    "monthly": (27, 34),
}


def _get_paycheck_config() -> list[str]:
    """Load optional income payee overrides from env.

    Checks YNAB_INCOME_PAYEES first (paycheck forecast specific),
    then falls back to YNAB_PAYCHECK_PAYEES (shared with income report).

    Set in .env:
      YNAB_PAYCHECK_PAYEES=Employer Name,Other Employer  # comma-separated
    """
    load_env()
    raw = os.environ.get("YNAB_INCOME_PAYEES", "").strip()
    if not raw:
        raw = os.environ.get("YNAB_PAYCHECK_PAYEES", "").strip()
    if raw:
        return [p.strip() for p in raw.split(",") if p.strip()]
    return []


def _get_recurring_inflows(conn: sqlite3.Connection, months: int = 12) -> list[dict]:
    """Get all positive inflows excluding transfers."""
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT date, payee_name, amount, memo, account_name
            FROM transactions
            WHERE deleted = 0
              AND amount > 0
              AND transfer_account_id IS NULL
              AND date >= date('now', ? || ' months')
            ORDER BY date DESC
        """,
            (f"-{months}",),
        ).fetchall()
    ]


def _detect_income_sources(inflows: list[dict], override_payees: list[str]) -> list[dict]:
    """Identify recurring income payees from transaction patterns.

    Returns a list of detected sources sorted by total volume, each containing:
      payee, count, total, transactions, median_amount, intervals, frequency
    """
    # Group by payee
    by_payee: dict[str, list[dict]] = {}
    for txn in inflows:
        payee = txn["payee_name"] or ""
        if not payee:
            continue
        by_payee.setdefault(payee, []).append(txn)

    # If overrides specified, filter to those payees (partial match)
    if override_payees:
        filtered: dict[str, list[dict]] = {}
        for override in override_payees:
            needle = override.lower()
            for payee, txns in by_payee.items():
                if needle in payee.lower():
                    filtered[payee] = txns
        by_payee = filtered

    sources = []
    for payee, txns in by_payee.items():
        if len(txns) < 3:
            continue

        total = sum(t["amount"] for t in txns)
        amounts = sorted(t["amount"] for t in txns)
        median_amt = statistics.median(amounts)

        # Compute regular pay amount excluding outlier spikes (bonuses).
        # Use amounts within 1.5× the median - this gives the true base pay.
        regular_amounts = [a for a in amounts if a <= median_amt * 1.5]
        regular_pay_amt = statistics.median(regular_amounts) if regular_amounts else median_amt

        # Calculate intervals between consecutive transactions
        dates = sorted(datetime.strptime(t["date"], "%Y-%m-%d").date() for t in txns)
        intervals = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]

        if not intervals:
            continue

        median_interval = statistics.median(intervals)

        # Detect frequency from median interval
        frequency = _classify_frequency(median_interval, intervals)
        if not frequency:
            continue

        # Score: regularity (low interval variance) × volume
        interval_cv = (
            statistics.stdev(intervals) / median_interval if len(intervals) > 1 and median_interval > 0 else 999
        )

        sources.append(
            {
                "payee": payee,
                "count": len(txns),
                "total": total,
                "transactions": sorted(txns, key=lambda t: t["date"], reverse=True),
                "median_amount": median_amt,
                "regular_amount": regular_pay_amt,
                "intervals": intervals,
                "median_interval": median_interval,
                "frequency": frequency,
                "interval_cv": interval_cv,
            }
        )

    # Sort by regularity first (low CV), then by total volume
    sources.sort(key=lambda s: (s["interval_cv"], -s["total"]))
    return sources


def _classify_frequency(median_interval: float, intervals: list[int]) -> str | None:
    """Classify pay frequency from interval data.

    Distinguishes biweekly from semimonthly by checking interval consistency:
    - Biweekly: intervals tightly clustered around 14 days
    - Semimonthly: intervals alternate between ~15 and ~16 days (variable)
    """
    if 5 <= median_interval <= 9:
        return "weekly"
    elif 10 <= median_interval <= 18:
        # Distinguish biweekly from semimonthly
        if len(intervals) >= 4:
            stdev = statistics.stdev(intervals)
            # Biweekly: very consistent (~14 days, stdev < 2)
            # Semimonthly: more variable (13-17 days, stdev >= 2)
            if stdev < 2 and 12 <= median_interval <= 16:
                return "biweekly"
            elif 13 <= median_interval <= 17:
                return "semimonthly"
        # Fallback: assume biweekly if close to 14
        if 12 <= median_interval <= 16:
            return "biweekly"
        return "semimonthly"
    elif 27 <= median_interval <= 34:
        return "monthly"
    return None


def _detect_bonus_pattern(source: dict) -> dict | None:
    """Detect bonus months from amount spikes in a source's transactions.

    Returns dict with bonus_months, bonus_amounts, cycle, next_expected, avg_bonus
    or None if no pattern found.
    """
    regular_amt = source["regular_amount"]
    # Bonus threshold: 50% above regular pay (generous to catch varied bonus amounts)
    threshold = regular_amt * 1.5

    all_txns = source["transactions"]
    bonus_txns = [t for t in all_txns if t["amount"] > threshold]
    if len(bonus_txns) < 2:
        return None

    bonus_months = sorted(set(t["date"][:7] for t in bonus_txns))
    bonus_amounts = [t["amount"] - regular_amt for t in bonus_txns]
    # Use median rather than mean -- annual one-time items (e.g. Profit Share) inflate
    # one bonus check per year and would permanently skew the mean upward.
    avg_bonus = statistics.median(bonus_amounts)

    # Detect which check position in the month gets the bonus (1st, 2nd, etc.)
    # by looking at all checks in each bonus month and finding the bonus check's position.
    bonus_positions = []
    for bt in bonus_txns:
        month_prefix = bt["date"][:7]
        month_txns = sorted(t["date"] for t in all_txns if t["date"].startswith(month_prefix))
        if bt["date"] in month_txns:
            position = month_txns.index(bt["date"]) + 1  # 1-based
            bonus_positions.append(position)

    # Most common position (e.g., 2 = "2nd check of the month")
    bonus_check_position = Counter(bonus_positions).most_common(1)[0][0] if bonus_positions else None

    # Detect quarterly pattern from month numbers
    month_nums = [int(m[5:7]) for m in bonus_months]
    quarters = [((n - 1) % 3) + 1 for n in month_nums]
    common_pos = Counter(quarters).most_common(1)[0][0]

    cycle = None
    if len(set(quarters)) <= 2 and len(bonus_months) >= 2:
        month_names = {
            1: "Jan/Apr/Jul/Oct",
            2: "Feb/May/Aug/Nov",
            3: "Mar/Jun/Sep/Dec",
        }
        cycle = month_names.get(common_pos, "")

    # Predict next bonus month
    next_expected = None
    if cycle:
        today = date.today()
        for offset in range(1, 13):
            future_month = (today.month - 1 + offset) % 12 + 1
            future_pos = ((future_month - 1) % 3) + 1
            if future_pos == common_pos:
                future_year = today.year + ((today.month - 1 + offset) // 12)
                next_expected = f"{future_year}-{future_month:02d}"
                break

    return {
        "bonus_months": bonus_months,
        "bonus_amounts": bonus_amounts,
        "avg_bonus": avg_bonus,
        "cycle": cycle,
        "next_expected": next_expected,
        "threshold": threshold,
        "check_position": bonus_check_position,
    }


def _project_next_dates(source: dict, count: int = 5) -> list[dict]:
    """Project upcoming pay dates based on detected frequency.

    Returns list of dicts with date, amount, is_bonus.
    """
    txns = source["transactions"]
    if not txns:
        return []

    last_date = datetime.strptime(txns[0]["date"], "%Y-%m-%d").date()
    regular_amt = source["regular_amount"]
    frequency = source["frequency"]

    # Determine interval for projection
    if frequency == "weekly":
        interval = timedelta(days=7)
    elif frequency == "biweekly":
        interval = timedelta(days=14)
    elif frequency == "semimonthly":
        # Approximate: use 1st and 15th or actual pattern
        interval = timedelta(days=15)
    elif frequency == "monthly":
        interval = timedelta(days=30)
    else:
        return []

    bonus_info = _detect_bonus_pattern(source)
    bonus_cycle_months: set[int] = set()
    if bonus_info and bonus_info["cycle"]:
        # Parse cycle months from the pattern
        month_map = {
            "Jan/Apr/Jul/Oct": {1, 4, 7, 10},
            "Feb/May/Aug/Nov": {2, 5, 8, 11},
            "Mar/Jun/Sep/Dec": {3, 6, 9, 12},
        }
        bonus_cycle_months = month_map.get(bonus_info["cycle"], set())

    # Track which check number we're on in each month
    bonus_check_pos = bonus_info.get("check_position") if bonus_info else None
    month_check_counter: dict[str, int] = {}

    # Count actual checks already in each month (so projections continue the count)
    for t in txns:
        mk = t["date"][:7]
        month_check_counter[mk] = month_check_counter.get(mk, 0) + 1

    projections = []
    current = last_date
    today = date.today()

    for _ in range(count * 3):  # Generate extra, then filter to future
        current = current + interval
        if current <= today:
            continue

        month_key = current.strftime("%Y-%m")
        month_check_counter[month_key] = month_check_counter.get(month_key, 0) + 1
        check_num = month_check_counter[month_key]

        is_bonus = current.month in bonus_cycle_months and (bonus_check_pos is None or check_num == bonus_check_pos)
        amount = regular_amt
        if is_bonus and bonus_info:
            amount = regular_amt + bonus_info["avg_bonus"]

        projections.append(
            {
                "date": current,
                "amount": amount,
                "is_bonus": is_bonus,
            }
        )
        if len(projections) >= count:
            break

    return projections


def _project_month_income(source: dict, target_month: date | None = None) -> dict:
    """Project total income for a given month.

    Returns dict with month, check_count, regular_total, bonus_total, total.
    """
    if target_month is None:
        today = date.today()
        target_month = today.replace(day=1)

    month_str = target_month.strftime("%Y-%m")
    projections = _project_next_dates(source, count=20)

    # Also include actual transactions already in the target month
    actual_txns = [t for t in source["transactions"] if t["date"].startswith(month_str)]

    regular_amt = source["regular_amount"]
    bonus_info = _detect_bonus_pattern(source)

    actual_regular = 0.0
    actual_bonus = 0.0
    actual_count = 0
    for t in actual_txns:
        actual_count += 1
        if bonus_info and t["amount"] > bonus_info["threshold"]:
            actual_regular += regular_amt
            actual_bonus += t["amount"] - regular_amt
        else:
            actual_regular += t["amount"]

    projected_regular = 0.0
    projected_bonus = 0.0
    projected_count = 0
    for p in projections:
        if p["date"].strftime("%Y-%m") == month_str:
            projected_count += 1
            if p["is_bonus"]:
                projected_regular += regular_amt
                projected_bonus += p["amount"] - regular_amt
            else:
                projected_regular += p["amount"]

    return {
        "month": month_str,
        "actual_count": actual_count,
        "projected_count": projected_count,
        "total_checks": actual_count + projected_count,
        "regular": actual_regular + projected_regular,
        "bonus": actual_bonus + projected_bonus,
        "total": actual_regular + projected_regular + actual_bonus + projected_bonus,
    }


def _get_monthly_budget(conn: sqlite3.Connection) -> float | None:
    """Get the current month's total budgeted amount."""
    row = conn.execute(
        """
        SELECT budgeted FROM budget_months
        WHERE month = strftime('%Y-%m-01', 'now')
    """
    ).fetchone()
    return dict(row)["budgeted"] if row else None


def _freq_label(frequency: str) -> str:
    """Human-readable frequency label."""
    return {
        "weekly": "weekly",
        "biweekly": "bi-weekly",
        "semimonthly": "semi-monthly",
        "monthly": "monthly",
    }.get(frequency, frequency)


def _checks_per_year(frequency: str) -> int:
    """Expected number of paychecks per year."""
    return {
        "weekly": 52,
        "biweekly": 26,
        "semimonthly": 24,
        "monthly": 12,
    }.get(frequency, 0)


def run_paycheck(months: int = 12) -> None:
    """Show paycheck forecast: detected sources, frequency, and income projection."""
    override_payees = _get_paycheck_config()

    conn = get_connection()
    try:
        init_db(conn)

        inflows = _get_recurring_inflows(conn, months)
        if not inflows:
            print("No inflow transactions found. Run 'ynab sync' first.")
            return

        sources = _detect_income_sources(inflows, override_payees)
        if not sources:
            print("No recurring income sources detected.")
            if not override_payees:
                print("Tip: Set YNAB_INCOME_PAYEES in .env to specify your employer payee name.")
            return

        # Use the top source as primary (most regular + highest volume)
        primary = sources[0]

        print("Paycheck Forecast")
        print("=" * 72)

        # ── Detection summary ──
        freq = _freq_label(primary["frequency"])
        cpy = _checks_per_year(primary["frequency"])
        print(
            f"\n  Source:    {primary['payee']}"
            f"\n  Cadence:  {freq}, ~${primary['regular_amount']:,.2f}/check"
            f" ({primary['count']} in last {months} months)"
        )
        if cpy:
            annual_regular = primary["regular_amount"] * cpy
            print(f"  Annual:   ~${annual_regular:,.0f} regular pay ({cpy} checks/yr)")

        bonus_info = _detect_bonus_pattern(primary)
        if bonus_info:
            cycle = bonus_info["cycle"] or "irregular"
            pos = bonus_info.get("check_position")
            pos_label = ""
            if pos:
                ordinal = {1: "1st", 2: "2nd", 3: "3rd"}.get(pos, f"{pos}th")
                pos_label = f", on {ordinal} check of month"
            print(
                f"  Bonuses:  {cycle} cycle, ~${bonus_info['avg_bonus']:,.0f}/bonus"
                f" ({len(bonus_info['bonus_months'])} detected{pos_label})"
            )
            if bonus_info["next_expected"]:
                print(f"  Next bonus: {bonus_info['next_expected']}")

        # ── Upcoming paychecks ──
        projections = _project_next_dates(primary, count=5)
        if projections:
            print("\nUpcoming:")
            print(f"  {'Date':<12} {'Type':<20} {'Amount':>10}")
            print("  " + "-" * 44)
            for p in projections:
                label = "Paycheck + Bonus" if p["is_bonus"] else "Paycheck"
                print(f"  {p['date'].strftime('%b %d'):>6}      {label:<20} ${p['amount']:>9,.2f}")

        # ── Current + next month projection ──
        today = date.today()
        current_month = today.replace(day=1)
        if today.month == 12:
            next_month = current_month.replace(year=today.year + 1, month=1)
        else:
            next_month = current_month.replace(month=today.month + 1)

        budget_need = _get_monthly_budget(conn)

        print("\nMonthly Projection:")
        print(f"  {'Month':<10} {'Checks':>6} {'Regular':>10} {'Bonus':>10} {'Total':>10}", end="")
        if budget_need is not None:
            print(f" {'Surplus':>10}", end="")
        print()
        print("  " + "-" * (56 if budget_need is None else 68))

        for month in [current_month, next_month]:
            proj = _project_month_income(primary, month)
            check_str = str(proj["total_checks"])
            if proj["actual_count"] > 0 and proj["projected_count"] > 0:
                check_str = f"{proj['actual_count']}+{proj['projected_count']}"
            bonus_str = f"${proj['bonus']:>9,.2f}" if proj["bonus"] else "         -"
            line = f"  {proj['month']:<10} {check_str:>6} ${proj['regular']:>9,.2f} {bonus_str} ${proj['total']:>9,.2f}"
            if budget_need is not None:
                surplus = proj["total"] - budget_need
                sign = "+" if surplus >= 0 else ""
                line += f" {sign}${surplus:>8,.2f}"
            print(line)

        # ── Last few paychecks ──
        recent = primary["transactions"][:5]
        if recent:
            print("\nRecent Paychecks:")
            for t in recent:
                flag = " *" if bonus_info and t["amount"] > bonus_info["threshold"] else ""
                print(f"  {t['date']}  ${t['amount']:>9,.2f}{flag}")
            if bonus_info:
                print("  (* = includes bonus)")

        # ── Additional sources (filter out low-value refund patterns) ──
        other_sources = [s for s in sources[1:] if s["regular_amount"] >= 50]
        if other_sources:
            print("\nOther Recurring Income Sources:")
            for s in other_sources:
                freq = _freq_label(s["frequency"])
                print(f"  {s['payee']:<30} {freq:<14} ~${s['regular_amount']:>9,.2f}  ({s['count']} txns)")

    finally:
        conn.close()
