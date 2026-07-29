"""CLI entry point for launchd-tools.

Commands:
  status              List all personal LaunchAgents with health detail
  health              Print briefing-friendly fleet rollup
  logs                Tail log output for a specific agent
  kickstart           Restart one allowlisted agent
"""

from __future__ import annotations

import argparse
import re
import sys

_LABEL_CHARSET = re.compile(r"^[A-Za-z0-9._-]+$")


def cmd_status(args: argparse.Namespace) -> None:
    """List all agents with health detail."""
    from launchd_tools.launchctl import list_agents
    from launchd_tools.mcp_server import _enrich_agent

    agents = list_agents()
    if not agents:
        print("No personal LaunchAgents found.")
        return

    for entry in agents:
        detail = _enrich_agent(entry["label"], entry["pid"], entry["last_exit_code"])
        health_str = "OK" if detail["healthy"] else "FAIL"
        pid_str = str(detail["pid"]) if detail["pid"] is not None else "-"
        exit_str = str(detail["last_exit_code"]) if detail["last_exit_code"] is not None else "never"
        last_str = detail["last_run"] if detail["last_run"] else "unknown"
        print(
            f"[{health_str}] {detail['label']}  kind={detail['kind']} pid={pid_str} exit={exit_str} last_run={last_str}"
        )


def cmd_health(args: argparse.Namespace) -> None:
    """Print briefing-friendly fleet rollup."""
    from launchd_tools.health import build_health_summary
    from launchd_tools.launchctl import list_agents
    from launchd_tools.mcp_server import _enrich_agent

    raw = list_agents()
    agents = [_enrich_agent(e["label"], e["pid"], e["last_exit_code"]) for e in raw]
    summary = build_health_summary(agents)
    unhealthy = [a for a in agents if not a["healthy"]]
    print(summary)
    if unhealthy:
        print()
        for a in unhealthy:
            if a["kind"] == "daemon":
                detail = "stopped"
            else:
                code = a.get("last_exit_code")
                detail = f"exit {code}" if code is not None else "exit unknown"
            print(f"  FAIL  {a['label']}  kind={a['kind']}  ({detail})")


def cmd_logs(args: argparse.Namespace) -> None:
    """Tail log output for a specific agent."""
    from launchd_tools.config import get_label_prefixes
    from launchd_tools.launchctl import parse_print, print_agent, tail_file

    label = args.label

    # Security: same charset + allowlist guards as kickstart
    if not _LABEL_CHARSET.match(label):
        print(f"Error: '{label}' contains invalid characters.", file=sys.stderr)
        sys.exit(1)

    prefixes = get_label_prefixes()
    if not any(label.startswith(p) for p in prefixes):
        print(f"Error: '{label}' is not in the allowlist.", file=sys.stderr)
        sys.exit(1)

    try:
        text = print_agent(label)
        parsed = parse_print(text)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    log_path = parsed.get("stdout_path") or parsed.get("stderr_path")
    if not log_path:
        print(f"No log path found for {label}.")
        return

    lines = tail_file(log_path, args.lines)
    if not lines:
        print(f"No log data at {log_path}.")
        return
    for line in lines:
        print(line)


def cmd_kickstart(args: argparse.Namespace) -> None:
    """Restart one allowlisted agent."""
    import os
    import subprocess

    from launchd_tools.config import get_label_prefixes

    label = args.label
    if not _LABEL_CHARSET.match(label):
        print(f"Error: '{label}' contains invalid characters.", file=sys.stderr)
        sys.exit(1)

    prefixes = get_label_prefixes()
    if not any(label.startswith(p) for p in prefixes):
        print(f"Error: '{label}' is not in the allowlist. Kickstart refused.", file=sys.stderr)
        sys.exit(1)

    uid = os.getuid()
    result = subprocess.run(
        ["launchctl", "kickstart", "-k", f"gui/{uid}/{label}"],
        capture_output=True,
        text=True,
        check=False,
    )
    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0:
        print(f"Error: {output}", file=sys.stderr)
        sys.exit(1)
    print(output or f"Kickstarted {label}")


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="launchd-tools",
        description="Read-only health visibility for personal macOS LaunchAgents",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="List all personal LaunchAgents with health detail")
    sub.add_parser("health", help="Print briefing-friendly fleet rollup")

    logs_p = sub.add_parser("logs", help="Tail log output for a specific agent")
    logs_p.add_argument("--label", required=True, help="Full launchd label")
    logs_p.add_argument("--lines", type=int, default=50, help="Number of lines to tail (default: 50)")

    kick_p = sub.add_parser("kickstart", help="Restart one allowlisted agent")
    kick_p.add_argument("--label", required=True, help="Full launchd label to kickstart")

    args = parser.parse_args()
    dispatch = {
        "status": cmd_status,
        "health": cmd_health,
        "logs": cmd_logs,
        "kickstart": cmd_kickstart,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
