# UniFi Tools

UniFi network CLI and MCP server for querying a UniFi controller (Dream Machine Pro, UDM SE, Cloud Key, etc.). Provides device inventory, client monitoring, VLAN/WLAN configuration, WAN health, IPS status, firewall zones, DNS settings, firewall policies, ACL rules, VPN status, and traffic matching lists.

## Setup

```bash
git clone https://github.com/mrlesmithjr/mcp.git
cd mcp/packages/unifi-tools
uv tool install --editable .
```

### Configure credentials

Run the setup wizard:

```bash
unifi-tools configure
```

The wizard detects 1Password (`op`) if installed and offers it as an input source. Otherwise prompts for manual entry. Create a **local admin account** on your UniFi controller for API access - avoid using your Ubiquiti cloud account.

The wizard also prompts for an optional API key. This unlocks additional commands and MCP tools that use the UniFi Network v1 API (Network 10.x+). To generate one: UniFi application > Settings > Integrations.

### Alternative: manual config

Use the included example as a starting point:

```bash
mkdir -p ~/.config/unifi-tools
cp config.json.example ~/.config/unifi-tools/config.json
```

Or create `~/.config/unifi-tools/config.json` directly:

```json
{
  "host": "192.168.1.1",
  "username": "admin",
  "password": "your-password",
  "port": 443,
  "site": "default",
  "verify_ssl": false
}
```

### Config Reference

| Config Key | Required | Default | Description |
|-----------|----------|---------|-------------|
| `host` | Yes | - | Controller IP or hostname (e.g. `192.168.1.1`) |
| `username` | Yes | - | Local admin username |
| `password` | Yes | - | Local admin password |
| `port` | No | `443` | HTTPS port |
| `site` | No | `default` | Site name |
| `verify_ssl` | No | `false` | SSL certificate verification |
| `api_key` | No | - | UniFi Network v1 API key (Network 10.x+); unlocks device stats, firewall policies, ACL rules, VPN status, device restart, and traffic matching lists; also enriches device and client data |
| `zone_map` | No | `{}` | Firewall zone ID-to-name mapping; not needed when `api_key` is set |

### Environment Variable Overrides

Environment variables take precedence over config.json values:

| Variable | Config Key |
|----------|-----------|
| `UNIFI_HOST` | `host` |
| `UNIFI_USERNAME` | `username` |
| `UNIFI_PASSWORD` | `password` |
| `UNIFI_PORT` | `port` |
| `UNIFI_SITE` | `site` |
| `UNIFI_VERIFY_SSL` | `verify_ssl` |
| `UNIFI_API_KEY` | `api_key` |
| `UNIFI_ZONE_MAP` | `zone_map` (JSON string) |

### Firewall Zone Mapping

When `api_key` is configured (Network 10.x+), `unifi-tools firewall` and the `firewall_zones` MCP tool use the v1 API and return proper zone names automatically. No `zone_map` config is needed.

Without `api_key` (legacy firmware), zone names are not exposed via the REST API. Map zone IDs to names manually in config.json:

```json
{
  "zone_map": {
    "zone-id-1": "LAN",
    "zone-id-2": "IoT",
    "zone-id-3": "Guest"
  }
}
```

Discover your zone IDs with: `unifi-tools firewall --raw`

## Usage

```bash
# Devices and clients
unifi-tools devices                       # Adopted devices (APs, switches, gateway)
unifi-tools clients                       # All connected clients
unifi-tools clients --network IoT         # Filter by network/SSID
unifi-tools clients --count               # Count summary by network

# Device management (requires api_key)
unifi-tools device-stats                  # Per-device CPU, memory, and uplink stats
unifi-tools device-stats --device NAME    # Filter to one device by name or MAC
unifi-tools device-restart DEVICE        # Restart a device by name or MAC
unifi-tools device-restart DEVICE --yes  # Skip confirmation prompt

# Network configuration
unifi-tools vlans                         # VLAN/network configs with subnets
unifi-tools wlans                         # WiFi SSIDs with security settings
unifi-tools dns                           # DNS resolver configuration

# Security and health
unifi-tools wan                           # WAN uptime, latency, speedtest
unifi-tools ips                           # IPS/IDS mode and categories
unifi-tools firewall                      # Network-to-zone assignments
unifi-tools firewall-policies             # Firewall policies with zones and actions (requires api_key)
unifi-tools acl-rules                     # ACL rules sorted by priority (requires api_key)
unifi-tools vpn                           # VPN server and site-to-site tunnel status (requires api_key)
unifi-tools traffic-matching-lists        # Traffic matching lists (IPV4/IPV6/PORT) used in firewall policies (requires api_key)
unifi-tools traffic-matching-lists --no-items  # Summary only - type, id, name without item details (requires api_key)

# Switch port management (requires api_key)
unifi-tools switch-ports DEVICE          # List switch ports with enabled and PoE status
unifi-tools port-toggle DEVICE PORT_IDX  # Toggle a switch port enabled/disabled
unifi-tools port-toggle DEVICE PORT_IDX --yes  # Skip confirmation prompt
unifi-tools port-poe DEVICE PORT_IDX on|off    # Set PoE state on a switch port
unifi-tools port-poe DEVICE PORT_IDX on|off --yes  # Skip confirmation prompt

# Access point radio config (requires api_key)
unifi-tools radio-list DEVICE            # Per-radio configuration for an access point
```

## MCP Server

### Claude Code

```bash
claude mcp add -s user unifi -- unifi-mcp
```

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "unifi": {
      "command": "unifi-mcp",
      "env": {
        "UNIFI_HOST": "192.168.1.1",
        "UNIFI_USERNAME": "admin",
        "UNIFI_PASSWORD": "your-password"
      }
    }
  }
}
```

### MCP Tools

35 tools across devices, clients, network, firewall, IPS, DNS, VPN, and system categories.

#### Devices

| Tool | Description |
|------|-------------|
| `device_list` | Adopted devices (APs, switches, gateways) with firmware, IP, uptime, and status; enriched with features and interfaces when `api_key` is set |
| `device_stats` | Per-device CPU %, memory %, load averages, and uplink rates (requires `api_key`) |
| `device_restart` | Restart a device by name or MAC address; device is offline for ~30-90 seconds (requires `api_key`) |
| `device_tag_list` | Device tags used as source/destination filters in firewall policies and ACL rules (requires `api_key`) |
| `pending_device_list` | Devices discovered on the network but not yet adopted into UniFi (requires `api_key`) |
| `radio_list` | Per-radio configuration for an access point (channel, txPower, settingPreference, etc.) (requires `api_key`) |
| `switch_port_list` | Switch ports on a device with enabled state and PoE status (requires `api_key`) |
| `switch_port_action` | Enable, disable, or toggle PoE on a specific switch port by index (requires `api_key`) |

#### Clients

| Tool | Description |
|------|-------------|
| `client_list` | Connected clients with IP, network, signal, and uptime; enriched with connected_at and access when `api_key` is set |
| `client_count` | Quick count of connected clients by network and wired/wireless |
| `client_block` | Block a client from the network by MAC address (requires `api_key`) |
| `client_unblock` | Unblock a previously blocked client by MAC address (requires `api_key`) |
| `client_reconnect` | Force a client to reconnect; useful after VLAN or policy changes (requires `api_key`) |

#### Network and WiFi

| Tool | Description |
|------|-------------|
| `vlan_list` | Network/VLAN configurations with subnets, DHCP ranges, and firewall zone assignments |
| `wlan_list` | WiFi SSIDs with security mode, VLAN assignment, and enabled state |
| `network_list` | VLAN/network config from the v1 API with additional fields (DHCP lease count, gateway IP, IPv6) (requires `api_key`) |
| `wifi_broadcast_list` | WiFi SSID broadcasts from the v1 API; may include per-radio detail beyond `wlan_list` (requires `api_key`) |
| `wifi_network_list` | WLAN configurations from the v1 API with full field detail including v1 IDs for write operations (requires `api_key`) |

#### Firewall and IPS

| Tool | Description |
|------|-------------|
| `firewall_zones` | Network-to-zone assignments; returns proper zone names from v1 API when `api_key` is set |
| `firewall_policy_list` | Custom firewall policies with source/dest zones, action, and ordering (requires `api_key`) |
| `acl_rule_list` | ACL rules sorted by priority index; IPV4 and MAC types with source/dest filters (requires `api_key`) |
| `traffic_matching_list` | Traffic matching lists (IPV4/IPV6/PORT) used in firewall policies; pass `include_items=False` for summary only (requires `api_key`) |
| `ips_status` | IPS/IDS mode, enabled detection categories, and suppressed alert count |
| `ips_suppressed_list` | All suppressed IPS alert signatures currently configured |
| `ips_suppress_destination` | Add signature-level suppression for scanning alerts to a given IPv4 CIDR |
| `ips_remove_category` | Remove a detection category from IPS enabled_categories |

#### DNS

| Tool | Description |
|------|-------------|
| `dns_config` | DNS resolver configuration: DoH state and configured resolvers |
| `dns_policy_list` | DNS routing and filtering policies (per-network or per-client DNS rules) (requires `api_key`) |

#### VPN and WAN

| Tool | Description |
|------|-------------|
| `vpn_status` | VPN server configurations and site-to-site tunnel overview (OpenVPN, WireGuard, IPsec) (requires `api_key`) |
| `wan_status` | WAN interface health: connectivity, latency, and speedtest results for primary and failover links |
| `wan_detail` | Richer WAN configuration from v1 API including IP addressing, failover config, and uplink type (requires `api_key`) |

#### System and Lookup

| Tool | Description |
|------|-------------|
| `system_info` | Controller snapshot: software version, hostname, timezone, uptime, and firmware summary |
| `radius_profile_list` | RADIUS authentication profiles and their network/SSID associations (requires `api_key`) |
| `dpi_application_list` | DPI application definitions: ID-to-name lookup table for application IDs in firewall and IPS events (requires `api_key`) |
| `dpi_category_list` | DPI category definitions: ID-to-name lookup table for category IDs in firewall and IPS events (requires `api_key`) |

## API Notes

### Legacy API (session cookies)

Used for all standard commands (devices, clients, VLANs, WLANs, WAN, IPS, DNS, firewall zones without `api_key`):

1. POST `/api/auth/login` with credentials
2. Session cookies + `X-CSRF-Token` header stored
3. Data endpoints under `/proxy/network/api/s/{site}/`

Sessions are cached to `~/.cache/unifi-tools/session.json` with a 30-minute TTL and automatic re-login on 401 responses.

### v1 API (UniFi Network 10.x+)

Used for all tools marked "requires `api_key`" in the MCP Tools table above, plus enriched fields on `device_list` and `client_list`. Requires `api_key` in config or `UNIFI_API_KEY` env var.

1. `X-API-Key` header on all requests
2. Site UUID auto-discovered from `/integration/v1/sites`
3. Data endpoints under `/integration/v1/sites/{uuid}/...`

Generate an API key in UniFi application > Settings > Integrations.

## Project Structure

```
unifi/
├── __init__.py      # Package init
├── __main__.py      # python -m unifi
├── config.py        # Layered config loader (config.json → env vars)
├── api.py           # REST API client with session caching
├── cli.py           # Argparse CLI with formatted output
└── mcp_server.py    # FastMCP server (JSON output)
```

## License

MIT
