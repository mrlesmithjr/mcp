"""Canonical entity schemas shared by both lawn_log backends (issue #57).

The column set and order per entity is fixed by design (see the Tier 2 spec
on issue #57): only backend selection, file routing, section headings, and
date_format are user-configurable. Keeping the schema in one place is what
lets lawnops.log_store, lawnops.log_compute, and the markdown/sqlite backends
all agree on field names without duplicating the contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class EntitySchema:
    """One row-shape contract, shared by the sqlite and markdown backends."""

    columns: tuple[str, ...]
    heading: str
    sql_table: str
    sql_column_map: dict  # canonical column name -> sqlite column name
    numeric_columns: frozenset = field(default_factory=frozenset)
    date_columns: frozenset = field(default_factory=frozenset)
    # Mirrors this entity's SQL column DEFAULTs (schema.py), for columns that
    # also exist in the markdown table. Applied by lawnops.log_store.append_row
    # before dispatching to either backend, so a value the sqlite schema would
    # have filled in silently is not left None under markdown (issue #57 review
    # finding MAJOR 1). Timestamp columns (created_at/updated_at) are not
    # markdown columns at all, so they are not listed here.
    defaults: dict = field(default_factory=dict)


ENTITIES: dict[str, EntitySchema] = {
    "treatments": EntitySchema(
        columns=("date", "area", "product", "method", "amount", "soil_temp_f", "cost", "notes"),
        heading="Treatments",
        sql_table="treatments",
        sql_column_map={
            "date": "date",
            "area": "treatment_area",
            "product": "product",
            "method": "method",
            "amount": "amount",
            "soil_temp_f": "soil_temp_f",
            "cost": "cost",
            "notes": "notes",
        },
        numeric_columns=frozenset({"soil_temp_f", "cost"}),
        date_columns=frozenset({"date"}),
    ),
    "products": EntitySchema(
        columns=("name", "category", "qty_on_hand", "unit", "last_ordered", "cost_each", "source", "notes"),
        heading="Products",
        sql_table="products",
        sql_column_map={
            c: c for c in ("name", "category", "qty_on_hand", "unit", "last_ordered", "cost_each", "source", "notes")
        },
        numeric_columns=frozenset({"qty_on_hand", "cost_each"}),
        date_columns=frozenset({"last_ordered"}),
        defaults={"qty_on_hand": 0, "unit": "bag"},
    ),
    "equipment": EntitySchema(
        columns=("name", "purchase_date", "cost", "source", "status", "notes"),
        heading="Equipment",
        sql_table="equipment",
        sql_column_map={c: c for c in ("name", "purchase_date", "cost", "source", "status", "notes")},
        numeric_columns=frozenset({"cost"}),
        date_columns=frozenset({"purchase_date"}),
        defaults={"status": "active"},
    ),
    "purchases": EntitySchema(
        columns=("date", "item", "category", "qty", "cost", "source", "notes"),
        heading="Purchases",
        sql_table="purchases",
        sql_column_map={c: c for c in ("date", "item", "category", "qty", "cost", "source", "notes")},
        numeric_columns=frozenset({"qty", "cost"}),
        date_columns=frozenset({"date"}),
        defaults={"qty": 1},
    ),
    "mowing_visits": EntitySchema(
        columns=("date", "provider", "cost", "notes"),
        heading="Mowing",
        sql_table="mowing_visits",
        sql_column_map={c: c for c in ("date", "provider", "cost", "notes")},
        numeric_columns=frozenset({"cost"}),
        date_columns=frozenset({"date"}),
    ),
    "seasonal_tasks": EntitySchema(
        columns=("season", "task", "timing", "product", "notes"),
        heading="Seasonal Tasks",
        sql_table="seasonal_tasks",
        sql_column_map={
            "season": "season",
            "task": "task_name",
            "timing": "timing",
            "product": "product",
            "notes": "notes",
        },
        numeric_columns=frozenset(),
        date_columns=frozenset(),
    ),
}
