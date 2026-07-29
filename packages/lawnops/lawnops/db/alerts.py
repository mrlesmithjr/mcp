"""Reorder alerts - identify products needing restocking."""

from lawnops.db.connection import get_db
from lawnops.matching import matches


def get_reorder_alerts(config):
    """Query products with qty_on_hand == 0 that have been used in treatments.

    Cross-references the treatments table to identify products used in
    recurring treatments. Returns a list of dicts with product info and
    last_ordered date.

    The cross-reference happens in Python rather than SQL. `treatments.product`
    is free text with no foreign key, so the same item appears under different
    names in each table ("Lesco 0-0-7 Prodiamine" vs the catalog's
    "Lesco 0-0-7 Pre-Emergent (50 lb)"). A LIKE join can only express substring
    containment, which those two do not satisfy in either direction, so every
    product reported treatment_count 0. See lawnops.matching.

    Raises RuntimeError if DB not initialized.
    """
    conn = get_db(config)
    products = conn.execute("""
        SELECT id, name, category, unit, cost_each, last_ordered, source
        FROM products
        WHERE qty_on_hand <= 0
        ORDER BY name
    """).fetchall()
    treatments = conn.execute("SELECT product, date FROM treatments").fetchall()
    conn.close()

    alerts = []
    for p in products:
        used = [t for t in treatments if matches(p["name"], t["product"])]
        alerts.append(
            {
                "name": p["name"],
                "category": p["category"],
                "unit": p["unit"],
                "cost_each": p["cost_each"],
                "last_ordered": p["last_ordered"],
                "source": p["source"],
                "treatment_count": len(used),
                "last_used": max((t["date"] for t in used), default=None),
            }
        )

    # Most-used first, matching the previous ORDER BY treatment_count DESC.
    alerts.sort(key=lambda a: (-a["treatment_count"], a["name"]))
    return alerts
