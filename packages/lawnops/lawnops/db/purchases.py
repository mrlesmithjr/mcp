"""Purchase tracking."""

from lawnops.db.connection import get_db


def add_purchase(config, date, item, category=None, qty=1, cost=None, source=None, notes=None):
    """Add a purchase record."""
    conn = get_db(config)
    conn.execute(
        """
        INSERT INTO purchases (date, item, category, qty, cost, source, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """,
        (date, item, category, qty, cost, source, notes),
    )
    conn.commit()
    conn.close()


def delete_purchase(config, row_id):
    """Delete a purchase by ID. Returns number of rows deleted."""
    conn = get_db(config)
    result = conn.execute("DELETE FROM purchases WHERE id = ?", (row_id,))
    conn.commit()
    rowcount = result.rowcount
    conn.close()
    return rowcount
