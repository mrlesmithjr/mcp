"""Configuration for obsidian-search-tools.

All configuration is read from environment variables -- no YAML config file.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# Reindex LaunchAgent cadence: comma-separated 24-hour HH:MM times. Default is
# a few times a day, which keeps the index reasonably fresh without running
# the ~130MB embedding model too often. See scheduler.py for the plist
# rendering that turns these into a StartCalendarInterval array (issue #60).
DEFAULT_REINDEX_TIMES: tuple[str, ...] = ("06:00", "12:00", "18:00")

# Staleness guard threshold (hours) used by `reindex --skip-if-fresh`, so
# RunAtLoad firing right after a calendar-scheduled run doesn't double-embed.
DEFAULT_REINDEX_STALENESS_HOURS = 2.0

_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def get_vault_path() -> Path | None:
    """Return OBSIDIAN_VAULT_PATH as a Path, or None if unset or empty."""
    val = os.environ.get("OBSIDIAN_VAULT_PATH", "").strip()
    return Path(val).expanduser() if val else None


def get_excluded_sections() -> set[str]:
    """Return OBSIDIAN_EXCLUDED_SECTIONS as a set of top-level subdir names to skip."""
    val = os.environ.get("OBSIDIAN_EXCLUDED_SECTIONS", "").strip()
    if not val:
        return set()
    return {s.strip() for s in val.split(",") if s.strip()}


def parse_reindex_times(raw: str) -> list[str]:
    """Parse and validate a comma-separated list of 24-hour HH:MM times.

    Raises ValueError (with a descriptive message) on the first malformed
    entry. Duplicate entries are dropped; input order is preserved.
    """
    times: list[str] = []
    for chunk in raw.split(","):
        candidate = chunk.strip()
        if not candidate:
            continue
        if not _TIME_RE.match(candidate):
            raise ValueError(f"Invalid reindex time {candidate!r}; expected 24-hour HH:MM, e.g. 06:00")
        if candidate not in times:
            times.append(candidate)
    if not times:
        raise ValueError("No valid times found")
    return times


def get_reindex_times() -> list[str]:
    """Return the configured reindex fire times (24-hour HH:MM).

    Reads OBSIDIAN_REINDEX_TIMES (comma-separated). Falls back to
    DEFAULT_REINDEX_TIMES when unset, empty, or invalid -- this function never
    raises, since it backs an unattended scheduled job.
    """
    raw = os.environ.get("OBSIDIAN_REINDEX_TIMES", "").strip()
    if not raw:
        return list(DEFAULT_REINDEX_TIMES)
    try:
        return parse_reindex_times(raw)
    except ValueError:
        return list(DEFAULT_REINDEX_TIMES)


def get_reindex_staleness_hours() -> float:
    """Return the reindex staleness guard threshold, in hours.

    Reads OBSIDIAN_REINDEX_STALENESS_HOURS. Falls back to
    DEFAULT_REINDEX_STALENESS_HOURS when unset, empty, non-numeric, or <= 0.
    """
    raw = os.environ.get("OBSIDIAN_REINDEX_STALENESS_HOURS", "").strip()
    if not raw:
        return DEFAULT_REINDEX_STALENESS_HOURS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_REINDEX_STALENESS_HOURS
    return value if value > 0 else DEFAULT_REINDEX_STALENESS_HOURS
