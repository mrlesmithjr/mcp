"""MCP server exposing iMessage as callable tools for Claude Code.

Reads from chat.db (SQLite), sends via AppleScript. Access-controlled via allowlist.
"""

import json
import logging
import sys

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

mcp = FastMCP(
    "imessage-tools",
    instructions=(
        "Use chat_list to discover available conversations. "
        "Use chat_messages to read a conversation by chat_id. "
        "Use send_message with a phone number or email to send. "
        "All operations are scoped to the allowlist in access.json."
    ),
)

# MCP tool annotations follow the workspace convention in the root CLAUDE.md.
# openWorldHint is set explicitly because the spec default is true (open world).
# Reads hit the local chat.db and the allowlist tools touch a local JSON file, so
# those are closed-world (openWorldHint=False). send_message is the exception: it
# delivers content to an external recipient, so it is open-world (matching the
# mail-tools send path).
_READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)

# ── Messages Manager ──

_manager = None


def _msgs():
    """Lazy-init the MessagesManager."""
    global _manager
    if _manager is None:
        from imessage_tools.messages import MessagesManager

        _manager = MessagesManager()
    return _manager


# ── Tools ──


@mcp.tool(annotations=_READ_ONLY)
def chat_list(days: int = 30) -> str:
    """List recent conversations with last message date. Scoped to allowlist.

    Args:
        days: Only show chats active in the last N days (default: 30)

    Returns JSON: {chats: [{chat_id, identifier, display_name, type, service,
    last_message}], count}
    """
    try:
        chats = _msgs().chat_list(days)
        return json.dumps({"chats": chats, "count": len(chats)})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_ONLY)
def chat_messages(chat_id: str, limit: int = 50) -> str:
    """Read messages from a conversation. Use chat_list to find chat_id values.

    Args:
        chat_id: Chat GUID from chat_list (e.g. "iMessage;-;+16785551234")
        limit: Maximum messages to return (default: 50)

    Returns JSON: {chat_id, messages: [{id, text, date, from_me, sender,
    has_attachments}], count}
    """
    try:
        messages = _msgs().chat_messages(chat_id, limit)
        return json.dumps({"chat_id": chat_id, "messages": messages, "count": len(messages)})
    except PermissionError as e:
        return json.dumps({"error": str(e), "hint": "Handle not in allowlist"})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
def send_message(to: str, text: str, files: list[str] | None = None) -> str:
    """Send an iMessage to a person or group chat.

    Args:
        to: Chat ID (from chat_list, for group chats), contact name (e.g. "Jane Doe"),
            phone number (e.g. "+16785551234"), or email
        text: Message text to send
        files: Optional list of absolute file paths to attach as separate messages

    Returns JSON: {sent: true, to, text, chat_name?, files?}
    """
    try:
        result = _msgs().send_message(to, text, files)
        return json.dumps(result)
    except PermissionError as e:
        return json.dumps({"error": str(e), "hint": "Add handle to allowlist"})
    except ValueError as e:
        return json.dumps({"error": str(e)})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_ONLY)
def unread() -> str:
    """Get chats with unread messages. Scoped to allowlist.

    Returns JSON: {chats: [{chat_id, display_name, unread_count,
    latest_message}], count}
    """
    try:
        chats = _msgs().unread_summary()
        return json.dumps({"chats": chats, "count": len(chats)})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_ONLY)
def search_messages(query: str, limit: int = 20) -> str:
    """Search message text across all allowed conversations.

    Args:
        query: Text to search for (case-insensitive substring match)
        limit: Maximum results (default: 20)

    Returns JSON: {query, messages: [{message_id, chat_id, chat_name, text,
    date, from_me, sender}], count}
    """
    try:
        messages = _msgs().search_messages(query, limit)
        return json.dumps({"query": query, "messages": messages, "count": len(messages)})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_ONLY)
def access_list() -> str:
    """Show all handles (phone numbers/emails) currently in the allowlist.

    Returns JSON: {allow: [str], count}
    """
    try:
        allowed = _msgs().access_list()
        return json.dumps({"allow": allowed, "count": len(allowed)})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
def access_add(handle: str) -> str:
    """Add a phone number or email to the allowlist. Takes effect immediately.

    Args:
        handle: Phone number (e.g. "+16785551234") or email address

    Returns JSON: {added: bool, handle, reason?}
    """
    try:
        result = _msgs().access_add(handle)
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
def access_remove(handle: str) -> str:
    """Remove a phone number or email from the allowlist. Takes effect immediately.

    Args:
        handle: Phone number (e.g. "+16785551234") or email address

    Returns JSON: {removed: bool, handle, reason?}
    """
    try:
        result = _msgs().access_remove(handle)
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})


def main():
    logger.info("Starting imessage-tools MCP server")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
