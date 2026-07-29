"""Optional import of lawn care spending from a ynab-tools SQLite database.

This module queries the ynab-tools database (if available) for transactions
in a configurable YNAB category and imports them as purchases and mowing visits.
No hard dependency on ynab-tools - gracefully errors if the database is not found.

All classification rules (mowing keywords, provider aliases, category keywords,
skip patterns) are loaded from config.yaml under the `ynab` key. See
config.example.yaml for the full schema.
"""

import os
import sqlite3
from pathlib import Path

# Default ynab-tools database location
DEFAULT_YNAB_DB = Path.home() / ".local" / "share" / "ynab-tools" / "ynab.db"

# Default YNAB category to pull from
DEFAULT_CATEGORY = "Home: Yard & Outdoor Maintenance"

# Fallback keyword lists (used when config doesn't specify them)
DEFAULT_MOWING_KEYWORDS = [
    "lawn care",
    "lawn service",
    "mowing",
    "yard care",
    "landscape",
    "landscaping",
]

DEFAULT_SKIP_PAYEES = ["starting balance"]

DEFAULT_CATEGORY_KEYWORDS = {
    "equipment": [
        "mower",
        "trimmer",
        "chainsaw",
        "blower",
        "spreader",
        "sprayer",
        "pressure washer",
        "cart",
        "tool",
        "hedge trimmer",
    ],
    "product": ["fertilizer", "pre-emergent", "weed", "herbicide", "insecticide", "ant bait", "fungicide"],
    "materials": ["tree", "brush", "stump", "removal", "pine straw", "mulch", "sod", "gravel", "rock"],
    "garden": ["seed", "plant", "bulb", "flower", "garden bed", "raised bed", "planter"],
}

# Payment apps whose payee name should be resolved via memo context
PAYMENT_APPS = ["venmo", "zelle", "cashapp", "paypal"]


def _get_ynab_config(config):
    """Extract YNAB import settings from config with defaults."""
    ynab = config.get("ynab", {})
    return {
        "db_path": ynab.get("db_path", str(DEFAULT_YNAB_DB)),
        "category": ynab.get("category", DEFAULT_CATEGORY),
        "mowing_keywords": ynab.get("mowing_keywords", DEFAULT_MOWING_KEYWORDS),
        "provider_aliases": ynab.get("provider_aliases", {}),
        "category_keywords": ynab.get("category_keywords", DEFAULT_CATEGORY_KEYWORDS),
        "skip_payees": ynab.get("skip_payees", DEFAULT_SKIP_PAYEES),
    }


def _is_mowing_service(payee_name, memo, mowing_keywords, provider_aliases):
    """Determine if a transaction is a mowing/lawn service visit."""
    combined = f"{payee_name} {memo or ''}".lower()
    # Check provider aliases first (exact payee matches)
    if any(alias in payee_name.lower() for alias in provider_aliases):
        return True
    return any(kw in combined for kw in mowing_keywords)


def _classify_category(payee_name, memo, category_keywords):
    """Classify a YNAB transaction into a lawnops purchase category."""
    combined = f"{payee_name} {memo or ''}".lower()
    for category, keywords in category_keywords.items():
        if any(kw in combined for kw in keywords):
            return category
    return "supplies"


def _normalize_provider(payee_name, memo, provider_aliases):
    """Normalize mowing service payee names using configured aliases.

    provider_aliases maps a lowercase payee substring to a canonical name.
    Example: {"joes mowing": "Joe's Mowing Service"}

    Also resolves payment-app payees (Venmo, Zelle, etc.) using memo context.
    """
    lower = payee_name.lower()
    for alias, canonical in provider_aliases.items():
        if alias in lower:
            return canonical
    # Payment-app payees - label as generic mowing service
    if lower in PAYMENT_APPS:
        return f"Mowing Service (via {payee_name})"
    return payee_name


def get_ynab_db(config):
    """Resolve the ynab-tools database path.

    Checks config for ynab.db_path, then falls back to the default location.
    Returns the path or raises RuntimeError if not found.
    """
    ynab_cfg = _get_ynab_config(config)
    db_path = os.path.expanduser(ynab_cfg["db_path"])
    if not os.path.exists(db_path):
        raise RuntimeError(
            f"ynab-tools database not found at {db_path}. "
            "Install ynab-tools and run 'ynab sync' first, or set ynab.db_path in config.yaml."
        )
    return db_path


def preview_ynab_import(config, year=None):
    """Preview what would be imported from YNAB without writing anything.

    Returns (mowing_visits, purchases, skipped) - each is a list of dicts.
    """
    ynab_cfg = _get_ynab_config(config)
    ynab_path = get_ynab_db(config)

    ynab_conn = sqlite3.connect(ynab_path)
    ynab_conn.row_factory = sqlite3.Row

    where = "WHERE category_name = ? AND deleted = 0 AND amount < 0"
    params = [ynab_cfg["category"]]
    if year:
        where += " AND date LIKE ?"
        params.append(f"{year}%")

    rows = ynab_conn.execute(
        f"""
        SELECT date, payee_name, memo, amount
        FROM transactions {where}
        ORDER BY date
    """,
        params,
    ).fetchall()
    ynab_conn.close()

    mowing_visits = []
    purchases = []
    skipped = []

    for r in rows:
        payee = r["payee_name"] or ""
        memo = r["memo"] or ""
        cost = abs(r["amount"])
        date = r["date"]

        if any(s in payee.lower() for s in ynab_cfg["skip_payees"]):
            skipped.append({"date": date, "payee": payee, "memo": memo, "cost": cost, "reason": "skipped payee"})
            continue

        if _is_mowing_service(payee, memo, ynab_cfg["mowing_keywords"], ynab_cfg["provider_aliases"]):
            mowing_visits.append(
                {
                    "date": date,
                    "provider": _normalize_provider(payee, memo, ynab_cfg["provider_aliases"]),
                    "cost": cost,
                    "notes": memo if memo else None,
                }
            )
        else:
            item = memo if memo else payee
            purchases.append(
                {
                    "date": date,
                    "item": item,
                    "category": _classify_category(payee, memo, ynab_cfg["category_keywords"]),
                    "cost": cost,
                    "source": payee,
                    "notes": None,
                }
            )

    return mowing_visits, purchases, skipped


def import_from_ynab(config, year=None):
    """Import lawn care transactions from ynab-tools database.

    Inserts mowing visits and purchases, skipping duplicates by date+cost.
    Returns (mowing_count, purchase_count, skip_count).
    Raises RuntimeError if lawnops DB or ynab DB not found.
    """
    from lawnops.db.connection import get_db

    conn = get_db(config)
    mowing_visits, purchases, skipped = preview_ynab_import(config, year)

    mowing_count = 0
    for m in mowing_visits:
        existing = conn.execute(
            "SELECT id FROM mowing_visits WHERE date = ? AND cost = ?", (m["date"], m["cost"])
        ).fetchone()
        if existing:
            continue
        conn.execute(
            """
            INSERT INTO mowing_visits (date, provider, cost, notes)
            VALUES (?, ?, ?, ?)
        """,
            (m["date"], m["provider"], m["cost"], m["notes"]),
        )
        mowing_count += 1

    purchase_count = 0
    for p in purchases:
        existing = conn.execute(
            "SELECT id FROM purchases WHERE date = ? AND cost = ? AND item = ?", (p["date"], p["cost"], p["item"])
        ).fetchone()
        if existing:
            continue
        conn.execute(
            """
            INSERT INTO purchases (date, item, category, cost, source, notes)
            VALUES (?, ?, ?, ?, ?, ?)
        """,
            (p["date"], p["item"], p["category"], p["cost"], p["source"], p["notes"]),
        )
        purchase_count += 1

    conn.commit()
    conn.close()

    return mowing_count, purchase_count, len(skipped)
