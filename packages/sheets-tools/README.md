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
