"""One-time sqlite -> markdown lawn_log migration (issue #57).

`lawnops db log-export` seeds the markdown backend's file(s) from the
current SQLite rows -- used once when switching `lawn_log.backend` from
`sqlite` to `markdown`. It always reads FROM sqlite and writes TO the
markdown backend resolved from the current config (via
`lawn_log.markdown.note` / `lawn_log.markdown.entities`), regardless of
which backend `lawn_log.backend` is currently set to, so it also works as
a one-time snapshot before flipping the config over. Re-running it is
idempotent: each entity's markdown section is fully overwritten (not
appended to), so it always mirrors the current sqlite rows exactly.
"""

from __future__ import annotations

from lawnops.log_backends.markdown_backend import MarkdownBackend
from lawnops.log_backends.sqlite_backend import SqliteBackend
from lawnops.log_schema import ENTITIES


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
