# Security Policy

## Reporting a vulnerability

Please report security issues privately via
[GitHub Security Advisories](https://github.com/mrlesmithjr/mcp/security/advisories/new)
rather than opening a public issue.

## What these tools have access to

This is the most important thing to understand before installing anything here.
These are personal automation servers, and several of them are deliberately
wired into sensitive local data:

| Tool | Reads / writes |
|------|----------------|
| `imessage-tools` | `~/Library/Messages/chat.db` (full iMessage history); sends messages as you |
| `mail-tools` | Gmail via OAuth: read, send, modify labels |
| `contacts-tools` | Google Contacts (People API): read and write |
| `apple-eventkit-tools` | Apple Calendar/Reminders and Google Calendar: read and write |
| `sheets-tools` | Google Sheets and Drive metadata |
| `ynab-tools` | Full YNAB budget: balances, transactions, income, net worth |
| `unifi-tools` | UniFi controller: network topology, clients, firewall rules |
| `nextdns-tools` | NextDNS profile: DNS query logs, allow/deny lists |
| `launchd-tools` | Enumerates and controls user LaunchAgents |
| `obsidian-search-tools` | Indexes an Obsidian vault into a local SQLite DB |

An MCP server runs with your user's privileges. A model driving these tools can
read anything the tool can read. Install only the plugins you actually want, and
review a tool's MCP surface before granting it to an agent.

## Credentials

No credentials ship in this repository, and none belong in it.

- Secrets live in `~/.config/<tool>/config.json`, written `0600` in a `0700`
  directory (`mcp_common.paths`), or in environment variables.
- Google OAuth tokens live in `~/.config/<tool>/google_tokens.json`; the shared
  client credentials live in `~/.config/google/credentials.json`.
- Every `*.example` file in this repo contains placeholders only.
- `.gitignore` excludes real `config.json`, `*.db`, and `payee_rules.json`.

If you believe a credential was ever committed here, report it via the advisory
link above rather than in a public issue.

## Access controls worth knowing about

- **`imessage-tools` is allowlist-gated.** Every read and send is scoped to
  handles in `~/.config/imessage-tools/access.json`. Anything else raises
  `PermissionError`. The allowlist is the security boundary — keep it narrow.
- **The YNAB dashboard refuses to bind a non-loopback address without a
  password.** Every route serves budget data and HTTP Basic auth is only
  enforced when `DASHBOARD_PASSWORD` is set, so binding `0.0.0.0` without one is
  rejected outright rather than warned about. Set `DASHBOARD_PASSWORD` (or
  `dashboard_password` in `config.json`) before exposing it beyond `127.0.0.1`,
  and note that HTTP Basic over plain HTTP sends the password in base64 on every
  request — put it behind TLS if it leaves the machine.
- **Destructive MCP tools are annotated.** Tools declare
  `readOnlyHint`/`destructiveHint`/`openWorldHint` so clients can gate
  confirmation. Annotations are hints, not enforcement: treat them as advisory
  and keep a human in the loop for destructive operations.

## Supported versions

Fixes land on the latest released version of each plugin. There are no
long-term support branches.
