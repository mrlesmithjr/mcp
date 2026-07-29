"""Service provider directory."""

from homeops.db.connection import get_db


def add_provider(config, name, category, phone=None, email=None, typical_cost=None, notes=None):
    """Add a service provider."""
    conn = get_db(config)
    conn.execute(
        "INSERT INTO providers (name, category, phone, email, typical_cost, notes) VALUES (?, ?, ?, ?, ?, ?)",
        (name, category, phone, email, typical_cost, notes),
    )
    conn.commit()
    conn.close()

    return {"name": name, "category": category}


def list_providers(config, category=None, active_only=True):
    """List service providers."""
    conn = get_db(config)
    query = "SELECT * FROM providers"
    params = []
    conditions = []

    if active_only:
        conditions.append("active = 1")
    if category:
        conditions.append("category = ?")
        params.append(category)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY category, name"

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_provider_detail(config, name):
    """Get provider details with cost history from task_log."""
    conn = get_db(config)
    # Find provider
    rows = conn.execute(
        "SELECT * FROM providers WHERE name LIKE ?",
        (f"%{name}%",),
    ).fetchall()

    if not rows:
        conn.close()
        return None

    provider = dict(rows[0])

    # Get cost history from task_log and costs
    cost_rows = conn.execute(
        "SELECT date, amount, description, notes FROM costs WHERE provider LIKE ? ORDER BY date DESC LIMIT 20",
        (f"%{provider['name']}%",),
    ).fetchall()

    provider["cost_history"] = [dict(r) for r in cost_rows]
    conn.close()
    return provider
