# NextDNS Tools

NextDNS CLI and MCP server for querying DNS analytics, managing allowlists/denylists, and monitoring security events via the [NextDNS API](https://nextdns.github.io/api/).

## Setup

```bash
git clone https://github.com/mrlesmithjr/mcp.git
cd mcp/packages/nextdns-tools
uv tool install --editable .
```

### Configure credentials

Run the setup wizard:

```bash
nextdns-tools configure
```

The wizard detects 1Password (`op`) if installed and offers it as an input source. Otherwise prompts for manual entry and saves credentials to `~/.config/nextdns-tools/config.json`.

**Getting credentials:**

1. Go to [NextDNS account](https://my.nextdns.io/account) and generate an API key
2. Find your profile ID from your NextDNS dashboard URL (e.g. `https://my.nextdns.io/abc123/setup` - profile ID is `abc123`)

### Alternative: manual config

Use the included example as a starting point:

```bash
mkdir -p ~/.config/nextdns-tools
cp config.json.example ~/.config/nextdns-tools/config.json
```

Or create `~/.config/nextdns-tools/config.json` directly:

```json
{
  "api_key": "your-nextdns-api-key",
  "profile_id": "your-profile-id"
}
```

### Alternative: environment variables

Environment variables override the config file - useful for CI or containers:

| Variable | Required | Description |
|----------|----------|-------------|
| `NEXTDNS_API_KEY` | Yes | API key from NextDNS account settings |
| `NEXTDNS_PROFILE_ID` | Yes | Your NextDNS profile ID |

## Usage

```bash
# Analytics
nextdns-tools status                 # Query volume breakdown (allowed/blocked/relayed)
nextdns-tools status --hours 48      # Custom time range
nextdns-tools blocked                # Top blocked domains
nextdns-tools devices                # Per-device query counts
nextdns-tools security               # Security threat events

# Logs
nextdns-tools logs                   # Recent DNS queries (last 50)
nextdns-tools logs --limit 100       # More entries
nextdns-tools logs --blocked         # Only blocked queries

# Profile
nextdns-tools profile                # Security features, blocklists, privacy settings

# List management
nextdns-tools allowlist              # Show current allowlist
nextdns-tools allowlist --add example.com
nextdns-tools allowlist --remove example.com
nextdns-tools denylist               # Show current denylist
nextdns-tools denylist --add ads.example.com

# Export
nextdns-tools export                 # Full profile config to stdout
nextdns-tools export -o backup.json  # Save to file
```

## MCP Server

### Claude Code

```bash
claude mcp add -s user nextdns -- nextdns-mcp
```

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "nextdns": {
      "command": "nextdns-mcp",
      "env": {
        "NEXTDNS_API_KEY": "your-api-key",
        "NEXTDNS_PROFILE_ID": "your-profile-id"
      }
    }
  }
}
```

### MCP Tools

| Tool | Description |
|------|-------------|
| `dns_status` | Query volume with allowed/blocked/relayed breakdown |
| `dns_blocked` | Top blocked domains with counts |
| `dns_devices` | Per-device query activity |
| `dns_security` | Security threat events |
| `dns_logs` | Recent DNS query log entries |
| `dns_profile` | Profile configuration (security, privacy, blocklists) |
| `dns_allowlist` | View current allowlist |
| `dns_allowlist_add` | Add domain to allowlist |
| `dns_allowlist_remove` | Remove domain from allowlist |
| `dns_denylist_add` | Add domain to denylist |
| `dns_export` | Export full profile configuration |

## Project Structure

```
nextdns/
├── __init__.py      # Package init
├── __main__.py      # python -m nextdns
├── config.py        # Layered config loader (config.json → env vars)
├── api.py           # NextDNS REST API client
├── cli.py           # Argparse CLI with formatted output
└── mcp_server.py    # FastMCP server (JSON output)
```

## License

MIT
