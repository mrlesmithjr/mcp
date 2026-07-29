"""Layered configuration loader for MCP servers.

Resolution order (highest priority wins):
  1. Environment variables (names supplied via env_map)
  2. ~/.config/<tool_name>/config.json
  3. .env file next to config.json (dev workflow fallback)

Tool-specific key maps are passed in by the caller so this module stays
generic. Each tool maintains its own mapping of config.json keys to env var
names and passes it here as env_map.

Example (tool side)::

    from mcp_common.config import load_layered_config

    _ENV_MAP = {
        "api_key": "MY_TOOL_API_KEY",
        "base_url": "MY_TOOL_BASE_URL",
    }

    def get_config() -> dict:
        return load_layered_config("my-tool", _ENV_MAP)

The returned dict contains the merged configuration. Keys present in
config.json are returned as-is (nested dicts are supported). Environment
variables override individual keys by their env_map mapping but do not
affect un-mapped keys from config.json.
"""

from __future__ import annotations

import json
import logging
import os

from mcp_common.paths import config_dir
from mcp_common.paths import config_file as _config_file

logger = logging.getLogger(__name__)


def load_layered_config(tool_name: str, env_map: dict[str, str] | None = None) -> dict:
    """Load configuration for tool_name using layered precedence.

    Parameters
    ----------
    tool_name:
        Tool identifier, e.g. "lawnops". Used to locate
        ~/.config/<tool_name>/config.json.
    env_map:
        Mapping of config.json top-level keys to environment variable names.
        Example: {"api_key": "MY_TOOL_API_KEY"}. When an env var is set it
        wins over config.json for that key. Pass None or {} to skip env
        override (config.json and .env only).

    Returns
    -------
    dict
        Merged configuration. Never raises; returns an empty dict on total
        failure (callers should validate required keys themselves).
    """
    if env_map is None:
        env_map = {}

    cfg_file = _config_file(tool_name)
    cfg_dir = config_dir(tool_name)
    result: dict = {}

    # Layer 1: config.json (baseline)
    if cfg_file.exists():
        try:
            with open(cfg_file) as fh:
                result = json.load(fh)
            logger.debug("Loaded config from %s", cfg_file)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load %s: %s", cfg_file, exc)

    # Layer 2: .env file in config dir (dev fallback; only sets missing env vars)
    env_file = cfg_dir / ".env"
    if env_file.exists():
        try:
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip())
        except OSError as exc:
            logger.warning("Could not read .env file %s: %s", env_file, exc)

    # Layer 3: environment variables (highest priority; override config.json keys)
    for config_key, env_var in env_map.items():
        value = os.environ.get(env_var)
        if value is not None and value.strip():
            result[config_key] = value.strip()

    return result
