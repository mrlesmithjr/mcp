"""Two-pot compliance report: structural backwards funding detection per bonus month."""

from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime, timedelta

from ..config import load_env
from ..db import get_connection, init_db
from ..reports.paycheck_breakdown import (
    _get_breakdown_config,
    _is_bonus_funded,
    _is_excluded,
)


def _prior_month(month_str: str) -> str:
    """Given 'YYYY-MM-01', return the prior month as 'YYYY-MM-01'."""
    d = date.fromisoformat(month_str)
    prior = d - timedelta(days=1)
    return prior.strftime("%Y-%m-01")


def _get_paychecks(conn: sqlite3.Connection, months: int = 12) -> list[dict]:
    """Fetch income transactions using the same payee/category logic as income.py."""
    payee_config = os.environ.get("YNAB_PAYCHECK_PAYEES", "").strip()
    if payee_config:
        payee_names = [p.strip() for p in payee_config.split(",") if p.strip()]
        clauses = " OR ".join(["t.payee_name LIKE ?"] * len(payee_names))
        params: list = [f"%{p}%" for p in payee_names]
        rows = conn.execute(
            f"""
            SELECT t.date, t.payee_name, t.amount
            FROM transactions t
            JOIN accounts a ON a.id = t.account_id
            WHERE t.deleted = 0 AND a.on_budget = 1 AND t.amount > 0
              AND (t.category_name LIKE 'Income:%' OR t.category_name LIKE 'Inflow:%')
              AND ({clauses})
              AND t.date >= date('now', ? || ' months')
            ORDER BY t.date DESC
            """,
            (*params, f"-{months}"),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT t.date, t.payee_name, t.amount
            FROM transactions t
            JOIN accounts a ON a.id = t.account_id
            WHERE t.deleted = 0 AND a.on_budget = 1
              AND t.category_name = 'Income: Paychecks'
              AND t.date >= date('now', ? || ' months')
            ORDER BY t.date DESC
            """,
            (f"-{months}",),
        ).fetchall()
    return [dict(r) for r in rows]


def _holding_balance(conn: sqlite3.Connection, month_str: str) -> float | None:
    load_env()
    name = os.environ.get("YNAB_HOLDING_CATEGORY", "Holding: Next Month").strip() or "Holding: Next Month"
    row = conn.execute(
        "SELECT balance FROM budget_categories WHERE budget_month = ? AND name = ?",
        (month_str, name),
    ).fetchone()
    return round(row["balance"], 2) if row else None


def run_two_pot_report(months: int = 3, month: str | None = None) -> None:
    """Print two-pot structural compliance for bonus months in the last N months."""
    load_env()
    bonus_groups, bonus_cats, regular_pay, checks, excluded, excluded_cats = _get_breakdown_config()
    bonus_threshold = float(os.environ.get("YNAB_BONUS_THRESHOLD", "") or 0) or None

    if not regular_pay:
        print("Error: YNAB_REGULAR_PAY is not configured.")
        print("Set it in ~/.config/ynab-tools/config.json or as an env var.")
        return

    if not bonus_threshold:
        print("Error: YNAB_BONUS_THRESHOLD is not configured.")
        print("Set it in ~/.config/ynab-tools/config.json or as an env var.")
        return

    monthly_regular = regular_pay * checks

    conn = get_connection()
    try:
        init_db(conn)

        # When a specific month is given, look back far enough to find it
        if month:
            from datetime import date as _date

            target_d = _date.fromisoformat(month[:7] + "-01")
            today = _date.today()
            months_ago = (today.year - target_d.year) * 12 + (today.month - target_d.month) + 1
            lookback = max(months, months_ago)
        else:
            lookback = max(months, 24)
        paychecks = _get_paychecks(conn, months=lookback)

        # Identify bonus months
        bonus_months: dict[str, dict] = {}
        for p in paychecks:
            if p["amount"] > bonus_threshold:
                m_key = p["date"][:7]
                if m_key not in bonus_months:
                    bonus_months[m_key] = {
                        "month": m_key,
                        "bonus_paycheck_date": p["date"],
                        "bonus_paycheck_amount": round(p["amount"], 2),
                        "bonus_portion": round(p["amount"] - regular_pay, 2),
                    }

        if month:
            # Filter to the requested month only
            target = month[:7]
            if target not in bonus_months:
                print(f"No bonus paycheck found in {target}.")
                return
            keys = [target]
        else:
            # Most recent N bonus months
            keys = sorted(bonus_months.keys(), reverse=True)[:months]

        if not keys:
            print(f"No bonus months found in the last {months} months.")
            return

        print("Two-Pot Compliance Report")
        print("=" * 72)
        print(f"Regular pay: ${regular_pay:,.2f} x {checks} checks = ${monthly_regular:,.2f}/mo")
        print(f"Bonus threshold: ${bonus_threshold:,.2f}")
        print()

        for month_key in keys:
            bm = bonus_months[month_key]
            month_str = month_key + "-01"

            # Use naive UTC midnight for comparison against moved_at (which stores
            # 'Z'-suffixed UTC). isoformat() with tzinfo produces '+00:00' suffix;
            # strftime produces a bare naive string that compares correctly.
            bonus_dt = datetime.strptime(bm["bonus_paycheck_date"], "%Y-%m-%d")
            bonus_date_ts = bonus_dt.strftime("%Y-%m-%dT%H:%M:%S")

            print(f"Month: {month_key}")
            print(
                f"  Bonus paycheck: {bm['bonus_paycheck_date']}  "
                f"${bm['bonus_paycheck_amount']:,.2f}  "
                f"(bonus portion: ${bm['bonus_portion']:,.2f})"
            )
            print()

            # Pull regular-pot categories and compute structural metric
            target_rows = conn.execute(
                """
                SELECT name, category_group_name, budgeted
                FROM budget_categories
                WHERE budget_month = ? AND deleted = 0
                """,
                (month_str,),
            ).fetchall()

            regular_pot_rows = [
                r
                for r in target_rows
                if not _is_excluded(r["category_group_name"] or "", r["name"], excluded, excluded_cats)
                and not _is_bonus_funded(r["name"], r["category_group_name"], bonus_groups, bonus_cats)
            ]
            regular_pot_budgeted = sum((r["budgeted"] or 0.0) for r in regular_pot_rows)
            structural_backwards = round(regular_pot_budgeted - monthly_regular, 2)

            # TBB to determine exactness
            tbb_row = conn.execute("SELECT to_be_budgeted FROM budget_months WHERE month = ?", (month_str,)).fetchone()
            tbb = round(tbb_row["to_be_budgeted"], 2) if tbb_row else None
            structural_is_exact = tbb == 0.0
            if tbb is None:
                exact_label = "unknown (budget_months row missing)"
            elif structural_is_exact:
                exact_label = "exact"
            else:
                exact_label = f"lower bound (TBB=${tbb:,.2f})"

            print("  Structural compliance (all funding sources):")
            print(f"    Regular-pot budgeted:   ${regular_pot_budgeted:>12,.2f}")
            print(f"    Regular pay total:      ${monthly_regular:>12,.2f}")
            if structural_backwards > 0:
                print(
                    f"    Structural backwards:   ${structural_backwards:>12,.2f}"
                    f"  [{exact_label}]  *** over-allocated ***"
                )
            elif structural_backwards < 0:
                print(
                    f"    Structural surplus:     ${abs(structural_backwards):>12,.2f}"
                    f"  [{exact_label}]  (under-allocated)"
                )
            else:
                print(f"    Structural backwards:   ${'0.00':>12}  [{exact_label}]  (balanced)")
            print()

            # Holding delta
            holding_end = _holding_balance(conn, month_str)
            holding_start = _holding_balance(conn, _prior_month(month_str))
            if holding_end is not None or holding_start is not None:
                hd = round((holding_end or 0.0) - (holding_start or 0.0), 2)
                hd_label = f"${hd:+,.2f}"
                if hd < 0:
                    hd_label += "  WARNING: Holding drew down in a bonus month"
                print(f"  Holding: Next Month delta: {hd_label}")
                print(
                    f"    Start: ${holding_start:,.2f}  End: ${holding_end:,.2f}"
                    if holding_start is not None and holding_end is not None
                    else f"    Start: {'N/A' if holding_start is None else f'${holding_start:,.2f}'}  "
                    f"End: {'N/A' if holding_end is None else f'${holding_end:,.2f}'}"
                )
                print()

            # Top 5 regular-pot categories by budgeted amount (likely culprits)
            top5 = sorted(regular_pot_rows, key=lambda r: -(r["budgeted"] or 0.0))[:5]
            if top5:
                print("  Top regular-pot categories by budgeted amount:")
                for r in top5:
                    grp = r["category_group_name"] or ""
                    grp_display = f"{grp}: " if grp else ""
                    print(f"    {grp_display}{r['name']:<40} ${r['budgeted'] or 0.0:,.2f}")
                print()

            # Workflow gap (all budget moves via money_movements API)
            entries = conn.execute(
                """
                SELECT to_category_name AS category_name,
                       bc.category_group_name AS category_group,
                       amount AS delta
                FROM money_movements mm
                LEFT JOIN budget_categories bc
                       ON bc.id = mm.to_category_id
                      AND bc.budget_month = mm.month
                WHERE mm.month = ? AND mm.moved_at >= ? AND mm.deleted = 0
                  AND mm.to_category_id IS NOT NULL
                ORDER BY amount DESC
                """,
                (month_str, bonus_date_ts),
            ).fetchall()

            backwards_entries = [
                e
                for e in entries
                if not _is_excluded(e["category_group"] or "", e["category_name"], excluded, excluded_cats)
                and not _is_bonus_funded(e["category_name"], e["category_group"], bonus_groups, bonus_cats)
            ]

            # Aggregate by category
            backwards_map: dict[str, float] = {}
            for e in backwards_entries:
                backwards_map[e["category_name"]] = round(backwards_map.get(e["category_name"], 0.0) + e["delta"], 2)

            workflow_gap = round(sum(backwards_map.values()), 2)
            date_str = bm["bonus_paycheck_date"]
            print(f"  Workflow gap (all sources, after {date_str}): ${workflow_gap:,.2f}")
            if backwards_map:
                print("  Note: this counts gross adds from money_movements and may double-count top-ups.")
                for cat, delta in sorted(backwards_map.items(), key=lambda x: -x[1])[:5]:
                    print(f"    {cat:<44} +${delta:,.2f}")
            else:
                print("  No ynab-tools regular-pot adds recorded after the bonus paycheck.")
            print()
            print("-" * 72)
            print()

    finally:
        conn.close()
