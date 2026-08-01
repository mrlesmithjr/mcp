# CLAUDE.md

**Last Updated**: 2026-08-01 (issue #39: removed `homeops/reminders.py` and the `mrlesmithjr-mcp-apple-eventkit-tools` dependency; `homeops task escalate` and `homeops utility check-anomaly` are now read-only reports, not Apple Reminders creators)

## Overview

**HomeOps** is a Python CLI + MCP tool for whole-house maintenance management. It tracks recurring tasks, pest control treatments, service providers, and maintenance costs.

**Companion to LawnOps** - LawnOps handles lawn/irrigation/treatments; HomeOps handles everything else (HVAC, gutters, pest control, electrical, plumbing, safety, appliances).

## Architecture

```
homeops/
├── homeops/
│   ├── __init__.py          # Version, re-exports
│   ├── __main__.py          # python -m homeops
│   ├── config.py            # Layered config loader (~/.config/homeops/config.json → env vars)
│   ├── status.py            # Status dashboard aggregation
│   ├── ha.py                # HVAC integration - queries Prometheus (HA's climate metrics exporter), not HA itself (issue #143)
│   ├── ynab_bridge.py       # YNAB budget integration
│   ├── checklists.py        # Seasonal maintenance checklists
│   ├── mcp_server.py        # MCP server for Claude integration
│   ├── cli/
│   │   ├── __init__.py      # Re-export main()
│   │   ├── main.py          # Argparse setup, command routing
│   │   └── display.py       # Terminal output formatting
│   └── db/
│       ├── __init__.py      # Re-export all CRUD
│       ├── connection.py    # get_db_path(), get_db()
│       ├── schema.py        # CREATE TABLE, init_db()
│       ├── tasks.py         # Recurring task CRUD + due date math
│       ├── pest.py          # Pest treatment log
│       ├── providers.py     # Service provider directory
│       ├── appliances.py    # Appliance lifecycle tracking
│       ├── utilities.py     # Utility bill tracking
│       ├── hvac.py          # HVAC history and analytics
│       └── costs.py         # Unified cost ledger + reports
├── pyproject.toml
└── CLAUDE.md
```

**Database**: `~/.local/share/homeops/homeops.db` (SQLite)
**Install**: `uv tool install .` → `homeops` CLI + `homeops-mcp` MCP server
**Credentials**: `homeops configure` (interactive) → `~/.config/homeops/config.json` (600). Written atomically (`_write_config_atomic` in `cli/main.py`: temp file + `chmod 0o600` + `Path.replace()`, issue #71) - same pattern as mail-tools' `gmail_tokens.json`/`sender_rules.json`. As of issue #143, HVAC tools query Prometheus instead of Home Assistant directly - no credentials required, just a reachable `prometheus_url` (default `http://localhost:9091`, no auth). `homeops configure` prompts only for `prometheus_url`; the old HA URL/token prompts and the 1Password-lookup path they used were removed since there's no secret left to collect (code review follow-up on issue #143 - the wizard's first draft still asked for HA credentials that nothing consumed).

## CLI Commands

```bash
# First-time setup
homeops configure        # Interactive setup (Prometheus URL, default already filled in)

# Budget planning (YNAB bridge)
homeops budget overview                     # Comprehensive planning overview
homeops budget sinking                      # Appliance replacement sinking fund plan
homeops budget upcoming                     # Maintenance tasks due within 90 days
homeops budget utilities                    # Utility budget recommendations (avg + 10%)

# Appliance lifecycle
homeops appliance list [--category hvac]    # All appliances with age, warranty, lifespan
homeops appliance add "Name" --category hvac --brand Navien --model NPE-240A --purchased 2021-06 --warranty-end 2031-06 --lifespan 20 --replacement-cost 3500 --location "Utility closet"
homeops appliance expiring                  # Warranties expiring within 12 months
homeops appliance aging                     # Within 2 years of expected end of life

# Utility bills
homeops utility add --date 2026-03 --type electric --amount 285.50 --usage "2,100 kWh"
homeops utility trend water [--months 12]   # Monthly trend for a utility type
homeops utility summary [--year 2026]       # Spending summary by type
homeops utility history [--months 12]       # All bills
homeops utility check-anomaly               # Report bills >20% above trailing baseline avg

# Recurring tasks
homeops task list                    # All tasks with status, next due, days until/overdue
homeops task overdue                 # Only overdue tasks
homeops task add "Name" --interval 90d --category hvac [--notes "..."]
homeops task done "Name" [--cost 25] [--provider "Premier Comfort"] [--notes "..."] [--date YYYY-MM-DD]
homeops task pause "Name"            # Deactivate without deleting
homeops task resume "Name"           # Reactivate
homeops task history "Name"          # Completion log for a specific task
homeops task escalate                # Report safety/60+-day overdue tasks

# Pest control
homeops pest add --date YYYY-MM-DD --area perimeter --product "Cyzmic CS" [--method spray] [--cost N] [--notes "..."]
homeops pest history [--year YYYY]   # Treatment log

# Service providers
homeops provider add "Name" --category gutters [--phone "..."] [--email "..."] [--cost 225] [--notes "..."]
homeops provider list [--category hvac]
homeops provider show "Name"         # Detail view with cost history

# Cost tracking
homeops cost add --date YYYY-MM-DD --category hvac --amount 277 [--provider "..."] [--description "..."]
homeops cost summary [--year YYYY]   # Spending by category
homeops cost history [--year YYYY] [--category hvac]

# Database
homeops db init                      # Explicit initialization (optional - DB is auto-created on first use)
```

## MCP Server

```bash
claude mcp add -s user homeops -- homeops-mcp
```

**Available MCP tools (31):**

Read tools:
- `home_status` - Comprehensive dashboard (overdue, alerts, spending)
- `task_list` - All tasks with status and due dates
- `task_overdue` - Only overdue tasks
- `task_history(task_name)` - Completion log for a task
- `pest_history(year?)` - Pest treatment log
- `provider_list(category?)` - Service provider directory
- `provider_detail(name)` - Provider detail with cost history
- `appliance_list(category?)` - All appliances with age/warranty/lifespan
- `appliance_alerts` - Expiring warranties + aging appliances
- `utility_summary(year?)` - Utility spending by type
- `utility_trend(type, months?)` - Monthly trend for a utility
- `cost_summary(year?)` - Spending by category
- `cost_history(year?, category?)` - Cost line items
- `budget_overview` - Comprehensive budget planning (sinking funds + maintenance + utilities)
- `sinking_fund_plan` - Appliance replacement savings plan
- `hvac_status` - Current HVAC status for all zones
- `hvac_history(hours?)` - HVAC mode/temp change history
- `hvac_trend(zone?, days?)` - Temperature trend over time by zone
- `hvac_mode_distribution(zone?, days?)` - Time spent per HVAC mode
- `hvac_efficiency(months?)` - HVAC efficiency metrics

Write tools:
- `task_done(name, date?, cost?, provider?, notes?)` - Mark task completed
- `task_add(name, category, interval, notes?)` - Add recurring task
- `task_pause(name)` - Pause/deactivate a task
- `task_resume(name)` - Resume a paused task
- `pest_add(date, area, product, method?, notes?, cost?)` - Log pest treatment
- `cost_add(date, category, amount, description, provider?, notes?)` - Log maintenance cost
- `utility_add(date, utility_type, amount, notes?)` - Log utility bill

Delete tools:
- `task_delete(name)` - Delete a recurring task (partial name match)
- `pest_delete(id)` - Delete a pest treatment by ID
- `cost_delete(id)` - Delete a cost entry by ID
- `utility_delete(id)` - Delete a utility bill by ID

## Key Design Decisions

- **Interval stored as days**: `parse_interval("6m")` → 180 days. Months = 30d, years = 365d.
- **Dual-write costs**: `task done --cost` and `pest add --cost` write to both domain table AND unified `costs` table (with `source` column for traceability).
- **Fuzzy name matching**: `task done` and `provider show` use `LIKE %name%` for convenience.
- **next_due is stored**: Computed on each `task done`, not at query time. Enables simple `WHERE next_due <= date('now')` for overdue queries.
- **Utility anomaly check is deterministic, not LLM-based** (issue #146): `homeops utility check-anomaly` replaces the old `com.homeops.utility-anomaly` LaunchAgent's `claude -p` invocation (`utility-anomaly.sh`, now deleted) with a plain Python decision function, `db.evaluate_utility_anomalies()`. For each utility type it pulls 7 months of trend data (6 baseline + the latest bill) via `get_utility_trend(config, type, months=7)`, skips types with fewer than 7 bills on record, and flags the latest bill if it's more than 20% above the average of the PRIOR 6 months - the baseline average deliberately excludes the latest bill itself, since it's the spike candidate being tested, not something to be smoothed by. A zero (or negative) baseline average is skipped rather than flagged, guarding the division and avoiding a false positive off a run of $0 bills. Fully DB-only, so it's unit-testable without any mocking. As of issue #39, this is a read-only report: it prints the anomalies found (type, latest amount vs. baseline, percent over) rather than creating an Apple Reminder - `homeops.reminders` and the `mrlesmithjr-mcp-apple-eventkit-tools` dependency it wrapped were removed entirely, since Reminders is deprecated as a delivery channel (issue #37 already removed the LaunchAgent that would have scheduled this check).
- **Task escalation check is deterministic, not LLM-based** (issue #146): `homeops task escalate` replaces the old `com.homeops.task-escalation` LaunchAgent's `claude -p` invocation (`task-escalation.sh`, now deleted) with `db.evaluate_task_escalation()`, a plain Python decision function over `get_overdue()`'s output. Any `safety`-category task qualifies at any overdue amount; any non-safety task qualifies once it's more than 60 days overdue (`>`, not `>=` - exactly 60 days does not fire). Fully DB-only, so it's unit-testable without any mocking. As of issue #39, this is a read-only report: it prints each qualifying task (name, category, days overdue, reason) rather than creating an Apple Reminder - `homeops.reminders` and the `mrlesmithjr-mcp-apple-eventkit-tools` dependency it wrapped were removed entirely, since Reminders is deprecated as a delivery channel (issue #37 already removed the LaunchAgent that would have scheduled this check).
- **HVAC data comes from Prometheus, not Home Assistant directly** (`ha.py`, issue #143): `hvac_status()`/`hvac_history()` query the Prometheus instance HA already exports climate metrics to (`http://localhost:9091`, no auth), not HA's own REST API - homeops and Home Assistant must never call each other in either direction, and this was the one confirmed violation. Every Prometheus request goes through `_prom_get`, which carries forward the bounded-timeout/retry pattern from the original HA-based version (issue #70): 10s timeout, retries transient failures (timeout, connection error, HTTP error, or a Prometheus-reported query error) up to `MAX_RETRIES` (2) times with linear backoff before giving up - originally mattered both for the on-demand MCP tool and the hourly `com.homeops.hvac-snapshot` LaunchAgent, though issue #37 removed that plist from the shipped plugin, so the retry/timeout logic now only fires on the on-demand MCP tool call path (the equivalent scheduled run, if any, lives in the maintainer's private provisioning). `hvac_status()` issues a handful of instant PromQL queries (mode, current temp, target temp, and one combined query for zone humidity + standalone sensors + outdoor), each with a regex-OR across zone `entity` labels, instead of one call per entity. `hvac_history()` issues range queries per metric type and diffs adjacent samples to reconstruct change events - Prometheus has no discrete per-change event log like HA's `/api/history`, so the 5-minute manual-override heuristic is now bounded to Prometheus's ~60s scrape interval (`SCRAPE_INTERVAL_SECONDS`) instead of exact timestamps. Two parity gaps vs. the old REST-based version, both accepted: `preset`/`fan_mode` are dropped from `hvac_status()`'s return shape (no Prometheus equivalent for preset; fan_mode does have one, but was dropped anyway rather than exposing an asymmetric pair), and `target_high`/`target_low` are always `None` (HA's Prometheus exporter only emits a single-setpoint metric, with no dual-setpoint/heat_cool equivalent at all). `_entity_regex()` doubles every backslash `re.escape()` produces before embedding an entity_id into a PromQL double-quoted string - PromQL applies Go string-escaping before parsing the regex, so a single backslash (e.g. before the `.` in `climate.living_room`) causes a 400 "unknown escape sequence" instead of matching a literal dot.

## Categories

**Tasks**: hvac, plumbing, gutters, pest, electrical, exterior, interior, safety, appliance
**Providers**: hvac, gutters, plumbing, electrical, pest, general, generator, lighting, radon
**Costs**: hvac, gutters, pest, plumbing, electrical, repair, supplies, service

