"""Google Calendar API client (issue #59) - REST alternative to EventKit
for the Personal calendar, once its data has migrated to Google.

Family Calendar stays on EventKit (calendar.py's CalendarManager)
permanently - see issue #59's Out of scope. This client only ever talks to
the single Google account/calendar configured for apple-eventkit-tools
(the sole authorized account / "primary" by default), not an arbitrary
per-call account.
"""

from __future__ import annotations

import logging
from datetime import datetime
from urllib.parse import quote, urlencode

from mcp_common.config import load_layered_config
from mcp_common.google_oauth import GoogleOAuthClient, GoogleOAuthError

logger = logging.getLogger(__name__)

CALENDAR_API = "https://www.googleapis.com/calendar/v3"

_ENV_MAP = {
    "account": "GOOGLE_CALENDAR_ACCOUNT",
    "calendar_id": "GOOGLE_CALENDAR_CALENDAR_ID",
}
_DEFAULT_CALENDAR_ID = "primary"

# Google's events.list caps a single page at 2500 items.
_MAX_PAGE_SIZE = 2500


class GoogleCalendarClient(GoogleOAuthClient):
    """Google Calendar API v3 client, same event/calendar dict shapes as
    apple_eventkit_tools.calendar._serialize_event/CalendarManager, so
    mcp_server.py can merge results from both backends.
    """

    SCOPES = ["https://www.googleapis.com/auth/calendar"]

    def __init__(self):
        super().__init__(tool_name="apple-eventkit-tools", scopes=self.SCOPES)
        cfg = load_layered_config("apple-eventkit-tools", _ENV_MAP)
        self.account = cfg.get("account") or self._sole_authorized_account()
        self.calendar_id = cfg.get("calendar_id", _DEFAULT_CALENDAR_ID)
        self._calendar_titles: dict[str, str] | None = None

    def _sole_authorized_account(self) -> str | None:
        """Account to use when none is configured.

        There is no meaningful hardcoded default here - the account is
        whichever Google account the installing user authorized. When exactly
        one is on file, use it (the overwhelmingly common single-account
        case). With zero or several, leave it unset so request() raises a
        clear "not authorized" error naming the account, rather than silently
        picking someone else's calendar. Override with
        GOOGLE_CALENDAR_ACCOUNT or config.json's "account" key.
        """
        accounts = self.list_authorized_accounts()
        return accounts[0] if len(accounts) == 1 else None

    # ── Calendars ──

    def list_calendars(self) -> list[dict]:
        """Return all calendars visible to the configured account via
        calendarList.list (NOT Calendars.list - that endpoint only returns
        calendars the caller itself owns/administers metadata for).
        """
        url = f"{CALENDAR_API}/users/me/calendarList"
        calendars = []
        page_token = None
        while True:
            page_url = url + (f"?pageToken={page_token}" if page_token else "")
            result = self.request(self.account, "GET", page_url)
            for item in result.get("items", []):
                calendars.append(
                    {
                        "id": item["id"],
                        "title": item.get("summary", item["id"]),
                        "type": "google",
                        "color": item.get("backgroundColor"),
                        "is_subscribed": not item.get("primary", False),
                        "allows_modification": item.get("accessRole") in ("owner", "writer"),
                        "source": "google",
                    }
                )
            page_token = result.get("nextPageToken")
            if not page_token:
                break
        return sorted(calendars, key=lambda c: c["title"])

    def _ensure_calendar_titles(self) -> dict[str, str]:
        """Lazily cache id->title from list_calendars() for this instance's
        lifetime (a single calendarList.list call, not one per invocation),
        shared by _calendar_title() and known_calendar_names().
        """
        if self._calendar_titles is None:
            try:
                self._calendar_titles = {c["id"]: c["title"] for c in self.list_calendars()}
            except GoogleOAuthError:
                self._calendar_titles = {}
        return self._calendar_titles

    def _calendar_title(self, calendar_id: str) -> str | None:
        return self._ensure_calendar_titles().get(calendar_id, calendar_id)

    def known_calendar_names(self) -> set[str]:
        """Lowercased set of every calendar id/title currently visible to
        the configured account, memoized for this instance's lifetime.

        Used by mcp_server._backend_for_calendar to route an arbitrary
        Google calendar name/id (not just the "personal"/"primary"
        aliases) correctly - without this, a second real Google calendar
        would silently route to EventKit and return an empty result
        instead of the real data (issue #59 code review, MAJOR #2).
        """
        names = set()
        for cal_id, title in self._ensure_calendar_titles().items():
            names.add(cal_id.lower())
            if title:
                names.add(title.lower())
        return names

    # ── Events ──

    def list_events(self, start: datetime, end: datetime, calendar_id: str | None = None) -> list[dict]:
        """List events in a date range via events.list.

        calendar_id is accepted for interface parity with CalendarManager
        but this client only ever has the one configured calendar - a
        non-None value here is ignored in favor of self.calendar_id, since
        an EventKit-style calendar name/id ("Personal") is not a valid
        Google calendar id.
        """
        cal_id = self.calendar_id
        title = self._calendar_title(cal_id)
        events = []
        page_token = None
        while True:
            params = {
                "timeMin": _to_rfc3339(start),
                "timeMax": _to_rfc3339(end),
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": str(_MAX_PAGE_SIZE),
            }
            if page_token:
                params["pageToken"] = page_token
            url = f"{CALENDAR_API}/calendars/{quote(cal_id, safe='')}/events?{urlencode(params)}"
            result = self.request(self.account, "GET", url)
            for raw in result.get("items", []):
                events.append(_serialize_event(raw, cal_id, title))
            page_token = result.get("nextPageToken")
            if not page_token:
                break
        return sorted(events, key=lambda e: e.get("start") or "")

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
        recurrence=None,
    ) -> dict:
        """Create a new event via events.insert.

        recurrence is a raw list of RFC5545 strings (e.g.
        ["RRULE:FREQ=WEEKLY;BYDAY=MO"]), unlike EventKit's rule objects.
        """
        cal_id = self.calendar_id
        body = {
            "summary": title,
            "start": _to_google_datetime(start, all_day),
            "end": _to_google_datetime(end, all_day),
        }
        if location:
            body["location"] = location
        if notes:
            body["description"] = notes
        if url:
            body["source"] = {"url": url, "title": title}
        if recurrence:
            body["recurrence"] = recurrence
        if alarm_minutes is not None:
            body["reminders"] = {
                "useDefault": False,
                "overrides": [{"method": "popup", "minutes": alarm_minutes}],
            }

        endpoint = f"{CALENDAR_API}/calendars/{quote(cal_id, safe='')}/events"
        raw = self.request(self.account, "POST", endpoint, body=body)
        return _serialize_event(raw, cal_id, self._calendar_title(cal_id))

    def update_event(self, event_id, future_events=False, **kwargs) -> dict:
        """Update an event via events.patch (partial update semantics).

        future_events has no direct Google equivalent: EventKit's
        EKSpanFutureEvents flag doesn't exist here. Updating a whole
        recurring series vs. a single instance in Google Calendar is
        controlled by whether event_id is the master event's id or a
        specific instance id, not by a flag - this parameter is accepted
        for interface parity but has no effect on this backend.

        If start or end is being updated and the caller doesn't explicitly
        pass all_day, the existing event is fetched first to inherit its
        current all_day-ness (same "never trust the caller's implicit
        assumption, re-fetch first" pattern GooglePeopleClient.update_contact
        uses for its etag). Without this, updating only start_time on an
        existing all-day event would default all_day=False and send a
        dateTime-typed start against an unchanged date-typed end - a type
        mismatch Google Calendar is likely to reject. EventKit's
        CalendarManager.update_event avoids this entirely by mutating the
        live EKEvent object in place, which implicitly preserves all-day-ness.
        """
        cal_id = self.calendar_id
        endpoint = f"{CALENDAR_API}/calendars/{quote(cal_id, safe='')}/events/{quote(event_id, safe='')}"

        body = {}
        if "title" in kwargs and kwargs["title"] is not None:
            body["summary"] = kwargs["title"]

        changing_dates = ("start" in kwargs and kwargs["start"] is not None) or (
            "end" in kwargs and kwargs["end"] is not None
        )
        all_day = kwargs.get("all_day")
        if changing_dates and all_day is None:
            existing = self.request(self.account, "GET", endpoint)
            all_day = bool((existing.get("start") or {}).get("date"))

        if "start" in kwargs and kwargs["start"] is not None:
            body["start"] = _to_google_datetime(kwargs["start"], bool(all_day))
        if "end" in kwargs and kwargs["end"] is not None:
            body["end"] = _to_google_datetime(kwargs["end"], bool(all_day))
        if "location" in kwargs and kwargs["location"] is not None:
            body["location"] = kwargs["location"]
        if "notes" in kwargs and kwargs["notes"] is not None:
            body["description"] = kwargs["notes"]

        raw = self.request(self.account, "PATCH", endpoint, body=body)
        return _serialize_event(raw, cal_id, self._calendar_title(cal_id))

    def delete_event(self, event_id, future_events=False) -> dict:
        """Delete an event via events.delete. See update_event's docstring
        for why future_events has no effect on this backend.
        """
        cal_id = self.calendar_id
        endpoint = f"{CALENDAR_API}/calendars/{quote(cal_id, safe='')}/events/{quote(event_id, safe='')}"
        self.request(self.account, "DELETE", endpoint)
        return {"deleted": event_id}

    def search_events(self, query, start, end, calendar_id=None, limit=20) -> list[dict]:
        """Search events via events.list's `q` param - free-text search
        across title/description/location/attendees. This is broader than
        CalendarManager.search_events's title-only substring match (a real
        behavior change moving from EventKit to Google for the same tool).
        """
        cal_id = self.calendar_id
        title = self._calendar_title(cal_id)
        params = {
            "timeMin": _to_rfc3339(start),
            "timeMax": _to_rfc3339(end),
            "q": query,
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": str(min(limit, _MAX_PAGE_SIZE)),
        }
        url = f"{CALENDAR_API}/calendars/{quote(cal_id, safe='')}/events?{urlencode(params)}"
        result = self.request(self.account, "GET", url)
        events = [_serialize_event(raw, cal_id, title) for raw in result.get("items", [])]
        return events[:limit]

    # NOTE (issue #59 code review, MINOR #4): this class intentionally does
    # NOT define find_conflicts/find_free_time. mcp_server.py's
    # event_conflicts/find_free_time tools always operate on a merged,
    # cross-backend event list (Personal + Family Calendar together), via
    # its own _find_conflicts/_find_free_time_slots - a per-backend version
    # here could never do that and would go uncalled dead code. Do not
    # re-add these methods without wiring mcp_server.py to actually call
    # them; see CalendarManager.find_conflicts/find_free_time in
    # calendar.py for the same reasoning on the EventKit side.


# ── Helpers ──


def _to_rfc3339(dt: datetime) -> str:
    """Format a datetime as RFC3339 for the Calendar API.

    Naive-datetime policy (issue #59 code review follow-up): a naive `dt`
    (no tzinfo) is deliberately treated as already being in the machine's
    system-local timezone via `dt.astimezone()` - the same implicit
    contract `utils.py`'s `_datetime_to_nsdate`/`_nsdate_to_datetime`
    already establish for the EventKit backend (`dt.timestamp()` and
    `datetime.fromtimestamp(ts).astimezone()` both carry the identical
    "naive means system-local" assumption). This is intentional, not an
    oversight: apple-eventkit-tools is a single-user personal tool that
    only ever runs on the user's own Mac, so "system timezone" IS the
    user's timezone - there is no separate "configured" timezone to
    introduce, and mcp_server.py's `_parse_datetime` commonly produces
    naive datetimes (e.g. `"2026-07-10T09:00"` with no UTC offset) that
    must keep meaning "9am local time" on this backend exactly as they do
    on the EventKit one. Callers that need a specific, non-system
    timezone must pass an already-aware `dt`.

    Tests must never assert on the exact serialized offset of a
    deliberately-naive input - that bakes in whatever the test runner's
    system timezone happens to be (issue #59, CI failure on a UTC
    runner). Construct a timezone-aware `dt` directly instead.
    """
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.isoformat()


def _to_google_datetime(dt: datetime, all_day: bool) -> dict:
    """Format a datetime as a Calendar API start/end object.

    See _to_rfc3339's docstring for the naive-datetime ("system-local")
    policy this shares. The all_day branch never needs it: `strftime`
    reads the naive datetime's own year/month/day fields directly with no
    timezone conversion at all, so it is not system-timezone-dependent.
    """
    if all_day:
        return {"date": dt.strftime("%Y-%m-%d")}
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return {"dateTime": dt.isoformat()}


def _parse_google_datetime(dt_field: dict) -> tuple[str | None, bool]:
    """Return (iso_string, all_day) for a Calendar API start/end dict."""
    if not dt_field:
        return None, False
    if "date" in dt_field:
        return dt_field["date"], True
    return dt_field.get("dateTime"), False


def _availability_from_transparency(transparency: str | None) -> str:
    """Google's `transparency` field: "opaque" (busy, default when absent)
    or "transparent" (free) - map to the same busy/free vocabulary
    CalendarManager._serialize_event uses for EventKit's availability enum.
    """
    return "free" if transparency == "transparent" else "busy"


def _serialize_event(raw: dict, calendar_id: str, calendar_title: str | None = None) -> dict:
    """Convert a Calendar API Event resource to the same dict shape as
    apple_eventkit_tools.calendar._serialize_event, prefixed "google:" so
    mcp_server.py can route update_event/delete_event calls back to this
    backend from the id alone.
    """
    start_value, all_day = _parse_google_datetime(raw.get("start", {}))
    end_value, _ = _parse_google_datetime(raw.get("end", {}))

    attendees = []
    for a in raw.get("attendees", []) or []:
        attendees.append(
            {
                "name": a.get("displayName") or a.get("email"),
                "status": a.get("responseStatus", "unknown"),
                "type": "resource" if a.get("resource") else "person",
            }
        )

    result = {
        "id": f"google:{raw.get('id')}",
        "title": raw.get("summary") or "(no title)",
        "start": start_value,
        "end": end_value,
        "all_day": all_day,
        "location": raw.get("location") or None,
        "notes": raw.get("description") or None,
        "calendar": calendar_title,
        "calendar_id": calendar_id,
        "status": raw.get("status", "confirmed"),
        "availability": _availability_from_transparency(raw.get("transparency")),
        "has_recurrence": bool(raw.get("recurrence")),
        "recurrence": raw.get("recurrence"),
        "url": raw.get("htmlLink"),
        "source": "google",
    }
    if attendees:
        result["attendees"] = attendees

    return result
