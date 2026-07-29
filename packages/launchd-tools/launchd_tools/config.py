"""Configuration loader for launchd-tools.

Resolution order (highest wins):
  1. LAUNCHD_TOOLS_LABEL_PREFIXES environment variable (comma-separated)
  2. ~/.config/launchd_tools/config.yaml  (label_prefixes list)
  3. Built-in defaults

This module NEVER raises. Defaults are always valid, so the tool works
with zero setup in briefing context.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

CONFIG_PATH = Path.home() / ".config" / "launchd_tools" / "config.yaml"

_DEFAULT_LABEL_PREFIXES: list[str] = [
    "com.homeops",
    "com.lawnops",
    "com.ynab-tools",
    "com.mrlesmithjr",
    "com.methodicalcloud",
    "com.larrysmithjr",
]

_label_prefixes_cache: list[str] | None = None


def _load_yaml_prefixes() -> list[str] | None:
    """Return label_prefixes from config.yaml, or None if absent/invalid."""
    if not CONFIG_PATH.exists():
        return None
    try:
        import yaml  # only import when the file is present

        with CONFIG_PATH.open() as fh:
            data = yaml.safe_load(fh) or {}
        raw = data.get("label_prefixes")
        if isinstance(raw, list) and raw:
            return [str(p) for p in raw]
    except Exception as exc:
        logger.warning("Failed to load %s: %s", CONFIG_PATH, exc)
    return None


def get_label_prefixes() -> list[str]:
    """Return the active LABEL_PREFIXES list.

    Layered: defaults -> config.yaml -> env var. Never raises.
    """
    global _label_prefixes_cache
    if _label_prefixes_cache is not None:
        return _label_prefixes_cache

    # Layer 1: defaults
    prefixes = list(_DEFAULT_LABEL_PREFIXES)

    # Layer 2: config.yaml (replaces defaults when present)
    yaml_prefixes = _load_yaml_prefixes()
    if yaml_prefixes is not None:
        prefixes = yaml_prefixes

    # Layer 3: env var (highest priority, replaces all)
    env_val = os.environ.get("LAUNCHD_TOOLS_LABEL_PREFIXES", "").strip()
    if env_val:
        env_prefixes = [p.strip() for p in env_val.split(",") if p.strip()]
        if env_prefixes:
            prefixes = env_prefixes

    _label_prefixes_cache = prefixes
    return _label_prefixes_cache


def reset_cache() -> None:
    """Reset config cache. For testing only."""
    global _label_prefixes_cache
    _label_prefixes_cache = None
