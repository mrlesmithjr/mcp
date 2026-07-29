"""Logging configuration for MCP servers.

MCP stdio transport uses stdout as the protocol channel. All logging output
MUST go to stderr to avoid corrupting the JSON-RPC stream. This module
provides a single entry point that enforces that constraint.
"""

from __future__ import annotations

import logging
import sys


def configure_logging(
    level: int = logging.INFO,
    fmt: str = "%(asctime)s %(levelname)s %(name)s %(message)s",
) -> None:
    """Configure root logging to stderr.

    Call once at MCP server startup, before any other imports that might
    touch the root logger. Subsequent calls are safe (idempotent via
    basicConfig check).

    Parameters
    ----------
    level:
        Logging level for the root logger. Defaults to INFO.
    fmt:
        Log record format string. Defaults to timestamped level + name + message.
    """
    # Force stream=sys.stderr so stdio transport's stdout stays clean.
    logging.basicConfig(
        level=level,
        format=fmt,
        stream=sys.stderr,
        force=True,
    )
