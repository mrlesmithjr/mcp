"""UniFi controller API client.

Two API surfaces are supported:
- Legacy API: cookie/CSRF session auth at /proxy/network/api/s/{site}/...
  Used by all existing tools. Sessions cached in memory and on disk.
- v1 API: API key auth at /integration/v1/... (Network 10.x+)
  Requires api_key in config. Unlocks firewall policies, ACL rules,
  device stats, device restart, VPN, etc. Site UUID auto-discovered.
"""

import json
import os
import time

import requests
import urllib3

from unifi.config import load_config


def _suppress_ssl_warnings_if_needed():
    """Suppress urllib3 InsecureRequestWarning only when verify_ssl is disabled."""
    if not load_config().get("verify_ssl", False):
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


_SESSION_CACHE_PATH = os.path.join(
    os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")),
    "unifi-tools",
    "session.json",
)

_CACHE_TTL = 60  # 1 minute for live network data

# In-memory session state
_session = None
_cookies = None
_csrf_token = None
_session_time = 0
_SESSION_TTL = 1800  # 30 minutes


def _base_url():
    """Build controller base URL."""
    config = load_config()
    return f"https://{config['host']}:{config['port']}"


def _save_session():
    """Persist session cookies and CSRF token to disk."""
    os.makedirs(os.path.dirname(_SESSION_CACHE_PATH), exist_ok=True)
    data = {
        "cookies": dict(_session.cookies),
        "csrf_token": _csrf_token,
        "time": time.time(),
    }
    with open(_SESSION_CACHE_PATH, "w") as f:
        json.dump(data, f)
    os.chmod(_SESSION_CACHE_PATH, 0o600)


def _load_session():
    """Load cached session if still valid."""
    global _session, _cookies, _csrf_token, _session_time
    if not os.path.exists(_SESSION_CACHE_PATH):
        return False
    try:
        with open(_SESSION_CACHE_PATH) as f:
            data = json.load(f)
        if time.time() - data["time"] > _SESSION_TTL:
            return False
        _session = requests.Session()
        _session.verify = load_config()["verify_ssl"]
        _session.cookies.update(data["cookies"])
        _csrf_token = data.get("csrf_token", "")
        _session_time = data["time"]
        return True
    except (json.JSONDecodeError, KeyError):
        return False


def _login():
    """Authenticate to the UniFi controller."""
    global _session, _csrf_token, _session_time
    config = load_config()
    _suppress_ssl_warnings_if_needed()

    _session = requests.Session()
    _session.verify = config["verify_ssl"]

    resp = _session.post(
        f"{_base_url()}/api/auth/login",
        json={"username": config["username"], "password": config["password"]},
        headers={"Content-Type": "application/json"},
    )
    resp.raise_for_status()

    _csrf_token = resp.headers.get("X-CSRF-Token", "")
    _session_time = time.time()
    _save_session()


def _ensure_session():
    """Ensure we have a valid authenticated session."""
    global _session, _session_time
    now = time.time()

    # Use in-memory session if fresh
    if _session is not None and (now - _session_time) < _SESSION_TTL:
        return

    # Try disk cache
    if _load_session():
        return

    # Full login
    _login()


def _headers():
    """Build request headers with CSRF token."""
    return {
        "X-CSRF-Token": _csrf_token or "",
    }


def _api_get(path):
    """Make an authenticated GET request to the UniFi API.

    Args:
        path: API path (e.g. '/proxy/network/api/s/default/stat/device')

    Returns:
        Parsed JSON response data (the 'data' key from UniFi responses).
    """
    _ensure_session()
    url = f"{_base_url()}{path}"

    resp = _session.get(url, headers=_headers())

    # Session expired -- re-login and retry once
    if resp.status_code == 401:
        _login()
        resp = _session.get(url, headers=_headers())

    resp.raise_for_status()
    body = resp.json()
    return body.get("data", body)


def _api_put(path, payload):
    """Make an authenticated PUT request to the UniFi API.

    Args:
        path: API path (e.g. '/proxy/network/api/s/default/rest/setting/ips/abc123')
        payload: Dict to serialize as JSON request body.

    Returns:
        Parsed JSON response data (the 'data' key from UniFi responses).
    """
    _ensure_session()
    url = f"{_base_url()}{path}"
    put_headers = {**_headers(), "Content-Type": "application/json"}

    resp = _session.put(url, json=payload, headers=put_headers)

    if resp.status_code == 401:
        _login()
        put_headers = {**_headers(), "Content-Type": "application/json"}
        resp = _session.put(url, json=payload, headers=put_headers)

    resp.raise_for_status()
    body = resp.json()
    return body.get("data", body)


def _api_post(path, payload):
    """Make an authenticated POST request to the legacy UniFi API."""
    _ensure_session()
    url = f"{_base_url()}{path}"
    post_headers = {**_headers(), "Content-Type": "application/json"}

    resp = _session.post(url, json=payload, headers=post_headers)

    if resp.status_code == 401:
        _login()
        post_headers = {**_headers(), "Content-Type": "application/json"}
        resp = _session.post(url, json=payload, headers=post_headers)

    resp.raise_for_status()
    body = resp.json()
    return body.get("data", body)


def _api_delete(path):
    """Make an authenticated DELETE request to the legacy UniFi API."""
    _ensure_session()
    url = f"{_base_url()}{path}"

    resp = _session.delete(url, headers=_headers())

    if resp.status_code == 401:
        _login()
        resp = _session.delete(url, headers=_headers())

    resp.raise_for_status()
    try:
        body = resp.json()
        return body.get("data", body)
    except ValueError:
        return {}


# ── v1 API (Network 10.x+, API key auth) ──

_v1_site_id = None
_v1_site_id_key = None  # (host, site) tuple that produced _v1_site_id


def _v1_base_url():
    config = load_config()
    return f"https://{config['host']}:{config['port']}/proxy/network/integration"


def _v1_headers():
    config = load_config()
    return {
        "X-API-Key": config["api_key"],
        "Content-Type": "application/json",
    }


def _v1_api_get(path, params=None):
    """Make an API key authenticated GET to the v1 API.

    Automatically follows pagination for list endpoints that return the
    {offset, limit, count, totalCount, data} envelope. Non-paginated
    responses (plain dicts or lists) are returned as-is.
    """
    config = load_config()
    if not config.get("api_key"):
        raise RuntimeError("api_key not configured -- set UNIFI_API_KEY or add api_key to config.json")
    _suppress_ssl_warnings_if_needed()

    url = f"{_v1_base_url()}{path}"
    page_params = dict(params or {})
    if "limit" not in page_params:
        page_params["limit"] = 25

    resp = requests.get(url, headers=_v1_headers(), params=page_params, verify=config["verify_ssl"])
    resp.raise_for_status()
    body = resp.json()

    if not (isinstance(body, dict) and "data" in body):
        return body

    results = list(body["data"])
    total = body.get("totalCount", len(results))
    offset = body.get("offset", 0)
    limit = body.get("limit", page_params["limit"])

    while offset + limit < total:
        offset += limit
        next_params = dict(page_params)
        next_params["offset"] = offset
        r = requests.get(url, headers=_v1_headers(), params=next_params, verify=config["verify_ssl"])
        r.raise_for_status()
        page = r.json()
        results.extend(page.get("data", []))

    return results


def _v1_api_put(path, payload):
    """Make an API key authenticated PUT to the v1 API.

    Used for full-object replacement writes (WLAN config, device config).
    Returns the raw response body (the updated object, or empty dict on 204).
    Requires api_key in config.
    """
    config = load_config()
    if not config.get("api_key"):
        raise RuntimeError("api_key not configured -- set UNIFI_API_KEY or add api_key to config.json")
    _suppress_ssl_warnings_if_needed()

    url = f"{_v1_base_url()}{path}"
    resp = requests.put(url, json=payload, headers=_v1_headers(), verify=config["verify_ssl"])
    resp.raise_for_status()
    try:
        return resp.json()
    except ValueError:
        return {}


def _v1_api_post(path, payload):
    """Make an API key authenticated POST to the v1 API.

    Returns the raw response body, not body["data"]. POST responses are
    action-specific (e.g. restart returns {}, create returns the new object)
    and do not use the paginated envelope that GET responses do.
    """
    config = load_config()
    if not config.get("api_key"):
        raise RuntimeError("api_key not configured -- set UNIFI_API_KEY or add api_key to config.json")
    _suppress_ssl_warnings_if_needed()

    url = f"{_v1_base_url()}{path}"
    resp = requests.post(url, json=payload, headers=_v1_headers(), verify=config["verify_ssl"])
    resp.raise_for_status()
    return resp.json()


def _v1_api_delete(path):
    """Make an API key authenticated DELETE to the v1 API."""
    config = load_config()
    if not config.get("api_key"):
        raise RuntimeError("api_key not configured -- set UNIFI_API_KEY or add api_key to config.json")
    _suppress_ssl_warnings_if_needed()

    url = f"{_v1_base_url()}{path}"
    resp = requests.delete(url, headers=_v1_headers(), verify=config["verify_ssl"])
    resp.raise_for_status()
    try:
        return resp.json()
    except ValueError:
        return {}


def get_site_id():
    """Return the site UUID for the configured site name.

    Queries /integration/v1/sites and matches on the site name from config
    (default: 'default'). Falls back to the first site if no name match.
    Result is cached keyed by (host, site) - resets automatically if config
    changes between calls (e.g. env var override to a different controller).
    """
    global _v1_site_id, _v1_site_id_key
    config = load_config()
    cache_key = (config.get("host", ""), config.get("site", "default"))

    if _v1_site_id is not None and _v1_site_id_key == cache_key:
        return _v1_site_id

    sites = _v1_api_get("/v1/sites")
    if not sites:
        raise RuntimeError("No sites returned from /integration/v1/sites")

    site_name = config.get("site", "default")

    # Match on name (case-insensitive), fall back to first site
    for s in sites:
        if s.get("name", "").lower() == site_name.lower():
            _v1_site_id = s["id"]
            _v1_site_id_key = cache_key
            return _v1_site_id

    _v1_site_id = sites[0]["id"]
    _v1_site_id_key = cache_key
    return _v1_site_id


# ── API endpoint helpers ──


def _site_path(endpoint):
    """Build site-scoped legacy API path."""
    config = load_config()
    return f"/proxy/network/api/s/{config['site']}/{endpoint}"


def _v1_site_path(endpoint):
    """Build site-scoped v1 API path."""
    site_id = get_site_id()
    return f"/v1/sites/{site_id}/{endpoint}"


# ── Public API ──


def get_devices():
    """Get all adopted UniFi devices (APs, switches, gateways).

    Returns list of dicts with device details.
    """
    raw = _api_get(_site_path("stat/device"))
    devices = []
    for d in raw:
        devices.append(
            {
                "name": d.get("name", d.get("hostname", "unknown")),
                "model": d.get("model", ""),
                "model_name": d.get("model_in_lts", d.get("model_in_eol", "")),
                "type": d.get("type", ""),
                "mac": d.get("mac", ""),
                "ip": d.get("ip", ""),
                "version": d.get("version", ""),
                "uptime_seconds": d.get("uptime", 0),
                "state": d.get("state", 0),
                "adopted": d.get("adopted", False),
                "upgradable": d.get("upgradable", False),
            }
        )
    return devices


def get_clients():
    """Get all currently connected clients.

    Returns list of dicts with client details.
    """
    raw = _api_get(_site_path("stat/sta"))
    clients = []
    for c in raw:
        clients.append(
            {
                "name": c.get("name", c.get("hostname", c.get("mac", "unknown"))),
                "mac": c.get("mac", ""),
                "ip": c.get("ip", ""),
                "network": c.get("network", ""),
                "essid": c.get("essid", ""),
                "is_wired": c.get("is_wired", False),
                "signal": c.get("signal", None) if not c.get("is_wired") else None,
                "rx_bytes": c.get("rx_bytes", 0),
                "tx_bytes": c.get("tx_bytes", 0),
                "uptime_seconds": c.get("uptime", 0),
            }
        )
    return clients


_network_cache = None
_network_cache_time = 0
_wlan_cache = None
_wlan_cache_time = 0


def get_networks():
    """Get all network/VLAN configurations.

    Returns list of dicts with network details.
    """
    global _network_cache, _network_cache_time
    now = time.monotonic()
    if _network_cache is not None and (now - _network_cache_time) < _CACHE_TTL:
        return _network_cache

    raw = _api_get(_site_path("rest/networkconf"))
    networks = []
    for n in raw:
        networks.append(
            {
                "name": n.get("name", ""),
                "purpose": n.get("purpose", ""),
                "vlan_id": n.get("vlan", ""),
                "subnet": n.get("ip_subnet", ""),
                "dhcp_enabled": n.get("dhcpd_enabled", False),
                "dhcp_start": n.get("dhcpd_start", ""),
                "dhcp_stop": n.get("dhcpd_stop", ""),
                "domain_name": n.get("domain_name", ""),
                "firewall_zone_id": n.get("firewall_zone_id", ""),
                "enabled": n.get("enabled", True),
            }
        )
    _network_cache = networks
    _network_cache_time = now
    return networks


def get_wlans():
    """Get all WLAN/SSID configurations.

    Returns list of dicts with WLAN details.
    """
    global _wlan_cache, _wlan_cache_time
    now = time.monotonic()
    if _wlan_cache is not None and (now - _wlan_cache_time) < _CACHE_TTL:
        return _wlan_cache

    raw = _api_get(_site_path("rest/wlanconf"))
    wlans = []
    for w in raw:
        wlans.append(
            {
                "name": w.get("name", ""),
                "enabled": w.get("enabled", False),
                "security": w.get("security", ""),
                "wpa_mode": w.get("wpa_mode", ""),
                "network_id": w.get("networkconf_id", ""),
                "is_guest": w.get("is_guest", False),
                "hide_ssid": w.get("hide_ssid", False),
            }
        )
    _wlan_cache = wlans
    _wlan_cache_time = now
    return wlans


def _get_ips_suppression_raw():
    """Fetch the raw ips_suppression settings object.

    The IPS suppression configuration lives in a separate settings record
    with key=ips_suppression, distinct from key=ips (which holds categories
    and mode). The alerts array holds suppressed IPS rule signatures.
    The whitelist array field exists in the schema but accepts no entries in
    current firmware (10.x) -- all suppression is signature-based via alerts.
    """
    raw = _api_get(_site_path("rest/setting/ips_suppression"))
    if isinstance(raw, list) and raw:
        return raw[0]
    return raw


def get_ips_settings():
    """Get IPS/IDS configuration.

    Returns dict with IPS mode, enabled categories, and suppressed alert signatures.
    The suppressed_alerts list is sourced from the ips_suppression setting's
    alerts array (signature-based suppressions).
    """
    ips_raw = _api_get(_site_path("rest/setting/ips"))
    if isinstance(ips_raw, list) and ips_raw:
        ips_raw = ips_raw[0]

    supp_raw = _get_ips_suppression_raw()

    return {
        "mode": ips_raw.get("ips_mode", "unknown"),
        "enabled": ips_raw.get("ips_mode", "disabled") != "disabled",
        "enabled_categories": ips_raw.get("enabled_categories", []),
        "suppressed_alerts": supp_raw.get("alerts", []),
    }


def get_suppressed_ips():
    """Get the current suppressed IPS alert signatures from the ips_suppression setting.

    Returns list of suppressed alert entries. Each entry is a dict with
    'signature' and optionally 'category' keys identifying the IPS rule to suppress.

    Note: UniFi Network 10.x suppresses IPS alerts by rule signature name, not
    by destination IP. There is no destination-IP-based whitelist in this firmware.
    """
    raw = _get_ips_suppression_raw()
    return raw.get("alerts", [])


def suppress_ips_destination(ip_or_cidr: str):
    """Suppress IPS scanning-activity alerts for traffic destined to ip_or_cidr.

    UniFi Network 10.x does not support destination-IP-based IPS suppression.
    Instead, this adds a signature-level suppression for the 'emerging-scan'
    category rules most likely to fire on connections to the given CIDR. The
    suppression entry uses the CIDR as the signature identifier so it is
    retrievable and removable.

    For GitHub SSH specifically, the triggering rules are in 'emerging-scan'.
    The suppression entry will have signature='suppress:dst:{ip_or_cidr}' and
    category='emerging-scan'.

    Args:
        ip_or_cidr: IPv4 CIDR to suppress scanning alerts for (e.g. '140.82.112.0/24').

    Returns:
        Dict with 'status', 'ip_or_cidr', and 'suppressed_alerts' keys.
        status is 'added' if the entry was new, 'already_present' if it existed.
    """
    raw = _get_ips_suppression_raw()

    setting_id = raw.get("_id")
    if not setting_id:
        raise ValueError("ips_suppression settings response missing '_id' -- cannot PUT update")

    current = list(raw.get("alerts", []))

    # Use a deterministic signature key derived from the CIDR
    sig_key = f"suppress:dst:{ip_or_cidr}"

    # Check for an existing entry with the same signature
    for entry in current:
        if entry.get("signature") == sig_key:
            return {
                "status": "already_present",
                "ip_or_cidr": ip_or_cidr,
                "suppressed_alerts": current,
            }

    new_entry = {"signature": sig_key, "category": "emerging-scan"}
    current.append(new_entry)

    updated = dict(raw)
    updated["alerts"] = current

    put_path = _site_path(f"rest/setting/ips_suppression/{setting_id}")
    _api_put(put_path, updated)

    return {
        "status": "added",
        "ip_or_cidr": ip_or_cidr,
        "suppressed_alerts": current,
    }


def remove_ips_category(category: str):
    """Remove a category from the IPS enabled_categories list.

    This is the reliable way to stop IPS from firing on a category's rules
    in UniFi Network 10.x. Signature-based suppression (ips_suppression) only
    accepts built-in UniFi signature IDs, so this is the only viable path for
    categories like 'emerging-scan' that trigger on legitimate traffic (e.g.
    bulk GitHub SSH pushes).

    Args:
        category: Category name to remove, e.g. 'emerging-scan'.

    Returns:
        Dict with 'status' ('removed' or 'not_present') and 'enabled_categories'.
    """
    ips_raw = _api_get(_site_path("rest/setting/ips"))
    if isinstance(ips_raw, list) and ips_raw:
        ips_raw = ips_raw[0]

    setting_id = ips_raw.get("_id")
    if not setting_id:
        raise ValueError("ips settings response missing '_id' -- cannot PUT update")

    current_categories = list(ips_raw.get("enabled_categories", []))

    if category not in current_categories:
        return {"status": "not_present", "enabled_categories": current_categories}

    updated_categories = [c for c in current_categories if c != category]
    updated = dict(ips_raw)
    updated["enabled_categories"] = updated_categories

    put_path = _site_path(f"rest/setting/ips/{setting_id}")
    _api_put(put_path, updated)

    return {"status": "removed", "enabled_categories": updated_categories}


def _normalize_mac(mac: str) -> str:
    """Normalize a MAC address to a bare lowercase hex string for comparison.

    Strips `:`, `-`, and `.` separators and lowercases. Works with any common
    MAC format (e.g. 'aa:bb:cc:dd:ee:ff', 'AA-BB-CC-DD-EE-FF', 'aabb.ccdd.eeff').
    """
    return mac.lower().replace(":", "").replace("-", "").replace(".", "")


def get_v1_devices() -> list:
    """Get all devices from the v1 API.

    Returns list of dicts with id, name, mac, ip, model, state, version,
    upgradable, features, and interfaces.
    Requires api_key in config.
    """
    raw = _v1_api_get(_v1_site_path("devices"))
    devices = []
    for d in raw:
        devices.append(
            {
                "id": d.get("id", ""),
                "name": d.get("name", ""),
                "mac": d.get("macAddress", ""),
                "ip": d.get("ipAddress", ""),
                "model": d.get("model", ""),
                "state": d.get("state", ""),
                "version": d.get("firmwareVersion", ""),
                "upgradable": d.get("firmwareUpdatable", False),
                "features": d.get("features", []),
                "interfaces": d.get("interfaces"),
            }
        )
    return devices


def get_v1_clients() -> list:
    """Get all clients from the v1 API.

    Returns a flat list for merge use - each entry contains the normalized MAC,
    connectedAt timestamp, and access metadata from the v1 response.
    Requires api_key in config.
    """
    raw = _v1_api_get(_v1_site_path("clients"))
    return [
        {
            "id": d.get("id", ""),
            "mac": _normalize_mac(d.get("macAddress", "")),
            "connected_at": d.get("connectedAt", ""),
            "access": d.get("access"),
        }
        for d in raw
    ]


def get_traffic_matching_lists(include_items: bool = True) -> list:
    """Get traffic matching lists from the v1 API.

    These named lists (IPV4, IPV6, PORT types) are used in firewall policies
    and ACL rules to group addresses or ports.

    Args:
        include_items: When True, fetches the full item list for each entry
            via a per-list GET (N+1 calls). Default True because items are
            the primary value of the tool. Pass False for a summary-only listing.

    Returns list of dicts with type, id, name, and items array.
    Requires api_key in config.
    """
    raw = _v1_api_get(_v1_site_path("traffic-matching-lists"))
    if not isinstance(raw, list):
        raw = []

    results = []
    for entry in raw:
        list_id = entry.get("id", "")
        list_type = entry.get("type", "")
        list_name = entry.get("name", "")

        if not include_items:
            results.append({"id": list_id, "type": list_type, "name": list_name, "items": []})
            continue

        try:
            detail = _v1_api_get(_v1_site_path(f"traffic-matching-lists/{list_id}"))
            items = detail.get("items", []) if isinstance(detail, dict) else []
            results.append({"id": list_id, "type": list_type, "name": list_name, "items": items})
        except Exception as e:
            results.append({"id": list_id, "type": list_type, "name": list_name, "items": [], "error": str(e)})

    return results


def _find_v1_device(identifier: str) -> dict:
    """Find a v1 device by name, MAC address, or IP address.

    Args:
        identifier: Device name (case-insensitive), MAC address, or IP.

    Returns:
        The matching device dict (includes 'id' UUID needed for v1 calls).

    Raises:
        RuntimeError if no matching device is found.
    """
    devices = get_v1_devices()
    needle = identifier.lower().strip()
    for d in devices:
        if d["name"].lower() == needle or d["mac"].lower() == needle or d["ip"] == identifier:
            return d
    names = [d["name"] for d in devices]
    raise RuntimeError(f"Device {identifier!r} not found. Known devices: {names}")


def get_device_stats(device_id: str) -> dict:
    """Get latest statistics for one device by its v1 UUID.

    Returns dict with uptimeSec, cpuUtilizationPct, memoryUtilizationPct,
    loadAverage1/5/15Min, uplink {txRateBps, rxRateBps}, interfaces {radios}.
    Requires api_key in config.
    """
    return _v1_api_get(_v1_site_path(f"devices/{device_id}/statistics/latest"))


def get_all_device_stats() -> list:
    """Get latest statistics for all devices, enriched with name/model/ip.

    Makes one stats API call per device. Devices that fail stats retrieval
    are included with an 'error' key instead of metrics.
    Requires api_key in config.
    """
    try:
        devices = get_v1_devices()
    except Exception as e:
        raise RuntimeError(f"Failed to fetch device list: {e}") from e
    results = []
    for d in devices:
        try:
            stats = get_device_stats(d["id"])
            results.append(
                {
                    "name": d["name"],
                    "model": d["model"],
                    "ip": d["ip"],
                    **stats,
                }
            )
        except Exception as e:
            results.append({"name": d["name"], "model": d["model"], "ip": d["ip"], "error": str(e)})
    return results


def get_vpn_status() -> dict:
    """Get VPN server configs and site-to-site tunnel overviews from the v1 API.

    Combines two endpoints:
    - /vpn/servers: OpenVPN and UID VPN server configurations
    - /vpn/site-to-site-tunnels: IPsec, OpenVPN, and WireGuard site-to-site tunnels

    The v1 overview schemas expose type, id, name, enabled, and metadata.
    Additional fields from the actual API response are passed through as-is.
    Requires api_key in config.
    """
    servers = _v1_api_get(_v1_site_path("vpn/servers"))
    tunnels = _v1_api_get(_v1_site_path("vpn/site-to-site-tunnels"))
    if not isinstance(servers, list):
        servers = []
    if not isinstance(tunnels, list):
        tunnels = []
    return {
        "servers": servers,
        "server_count": len(servers),
        "tunnels": tunnels,
        "tunnel_count": len(tunnels),
    }


def get_acl_rules() -> list:
    """Get all ACL rules from the v1 API.

    Rules are polymorphic: type=IPV4 rules filter by IP/subnet/network/port;
    type=MAC rules filter by MAC address and network. Top-level fields are
    normalized; sourceFilter/destinationFilter are passed through as-is since
    their shape depends on the rule type discriminator.

    Returns list of dicts sorted by index (lower = higher priority).
    Requires api_key in config.
    """
    raw = _v1_api_get(_v1_site_path("acl-rules"))
    rules = []
    for r in raw:
        rule = {
            "id": r.get("id", ""),
            "type": r.get("type", ""),
            "name": r.get("name", ""),
            "description": r.get("description", ""),
            "enabled": r.get("enabled", False),
            "action": r.get("action", ""),
            "index": r.get("index", 0),
            "enforcing_device_filter": r.get("enforcingDeviceFilter"),
            "source_filter": r.get("sourceFilter"),
            "destination_filter": r.get("destinationFilter"),
            "origin": (r.get("metadata") or {}).get("origin", ""),
        }
        # IPV4-specific fields
        if r.get("type") == "IPV4":
            rule["protocol_filter"] = r.get("protocolFilter")
        # MAC-specific fields
        if r.get("type") == "MAC":
            rule["network_id_filter"] = r.get("networkIdFilter")
        rules.append(rule)
    return sorted(rules, key=lambda x: x["index"])


def get_firewall_policies() -> list:
    """Get all firewall policies from the v1 API.

    Returns list of normalized policy dicts with id, name, description,
    enabled, index, action, source/dest zone IDs, ip_version, logging, schedule.
    Requires api_key in config.
    """
    raw = _v1_api_get(_v1_site_path("firewall/policies"))
    policies = []
    for p in raw:
        policies.append(
            {
                "id": p.get("id", ""),
                "name": p.get("name", ""),
                "description": p.get("description", ""),
                "enabled": p.get("enabled", False),
                "index": p.get("index", 0),
                "action": (p.get("action") or {}).get("type", ""),
                "source_zone_id": (p.get("source") or {}).get("zoneId", ""),
                "source_filter": (p.get("source") or {}).get("trafficFilter"),
                "destination_zone_id": (p.get("destination") or {}).get("zoneId", ""),
                "destination_filter": (p.get("destination") or {}).get("trafficFilter"),
                "ip_version": (p.get("ipProtocolScope") or {}).get("ipVersion", ""),
                "connection_states": p.get("connectionStateFilter", []),
                "logging_enabled": p.get("loggingEnabled", False),
                "schedule_mode": (p.get("schedule") or {}).get("mode", ""),
                "origin": (p.get("metadata") or {}).get("origin", ""),
            }
        )
    return policies


def restart_device(device_id: str) -> dict:
    """Restart a UniFi device via the v1 API.

    Args:
        device_id: The device's v1 UUID (from get_v1_devices()).

    Returns:
        Response dict from the controller (typically empty on success).
    Requires api_key in config.
    """
    return _v1_api_post(_v1_site_path(f"devices/{device_id}/actions"), {"action": "RESTART"})


def get_wan_status():
    """Get WAN interface status (health endpoint).

    Returns list of WAN interface dicts.
    """
    raw = _api_get(_site_path("stat/health"))
    wans = []
    for item in raw:
        if item.get("subsystem") == "wan":
            wans.append(
                {
                    "status": item.get("status", "unknown"),
                    "num_gw": item.get("num_gw", 0),
                    "gateways": item.get("gateways", []),
                    "uptime_seconds": item.get("wan_uptime", 0),
                    "rx_bytes": item.get("rx_bytes-r", 0),
                    "tx_bytes": item.get("tx_bytes-r", 0),
                    "latency_avg": item.get("latency", 0),
                    "speedtest_ping": item.get("speedtest_ping", 0),
                    "speedtest_download": item.get("xput_down", 0),
                    "speedtest_upload": item.get("xput_up", 0),
                }
            )
    return wans


def get_dns_settings():
    """Get DNS/DoH configuration from system settings."""
    raw = _api_get(_site_path("rest/setting/network_optimization"))
    if isinstance(raw, list) and raw:
        raw = raw[0]

    return {
        "doh_state": raw.get("doh_state", "unknown"),
        "dns_servers": raw.get("dns_servers", []),
    }


def get_firewall_zones():
    """Get firewall zone assignments.

    Uses the v1 API when api_key is configured -- returns proper zone names
    without needing a zone_map. Falls back to the legacy API + zone_map
    config when api_key is absent.
    """
    config = load_config()

    if config.get("api_key"):
        # v1 API: zones have names natively; cross-ref networkIds to get VLAN info
        v1_zones = _v1_api_get(_v1_site_path("firewall/zones"))
        networks = get_networks()
        # Build a lookup from legacy firewall_zone_id to zone name using the
        # v1 zone id. The v1 zone id matches the firewall_zone_id in legacy networkconf.
        zone_name_map = {z["id"]: z["name"] for z in v1_zones}
        zones = []
        for n in networks:
            zone_id = n.get("firewall_zone_id", "")
            zones.append(
                {
                    "network": n["name"],
                    "vlan_id": n["vlan_id"],
                    "zone_id": zone_id,
                    "zone_name": zone_name_map.get(zone_id, "unknown"),
                }
            )
        return zones

    # Legacy fallback: zone names from zone_map config
    zone_names = config.get("zone_map", {})
    networks = get_networks()
    zones = []
    for n in networks:
        zone_id = n.get("firewall_zone_id", "")
        zones.append(
            {
                "network": n["name"],
                "vlan_id": n["vlan_id"],
                "zone_id": zone_id,
                "zone_name": zone_names.get(zone_id, "unknown"),
            }
        )
    return zones


# ── New v1 API functions ──


def get_pending_devices() -> list:
    """Get devices discovered on the network but not yet adopted.

    Top-level endpoint (not per-site). Returns empty list when all
    devices are adopted. Requires api_key in config.
    """
    return _v1_api_get("/integration/v1/pending-devices")


def get_dns_policies() -> list:
    """Get DNS routing and filtering policies from the v1 API.

    These are per-network or per-client DNS routing rules, distinct from
    the legacy dns_config resolver settings. Requires api_key in config.
    """
    return _v1_api_get(_v1_site_path("dns/policies"))


def get_v1_wans() -> list:
    """Get WAN interface data from the v1 API.

    Returns structured WAN configuration and live state with richer detail
    than the legacy stat/health endpoint. Requires api_key in config.
    """
    raw = _v1_api_get(_v1_site_path("wans"))
    if not isinstance(raw, list):
        raw = [raw] if raw else []
    return raw


def get_dpi_applications() -> list:
    """Get DPI application definitions (ID-to-name lookup table).

    Top-level endpoint, not per-site. Resolves numeric application IDs
    in firewall policies and IPS events to readable names.
    Requires api_key in config.
    """
    return _v1_api_get("/integration/v1/dpi/applications")


def get_dpi_categories() -> list:
    """Get DPI category definitions (ID-to-name lookup table).

    Top-level endpoint, not per-site. Resolves numeric category IDs
    in firewall policies and IPS events to readable names.
    Requires api_key in config.
    """
    return _v1_api_get("/integration/v1/dpi/categories")


def get_v1_wifi_broadcasts() -> list:
    """Get WiFi SSID broadcast data from the v1 API.

    May return richer per-radio detail than the legacy wlan_list endpoint.
    Requires api_key in config.
    """
    return _v1_api_get(_v1_site_path("wifi/broadcasts"))


def get_v1_networks() -> list:
    """Get network/VLAN configuration from the v1 API.

    May return additional fields (DHCP lease count, gateway IP, IPv6 config)
    beyond what the legacy vlan_list endpoint provides.
    Requires api_key in config.
    """
    return _v1_api_get(_v1_site_path("networks"))


def get_radius_profiles() -> list:
    """Get RADIUS authentication profiles from the v1 API.

    Returns RADIUS server configurations and their SSID/network associations.
    Returns empty list when no RADIUS profiles are configured.
    Requires api_key in config.
    """
    return _v1_api_get(_v1_site_path("radius/profiles"))


def get_v1_wifi_networks() -> list:
    """Get WLAN configurations from the v1 API.

    Returns the writable wifi-networks resource (not the read-only broadcasts
    projection). Each entry includes the v1 id needed for PUT updates, along
    with full field detail for all SSID-level settings.
    Requires api_key in config.
    """
    return _v1_api_get(_v1_site_path("wifi-networks"))


def _find_v1_wlan(name: str) -> dict:
    """Find a v1 wifi-network object by SSID name.

    Args:
        name: SSID name (case-insensitive exact match).

    Returns:
        The matching wifi-network dict (includes 'id' needed for PUT).

    Raises:
        RuntimeError if no matching SSID is found.
    """
    wlans = get_v1_wifi_networks()
    needle = name.lower().strip()
    for w in wlans:
        # v1 API may use 'name' or 'ssid' for the SSID string - try both
        if w.get("name", "").lower() == needle or w.get("ssid", "").lower() == needle:
            return w
    names = [w.get("name") or w.get("ssid", "") for w in wlans]
    raise RuntimeError(f"WLAN {name!r} not found. Known SSIDs: {names}")


def get_device_radios(device_id: str) -> list:
    """Get per-radio configuration for a device from the v1 API.

    Fetches the full device object and extracts interfaces.radios[].
    Field names (channel, txPower, settingPreference, etc.) are returned as-is
    from the v1 API -- use this to discover the exact names before writing.
    Requires api_key in config.

    Args:
        device_id: The device's v1 UUID (from get_v1_devices()).

    Returns:
        List of radio config dicts. Empty list if device has no radios or
        does not expose interfaces.radios in this firmware version.
    """
    raw = _v1_api_get(_v1_site_path(f"devices/{device_id}"))
    if not isinstance(raw, dict):
        return []
    interfaces = raw.get("interfaces", {})
    if not isinstance(interfaces, dict):
        return []
    return interfaces.get("radios", [])


def get_device_tags() -> list:
    """Get device tags used in firewall policies and ACL rules.

    Tags group devices for use in firewall policy source/destination filters.
    Requires api_key in config.
    """
    return _v1_api_get(_v1_site_path("device-tags"))


def get_system_info() -> dict:
    """Get controller system information.

    Combines legacy sysinfo (controller version, hostname, timezone) with
    device firmware summary from the v1 device list.
    """
    info = {}

    try:
        raw = _api_get(_site_path("stat/sysinfo"))
        if isinstance(raw, list) and raw:
            raw = raw[0]
        info["controller_version"] = raw.get("version", "unknown")
        info["hostname"] = raw.get("hostname", "unknown")
        info["timezone"] = raw.get("timezone", "unknown")
        info["uptime_seconds"] = raw.get("uptime", 0)
        info["autobackup"] = raw.get("autobackup", False)
    except Exception as e:
        info["sysinfo_error"] = str(e)

    config = load_config()
    if config.get("api_key"):
        try:
            devices = get_v1_devices()
            info["device_count"] = len(devices)
            info["upgradable_count"] = sum(1 for d in devices if d.get("upgradable"))
            info["upgradable_devices"] = [
                {"name": d["name"], "version": d["version"]} for d in devices if d.get("upgradable")
            ]
        except Exception as e:
            info["device_summary_error"] = str(e)

    return info


def get_device_ports(device_id: str) -> list:
    """Get all switch ports for a device from the v1 API.

    Tries the per-device ports sub-list endpoint first; falls back to the
    device detail endpoint if that path is not available on this firmware.
    Requires api_key in config.

    Args:
        device_id: The device's v1 UUID (from get_v1_devices()).

    Returns:
        List of port dicts. Field names depend on firmware version but typically
        include portIdx, name, enabled, and poeMode (for PoE-capable ports).
    """
    try:
        raw = _v1_api_get(_v1_site_path(f"devices/{device_id}/interfaces/ports"))
        if isinstance(raw, list):
            return raw
    except requests.HTTPError as e:
        if e.response.status_code != 404:
            raise

    # Fall back to device detail endpoint
    raw = _v1_api_get(_v1_site_path(f"devices/{device_id}"))
    if isinstance(raw, dict):
        interfaces = raw.get("interfaces", {})
        if isinstance(interfaces, dict):
            return interfaces.get("ports", [])
    return []


def port_action(device_id: str, port_idx: int, action: str) -> dict:
    """Perform an action on a specific switch port via the v1 API.

    Args:
        device_id: The device's v1 UUID (from get_v1_devices()).
        port_idx: The port index (portIdx field from get_device_ports()).
        action: One of 'enable', 'disable', 'poe_enable', 'poe_disable'.

    Returns:
        Response dict from the controller (typically empty on success).
    Requires api_key in config.
    """
    valid = ("enable", "disable", "poe_enable", "poe_disable")
    if action not in valid:
        raise ValueError(f"Invalid action {action!r}. Must be one of: {', '.join(valid)}")
    return _v1_api_post(
        _v1_site_path(f"devices/{device_id}/interfaces/ports/{port_idx}/actions"),
        {"action": action},
    )


def client_action(mac: str, action: str) -> dict:
    """Perform an action on a connected client by MAC address.

    Args:
        mac: Client MAC address (any format - normalized internally).
        action: One of 'block', 'unblock', 'reconnect'.

    Returns dict with status and client_id.
    Requires api_key in config.
    """
    if action not in ("block", "unblock", "reconnect"):
        raise ValueError(f"Invalid action {action!r}. Must be block, unblock, or reconnect.")

    norm = _normalize_mac(mac)
    clients = get_v1_clients()
    match = next((c for c in clients if _normalize_mac(c.get("mac", "")) == norm), None)
    if not match:
        raise RuntimeError(f"Client with MAC {mac!r} not found in active client list.")

    client_id = match.get("id")
    if not client_id:
        raise RuntimeError(f"Client {mac!r} has no v1 ID - cannot perform action.")

    site_id = get_site_id()
    result = _v1_api_post(
        f"/integration/v1/sites/{site_id}/clients/{client_id}/actions",
        {"action": action.upper()},
    )
    return {"status": action, "client_id": client_id, "mac": norm, "result": result}
