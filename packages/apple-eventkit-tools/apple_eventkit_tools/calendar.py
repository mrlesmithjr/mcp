"""EventKit bridge - read/write Apple Calendar via PyObjC.

Talks directly to the CalendarAgent daemon; Calendar.app does NOT need to be open.
macOS will prompt the terminal for Calendar access on first use (TCC permission).
"""

import logging

import EventKit
from Foundation import NSURL

from apple_eventkit_tools.base import EventKitBase
from apple_eventkit_tools.utils import _datetime_to_nsdate, _nscolor_hex, _nsdate_to_datetime

logger = logging.getLogger(__name__)

# EventKit constants
EK_ENTITY_EVENT = 0  # EKEntityTypeEvent
EK_SPAN_THIS = 0  # EKSpanThisEvent
EK_SPAN_FUTURE = 1  # EKSpanFutureEvents
EK_AUTH_AUTHORIZED = 3  # EKAuthorizationStatusAuthorized
EK_AUTH_FULL_ACCESS = 4  # EKAuthorizationStatusFullAccess (macOS 14+)


class CalendarError(Exception):
    """Raised when EventKit calendar operations fail."""


class CalendarManager(EventKitBase):
    """Manages Apple Calendar access through EventKit."""

    def _access_config(self):
        return (EK_ENTITY_EVENT, "requestFullAccessToEventsWithCompletion_", "Calendars")

    def list_calendars(self):
        """Return all calendars the user has access to."""
        cals = self.store.calendarsForEntityType_(EK_ENTITY_EVENT)
        results = []
        for cal in cals:
            results.append(
                {
                    "id": cal.calendarIdentifier(),
                    "title": cal.title(),
                    "type": _calendar_type_name(cal.type()),
                    "color": _nscolor_hex(cal.color()),
                    "is_subscribed": cal.isSubscribed(),
                    "allows_modification": cal.allowsContentModifications(),
                }
            )
        return sorted(results, key=lambda c: c["title"])

    def list_events(self, start, end, calendar_id=None):
        """List events in a date range, optionally filtered to one calendar."""
        start_ns = _datetime_to_nsdate(start)
        end_ns = _datetime_to_nsdate(end)

        calendars = None
        if calendar_id:
            cal = self.store.calendarWithIdentifier_(calendar_id)
            if cal is None:
                cal = self._find_calendar_by_title(calendar_id)
            if cal is None:
                raise CalendarError(f"Calendar not found: {calendar_id}")
            calendars = [cal]

        predicate = self.store.predicateForEventsWithStartDate_endDate_calendars_(start_ns, end_ns, calendars)
        ek_events = self.store.eventsMatchingPredicate_(predicate)

        events = []
        for ev in ek_events or []:
            events.append(_serialize_event(ev))

        return sorted(events, key=lambda e: e["start"])

    def create_event(
        self,
        title,
        start,
        end,
        calendar_id=None,
        location=None,
        notes=None,
        all_day=False,
        url=None,
        alarm_minutes=None,
    ):
        """Create a new calendar event."""
        ev = EventKit.EKEvent.eventWithEventStore_(self.store)
        ev.setTitle_(title)
        ev.setStartDate_(_datetime_to_nsdate(start))
        ev.setEndDate_(_datetime_to_nsdate(end))
        ev.setAllDay_(all_day)

        if location:
            ev.setLocation_(location)
        if notes:
            ev.setNotes_(notes)
        if url:
            ev.setURL_(NSURL.URLWithString_(url))

        if calendar_id:
            cal = self.store.calendarWithIdentifier_(calendar_id)
            if cal is None:
                cal = self._find_calendar_by_title(calendar_id)
            if cal and cal.allowsContentModifications():
                ev.setCalendar_(cal)
        if ev.calendar() is None:
            ev.setCalendar_(self.store.defaultCalendarForNewEvents())

        if alarm_minutes is not None:
            alarm = EventKit.EKAlarm.alarmWithRelativeOffset_(-alarm_minutes * 60)
            ev.addAlarm_(alarm)

        success, error = self.store.saveEvent_span_error_(ev, EK_SPAN_THIS, None)
        if not success:
            raise CalendarError(f"Failed to create event: {error}")

        return _serialize_event(ev)

    def update_event(self, event_id, future_events=False, **kwargs):
        """Update an existing event by ID."""
        ev = self.store.eventWithIdentifier_(event_id)
        if ev is None:
            raise CalendarError(f"Event not found: {event_id}")

        if "title" in kwargs and kwargs["title"] is not None:
            ev.setTitle_(kwargs["title"])
        if "start" in kwargs and kwargs["start"] is not None:
            ev.setStartDate_(_datetime_to_nsdate(kwargs["start"]))
        if "end" in kwargs and kwargs["end"] is not None:
            ev.setEndDate_(_datetime_to_nsdate(kwargs["end"]))
        if "location" in kwargs and kwargs["location"] is not None:
            ev.setLocation_(kwargs["location"])
        if "notes" in kwargs and kwargs["notes"] is not None:
            ev.setNotes_(kwargs["notes"])
        if "all_day" in kwargs and kwargs["all_day"] is not None:
            ev.setAllDay_(kwargs["all_day"])

        span = EK_SPAN_FUTURE if future_events else EK_SPAN_THIS
        success, error = self.store.saveEvent_span_error_(ev, span, None)
        if not success:
            raise CalendarError(f"Failed to update event: {error}")

        return _serialize_event(ev)

    def delete_event(self, event_id, future_events=False):
        """Delete an event by ID."""
        ev = self.store.eventWithIdentifier_(event_id)
        if ev is None:
            raise CalendarError(f"Event not found: {event_id}")

        span = EK_SPAN_FUTURE if future_events else EK_SPAN_THIS
        success, error = self.store.removeEvent_span_error_(ev, span, None)
        if not success:
            raise CalendarError(f"Failed to delete event: {error}")

        return {"deleted": event_id}

    # NOTE (issue #59 code review, MINOR #4): this class intentionally does
    # NOT define find_free_time/find_conflicts. mcp_server.py's
    # find_free_time/event_conflicts tools always operate on a merged,
    # cross-backend event list (Family Calendar + Personal/Google
    # together) via its own _find_free_time_slots/_find_conflicts - a
    # per-backend version here could never do that and would go uncalled
    # dead code. Do not re-add these methods without wiring mcp_server.py
    # to actually call them; see GoogleCalendarClient in google_calendar.py
    # for the same reasoning on the Google side.

    def search_events(self, query, start, end, calendar_id=None, limit=20):
        """Search events by title across a date range (case-insensitive).

        Args:
            query: Search text matched against event title.
            start: Start datetime.
            end: End datetime.
            calendar_id: Optional calendar name or ID to filter.
            limit: Maximum number of results (default: 20).
        """
        events = self.list_events(start, end, calendar_id=calendar_id)
        query_lower = query.lower()
        matched = [e for e in events if query_lower in (e.get("title") or "").lower()]
        return matched[:limit]

    def _find_calendar_by_title(self, title):
        """Find a calendar by title (case-insensitive)."""
        cals = self.store.calendarsForEntityType_(EK_ENTITY_EVENT)
        title_lower = title.lower()
        for cal in cals:
            if cal.title().lower() == title_lower:
                return cal
        return None


# ── Helpers ──


def _serialize_event(ev):
    """Convert an EKEvent to a serializable dict."""
    start = _nsdate_to_datetime(ev.startDate())
    end = _nsdate_to_datetime(ev.endDate())

    result = {
        "id": ev.eventIdentifier(),
        "title": ev.title() or "(no title)",
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "all_day": bool(ev.isAllDay()),
        "location": ev.location() or None,
        "notes": ev.notes() or None,
        "calendar": ev.calendar().title() if ev.calendar() else None,
        "calendar_id": ev.calendar().calendarIdentifier() if ev.calendar() else None,
        "status": _event_status_name(ev.status()),
        "availability": _availability_name(ev.availability()),
        "has_recurrence": bool(ev.hasRecurrenceRules()),
        "url": str(ev.URL()) if ev.URL() else None,
    }

    attendees = ev.attendees()
    if attendees:
        result["attendees"] = []
        for a in attendees:
            result["attendees"].append(
                {
                    "name": a.name() or None,
                    "status": _participant_status_name(a.participantStatus()),
                    "type": _participant_type_name(a.participantType()),
                }
            )

    return result


def _calendar_type_name(cal_type):
    """Convert EKCalendarType to string."""
    names = {0: "local", 1: "caldav", 2: "exchange", 3: "subscription", 4: "birthday"}
    return names.get(cal_type, f"unknown({cal_type})")


def _event_status_name(status):
    """Convert EKEventStatus to string."""
    names = {0: "none", 1: "confirmed", 2: "tentative", 3: "cancelled"}
    return names.get(status, f"unknown({status})")


def _availability_name(avail):
    """Convert EKEventAvailability to string."""
    names = {-1: "not_supported", 0: "busy", 1: "free", 2: "tentative", 3: "unavailable"}
    return names.get(avail, f"unknown({avail})")


def _participant_status_name(status):
    """Convert EKParticipantStatus to string."""
    names = {
        0: "unknown",
        1: "pending",
        2: "accepted",
        3: "declined",
        4: "tentative",
        5: "delegated",
        6: "completed",
        7: "in_process",
    }
    return names.get(status, f"unknown({status})")


def _participant_type_name(ptype):
    """Convert EKParticipantType to string."""
    names = {0: "unknown", 1: "person", 2: "room", 3: "resource", 4: "group"}
    return names.get(ptype, f"unknown({ptype})")
