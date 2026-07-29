"""Pure health classification for LaunchAgent entries.

No I/O -- only logic over parsed data structures. This makes
all classification paths unit-testable without live launchctl.
"""

from __future__ import annotations


def classify_kind(parsed: dict, on_demand: bool | None = None) -> str:
    """Classify a parsed agent as 'daemon' or 'scheduled'.

    Precedence (highest first):
      1. has_event_triggers or calendarinterval in properties => 'scheduled'
      2. 'keepalive' or 'runatload' in properties => 'daemon'
      3. on_demand == False (from list) => 'daemon'
      4. Default: 'scheduled'
    """
    has_triggers = parsed.get("has_event_triggers", False)
    properties = parsed.get("properties", "") or ""

    if has_triggers or "calendarinterval" in properties:
        return "scheduled"

    if "keepalive" in properties or "runatload" in properties:
        return "daemon"

    if on_demand is False:
        return "daemon"

    return "scheduled"


def is_healthy(parsed: dict, kind: str) -> bool:
    """Return True if the agent is in a healthy state for its kind.

    Daemon: healthy = state == 'running' and pid present (int).
    Scheduled: healthy = last_exit_code in (0, None).
      None means (never exited), which is healthy (never failed yet).
    """
    if kind == "daemon":
        state = parsed.get("state", "")
        pid = parsed.get("pid")
        return state == "running" and isinstance(pid, int)

    # scheduled
    last_exit = parsed.get("last_exit_code", None)
    return last_exit in (0, None)


def build_health_summary(agents: list[dict]) -> str:
    """Build a briefing-friendly one-line summary from classified agents.

    agents: list of {label, healthy, kind, last_exit_code, ...}

    Returns:
      "All N agents healthy."
    or:
      "K failed: label (stopped), label2 (exit 1), ..."

    Failure rendering is kind-aware:
      daemon failure => "(stopped)"  -- it's not running, exit code is irrelevant
      scheduled failure with code => "(exit N)"
      scheduled failure with None code => "(exit unknown)"
    """
    total = len(agents)
    failed = [a for a in agents if not a.get("healthy", True)]
    if not failed:
        return f"All {total} agents healthy."

    parts = []
    for a in failed:
        kind = a.get("kind", "scheduled")
        if kind == "daemon":
            parts.append(f"{a['label']} (stopped)")
        else:
            code = a.get("last_exit_code")
            code_str = str(code) if code is not None else "unknown"
            parts.append(f"{a['label']} (exit {code_str})")
    return f"{len(failed)} failed: {', '.join(parts)}"
