"""LawnOps integrations for the coordinator, irrigation and product inventory."""

import logging
import os
import sqlite3

logger = logging.getLogger(__name__)

_LAWNOPS_DB = os.path.expanduser("~/.local/share/lawnops/lawnops.db")


def suspend_irrigation(hours=24):
    """Suspend all irrigation zones for N hours via Hydrawise.

    Returns (until_str, zone_name_or_None).
    Raises on Hydrawise API error.
    """
    from lawnops.irrigation import suspend

    return suspend(hours)


def get_irrigation_budget_status():
    """Read LawnOps' current irrigation budget status for the daily budget-alert check.

    Returns dict: {budget_status, pct_used, remaining, projected_cost,
    monthly_dollars_limit}. Raises if LawnOps config is missing/invalid.
    """
    from lawnops.config import load_config
    from lawnops.db import irrigation_budget

    result = irrigation_budget(load_config())
    return {
        "budget_status": result.get("budget_status"),
        "pct_used": result.get("pct_used"),
        "remaining": result.get("remaining"),
        "projected_cost": result.get("projected_cost"),
        "monthly_dollars_limit": result.get("monthly_dollars_limit"),
    }


def get_product(name):
    """Look up a product by partial name match. Returns dict or None if not found."""
    if not os.path.exists(_LAWNOPS_DB):
        return None
    conn = sqlite3.connect(_LAWNOPS_DB)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT name, qty_on_hand, unit FROM products WHERE name LIKE ? LIMIT 1",
        (f"%{name}%",),
    ).fetchone()
    conn.close()
    return dict(row) if row else None
