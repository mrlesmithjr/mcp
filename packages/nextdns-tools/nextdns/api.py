"""NextDNS API client for analytics, logs, and profile management."""

from datetime import datetime, timedelta, timezone

import requests

from nextdns.config import load_config

_session = None
_CACHE_TTL = 300  # 5 minutes
_analytics_cache = None
_analytics_cache_time = 0


def _get_session():
    """Get authenticated requests session."""
    global _session
    if _session is None:
        config = load_config()
        _session = requests.Session()
        _session.headers.update({"X-Api-Key": config["api_key"]})
    return _session


def _api_get(path, params=None):
    """Make a GET request to the NextDNS API."""
    s = _get_session()
    r = s.get(f"https://api.nextdns.io{path}", params=params)
    r.raise_for_status()
    return r.json().get("data", r.json())


def _profile_path(endpoint=""):
    """Build profile-scoped API path."""
    config = load_config()
    pid = config["profile_id"]
    return f"/profiles/{pid}{endpoint}"


def _date_range(hours=24):
    """Build from/to date range."""
    now = datetime.now(timezone.utc)
    from_dt = now - timedelta(hours=hours)
    return {
        "from": from_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "to": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ── Analytics ──


def get_status(hours=24):
    """Get query status breakdown (allowed, blocked, etc.).

    Returns dict: {queries, by_status: [{status, count}]}
    """
    params = _date_range(hours)
    raw = _api_get(_profile_path("/analytics/status"), params)
    if not isinstance(raw, list):
        raw = [raw]

    total = sum(item.get("queries", 0) for item in raw)
    return {
        "hours": hours,
        "total_queries": total,
        "by_status": [{"status": item.get("status", "unknown"), "count": item.get("queries", 0)} for item in raw],
    }


def get_blocked(hours=24):
    """Get top blocked domains.

    Returns list of dicts: [{domain, count, tracker}]
    """
    params = _date_range(hours)
    params["status"] = "blocked"
    raw = _api_get(_profile_path("/analytics/domains"), params)
    if not isinstance(raw, list):
        raw = []

    return [
        {
            "domain": item.get("domain", ""),
            "count": item.get("queries", 0),
            "tracker": item.get("tracker", ""),
        }
        for item in raw
    ]


def get_devices(hours=24):
    """Get device activity by query count.

    Returns list of dicts: [{name, count, percentage}]
    """
    params = _date_range(hours)
    raw = _api_get(_profile_path("/analytics/devices"), params)
    if not isinstance(raw, list):
        raw = []

    total = sum(item.get("queries", 0) for item in raw)
    return [
        {
            "name": item.get("name", item.get("id", "unknown")),
            "count": item.get("queries", 0),
            "percentage": round(item.get("queries", 0) / total * 100, 1) if total > 0 else 0,
        }
        for item in raw
    ]


def get_security(hours=24):
    """Get DNS security posture - encryption, DNSSEC, and protocol stats.

    Returns dict: {encryption, dnssec, protocols, security_config}
    """
    params = _date_range(hours)

    # Encryption stats
    enc_raw = _api_get(_profile_path("/analytics/encryption"), params)
    if not isinstance(enc_raw, list):
        enc_raw = []
    total_enc = sum(item.get("queries", 0) for item in enc_raw)
    encrypted = sum(item.get("queries", 0) for item in enc_raw if item.get("encrypted"))
    enc_pct = round(encrypted / total_enc * 100, 1) if total_enc else 0

    # DNSSEC stats
    dnssec_raw = _api_get(_profile_path("/analytics/dnssec"), params)
    if not isinstance(dnssec_raw, list):
        dnssec_raw = []
    total_dnssec = sum(item.get("queries", 0) for item in dnssec_raw)
    validated = sum(item.get("queries", 0) for item in dnssec_raw if item.get("validated"))
    dnssec_pct = round(validated / total_dnssec * 100, 1) if total_dnssec else 0

    # Protocol breakdown
    proto_raw = _api_get(_profile_path("/analytics/protocols"), params)
    if not isinstance(proto_raw, list):
        proto_raw = []
    protocols = [{"protocol": item.get("protocol", "unknown"), "queries": item.get("queries", 0)} for item in proto_raw]

    # Security config from profile
    profile = get_profile()
    sec_cfg = profile.get("security", {})

    return {
        "hours": hours,
        "encryption": {
            "total": total_enc,
            "encrypted": encrypted,
            "percent": enc_pct,
        },
        "dnssec": {
            "total": total_dnssec,
            "validated": validated,
            "percent": dnssec_pct,
        },
        "protocols": protocols,
        "security_config": {
            "threat_intelligence": sec_cfg.get("threatIntelligenceFeeds", False),
            "ai_threat_detection": sec_cfg.get("aiThreatDetection", False),
            "google_safe_browsing": sec_cfg.get("googleSafeBrowsing", False),
            "cryptojacking": sec_cfg.get("cryptojacking", False),
            "dns_rebinding": sec_cfg.get("dnsRebinding", False),
            "idn_homographs": sec_cfg.get("idnHomographs", False),
            "typosquatting": sec_cfg.get("typosquatting", False),
            "dga": sec_cfg.get("dga", False),
            "nrd": sec_cfg.get("nrd", False),
            "csam": sec_cfg.get("csam", False),
        },
    }


def get_logs(limit=100):
    """Get recent DNS query logs.

    Returns list of dicts: [{timestamp, domain, status, reasons, device, encrypted}]
    """
    raw = _api_get(_profile_path("/logs"))
    if not isinstance(raw, list):
        raw = []

    return [
        {
            "timestamp": item.get("timestamp", ""),
            "domain": item.get("domain", ""),
            "status": item.get("status", ""),
            "reasons": [r.get("id", str(r)) if isinstance(r, dict) else str(r) for r in item.get("reasons", [])],
            "device": (item.get("device") or {}).get("name", ""),
            "encrypted": item.get("encrypted", False),
        }
        for item in raw[:limit]
    ]


# ── Profile ──


def get_profile():
    """Get full profile configuration.

    Returns dict with security, privacy, parentalControl, denylist,
    allowlist, settings sections.
    """
    return _api_get(_profile_path())


def get_allowlist():
    """Get current allowlist entries.

    Returns list of domain strings.
    """
    raw = _api_get(_profile_path("/allowlist"))
    if not isinstance(raw, list):
        raw = []
    return [item.get("id", "") for item in raw]


def get_denylist():
    """Get current denylist entries.

    Returns list of domain strings.
    """
    raw = _api_get(_profile_path("/denylist"))
    if not isinstance(raw, list):
        raw = []
    return [item.get("id", "") for item in raw]


def add_allowlist(domain):
    """Add a domain to the allowlist.

    Returns True if successful.
    """
    s = _get_session()
    r = s.post(
        f"https://api.nextdns.io{_profile_path('/allowlist')}",
        json={"id": domain},
    )
    return r.status_code in (200, 204)


def remove_allowlist(domain):
    """Remove a domain from the allowlist.

    Returns True if successful.
    """
    s = _get_session()
    r = s.delete(f"https://api.nextdns.io{_profile_path('/allowlist')}/{domain}")
    return r.status_code in (200, 204)


def add_denylist(domain):
    """Add a domain to the denylist.

    Returns True if successful.
    """
    s = _get_session()
    r = s.post(
        f"https://api.nextdns.io{_profile_path('/denylist')}",
        json={"id": domain},
    )
    return r.status_code in (200, 204)


def remove_denylist(domain):
    """Remove a domain from the denylist.

    Returns True if successful.
    """
    s = _get_session()
    r = s.delete(f"https://api.nextdns.io{_profile_path('/denylist')}/{domain}")
    return r.status_code in (200, 204)


def export_profile():
    """Export full profile configuration for backup.

    Returns dict with all profile settings.
    """
    config = load_config()
    profile = get_profile()
    profile["_profile_id"] = config["profile_id"]
    profile["_exported_at"] = datetime.now(timezone.utc).isoformat()
    return profile
