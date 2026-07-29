"""Apple Reminders helper - search-then-create dedup pattern.

Wraps apple_eventkit_tools.reminders.RemindersManager directly (not via an
MCP round-trip and not via osascript) so LaunchAgent-driven checks can create
a reminder without shelling out to `claude -p` (issue #146). Kept generic -
not utility-anomaly-specific - so other deterministic checks (task
escalation, etc.) can reuse the same dedup pattern.
"""

from apple_eventkit_tools.reminders import RemindersManager


def create_reminder_if_missing(
    config,
    title,
    search_query,
    notes=None,
    due_date=None,
    due_time=None,
    priority=0,
    list_name=None,
):
    """Create a reminder only if a search for `search_query` finds nothing.

    Args:
        config: HomeOps config dict (reads config["reminders"]["list"] /
            config["reminders"]["default_time"] as defaults).
        title: Reminder title.
        search_query: Substring to search existing reminders for before
            creating (dedup key - use a stable prefix, not a dynamic summary).
        notes: Notes/body text.
        due_date: Due date as YYYY-MM-DD string.
        due_time: Due time as HH:MM string. Defaults to
            config["reminders"]["default_time"] when not given.
        priority: 0=none, 1=high, 5=medium, 9=low.
        list_name: Target Reminders list. Defaults to
            config["reminders"]["list"] when not given.

    Returns:
        A dict: {"created": True, "reminder": {...}} if a new reminder was
        created, or {"created": False, "existing": {...}} if a match was
        already found (no reminder created).
    """
    reminders_cfg = config.get("reminders", {})
    resolved_list = list_name or reminders_cfg.get("list")
    resolved_time = due_time or reminders_cfg.get("default_time")

    manager = RemindersManager()

    existing = manager.search_reminders(search_query, list_name=resolved_list)
    if existing:
        return {"created": False, "existing": existing[0]}

    reminder = manager.create_reminder(
        title,
        list_name=resolved_list,
        due_date=due_date,
        due_time=resolved_time,
        notes=notes,
        priority=priority,
    )
    return {"created": True, "reminder": reminder}
