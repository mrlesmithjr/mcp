# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Status**: ACTIVE
**Last Updated**: 2026-07-26 (issue #146: `lawnops bermuda-check` replaces the old `claude -p`-based `bermuda-greenup-alert.sh` LaunchAgent script; `lawnops irrigation check` similarly replaces `irrigation-check.sh`; removed the fictional `lawnops remind` command from docs, issue #146 Unit 10)

## Overview

LawnOps is a Python CLI tool for lawn care operations - soil temperature monitoring via Open-Meteo API, application window planning, Hunter Hydrawise irrigation controller management, treatment/product/cost tracking in SQLite, coverage/mix calculators, and Apple Reminders integration. Configuration is stored in `~/.config/lawnops/config.json`; Hydrawise credentials can be set up via `lawnops configure`.

## Commands

```bash
# Install
uv tool install --editable .

# First-time credential setup (interactive - detects 1Password)
lawnops configure    # Sets Hydrawise API key, username, password

# Run CLI
lawnops              # Defaults to 'now' (current soil temp)
lawnops now          # Current soil temp + threshold status
lawnops trend        # 14-day soil temp history
lawnops advisory     # Pre-emergent application recommendation
lawnops spray        # 48-hour spray window assessment (mow-buffer aware)
lawnops recommend    # Fertilizer recommendation based on season, soil temp, and history
lawnops bermuda-check # Check soil temp for Bermuda green-up, create a Reminder if ready

# Application planning
lawnops window spray          # Best spray day this week (scores 7 days)
lawnops window granular       # Best granular application day
lawnops window pre-emergent   # Best day (bonuses for post-application rain)

# Calculators
lawnops coverage "Product"           # Bags needed for yard
lawnops coverage "Product" --sqft N  # Override yard size
lawnops mix "Product" --tank 4       # Concentrate per tank load
lawnops mix "Product" --tank 4 --rate northern  # Override rate type

# Irrigation (requires Hydrawise credentials - run 'lawnops configure')
lawnops irrigation status
lawnops irr run <zone> <minutes>
lawnops irr runall <minutes> [--zones 1 3 4]
lawnops irr stop [zone]
lawnops irr suspend <hours> [--zone N]
lawnops irr resume [--zone N]
lawnops irr history [--days 7]
lawnops irrigation export                   # Export live config to YAML (read-only DR snapshot)
lawnops irrigation export --output PATH     # Write to custom path
lawnops irrigation diff                     # Diff live config vs desired-state YAML (exits non-zero on drift)
lawnops irrigation diff --file PATH         # Diff against a specific YAML file
lawnops irrigation apply                    # DRY-RUN: show planned writes without touching controller
lawnops irrigation apply --confirm          # Execute writes to live controller (program-level only)
lawnops irrigation apply --file PATH --confirm  # Apply from a custom YAML file
lawnops irrigation check                    # Check budget/ET issues, create a Reminder if action needed

# Database
lawnops db init                      # Explicit initialization (optional - DB is auto-created on first use)
lawnops db treatment add --date 2026-03-15 --area "front" --product "..." --cost 25
lawnops db treatment list [--year 2026]
lawnops db product list
lawnops db product add "Name" --category pre-emergent --qty 2 --cost 36.97 --source "Store"
lawnops db product update "Name" --qty 1
lawnops db product alerts            # Zero-stock reorder alerts
lawnops db purchase add --date 2026-03-15 --item "..." --cost 30
lawnops db mowing add --date 2026-03-15 --cost 50
lawnops db mowing summary [--year 2026]
lawnops db equipment list
lawnops db report spend [--year 2026] [--category product]
lawnops db sync-irrigation [--days 7]
lawnops db import-obsidian           # One-time import from Obsidian task list
lawnops db import-ynab [--year 2026] [--preview]

# Run as module
python -m lawnops

# MCP server
lawnops-mcp              # Run MCP server over stdio
python -m lawnops.mcp_server  # Alternative
```

## Architecture

The app follows a layered pattern: **CLI (argparse) → domain logic → data layer**.

- `lawnops/cli/main.py` - Argparse setup and command routing. All commands route through `main()`, with `_handle_db()` and `_handle_irrigation()` as sub-routers.
- `lawnops/cli/display.py` - All terminal output formatting. Receives data, prints it. No business logic. Uses `_DASH = "\u2014"` constant for em dashes in f-strings (Python 3.11 backslash restriction).
- `lawnops/weather.py` - Open-Meteo API client. Returns raw JSON, aggregates hourly data into daily summaries.
- `lawnops/advisory.py` - Pre-emergent timing logic (SAFE/WARNING/URGENT/LATE levels based on consecutive days above soil temp threshold) and spray window assessment (GO/NO-GO based on air temp, wind, rain forecast). Mow-buffer aware.
- `lawnops/mowing.py` - Mowing schedule date math: next mow dates, no-mow buffer windows, buffer day checks. Reads `mowing.schedule_day` and `mowing.no_mow_buffer_days` from config.
- `lawnops/recommend.py` - Fertilizer recommendation engine. Bermuda grass seasonal calendar maps month + soil temp to fertilizer phase, actions, and product categories. Cross-references treatment history and product inventory.
- `lawnops/window.py` - Application window scoring. Evaluates next 7 forecast days for spray/granular/pre-emergent suitability. Returns scored day list with GO/CAUTION/NO-GO status.
- `lawnops/coverage.py` - Bag/coverage calculator. Fuzzy-matches product names against `product_rates` config.
- `lawnops/mixrate.py` - Spray concentrate mix rate calculator. Southern/northern rate support.
- `lawnops/irrigation.py` - Hydrawise controller integration via `pydrawise` library. Async internals wrapped with `asyncio.run()` for sync public API. All functions raise `RuntimeError` on failure.
- `lawnops/reminders.py` - Apple Reminders search-then-create dedup helper, wraps `apple_eventkit_tools.reminders.RemindersManager` directly (no MCP round-trip, no `osascript`; issue #146). A separate small per-package copy of the same pattern in `homeops/reminders.py`, not a shared import - promoting to a shared location is deferred until a 3rd/4th real consumer shows an actual duplication problem worth solving.
- `lawnops/config.py` - Layered config loader (`~/.config/lawnops/config.json` → env vars). Config is a plain dict passed through the call chain.
- `lawnops/mcp_server.py` - FastMCP server exposing 46 tools (weather, inventory, reports, calculators, irrigation status/control/export/diff/apply, pollen, write operations for mowing/treatments/purchases/equipment). Returns structured JSON from domain functions directly - bypasses `cli/display.py` for token efficiency. Config read fresh on every call (no cache); weather data cached with 5-min TTL.
- `lawnops/irrigation_config.py` - Declarative config serializer. `export_config(ctrl, programs)` builds the desired-state dict from live pydrawise objects; `write_yaml(cfg, output_path)` writes it to `~/.config/lawnops/irrigation_state.yaml` by default (or a caller-supplied path). The snapshot is operational home state, never committed to the repo (gitignored). Zone advisory fields (`watering_adjustment_pct`, `cycle_soak`) are exported but marked `advisory_only: true` / `ise_blocked: true` because Hydrawise updateZone* mutations return ISE server-side.
- `lawnops/db/` - SQLite CRUD modules, one per domain entity (observations, treatments, products, purchases, mowing, equipment, irrigation_log, reports, alerts). All use `connection.get_db()` for connections with `sqlite3.Row` factory. `irrigation_analytics.py` handles pace, budget, zone analysis, and ET recommendations using schedule-based projection derived from 90-day run history.

## Key Design Decisions

- **No ORM** - raw SQLite with `sqlite3.Row` for simplicity.
- **Config as dict** - loaded fresh from disk on every MCP tool call (no module-level cache). This ensures `irrigation_budget_update` changes are visible immediately without a server restart.
- **Irrigation is async-wrapped** - `pydrawise` is async; each public function wraps its coroutine with `asyncio.run()`.
- **Hydrawise client is cached, config is not** (issue #75) - `get_client()` still reads `load_config()` fresh on every call (per the config-caching decision above), but the resulting Hydrawise/Auth client is cached at module level keyed by `(username, password)` and reused across calls, instead of rebuilt from scratch every time. pydrawise's `Auth` already caches/refreshes its own OAuth token internally; discarding the `Auth` instance after every call meant that internal caching never got a chance to help, so every irrigation tool invocation paid a full password-grant round trip. The cached client is rebuilt automatically if credentials change between calls.
- **Auto-logging** - Weather commands auto-log daily observations to SQLite. Irrigation commands auto-log runs. Controlled by `database.auto_log` config and `--no-log` flag.
- **Display is separated from logic** - `cli/display.py` only formats/prints; domain modules return data.
- **Business logic never prints or exits** - Modules raise `RuntimeError`; only `cli/main.py` catches and calls `sys.exit()`.
- **Product rates are config-driven** - `product_rates` section in config.yaml defines coverage, mix ratios, and notes per product. Fuzzy substring matching for CLI lookups.
- **Mow-buffer awareness** - Spray advisory and window finder respect configurable no-mow buffer days around mowing schedule.
- **Irrigation check is deterministic, not LLM-based** (issue #146): `lawnops irrigation check` replaces the old `com.lawnops.irrigation-check` LaunchAgent's `claude -p` invocation (`irrigation-check.sh`, now deleted) with `db.evaluate_irrigation_check()`, a plain Python decision function that reuses `irrigation_budget()`'s `budget_status` (qualifies on `"warning"`/`"over"`, no threshold math reimplemented) and `et_recommendations()`'s `recommendations` list. `et_recommendations()` is inherently backward-looking - it analyzes elapsed months' actual water-bill data, and its own default `year` parameter defaults to *last* year for full-year historical analysis - so the check calls it explicitly with `year=datetime.now().year` to get the most recent elapsed-month data actually available, rather than inventing new forecasting logic. Fully DB-only, so it's unit-testable without mocking Reminders. Uses the shared `create_reminder_if_missing()` dedup pattern with a deliberate split: the reminder *title* is dynamic (e.g. `"Irrigation: action needed: budget + ET"`) but the dedup `search_query` is always the stable prefix `"Irrigation: action needed"` - a dynamic full-title search would never match a prior run whose specific issue summary differed, defeating the dedup entirely. At the time, the plist called the venv binary directly instead of a bash wrapper around an LLM prompt; issue #37 removed this and every other lawnops LaunchAgent plist from the shipped plugin, so `lawnops irrigation check` no longer ships a scheduled trigger here - it now runs only on demand or via the maintainer's private provisioning.
- **Bermuda green-up check is deterministic, not LLM-based** (issue #146, final slice): `lawnops bermuda-check` replaces the old `com.lawnops.bermuda-greenup` LaunchAgent's `claude -p` invocation (`bermuda-greenup-alert.sh`, now deleted) with `advisory.bermuda_greenup_ready(current_soil_temp, config)`, a one-line pure comparison against `config["thresholds"].get("bermuda_greenup_soil_temp", 65.0)`. The `.get()` with a literal `65.0` fallback (rather than a bare subscript) matters because every install predating this change has a `~/.config/lawnops/config.json` with no such key yet - the fallback preserves the old script's hardcoded 65°F behavior with zero required action, while `config.example.yaml` documents the new key for anyone who wants to tune it. Uses the same `create_reminder_if_missing()` dedup pattern as irrigation-check, with a fixed (non-dynamic) notes checklist. This was the last of the 4 LaunchAgent scripts (#146) rebuilt this way. Issue #146's Unit 10 doc consistency pass is now closed: a `lawnops remind` command was documented in this file's Commands section and in README.md (and a matching `homeops remind` in the sibling package) but was never actually implemented - `reminders.py` only ever exposed the internal `create_reminder_if_missing()` dedup helper used by the LaunchAgent checks, not a user-facing create/list command. The fictional command block was removed from both packages' CLAUDE.md and README.md rather than backfilled, since no design intent for a standalone reminder-creation CLI was ever recorded.

## Configuration

- `~/.config/lawnops/config.json` - All config: location, thresholds, product rates, Hydrawise credentials, mowing schedule, DB path, etc. Managed by `lawnops configure` for credential fields; edit directly for non-sensitive settings.
- Env vars (`HYDRAWISE_API_KEY`, `HYDRAWISE_USERNAME`, `HYDRAWISE_PASSWORD`) override config file.
- Database: `~/.local/share/lawnops/lawnops.db` - SQLite database (XDG location). Auto-created and initialized on first use; `lawnops db init` is available for explicit initialization but is not required.

## MCP Server

LawnOps exposes 46 tools via MCP, registered at **user scope** so they're available from any Claude Code session (Obsidian planning, YNAB budget review, etc.):

| Category | Tools |
|----------|-------|
| Weather | `soil_temp_now`, `soil_temp_trend`, `pre_emergent_advisory`, `spray_advisory`, `fertilizer_recommendation` |
| Pollen | `pollen_now`, `pollen_trend` |
| Window | `application_window(app_type)` |
| Inventory | `product_list`, `product_add`, `product_update`, `product_delete`, `product_alerts`, `treatment_list`, `mowing_summary`, `equipment_list` |
| Write | `mowing_add`, `mowing_delete`, `treatment_add`, `treatment_delete`, `purchase_add`, `purchase_delete`, `equipment_add`, `equipment_delete` |
| Reports | `spend_report(year?, category?)` |
| Calculators | `coverage_calculator(product, sqft?)`, `mix_calculator(product, tank?, rate?)` |
| Irrigation | `irrigation_status`, `irrigation_history`, `irrigation_budget`, `irrigation_budget_update`, `irrigation_pace`, `irrigation_run_zone`, `irrigation_run_all`, `irrigation_stop`, `irrigation_suspend`, `irrigation_resume`, `irrigation_program_list`, `irrigation_program_update`, `irrigation_export`, `irrigation_diff`, `irrigation_apply`, `irrigation_zone_update` |
| Water & Efficiency | `water_usage_report`, `et_recommendations`, `zone_analysis` |

### Irrigation Config (Declarative, refs #12)

`irrigation_export` / `lawnops irrigation export` serializes live controller state to `~/.config/lawnops/irrigation_state.yaml` by default (override with `--output`). Never committed to the repo.
Schema v1 covers programs (name, type, day_pattern, period_days, start_times, zones + run_duration, 12-month seasonal_adjustment_factors, predictive_watering condition IDs) and a zones_catalog with per-zone advisory fields.
Zone-level writes (watering_adjustment_pct, cycle_soak) are captured but marked `advisory_only: true` / `ise_blocked: true` -- Hydrawise updateZone* mutations return ISE; they are never submitted by apply.
Phase 2 (diff): `irrigation_diff` MCP tool + `lawnops irrigation diff` CLI.
Phase 3 (apply): `irrigation_apply` MCP tool + `lawnops irrigation apply [--file PATH] [--confirm]` CLI. Default is dry-run. Only program-level fields writable via `update_program` are applied; zone-level and non-writable program fields (name, day_pattern, program_type, ignore_rain_sensor) are reported as SKIPPED. The apply path never calls run/stop/suspend/resume.

Registration: `claude mcp add -s user lawnops -- lawnops-mcp`

## Claude Commands

Slash commands in `.claude/commands/`:

| Command | Purpose |
|---------|---------|
| `/weekly-lawn-check` | Soil temp + spray window + product alerts + application windows |
| `/seasonal-review` | Treatment history + spending + recommendation + inventory |
| `/application-day` | Window finder (all types) + spray advisory + optional coverage calc |

## Dependencies

- `requests` - Open-Meteo API calls
- `pyyaml` - Config loading
- `pydrawise>=2026.4.0,<2027` - Hunter Hydrawise GraphQL v2 API client (async); pinned to guard against breaking mutation schema changes
- `mcp` - MCP server framework (regular dependency, included in a plain install)
