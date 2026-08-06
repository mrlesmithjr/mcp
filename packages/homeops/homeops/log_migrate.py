"""One-time sqlite -> markdown home_log migration (issue #58).

`homeops db log-export` seeds the markdown backend's file(s) from the
current SQLite rows -- used once when switching `home_log.backend` from
`sqlite` to `markdown`. It always reads FROM sqlite and writes TO the
markdown backend resolved from the current config (via
`home_log.markdown.note` / `home_log.markdown.entities`), regardless of
which backend `home_log.backend` is currently set to, so it also works as
a one-time snapshot before flipping the config over. Re-running it is
idempotent: each entity's markdown section is fully overwritten (not
appended to), so it always mirrors the current sqlite rows exactly.

`task_log` rows are rejoined by task name (not sqlite's task_id) when read
from the sqlite backend -- see log_backends/sqlite_backend.py -- so the
exported markdown Task Log section already references tasks the same way
the markdown backend expects on every subsequent read.

Ported from lawnops' log_migrate.py (issue #57), same contract.
"""

from __future__ import annotations

from homeops.log_backends.markdown_backend import MarkdownBackend
from homeops.log_backends.sqlite_backend import SqliteBackend
from homeops.log_schema import ENTITIES


def export_log(config: dict, preview: bool = False) -> dict[str, int]:
    """Seed the markdown backend from sqlite. Returns {entity: row_count}.

    With preview=True, counts what would be written without touching any file.
    """
    sqlite_backend = SqliteBackend(config)
    markdown_backend = MarkdownBackend(config)

    counts = {}
    for entity in ENTITIES:
        rows = sqlite_backend.read_table(entity)
        counts[entity] = len(rows)
        if not preview:
            markdown_backend.replace_table(entity, rows)
    return counts
