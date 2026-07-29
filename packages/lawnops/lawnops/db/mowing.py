"""Mowing service tracking."""

from datetime import datetime

from lawnops.db.connection import get_db


def add_mowing(config, date, provider=None, cost=None, notes=None):
    """Log a mowing visit."""
    if provider is None:
        provider = config.get("mowing", {}).get("default_provider", "Mowing Service")
    conn = get_db(config)
    conn.execute(
        """
        INSERT INTO mowing_visits (date, provider, cost, notes)
        VALUES (?, ?, ?, ?)
    """,
        (date, provider, cost, notes),
    )
    conn.commit()
    conn.close()


def get_mowing_summary(config, year=None):
    """Get mowing summary for a year.

    Returns (visits_list, total_visits, total_cost, year).
    """
    conn = get_db(config)
    if year is None:
        year = datetime.now().year
    rows = conn.execute(
        """
        SELECT id, date, provider, cost, notes FROM mowing_visits
        WHERE date LIKE ? ORDER BY date
    """,
        (f"{year}%",),
    ).fetchall()
    total = conn.execute(
        """
        SELECT COUNT(*) as visits, COALESCE(SUM(cost), 0) as total_cost
        FROM mowing_visits WHERE date LIKE ?
    """,
        (f"{year}%",),
    ).fetchone()
    conn.close()
    return rows, total["visits"], total["total_cost"], year


def get_mowing_gap(config, year=None):
    """Report whether mowing has stopped being logged.

    get_mowing_summary answers "what is recorded" and cannot distinguish a
    service that stopped from one that is still running but untracked. Nothing
    else closes that gap either: next_mow_date comes from the configured
    schedule_day (see lawnops.mowing.get_next_mow_dates) and never consults the
    log, so a season's worth of unlogged visits looks identical to a healthy one.

    Returns a dict, or None when there is nothing to judge (no visits recorded,
    or no schedule_day configured so there is no expected cadence).
    """
    conn = get_db(config)
    if year is None:
        year = datetime.now().year
    row = conn.execute(
        "SELECT MAX(date) AS last_date FROM mowing_visits WHERE date LIKE ?",
        (f"{year}%",),
    ).fetchone()
    conn.close()

    last_date = row["last_date"] if row else None
    if not last_date:
        return None

    # schedule_day implies a weekly service; without it there is no baseline.
    if not config.get("mowing", {}).get("schedule_day"):
        return None
    expected_interval_days = 7

    try:
        last = datetime.strptime(last_date, "%Y-%m-%d").date()
    except ValueError:
        return None
    days_since = (datetime.now().date() - last).days

    # 1.5x the cadence: a single late visit is normal, a missed cycle is not.
    return {
        "last_visit": last_date,
        "days_since_last": days_since,
        "expected_interval_days": expected_interval_days,
        "unlogged_suspected": days_since > expected_interval_days * 1.5,
    }


def delete_mowing(config, row_id):
    """Delete a mowing visit by ID. Returns number of rows deleted."""
    conn = get_db(config)
    result = conn.execute("DELETE FROM mowing_visits WHERE id = ?", (row_id,))
    conn.commit()
    rowcount = result.rowcount
    conn.close()
    return rowcount
