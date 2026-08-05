"""Backend-neutral agronomic log store (issue #57).

Dispatches read_table/append_row/update_row/delete_row to the configured
`lawn_log.backend`: `sqlite` (default, zero external config) or `markdown`
(external file, source of truth when configured). The MCP tool layer in
lawnops.mcp_server is backend-agnostic -- it only ever calls through this
module (plus lawnops.log_compute for shared report/list business logic), so
adding a future backend is a third implementation here, not a tool rewrite.

Row dicts use the canonical column names in lawnops.log_schema.ENTITIES,
regardless of backend. `config` is accepted (not cached) on every call to
match the rest of lawnops: config is loaded fresh per MCP tool invocation
(see lawnops/config.py), so a `lawn_log` edit takes effect on the next call
with no server restart.
"""

from __future__ import annotations

from lawnops.log_backends.markdown_backend import MarkdownBackend
from lawnops.log_backends.sqlite_backend import SqliteBackend
from lawnops.log_schema import ENTITIES

_BACKENDS = {
    "sqlite": SqliteBackend,
    "markdown": MarkdownBackend,
}


def _backend(config: dict):
    name = (config.get("lawn_log") or {}).get("backend", "sqlite")
    cls = _BACKENDS.get(name)
    if cls is None:
        raise RuntimeError(f"Unknown lawn_log.backend {name!r}; expected one of {sorted(_BACKENDS)}")
    return cls(config)


def _with_defaults(entity: str, row: dict) -> dict:
    """Apply this entity's SQL-DEFAULT-mirroring defaults (lawnops.log_schema)
    to any column left None in `row`. Applied once here, before dispatching to
    either backend, so sqlite and markdown emit identical rows for columns the
    sqlite schema would otherwise fill in silently (issue #57 review MAJOR 1)."""
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
