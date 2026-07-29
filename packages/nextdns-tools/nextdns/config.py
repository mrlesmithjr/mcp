"""Configuration loader - layered config from defaults, config.json, and env vars.

Resolution order (highest wins):
  1. Environment variables (NEXTDNS_API_KEY, NEXTDNS_PROFILE_ID)
  2. ~/.config/nextdns-tools/config.json
  3. Built-in defaults
"""

import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".config" / "nextdns-tools"
CONFIG_FILE = CONFIG_DIR / "config.json"

_config_cache = None

_DEFAULTS = {
    "api_key": "",
    "profile_id": "",
}

_ENV_MAP = {
    "NEXTDNS_API_KEY": "api_key",
    "NEXTDNS_PROFILE_ID": "profile_id",
}


def load_config() -> dict:
    """Load NextDNS configuration with layered resolution.

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
            config.update({k: v for k, v in file_config.items() if v})
            logger.info("Loaded config from %s", CONFIG_FILE)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load %s: %s", CONFIG_FILE, e)

    # Layer 3: environment variables (highest priority)
    for env_var, config_key in _ENV_MAP.items():
        value = os.environ.get(env_var)
        if value:
            config[config_key] = value

    # Validate required fields
    missing = [k for k in ("api_key", "profile_id") if not config.get(k)]
    if missing:
        print(
            f"NextDNS configuration incomplete - missing: {', '.join(missing)}\n"
            f"\n"
            f"Option 1: Create {CONFIG_FILE}\n"
            f"  mkdir -p {CONFIG_DIR}\n"
            f"  cat > {CONFIG_FILE} << 'EOF'\n"
            f"  {{\n"
            f'    "api_key": "your-nextdns-api-key",\n'
            f'    "profile_id": "your-profile-id"\n'
            f"  }}\n"
            f"  EOF\n"
            f"\n"
            f"Option 2: Set environment variables\n"
            f"  export NEXTDNS_API_KEY=your-api-key\n"
            f"  export NEXTDNS_PROFILE_ID=your-profile-id\n",
            file=sys.stderr,
        )
        raise RuntimeError(f"Missing required config: {', '.join(missing)}. Set them in {CONFIG_FILE}")

    _config_cache = config
    return _config_cache
