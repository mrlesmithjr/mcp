"""Pest control treatment tracking."""

from homeops.db.connection import get_db


def add_pest_treatment(config, treatment_date, area, product, method=None, notes=None, cost=None):
    """Log a pest control treatment."""
    conn = get_db(config)
    conn.execute(
        "INSERT INTO pest_treatments (date, area, product, method, notes, cost) VALUES (?, ?, ?, ?, ?, ?)",
        (treatment_date, area, product, method, notes, cost),
    )

    # If cost provided, also log to costs table
    if cost and cost > 0:
        conn.execute(
            "INSERT INTO costs (date, category, amount, description, source) "
            "VALUES (?, 'pest', ?, ?, 'pest_treatment')",
            (treatment_date, cost, f"{product} - {area}"),
        )

    conn.commit()
    conn.close()

    return {
        "date": treatment_date,
        "area": area,
        "product": product,
        "method": method,
        "cost": cost,
    }


def delete_pest_treatment(config, treatment_id):
    """Delete a pest treatment by ID. Returns True if deleted."""
    conn = get_db(config)
    result = conn.execute("DELETE FROM pest_treatments WHERE id = ?", (treatment_id,))
    conn.commit()
    rowcount = result.rowcount
    conn.close()
    return rowcount > 0


def list_pest_history(config, year=None, limit=25):
    """List pest treatment history."""
    conn = get_db(config)
    if year:
        rows = conn.execute(
            "SELECT * FROM pest_treatments WHERE date LIKE ? ORDER BY date DESC LIMIT ?",
            (f"{year}%", limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM pest_treatments ORDER BY date DESC LIMIT ?",
            (limit,),
        ).fetchall()

    conn.close()
    return [dict(r) for r in rows]
