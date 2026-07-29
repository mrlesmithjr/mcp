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


def delete_mowing(config, row_id):
    """Delete a mowing visit by ID. Returns number of rows deleted."""
    conn = get_db(config)
    result = conn.execute("DELETE FROM mowing_visits WHERE id = ?", (row_id,))
    conn.commit()
    rowcount = result.rowcount
    conn.close()
    return rowcount
