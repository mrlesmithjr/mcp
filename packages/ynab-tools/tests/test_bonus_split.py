"""Tests for bonus_split payee detection window (issue #218).

Guards against regression where _find_latest_paycheck calls
_get_recurring_inflows with months=3 (too short for payee detection)
instead of months=12 (matching paycheck_funding._get_next_income).
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch


def test_find_latest_paycheck_uses_12_month_window():
    """_find_latest_paycheck must call _get_recurring_inflows with months=12.

    Both bonus_split and paycheck_funding perform the same paycheck-payee
    identification task. The 12-month window is required for resilience to
    payee name changes and sparse inflow history (e.g. months with only
    one paycheck due to holidays). refs #210, #218.
    """
    from ynab_tools.reports.bonus_split import _find_latest_paycheck

    conn = sqlite3.connect(":memory:")
    captured: dict[str, object] = {}

    def fake_get_recurring_inflows(c, months):
        captured["months"] = months
        return []

    with patch(
        "ynab_tools.reports.bonus_split._get_recurring_inflows",
        side_effect=fake_get_recurring_inflows,
    ):
        _find_latest_paycheck(conn, regular_pay=3000.0)

    assert "months" in captured, "_get_recurring_inflows was never called"
    assert captured["months"] == 12, (
        f"Expected months=12 but got months={captured['months']}. "
        "bonus_split payee detection must use a 12-month window to match "
        "paycheck_funding._get_next_income (refs #210)."
    )
    conn.close()
