"""Spending reports."""

from datetime import datetime

from lawnops.db.connection import get_db


def get_spend_report(config, year=None, category=None):
    """Get spending summary.

    Returns (category_rows, grand_total, item_rows, year).
    """
    conn = get_db(config)
    if year is None:
        year = datetime.now().year

    where = "WHERE date LIKE ?"
    params = [f"{year}%"]
    if category:
        where += " AND category = ?"
        params.append(category)

    category_rows = conn.execute(
        f"""
        SELECT category, SUM(cost) as total, COUNT(*) as count
        FROM purchases {where}
        GROUP BY category ORDER BY total DESC
    """,
        params,
    ).fetchall()

    grand_total = conn.execute(
        f"""
        SELECT COALESCE(SUM(cost), 0) FROM purchases {where}
    """,
        params,
    ).fetchone()[0]

    item_rows = conn.execute(
        f"""
        SELECT id, date, item, category, cost, source
        FROM purchases {where} ORDER BY date
    """,
        params,
    ).fetchall()
    conn.close()

    return category_rows, grand_total, item_rows, year
