# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Status**: ACTIVE
**Last Updated**: 2026-05-21

## Overview

NextDNS CLI and MCP server for querying DNS analytics, managing allowlists/denylists, and monitoring security events via the NextDNS API.

## Setup

```bash
# Install
pip install -e .

# Configure credentials (interactive - detects 1Password)
nextdns-tools configure

# Or set manually via env vars
export NEXTDNS_API_KEY=your-key
export NEXTDNS_PROFILE_ID=your-profile-id
```

Credentials are stored in `~/.config/nextdns-tools/config.json` (600 permissions). Environment variables override the config file.

## Commands

```bash
nextdns-tools status                 # Query volume breakdown
nextdns-tools status --hours 48
nextdns-tools blocked                # Top blocked domains
nextdns-tools devices                # Per-device query counts
nextdns-tools security               # Security threat events
nextdns-tools logs                   # Recent DNS queries (last 50)
nextdns-tools logs --limit 100
nextdns-tools logs --blocked
nextdns-tools profile                # Security features, blocklists, privacy settings
nextdns-tools allowlist              # Show allowlist
nextdns-tools allowlist --add example.com
nextdns-tools allowlist --remove example.com
nextdns-tools denylist --add ads.example.com
nextdns-tools export                 # Full profile config to stdout
nextdns-tools export -o backup.json
```

## Architecture

```
nextdns/
├── __init__.py      # Package init
├── __main__.py      # python -m nextdns
├── config.py        # Layered config loader (config.json → env vars)
├── api.py           # NextDNS REST API client
├── cli.py           # Argparse CLI with formatted output
└── mcp_server.py    # FastMCP server (JSON output)
```

**Config resolution order** (highest wins):
1. Environment variables (`NEXTDNS_API_KEY`, `NEXTDNS_PROFILE_ID`)
2. `~/.config/nextdns-tools/config.json`
3. Built-in defaults

## MCP Server

```bash
claude mcp add -s user nextdns -- nextdns-mcp
```

MCP tools: `dns_status`, `dns_blocked`, `dns_devices`, `dns_security`, `dns_logs`, `dns_profile`, `dns_allowlist`, `dns_allowlist_add`, `dns_allowlist_remove`, `dns_denylist_add`, `dns_export`

## Key Patterns

- All API calls use `X-Api-Key` header
- Config loaded once at startup via `load_config()` - cached globally
- `configure` command detects `op` (1Password) at runtime and offers it as a credential source
- CLI output uses `_print_*` helpers in `cli.py`; MCP tools return raw dicts/lists
