"""Product inventory management."""

from datetime import datetime

from lawnops.db.connection import get_db


def add_product(config, name, category=None, qty=1, unit="bag", cost=None, source=None, notes=None):
    """Add a product to inventory."""
    conn = get_db(config)
    today = datetime.now().strftime("%Y-%m-%d")
    conn.execute(
        """
        INSERT INTO products (name, category, qty_on_hand, unit, last_ordered, cost_each, source, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (name, category, qty, unit, today, cost, source, notes),
    )
    conn.commit()
    conn.close()


def list_products(config):
    """List all products. Returns list of sqlite3.Row."""
    conn = get_db(config)
    rows = conn.execute("""
        SELECT name, category, qty_on_hand, unit, last_ordered, cost_each, source
        FROM products ORDER BY category, name
    """).fetchall()
    conn.close()
    return rows


def update_product(config, name, qty=None, cost=None):
    """Update product stock by partial name match. Returns number of rows updated."""
    conn = get_db(config)
    updates = []
    params = []
    if qty is not None:
        updates.append("qty_on_hand = ?")
        params.append(qty)
    if cost is not None:
        updates.append("cost_each = ?")
        params.append(cost)
    if not updates:
        conn.close()
        return 0

    updates.append("updated_at = datetime('now')")
    params.append(f"%{name}%")

    result = conn.execute(f"UPDATE products SET {', '.join(updates)} WHERE name LIKE ?", params)
    conn.commit()
    rowcount = result.rowcount
    conn.close()
    return rowcount


def delete_product(config, name):
    """Delete product(s) by partial name match. Returns number of rows deleted."""
    conn = get_db(config)
    result = conn.execute("DELETE FROM products WHERE name LIKE ?", (f"%{name}%",))
    conn.commit()
    rowcount = result.rowcount
    conn.close()
    return rowcount
