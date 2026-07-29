# Sheets Tools

Google Sheets MCP server for [Claude Code](https://docs.anthropic.com/en/docs/claude-code). Create, read, write, append, clear, and manage tabs in spreadsheets via the Google Sheets API v4.

## Features

### Spreadsheets

| Tool | Description |
|------|-------------|
| `sheet_create` | Create a new spreadsheet, optionally with named tabs |
| `sheet_info` | Metadata: title, tabs, and each tab's dimensions |

### Ranges

| Tool | Description |
|------|-------------|
| `sheet_read_range` | Read a range in A1 notation |
| `sheet_write_range` | Overwrite a range |
| `sheet_append_row` | Append a row after the last populated row |
| `sheet_clear_range` | Clear cell contents, keeping the tab |

### Tabs

| Tool | Description |
|------|-------------|
| `sheet_add_sheet` | Add a new tab |
| `sheet_delete_rows` | Delete a range of rows |
| `sheet_delete_sheet` | Delete a tab |

### Discovery and sharing

| Tool | Description |
|------|-------------|
| `sheet_list` | List spreadsheets visible to the account, optionally filtered by name |
| `sheet_share` | Share a spreadsheet with another Google account |

### Authorization

| Tool | Description |
|------|-------------|
| `google_sheets_authorize` | Authorize the Google account for Sheets API access |
| `google_sheets_status` | Show Sheets API authorization status |

## Installation

### From Source

```bash
git clone https://github.com/mrlesmithjr/mcp.git
cd mcp/packages/sheets-tools
uv tool install --editable .
```

## Configuration

### Google OAuth Setup

sheets-tools shares the same OAuth client credentials as contacts-tools and apple-eventkit-tools - one GCP app registration, one credentials file:

1. Copy the shared Google OAuth client credentials to `~/.config/google/credentials.json` (the same file used by mail-tools' Gmail integration, apple-eventkit-tools' Calendar integration, and contacts-tools' People integration).
2. Enable both the Sheets API and the Drive API on that GCP project: [console.cloud.google.com/apis/library/sheets.googleapis.com](https://console.cloud.google.com/apis/library/sheets.googleapis.com) and [console.cloud.google.com/apis/library/drive.googleapis.com](https://console.cloud.google.com/apis/library/drive.googleapis.com) (or `gcloud services enable sheets.googleapis.com drive.googleapis.com`). The Drive API backs `sheet_list`/`sheet_share`, since the Sheets API has no listing/sharing endpoints of its own. This is a one-time requirement - skipping it surfaces as a 403 on the first live API call, not at authorize time.
3. Run `google_sheets_authorize` once. Tokens are stored separately at `~/.config/sheets-tools/google_tokens.json` - a Sheets-scoped token is not interchangeable with a Calendar- or Contacts-scoped one, even for the same Google account. If you authorized before `sheet_list`/`sheet_share` were added, re-run `google_sheets_authorize` to pick up the widened `drive` scope - the old token only covers `spreadsheets` and will 403 on both new tools.

### Claude Code MCP Server

Register as a Claude Code MCP server:

```bash
claude mcp add -s user sheets -- sheets-mcp
```

Or add manually to your Claude Code `settings.json`:

```json
{
  "mcpServers": {
    "sheets": {
      "command": "sheets-mcp"
    }
  }
}
```

### Claude Desktop

Claude Desktop has no plugin or marketplace support and does not read Claude Code's MCP
config, so it needs its own entry pointing at an **absolute path**.

#### 1. Authorize first

Desktop is not the place to do OAuth setup. Complete the [Google OAuth Setup](#google-oauth-setup)
steps above from any client (Claude Code is easiest) and confirm `google_sheets_status`
reports `authorized: true` and `live: true`. Authorization is per-machine, not per-client:
the token at `~/.config/sheets-tools/google_tokens.json` is shared, so Desktop picks up
whatever you already authorized.

#### 2. Find the absolute path

Desktop launches servers with a minimal environment and does **not** inherit your shell's
`PATH`, so a bare `"command": "sheets-mcp"` fails to start with no useful error. Get the
real path for however you installed:

```bash
# uv tool install (sheets-mcp is on PATH)
which sheets-mcp

# monorepo / workspace checkout
ls "$PWD/.venv/bin/sheets-mcp"

# installed as a Claude Code plugin
ls ~/.local/share/sheets-tools/venv/bin/sheets-mcp
```

There is no short name to fall back on. The plugin installer only symlinks human-facing
CLIs into `~/.local/bin`, and sheets-tools ships exactly one console script - `sheets-mcp`,
which is deliberately excluded. The absolute path is required.

#### 3. Add the server

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`. The file already
exists and holds Desktop's own settings, so **merge** this key in rather than replacing
the file - and quit Desktop with **⌘Q** *before* editing, since it rewrites this file on
quit and will otherwise clobber your change:

```json
{
  "mcpServers": {
    "sheets-tools": {
      "command": "/absolute/path/to/sheets-mcp"
    }
  }
}
```

No `env` block is needed. The server reads the shared OAuth client from
`~/.config/google/credentials.json` and its own token from
`~/.config/sheets-tools/google_tokens.json`. Only set `GOOGLE_SHEETS_ACCOUNT` here if you
need to pin a specific account for this client alone.

#### 4. Restart and verify

Reopen Desktop. The tools appear under the hammer icon. Ask it something concrete:

```
List my spreadsheets
```

That request exercises the Drive-backed path, so it also proves the widened `drive` scope
took - a Sheets-only token gets this far and then 403s.

To verify the server independently of Desktop, run the same handshake Desktop does, with
an empty environment to prove the absolute path and config lookup both hold:

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
  | env -i HOME="$HOME" PATH=/usr/bin:/bin /absolute/path/to/sheets-mcp
```

A working server returns an `initialize` result followed by all 13 tools.

#### Troubleshooting

| Symptom | Cause |
|---------|-------|
| Server never appears; no error | Bare command name instead of an absolute path, or Desktop was not fully quit (⌘Q) |
| Your config edit vanished | Desktop was running when you edited it and rewrote the file on quit. Quit first, then edit |
| Tools load; every call returns a missing-token error | `google_sheets_authorize` has not been run, or `HOME` is not what you expect |
| `sheet_list`/`sheet_share` 403; everything else works | Token predates the widened `drive` scope, or the Drive API is not enabled on the GCP project. Re-run `google_sheets_authorize` |
| Every tool 403s on the first live call | Sheets API not enabled on the GCP project - `gcloud services enable sheets.googleapis.com` |
| `ModuleNotFoundError: No module named 'mcp.server.fastmcp'` | The venv resolved `mcp` 2.x. Rebuild it; the dependency is capped at `<2` |
| Two `sheets-mcp` processes | Normal - Desktop starts one server per window |

> On a machine with the monorepo checked out, pointing Desktop at `.venv/bin/sheets-mcp`
> means Desktop runs whatever code is currently checked out, picked up on next launch.
> That is usually what you want on a dev machine; the plugin venv under
> `~/.local/share/sheets-tools/` is the frozen alternative.

## Usage Examples

Once registered and authorized, you can ask Claude things like:

- "Create a spreadsheet called 'LGI Water Damage Claim Log' with tabs for Timeline and Receipts"
- "Read the Budget tab of this spreadsheet"
- "Append a row to the expense log with today's date and this amount"
- "Clear the old data in rows 2 through 50"
- "Add a new tab called 'Q3' to this spreadsheet"

## Requirements

- Python 3.11+
- A Google account with a shared OAuth client credentials file and the Sheets API enabled

## Project Structure

```
sheets_tools/
├── __init__.py       # Package init
├── __main__.py       # python -m sheets_tools.mcp_server
├── google_sheets.py  # GoogleSheetsClient(GoogleOAuthClient) - Sheets API v4
└── mcp_server.py     # FastMCP server (13 tools, JSON output)
```

## License

MIT
