"""Apple Reminders helper - search-then-create dedup pattern.

Wraps apple_eventkit_tools.reminders.RemindersManager directly (not via an
MCP round-trip and not via osascript) so LaunchAgent-driven checks can create
a reminder without shelling out to `claude -p` (issue #146). Deliberately a
separate small per-package copy of the same helper in homeops/reminders.py
rather than a shared import - promoting to a shared location is deferred
until a 3rd/4th real consumer shows an actual duplication problem worth
solving (issue #146 design notes).
"""

from apple_eventkit_tools.reminders import RemindersManager

# Unlike homeops, lawnops has no existing "reminders" config section
# (default_time/list), so these literal fallbacks match what the old
# irrigation-check.sh hardcoded rather than being config-driven.
_DEFAULT_LIST = "Personal"
_DEFAULT_TIME = "08:00"


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
        config: LawnOps config dict (reads config["reminders"]["list"] /
            config["reminders"]["default_time"] as defaults, if present).
        title: Reminder title.
        search_query: Substring to search existing reminders for before
            creating (dedup key - use a stable prefix, not a dynamic summary).
        notes: Notes/body text.
        due_date: Due date as YYYY-MM-DD string.
        due_time: Due time as HH:MM string. Defaults to
            config["reminders"]["default_time"] when not given, else "08:00".
        priority: 0=none, 1=high, 5=medium, 9=low.
        list_name: Target Reminders list. Defaults to
            config["reminders"]["list"] when not given, else "Personal".

    Returns:
        A dict: {"created": True, "reminder": {...}} if a new reminder was
        created, or {"created": False, "existing": {...}} if a match was
        already found (no reminder created).
    """
    reminders_cfg = config.get("reminders", {})
    resolved_list = list_name or reminders_cfg.get("list") or _DEFAULT_LIST
    resolved_time = due_time or reminders_cfg.get("default_time") or _DEFAULT_TIME

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
