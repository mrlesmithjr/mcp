"""LaunchAgent scheduling helpers for the reindex job (issue #60).

Two responsibilities:
  - Render the packaged plist's __START_CALENDAR_INTERVAL__ placeholder into a
    real StartCalendarInterval array, one <dict> entry per configured HH:MM
    time. Called by launchagents/render.sh at install time, after __HOME__
    substitution.
  - Answer "is the index stale enough to justify a rebuild", used by
    `reindex --skip-if-fresh` so RunAtLoad firing right after (or right
    before) a calendar-scheduled run doesn't double-embed the vault.

Kept separate from indexer.py/searcher.py, which own retrieval and are out of
scope for this change.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

_PLACEHOLDER = "__START_CALENDAR_INTERVAL__"


def render_calendar_interval_xml(times: list[str]) -> str:
    """Render the <key>StartCalendarInterval</key><array>...</array> block.

    One <dict> entry per HH:MM time. Indentation (tabs) matches the shipped
    plist template's style.
    """
    entries = []
    for t in times:
        hour_str, minute_str = t.split(":")
        entries.append(
            "\t\t<dict>\n"
            "\t\t\t<key>Hour</key>\n"
            f"\t\t\t<integer>{int(hour_str)}</integer>\n"
            "\t\t\t<key>Minute</key>\n"
            f"\t\t\t<integer>{int(minute_str)}</integer>\n"
            "\t\t</dict>"
        )
    body = "\n".join(entries)
    return f"\t<key>StartCalendarInterval</key>\n\t<array>\n{body}\n\t</array>"


def render_plist(text: str, times: list[str]) -> str:
    """Substitute the packaged __START_CALENDAR_INTERVAL__ placeholder in text.

    __HOME__ substitution happens upstream (install_deps.sh's sed pass)
    before this runs; this function only touches the schedule placeholder.
    """
    return text.replace(_PLACEHOLDER, render_calendar_interval_xml(times))


def get_last_reindex(db_path: Path | None = None) -> str | None:
    """Return the last_reindex UTC ISO timestamp from index_meta, or None.

    None covers both "never indexed" (no DB yet) and "no last_reindex key"
    (DB exists but is empty).
    """
    from obsidian_search_tools.db import connect, get_db_path

    path = db_path or get_db_path()
    if not path.exists():
        return None
    conn = connect(path)
    try:
        row = conn.execute("SELECT value FROM index_meta WHERE key = 'last_reindex'").fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def is_stale(last_reindex: str | None, staleness_hours: float) -> bool:
    """Return True if last_reindex is missing/unparseable or older than staleness_hours.

    last_reindex is the UTC ISO timestamp indexer.py stores in index_meta.
    """
    if not last_reindex:
        return True
    try:
        last = datetime.fromisoformat(last_reindex)
    except ValueError:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last) >= timedelta(hours=staleness_hours)
