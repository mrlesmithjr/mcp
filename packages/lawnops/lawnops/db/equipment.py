"""Equipment inventory."""

from lawnops.db.connection import get_db


def add_equipment(config, name, cost=None, purchase_date=None, source=None, notes=None, status=None):
    """Add equipment to inventory.

    `status` is optional and only included in the INSERT when given: leaving
    it out lets the table's own `DEFAULT 'active'` apply, since binding NULL
    for a listed column would override that default instead of falling back
    to it.
    """
    conn = get_db(config)
    if status is not None:
        conn.execute(
            """
            INSERT INTO equipment (name, cost, purchase_date, source, status, notes)
            VALUES (?, ?, ?, ?, ?, ?)
        """,
            (name, cost, purchase_date, source, status, notes),
        )
    else:
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
