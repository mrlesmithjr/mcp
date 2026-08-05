"""SQLite backend for the lawnops agronomic log (issue #57).

The default, zero-config backend. Writes delegate to the existing
lawnops.db CRUD functions so their business-rule defaults (add_product's
last_ordered=today(), add_mowing's config-driven provider default, etc.)
stay exactly as they were before this backend abstraction existed. Reads
use a plain `SELECT *` mapped through the entity's canonical column names;
year filtering, sorting, and aggregation live in lawnops.log_compute so
both backends produce identical MCP tool output from the same rows.
"""

from __future__ import annotations

from lawnops.db.connection import get_db
from lawnops.db.equipment import add_equipment, delete_equipment
from lawnops.db.mowing import add_mowing, delete_mowing
from lawnops.db.products import add_product, delete_product, update_product
from lawnops.db.purchases import add_purchase, delete_purchase
from lawnops.db.treatments import add_treatment, delete_treatment
from lawnops.log_schema import ENTITIES


class SqliteBackend:
    """Delegates to lawnops.db/. `sql_table` always comes from the static
    ENTITIES whitelist in lawnops.log_schema, never from caller input."""

    def __init__(self, config: dict):
        self.config = config

    def read_table(self, entity: str) -> list[dict]:
        spec = ENTITIES[entity]
        conn = get_db(self.config)
        rows = conn.execute(f"SELECT * FROM {spec.sql_table}").fetchall()
        conn.close()
        out = []
        for row in rows:
            d = {"id": row["id"]}
            for canonical, sql_col in spec.sql_column_map.items():
                d[canonical] = row[sql_col]
            out.append(d)
        return out

    def append_row(self, entity: str, row: dict) -> dict:
        config = self.config
        if entity == "treatments":
            add_treatment(
                config,
                row["date"],
                row["area"],
                row["product"],
                row.get("method"),
                row.get("amount"),
                row.get("soil_temp_f"),
                row.get("cost"),
                row.get("notes"),
            )
        elif entity == "products":
            add_product(
                config,
                row["name"],
                row.get("category"),
                row.get("qty_on_hand", 1),
                row.get("unit", "bag"),
                row.get("cost_each"),
                row.get("source"),
                row.get("notes"),
            )
        elif entity == "equipment":
            add_equipment(
                config,
                row["name"],
                row.get("cost"),
                row.get("purchase_date"),
                row.get("source"),
                row.get("notes"),
                row.get("status"),
            )
        elif entity == "purchases":
            add_purchase(
                config,
                row["date"],
                row["item"],
                row.get("category"),
                row.get("qty", 1),
                row.get("cost"),
                row.get("source"),
                row.get("notes"),
            )
        elif entity == "mowing_visits":
            add_mowing(config, row["date"], row.get("provider"), row.get("cost"), row.get("notes"))
        elif entity == "seasonal_tasks":
            conn = get_db(config)
            conn.execute(
                "INSERT INTO seasonal_tasks (season, task_name, timing, product, notes) VALUES (?, ?, ?, ?, ?)",
                (row.get("season"), row.get("task"), row.get("timing"), row.get("product"), row.get("notes")),
            )
            conn.commit()
            conn.close()
        else:
            raise RuntimeError(f"Unknown lawn_log entity: {entity!r}")
        return row

    def update_row(self, entity: str, match: dict, changes: dict) -> int:
        if entity == "products":
            return update_product(
                self.config, match["name_contains"], changes.get("qty_on_hand"), changes.get("cost_each")
            )
        raise RuntimeError(f"update_row is not supported for entity {entity!r} on the sqlite backend")

    def delete_row(self, entity: str, match: dict) -> int:
        if entity == "treatments":
            return delete_treatment(self.config, match["id"])
        if entity == "equipment":
            return delete_equipment(self.config, match["id"])
        if entity == "purchases":
            return delete_purchase(self.config, match["id"])
        if entity == "mowing_visits":
            return delete_mowing(self.config, match["id"])
        if entity == "products":
            return delete_product(self.config, match["name_contains"])
        raise RuntimeError(f"delete_row is not supported for entity {entity!r} on the sqlite backend")
