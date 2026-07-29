"""Subscriptions report: recurring charges in subscription categories."""

from __future__ import annotations

from datetime import date

from ..dashboard.api.subscriptions import _build_subscriptions
from ..db import get_connection, init_db


def _short_date(iso: str | None) -> str:
    if not iso:
        return "---"
    d = date.fromisoformat(iso[:10])
    return f"{d.strftime('%b')} {d.day}"


def run_subscriptions() -> None:
    """Print a formatted subscriptions summary."""
    conn = get_connection()
    try:
        init_db(conn)
        data = _build_subscriptions(conn)
    finally:
        conn.close()

    subs = data["subscriptions"]
    monthly_total = data["monthly_total"]
    annual_total = data["annual_total"]
    active_count = data["active_count"]
    check_count = data["check_count"]
    yoy_change = data["yoy_change"]

    print("Subscriptions")
    print("=" * 70)
    print(f"Monthly cost:  ${monthly_total:,.2f}")
    print(f"Annual cost:   ${annual_total:,.2f}")
    print(f"Active:        {active_count}")
    if check_count:
        print(f"Needs check:   {check_count}")
    if yoy_change is not None:
        sign = "+" if yoy_change >= 0 else ""
        print(f"YoY change:    {sign}${yoy_change:.2f}/mo vs last year")
    print()

    if not subs:
        hint = data.get("config_hint")
        if hint:
            print(hint)
        else:
            print("No subscription transactions found.")
        return

    print(
        f"{'Service':<28} {'Cat':<10} {'Freq':<10} {'Amount':>9} {'Monthly':>9}"
        f" {'Annual':>9} {'Last':<8} {'Next':<10} {'Status'}"
    )
    print("-" * 108)

    for s in subs:
        freq = s["frequency"]
        cat = s["category"].replace("Subscriptions (", "").replace(")", "")
        monthly_str = f"${s['monthly_cost']:,.2f}" if freq != "monthly" else ""
        last = _short_date(s["last_charged"])
        nxt = _short_date(s.get("next_expected"))
        status = s["status"]
        payee = s["payee"][:27]
        print(
            f"{payee:<28} {cat:<10} {freq:<10} ${s['median_amount']:>8,.2f}"
            f" {monthly_str:>9} ${s['annual_cost']:>8,.2f} {last:<8} {nxt:<10} {status}"
        )

    if check_count:
        print()
        print("Subscriptions marked 'check' have not charged recently and may be cancelled.")
