"""Database sub-package for LawnOps - SQLite CRUD for yard maintenance tracking."""

from lawnops.db.alerts import get_reorder_alerts
from lawnops.db.connection import connect, get_db, get_db_path
from lawnops.db.equipment import add_equipment, delete_equipment, list_equipment
from lawnops.db.irrigation_analytics import (
    et_recommendations,
    evaluate_irrigation_check,
    irrigation_budget,
    irrigation_pace,
    zone_analysis,
)
from lawnops.db.irrigation_log import backfill_irrigation, log_irrigation_run, sync_irrigation
from lawnops.db.mowing import add_mowing, delete_mowing, get_mowing_gap, get_mowing_summary
from lawnops.db.observations import log_daily_observations
from lawnops.db.obsidian_import import import_from_obsidian
from lawnops.db.pollen import get_pollen_history, log_pollen
from lawnops.db.products import add_product, delete_product, list_products, update_product
from lawnops.db.purchases import add_purchase, delete_purchase
from lawnops.db.reports import get_spend_report
from lawnops.db.schema import SCHEMA_SQL, SCHEMA_VERSION
from lawnops.db.treatments import add_treatment, delete_treatment, list_treatments
from lawnops.db.water_usage import get_water_usage_report
from lawnops.db.ynab_import import import_from_ynab, preview_ynab_import


def init_db(config: dict | None = None) -> str:
    """Initialize the database (idempotent). Returns the database path as a string."""
    conn = connect(config)
    conn.close()
    return str(get_db_path(config))


__all__ = [
    "get_db_path",
    "get_db",
    "connect",
    "SCHEMA_SQL",
    "SCHEMA_VERSION",
    "init_db",
    "log_daily_observations",
    "add_treatment",
    "list_treatments",
    "delete_treatment",
    "add_product",
    "list_products",
    "update_product",
    "delete_product",
    "add_purchase",
    "delete_purchase",
    "add_mowing",
    "get_mowing_gap",
    "get_mowing_summary",
    "delete_mowing",
    "add_equipment",
    "list_equipment",
    "delete_equipment",
    "log_irrigation_run",
    "sync_irrigation",
    "backfill_irrigation",
    "get_spend_report",
    "get_water_usage_report",
    "get_reorder_alerts",
    "import_from_obsidian",
    "preview_ynab_import",
    "import_from_ynab",
    "irrigation_pace",
    "irrigation_budget",
    "zone_analysis",
    "et_recommendations",
    "evaluate_irrigation_check",
    "log_pollen",
    "get_pollen_history",
]
