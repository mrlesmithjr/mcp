"""SQLite backend for the homeops home_log (issue #58).

The default, zero-config backend. Reads use a plain `SELECT *` mapped
through the entity's canonical column names; row shaping
(days_until/overdue, age/warranty math, filtering, sorting, aggregation)
lives in homeops.log_compute so both backends produce identical MCP tool
output.

Writes delegate to the existing homeops.db CRUD functions where doing so
has no cross-entity side effect and covers the full canonical column set
(providers, appliances). Three entities are a deliberate exception:

- `costs` needs a plain insert, not `homeops.db.costs.add_cost`, because
  `add_cost` has no `source`/`source_id` parameter -- it always inserts the
  schema default `'manual'` and leaves `source_id` NULL.
  `homeops.mcp_server.task_done`/`pest_add`/`utility_add` need to write
  `source='task_log'`/`'pest_treatment'`/`'utility'` for their
  dual-written costs rows (matching the pre-#58 values
  `homeops.db.tasks.mark_done`/`db.pest.add_pest_treatment`/
  `db.utilities.add_utility_bill` used internally), and `task_done`
  additionally needs `source_id` set to the just-appended task_log row's
  id (mirroring pre-#58 `mark_done`'s `last_insert_rowid()`), so this
  backend must accept whatever `source`/`source_id` the caller supplies.
- `homeops.db.pest.add_pest_treatment` and `homeops.db.utilities.
  add_utility_bill` each also dual-write a row into `costs` and (for
  utilities) validate the utility type -- reproducing that here would only
  cover the sqlite backend, and calling those functions AND separately
  dual-writing for the markdown backend would double-count the cost under
  sqlite. So `pest_treatments`/`utility_bills` are plain inserts here, and
  the MCP tool layer (homeops.mcp_server.pest_add/utility_add)
  orchestrates the `costs` dual-write and utility-type validation once,
  identically for both backends.

`homeops.db.costs`/`db.pest`/`db.utilities` are unchanged and still used
as-is by the (unrewired) `homeops cost add`/`pest add`/`utility add` CLI
commands.

`task_log` is the one entity with no 1:1 SQL column mapping: SQLite stores
it by `task_id` FK, but the canonical/markdown column is `task` (the task's
name, unique). Reads JOIN to resolve the name; writes resolve the id by an
exact name lookup (issue #58 design -- see log_schema.py).
"""

from __future__ import annotations

from homeops.db.appliances import add_appliance
from homeops.db.connection import get_db
from homeops.db.costs import delete_cost
from homeops.db.pest import delete_pest_treatment
from homeops.db.providers import add_provider
from homeops.db.tasks import delete_task
from homeops.db.utilities import delete_utility_bill
from homeops.log_schema import ENTITIES


class SqliteBackend:
    """`sql_table` always comes from the static ENTITIES whitelist in
    homeops.log_schema, never from caller input."""

    def __init__(self, config: dict):
        self.config = config

    def read_table(self, entity: str) -> list[dict]:
        if entity == "task_log":
            # tl.* already covers this entity's passthrough columns
            # (task_id, created_at); selecting them explicitly keeps the
            # column list self-documenting alongside the other branches.
            conn = get_db(self.config)
            rows = conn.execute(
                "SELECT tl.id AS id, t.name AS task, tl.date AS date, tl.cost AS cost, "
                "tl.provider AS provider, tl.notes AS notes, "
                "tl.task_id AS task_id, tl.created_at AS created_at "
                "FROM task_log tl JOIN tasks t ON tl.task_id = t.id"
            ).fetchall()
            conn.close()
            return [dict(row) for row in rows]

        spec = ENTITIES[entity]
        conn = get_db(self.config)
        rows = conn.execute(f"SELECT * FROM {spec.sql_table}").fetchall()
        conn.close()
        out = []
        for row in rows:
            d = {"id": row["id"]}
            for canonical, sql_col in spec.sql_column_map.items():
                d[canonical] = row[sql_col]
            # sqlite-only metadata (created_at, costs.source_id) that
            # pre-#58's direct-SQL reads always returned via `SELECT *` --
            # see EntitySchema.passthrough's docstring.
            for col in spec.passthrough:
                d[col] = row[col]
            out.append(d)
        return out

    def append_row(self, entity: str, row: dict) -> dict:
        config = self.config
        if entity == "tasks":
            conn = get_db(config)
            try:
                conn.execute(
                    "INSERT INTO tasks (name, category, interval_days, last_done, next_due, active, notes) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        row["name"],
                        row["category"],
                        row["interval_days"],
                        row.get("last_done"),
                        row.get("next_due"),
                        row.get("active", 1),
                        row.get("notes"),
                    ),
                )
                conn.commit()
            except Exception as e:
                conn.close()
                if "UNIQUE" in str(e):
                    raise RuntimeError(f"Task '{row['name']}' already exists.") from e
                raise
            else:
                conn.close()
        elif entity == "task_log":
            conn = get_db(config)
            task_row = conn.execute("SELECT id FROM tasks WHERE name = ?", (row["task"],)).fetchone()
            if task_row is None:
                conn.close()
                raise RuntimeError(f"No task named '{row['task']}' -- cannot log a task_log entry against it.")
            cursor = conn.execute(
                "INSERT INTO task_log (task_id, date, cost, provider, notes) VALUES (?, ?, ?, ?, ?)",
                (task_row["id"], row["date"], row.get("cost"), row.get("provider"), row.get("notes")),
            )
            conn.commit()
            new_id = cursor.lastrowid
            conn.close()
            # Surfaces the new task_log row's own id so callers (task_done)
            # can pass it as costs.source_id for the dual-written cost row,
            # mirroring pre-#58 mark_done's `last_insert_rowid()` (issue #58
            # code review finding MAJOR).
            return {**row, "id": new_id}
        elif entity == "pest_treatments":
            conn = get_db(config)
            conn.execute(
                "INSERT INTO pest_treatments (date, area, product, method, notes, cost) VALUES (?, ?, ?, ?, ?, ?)",
                (row["date"], row["area"], row["product"], row.get("method"), row.get("notes"), row.get("cost")),
            )
            conn.commit()
            conn.close()
        elif entity == "costs":
            conn = get_db(config)
            conn.execute(
                "INSERT INTO costs (date, category, amount, provider, description, source, source_id, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["date"],
                    row["category"],
                    row["amount"],
                    row.get("provider"),
                    row.get("description"),
                    row.get("source") or "manual",
                    row.get("source_id"),
                    row.get("notes"),
                ),
            )
            conn.commit()
            conn.close()
        elif entity == "utility_bills":
            conn = get_db(config)
            conn.execute(
                "INSERT INTO utility_bills (bill_date, type, amount, usage, notes) VALUES (?, ?, ?, ?, ?)",
                (row["bill_date"], row["type"], row["amount"], row.get("usage"), row.get("notes")),
            )
            conn.commit()
            conn.close()
        elif entity == "providers":
            add_provider(
                config,
                row["name"],
                row["category"],
                phone=row.get("phone"),
                email=row.get("email"),
                typical_cost=row.get("typical_cost"),
                notes=row.get("notes"),
            )
        elif entity == "appliances":
            add_appliance(
                config,
                row["name"],
                row["category"],
                brand=row.get("brand"),
                model=row.get("model"),
                purchased=row.get("purchase_date"),
                warranty_end=row.get("warranty_end"),
                expected_lifespan_years=row.get("expected_lifespan_years"),
                replacement_cost=row.get("replacement_cost"),
                location=row.get("location"),
                notes=row.get("notes"),
            )
        else:
            raise RuntimeError(f"Unknown home_log entity: {entity!r}")
        return row

    def update_row(self, entity: str, match: dict, changes: dict) -> int:
        if entity == "tasks":
            if "id" not in match:
                raise RuntimeError("tasks update_row requires an id match on the sqlite backend")
            allowed = set(ENTITIES["tasks"].columns)
            if not set(changes) <= allowed:
                raise RuntimeError(f"Unsupported tasks columns in update_row: {set(changes) - allowed}")
            if not changes:
                return 0
            conn = get_db(self.config)
            set_clause = ", ".join(f"{col} = ?" for col in changes)
            params = [*changes.values(), match["id"]]
            result = conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", params)
            conn.commit()
            rowcount = result.rowcount
            conn.close()
            return rowcount
        raise RuntimeError(f"update_row is not supported for entity {entity!r} on the sqlite backend")

    def delete_row(self, entity: str, match: dict) -> int:
        if entity == "tasks":
            return delete_task(self.config, match["name_contains"])
        if entity == "pest_treatments":
            return int(delete_pest_treatment(self.config, match["id"]))
        if entity == "costs":
            return int(delete_cost(self.config, match["id"]))
        if entity == "utility_bills":
            return int(delete_utility_bill(self.config, match["id"]))
        raise RuntimeError(f"delete_row is not supported for entity {entity!r} on the sqlite backend")
