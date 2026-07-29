# Contacts Tools

Apple Contacts MCP server for [Claude Code](https://docs.anthropic.com/en/docs/claude-code). Search, create, update, merge, enrich, and manage contacts natively through macOS CNContactStore via [PyObjC](https://pyobjc.readthedocs.io/) - no API keys or credentials needed for core functionality.

Uses the native Contacts framework for fast, direct access. Contacts.app does not need to be running. Works with iCloud, Google, Exchange, and local contact accounts configured in macOS.

**macOS only** - requires the Contacts framework (macOS 12+).

## Features

### Browse & Search

| Tool | Description |
|------|-------------|
| `contact_groups` | List all contact groups |
| `contact_list` | List contacts (all or filtered by group) |
| `contact_search` | Search by name, email, phone, or organization |
| `contact_detail` | Full contact record including addresses, birthday, social profiles, notes |

### Create & Modify

| Tool | Description |
|------|-------------|
| `contact_create` | Create a new contact with name, email, phone, address, notes (with duplicate prevention) |
| `contact_update` | Update any field (phones, emails, addresses, URLs, social profiles, department, notes) |
| `contact_add_to_group` | Add an existing contact to a group |
| `contact_delete` | Delete a contact |

### Cleanup & Maintenance

| Tool | Description |
|------|-------------|
| `contact_duplicates` | Find duplicate contacts by matching name, email, or phone |
| `contact_merge` | Merge duplicates into one contact, combining data and deleting extras |
| `contact_incomplete` | Find contacts missing phone, email, address, or photo |

### Enrichment

| Tool | Description |
|------|-------------|
| `contact_enrich` | Look up a contact's email against Gravatar and People Data Labs for profile photos, social links, and more |
| `contact_enrich_all` | Bulk-scan contacts with avatar-first optimization and built-in rate limiting |

### Cross-App Intelligence

| Tool | Description |
|------|-------------|
| `contact_last_interaction` | Find when you last emailed a contact (cross-references Mail.app) |
| `contact_stale` | Find contacts with no email interaction in N days |
| `contact_unknown_senders` | Find frequent email senders who are not in your contacts |

### Export

| Tool | Description |
|------|-------------|
| `contact_export` | Export contacts to vCard (.vcf) format - all, by group, or specific contacts |

## Installation

### From Source

```bash
git clone https://github.com/mrlesmithjr/mcp.git
cd mcp/packages/contacts-tools
uv tool install --editable .
```

On first use, macOS will prompt your terminal app for Contacts access. Grant it in **System Settings > Privacy & Security > Contacts**.

## Configuration

### Claude Code MCP Server

Register as a Claude Code MCP server:

```bash
claude mcp add -s user contacts -- contacts-mcp
```

Or add manually to your Claude Code `settings.json`:

```json
{
  "mcpServers": {
    "contacts": {
      "command": "contacts-mcp"
    }
  }
}
```

### Environment Variables

All environment variables are optional. Core contact operations work without any keys.

| Variable | Purpose |
|----------|---------|
| `GRAVATAR_API_KEY` | Gravatar enrichment at 1,000 req/hr (vs 100 without). Free at [gravatar.com](https://gravatar.com) |
| `PDL_API_KEY` | People Data Labs enrichment (company, job title, social profiles). Free tier at [peopledatalabs.com](https://www.peopledatalabs.com) |

Pass them through the MCP server config:

```json
{
  "mcpServers": {
    "contacts": {
      "command": "contacts-mcp",
      "env": {
        "GRAVATAR_API_KEY": "your-key-here",
        "PDL_API_KEY": "your-key-here"
      }
    }
  }
}
```

## Usage Examples

Once registered, you can ask Claude things like:

- "Find the dentist's phone number"
- "Create a contact for the plumber: Mike's Plumbing, 678-555-1234"
- "Find duplicate contacts and merge them"
- "Which contacts are missing email addresses?"
- "Who have I not emailed in over a year?"
- "Find email senders that aren't in my contacts"
- "Enrich my contacts with Gravatar data"
- "Export my Emergency group to a vCard file"

### Pairing with Other Tools

Contacts Tools pairs naturally with other Apple MCP servers:

- **[mail-tools](https://github.com/mrlesmithjr/mcp/tree/main/packages/mail-tools)** - "Email the dentist about rescheduling"
- **[apple-eventkit-tools](https://github.com/mrlesmithjr/mcp/tree/main/packages/apple-eventkit-tools)** - "Schedule a meeting with John next Tuesday" or "Remind me to call the plumber tomorrow"

## How It Works

CNContactStore is Apple's native framework for contact access. It provides fast, indexed access to all contacts configured in macOS - iCloud, Google, Exchange, and local contacts.

The Mail.app cross-reference tools (`contact_last_interaction`, `contact_stale`, `contact_unknown_senders`) query Mail's local SQLite database directly - Mail.app does not need to be running.

Enrichment uses a two-pass approach: a fast avatar-only sweep (unlimited, no rate limit) identifies which contacts have Gravatar profiles, then the profile API is called only for hits.

## Requirements

- macOS 12+ (Monterey or later)
- Python 3.11+
- Terminal app granted Contacts access (TCC permission)
- For mail cross-reference tools: Mail.app configured with at least one account

## Project Structure

```
contacts_tools/
├── __init__.py      # Package init
├── __main__.py      # python -m contacts_tools.mcp_server
├── contacts.py      # CNContactStore bridge (ContactsManager class)
└── mcp_server.py    # FastMCP server (17 tools, JSON output)
```

## License

MIT
