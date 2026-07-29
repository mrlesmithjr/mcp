"""Configuration for obsidian-search-tools.

All configuration is read from environment variables -- no YAML config file.
"""

from __future__ import annotations

import os
from pathlib import Path


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
