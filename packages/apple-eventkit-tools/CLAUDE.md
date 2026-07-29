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
| `list_calendars` | All calendars (both backends) with ID, title, type, color, `source`. Calendar `title` is wrapped with session security markers (issue #121) - see Prompt Injection Guarding below |
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
| `reminder_lists` | All Reminders lists with ID, title, color. List `title` is wrapped with session security markers (issue #121) - see Prompt Injection Guarding below |
| `reminder_list(list_name?, include_completed?)` | List reminders, optionally by list. `title`/`notes`/`list` are wrapped with session security markers (issue #121) |
| `reminder_overdue` | All past-due incomplete reminders. Wrapped the same as `reminder_list` |
| `reminder_search(query)` | Search by title or notes. Wrapped the same as `reminder_list` |
| `reminder_create(title, list_name?, due_date?, due_time?, notes?, priority?)` | Create a reminder. Not wrapped - the response echoes the caller's own just-authored arguments |
| `reminder_update(reminder_id, ...)` | Update reminder fields. Returns the FULL updated reminder including untouched fields - `title`/`notes`/`list` are always wrapped regardless of what this call changed (issue #121) |
| `reminder_complete(reminder_id)` | Mark reminder as completed |
| `reminder_uncomplete(reminder_id)` | Unmark a completed reminder |
| `reminder_delete(reminder_id)` | Delete a reminder |

### Prompt Injection Guarding (issue #121)

`list_events`/`today_agenda`/`upcoming_events`/`event_search`/`event_conflicts`/`list_calendars` (Calendar) and `reminder_list`/`reminder_overdue`/`reminder_search`/`reminder_lists` (Reminders) return event/reminder/calendar/list content that isn't necessarily account-owner-authored: a calendar event can arrive as an invite from someone else (attacker-controlled title/notes/location/attendee name), and both Calendar and Reminders support iCloud family/device sharing, including calendars and lists a SECOND PARTY shares with or publishes to this account. This server also exposes write-capable tools (`create_event`, `update_event`, `delete_event`, `reminder_create`, `reminder_update`, `reminder_complete`/`uncomplete`, `reminder_delete`) in the same conversation, making this an indirect-prompt-injection vector - the same class closed for mail-tools (#115), sheets-tools (#118), and contacts-tools (#120). `mcp_server.py` uses the `prompt-security-utils` PyPI library the same way: `generate_markers()` runs once at module load, `security_instructions()` folds those markers into `FastMCP(instructions=...)` (appended after the existing "Check event_conflicts..." guidance, not replacing it), and `_wrap_event()`/`_wrap_events()`/`_wrap_reminder()`/`_wrap_reminders()` (thin wrappers over the library's `wrap_field()` via `_wrap_untrusted_field()`) wrap `title`/`notes`/`location`/`calendar`/`attendees[].name` (events) or `title`/`notes`/`list` (reminders) before `json.dumps`; `list_calendars`/`reminder_lists` wrap their own `title` field the same way, inline in each tool function. `SecurityConfig(semantic_enabled=False, llm_screen_enabled=False)` matches the other three packages.

**Calendar/list names are wrapped too (issue #121 follow-up review, corrects the original decision below).** The original #121 pass left `list_calendars`'/`reminder_lists`' own `title` (and the per-event/per-reminder `calendar`/`list` field) unwrapped on the reasoning that calendar/list names are "a small, account-owned, effectively static set of names the user themselves creates/administers." That reasoning does not hold: `GoogleCalendarClient.list_calendars()` (`google_calendar.py`) deliberately calls `calendarList.list` (not `Calendars.list`) specifically because it surfaces calendars SHARED WITH or SUBSCRIBED BY the account, not just ones it owns - it already computes `is_subscribed = not item.get("primary", False)` for exactly these entries, and a shared/subscribed calendar's `title` comes from `item.get("summary", item["id"])`, chosen by whoever shared or published the calendar, not this account. `CalendarManager.list_calendars()` (`calendar.py`) carries the same `is_subscribed` flag for EventKit-subscribed feeds (e.g. a subscribed public iCal feed). The same logic applies to Reminders lists via iCloud family/device sharing. Decision: wrap `title` in `list_calendars`/`reminder_lists`, and wrap the `calendar`/`list` field wherever `_wrap_event`/`_wrap_reminder` are already applied, unconditionally rather than gating on `is_subscribed`/`accessRole` - simpler and consistent with how every other ambiguous field in this series (mail-tools, sheets-tools, contacts-tools) is handled: default to wrapping rather than distinguishing trust tiers within a single field.

**Dual-backend wrapping.** Calendar tools route through EventKit and/or the Google Calendar API (see Architecture above) and `list_events`/`event_search`/`event_conflicts`/`find_free_time` can merge results from both. Wrapping happens in each `@mcp.tool` function, after `_list_events_merged()` (or `event_search`'s own inline merge) has already combined both backends' results into one list - never inside `CalendarManager`/`GoogleCalendarClient` individually - so a merged response can never end up with one wrapped event sitting next to one unwrapped event. `_wrap_event()` is reused for both the full event shape (`list_events` etc.) and the trimmed `event_a`/`event_b` refs `event_conflicts` builds (id/title/start/end/calendar only) - it only wraps fields that are actually present, so the same helper covers both shapes without duplicating logic. `event_conflicts` computes overlaps from the raw (unwrapped) merged event list first - title/calendar never enter the overlap math - then wraps `event_a`/`event_b` before returning. `list_calendars` merges and sorts both backends' calendar lists on the raw (unwrapped) `title` first, then wraps `title` in a final pass just before `json.dumps` - so display ordering is unaffected by wrapping.

**`update_event`/`reminder_update` echo untouched fields.** Both tools mutate the live object and return the FULL result (`_serialize_event`/`_serialize_reminder`), not just the fields the caller changed - calling `update_event(event_id, start_time=...)` without touching `title`/`notes`/`calendar` still echoes back whatever title/notes/calendar the event already had, which may be pre-existing, untouched, attacker-controlled content from an earlier invite. Both wrap the full returned object regardless of which fields the call actually changed.

**Reminders wrapping decision (explicit, per issue #121).** Reminders are EventKit-only/closed-world (`openWorldHint=False`) and lower risk than Calendar - they don't ingest external invites the way Google Calendar does. They are NOT risk-free, though: iCloud family/device sharing means a shared list's `title`/`notes`/`list` content isn't guaranteed to be authored by the account owner, and this server exposes `reminder_update`/`reminder_delete`/`reminder_complete`/`reminder_uncomplete` in the same conversation - the same vulnerability shape as Calendar, just a narrower attack surface (no invite-based ingestion). Decision: **wrap** `reminder_list`/`reminder_overdue`/`reminder_search`/`reminder_update`'s `title`/`notes`/`list`, matching Calendar rather than treating "closed-world" as "low priority enough to skip."

**Full sweep results (all 21 tools).** `create_event`/`reminder_create` are not wrapped - their responses echo the caller's own just-supplied arguments (brand new content, not pre-existing data from elsewhere), same reasoning as sheets-tools' `sheet_create`. `delete_event`/`reminder_delete`/`reminder_complete`/`reminder_uncomplete` return only an id/boolean, no content. `find_free_time` returns only derived gap timestamps (`start`/`end`/`duration_minutes`), no event content. `list_calendars`'s calendar `title`, `reminder_lists`' list `title`, and the per-event/per-reminder `calendar`/`list` field are all wrapped (see the follow-up review note above). `calendar_id`/`status`/`availability`/`has_recurrence`/`all_day`/`start`/`end`/`id`/`recurrence` (Google's RFC5545 rule strings) are left unwrapped throughout - system-generated, enum-valued, or structured/non-prose fields, not free text. `google_calendar_authorize`/`google_calendar_status` return only auth state, no calendar content.

**`url` is wrapped too (issue #121 third follow-up review, corrects an earlier claim in this same section).** An earlier pass grouped `url` with the system-generated/enum-valued fields above and left it unwrapped. That was correct for the Google backend only: `google_calendar.py`'s `_serialize_event` sets `"url": raw.get("htmlLink")`, a Calendar-API-generated, non-writable view link, genuinely safe to leave bare. It was wrong for the EventKit backend: `calendar.py`'s `create_event()` writes `ev.setURL_(NSURL.URLWithString_(url))` verbatim from the `url` parameter, which is caller/inviter-settable free text - exactly as untrusted as `location`, since anyone with edit access to an event (including an inviter, or the publisher of a subscribed calendar) can set it. Since both backends' results are merged into one shape before `_wrap_event()` runs (see "Dual-backend wrapping" above), `url` is now wrapped unconditionally in `_wrap_event()`'s field list alongside `title`/`notes`/`location`/`calendar`, matching how every other single-field wrap in this series does not try to distinguish backend origin per-field. This incidentally wraps Google's safe `htmlLink`-derived `url` too - unnecessary but harmless.

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
