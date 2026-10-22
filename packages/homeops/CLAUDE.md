# CLAUDE.md

**Last Updated**: 2026-08-05 (issue #58: tasks/task_log/pest_treatments/costs/utility_bills/providers/appliances now have a selectable storage backend via the `home_log` config block, `sqlite` (default) or `markdown` (external file), mirroring lawnops #57; previous entry: issue #39, removed `homeops/reminders.py` and the `mrlesmithjr-mcp-apple-eventkit-tools` dependency; `homeops task escalate` and `homeops utility check-anomaly` are now read-only reports, not Apple Reminders creators)

## Overview

**HomeOps** is a Python CLI + MCP tool for whole-house maintenance management. It tracks recurring tasks, pest control treatments, service providers, and maintenance costs, with a selectable storage backend (SQLite by default, or an external markdown file) for that self-generated data.

**Companion to LawnOps** - LawnOps handles lawn/irrigation/treatments; HomeOps handles everything else (HVAC, gutters, pest control, electrical, plumbing, safety, appliances).

## Architecture

```
homeops/
├── homeops/
│   ├── __init__.py          # Version, re-exports
│   ├── __main__.py          # python -m homeops
│   ├── config.py            # Layered config loader (~/.config/homeops/config.json → env vars)
│   ├── status.py            # Status dashboard aggregation (routed through log_store/log_compute, issue #58)
│   ├── ha.py                # HVAC integration - queries Prometheus (HA's climate metrics exporter), not HA itself (issue #143)
│   ├── ynab_bridge.py       # YNAB budget integration (routed through log_store/log_compute, issue #58)
│   ├── checklists.py        # Seasonal maintenance checklists (CLI-only; still writes tasks via raw SQL, unrewired - see issue #58 notes below)
│   ├── log_store.py         # Backend-neutral home_log dispatch (issue #58): read_table/append_row/update_row/delete_row
│   ├── log_schema.py        # Canonical per-entity column order + defaults, shared by both backends
│   ├── log_compute.py       # Backend-neutral list/report logic over home_log rows (mirrors db/ shaping)
│   ├── log_migrate.py       # export_log(): seeds the markdown backend from existing SQLite rows
│   ├── log_backends/
│   │   ├── sqlite_backend.py    # Default backend; delegates to db/ CRUD where side-effect-free
│   │   └── markdown_backend.py  # External markdown file, source of truth when configured
│   ├── atomic_io.py         # Atomic file writes for the markdown backend (temp file + fsync + os.replace)
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
homeops db log-export [--preview]    # Seed home_log markdown backend from existing SQLite rows (issue #58)
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
- **Utility anomaly / task escalation checks are deterministic, not LLM-based** (issue #146; see the root CLAUDE.md's "Deterministic checks replace `claude -p` invocations" for the migration story shared with lawnops). `homeops utility check-anomaly` uses `db.evaluate_utility_anomalies()`: for each utility type it pulls 7 months of trend data via `get_utility_trend(config, type, months=7)`, skips types with fewer than 7 bills on record, and flags the latest bill if it's more than 20% above the average of the PRIOR 6 months (the baseline deliberately excludes the latest bill itself, since it's the spike candidate, not something to be smoothed by; a zero/negative baseline is skipped rather than flagged, to avoid a false positive off a run of $0 bills). `homeops task escalate` uses `db.evaluate_task_escalation()` over `get_overdue()`'s output: any `safety`-category task qualifies at any overdue amount, and any other task qualifies once it's more than 60 days overdue (`>`, not `>=`). Both are fully DB-only (unit-testable with no mocking) and both are read-only reports, per the root CLAUDE.md section.
- **HVAC data comes from Prometheus, not Home Assistant directly** (`ha.py`, issue #143): `hvac_status()`/`hvac_history()` query the Prometheus instance HA already exports climate metrics to (`http://localhost:9091`, no auth), not HA's own REST API - homeops and Home Assistant must never call each other in either direction, and this was the one confirmed violation. Every Prometheus request goes through `_prom_get`, which carries forward the bounded-timeout/retry pattern from the original HA-based version (issue #70): 10s timeout, retries transient failures (timeout, connection error, HTTP error, or a Prometheus-reported query error) up to `MAX_RETRIES` (2) times with linear backoff before giving up - originally mattered both for the on-demand MCP tool and the hourly `com.homeops.hvac-snapshot` LaunchAgent, though issue #37 removed that plist from the shipped plugin, so the retry/timeout logic now only fires on the on-demand MCP tool call path (the equivalent scheduled run, if any, lives in the maintainer's private provisioning). `hvac_status()` issues a handful of instant PromQL queries (mode, current temp, target temp, and one combined query for zone humidity + standalone sensors + outdoor), each with a regex-OR across zone `entity` labels, instead of one call per entity. `hvac_history()` issues range queries per metric type and diffs adjacent samples to reconstruct change events - Prometheus has no discrete per-change event log like HA's `/api/history`, so the 5-minute manual-override heuristic is now bounded to Prometheus's ~60s scrape interval (`SCRAPE_INTERVAL_SECONDS`) instead of exact timestamps. Two parity gaps vs. the old REST-based version, both accepted: `preset`/`fan_mode` are dropped from `hvac_status()`'s return shape (no Prometheus equivalent for preset; fan_mode does have one, but was dropped anyway rather than exposing an asymmetric pair), and `target_high`/`target_low` are always `None` (HA's Prometheus exporter only emits a single-setpoint metric, with no dual-setpoint/heat_cool equivalent at all). `_entity_regex()` doubles every backslash `re.escape()` produces before embedding an entity_id into a PromQL double-quoted string - PromQL applies Go string-escaping before parsing the regex, so a single backslash (e.g. before the `.` in `climate.living_room`) causes a 400 "unknown escape sequence" instead of matching a literal dot.
- **Home log storage is selectable** (issue #58, mirrors lawnops #57): the `home_log` config block picks `backend: sqlite` (default; behavior unchanged, zero config needed) or `backend: markdown` (an external markdown file becomes the source of truth for tasks/task_log/pest_treatments/costs/utility_bills/providers/appliances). Opt-in only - no `home_log` config means unchanged sqlite behavior. HVAC (`hvac_snapshots`) is always sqlite, a live cache of Prometheus, untouched by this. `status.py` (`home_status`) and `ynab_bridge.py` (`budget_overview`, `sinking_fund_plan`, plus the CLI-shared `upcoming_maintenance_costs`/`utility_budget_recommendation`) are both routed through `log_store`/`log_compute` too, not just `mcp_server.py`'s per-entity tools, since they compose sinking-fund/upcoming-maintenance data that must reflect the active backend for `sinking_fund_plan` to produce equal results across backends - this is a deliberate deviation from a literal reading of the issue's own acceptance-list bullet "no diff to ynab_bridge.py" (inherited boilerplate from the lawnops #57 spec template, which has no ynab_bridge.py-equivalent module to conflict with). `checklists.py` (`homeops checklist load`) and the CLI's own `task`/`pest`/`provider`/`cost`/`utility`/`appliance` subcommands are intentionally NOT rewired - same precedent as lawnops #57, where only the MCP tool layer (plus the composition modules two backends must agree on) goes through the abstraction and the CLI keeps writing SQLite directly; `homeops db log-export` reads FROM sqlite regardless. `pest_add`/`utility_add`'s dual-write into the unified `costs` ledger, and `utility_add`'s type validation, are orchestrated once in `mcp_server.py` (not inside `SqliteBackend`) so both backends get the same `costs` row instead of sqlite double-writing it - see `log_backends/sqlite_backend.py`'s module docstring. Markdown backend caveats (fails loud on a bad path, `|` escaping, non-durable row ids) are identical to lawnops' - see lawnops/CLAUDE.md's "Agronomic log storage is selectable" bullet rather than re-deriving them here. `utility_bills.bill_date` (YYYY-MM) and `appliances.purchase_date`/`warranty_end` (YYYY-MM-DD or YYYY-MM) are excluded from the shared `date_format` machinery and stored as plain text, since they don't fit a single full-date format. Every rewired read tool's output key set matches its pre-#58 direct-SQL shape exactly, on the DEFAULT sqlite backend, byte-for-byte (issue #58 code review finding CRITICAL) - `created_at` (all entities), `costs.source_id`, and `task_log`/`task_history`'s `task_id` are sqlite-populated **passthrough** metadata columns (`log_schema.EntitySchema.passthrough`): `SqliteBackend.read_table` emits them with their real value, `MarkdownBackend.read_table` emits the same keys with value `None`, and neither is ever added as a column in the markdown table itself. `task_done`'s dual-written costs row also sets `source_id` to the newly-appended task_log row's own id on sqlite (mirroring pre-#58 `mark_done`'s `last_insert_rowid()`); `SqliteBackend.append_row("task_log", ...)` surfaces that id back to the caller for this purpose, and it is `None` under markdown (append_row there has no stable id to return). Use `homeops db log-export` to seed the markdown file(s) from existing sqlite rows when switching backends (idempotent).

## Configuration

- `~/.config/homeops/config.json` - Prometheus URL, database path, categories. Managed by `homeops configure` for the Prometheus URL; edit directly for other settings.
- `home_log` block (issue #58, optional - omit entirely for unchanged sqlite behavior):
  - `backend`: `sqlite` (default) or `markdown`.
  - `markdown.note`: default file path for every entity not overridden below (`~` expanded).
  - `markdown.entities.<entity>`: route one entity (`tasks`, `task_log`, `pest_treatments`, `costs`, `utility_bills`, `providers`, `appliances`) to its own file instead of `note`.
  - `markdown.section_headings.<entity>`: override the `## Heading` used per entity (defaults to the entity's title-case name).
  - `markdown.date_format`: display format for full-date columns (`last_done`/`next_due`/`date`); `utility_bills.bill_date` and `appliances.purchase_date`/`warranty_end` are unaffected (stored as plain text).
  - Config is read fresh per call for `log_store`/`log_compute`; `mcp_server._config()` itself still caches the loaded config once per server lifetime (unchanged pre-#58 behavior) - a `home_log` edit takes effect on the next server restart, same as any other config change.

## Categories

**Tasks**: hvac, plumbing, gutters, pest, electrical, exterior, interior, safety, appliance
**Providers**: hvac, gutters, plumbing, electrical, pest, general, generator, lighting, radon
**Costs**: hvac, gutters, pest, plumbing, electrical, repair, supplies, service

