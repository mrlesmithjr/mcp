"""Backend-neutral home_log store (issue #58).

Dispatches read_table/append_row/update_row/delete_row to the configured
`home_log.backend`: `sqlite` (default, zero external config) or `markdown`
(external file, source of truth when configured). The MCP tool layer in
homeops.mcp_server (plus homeops.status and homeops.ynab_bridge, which the
home_status/budget_overview/sinking_fund_plan tools depend on) is
backend-agnostic -- it only ever calls through this module (plus
homeops.log_compute for shared report/list business logic), so adding a
future backend is a third implementation here, not a tool rewrite.

Row dicts use the canonical column names in homeops.log_schema.ENTITIES,
regardless of backend. `config` is accepted (not cached) on every call, so
a `home_log` edit takes effect on the next call -- log_store itself never
caches; whether the caller's own config load is cached is a separate,
unrelated concern (homeops.mcp_server._config() caches per server
lifetime, same as before this change).

Ported from lawnops' log_store.py (issue #57), same contract.
"""

from __future__ import annotations

from homeops.log_backends.markdown_backend import MarkdownBackend
from homeops.log_backends.sqlite_backend import SqliteBackend
from homeops.log_schema import ENTITIES

_BACKENDS = {
    "sqlite": SqliteBackend,
    "markdown": MarkdownBackend,
}


def _backend(config: dict):
    name = (config.get("home_log") or {}).get("backend", "sqlite")
    cls = _BACKENDS.get(name)
    if cls is None:
        raise RuntimeError(f"Unknown home_log.backend {name!r}; expected one of {sorted(_BACKENDS)}")
    return cls(config)


def _with_defaults(entity: str, row: dict) -> dict:
    """Apply this entity's SQL-DEFAULT-mirroring defaults (homeops.log_schema)
    to any column left None in `row`. Applied once here, before dispatching to
    either backend, so sqlite and markdown emit identical rows for columns the
    sqlite schema would otherwise fill in silently."""
    merged = dict(row)
    for column, default in ENTITIES[entity].defaults.items():
        if merged.get(column) is None:
            merged[column] = default
    return merged


def read_table(config: dict, entity: str) -> list[dict]:
    """Every row for `entity`, as canonical column dicts (each carries an 'id')."""
    return _backend(config).read_table(entity)


def append_row(config: dict, entity: str, row: dict) -> dict:
    """Append `row` (canonical column dict) to `entity`. Returns the stored row."""
    return _backend(config).append_row(entity, _with_defaults(entity, row))


def update_row(config: dict, entity: str, match: dict, changes: dict) -> int:
    """Update rows in `entity` matching `match` with `changes`. Returns the count updated."""
    return _backend(config).update_row(entity, match, changes)


def delete_row(config: dict, entity: str, match: dict) -> int:
    """Delete rows in `entity` matching `match`. Returns the count deleted."""
    return _backend(config).delete_row(entity, match)
