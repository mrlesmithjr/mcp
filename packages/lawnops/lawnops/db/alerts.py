"""Reorder alerts - identify products needing restocking."""

from lawnops.db.connection import get_db


def get_reorder_alerts(config):
    """Query products with qty_on_hand == 0 that have been used in treatments.

    Cross-references the treatments table to identify products used in
    recurring treatments. Returns a list of dicts with product info and
    last_ordered date.

    Raises RuntimeError if DB not initialized.
    """
    conn = get_db(config)
    rows = conn.execute("""
        SELECT
            p.name,
            p.category,
            p.unit,
            p.cost_each,
            p.last_ordered,
            p.source,
            COUNT(t.id) AS treatment_count,
            MAX(t.date) AS last_used
        FROM products p
        LEFT JOIN treatments t ON LOWER(t.product) LIKE '%' || LOWER(p.name) || '%'
        WHERE p.qty_on_hand <= 0
        GROUP BY p.id
        ORDER BY treatment_count DESC, p.name
    """).fetchall()
    conn.close()

    alerts = []
    for r in rows:
        alerts.append(
            {
                "name": r["name"],
                "category": r["category"],
                "unit": r["unit"],
                "cost_each": r["cost_each"],
                "last_ordered": r["last_ordered"],
                "source": r["source"],
                "treatment_count": r["treatment_count"],
                "last_used": r["last_used"],
            }
        )
    return alerts
