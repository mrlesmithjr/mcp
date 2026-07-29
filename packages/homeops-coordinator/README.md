# homeops-coordinator

An event-driven coordinator daemon for cross-system personal ops sync. Runs 24/7 as a macOS LaunchAgent, processing events from connected tools and executing deterministic rules, no AI, no API cost.

## What It Does

Tools like [homeops](https://github.com/mrlesmithjr/mcp/tree/main/packages/homeops) and [lawnops](https://github.com/mrlesmithjr/mcp/tree/main/packages/lawnops) write events to a shared SQLite bus (`events.db`) when state changes. The coordinator polls that bus every 60 seconds and applies rules, completing Apple Reminders when tasks are done, suspending irrigation after lawn treatments, checking budget balances when bills are added, and auto-completing YNAB planned expenses when matching transactions clear. Outside the event bus, it also runs a daily wall-clock-gated check of LawnOps' irrigation budget status and alerts via Apple Reminder if it's in warning or over.

```
homeops task_done ──┐
homeops utility_add─┤
lawnops treatment_add┘   →   events.db   →   coordinator daemon   →   actions
```

## Architecture

```
coordinator/
├── __main__.py       entry point, sets up logging, starts daemon
├── daemon.py         60s polling loop + 15-min YNAB poller + daily irrigation budget check
├── events.py         read/mark-processed events from events.db
├── rules.py          deterministic rule table, add new rules here
├── reminders.py      Apple Reminders wrapper (pyobjc EventKit)
├── lawnops_tools.py  Hydrawise irrigation + lawnops product inventory + irrigation budget status
└── ynab_tools.py     YNAB planned expense matching + category balance
```

### Rule table

| Event | Source | Action |
|-------|--------|--------|
| `task_done`, `task_pause`, `task_delete` | homeops | Search Apple Reminders by task name, complete all matches |
| `treatment_add` | lawnops | Suspend Hydrawise irrigation 24h (skipped if the treatment's own `date` is more than `TREATMENT_STALE_DAYS` (2) days old, e.g. after a coordinator outage); create reorder Reminder if product qty is 0 regardless of staleness |
| `utility_add` | homeops | Read YNAB Utilities category balance; create high-priority Reminder if bill exceeds balance |
| YNAB cleared transaction | ynab.db | Auto-complete matching active planned expense (±5% amount, same category) |

Adding a new rule: add an `elif` branch in `coordinator/rules.py`. No other files change.

### Daily irrigation budget check

Not event-driven -- `daemon.py` calls `rules.check_irrigation_budget()` directly once
per calendar day, gated by wall-clock time (at/after 04:30) rather than by the events
table. It reads LawnOps' irrigation budget status via `lawnops_tools.get_irrigation_budget_status()`
and, if `budget_status` is `warning` or `over`, creates an Apple Reminder (title includes
percent used; `over` gets priority 1, `warning` priority 5) so the user can decide whether
to run `lawnops irrigation suspend` manually. Stays silent for `ok`/`no_budget`, and dedups
against any already-open "Irrigation budget" reminder the same way `_handle_utility_add`
dedups its own reminders. Purely human-in-the-loop -- no Home Assistant involvement.

## Prerequisites

- macOS (uses pyobjc EventKit for Apple Reminders)
- [apple-eventkit-tools](https://github.com/mrlesmithjr/mcp/tree/main/packages/apple-eventkit-tools) installed and TCC-authorized
- [lawnops](https://github.com/mrlesmithjr/mcp/tree/main/packages/lawnops) installed (for irrigation + inventory rules)
- [ynab-tools](https://github.com/mrlesmithjr/mcp/tree/main/packages/ynab-tools) synced to `~/.local/share/ynab-tools/ynab.db` (for YNAB rules)

## Installation

```bash
# Clone the workspace
git clone https://github.com/mrlesmithjr/mcp.git

# Install dependencies first
claude plugin install apple-eventkit-tools@mrlesmithjr-mcp
uv tool install --editable mcp/packages/lawnops

# Install the coordinator
uv tool install --editable mcp/packages/homeops-coordinator
```

## Adding Event Sources

The canonical way to emit events is `mcp_common.coordinator_events.emit_coordinator_event`
(in the `mcp-common` shared package, which both `homeops` and `lawnops` already depend
on). It matches the schema below, is self-healing (creates the data dir and table on
first use), and never raises. A failed emit returns `False` instead of interrupting the
caller:

```python
from mcp_common.coordinator_events import emit_coordinator_event

emit_coordinator_event("mytool", "record_added", {"name": "example", "category": "general"})
```

Wire the call into the tool's **db-layer function** (not the MCP tool wrapper), so both
CLI and MCP invocation paths emit the event. See `homeops/db/tasks.py::mark_done`,
`homeops/db/utilities.py::add_utility_bill`, and `lawnops/db/treatments.py::add_treatment`
for the four current call sites.

`coordinator_events` lives in the leaf `mcp-common` package rather than here in
`homeops-coordinator`, because `lawnops` cannot depend on `homeops-coordinator` for this:
`homeops-coordinator` imports `lawnops.irrigation.suspend` directly, so that direction
would be a circular dependency. Tools that don't want a dependency on `mcp-common` can
still emit events with a self-contained function using the same schema:

```python
import json as _json
import os as _os
import sqlite3 as _sqlite3

_COORDINATOR_DB = _os.path.expanduser("~/.local/share/coordinator/events.db")
_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source       TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    payload      TEXT NOT NULL,
    created_at   TEXT DEFAULT (datetime('now')),
    processed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_processed ON events (processed_at, created_at);
"""

def emit_event(source, event_type, payload):
    try:
        _os.makedirs(_os.path.dirname(_COORDINATOR_DB), exist_ok=True)
        conn = _sqlite3.connect(_COORDINATOR_DB)
        conn.executescript(_EVENTS_DDL)
        conn.execute(
            "INSERT INTO events (source, event_type, payload) VALUES (?, ?, ?)",
            (source, event_type, _json.dumps(payload)),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # never interrupt the calling operation
```

Either way, add a rule branch in `coordinator/rules.py` once the event type is emitted.

## Running as a macOS LaunchAgent

Create `~/Library/LaunchAgents/com.coordinator.homeops.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.coordinator.homeops</string>
    <key>ProgramArguments</key>
    <array>
        <string>/path/to/uv/tools/homeops-coordinator/bin/homeops-coordinator</string>
    </array>
    <key>KeepAlive</key>
    <true/>
    <key>RunAtLoad</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>30</integer>
    <key>StandardOutPath</key>
    <string>/Users/YOU/.local/share/coordinator/coordinator.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/YOU/.local/share/coordinator/coordinator.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>HOME</key>
        <string>/Users/YOU</string>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
```

Load it:

```bash
launchctl load ~/Library/LaunchAgents/com.coordinator.homeops.plist
```

The daemon restarts automatically if it crashes (`KeepAlive`). `ThrottleInterval: 30` prevents a crash loop from burning CPU.

## Events DB Schema

Path: `~/.local/share/coordinator/events.db`

```sql
CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source       TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    payload      TEXT NOT NULL,       -- JSON
    created_at   TEXT DEFAULT (datetime('now')),
    processed_at TEXT                 -- NULL = pending
);
```

Failed events (exceptions during processing) stay `processed_at = NULL` and are retried on the next poll cycle.

## License

MIT
