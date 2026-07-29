"""Equipment inventory."""

from lawnops.db.connection import get_db


def add_equipment(config, name, cost=None, purchase_date=None, source=None, notes=None):
    """Add equipment to inventory."""
    conn = get_db(config)
    conn.execute(
        """
        INSERT INTO equipment (name, cost, purchase_date, source, notes)
        VALUES (?, ?, ?, ?, ?)
    """,
        (name, cost, purchase_date, source, notes),
    )
    conn.commit()
    conn.close()


def list_equipment(config):
    """List all equipment. Returns list of sqlite3.Row."""
    conn = get_db(config)
    rows = conn.execute("""
        SELECT id, name, purchase_date, cost, source, status, notes
        FROM equipment ORDER BY purchase_date
    """).fetchall()
    conn.close()
    return rows


def delete_equipment(config, row_id):
    """Delete equipment by ID. Returns number of rows deleted."""
    conn = get_db(config)
    result = conn.execute("DELETE FROM equipment WHERE id = ?", (row_id,))
    conn.commit()
    rowcount = result.rowcount
    conn.close()
    return rowcount
