"""Test-session config isolation for lawnops.

lawnops.mcp_server imports lawnops.config and calls load_config() at module
import time (see lawnops/mcp_server.py:21), so anything that imports
mcp_server - including this package's own tests - requires a real config to
exist (lawnops/config.py raises RuntimeError otherwise, with no built-in
safe defaults). Tests must not depend on, read, or overwrite a developer's
real ~/.config/lawnops/config.json, so this conftest points lawnops.config
at an isolated temp file before any test can trigger that import. conftest.py
is loaded by pytest before test collection begins, so this patch is always
in place first.
"""

import json
import tempfile
from pathlib import Path

import lawnops.config as _config

_tmp_dir = Path(tempfile.mkdtemp(prefix="lawnops-test-config-"))
_tmp_config_file = _tmp_dir / "config.json"
_tmp_config_file.write_text(
    json.dumps(
        {
            "location": {
                "name": "Test City, ST",
                "latitude": 0.0,
                "longitude": 0.0,
                "yard_sqft": 10000,
                "grass_type": "bermuda",
            },
            "hydrawise": {"api_key": "test", "username": "test", "password": "test"},
            "database": {"path": str(_tmp_dir / "lawnops.db")},
        }
    )
)

_config.CONFIG_DIR = _tmp_dir
_config.CONFIG_FILE = _tmp_config_file
