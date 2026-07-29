# apple-eventkit-tools

Apple Calendar and Reminders MCP server for [Claude Code](https://docs.anthropic.com/en/docs/claude-code). Reads and writes calendar events and reminders natively through macOS EventKit via [PyObjC](https://pyobjc.readthedocs.io/) - no API keys, no credentials, no CalDAV setup.

Talks directly to the CalendarAgent daemon. Calendar.app and Reminders.app do not need to be running. Works with all providers configured in macOS: iCloud, Exchange, Google, subscriptions, and local calendars.

**macOS only** - requires EventKit framework.

> This package merges the former ical-tools and reminders-tools into a single package backed by a shared EventKit base class.

## Setup

### From Source

```bash
git clone https://github.com/mrlesmithjr/mcp.git
cd mcp/packages/apple-eventkit-tools
uv tool install --editable .
```

On first use, macOS will prompt your terminal app for Calendar and Reminders access. Grant both in **System Settings > Privacy & Security**.

## MCP Server

Register as a Claude Code MCP server:

```bash
claude mcp add -s user apple-eventkit-tools -- apple-eventkit-mcp
```

### MCP Tools: Calendar (10)

**Read**

| Tool | Description |
|------|-------------|
| `list_calendars` | All calendars with type, color, and modification status |
| `list_events` | Events in a date range, optional calendar filter |
| `today_agenda` | Quick view of today's events |
| `upcoming_events` | Events in the next N days (default: 7) |
| `event_search` | Search events by title across a date range |

**Write**

| Tool | Description |
|------|-------------|
| `create_event` | Create event with location, notes, alarms, URL |
| `update_event` | Update any field, supports recurring event span |
| `delete_event` | Delete single or future recurring occurrences |

**Analysis**

| Tool | Description |
|------|-------------|
| `find_free_time` | Find available time slots in a date range |
| `event_conflicts` | Find overlapping/conflicting events with overlap duration |

### MCP Tools: Reminders (9)

**Lists**

| Tool | Description |
|------|-------------|
| `reminder_lists` | List all reminder lists with IDs, colors, and modification status |
| `reminder_list` | List reminders (all or by list), with optional completed filter |

**Create and Modify**

| Tool | Description |
|------|-------------|
| `reminder_create` | Create a reminder with due date, time, notes, priority, and recurrence |
| `reminder_update` | Update title, due date, time, notes, or priority |
| `reminder_complete` | Mark a reminder as completed |
| `reminder_uncomplete` | Mark a completed reminder as incomplete |
| `reminder_delete` | Delete a reminder |

**Search and Analysis**

| Tool | Description |
|------|-------------|
| `reminder_search` | Search reminders by title or notes text (case-insensitive) |
| `reminder_overdue` | Find past-due incomplete reminders, sorted by most overdue first |

## Entry Points

Three command names are available - all run the same server:

| Command | Purpose |
|---------|---------|
| `apple-eventkit-mcp` | Canonical entry point for new installations |
| `ical-mcp` | Backward-compatible alias for existing ical-tools registrations |
| `reminders-mcp` | Backward-compatible alias for existing reminders-tools registrations |

## How It Works

EventKit is Apple's native framework for calendar and reminder access. The same framework powers Calendar.app and Reminders.app. PyObjC bridges it to Python, giving full read/write access to all calendars and reminder lists configured in macOS.

No network requests are made. All data comes from the local CalendarAgent daemon, which syncs with iCloud (or other providers) in the background.

## Requirements

- macOS 12+ (Monterey or later)
- Python 3.11+
- Terminal app granted Calendar and Reminders access (TCC permissions)

## Project Structure

```
apple_eventkit_tools/
├── __init__.py      # Package init
├── __main__.py      # python -m entry
├── base.py          # EventKitBase: shared store init and _request_access
├── utils.py         # Shared date/color conversion utilities
├── calendar.py      # CalendarManager (10 calendar operations)
├── reminders.py     # RemindersManager (9 reminders operations)
└── mcp_server.py    # FastMCP server (19 tools, JSON output)
```

## License

MIT
