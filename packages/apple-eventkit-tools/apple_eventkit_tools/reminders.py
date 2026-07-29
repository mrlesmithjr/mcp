"""EventKit bridge - read/write Apple Reminders via PyObjC.

Talks directly to the CalendarAgent daemon; Reminders.app does NOT need to be open.
macOS will prompt the terminal for Reminders access on first use (TCC permission).
"""

import logging
import threading
from datetime import datetime

import EventKit

from apple_eventkit_tools.base import EventKitBase
from apple_eventkit_tools.utils import _datetime_to_date_components, _datetime_to_nsdate, _nscolor_hex

logger = logging.getLogger(__name__)

# EventKit constants
EK_ENTITY_REMINDER = 1  # EKEntityTypeReminder


class RemindersError(Exception):
    """Raised when EventKit reminders operations fail."""


class RemindersManager(EventKitBase):
    """Manages Apple Reminders access through EventKit."""

    def _access_config(self):
        return (EK_ENTITY_REMINDER, "requestFullAccessToRemindersWithCompletion_", "Reminders")

    def list_lists(self):
        """Return all reminder lists."""
        cals = self.store.calendarsForEntityType_(EK_ENTITY_REMINDER)
        results = []
        for cal in cals:
            results.append(
                {
                    "id": cal.calendarIdentifier(),
                    "title": cal.title(),
                    "color": _nscolor_hex(cal.color()),
                    "allows_modification": cal.allowsContentModifications(),
                }
            )
        return sorted(results, key=lambda c: c["title"])

    def list_reminders(self, list_name=None, include_completed=False):
        """List reminders, optionally filtered to one list.

        This is synchronous - it fetches reminders using a predicate and
        waits for the async callback to complete.
        """
        calendars = None
        if list_name:
            cal = self._find_list(list_name)
            if cal is None:
                raise RemindersError(f"List not found: {list_name}")
            calendars = [cal]

        if include_completed:
            predicate = self.store.predicateForRemindersInCalendars_(calendars)
        else:
            predicate = self.store.predicateForIncompleteRemindersWithDueDateStarting_ending_calendars_(
                None, None, calendars
            )

        results = []
        done = threading.Event()

        def callback(reminders):
            if reminders:
                for r in reminders:
                    results.append(_serialize_reminder(r))
            done.set()

        self.store.fetchRemindersMatchingPredicate_completion_(predicate, callback)
        if not done.wait(timeout=30):
            raise RemindersError("EventKit fetch timed out")

        results.sort(key=lambda r: r["due_date"] or "9999-12-31")
        return results

    def create_reminder(
        self,
        title,
        list_name=None,
        due_date=None,
        due_time=None,
        notes=None,
        priority=0,
        recurrence=None,
        recurrence_interval=1,
        recurrence_end_date=None,
    ):
        """Create a new reminder.

        Args:
            title: Reminder title.
            list_name: Target list name (default: system default).
            due_date: Due date as YYYY-MM-DD string.
            due_time: Due time as HH:MM string (requires due_date).
            notes: Notes/body text.
            priority: 0=none, 1=high, 5=medium, 9=low.
            recurrence: Repeat frequency - "daily", "weekly", "monthly", "yearly"
            recurrence_interval: Repeat every N periods (default: 1)
            recurrence_end_date: Stop repeating after this date (YYYY-MM-DD)
        """
        reminder = EventKit.EKReminder.reminderWithEventStore_(self.store)
        reminder.setTitle_(title)

        if list_name:
            cal = self._find_list(list_name)
            if cal and cal.allowsContentModifications():
                reminder.setCalendar_(cal)
        if reminder.calendar() is None:
            reminder.setCalendar_(self.store.defaultCalendarForNewReminders())

        if due_date:
            dt = _parse_due_date(due_date, due_time)
            date_components = _datetime_to_date_components(dt)
            reminder.setDueDateComponents_(date_components)

            if due_time:
                alarm = EventKit.EKAlarm.alarmWithAbsoluteDate_(_datetime_to_nsdate(dt))
                reminder.addAlarm_(alarm)

        if notes:
            reminder.setNotes_(notes)
        if priority:
            reminder.setPriority_(priority)

        if recurrence and due_date:
            rule = _create_recurrence_rule(recurrence, recurrence_interval, recurrence_end_date)
            if rule:
                reminder.addRecurrenceRule_(rule)

        success, error = self.store.saveReminder_commit_error_(reminder, True, None)
        if not success:
            raise RemindersError(f"Failed to create reminder: {error}")

        return _serialize_reminder(reminder)

    def complete_reminder(self, reminder_id):
        """Mark a reminder as completed."""
        reminder = self._find_reminder(reminder_id)
        if reminder is None:
            raise RemindersError(f"Reminder not found: {reminder_id}")

        reminder.setCompleted_(True)
        reminder.setCompletionDate_(_datetime_to_nsdate(datetime.now()))

        success, error = self.store.saveReminder_commit_error_(reminder, True, None)
        if not success:
            raise RemindersError(f"Failed to complete reminder: {error}")

        return {"id": reminder_id, "completed": True}

    def uncomplete_reminder(self, reminder_id):
        """Mark a completed reminder as incomplete."""
        reminder = self._find_reminder(reminder_id)
        if reminder is None:
            raise RemindersError(f"Reminder not found: {reminder_id}")

        reminder.setCompleted_(False)
        reminder.setCompletionDate_(None)

        success, error = self.store.saveReminder_commit_error_(reminder, True, None)
        if not success:
            raise RemindersError(f"Failed to uncomplete reminder: {error}")

        return {"id": reminder_id, "completed": False}

    def _find_reminder_fresh(self, reminder_id):
        """Find a reminder via predicate fetch to get a live mutable reference.

        calendarItemWithIdentifier_ can return a stale cached object where
        modifications save 'successfully' but don't actually persist. Fetching
        via predicate returns a fresh mutable reference from the store.
        """
        predicate = self.store.predicateForRemindersInCalendars_(None)
        done = threading.Event()
        found = [None]

        def callback(reminders):
            if reminders:
                for r in reminders:
                    if r.calendarItemIdentifier() == reminder_id:
                        found[0] = r
                        break
            done.set()

        self.store.fetchRemindersMatchingPredicate_completion_(predicate, callback)
        if not done.wait(timeout=30):
            raise RemindersError("EventKit fetch timed out")
        return found[0]

    def update_reminder(self, reminder_id, title=None, due_date=None, due_time=None, notes=None, priority=None):
        """Update an existing reminder."""
        reminder = self._find_reminder_fresh(reminder_id)
        if reminder is None:
            raise RemindersError(f"Reminder not found: {reminder_id}")

        if title is not None:
            reminder.setTitle_(title)
        if notes is not None:
            reminder.setNotes_(notes)
        if priority is not None:
            reminder.setPriority_(priority)
        if due_date is not None:
            dt = _parse_due_date(due_date, due_time)
            date_components = _datetime_to_date_components(dt)
            reminder.setDueDateComponents_(date_components)
            # Remove stale absolute-date alarms - Reminders.app displays the
            # alarm date, not dueDateComponents, so they must stay in sync.
            for alarm in list(reminder.alarms() or []):
                reminder.removeAlarm_(alarm)
            if due_time:
                reminder.addAlarm_(EventKit.EKAlarm.alarmWithAbsoluteDate_(_datetime_to_nsdate(dt)))

        success, error = self.store.saveReminder_commit_error_(reminder, True, None)
        if not success:
            raise RemindersError(f"Failed to update reminder: {error}")

        return _serialize_reminder(reminder)

    def delete_reminder(self, reminder_id):
        """Delete a reminder."""
        reminder = self._find_reminder(reminder_id)
        if reminder is None:
            raise RemindersError(f"Reminder not found: {reminder_id}")

        success, error = self.store.removeReminder_commit_error_(reminder, True, None)
        if not success:
            raise RemindersError(f"Failed to delete reminder: {error}")

        return {"id": reminder_id, "deleted": True}

    def overdue_reminders(self, list_name=None, limit=50):
        """Find all reminders that are past due (due_date < today, not completed).

        Args:
            list_name: Optional list name to filter.
            limit: Maximum number of results (default: 50).
        """
        calendars = None
        if list_name:
            cal = self._find_list(list_name)
            if cal is None:
                raise RemindersError(f"List not found: {list_name}")
            calendars = [cal]

        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        today_nsdate = _datetime_to_nsdate(today_start)

        predicate = self.store.predicateForIncompleteRemindersWithDueDateStarting_ending_calendars_(
            None, today_nsdate, calendars
        )

        results = []
        done = threading.Event()

        def callback(reminders):
            if reminders:
                for r in reminders:
                    data = _serialize_reminder(r)
                    if data["due_date"]:
                        due_str = data["due_date"][:10]
                        try:
                            due_dt = datetime.strptime(due_str, "%Y-%m-%d").date()
                            days_overdue = (datetime.now().date() - due_dt).days
                            if days_overdue > 0:
                                data["days_overdue"] = days_overdue
                                results.append(data)
                        except ValueError:
                            pass
            done.set()

        self.store.fetchRemindersMatchingPredicate_completion_(predicate, callback)
        if not done.wait(timeout=30):
            raise RemindersError("EventKit fetch timed out")

        results.sort(key=lambda r: r.get("days_overdue", 0), reverse=True)
        return results[:limit]

    def search_reminders(self, query, list_name=None, include_completed=False, limit=20):
        """Search reminders by title or notes text (case-insensitive).

        Args:
            query: Search text.
            list_name: Optional list name to filter.
            include_completed: Include completed reminders (default: false).
            limit: Maximum number of results (default: 20).
        """
        calendars = None
        if list_name:
            cal = self._find_list(list_name)
            if cal is None:
                raise RemindersError(f"List not found: {list_name}")
            calendars = [cal]

        if include_completed:
            predicate = self.store.predicateForRemindersInCalendars_(calendars)
        else:
            predicate = self.store.predicateForIncompleteRemindersWithDueDateStarting_ending_calendars_(
                None, None, calendars
            )

        results = []
        done = threading.Event()
        query_lower = query.lower()

        def callback(reminders):
            if reminders:
                for r in reminders:
                    title = (r.title() or "").lower()
                    notes = (r.notes() or "").lower()
                    if query_lower in title or query_lower in notes:
                        results.append(_serialize_reminder(r))
            done.set()

        self.store.fetchRemindersMatchingPredicate_completion_(predicate, callback)
        if not done.wait(timeout=30):
            raise RemindersError("EventKit fetch timed out")

        results.sort(key=lambda r: r["due_date"] or "9999-12-31")
        return results[:limit]

    def _find_list(self, name):
        """Find a reminder list by name or ID (case-insensitive)."""
        name_lower = name.lower()
        cals = self.store.calendarsForEntityType_(EK_ENTITY_REMINDER)
        for cal in cals:
            if cal.title().lower() == name_lower:
                return cal
            if cal.calendarIdentifier() == name:
                return cal
        return None

    def _find_reminder(self, reminder_id):
        """Find a reminder by its calendar item identifier."""
        return self.store.calendarItemWithIdentifier_(reminder_id)


# ── Helpers ──


def _serialize_reminder(r):
    """Convert an EKReminder to a serializable dict."""
    due_date = None
    due_components = r.dueDateComponents()
    if due_components:
        year = due_components.year()
        month = due_components.month()
        day = due_components.day()
        hour = due_components.hour()
        minute = due_components.minute()

        if year and year < 9999 and month and day:
            if hour is not None and hour < 99 and minute is not None and minute < 99:
                due_date = f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}"
            else:
                due_date = f"{year:04d}-{month:02d}-{day:02d}"

    completion_date = None
    if r.completionDate():
        try:
            ts = r.completionDate().timeIntervalSince1970()
            completion_date = datetime.fromtimestamp(ts).astimezone().isoformat()
        except Exception:
            pass

    priority_names = {0: "none", 1: "high", 5: "medium", 9: "low"}

    result = {
        "id": r.calendarItemIdentifier(),
        "title": r.title() or "(no title)",
        "completed": bool(r.isCompleted()),
        "due_date": due_date,
        "completion_date": completion_date,
        "notes": r.notes() or None,
        "priority": priority_names.get(r.priority(), f"unknown({r.priority()})"),
        "list": r.calendar().title() if r.calendar() else None,
        "list_id": r.calendar().calendarIdentifier() if r.calendar() else None,
    }

    rules = r.recurrenceRules()
    if rules and len(rules) > 0:
        rule = rules[0]
        freq_names = {0: "daily", 1: "weekly", 2: "monthly", 3: "yearly"}
        result["recurrence"] = freq_names.get(rule.frequency(), "unknown")
        result["recurrence_interval"] = rule.interval()
        if rule.recurrenceEnd():
            end_date = rule.recurrenceEnd().endDate()
            if end_date:
                try:
                    ts = end_date.timeIntervalSince1970()
                    result["recurrence_end"] = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                except Exception:
                    pass

    return result


def _parse_due_date(date_str, time_str=None):
    """Parse YYYY-MM-DD and optional HH:MM into a datetime."""
    if time_str:
        return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    return datetime.strptime(date_str, "%Y-%m-%d")


def _create_recurrence_rule(frequency, interval=1, end_date_str=None):
    """Create an EKRecurrenceRule.

    Args:
        frequency: "daily", "weekly", "monthly", "yearly"
        interval: Every N periods
        end_date_str: Optional end date as YYYY-MM-DD
    """
    freq_map = {
        "daily": 0,  # EKRecurrenceFrequencyDaily
        "weekly": 1,  # EKRecurrenceFrequencyWeekly
        "monthly": 2,  # EKRecurrenceFrequencyMonthly
        "yearly": 3,  # EKRecurrenceFrequencyYearly
    }

    freq = freq_map.get(frequency.lower())
    if freq is None:
        return None

    end = None
    if end_date_str:
        end_dt = datetime.strptime(end_date_str, "%Y-%m-%d")
        end_nsdate = _datetime_to_nsdate(end_dt)
        end = EventKit.EKRecurrenceEnd.recurrenceEndWithEndDate_(end_nsdate)

    return EventKit.EKRecurrenceRule.alloc().initRecurrenceWithFrequency_interval_end_(freq, interval, end)
