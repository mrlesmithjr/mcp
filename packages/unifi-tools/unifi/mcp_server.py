"""MCP server exposing UniFi network data as callable tools for Claude Code.

Returns structured JSON optimized for LLM consumption.
"""

import json
import logging
import sys
import time

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
logger = logging.getLogger(__name__)

mcp = FastMCP("unifi")

# MCP tool annotations follow the workspace convention in the root CLAUDE.md.
# unifi-tools targets the user's own LAN-local UniFi controller (not a cloud
# or third-party service), so all tools are closed-world: openWorldHint=False.
# openWorldHint defaults to True per spec, so we set it explicitly everywhere.
_READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)

# ── Caching ──

from unifi.api import _CACHE_TTL, _normalize_mac  # noqa: E402 - intentional post-mcp init import

_device_cache: list | None = None
_device_cache_time: float = 0
_client_cache: list | None = None
_client_cache_time: float = 0
_v1_device_cache: list | None = None
_v1_device_cache_time: float = 0
_v1_client_cache: list | None = None
_v1_client_cache_time: float = 0
_traffic_matching_full_cache: list | None = None
_traffic_matching_full_cache_time: float = 0
_traffic_matching_summary_cache: list | None = None
_traffic_matching_summary_cache_time: float = 0


def _cached_devices() -> list:
    """Fetch devices with TTL cache."""
    global _device_cache, _device_cache_time
    now = time.monotonic()
    if _device_cache is not None and (now - _device_cache_time) < _CACHE_TTL:
        return _device_cache
    from unifi.api import get_devices

    _device_cache = get_devices()
    _device_cache_time = now
    return _device_cache


def _cached_clients() -> list:
    """Fetch clients with TTL cache."""
    global _client_cache, _client_cache_time
    now = time.monotonic()
    if _client_cache is not None and (now - _client_cache_time) < _CACHE_TTL:
        return _client_cache
    from unifi.api import get_clients

    _client_cache = get_clients()
    _client_cache_time = now
    return _client_cache


def _cached_v1_devices() -> list:
    """Fetch v1 devices with TTL cache."""
    global _v1_device_cache, _v1_device_cache_time
    now = time.monotonic()
    if _v1_device_cache is not None and (now - _v1_device_cache_time) < _CACHE_TTL:
        return _v1_device_cache
    from unifi.api import get_v1_devices

    _v1_device_cache = get_v1_devices()
    _v1_device_cache_time = now
    return _v1_device_cache


def _cached_v1_clients() -> list:
    """Fetch v1 clients with TTL cache."""
    global _v1_client_cache, _v1_client_cache_time
    now = time.monotonic()
    if _v1_client_cache is not None and (now - _v1_client_cache_time) < _CACHE_TTL:
        return _v1_client_cache
    from unifi.api import get_v1_clients

    _v1_client_cache = get_v1_clients()
    _v1_client_cache_time = now
    return _v1_client_cache


def _cached_traffic_matching(include_items: bool = True) -> list:
    """Fetch traffic matching lists with TTL cache.

    Uses separate cache pairs for full (include_items=True) and summary
    (include_items=False) modes so callers don't evict each other's data.
    """
    global _traffic_matching_full_cache, _traffic_matching_full_cache_time
    global _traffic_matching_summary_cache, _traffic_matching_summary_cache_time
    now = time.monotonic()
    if include_items:
        if _traffic_matching_full_cache is not None and (now - _traffic_matching_full_cache_time) < _CACHE_TTL:
            return _traffic_matching_full_cache
        from unifi.api import get_traffic_matching_lists

        _traffic_matching_full_cache = get_traffic_matching_lists(include_items=True)
        _traffic_matching_full_cache_time = now
        return _traffic_matching_full_cache
    else:
        if _traffic_matching_summary_cache is not None and (now - _traffic_matching_summary_cache_time) < _CACHE_TTL:
            return _traffic_matching_summary_cache
        from unifi.api import get_traffic_matching_lists

        _traffic_matching_summary_cache = get_traffic_matching_lists(include_items=False)
        _traffic_matching_summary_cache_time = now
        return _traffic_matching_summary_cache


# ── Devices ──


@mcp.tool(annotations=_READ_ONLY)
def device_list() -> str:
    """List all adopted UniFi devices - access points, switches, and gateways
    with firmware version, IP, uptime, and upgrade status.

    When api_key is configured, each device is enriched with v1 API fields:
    features (list of capability strings) and interfaces (port/radio detail).

    Returns JSON: {devices: [{name, model, type, ip, version, uptime_seconds,
    adopted, upgradable, features (v1), interfaces (v1)}], count}
    """
    try:
        from unifi.config import load_config

        devices = _cached_devices()
        config = load_config()
        if config.get("api_key"):
            try:
                v1_devices = _cached_v1_devices()
                v1_by_mac = {_normalize_mac(d["mac"]): d for d in v1_devices}
                for device in devices:
                    v1 = v1_by_mac.get(_normalize_mac(device.get("mac", "")), {})
                    device["features"] = v1.get("features", [])
                    device["interfaces"] = v1.get("interfaces")
            except Exception:
                logger.warning("v1 device enrichment failed; returning legacy-only data")
                for device in devices:
                    device.pop("features", None)
                    device.pop("interfaces", None)
        return json.dumps({"devices": devices, "count": len(devices)})
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Clients ──


@mcp.tool(annotations=_READ_ONLY)
def client_list(network: str | None = None) -> str:
    """List all connected clients with IP, network, signal strength, and bandwidth.

    When api_key is configured, each client is enriched with v1 API fields:
    connected_at (ISO timestamp of first connection) and access (access metadata).

    Args:
        network: Optional network/VLAN name filter (case-insensitive partial match)

    Returns JSON: {clients: [{name, mac, ip, network, essid, is_wired,
    signal, rx_bytes, tx_bytes, uptime_seconds, connected_at (v1), access (v1)}], count}
    """
    try:
        from unifi.config import load_config

        clients = _cached_clients()
        config = load_config()
        if config.get("api_key"):
            try:
                v1_clients = _cached_v1_clients()
                v1_by_mac = {c["mac"]: c for c in v1_clients}  # already normalized in get_v1_clients()
                for client in clients:
                    v1 = v1_by_mac.get(_normalize_mac(client.get("mac", "")), {})
                    client["connected_at"] = v1.get("connected_at", "")
                    client["access"] = v1.get("access")
            except Exception:
                logger.warning("v1 client enrichment failed; returning legacy-only data")
                for client in clients:
                    client.pop("connected_at", None)
                    client.pop("access", None)
        if network:
            network_lower = network.lower()
            clients = [
                c
                for c in clients
                if network_lower in (c.get("network") or "").lower() or network_lower in (c.get("essid") or "").lower()
            ]
        return json.dumps({"clients": clients, "count": len(clients)})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_ONLY)
def client_count() -> str:
    """Quick count of connected clients by network and connection type.

    Returns JSON: {total, wired, wireless, by_network: {name: count}}
    """
    try:
        clients = _cached_clients()
        wired = sum(1 for c in clients if c.get("is_wired"))
        wireless = len(clients) - wired
        by_network = {}
        for c in clients:
            net = c.get("network") or c.get("essid") or "unknown"
            by_network[net] = by_network.get(net, 0) + 1
        return json.dumps(
            {
                "total": len(clients),
                "wired": wired,
                "wireless": wireless,
                "by_network": by_network,
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Device Stats (v1 API) ──


@mcp.tool(annotations=_READ_ONLY)
def device_stats(device: str | None = None) -> str:
    """Per-device CPU, memory, uplink rates, and load averages.

    Requires api_key to be configured (UniFi Network 10.x+ v1 API).

    Args:
        device: Device name or MAC address. Omit to return stats for all devices.

    Returns JSON: [{name, model, ip, uptimeSec, cpuUtilizationPct,
    memoryUtilizationPct, loadAverage1Min, loadAverage5Min, loadAverage15Min,
    uplink: {txRateBps, rxRateBps}, interfaces}]
    or {error} if api_key is not configured.
    """
    try:
        from unifi.api import get_all_device_stats, _find_v1_device, get_device_stats

        if device:
            d = _find_v1_device(device)
            stats = get_device_stats(d["id"])
            result = [{"name": d["name"], "model": d["model"], "ip": d["ip"], **stats}]
        else:
            result = get_all_device_stats()
        return json.dumps({"devices": result, "count": len(result)})
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Networks / VLANs ──


@mcp.tool(annotations=_READ_ONLY)
def vlan_list() -> str:
    """List all network/VLAN configurations with subnets, DHCP ranges,
    and firewall zone assignments.

    Returns JSON: {networks: [{name, vlan_id, subnet, dhcp_enabled,
    dhcp_start, dhcp_stop, firewall_zone_id, enabled}]}
    """
    try:
        from unifi.api import get_networks

        networks = get_networks()
        return json.dumps({"networks": networks, "count": len(networks)})
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── WLANs ──


@mcp.tool(annotations=_READ_ONLY)
def wlan_list() -> str:
    """List all WiFi SSIDs with security mode, VLAN assignment, and status.

    Returns JSON: {wlans: [{name, enabled, security, wpa_mode,
    network_id, is_guest, hide_ssid}]}
    """
    try:
        from unifi.api import get_wlans

        wlans = get_wlans()
        return json.dumps({"wlans": wlans, "count": len(wlans)})
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── VPN (v1 API) ──


@mcp.tool(annotations=_READ_ONLY)
def vpn_status() -> str:
    """VPN server configurations and site-to-site tunnel overviews.

    Combines two v1 API endpoints:
    - vpn/servers: OpenVPN and UID VPN servers (type, id, name, enabled)
    - vpn/site-to-site-tunnels: IPsec, OpenVPN, and WireGuard tunnels (type, id, name)

    Requires api_key to be configured (UniFi Network 10.x+ v1 API).

    Returns JSON: {servers: [{type, id, name, enabled, ...}], server_count,
    tunnels: [{type, id, name, ...}], tunnel_count}
    """
    try:
        from unifi.api import get_vpn_status

        return json.dumps(get_vpn_status())
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── WAN ──


@mcp.tool(annotations=_READ_ONLY)
def wan_status() -> str:
    """WAN interface health - connectivity, latency, and speedtest results
    for primary and failover links.

    Returns JSON: {wans: [{status, uptime_seconds, latency_avg,
    speedtest_download, speedtest_upload, rx_bytes, tx_bytes}]}
    """
    try:
        from unifi.api import get_wan_status

        wans = get_wan_status()
        return json.dumps({"wans": wans, "count": len(wans)})
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── ACL Rules (v1 API) ──


@mcp.tool(annotations=_READ_ONLY)
def acl_rule_list() -> str:
    """List all ACL rules sorted by priority index (lower index = higher priority).

    Rules come in two types:
    - IPV4: filter by IP address, subnet, network ID, or port; optional protocolFilter (TCP/UDP)
    - MAC: filter by MAC address with an optional networkIdFilter scope

    sourceFilter and destinationFilter are polymorphic objects whose shape depends
    on the rule type. enforcingDeviceFilter is null when the rule applies to all switches.

    Requires api_key to be configured (UniFi Network 10.x+ v1 API).

    Returns JSON: {rules: [{id, type, name, description, enabled, action, index,
    enforcing_device_filter, source_filter, destination_filter, origin,
    protocol_filter (IPV4 only), network_id_filter (MAC only)}], count}
    """
    try:
        from unifi.api import get_acl_rules

        rules = get_acl_rules()
        return json.dumps({"rules": rules, "count": len(rules)})
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Traffic Matching Lists (v1 API) ──


@mcp.tool(annotations=_READ_ONLY)
def traffic_matching_list(include_items: bool = True) -> str:
    """List all traffic matching lists (IPV4, IPV6, PORT) used in firewall policies and ACL rules.

    By default fetches the full item details for each list (N+1 calls).
    Set include_items=False for a summary-only listing (type, id, name).

    Items are polymorphic by list type:
    - IPV4/IPV6 lists: ADDRESS (value), ADDRESS_RANGE (start, stop), SUBNET (value)
    - PORT lists: NUMBER (value), NUMBER_RANGE (start, stop)

    Requires api_key to be configured (UniFi Network 10.x+ v1 API).

    Returns JSON: {lists: [{type, id, name, items: [{type, value/start/stop}]}], count}
    """
    try:
        lists = _cached_traffic_matching(include_items=include_items)
        return json.dumps({"lists": lists, "count": len(lists)})
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Firewall Policies (v1 API) ──


@mcp.tool(annotations=_READ_ONLY)
def firewall_policy_list() -> str:
    """List all custom firewall policies with source/dest zones, action, and ordering.

    Requires api_key to be configured (UniFi Network 10.x+ v1 API).
    Use firewall_zones to resolve zone IDs to names.

    Returns JSON: {policies: [{id, name, description, enabled, index, action,
    source_zone_id, source_filter, destination_zone_id, destination_filter,
    ip_version, connection_states, logging_enabled, schedule_mode, origin}], count}
    """
    try:
        from unifi.api import get_firewall_policies

        policies = get_firewall_policies()
        return json.dumps({"policies": policies, "count": len(policies)})
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Security ──


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    )
)
def device_restart(device: str) -> str:
    """Restart a UniFi device by name or MAC address.

    This sends a restart command to the device via the UniFi controller.
    The device will be temporarily offline during reboot (typically 30-90 seconds).
    Requires api_key to be configured (UniFi Network 10.x+ v1 API).

    Args:
        device: Device name (e.g. 'Living Room AP') or MAC address.

    Returns JSON: {status, device_name, device_id}
    """
    try:
        from unifi.api import _find_v1_device, restart_device

        d = _find_v1_device(device)
        restart_device(d["id"])
        return json.dumps({"status": "restarting", "device_name": d["name"], "device_id": d["id"]})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_ONLY)
def ips_status() -> str:
    """IPS/IDS configuration - mode (ips vs ids), enabled detection categories,
    and suppressed alerts.

    Returns JSON: {mode, enabled, enabled_categories, suppressed_alerts}
    """
    try:
        from unifi.api import get_ips_settings

        data = get_ips_settings()
        data["category_count"] = len(data.get("enabled_categories", []))
        return json.dumps(data)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_ONLY)
def ips_suppressed_list() -> str:
    """List all suppressed IPS alert signatures currently configured.

    Returns JSON: {suppressed_alerts: [{signature, category, ...}], count}
    Note: UniFi Network 10.x uses signature-based suppression (not destination IP).
    """
    try:
        from unifi.api import get_suppressed_ips

        entries = get_suppressed_ips()
        return json.dumps({"suppressed_alerts": entries, "count": len(entries)})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def ips_suppress_destination(ip_or_cidr: str) -> str:
    """Suppress IPS scanning-activity alerts for connections destined to a given CIDR.

    Adds a signature-level suppression entry for the emerging-scan category,
    keyed to the given CIDR. Use this to stop IPS from blocking connections
    to known-good hosts like GitHub SSH servers (140.82.112.0/24, 192.30.252.0/22).

    Note: UniFi Network 10.x does not support destination-IP-based suppression.
    This uses signature suppression with a deterministic key of suppress:dst:{cidr}.

    Args:
        ip_or_cidr: IPv4 CIDR to suppress scanning alerts for (e.g. '140.82.112.0/24')

    Returns JSON: {status, ip_or_cidr, suppressed_alerts}
    status is 'added' or 'already_present'.
    """
    try:
        from unifi.api import suppress_ips_destination

        result = suppress_ips_destination(ip_or_cidr)
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def ips_remove_category(category: str) -> str:
    """Remove a detection category from IPS enabled_categories.

    This is the reliable way to stop IPS from blocking traffic matching a
    category's rules. Use this when signature suppression doesn't work (e.g.
    'emerging-scan' blocks bulk GitHub SSH pushes).

    Args:
        category: Category name to disable, e.g. 'emerging-scan'

    Returns JSON: {status, enabled_categories}
    status is 'removed' or 'not_present'.
    """
    try:
        from unifi.api import remove_ips_category

        result = remove_ips_category(category)
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_ONLY)
def firewall_zones() -> str:
    """Firewall zone assignments - which network/VLAN is in which security zone.

    Returns JSON: {zones: [{network, vlan_id, zone_id, zone_name}]}
    """
    try:
        from unifi.api import get_firewall_zones

        zones = get_firewall_zones()
        return json.dumps({"zones": zones, "count": len(zones)})
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── DNS ──


@mcp.tool(annotations=_READ_ONLY)
def dns_config() -> str:
    """DNS configuration - DoH state and configured resolvers.

    Returns JSON: {doh_state, dns_servers}
    """
    try:
        from unifi.api import get_dns_settings

        data = get_dns_settings()
        return json.dumps(data)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Pending Devices ──


@mcp.tool(annotations=_READ_ONLY)
def pending_device_list() -> str:
    """List devices discovered on the network but not yet adopted into UniFi.

    Useful when adding new switches, APs, or cameras - shows what is waiting
    for adoption without opening the UniFi dashboard.
    Requires api_key (v1 API).

    Returns JSON: {devices: [...], count}
    """
    try:
        from unifi.api import get_pending_devices

        devices = get_pending_devices()
        if not isinstance(devices, list):
            devices = []
        return json.dumps({"devices": devices, "count": len(devices)})
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── DNS Policies ──


@mcp.tool(annotations=_READ_ONLY)
def dns_policy_list() -> str:
    """List DNS routing and filtering policies.

    These are per-network or per-client DNS routing rules (e.g. route IoT VLAN
    through Pi-hole). Distinct from dns_config which returns resolver settings.
    Requires api_key (v1 API).

    Returns JSON: {policies: [...], count}
    """
    try:
        from unifi.api import get_dns_policies

        policies = get_dns_policies()
        if not isinstance(policies, list):
            policies = []
        return json.dumps({"policies": policies, "count": len(policies)})
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── WAN Detail ──


@mcp.tool(annotations=_READ_ONLY)
def wan_detail() -> str:
    """Richer WAN interface data from the v1 API.

    Complements wan_status (which uses the legacy stat endpoint) with
    structured WAN configuration including IP addressing, failover config,
    and uplink type from the v1 wans endpoint.
    Requires api_key (v1 API).

    Returns JSON: {wans: [...], count}
    """
    try:
        from unifi.api import get_v1_wans

        wans = get_v1_wans()
        return json.dumps({"wans": wans, "count": len(wans)})
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── DPI Lookups ──


@mcp.tool(annotations=_READ_ONLY)
def dpi_application_list() -> str:
    """List DPI application definitions - ID-to-name lookup table.

    Resolves the numeric application IDs that appear in firewall policies
    and IPS events to readable names (e.g. 1234 -> 'Netflix').
    Requires api_key (v1 API).

    Returns JSON: {applications: [...], count}
    """
    try:
        from unifi.api import get_dpi_applications

        apps = get_dpi_applications()
        if not isinstance(apps, list):
            apps = []
        return json.dumps({"applications": apps, "count": len(apps)})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_ONLY)
def dpi_category_list() -> str:
    """List DPI category definitions - ID-to-name lookup table.

    Resolves the numeric category IDs that appear in firewall policies
    and IPS events to readable names (e.g. 'Streaming').
    Requires api_key (v1 API).

    Returns JSON: {categories: [...], count}
    """
    try:
        from unifi.api import get_dpi_categories

        cats = get_dpi_categories()
        if not isinstance(cats, list):
            cats = []
        return json.dumps({"categories": cats, "count": len(cats)})
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── WiFi Broadcasts ──


@mcp.tool(annotations=_READ_ONLY)
def wifi_broadcast_list() -> str:
    """List WiFi SSID broadcasts from the v1 API.

    May include per-radio detail (2.4 GHz / 5 GHz / 6 GHz separation) beyond
    what wlan_list returns from the legacy API.
    Requires api_key (v1 API).

    Returns JSON: {broadcasts: [...], count}
    """
    try:
        from unifi.api import get_v1_wifi_broadcasts

        broadcasts = get_v1_wifi_broadcasts()
        if not isinstance(broadcasts, list):
            broadcasts = []
        return json.dumps({"broadcasts": broadcasts, "count": len(broadcasts)})
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── WiFi Networks (v1, writable) ──


@mcp.tool(annotations=_READ_ONLY)
def wifi_network_list() -> str:
    """List WLAN configurations from the v1 API with full field detail.

    Returns the writable wifi-networks resource, not the read-only broadcasts
    projection. Includes the v1 id needed for future PUT updates and all
    SSID-level settings as returned by the controller. Use this to discover
    exact field names and values before performing write operations.
    Requires api_key (v1 API).

    Returns JSON: {networks: [...], count}
    """
    try:
        from unifi.api import get_v1_wifi_networks

        networks = get_v1_wifi_networks()
        if not isinstance(networks, list):
            networks = []
        return json.dumps({"networks": networks, "count": len(networks)})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_ONLY)
def radio_list(device: str) -> str:
    """List per-radio configuration for a UniFi access point.

    Returns the raw radio config objects from interfaces.radios[] on the
    device record. Field names (channel, txPower, settingPreference, etc.)
    are returned as-is from the v1 API to aid discovery of exact names
    before write operations are performed.
    Requires api_key (v1 API).

    Args:
        device: Device name (e.g. 'Foyer AP') or MAC address.

    Returns JSON: {device_name, device_id, radios: [{...raw fields...}], count}
    """
    try:
        from unifi.api import _find_v1_device, get_device_radios

        d = _find_v1_device(device)
        radios = get_device_radios(d["id"])
        return json.dumps(
            {
                "device_name": d["name"],
                "device_id": d["id"],
                "radios": radios,
                "count": len(radios),
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Networks (v1) ──


@mcp.tool(annotations=_READ_ONLY)
def network_list() -> str:
    """List network/VLAN configuration from the v1 API.

    May include additional fields beyond vlan_list (DHCP lease count,
    gateway IP, IPv6 config). Use vlan_list for the legacy summary view.
    Requires api_key (v1 API).

    Returns JSON: {networks: [...], count}
    """
    try:
        from unifi.api import get_v1_networks

        networks = get_v1_networks()
        if not isinstance(networks, list):
            networks = []
        return json.dumps({"networks": networks, "count": len(networks)})
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── RADIUS Profiles ──


@mcp.tool(annotations=_READ_ONLY)
def radius_profile_list() -> str:
    """List RADIUS authentication profiles and their network/SSID associations.

    Returns empty list when no RADIUS profiles are configured (802.1X not in use).
    Requires api_key (v1 API).

    Returns JSON: {profiles: [...], count}
    """
    try:
        from unifi.api import get_radius_profiles

        profiles = get_radius_profiles()
        if not isinstance(profiles, list):
            profiles = []
        return json.dumps({"profiles": profiles, "count": len(profiles)})
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Device Tags ──


@mcp.tool(annotations=_READ_ONLY)
def device_tag_list() -> str:
    """List device tags used in firewall policies and ACL rules.

    Tags group devices for use as source/destination filters in policies.
    Use this to resolve tag references in firewall_policy_list and acl_rule_list.
    Requires api_key (v1 API).

    Returns JSON: {tags: [...], count}
    """
    try:
        from unifi.api import get_device_tags

        tags = get_device_tags()
        if not isinstance(tags, list):
            tags = []
        return json.dumps({"tags": tags, "count": len(tags)})
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── System Info ──


@mcp.tool(annotations=_READ_ONLY)
def system_info() -> str:
    """Controller system snapshot - software version, hostname, timezone, uptime,
    and device firmware summary (count of upgradable devices).

    Useful as a preflight check before running playbooks or making config changes.
    Device firmware summary requires api_key (v1 API); controller info uses legacy API.

    Returns JSON: {controller_version, hostname, timezone, uptime_seconds,
    autobackup, device_count, upgradable_count, upgradable_devices}
    """
    try:
        from unifi.api import get_system_info

        return json.dumps(get_system_info())
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Switch Port Actions (v1 API) ──


@mcp.tool(annotations=_READ_ONLY)
def switch_port_list(device: str) -> str:
    """List all switch ports on a device with enabled and PoE status.

    Requires the device to support the 'switching' feature (i.e. be a switch).
    Requires api_key to be configured (UniFi Network 10.x+ v1 API).

    Args:
        device: Device name (e.g. 'Office Switch') or MAC address.

    Returns JSON: {device_name, device_id, ports: [{portIdx, name, enabled, poeMode, ...}], count}
    """
    try:
        from unifi.api import _find_v1_device, get_device_ports

        d = _find_v1_device(device)
        ports = get_device_ports(d["id"])
        return json.dumps(
            {
                "device_name": d["name"],
                "device_id": d["id"],
                "switching_capable": "switching" in d.get("features", []),
                "ports": ports,
                "count": len(ports),
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )
)
def switch_port_action(device: str, port_idx: int, action: str) -> str:
    """Perform an action on a specific switch port.

    Actions:
    - enable: Re-enable a disabled port (restores connectivity).
    - disable: Shut down a port (no traffic, link down).
    - poe_enable: Turn on PoE power delivery for the port.
    - poe_disable: Cut PoE power delivery (power-cycles the connected device).

    Use switch_port_list first to find port indexes and current state.
    Requires api_key to be configured (UniFi Network 10.x+ v1 API).

    Args:
        device: Device name or MAC address.
        port_idx: Port index (portIdx from switch_port_list).
        action: One of 'enable', 'disable', 'poe_enable', 'poe_disable'.

    Returns JSON: {status, device_name, device_id, port_idx, action}
    """
    try:
        from unifi.api import _find_v1_device, get_device_ports, port_action

        d = _find_v1_device(device)
        ports = get_device_ports(d["id"])
        if not any(p.get("portIdx") == port_idx for p in ports):
            return json.dumps(
                {
                    "error": f"Port index {port_idx} not found on '{d['name']}'. Use switch_port_list to find valid indexes."
                }
            )
        port_action(d["id"], port_idx, action)
        return json.dumps(
            {
                "status": "ok",
                "device_name": d["name"],
                "device_id": d["id"],
                "port_idx": port_idx,
                "action": action,
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Client Actions ──


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def client_block(mac: str) -> str:
    """Block a client from the network by MAC address.

    Sends a block action via the v1 API. The client will lose connectivity
    immediately. Use client_unblock to restore access.
    Requires api_key (v1 API).

    Args:
        mac: Client MAC address (any format).

    Returns JSON: {status, client_id, mac}
    """
    try:
        from unifi.api import client_action

        return json.dumps(client_action(mac, "block"))
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def client_unblock(mac: str) -> str:
    """Unblock a previously blocked client by MAC address.

    Requires api_key (v1 API).

    Args:
        mac: Client MAC address (any format).

    Returns JSON: {status, client_id, mac}
    """
    try:
        from unifi.api import client_action

        return json.dumps(client_action(mac, "unblock"))
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )
)
def client_reconnect(mac: str) -> str:
    """Force a client to reconnect to the network by MAC address.

    The client will briefly disconnect and re-associate. Useful after
    changing VLAN assignments or network policies.
    Requires api_key (v1 API).

    Args:
        mac: Client MAC address (any format).

    Returns JSON: {status, client_id, mac}
    """
    try:
        from unifi.api import client_action

        return json.dumps(client_action(mac, "reconnect"))
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Entry Point ──


def main():
    """Run the MCP server."""
    logger.info("Starting UniFi MCP server...")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
