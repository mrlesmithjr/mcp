"""Configuration loader - layered config from defaults, config.json, and env vars.

Resolution order (highest wins):
  1. Environment variables (PROMETHEUS_URL, HOMEOPS_DB_PATH)
  2. ~/.config/homeops/config.json
  3. Legacy config.yaml in project root (fallback)
  4. Built-in defaults
"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".config" / "homeops"
CONFIG_FILE = CONFIG_DIR / "config.json"

_config_cache = None

# Single source of truth for the Prometheus fallback URL - ha.py imports
# this instead of hardcoding its own copy (issue #143 code review).
DEFAULT_PROMETHEUS_URL = "http://localhost:9091"

_DEFAULTS = {
    "prometheus_url": DEFAULT_PROMETHEUS_URL,
    "database": {
        "path": "~/.local/share/homeops/homeops.db",
    },
    "reminders": {
        "list": "Personal",
        "default_time": "10:00",
    },
    "categories": {
        "tasks": [
            "hvac",
            "plumbing",
            "gutters",
            "pest",
            "electrical",
            "exterior",
            "interior",
            "safety",
            "appliance",
        ],
        "providers": [
            "hvac",
            "gutters",
            "plumbing",
            "electrical",
            "pest",
            "general",
            "generator",
            "lighting",
            "radon",
        ],
        "costs": [
            "hvac",
            "gutters",
            "pest",
            "plumbing",
            "electrical",
            "repair",
            "supplies",
            "service",
        ],
    },
}

_ENV_MAP = {
    "PROMETHEUS_URL": "prometheus_url",
}


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Merge overlay into base, handling nested dicts."""
    result = dict(base)
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config() -> dict:
    """Load HomeOps configuration with layered resolution.

    Order: defaults → legacy config.yaml → config.json → environment variables.
    """
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    config = json.loads(json.dumps(_DEFAULTS))  # deep copy

    # Layer 2: Legacy config.yaml fallback
    _project_root = Path(__file__).parent.parent
    yaml_path = _project_root / "config.yaml"
    if yaml_path.exists() and not CONFIG_FILE.exists():
        try:
            import yaml

            with open(yaml_path) as f:
                yaml_config = yaml.safe_load(f) or {}
            # Map yaml keys to our format
            if "reminders" in yaml_config:
                config["reminders"] = yaml_config["reminders"]
            if "database" in yaml_config:
                config["database"] = yaml_config["database"]
            if "categories" in yaml_config:
                config["categories"] = yaml_config["categories"]
            logger.info("Loaded legacy config from %s", yaml_path)
        except Exception as e:
            logger.warning("Failed to load legacy config.yaml: %s", e)

    # Layer 3: config.json
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                file_config = json.load(f)
            config = _deep_merge(config, file_config)
            logger.info("Loaded config from %s", CONFIG_FILE)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load %s: %s", CONFIG_FILE, e)

    # Layer 4: environment variables (highest priority)
    for env_var, config_key in _ENV_MAP.items():
        value = os.environ.get(env_var)
        if value:
            config[config_key] = value

    db_path_env = os.environ.get("HOMEOPS_DB_PATH")
    if db_path_env:
        config["database"]["path"] = db_path_env

    # Expand ~ in database path
    config["database"]["path"] = os.path.expanduser(config["database"]["path"])

    # Strip trailing slash from prometheus_url
    if config["prometheus_url"]:
        config["prometheus_url"] = config["prometheus_url"].rstrip("/")

    _config_cache = config
    return _config_cache
