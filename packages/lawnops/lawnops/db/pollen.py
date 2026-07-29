"""Pollen count database operations - logging, history, and trend queries."""

import json
from datetime import datetime, timedelta

from lawnops.db.connection import get_db


def log_pollen(config, pollen_data):
    """Log pollen count to the database. Silently skips if auto_log disabled.

    Args:
        config: Full config dict.
        pollen_data: Dict from pollen.fetch_pollen() - must have date, total_count, category.
    """
    if not config.get("database", {}).get("auto_log", True):
        return
    if pollen_data is None:
        return
    try:
        conn = get_db(config)
        conn.execute(
            """
            INSERT INTO pollen_counts (date, total_count, category, trees, grasses, weeds, molds)
            VALUES (:date, :total_count, :category, :trees, :grasses, :weeds, :molds)
            ON CONFLICT(date) DO UPDATE SET
                total_count=:total_count, category=:category,
                trees=:trees, grasses=:grasses, weeds=:weeds, molds=:molds,
                logged_at=datetime('now')
        """,
            {
                "date": pollen_data["date"],
                "total_count": pollen_data["total_count"],
                "category": pollen_data["category"],
                "trees": json.dumps(pollen_data.get("trees", [])),
                "grasses": json.dumps(pollen_data.get("grasses", [])),
                "weeds": json.dumps(pollen_data.get("weeds", [])),
                "molds": json.dumps(pollen_data.get("molds", [])),
            },
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_pollen_history(config, days=14):
    """Return recent pollen counts from the database.

    Returns list of dicts with keys: date, total_count, category, trees, grasses, weeds, molds.
    """
    try:
        conn = get_db(config)
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = conn.execute(
            """
            SELECT date, total_count, category, trees, grasses, weeds, molds
            FROM pollen_counts
            WHERE date >= ?
            ORDER BY date
        """,
            (cutoff,),
        ).fetchall()
        conn.close()

        result = []
        for row in rows:
            entry = dict(row)
            # Parse JSON arrays
            for field in ("trees", "grasses", "weeds", "molds"):
                val = entry.get(field)
                if val and isinstance(val, str):
                    try:
                        entry[field] = json.loads(val)
                    except (json.JSONDecodeError, TypeError):
                        entry[field] = []
                elif not val:
                    entry[field] = []
            result.append(entry)
        return result
    except Exception:
        return []
