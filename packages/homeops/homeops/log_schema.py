"""Canonical entity schemas shared by both home_log backends (issue #58).

The column set and order per entity is fixed by design (see the Tier 2 spec
on issue #58): only backend selection, file routing, section headings, and
date_format are user-configurable. Keeping the schema in one place is what
lets homeops.log_store, homeops.log_compute, and the markdown/sqlite
backends all agree on field names without duplicating the contract.

Two homeops-specific format notes vs. the lawnops #57 reference this
package mirrors:

- `utility_bills.bill_date` is a YYYY-MM month, not a full YYYY-MM-DD date,
  so it is deliberately NOT listed in `date_columns` -- the shared
  date_format machinery (`_parse_display_date`/`_format_display_date` in
  the markdown backend) assumes a full date and would reject a bare month.
  It is stored and displayed as plain text either way.
- `appliances.purchase_date`/`warranty_end` accept either YYYY-MM-DD or
  YYYY-MM (`homeops.db.appliances._parse_date` already handles both), so
  they are also excluded from `date_columns` and passed through verbatim
  rather than forced through one fixed display format.

See `EntitySchema.passthrough` below for `created_at`/`source_id`/`task_id`:
sqlite-only metadata columns that are surfaced on read (real value on
sqlite, `None` on markdown) without being added to the markdown table.
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
    # Mirrors this entity's SQL column DEFAULTs (db/schema.py), for columns
    # that also exist in the markdown table. Applied by
    # homeops.log_store.append_row before dispatching to either backend, so
    # a value the sqlite schema would have filled in silently is not left
    # None under markdown (mirrors lawnops #57 review finding MAJOR 1).
    defaults: dict = field(default_factory=dict)
    # sqlite-only metadata columns (e.g. created_at, costs.source_id,
    # task_log.task_id) that pre-#58's direct-SQL read functions always
    # returned (`SELECT *` / `dict(row)`) but that are deliberately NOT part
    # of the markdown table's clean column set -- adding them there would
    # pollute a human-edited note with implementation detail no one edits.
    # SqliteBackend.read_table emits these with their real value;
    # MarkdownBackend.read_table emits the same keys with value None, so the
    # KEY SET returned by read_table is identical on both backends and the
    # default sqlite backend's output is unchanged from pre-#58 (issue #58
    # code review finding CRITICAL).
    passthrough: tuple[str, ...] = ()


ENTITIES: dict[str, EntitySchema] = {
    "tasks": EntitySchema(
        columns=("name", "category", "interval_days", "last_done", "next_due", "active", "notes"),
        heading="Tasks",
        sql_table="tasks",
        sql_column_map={
            c: c for c in ("name", "category", "interval_days", "last_done", "next_due", "active", "notes")
        },
        numeric_columns=frozenset({"interval_days", "active"}),
        date_columns=frozenset({"last_done", "next_due"}),
        defaults={"active": 1},
        passthrough=("created_at",),
    ),
    "task_log": EntitySchema(
        # References its task by name, not by SQLite's task_id -- the
        # sqlite backend resolves the id via a JOIN (read) / name lookup
        # (write); see log_backends/sqlite_backend.py. `task_id` itself is
        # still surfaced as sqlite-only passthrough metadata (pre-#58
        # get_task_history's `tl.*` included it).
        columns=("task", "date", "cost", "provider", "notes"),
        heading="Task Log",
        sql_table="task_log",
        sql_column_map={c: c for c in ("task", "date", "cost", "provider", "notes")},
        numeric_columns=frozenset({"cost"}),
        date_columns=frozenset({"date"}),
        passthrough=("task_id", "created_at"),
    ),
    "pest_treatments": EntitySchema(
        columns=("date", "area", "product", "method", "cost", "notes"),
        heading="Pest",
        sql_table="pest_treatments",
        sql_column_map={c: c for c in ("date", "area", "product", "method", "cost", "notes")},
        numeric_columns=frozenset({"cost"}),
        date_columns=frozenset({"date"}),
        passthrough=("created_at",),
    ),
    "costs": EntitySchema(
        columns=("date", "category", "amount", "provider", "description", "source", "notes"),
        heading="Costs",
        sql_table="costs",
        sql_column_map={c: c for c in ("date", "category", "amount", "provider", "description", "source", "notes")},
        numeric_columns=frozenset({"amount"}),
        date_columns=frozenset({"date"}),
        defaults={"source": "manual"},
        passthrough=("source_id", "created_at"),
    ),
    "utility_bills": EntitySchema(
        columns=("bill_date", "type", "amount", "usage", "notes"),
        heading="Utilities",
        sql_table="utility_bills",
        sql_column_map={c: c for c in ("bill_date", "type", "amount", "usage", "notes")},
        numeric_columns=frozenset({"amount"}),
        date_columns=frozenset(),  # bill_date is YYYY-MM -- see module docstring
        passthrough=("created_at",),
    ),
    "providers": EntitySchema(
        columns=("name", "category", "phone", "email", "typical_cost", "active", "notes"),
        heading="Providers",
        sql_table="providers",
        sql_column_map={c: c for c in ("name", "category", "phone", "email", "typical_cost", "active", "notes")},
        numeric_columns=frozenset({"typical_cost", "active"}),
        date_columns=frozenset(),
        defaults={"active": 1},
        passthrough=("created_at",),
    ),
    "appliances": EntitySchema(
        columns=(
            "name",
            "category",
            "brand",
            "model",
            "purchase_date",
            "warranty_end",
            "expected_lifespan_years",
            "replacement_cost",
            "location",
            "notes",
        ),
        heading="Appliances",
        sql_table="appliances",
        sql_column_map={
            c: c
            for c in (
                "name",
                "category",
                "brand",
                "model",
                "purchase_date",
                "warranty_end",
                "expected_lifespan_years",
                "replacement_cost",
                "location",
                "notes",
            )
        },
        numeric_columns=frozenset({"expected_lifespan_years", "replacement_cost"}),
        date_columns=frozenset(),  # mixed YYYY-MM-DD/YYYY-MM -- see module docstring
        passthrough=("created_at",),
    ),
}
