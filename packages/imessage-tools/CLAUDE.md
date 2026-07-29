# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Status**: ACTIVE
**Last Updated**: April 15, 2026

## Overview

iMessage MCP server for Claude Code. Reads conversations from `~/Library/Messages/chat.db` (SQLite) and sends messages via AppleScript. Access-controlled via an allowlist at `~/.config/imessage-tools/access.json` - all operations are scoped to approved handles only.

## Setup

```bash
# Install
pip install -e .

# Register MCP server (user scope)
claude mcp add -s user imessage -- imessage-mcp
```

**Required macOS permissions** (System Settings > Privacy):
- Full Disk Access (or Messages access) for `chat.db` reads
- Automation access to Messages.app for sends

**Allowlist setup**: create `~/.config/imessage-tools/access.json` with approved handles:
```json
{
  "allow": ["+16785551234", "user@example.com"]
}
```

## Architecture

```
imessage_tools/
├── __init__.py
├── __main__.py
├── messages.py      # MessagesManager - SQLite reads + AppleScript sends
├── contacts.py      # ContactResolver - maps handles to display names
└── mcp_server.py    # FastMCP server (JSON output)
```

**Data access**: `~/Library/Messages/chat.db` is read-only SQLite. Apple stores dates as nanoseconds since 2001-01-01 (Apple epoch). Sends go via `osascript` to Messages.app.

**Access control**: `MessagesManager` loads `access.json` at init. Any operation on a handle not in the allowlist raises `PermissionError`.

## MCP Tools

| Tool | Description |
|------|-------------|
| `chat_list(days?)` | Recent conversations scoped to allowlist |
| `chat_messages(chat_id, limit?)` | Read messages from a conversation |
| `search_messages(query, days?, limit?)` | Full-text search across allowed chats |
| `unread` | Unread messages across all allowed chats |
| `send_message(handle, text)` | Send a message via Messages.app |
| `access_list` | Show current allowlist |
| `access_add(handle)` | Add a handle to the allowlist |
| `access_remove(handle)` | Remove a handle from the allowlist |

## Key Patterns

- `chat_id` format: `"iMessage;-;+16785551234"` or `"iMessage;-;user@example.com"` - get from `chat_list`
- Apple epoch offset: `978307200` Unix seconds; timestamps in DB are nanoseconds since that epoch
- `access.json` at `~/.config/imessage-tools/access.json` - set permissions 600
- All tools return structured JSON; `PermissionError` returns `{"error": "...", "hint": "Handle not in allowlist"}`
