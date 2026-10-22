# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Status**: ACTIVE
**Last Updated**: 2026-08-05 (issue #57: the agronomic log - treatments, products, equipment, purchases, mowing_visits, seasonal_tasks - now has a selectable storage backend via the `lawn_log` config block, `sqlite` (default) or `markdown` (external file); previous entry: issue #44, `water_usage_report`'s per-month irrigation cost now comes from the same authoritative `hydrawise.budget.cost_per_minute` as `irrigation_budget`/`irrigation_pace`, not a water-bill regression; issue #42: those two tools plus `et_recommendations`'s ET/cost projections already used the config value)

## Overview

LawnOps is a Python CLI tool for lawn care operations - soil temperature monitoring via Open-Meteo API, application window planning, Hunter Hydrawise irrigation controller management, treatment/product/cost tracking with a selectable storage backend (SQLite by default, or an external markdown file), and coverage/mix calculators. Configuration is stored in `~/.config/lawnops/config.json`; Hydrawise credentials can be set up via `lawnops configure`.

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
lawnops bermuda-check # Report soil temp status for Bermuda green-up

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
lawnops irrigation check                    # Report budget/ET issues

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
lawnops db log-export [--preview]    # Seed lawn_log markdown backend from existing SQLite rows (issue #57)

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
- `lawnops/config.py` - Layered config loader (`~/.config/lawnops/config.json` → env vars). Config is a plain dict passed through the call chain.
- `lawnops/mcp_server.py` - FastMCP server exposing 46 tools (weather, inventory, reports, calculators, irrigation status/control/export/diff/apply, pollen, write operations for mowing/treatments/purchases/equipment). The 16 agronomic-log tools (product/treatment/mowing/equipment/purchase list-add-delete plus `spend_report`) route through `log_store`/`log_compute` (issue #57) rather than `lawnops.db` directly, so they work unchanged against either storage backend; names, signatures, annotations, and JSON shapes are unaffected. Returns structured JSON from domain functions directly - bypasses `cli/display.py` for token efficiency. Config read fresh on every call (no cache); weather data cached with 5-min TTL.
- `lawnops/irrigation_config.py` - Declarative config serializer. `export_config(ctrl, programs)` builds the desired-state dict from live pydrawise objects; `write_yaml(cfg, output_path)` writes it to `~/.config/lawnops/irrigation_state.yaml` by default (or a caller-supplied path). The snapshot is operational home state, never committed to the repo (gitignored). Zone advisory fields (`watering_adjustment_pct`, `cycle_soak`) are exported but marked `advisory_only: true` / `ise_blocked: true` because Hydrawise updateZone* mutations return ISE server-side.
- `lawnops/db/` - SQLite CRUD modules, one per domain entity (observations, treatments, products, purchases, mowing, equipment, irrigation_log, reports, alerts). All use `connection.get_db()` for connections with `sqlite3.Row` factory. `irrigation_analytics.py` handles pace, budget, zone analysis, and ET recommendations using schedule-based projection derived from 90-day run history.
- `lawnops/log_store.py` - Backend-neutral dispatch for the agronomic log (treatments, products, equipment, purchases, mowing_visits, seasonal_tasks): `read_table`/`append_row`/`update_row`/`delete_row` route to whichever backend `lawn_log.backend` selects (issue #57). Config is passed through and read fresh on every call, no caching. Row dicts use the canonical column names in `log_schema.ENTITIES` regardless of backend.
- `lawnops/log_backends/` - `sqlite_backend.py` (default; delegates writes to the existing `lawnops.db` CRUD functions so their business-rule defaults are unchanged) and `markdown_backend.py` (external markdown file, source of truth when configured; fixed `## Heading` table per entity, one file or per-entity files via `lawn_log.markdown.entities`).
- `lawnops/log_schema.py` - Canonical per-entity column order and `defaults` (mirroring the SQLite schema's `DEFAULT`s) shared by both backends, so a value SQLite fills in silently is not left blank under markdown.
- `lawnops/log_compute.py` - Backend-neutral list/report logic (year filtering, sorting, reorder alerts, mowing summary/gap, spend report) over rows from `log_store.read_table()`, so both backends produce identical MCP tool output.
- `lawnops/log_migrate.py` - `export_log(config, preview=False)`, powering `lawnops db log-export`: reads current rows from the sqlite backend and overwrites the markdown backend's sections with them (idempotent), used once when switching `lawn_log.backend` from `sqlite` to `markdown`.

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
- **Irrigation/bermuda checks are deterministic, not LLM-based** (issue #146; see the root CLAUDE.md's "Deterministic checks replace `claude -p` invocations" for the migration story shared with homeops). `lawnops irrigation check` uses `db.evaluate_irrigation_check()`, reusing `irrigation_budget()`'s `budget_status` (qualifies on `"warning"`/`"over"`) and `et_recommendations(year=datetime.now().year)`'s `recommendations` list - the explicit `year` matters because `et_recommendations()` otherwise defaults to *last* year for full historical analysis, and the check wants the most recent elapsed-month data instead. `lawnops bermuda-check` uses `advisory.bermuda_greenup_ready(current_soil_temp, config)`, a one-line comparison against `config["thresholds"].get("bermuda_greenup_soil_temp", 65.0)` - the `.get()` fallback preserves the pre-#146 script's hardcoded 65°F behavior for any install whose config predates this key. Both are fully DB-only (unit-testable with no mocking) and both are read-only reports, per the root CLAUDE.md section.
- **`hydrawise.budget.cost_per_minute` is the authoritative cost-per-minute** (issue #42, extended to `water_usage_report` in #44): `irrigation_budget`, `irrigation_pace`, `water_usage_report`, and the ET/cost projections in `irrigation_analytics.py` all read this config value directly for cost math, and `cpm_source` in their output reads `"config"` whenever it is set to a positive number. Water-bill accounting (rate tiers, service periods, meter readings) lives outside lawnops now - it should no longer be reproduced by regressing a $/minute figure out of imported bills. Calibrate the value yourself from your own water bills (rate x usage against actual logged irrigation runtime for the matching billing period) and update it by hand whenever a new bill lands; there is no automatic recalibration. `_compute_dynamic_cpm()` still computes a weighted CPM from the three most recent qualifying billing months as a diagnostic-only `observed_cpm_from_bills` field (source `"computed"`) - useful for noticing drift between the configured rate and what bills actually show - but it is never used for a cost projection. It only becomes the authoritative value (source `"computed"`, falling further back to a hardcoded `0.12` with source `"default"` if there isn't enough bill history) when `cost_per_minute` is unset, zero, or negative in config - a `<= 0` value is rejected with a `warning` rather than trusted, since it would otherwise silently zero out or invert every downstream cost projection.
- **`water_usage_report`'s per-month cost is `irrigation_minutes * config CPM`, not a bill regression** (issue #44): before this, `db/water_usage.py` derived per-month irrigation cost and `cost_per_minute` by regressing imported water bills (`bill - baseline`, divided by minutes), which produced `$0.00` for months whose bill hadn't posted yet and a per-minute figure that diverged from the config CPM `irrigation_budget` uses. Now every month with logged runtime gets `irrigation_minutes * cpm` and `cost_per_minute == cpm` (via `_compute_dynamic_cpm` in `irrigation_analytics.py`), so `avg_cost_per_minute` is the same constant everywhere. The `water_bill` field is retained as informational context only (baseline calc still uses it). Because `irrigation_analytics.py` already imports helpers from `water_usage.py` at module load, `get_water_usage_report` imports `_compute_dynamic_cpm` lazily inside the function to avoid a circular import; the pre-#44 bill-regression math itself was extracted to `water_usage._bill_regressed_months()`, which `irrigation_analytics._weighted_cpm_from_bills()` now calls directly instead of going through `get_water_usage_report` (calling back into `get_water_usage_report` would recurse into `_compute_dynamic_cpm` -> `_weighted_cpm_from_bills` -> `get_water_usage_report` forever). One side effect: `et_recommendations`'s legacy `months`/`recommendations` list (which flags "expensive" months by comparing per-month `cost_per_minute` to `avg_cost_per_minute`) is now effectively inert, since both values are always the same config constant -- `budget_based_recommendation` (the primary, forward-looking field) is unaffected since it already read the config CPM directly per #42.
- **Scheduling authority is not assumed** (issue #53): lawnops no longer treats the Hydrawise controller as necessarily *the* irrigation scheduler. `irrigation.has_active_program(ctrl, program_zone_nums)` is a pure, offline signal derived only from live controller state (program membership + per-zone suspensions, never hardcoded IPs/entities/dates/thresholds) - active means at least one standard program has zones and at least one of those zones is not currently suspended. `db.irrigation_log.sync_skipped_runs()` gates weather-inferred skip entries on it (returns `(count, active)`; inactive writes zero `irrigation_skips` rows and never reaches `_infer_skip_reason`), and `irrigation_history` gates both the write and the read of stored skips on the same `active` flag, adding `active_program` and a neutral `note` to its output rather than presenting Hydrawise's cloud run log as a complete record. `irrigation_status`'s `_serialize_irrigation_status` adds a `scheduling_note` (composed from live zone/program counts) only when inactive - it is absent entirely when active, so Hydrawise-scheduler output is unchanged. `irrigation_analytics._project_month` also has a DB-only recency guard (`_has_recent_runs`): schedule-based projection degrades to actual-only when no run has landed within `max(period_days * 4, 21)` days - not the raw derived cycle length, which would false-positive on a normal multi-day weather skip streak - so stale pre-retirement rows in `irrigation_runs` don't get extrapolated forward. Hard constraints, non-negotiable: no Prometheus or Home Assistant API as a source of truth or requirement, ever; no hardcoding any deployment specifics; no regression for Hydrawise-as-scheduler users (their controller always reports an active program, so every gate above evaluates to the pre-#53 behavior).
- **Agronomic log storage is selectable** (issue #57): the `lawn_log` config block picks `backend: sqlite` (default; behavior unchanged, zero config needed) or `backend: markdown` (an external markdown file becomes the source of truth for treatments/products/equipment/purchases/mowing_visits/seasonal_tasks). This is opt-in only - with no `lawn_log` config the tool behaves exactly as before. Live/derived data (weather, soil, pollen, Hydrawise irrigation) is unaffected and always SQLite. Markdown backend caveats: it fails loud (never silently falls back to SQLite) when the configured path is unset or its parent directory does not exist; a cell value's `|` is escaped on write and unescaped on read, and a newline in a cell collapses to a space (this table format cannot represent multi-line cells); and row `id` is 1-indexed table position, recomputed fresh on every read, not a stable identifier - a hand-edit or prior delete in the same section can retarget a later update/delete at the wrong row, so treat markdown-backend ids as non-durable across turns. Use `lawnops db log-export` to seed the markdown file(s) from existing SQLite rows when switching backends (idempotent - safe to re-run, each entity's section is fully overwritten).

## Configuration

- `~/.config/lawnops/config.json` - All config: location, thresholds, product rates, Hydrawise credentials, mowing schedule, DB path, etc. Managed by `lawnops configure` for credential fields; edit directly for non-sensitive settings.
- Env vars (`HYDRAWISE_API_KEY`, `HYDRAWISE_USERNAME`, `HYDRAWISE_PASSWORD`) override config file.
- Database: `~/.local/share/lawnops/lawnops.db` - SQLite database (XDG location). Auto-created and initialized on first use; `lawnops db init` is available for explicit initialization but is not required.
- `lawn_log` block (issue #57, optional - omit entirely for unchanged SQLite behavior):
  - `backend`: `sqlite` (default) or `markdown`.
  - `markdown.note`: default file path for every entity not overridden below (`~` expanded).
  - `markdown.entities.<entity>`: route one entity (`treatments`, `products`, `equipment`, `purchases`, `mowing_visits`, `seasonal_tasks`) to its own file instead of `note`.
  - `markdown.section_headings.<entity>`: override the `## Heading` used per entity (defaults to the entity's title-case name).
  - `markdown.date_format`: display format for date columns in the markdown table (rows are still stored/filtered internally as `YYYY-MM-DD`).
  - Config is read fresh per call, same as the rest of lawnops - a `lawn_log` edit takes effect on the next call, no server restart needed.

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
