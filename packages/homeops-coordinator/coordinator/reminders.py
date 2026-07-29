"""Thin singleton wrapper around RemindersManager for coordinator use."""

import logging

logger = logging.getLogger(__name__)

_manager = None


def _get():
    global _manager
    if _manager is None:
        from apple_eventkit_tools.reminders import RemindersManager

        _manager = RemindersManager()
        logger.info("RemindersManager initialized")
    return _manager


def search(query):
    """Search incomplete reminders by text. Returns list of reminder dicts."""
    try:
        return _get().search_reminders(query=query, include_completed=False)
    except Exception:
        logger.exception("search_reminders failed for query=%r", query)
        return []


def complete(reminder_id):
    """Mark a reminder as complete. Returns result dict or None on error."""
    try:
        return _get().complete_reminder(reminder_id)
    except Exception:
        logger.exception("complete_reminder failed for id=%r", reminder_id)
        return None


def create(title, notes=None, priority=5, due_date=None, due_time=None):
    """Create a reminder in the Personal list. Returns reminder dict or None on error."""
    try:
        result = _get().create_reminder(
            title=title,
            list_name="Personal",
            notes=notes,
            priority=priority,
            due_date=due_date,
            due_time=due_time,
        )
        return result
    except Exception:
        logger.exception("create_reminder failed for title=%r", title)
        return None


def update(reminder_id, title=None, notes=None, priority=None, due_date=None):
    """Update an existing reminder's fields. Returns result dict or None on error."""
    try:
        return _get().update_reminder(
            reminder_id,
            title=title,
            notes=notes,
            priority=priority,
            due_date=due_date,
        )
    except Exception:
        logger.exception("update_reminder failed for id=%r", reminder_id)
        return None
