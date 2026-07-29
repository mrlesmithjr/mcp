"""MCP server for launchd-tools.

Four tools:
  agent_status    - list all personal LaunchAgents with health summary
  agent_health    - briefing-friendly one-line fleet rollup
  agent_logs      - tail log output for a specific agent
  agent_kickstart - safely restart one allowlisted agent
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from launchd_tools.config import get_label_prefixes
from launchd_tools.health import build_health_summary, classify_kind, is_healthy
from launchd_tools.launchctl import infer_last_run, list_agents, parse_print, print_agent, tail_file

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
logger = logging.getLogger(__name__)

_LABEL_CHARSET = re.compile(r"^[A-Za-z0-9._-]+$")

mcp = FastMCP(
    "launchd-tools",
    instructions=(
        "Use agent_health for a one-line fleet rollup (briefing). "
        "agent_status lists all personal LaunchAgents with per-agent health detail. "
        "agent_logs tails a label's log output. "
        "agent_kickstart safely restarts one allowlisted agent. "
        "All tools are scoped to the LABEL_PREFIXES allowlist."
    ),
)


def _validate_label(label: str) -> str | None:
    """Return an error string if the label fails charset or allowlist checks, else None."""
    if not _LABEL_CHARSET.match(label):
        return f"Label '{label}' contains invalid characters. Only [A-Za-z0-9._-] are allowed."
    prefixes = get_label_prefixes()
    if not any(label.startswith(p) for p in prefixes):
        return f"Label '{label}' is not in the allowlist."
    return None


def _enrich_agent(label: str, pid: int | None, list_exit_code: int) -> dict:
    """Enrich one list entry with print data, kind, health, and last_run."""
    try:
        text = print_agent(label)
        parsed = parse_print(text)
    except Exception as exc:
        logger.warning("Could not print %s: %s", label, exc)
        parsed = {}

    kind = classify_kind(parsed)
    # Prefer parsed last_exit_code (handles never-exited sentinel -> None)
    parsed_exit = parsed.get("last_exit_code", list_exit_code)
    merged = {"pid": pid, "state": parsed.get("state", ""), **parsed}
    healthy = is_healthy(merged, kind)
    last_run = infer_last_run(parsed.get("stdout_path"), parsed.get("stderr_path"))

    return {
        "label": label,
        "kind": kind,
        "healthy": healthy,
        "state": parsed.get("state", "not running"),
        "pid": pid,
        "runs": parsed.get("runs"),
        "last_exit_code": parsed_exit,
        "last_run": last_run,
        "program": parsed.get("program"),
    }


# MCP tool annotations follow the workspace convention in the root CLAUDE.md.
# openWorldHint is set explicitly because the spec default is true (open world);
# every launchd-tools tool reads only local launchctl/log state, so all are closed.
_READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)


@mcp.tool(annotations=_READ_ONLY)
def agent_status() -> str:
    """List all personal LaunchAgents with per-agent health detail.

    Returns JSON: {count, agents: [{label, kind, healthy, state, pid,
    runs, last_exit_code, last_run, program}]}
    """
    try:
        raw = list_agents()
        agents = [_enrich_agent(e["label"], e["pid"], e["last_exit_code"]) for e in raw]
        return json.dumps({"count": len(agents), "agents": agents})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_ONLY)
def agent_health() -> str:
    """Briefing-friendly fleet health rollup for all personal LaunchAgents.

    Returns JSON: {summary, total, healthy_count, failed_count,
    unhealthy: [{label, kind, last_exit_code}]}
    """
    try:
        raw = list_agents()
        agents = [_enrich_agent(e["label"], e["pid"], e["last_exit_code"]) for e in raw]
        summary = build_health_summary(agents)
        unhealthy = [a for a in agents if not a["healthy"]]
        healthy_count = len(agents) - len(unhealthy)

        return json.dumps(
            {
                "summary": summary,
                "total": len(agents),
                "healthy_count": healthy_count,
                "failed_count": len(unhealthy),
                "unhealthy": [
                    {"label": a["label"], "kind": a["kind"], "last_exit_code": a["last_exit_code"]} for a in unhealthy
                ],
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_ONLY)
def agent_logs(label: str, lines: int = 50) -> str:
    """Tail log output for a specific LaunchAgent.

    Args:
        label: Full launchd label (must match an allowlisted prefix)
        lines: Number of tail lines to return (default: 50)

    Returns JSON: {label, log_lines: [...], stdout_path, stderr_path}
    """
    try:
        # Security: same charset + allowlist guards as agent_kickstart
        err = _validate_label(label)
        if err:
            return json.dumps({"error": err})

        text = print_agent(label)
        parsed = parse_print(text)

        stdout_path = parsed.get("stdout_path")
        stderr_path = parsed.get("stderr_path")

        # Prefer stdout; fall back to stderr when they are the same or stdout absent
        log_path = stdout_path or stderr_path
        log_lines: list[str] = []
        if log_path:
            log_lines = tail_file(log_path, lines)

        return json.dumps(
            {
                "label": label,
                "log_lines": log_lines,
                "stdout_path": stdout_path,
                "stderr_path": stderr_path,
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
def agent_kickstart(label: str) -> str:
    """Kickstart (restart) one allowlisted LaunchAgent.

    Validates the label against the allowlist AND a strict charset before
    calling launchctl. Never uses shell=True.

    Args:
        label: Full launchd label to kickstart

    Returns JSON: {label, status, output}
    """
    try:
        err = _validate_label(label)
        if err:
            return json.dumps({"error": err})

        uid = os.getuid()
        result = subprocess.run(
            ["launchctl", "kickstart", "-k", f"gui/{uid}/{label}"],
            capture_output=True,
            text=True,
            check=False,
        )
        output = (result.stdout + result.stderr).strip()
        if result.returncode != 0:
            return json.dumps({"label": label, "status": "error", "output": output})
        return json.dumps({"label": label, "status": "ok", "output": output})
    except Exception as e:
        return json.dumps({"error": str(e)})


def main() -> None:
    """Run the MCP server."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
