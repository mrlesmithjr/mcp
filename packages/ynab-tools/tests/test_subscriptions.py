"""Tests for subscription analytics (_build_subscriptions)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from ynab_tools.dashboard.api.subscriptions import _build_subscriptions
from ynab_tools.db import get_connection, init_db


def _make_db(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)
    return conn


def _insert(
    conn,
    *,
    payee: str,
    txn_date: str,
    amount: float,
    import_original: str | None = None,
    category: str = "Subscriptions (Personal)",
):
    conn.execute(
        """
        INSERT INTO transactions
        (id, date, amount, memo, cleared, approved, account_id, account_name,
         payee_id, payee_name, category_id, category_name, transfer_account_id,
         import_payee_name, import_payee_name_original, deleted)
        VALUES (?, ?, ?, '', 'cleared', 1, 'acc1', 'Checking',
                'p1', ?, 'cat1', ?, NULL, NULL, ?, 0)
        """,
        (f"{payee}-{txn_date}", txn_date, -abs(amount), payee, category, import_original),
    )


def today_minus(days: int) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


def test_monthly_frequency(tmp_path):
    conn = _make_db(tmp_path)
    for i in range(5, 0, -1):
        _insert(conn, payee="Netflix", txn_date=today_minus(i * 30), amount=15.49)
    _insert(conn, payee="Netflix", txn_date=today_minus(5), amount=15.49)

    data = _build_subscriptions(conn)
    subs = {s["payee"]: s for s in data["subscriptions"]}
    assert "Netflix" in subs
    s = subs["Netflix"]
    assert s["frequency"] == "monthly"
    assert s["monthly_cost"] == pytest.approx(s["median_amount"], abs=0.01)
    assert s["status"] == "active"


def test_annual_frequency(tmp_path):
    conn = _make_db(tmp_path)
    _insert(conn, payee="OnX Maps", txn_date=today_minus(370), amount=29.99)
    _insert(conn, payee="OnX Maps", txn_date=today_minus(5), amount=29.99)

    data = _build_subscriptions(conn)
    subs = {s["payee"]: s for s in data["subscriptions"]}
    assert "OnX Maps" in subs
    s = subs["OnX Maps"]
    assert s["frequency"] == "annual"
    assert s["monthly_cost"] == pytest.approx(29.99 / 12, abs=0.01)


def test_quarterly_frequency(tmp_path):
    conn = _make_db(tmp_path)
    for i in range(3, 0, -1):
        _insert(conn, payee="Quarterly Service", txn_date=today_minus(i * 91), amount=45.00)
    _insert(conn, payee="Quarterly Service", txn_date=today_minus(5), amount=45.00)

    data = _build_subscriptions(conn)
    subs = {s["payee"]: s for s in data["subscriptions"]}
    assert "Quarterly Service" in subs
    s = subs["Quarterly Service"]
    assert s["frequency"] == "quarterly"
    assert s["monthly_cost"] == pytest.approx(45.00 / 3, abs=0.01)


def test_single_charge_assumed_annual(tmp_path):
    conn = _make_db(tmp_path)
    _insert(conn, payee="AnnualOnly", txn_date=today_minus(10), amount=99.00)

    data = _build_subscriptions(conn)
    subs = {s["payee"]: s for s in data["subscriptions"]}
    assert "AnnualOnly" in subs
    s = subs["AnnualOnly"]
    assert s["frequency"] == "annual"
    assert s["monthly_cost"] == pytest.approx(99.00 / 12, abs=0.01)


def test_hide_expired_monthly(tmp_path):
    conn = _make_db(tmp_path)
    for i in range(5, 0, -1):
        _insert(conn, payee="OldService", txn_date=today_minus(i * 30 + 100), amount=9.99)

    data = _build_subscriptions(conn)
    payees = [s["payee"] for s in data["subscriptions"]]
    assert "OldService" not in payees


def test_monthly_to_annual_switch(tmp_path):
    conn = _make_db(tmp_path)
    for i in range(4, 0, -1):
        _insert(conn, payee="Spotify", txn_date=today_minus(i * 30 + 10), amount=10.99)
    _insert(conn, payee="Spotify", txn_date=today_minus(5), amount=119.88)

    data = _build_subscriptions(conn)
    subs = {s["payee"]: s for s in data["subscriptions"]}
    assert "Spotify" in subs
    s = subs["Spotify"]
    assert s["frequency"] == "annual"
    assert s["monthly_cost"] == pytest.approx(119.88 / 12, abs=0.01)


def test_canonical_merge(tmp_path):
    conn = _make_db(tmp_path)
    # Two YNAB payee names both matching ^YOU NEED A BUDGET.* -> "YNAB"
    for i in range(3, 0, -1):
        _insert(
            conn,
            payee="YOU NEED A BUDGET LLC",
            txn_date=today_minus(i * 30 + 365),
            amount=14.99,
            import_original=f"YOU NEED A BUDGET LLC HTTPSYYY {i}",
        )
    for i in range(3, 0, -1):
        _insert(
            conn,
            payee="YNAB",
            txn_date=today_minus(i * 30),
            amount=14.99,
            import_original=f"YouNeedABudget.com charge {i}",
        )

    data = _build_subscriptions(conn)
    subs = {s["payee"]: s for s in data["subscriptions"]}
    assert "YNAB" in subs
    assert "YOU NEED A BUDGET LLC" not in subs


def test_monthly_data_coverage(tmp_path):
    conn = _make_db(tmp_path)
    dates = [today_minus(5), today_minus(35), today_minus(65)]
    for d in dates:
        _insert(conn, payee="Dropbox", txn_date=d, amount=11.99)

    data = _build_subscriptions(conn)
    subs = {s["payee"]: s for s in data["subscriptions"]}
    assert "Dropbox" in subs
    monthly_data = subs["Dropbox"]["monthly_data"]
    assert len(monthly_data) == 3
    for entry in monthly_data:
        assert entry["total"] == pytest.approx(11.99, abs=0.01)


def test_yoy_change_returned(tmp_path):
    conn = _make_db(tmp_path)
    # Recent window: last 3 months
    for i in [10, 40, 70]:
        _insert(conn, payee="Adobe", txn_date=today_minus(i), amount=54.99)
    # Prior window: 12-15 months ago
    for i in [395, 425, 455]:
        _insert(conn, payee="Adobe", txn_date=today_minus(i), amount=54.99)

    data = _build_subscriptions(conn)
    assert data["yoy_change"] is not None
    assert isinstance(data["yoy_change"], float)


def test_business_category(tmp_path):
    conn = _make_db(tmp_path)
    _insert(conn, payee="Claude.ai", txn_date=today_minus(5), amount=20.00, category="Subscriptions (Business)")

    data = _build_subscriptions(conn)
    subs = {s["payee"]: s for s in data["subscriptions"]}
    assert "Claude.ai" in subs
    assert subs["Claude.ai"]["category"] == "Subscriptions (Business)"


def test_detection_months_in_response(tmp_path):
    conn = _make_db(tmp_path)
    _insert(conn, payee="Netflix", txn_date=today_minus(5), amount=15.49)

    data = _build_subscriptions(conn)
    assert "detection_months" in data
    assert data["detection_months"] == 24


def test_status_check_for_overdue(tmp_path):
    conn = _make_db(tmp_path)
    # Monthly service: 5 charges spaced ~30 days apart, last one 60 days ago.
    # median interval = 30 days, threshold = 45 days, days_since = 60 -> "check"
    for i in range(5, 0, -1):
        _insert(conn, payee="OverdueService", txn_date=today_minus(i * 30 + 30), amount=9.99)

    data = _build_subscriptions(conn)
    subs = {s["payee"]: s for s in data["subscriptions"]}
    assert "OverdueService" in subs
    assert subs["OverdueService"]["status"] == "check"
