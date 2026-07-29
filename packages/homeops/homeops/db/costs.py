"""Cost tracking and reporting."""

from homeops.db.connection import get_db


def add_cost(config, cost_date, category, amount, provider=None, description=None, notes=None):
    """Add a maintenance cost entry."""
    conn = get_db(config)
    conn.execute(
        "INSERT INTO costs (date, category, amount, provider, description, notes) VALUES (?, ?, ?, ?, ?, ?)",
        (cost_date, category, amount, provider, description, notes),
    )
    conn.commit()
    conn.close()

    return {
        "date": cost_date,
        "category": category,
        "amount": amount,
        "provider": provider,
    }


def delete_cost(config, cost_id):
    """Delete a cost entry by ID. Returns True if deleted."""
    conn = get_db(config)
    result = conn.execute("DELETE FROM costs WHERE id = ?", (cost_id,))
    conn.commit()
    rowcount = result.rowcount
    conn.close()
    return rowcount > 0


def get_cost_summary(config, year=None):
    """Get spending summary by category."""
    conn = get_db(config)
    if year:
        rows = conn.execute(
            "SELECT category, COUNT(*) as count, SUM(amount) as total, "
            "AVG(amount) as avg, MIN(amount) as min, MAX(amount) as max "
            "FROM costs WHERE date LIKE ? GROUP BY category ORDER BY total DESC",
            (f"{year}%",),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT category, COUNT(*) as count, SUM(amount) as total, "
            "AVG(amount) as avg, MIN(amount) as min, MAX(amount) as max "
            "FROM costs GROUP BY category ORDER BY total DESC",
        ).fetchall()

    conn.close()
    return [dict(r) for r in rows]


def get_cost_history(config, year=None, category=None, limit=50):
    """Get cost line items."""
    conn = get_db(config)
    query = "SELECT * FROM costs"
    params = []
    conditions = []

    if year:
        conditions.append("date LIKE ?")
        params.append(f"{year}%")
    if category:
        conditions.append("category = ?")
        params.append(category)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY date DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]
