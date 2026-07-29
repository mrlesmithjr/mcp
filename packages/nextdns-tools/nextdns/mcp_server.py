"""MCP server exposing NextDNS data as callable tools for Claude Code.

Returns structured JSON optimized for LLM consumption.
"""

import json
import logging
import os
import sys

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
logger = logging.getLogger(__name__)

mcp = FastMCP("nextdns")

# MCP tool annotations follow the workspace convention in the root CLAUDE.md.
# All nextdns-tools call the NextDNS cloud API over the internet, so every
# tool is open-world. Read-only tools share this constant; write tools are
# annotated inline.
_READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)

# ── Caching ──

_CACHE_TTL = 300  # 5 minutes


# ── Analytics ──


@mcp.tool(annotations=_READ_ONLY)
def dns_status(hours: int = 24) -> str:
    """DNS query status - total queries, allowed vs blocked breakdown.

    Args:
        hours: Number of hours to analyze (default: 24)

    Returns JSON: {hours, total_queries, by_status: [{status, count}]}
    """
    try:
        from nextdns.api import get_status

        data = get_status(hours)
        return json.dumps(data)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_ONLY)
def dns_blocked(hours: int = 24) -> str:
    """Top blocked domains with query counts.

    Args:
        hours: Number of hours to analyze (default: 24)

    Returns JSON: {blocked: [{domain, count}]}
    """
    try:
        from nextdns.api import get_blocked

        blocked = get_blocked(hours)
        return json.dumps({"blocked": blocked, "count": len(blocked)})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_ONLY)
def dns_devices(hours: int = 24) -> str:
    """Device activity ranked by DNS query count.

    Args:
        hours: Number of hours to analyze (default: 24)

    Returns JSON: {devices: [{name, count, percentage}]}
    """
    try:
        from nextdns.api import get_devices

        devices = get_devices(hours)
        return json.dumps({"devices": devices, "count": len(devices)})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_ONLY)
def dns_security(hours: int = 24) -> str:
    """DNS security posture - encryption %, DNSSEC validation, protocols,
    and active security features (threat intel, safe browsing, etc.).

    Args:
        hours: Number of hours to analyze (default: 24)

    Returns JSON: {encryption: {total, encrypted, percent},
    dnssec: {total, validated, percent}, protocols: [{protocol, queries}],
    security_config: {threat_intelligence, ai_threat_detection, ...}}
    """
    try:
        from nextdns.api import get_security

        data = get_security(hours)
        return json.dumps(data)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_ONLY)
def dns_logs(limit: int = 50) -> str:
    """Recent DNS query logs with domain, status, and blocking reasons.

    Args:
        limit: Number of log entries (default: 50)

    Returns JSON: {logs: [{timestamp, domain, status, reasons, device, encrypted}]}
    """
    try:
        from nextdns.api import get_logs

        logs = get_logs(limit)
        return json.dumps({"logs": logs, "count": len(logs)})
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Profile ──


@mcp.tool(annotations=_READ_ONLY)
def dns_profile() -> str:
    """Full NextDNS profile configuration - security, privacy, blocklists,
    allowlist, denylist, and settings.

    Returns JSON: {security, privacy, parentalControl, denylist, allowlist, settings}
    """
    try:
        from nextdns.api import get_profile

        profile = get_profile()
        return json.dumps(profile)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_ONLY)
def dns_allowlist() -> str:
    """Current allowlist entries.

    Returns JSON: {allowlist: [domain, ...], count: N}
    """
    try:
        from nextdns.api import get_allowlist

        entries = get_allowlist()
        return json.dumps({"allowlist": entries, "count": len(entries)})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )
)
def dns_allowlist_add(domain: str) -> str:
    """Add a domain to the NextDNS allowlist.

    Args:
        domain: Domain to allow (e.g. "example.com")

    Returns JSON: {domain, added: true/false}
    """
    try:
        from nextdns.api import add_allowlist

        success = add_allowlist(domain)
        return json.dumps({"domain": domain, "added": success})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )
)
def dns_allowlist_remove(domain: str) -> str:
    """Remove a domain from the NextDNS allowlist.

    Args:
        domain: Domain to remove

    Returns JSON: {domain, removed: true/false}
    """
    try:
        from nextdns.api import remove_allowlist

        success = remove_allowlist(domain)
        return json.dumps({"domain": domain, "removed": success})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )
)
def dns_denylist_add(domain: str) -> str:
    """Add a domain to the NextDNS denylist.

    Args:
        domain: Domain to block (e.g. "malware.example.com")

    Returns JSON: {domain, added: true/false}
    """
    try:
        from nextdns.api import add_denylist

        success = add_denylist(domain)
        return json.dumps({"domain": domain, "added": success})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_ONLY)
def dns_export() -> str:
    """Export full NextDNS profile configuration for backup.

    Returns JSON: full profile config with metadata
    """
    try:
        from nextdns.api import export_profile

        data = export_profile()
        return json.dumps(data)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Entry Point ──


def main():
    """Run the MCP server."""
    logger.info("Starting NextDNS MCP server...")
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
