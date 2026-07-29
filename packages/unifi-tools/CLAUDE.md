# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Status**: ACTIVE
**Last Updated**: 2026-06-20

## Overview

UniFi network CLI and MCP server for querying a UniFi controller (Dream Machine Pro, UDM SE, etc.). Provides device inventory, client monitoring, VLAN/WLAN configuration, WAN health, IPS status, firewall zones, DNS settings, device stats, firewall policies, ACL rules, VPN status, traffic matching lists, switch port management, radio config, client blocking, DPI lookups, RADIUS profiles, and pending device adoption.

Two API surfaces are in use:
- **Legacy API** (session cookies): `POST /api/auth/login` + `/proxy/network/api/s/{site}/`. Requires `host`, `username`, `password`.
- **v1 API** (Network 10.x+): `/integration/v1/...` with `X-API-Key` header. Requires `api_key` in config. Unlocks `device_stats`, `firewall_policy_list`, `acl_rule_list`, `vpn_status`, `device_restart`, `traffic_matching_list`, `switch_port_list`, `switch_port_action`, `client_block`, `client_unblock`, `client_reconnect`, `radio_list`, `network_list`, `wifi_broadcast_list`, `wifi_network_list`, `wan_detail`, `dns_policy_list`, `pending_device_list`, `device_tag_list`, `radius_profile_list`, `dpi_application_list`, `dpi_category_list`, and proper zone names in `firewall_zones`. Also enriches `device_list` (features, interfaces) and `client_list` (connected_at, access) when set. Site UUID is auto-discovered via `/integration/v1/sites`.

## Setup

```bash
# Install
pip install -e .

# Configure credentials (interactive - detects 1Password)
unifi-tools configure

# Or set via env vars
export UNIFI_HOST=192.168.1.1
export UNIFI_USERNAME=admin
export UNIFI_PASSWORD=your-password
export UNIFI_API_KEY=your-api-key   # Optional: unlocks v1 API features
```

Credentials are stored in `~/.config/unifi-tools/config.json` (600 permissions). Use a local admin account - avoid the Ubiquiti cloud account for API use.

To use v1 API features, generate an API key in UniFi application > Settings > Integrations.

## Commands

```bash
unifi-tools devices                       # Adopted devices with firmware and status
unifi-tools clients                       # All connected clients
unifi-tools clients --network IoT         # Filter by network/SSID
unifi-tools clients --count               # Count summary by network
unifi-tools device-stats                  # Per-device CPU, memory, uplink stats (requires api_key)
unifi-tools device-stats --device NAME    # Filter to one device by name or MAC
unifi-tools device-restart DEVICE        # Restart device by name or MAC (requires api_key)
unifi-tools device-restart DEVICE --yes  # Skip confirmation prompt
unifi-tools vlans                         # VLAN/network configs
unifi-tools wlans                         # WiFi SSIDs
unifi-tools wan                           # WAN uptime, latency, speedtest
unifi-tools ips                           # IPS/IDS mode and categories
unifi-tools firewall                      # Network-to-zone assignments
unifi-tools firewall-policies             # Firewall policies with zones and actions (requires api_key)
unifi-tools acl-rules                     # ACL rules sorted by priority (requires api_key)
unifi-tools vpn                           # VPN server and site-to-site tunnel status (requires api_key)
unifi-tools traffic-matching-lists        # Traffic matching lists (IPV4/IPV6/PORT) used in firewall policies (requires api_key)
unifi-tools traffic-matching-lists --no-items  # Summary only - type, id, name without item details (requires api_key)
unifi-tools dns                           # DNS resolver configuration
unifi-tools switch-ports DEVICE          # List switch ports with enabled and PoE status (requires api_key)
unifi-tools port-toggle DEVICE PORT_IDX  # Toggle a switch port enabled/disabled (requires api_key)
unifi-tools port-toggle DEVICE PORT_IDX --yes  # Skip confirmation prompt
unifi-tools port-poe DEVICE PORT_IDX on|off    # Set PoE state on a switch port (requires api_key)
unifi-tools port-poe DEVICE PORT_IDX on|off --yes  # Skip confirmation prompt
unifi-tools radio-list DEVICE            # Per-radio configuration for an access point (requires api_key)
```

## Architecture

```
unifi/
├── __init__.py      # Package init
├── __main__.py      # python -m unifi
├── config.py        # Layered config loader (config.json → env vars)
├── api.py           # REST API client with session caching
├── cli.py           # Argparse CLI with formatted output
└── mcp_server.py    # FastMCP server (JSON output)
```

**Config resolution order** (highest wins):
1. Environment variables (`UNIFI_HOST`, `UNIFI_USERNAME`, `UNIFI_PASSWORD`, `UNIFI_API_KEY`, etc.)
2. `~/.config/unifi-tools/config.json`
3. Built-in defaults (`port: 443`, `site: default`, `verify_ssl: false`)

**Session caching**: Login cookies + CSRF token cached at `~/.cache/unifi-tools/session.json` with 30-minute TTL. Auto re-login on 401.

**Legacy auth flow**: POST `/api/auth/login` → cookies + `X-CSRF-Token` → data under `/proxy/network/api/s/{site}/`

**v1 auth flow**: `X-API-Key` header → `/integration/v1/sites` for site UUID → `/integration/v1/sites/{uuid}/...`

**Caching**: `get_devices()`, `get_clients()`, `get_networks()`, and `get_wlans()` all use a 60s in-process TTL cache.

## MCP Server

```bash
claude mcp add -s user unifi -- unifi-mcp
```

MCP tools (35 total):

Legacy API (no `api_key` needed):
`device_list`, `client_list`, `client_count`, `vlan_list`, `wlan_list`, `wan_status`, `ips_status`, `ips_suppressed_list`, `ips_suppress_destination`, `ips_remove_category`, `firewall_zones`, `dns_config`

v1 API (requires `api_key`):
`device_stats`, `device_restart`, `device_tag_list`, `pending_device_list`, `radio_list`, `switch_port_list`, `switch_port_action`, `client_block`, `client_unblock`, `client_reconnect`, `network_list`, `wifi_broadcast_list`, `wifi_network_list`, `firewall_policy_list`, `acl_rule_list`, `traffic_matching_list`, `dns_policy_list`, `vpn_status`, `wan_detail`, `system_info`, `radius_profile_list`, `dpi_application_list`, `dpi_category_list`

`device_list` and `client_list` are enriched with additional v1 fields when `api_key` is set. `firewall_zones` returns proper zone names from the v1 API when `api_key` is set; falls back to `zone_map` config without it. `system_info` uses the legacy API for controller info and v1 API for firmware summary.

## Key Patterns

- `configure` command detects `op` (1Password) at runtime and offers it as a credential source; preserves non-sensitive fields (port, site, verify_ssl) on re-run; optionally sets `api_key`
- `zone_map` in config.json maps firewall zone IDs to names for legacy firmware; not needed when `api_key` is set - `firewall_zones` uses the v1 API and returns proper names directly
- CLI output uses `_print_*` helpers in `cli.py`; MCP tools return raw dicts/lists
- `_api_post` and `_api_delete` added to legacy client for write operations (used by IPS suppression tools)
