"""Error helpers for MCP tool functions.

MCP tools must return a JSON string on every code path, including failures.
These utilities make the error path consistent and concise.

Usage::

    from mcp_common.errors import error_json, safe_tool

    @mcp.tool()
    def my_tool(x: str) -> str:
        try:
            result = do_work(x)
            return json.dumps({"status": "ok", "data": result})
        except Exception as exc:
            return error_json(exc)

    # Or use the decorator for the full pattern:

    @mcp.tool()
    @safe_tool
    def my_tool(x: str) -> str:
        result = do_work(x)
        return json.dumps({"status": "ok", "data": result})
"""

from __future__ import annotations

import functools
import json
import logging
from collections.abc import Callable
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., str])


def tool_error(message: str, **extra: Any) -> dict:
    """Return a standard error payload dict (not yet JSON-encoded).

    Parameters
    ----------
    message:
        Human-readable error description.
    **extra:
        Additional key/value pairs to include in the payload.
    """
    payload: dict[str, Any] = {"status": "error", "error": message}
    payload.update(extra)
    return payload


def error_json(exc: Exception, **extra: Any) -> str:
    """Return a JSON-encoded error string from an exception.

    Logs the exception at WARNING level so the MCP server log captures it
    without crashing.

    Parameters
    ----------
    exc:
        The caught exception.
    **extra:
        Additional key/value pairs to include alongside the error message.
    """
    logger.warning("Tool error: %s", exc, exc_info=True)
    return json.dumps(tool_error(str(exc), **extra))


def safe_tool(fn: F) -> F:
    """Decorator that wraps an MCP tool in a try/except returning error_json.

    The wrapped function must return a JSON string. If it raises, the
    exception is caught and returned as a JSON error payload.

    This decorator is applied AFTER @mcp.tool() because decorators execute
    from inner to outer; @mcp.tool() must see the unwrapped function so it
    can inspect the signature for parameter schema generation.

    Correct order::

        @mcp.tool()
        @safe_tool
        def my_tool(x: str) -> str:
            ...
    """

    @functools.wraps(fn)
    def _wrapper(*args: Any, **kwargs: Any) -> str:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            return error_json(exc)

    return _wrapper  # type: ignore[return-value]
