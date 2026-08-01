"""Treatment tracking - add and list lawn treatments."""

from datetime import datetime

from lawnops.db.connection import get_db


def add_treatment(config, date, area, product, method=None, amount=None, soil_temp=None, cost=None, notes=None):
    """Add a treatment record."""
    conn = get_db(config)
    conn.execute(
        """
        INSERT INTO treatments (date, treatment_area, product, method, amount, soil_temp_f, cost, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (date, area, product, method, amount, soil_temp, cost, notes),
    )
    conn.commit()
    conn.close()


def list_treatments(config, year=None):
    """List treatments for a given year. Returns list of sqlite3.Row."""
    conn = get_db(config)
    if year is None:
        year = datetime.now().year
    rows = conn.execute(
        """
        SELECT id, date, treatment_area, product, method, cost, notes
        FROM treatments WHERE date LIKE ? ORDER BY date
    """,
        (f"{year}%",),
    ).fetchall()
    conn.close()
    return rows, year


def delete_treatment(config, row_id):
    """Delete a treatment by ID. Returns number of rows deleted."""
    conn = get_db(config)
    result = conn.execute("DELETE FROM treatments WHERE id = ?", (row_id,))
    conn.commit()
    rowcount = result.rowcount
    conn.close()
    return rowcount
