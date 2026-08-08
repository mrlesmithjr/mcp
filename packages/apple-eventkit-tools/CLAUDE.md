# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Status**: ACTIVE
**Last Updated**: 2026-07-11

## Overview

Unified Apple Calendar and Reminders MCP server for Claude Code. As of issue #59, the Personal calendar routes through the Google Calendar API (`GoogleCalendarClient`); Family Calendar and all Reminders continue through the native PyObjC EventKit framework permanently (Family Calendar cannot migrate - see issue #59's Out of scope). No credentials or config file required for EventKit access - macOS TCC grants access on first use. Google Calendar access requires OAuth (see `google_calendar_authorize`/`google_calendar_status` below) with credentials shared at `~/.config/google/credentials.json` across all Google-using packages.

This package merges ical-tools (calendar tools) and reminders-tools (9 reminders tools) into a single package with a shared EventKit base class, plus the Google Calendar client added in #59.

## Setup

```bash
uv tool install --editable .
claude mcp add -s user apple-eventkit-tools -- apple-eventkit-mcp
```

For Personal calendar access, copy the shared Google OAuth client credentials to `~/.config/google/credentials.json` (same file used by mail-tools' Gmail integration - one GCP app registration, Calendar API enabled on the same project), then run `google_calendar_authorize` once.

**Before the first authorize**, the Calendar API must be enabled on that GCP project - it isn't on by default even though the project already has Gmail API enabled. This is a one-time Google Cloud requirement, not something the code can do for you: enable it at `console.cloud.google.com/apis/library/calendar-json.googleapis.com`, or `gcloud services enable calendar-json.googleapis.com` if using the CLI. Skipping this step surfaces as a 403 error on the first live API call, not at authorize time.

## Architecture

```
apple_eventkit_tools/
├── __init__.py         # Package init
├── __main__.py         # python -m entry
├── base.py             # EventKitBase: store lazy-init + _request_access
├── utils.py            # _nscolor_hex, _datetime_to_nsdate, _nsdate_to_datetime, _datetime_to_date_components
├── calendar.py         # CalendarManager(EventKitBase) - EventKit backend, Family Calendar permanently
├── google_calendar.py  # GoogleCalendarClient(GoogleOAuthClient) - Personal calendar (issue #59)
├── reminders.py        # RemindersManager(EventKitBase) - 9 reminders tools
└── mcp_server.py       # Single FastMCP("apple-eventkit-tools"), 21 tools
```

## MCP Tools (21 total)

### Calendar (12)

Routing (`_backend_for_calendar` in `mcp_server.py`): a `calendar` arg of `"personal"`/`"primary"` (case-insensitive) always routes to Google - a fixed fast path, since Family Calendar can never migrate. Any OTHER name is checked against `GoogleCalendarClient.known_calendar_names()` (that account's live, per-process-memoized calendar id/title list); a match routes to Google, otherwise it routes to EventKit. If the Google lookup itself fails (not authorized, network outage), it falls back to EventKit rather than raising. Omitted `calendar` merges both backends. `create_event`'s `calendar=None` default routes to Google. `update_event`/`delete_event` route by a `"google:"` id prefix instead of a `calendar` arg.

| Tool | Description |
|------|-------------|
| `list_calendars` | All calendars (both backends) with ID, title, type, color, `source`. Calendar `title` is wrapped with session security markers (issue #121) - see Prompt injection guarding below |
| `list_events(start_date, end_date, calendar?)` | Events in a date range, merged across backends. `title`/`notes`/`location`/`calendar`/`attendees[].name` are wrapped with session security markers (issue #121) |
| `today_agenda` | Today's events across all calendars. Wrapped the same as `list_events` |
| `upcoming_events(days?)` | Next N days of events. Wrapped the same as `list_events` |
| `event_search(query, start_date?, end_date?)` | Search events - title-only on EventKit, broader (title/description/location/attendees) on Google. Wrapped the same as `list_events` |
| `event_conflicts(start, end)` | Check for conflicts before creating - detects cross-backend overlaps too. `event_a`/`event_b`'s `title`/`calendar` are wrapped (issue #121) |
| `find_free_time(date, duration_minutes?)` | Find open slots, merged across both backends. Not wrapped - slots carry no event content |
| `create_event(title, start, end, calendar?, notes?, location?)` | Create an event (defaults to Google/Personal). Not wrapped - the response echoes the caller's own just-authored arguments, not pre-existing content |
| `update_event(event_id, ...)` | Update event fields (routes on `"google:"` id prefix). Returns the FULL updated event including untouched fields - `title`/`notes`/`location`/`calendar`/`attendees[].name` are always wrapped regardless of what this call changed (issue #121) |
| `delete_event(event_id)` | Delete an event (routes on `"google:"` id prefix) |
| `google_calendar_authorize(account?)` | Authorize the Google account for Calendar API access |
| `google_calendar_status(verify?)` | Show Google Calendar API authorization status |

### Reminders (9)

| Tool | Description |
|------|-------------|
| `reminder_lists` | All Reminders lists with ID, title, color. List `title` is wrapped with session security markers (issue #121) - see Prompt injection guarding below |
| `reminder_list(list_name?, include_completed?)` | List reminders, optionally by list. `title`/`notes`/`list` are wrapped with session security markers (issue #121) |
| `reminder_overdue` | All past-due incomplete reminders. Wrapped the same as `reminder_list` |
| `reminder_search(query)` | Search by title or notes. Wrapped the same as `reminder_list` |
| `reminder_create(title, list_name?, due_date?, due_time?, notes?, priority?)` | Create a reminder. Not wrapped - the response echoes the caller's own just-authored arguments |
| `reminder_update(reminder_id, ...)` | Update reminder fields. Returns the FULL updated reminder including untouched fields - `title`/`notes`/`list` are always wrapped regardless of what this call changed (issue #121) |
| `reminder_complete(reminder_id)` | Mark reminder as completed |
| `reminder_uncomplete(reminder_id)` | Unmark a completed reminder |
| `reminder_delete(reminder_id)` | Delete a reminder |

### Prompt injection guarding (issue #121)

Calendar and Reminders content isn't necessarily account-owner-authored: a calendar event can arrive as an invite from someone else (attacker-controlled title/notes/location/attendee name), and both Calendar and Reminders support iCloud family/device sharing, including calendars and lists a second party shares with or publishes to this account. This server also exposes write-capable tools in the same conversation, making it an indirect-prompt-injection vector - the same class closed for mail-tools, sheets-tools, and contacts-tools. Guarded via the `prompt-security-utils` library - see the root CLAUDE.md's "Prompt injection guarding" section for the shared mechanism. `security_instructions()` is appended after, not replacing, the existing "Check event_conflicts..." guidance.

**Wrapped fields:**

| Tool(s) | Wrapped fields |
|---------|-----------------|
| `list_calendars` | `title` |
| `list_events`, `today_agenda`, `upcoming_events`, `event_search`, `update_event` | `title`, `notes`, `location`, `calendar`, `url`, `attendees[].name` |
| `event_conflicts` | `event_a`/`event_b`'s `title`, `calendar` |
| `reminder_lists` | `title` |
| `reminder_list`, `reminder_overdue`, `reminder_search`, `reminder_update` | `title`, `notes`, `list` |

Not wrapped: `create_event`/`reminder_create` (echo the caller's own just-authored arguments, not pre-existing content), `find_free_time` (derived gap timestamps only, no event content), `delete_event`/`reminder_delete`/`reminder_complete`/`reminder_uncomplete` (id/boolean only), `calendar_id`/`status`/`availability`/`has_recurrence`/`all_day`/`start`/`end`/`id`/`recurrence` (system-generated, enum-valued, or structured fields, not free text), `google_calendar_authorize`/`google_calendar_status` (auth state only). Calendar/list `title` and the per-event/per-reminder `calendar`/`list` field are wrapped unconditionally rather than gated on `is_subscribed`/`accessRole`: a shared or subscribed calendar's `title` (`GoogleCalendarClient.list_calendars()` surfaces these via `calendarList.list`, not just owned calendars; `CalendarManager.list_calendars()` carries the same `is_subscribed` flag for EventKit feeds) is chosen by whoever shared or published it, not this account.

Package-specific mechanics worth knowing:

- **Dual-backend wrapping**: `list_events`/`event_search`/`event_conflicts`/`find_free_time` can merge EventKit and Google Calendar results. Wrapping happens in each `@mcp.tool` function after the merge, never inside `CalendarManager`/`GoogleCalendarClient` individually, so a merged response never ends up with one wrapped event next to one unwrapped one. `event_conflicts` computes overlaps from the raw (unwrapped) merged list first (title/calendar never enter the overlap math), then wraps before returning; `list_calendars` sorts on the raw `title` first, then wraps in a final pass, so display ordering is unaffected.
- **`update_event`/`reminder_update` echo untouched fields**: both return the FULL updated object (`_serialize_event`/`_serialize_reminder`), not just the changed fields, so a call that only touches `start_time` still echoes back whatever `title`/`notes`/`calendar` the event already had - potentially pre-existing, attacker-controlled content from an earlier invite. Both wrap the full object regardless of which fields actually changed.
- **`url` is wrapped**: safe on the Google backend (`google_calendar.py` sets it from the API's non-writable `htmlLink`) but caller/inviter-settable free text on EventKit (`calendar.py`'s `create_event()` writes it verbatim) - since both backends merge into one shape before wrapping, `url` is wrapped unconditionally alongside `title`/`notes`/`location`/`calendar`.
- **Reminders are wrapped despite being closed-world** (`openWorldHint=False`): lower risk than Calendar since there's no invite-based ingestion, but iCloud family/device sharing still means a shared list's content isn't guaranteed account-owner-authored, and this server exposes reminder mutation tools in the same conversation.

## Key Patterns

- `EventKitBase._request_access` uses `_access_config()` hook: subclasses return `(entity_type_int, full_access_method_name, error_label)`
- The base dispatches via `getattr(self._store, full_access_method)` - avoids duplicating the hasattr/fallback pattern
- Threading pattern for async callbacks: `threading.Event` + 30-second timeout in `_request_access`, `list_reminders`, `overdue_reminders`, `search_reminders`, `_find_reminder_fresh`
- `_find_reminder_fresh` exists because `calendarItemWithIdentifier_` returns stale cached objects; predicate fetch gives a live mutable reference
- Call `event_conflicts` before `create_event` (enforced via MCP instructions)
- Date strings accept ISO 8601 or date-only (YYYY-MM-DD)
- `due_date` format: YYYY-MM-DD; `due_time` format: HH:MM (24-hour)
- Priority: 0=none, 1=high, 5=medium, 9=low (matches EKReminderPriority)
- All tools return structured JSON; errors return `{"error": "..."}`
- `future_events` (recurring-series edit/delete scope) has no Google Calendar equivalent - EventKit's `EKSpanFutureEvents` flag is silently ignored on `"google:"`-prefixed ids; Google controls series-vs-instance scope via which event id you pass, not a flag (see `GoogleCalendarClient.update_event`'s docstring)
- `GoogleCalendarClient` only ever talks to one configured account/calendar (defaults to the sole authorized account / `"primary"`, overridable via `~/.config/apple-eventkit-tools/config.json` or `GOOGLE_CALENDAR_ACCOUNT`/`GOOGLE_CALENDAR_CALENDAR_ID` env vars) - it is not a per-call multi-account client

## Entry Points

| Command | Purpose |
|---------|---------|
| `apple-eventkit-mcp` | Canonical entry point |
| `ical-mcp` | Backward-compatible alias |
| `reminders-mcp` | Backward-compatible alias |
