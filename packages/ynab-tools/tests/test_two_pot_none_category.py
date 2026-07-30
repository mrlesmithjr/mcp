"""Regression tests for two-pot classification with unknown (None) category names (issue #33).

Two-pot compliance classifies every money_movements row whose to_category_id is
set. When a movement references a category that no longer exists in
budget_categories, both the stored to_category_name and the join yield NULL, so a
None name reaches _is_bonus_funded. Before #33 that crashed on name.lower() with
"'NoneType' object has no attribute 'lower'", taking down both the
two_pot_compliance MCP tool and GET /api/two-pot. A second None then surfaced in
the CLI report, which formatted the None category name (f"{cat:<44}").

The unit tests cover the guarded helpers; the fixture tests drive a NULL
to_category_name row all the way through both the dashboard builder and the CLI
report to prove neither raises and both label the row "(unknown category)".
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from ynab_tools.db import get_connection, init_db
from ynab_tools.reports.paycheck_breakdown import _is_bonus_funded, _is_excluded


def test_is_bonus_funded_tolerates_none_name() -> None:
    """A None category name must not raise; it cannot match a bonus name/group."""
    assert _is_bonus_funded(None, "Bonus Fund", {"bonus"}, {"Vacation"}) is True  # matched by group
    assert _is_bonus_funded(None, None, {"bonus"}, {"Vacation"}) is False  # unknown -> regular-pot
    assert _is_bonus_funded(None, "Monthly Bills", {"bonus"}, {"Vacation"}) is False


def test_is_excluded_tolerates_none_name() -> None:
    """Sibling guard: _is_excluded already tolerates a None name via the set check."""
    assert _is_excluded("", None, {"Credit Card"}, {"Interest"}) is False


def _seed_ghost_category_budget(db_path: Path) -> str:
    """Build a temp DB with a bonus month and one movement into a now-deleted category.

    Returns the bonus month key ('YYYY-MM'). The movement's to_category_id points at
    a category that has no budget_categories row, so both the stored name and the
    LEFT JOIN yield NULL - exactly the issue #33 shape.
    """
    conn = get_connection(db_path)
    init_db(conn)

    today = date.today()
    month_str = today.strftime("%Y-%m-01")
    pay_date = today.strftime("%Y-%m-%d")
    # moved_at must sort at/after the bonus paycheck timestamp for the movement to count.
    moved_at = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    conn.execute(
        "INSERT INTO accounts (id, name, type, on_budget, closed, deleted, balance)"
        " VALUES ('acct1', 'Checking', 'checking', 1, 0, 0, 5000)"
    )
    conn.execute(
        """INSERT INTO transactions
           (id, date, amount, cleared, approved, account_id, account_name,
            payee_name, category_name, deleted)
           VALUES ('pay1', ?, 5000, 'cleared', 1, 'acct1', 'Checking',
                   'Employer', 'Income: Paychecks', 0)""",
        (pay_date,),
    )
    # Movement into a ghost category: id set, name NULL, no budget_categories row.
    conn.execute(
        """INSERT INTO money_movements
           (id, month, moved_at, to_category_id, to_category_name,
            amount_milliunits, amount, deleted)
           VALUES ('mv1', ?, ?, 'ghost-cat-id', NULL, 40000, 40.0, 0)""",
        (month_str, moved_at),
    )
    conn.commit()
    conn.close()
    return today.strftime("%Y-%m")


def _set_two_pot_env(monkeypatch) -> None:
    monkeypatch.setenv("YNAB_REGULAR_PAY", "1000")
    monkeypatch.setenv("YNAB_BONUS_THRESHOLD", "1500")


def test_build_two_pot_labels_deleted_category(tmp_path, monkeypatch) -> None:
    """Dashboard builder: a NULL-name movement returns as '(unknown category)', no raise."""
    _set_two_pot_env(monkeypatch)
    month_key = _seed_ghost_category_budget(tmp_path / "two_pot.db")

    from ynab_tools.dashboard.api.two_pot import _build_two_pot

    conn = get_connection(tmp_path / "two_pot.db")
    try:
        result = _build_two_pot(conn)
    finally:
        conn.close()

    assert result["config_ok"] is True
    months = {m["month"]: m for m in result["months"]}
    assert month_key in months
    backwards_names = [r["category_name"] for r in months[month_key]["backwards"]]
    assert "(unknown category)" in backwards_names


def test_run_two_pot_report_survives_deleted_category(tmp_path, monkeypatch, capsys) -> None:
    """CLI report: a NULL-name movement prints '(unknown category)' instead of crashing."""
    _set_two_pot_env(monkeypatch)
    _seed_ghost_category_budget(tmp_path / "two_pot.db")

    import ynab_tools.reports.two_pot_report as report_mod

    monkeypatch.setattr(report_mod, "get_connection", lambda *a, **k: get_connection(tmp_path / "two_pot.db"))
    report_mod.run_two_pot_report(months=1)

    out = capsys.readouterr().out
    assert "Traceback" not in out
    assert "(unknown category)" in out
