# iMessage Tools

iMessage MCP server for [Claude Code](https://docs.anthropic.com/en/docs/claude-code). Read conversations, send messages, and search chat history natively through macOS Messages via SQLite and AppleScript - no API keys or credentials needed.

Reads from `chat.db` (SQLite, read-only) for fast message access. Sends via AppleScript through Messages.app. All operations are scoped to a user-managed allowlist for safety.

**macOS only** - requires Messages.app and Full Disk Access for chat.db reads (macOS 12+).

## Features

### Conversations

| Tool | Description |
|------|-------------|
| `chat_list` | List recent conversations with last message date (DMs and group chats) |
| `chat_messages` | Read messages from a conversation by chat ID |
| `send_message` | Send to a person (by name, phone, or email) or group chat (by chat ID) |
| `unread` | Get chats with unread messages |
| `search_messages` | Search message text across all allowed conversations |

### Access Control

| Tool | Description |
|------|-------------|
| `access_list` | Show all handles currently in the allowlist |
| `access_add` | Add a phone number or email to the allowlist (takes effect immediately) |
| `access_remove` | Remove a handle from the allowlist (takes effect immediately) |

### Contact Resolution

When `pyobjc-framework-Contacts` is installed, phone numbers are automatically resolved to contact names:

- Chat list shows "Jane Doe" instead of "+16785551234"
- Message senders display as contact names
- `send_message` accepts contact names - e.g., `send_message(to="Jane Doe", text="...")`
- Falls back gracefully to raw phone numbers if PyObjC is not installed

## Installation

### From Source

```bash
git clone https://github.com/mrlesmithjr/mcp.git
cd mcp/packages/imessage-tools
uv tool install --editable .
```

Contact name resolution is built in (the Contacts framework is a regular dependency).

### Permissions

On first use, macOS will prompt for:

- **Full Disk Access** - required for reading `~/Library/Messages/chat.db`. Grant in **System Settings > Privacy & Security > Full Disk Access** for your terminal app.
- **Contacts Access** - required for name resolution (optional). Grant in **System Settings > Privacy & Security > Contacts**.
- **Automation (Messages.app)** - required for sending. macOS prompts automatically on first send.

## Configuration

### Claude Code MCP Server

Register as a Claude Code MCP server:

```bash
claude mcp add -s user imessage-tools -- imessage-mcp
```

Or add manually to your Claude Code `settings.json`:

```json
{
  "mcpServers": {
    "imessage-tools": {
      "command": "python",
      "args": ["-m", "imessage_tools.mcp_server"]
    }
  }
}
```

### Allowlist

All operations are scoped to an allowlist at `~/.config/imessage-tools/access.json`:

```json
{
  "allow": [
    "+16785551234",
    "+16785559876"
  ]
}
```

You can manage this file directly or use the `access_add` / `access_remove` tools at runtime - changes take effect immediately without restarting.

If `~/.config/imessage-tools/access.json` does not exist, the tool raises a `RuntimeError` naming that path and pointing to `access.json.example`. Copy the example to get started:

```bash
mkdir -p ~/.config/imessage-tools
cp access.json.example ~/.config/imessage-tools/access.json
```

Then edit the file to add at least one handle before using the server.

## Usage Examples

Once registered, you can ask Claude things like:

- "Show my recent conversations"
- "Read my chat with Kim"
- "Text Kim: I'll be home at 6"
- "Send 'Happy birthday!' to +16785551234"
- "Reply in the family group chat: sounds good"
- "Search my messages for 'dentist appointment'"
- "Any unread messages?"
- "Allow my friend's number: +16785559876"

### Sending to Group Chats

Use `chat_list` to find the `chat_id` for a group chat, then pass it to `send_message`:

```
1. chat_list(days=7)          → find the group chat ID
2. send_message(to="any;+;chat123...", text="message")
```

### Pairing with Other Tools

iMessage Tools pairs naturally with other Apple MCP servers:

- **[contacts-tools](https://github.com/mrlesmithjr/mcp/tree/main/packages/contacts-tools)** - Look up contacts before messaging
- **[mail-tools](https://github.com/mrlesmithjr/mcp/tree/main/packages/mail-tools)** - Cross-reference email and chat communication
- **[apple-eventkit-tools](https://github.com/mrlesmithjr/mcp/tree/main/packages/apple-eventkit-tools)** - "Remind me to text Kim tomorrow"

## How It Works

**Read side:** Queries `~/Library/Messages/chat.db` (SQLite, read-only). This is the same database Messages.app uses. The database contains all iMessage, SMS, and RCS conversations. Messages with `attributedBody` blobs (common in SMS/RCS) are parsed to extract readable text.

**Send side:** Uses AppleScript to send through Messages.app. Individual messages use the `participant` API; group chats use the `chat id` API.

**Contact resolution:** Uses Apple's CNContactStore (PyObjC) to build an in-memory phone→name cache on first use. This is the same framework used by Contacts.app - no network calls, no API keys.

**Access control:** A simple JSON allowlist at `~/.config/imessage-tools/access.json`. Only conversations involving allowlisted handles are visible or sendable. Managed via MCP tools or direct file editing.

## Requirements

- macOS 12+ (Monterey or later)
- Python 3.11+
- Full Disk Access for terminal app (for chat.db reads)
- Messages.app (for sending)
- Contact name resolution is built in (via `pyobjc-framework-Contacts`, a regular dependency)
