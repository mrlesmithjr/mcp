"""Configuration loader - layered config from defaults, config.json, and env vars.

Resolution order (highest wins):
  1. Environment variables (UNIFI_HOST, UNIFI_USERNAME, etc.)
  2. ~/.config/unifi-tools/config.json
  3. Built-in defaults
"""

import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".config" / "unifi-tools"
CONFIG_FILE = CONFIG_DIR / "config.json"

_config_cache = None

_DEFAULTS = {
    "host": "",
    "username": "",
    "password": "",
    "api_key": "",
    "port": 443,
    "site": "default",
    "verify_ssl": False,
    "zone_map": {},
}

_ENV_MAP = {
    "UNIFI_HOST": "host",
    "UNIFI_USERNAME": "username",
    "UNIFI_PASSWORD": "password",
    "UNIFI_API_KEY": "api_key",
    "UNIFI_PORT": "port",
    "UNIFI_SITE": "site",
    "UNIFI_VERIFY_SSL": "verify_ssl",
    "UNIFI_ZONE_MAP": "zone_map",
}


def load_config() -> dict:
    """Load UniFi configuration with layered resolution.

    Order: defaults → config.json → environment variables.
    """
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    config = dict(_DEFAULTS)

    # Layer 2: config.json
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                file_config = json.load(f)
            config.update({k: v for k, v in file_config.items() if k in _DEFAULTS})
            logger.info("Loaded config from %s", CONFIG_FILE)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load %s: %s", CONFIG_FILE, e)

    # Layer 3: environment variables (highest priority)
    for env_var, config_key in _ENV_MAP.items():
        value = os.environ.get(env_var)
        if value:
            config[config_key] = value

    # Type coercion
    if isinstance(config["port"], str):
        config["port"] = int(config["port"])
    if isinstance(config["verify_ssl"], str):
        config["verify_ssl"] = config["verify_ssl"].lower() == "true"
    if isinstance(config["zone_map"], str):
        try:
            config["zone_map"] = json.loads(config["zone_map"])
        except json.JSONDecodeError:
            config["zone_map"] = {}

    # Strip trailing slash from host
    if config["host"]:
        config["host"] = config["host"].rstrip("/")

    # Validate required fields
    missing = [k for k in ("host", "username", "password") if not config.get(k)]
    if missing:
        print(
            f"UniFi configuration incomplete - missing: {', '.join(missing)}\n"
            f"\n"
            f"Option 1: Create {CONFIG_FILE}\n"
            f"  mkdir -p {CONFIG_DIR}\n"
            f"  cat > {CONFIG_FILE} << 'EOF'\n"
            f"  {{\n"
            f'    "host": "192.168.1.1",\n'
            f'    "username": "admin",\n'
            f'    "password": "your-password",\n'
            f'    "port": 443,\n'
            f'    "site": "default",\n'
            f'    "verify_ssl": false\n'
            f"  }}\n"
            f"  EOF\n"
            f"\n"
            f"Option 2: Set environment variables\n"
            f"  export UNIFI_HOST=192.168.1.1\n"
            f"  export UNIFI_USERNAME=admin\n"
            f"  export UNIFI_PASSWORD=your-password\n",
            file=sys.stderr,
        )
        raise RuntimeError(f"Missing required config: {', '.join(missing)}. Set them in {CONFIG_FILE}")

    _config_cache = config
    return _config_cache
