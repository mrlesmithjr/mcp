"""Configuration loader - layered config from defaults, config.json, and env vars.

Resolution order (highest wins):
  1. Environment variables (HYDRAWISE_API_KEY, HYDRAWISE_USERNAME, HYDRAWISE_PASSWORD, LAWNOPS_DB_PATH)
  2. ~/.config/lawnops/config.json
  3. Legacy config.yaml in project root (fallback)
  4. Built-in defaults
"""

import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".config" / "lawnops"
CONFIG_FILE = CONFIG_DIR / "config.json"

_ENV_MAP = {
    "HYDRAWISE_API_KEY": ("hydrawise", "api_key"),
    "HYDRAWISE_USERNAME": ("hydrawise", "username"),
    "HYDRAWISE_PASSWORD": ("hydrawise", "password"),
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


def load_config(base_dir=None) -> dict:
    """Load LawnOps configuration with layered resolution.

    Order: defaults → legacy config.yaml → config.json → environment variables.

    Always reads from disk - no caching. This ensures config edits (including
    those made by irrigation_budget_update) take effect on the next call without
    requiring a server restart.

    Args:
        base_dir: Legacy parameter for config.yaml location. Ignored when
                  config.json exists. Defaults to project root.
    """
    config = {}

    # Layer 1: Legacy config.yaml fallback
    if base_dir is None:
        base_dir = Path(__file__).parent.parent
    else:
        base_dir = Path(base_dir)

    yaml_path = base_dir / "config.yaml"
    if yaml_path.exists() and not CONFIG_FILE.exists():
        try:
            import yaml

            with open(yaml_path) as f:
                config = yaml.safe_load(f) or {}
            logger.info("Loaded legacy config from %s", yaml_path)
        except Exception as e:
            logger.warning("Failed to load legacy config.yaml: %s", e)

    # Layer 2: config.json (overrides yaml if both exist)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                file_config = json.load(f)
            if config:
                config = _deep_merge(config, file_config)
            else:
                config = file_config
            logger.info("Loaded config from %s", CONFIG_FILE)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load %s: %s", CONFIG_FILE, e)

    # Layer 3: environment variables (highest priority)
    for env_var, key_path in _ENV_MAP.items():
        value = os.environ.get(env_var)
        if value:
            section, key = key_path
            config.setdefault(section, {})[key] = value

    db_path_env = os.environ.get("LAWNOPS_DB_PATH")
    if db_path_env:
        config.setdefault("database", {})["path"] = db_path_env

    # Expand ~ in paths
    if "database" in config and "path" in config["database"]:
        config["database"]["path"] = os.path.expanduser(config["database"]["path"])
    if "ynab" in config and "db_path" in config["ynab"]:
        config["ynab"]["db_path"] = os.path.expanduser(config["ynab"]["db_path"])

    if not config:
        print(
            f"LawnOps configuration not found.\n"
            f"\n"
            f"Create {CONFIG_FILE} with at minimum:\n"
            f"  mkdir -p {CONFIG_DIR}\n"
            f"  cat > {CONFIG_FILE} << 'EOF'\n"
            f"  {{\n"
            f'    "location": {{\n'
            f'      "name": "City, ST",\n'
            f'      "latitude": 0.0,\n'
            f'      "longitude": 0.0,\n'
            f'      "yard_sqft": 10000,\n'
            f'      "grass_type": "bermuda"\n'
            f"    }},\n"
            f'    "hydrawise": {{\n'
            f'      "api_key": "your-api-key",\n'
            f'      "username": "your-email",\n'
            f'      "password": "your-password"\n'
            f"    }},\n"
            f'    "database": {{"path": "~/.local/share/lawnops/lawnops.db"}}\n'
            f"  }}\n"
            f"  EOF\n"
            f"\n"
            f"See the README for the full config reference.\n",
            file=sys.stderr,
        )
        raise RuntimeError("No configuration found")

    return config
