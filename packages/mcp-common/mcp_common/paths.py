"""XDG-style path resolvers for MCP server config and data directories.

Tools follow the XDG Base Directory convention:
- Config: ~/.config/<tool_name>/
- Data:   ~/.local/share/<tool_name>/

Both directories are created on first access with mode 0o700 (user-only).
"""

from __future__ import annotations

import stat
from pathlib import Path


def config_dir(tool_name: str) -> Path:
    """Return (and create) ~/.config/<tool_name>/.

    Parameters
    ----------
    tool_name:
        Identifier used as the config directory name, e.g. "lawnops".
    """
    path = Path.home() / ".config" / tool_name
    path.mkdir(parents=True, exist_ok=True)
    # Ensure restrictive permissions (user-only read/write/execute).
    path.chmod(stat.S_IRWXU)
    return path


def config_file(tool_name: str) -> Path:
    """Return the path to ~/.config/<tool_name>/config.json.

    The parent directory is created if it does not exist. The file itself
    is NOT created; callers check .exists() before reading.
    """
    return config_dir(tool_name) / "config.json"


def data_dir(tool_name: str) -> Path:
    """Return (and create) ~/.local/share/<tool_name>/.

    Parameters
    ----------
    tool_name:
        Identifier used as the data directory name, e.g. "lawnops".
    """
    path = Path.home() / ".local" / "share" / tool_name
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(stat.S_IRWXU)
    return path
