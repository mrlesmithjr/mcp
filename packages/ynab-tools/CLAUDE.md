# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Status**: ACTIVE
**Last Updated**: 2026-05-31

## Overview

CLI toolkit for syncing YNAB (You Need A Budget) data to a local SQLite database and performing payee management, transaction categorization, net worth tracking, and brokerage data import. Talks to the YNAB API v1, stores everything in `ynab.db`.

## Documentation

User-facing reference lives in `docs/`. Update these when code changes affect user-visible behavior.

| File | Contents |
|------|----------|
| `docs/getting-started.md` | Step-by-step onboarding: install, credentials, first sync, MCP setup |
| `docs/configuration.md` | Full env var reference, config precedence, all optional settings |
| `docs/commands.md` | Complete CLI command reference |
| `docs/workflows.md` | Paycheck day, month-end, payee cleanup, and sync workflow diagrams |
| `docs/mcp-server.md` | MCP setup and all 47 tool descriptions |
| `docs/dashboard.md` | Dashboard install, service management, calibration logic |
| `docs/database.md` | Schema, ERD, table reference, data files |

## After-Code-Change Checklist (MANDATORY)

After any session that modifies Python source or frontend (TypeScript/React) files, you MUST reinstall and restart before reporting the work as done. The dashboard LaunchAgent runs the installed snapshot - source edits are invisible to it until reinstalled.

**Python-only changes** (no frontend files touched):
```bash
cd packages/ynab-tools
uv tool install --editable ".[dashboard]"
ynab dashboard restart   # or uninstall + install if restart fails
```

**Frontend changes** (any `.tsx`, `.ts`, `.css` under `ynab_tools/dashboard/frontend/src`):
```bash
cd packages/ynab-tools/ynab_tools/dashboard/frontend
npm run build
cd packages/ynab-tools
uv tool install --editable ".[dashboard]"
ynab dashboard restart   # or uninstall + install if restart fails
```

If `ynab dashboard restart` exits with an I/O error, fall back to:
```bash
ynab dashboard uninstall && sleep 2 && ynab dashboard install
```

Confirm with `ynab dashboard status` - look for `Status: running`.

This applies even when the dashboard is not the focus of the change. Any reinstall also updates the `ynab` CLI and `ynab-mcp` server binaries.

**Why skipping the restart is dangerous, not just stale**: `uv tool install --editable` resolves an interpreter version itself (no `--python` pin), so a reinstall can silently land on a different Python minor version than the one currently running (e.g. 3.11 -> 3.12) and replace `venv/lib/python<old>/site-packages` on disk. A dashboard process already running against the old layout keeps working for routes that don't trigger lazy imports, then throws `ModuleNotFoundError: No module named 'anyio._backends'` (or similar) on the first request that does, because anyio imports its backend submodule lazily on first threadpool use rather than at startup. The process looks healthy (PID alive, port open) but returns 500 on every real request. The venv itself is not broken in this case - only the already-running process is stale. Fix is `ynab dashboard restart`, never a venv rebuild in place (issue #124).

## Setup & Commands

Full command reference (every subcommand, flag, and option): [`docs/commands.md`](docs/commands.md).
The handful below cover day-to-day use.

```bash
# Install (base only)
uv tool install .

# Install with dashboard - ALWAYS use this form when the dashboard is in use.
# Must build the frontend first or the dashboard will serve a blank page.
cd ynab_tools/dashboard/frontend && npm run build && cd -
uv tool install --editable ".[dashboard]"

# First-time credential setup (interactive - detects 1Password)
ynab configure       # Saves to ~/.config/ynab-tools/config.json (600)
ynab plans           # List all YNAB budget plans (use during setup to find plan ID)

# Credentials can also be set via env vars (override config file)
# YNAB_ACCESS_TOKEN, YNAB_PLAN_ID (also accepts YNAB_BUDGET_ID)

# Most-used commands - see docs/commands.md for payee/category/reconcile/import/audit/etc.
ynab sync                                       # Delta sync (uses server_knowledge)
ynab budget                                     # RTA, overspent, near-limit, underfunded, health ratios
ynab spending                                   # Current month by category vs budget targets
ynab recent                                     # Last 25 transactions (split-aware)
ynab unapproved                                 # List unapproved transactions (API-verified)
ynab fund "Groceries" 850                       # Set category budget to $850
ynab paycheck-funding --apply --through-tier 2  # Apply tier-based funding plan (safe default)
ynab net-worth                                  # Take net worth snapshot

# Web dashboard (requires dashboard extra: uv tool install ".[dashboard]")
ynab dashboard install    # Install as macOS LaunchAgent (auto-syncs hourly)
ynab dashboard status     # Running/stopped, URL, recent log lines
ynab dashboard restart    # Restart after upgrades
```

## Architecture

**Single entry point**: `ynab_tools/cli.py:main` - argparse-based CLI registered as `ynab` console script. Uses lazy imports for each command to keep startup fast.

**Key modules**:

| Module | Role |
|--------|------|
| `client.py` | `YNABClient` - API wrapper using `/plans/` paths with rate limiting (200 req/hr) and 429 retry |
| `config.py` | Layered config loader (`~/.config/ynab-tools/config.json` → env vars), path constants (`DB_PATH`, `RULES_FILE`, etc.) |
| `db.py` | SQLite schema, `get_connection()`, query helpers |
| `sync.py` | Sync via plan export (1 API call for delta) or multi-call (for `--full --months N`). Uses `server_knowledge` for delta tracking. Split transaction subtransactions always DELETE before the insert loop; a DELETE of 0 rows is a no-op, so unchanged transactions in a delta sync are unaffected. This unconditional delete also handles un-split transactions (empty `subtransactions: []`) that would otherwise leave orphaned rows (issue #28). |
| `stats.py` | Shared statistics engine (spending_stats, recommend_target, round_up_5) and anomaly scoring (zscore_vs_history, category_zscore, anomaly_label, etc). Used by CLI and dashboard. |
| `dashboard/api/_anomaly.py` | Shared anomaly helper for dashboard endpoints: re-exports `anomaly_label`, `anomaly_likely_one_time`, `category_zscore`, `category_zscore_by_name` from `stats.py`. CLI reports import directly from `stats`. |
| `mcp_server.py` | FastMCP server exposing 47 tools via stdio transport. Uses `_capture()` to redirect report stdout → string. |
| `dashboard/` | FastAPI backend + React/Vite frontend. Entry point: `ynab-dashboard`. API docs at `/api/docs`. Background auto-sync loop via asyncio lifespan (`--sync-interval`). Optional HTTP Basic auth via `DASHBOARD_PASSWORD` applied as a global FastAPI dependency (all routes; `/assets` static mount excluded). |
| `dashboard/api/admin.py` | Admin validation data endpoint. `get_validation_data()` returns `category_group_map: dict[str, str]` mapping each category name to its parent group name; used by the frontend to suppress redundant per-category suggestions when the group is already covered. |
| `dashboard/api/home_spending.py` | Home spending by category. `_spending_for_month()` uses `budget_categories.activity` (YNAB server-computed) rather than `transactions LEFT JOIN subtransactions` to avoid overcounting. The root sync bug (YNAB regenerating subtransaction IDs on split edits causing duplicate rows) is fixed in issue #27, but this query continues to use `budget_categories.activity` for resilience. |
| `dashboard/api/spending_pace.py` | Intra-month spending pace and overspend detection. Positive activity (refunds) not flagged as overspent. pct_elapsed/pct_used are 0-100 percentages. Trailing-average sub-query omits hidden=0 (historical); main current-month query retains it. |
| `dashboard/api/sinking_funds.py` | Dashboard sinking-fund goal progress. Applies `_is_bonus_funded()` and `_is_excluded()` before returning results so only BONUS-pot goal-bearing categories are included. |
| `dashboard/api/overspend_plan.py` | Waterfall donor and coverage-gap calculation. `_get_donor_categories` applies `_is_excluded()` and `_is_bonus_funded()` to exclude bonus-pot categories from the donor list. |
| `payees/` | Audit mismatches, normalize duplicates, find orphans, backup/restore |
| `categories/classifier.py` | Auto-categorize using `ynab_tools/data/category_definitions.json` |
| `reports/net_worth.py` | Net worth snapshots stored in DB |
| `reports/budget.py` | Budget check: RTA, overspent, near-limit, underfunded, health ratios, overspend classification |
| `reports/spending.py` | Spending analysis by category vs budget, category trends, fixed/discretionary breakdown |
| `reports/summary.py` | Monthly income vs spending vs net overview |
| `reports/month_end.py` | Consolidated month-end closeout report |
| `reports/debt.py` | Debt account balances, payment tracking, payoff estimates |
| `reports/income.py` | Income breakdown: regular pay, bonuses, YTD comparison |
| `reports/paycheck.py` | Paycheck forecast with bonus detection and projections |
| `reports/paycheck_breakdown.py` | Budget breakdown: classify categories as regular-pot vs bonus-pot funded |
| `reports/paycheck_funding.py` | Paycheck funding plan: prioritized tier-based category funding from RTA. Applies `_is_excluded()` and `_is_bonus_funded()` to all tiers; uses `seen_ids` set to deduplicate categories across tiers so a category cannot appear in multiple tiers. |
| `reports/bonus_split.py` | Split bonus paycheck: move bonus portion to Holding: Next Month before paycheck funding |
| `reports/two_pot_report.py` | Two-pot compliance CLI report: `run_two_pot_report()` plus helpers `_prior_month()`, `_get_paychecks()`, `_holding_balance()`. Reports structural backwards metric, Holding delta, and workflow gap per bonus month. Workflow gap queries `money_movements` (all budget moves, including YNAB app moves). |
| `reports/subscriptions.py` | Recurring subscription detection and cost analysis |
| `reports/funding.py` | Category funding: set/adjust budgets, fund goals, spending-based recommendations |
| `reports/transactions.py` | Recent transactions, large expenses, sinking fund status, transaction CRUD, unapproved/approve |
| `reports/planned.py` | Planned expenses: CRUD, funding gap calculation, budget integration |
| `reports/categories.py` | Category management: create groups/categories, set/clear goals |
| `reports/audit_log.py` | Audit log viewer for all ynab-tools actions |
| `reports/retirement.py` | Retirement account balances, contributions, growth projections |
| `dashboard/api/net_worth.py` | `/api/net-worth` - current NW, ex-mortgage NW, MoM/YoY deltas, 24-month history, asset/liability breakdown. Ex-mortgage history line uses off-budget debt as proxy (approximate); current-month breakdown is exact. |
| `reports/reconcile.py` | Account reconciliation against real-world balances |
| `importers/brokerage.py` | Convert brokerage CSV to YNAB import format |
| `importers/fidelity.py` | Fidelity portfolio positions import and reconciliation |

**Data flow**: YNAB API → `sync.py` → SQLite → payee/category tools read from DB, write back via `YNABClient`.

**User data directory**: DB and backups live in `~/.local/share/ynab-tools/` by default, overridable via `YNAB_DATA_DIR` env var. Resolved at import time in `config.py:_get_data_dir()`.

**Delta sync**: Default sync uses the plan export endpoint (single API call) with a unified `server_knowledge` token. Falls back to multi-call sync (per-endpoint knowledge for accounts, transactions, payees) when using `--full --months N` to limit history. `--full` clears sync state.

**YNAB amounts**: Stored as milliunits in the API (1 dollar = 1000). Converted to dollars via `milliunits_to_dollars()` during sync.

## Data Files

| File | Purpose |
|------|---------|
| `ynab_tools/data/payee_rules.json` | Regex-based import pattern → canonical payee/category mappings |
| `ynab_tools/data/category_definitions.example.json` | Template of category definitions: keywords, typical payees, and disambiguation rules for multi-category stores. Copy to `category_definitions.json` and fill in your own payees - that copy is gitignored, since the merchants you list record where you actually shop |
| `ynab_tools/data/queries.sql` | Ad-hoc SQL reference queries |

## Important Patterns

> Implementation gotchas, SQL invariants, and edge cases: see `docs/implementation-notes.md`.

## Lookback Window Standards

All historical queries in ynab-tools use one of four lookback windows. Each window is sized to the half-life of the pattern it detects. Do not change a window without understanding the rationale below.

| Window | Used In | Why |
|--------|---------|-----|
| 3 months | Tier 4 daily spending detection (`paycheck_funding.py:192`) | Immediate pattern recognition. Daily spending patterns shift quickly; 3 months ensures auto-bridging reflects current behavior, not stale history. |
| 6 months | Tier 3 monthly bill detection (`paycheck_funding.py:312`), `spending.py`, `debt.py`, `calibration.py` | Medium cadence. Captures seasonal utility variance, two full billing cycles for monthly bills, and covers most subscription changes without including prior-year lifestyle baselines. |
| 12 months | Income detection (`paycheck_funding.py:52`), `bonus_split.py` payee detection, dashboard overview (`overview_bundle.py`) | Must capture all four quarterly bonus events and the full bi-weekly cycle (some months have 3 paychecks). Also provides one full seasonal cycle for trend comparisons. Payee detection must use the same window regardless of which command triggers it. |
| 24 months | Subscription detection (`subscriptions.py`); setup wizard income derivation (`dashboard/api/admin.py` derive-income endpoint) | Annual subscriptions appear once per year; 24 months guarantees at least two occurrences before a charge is classified as a true recurring subscription. The setup wizard uses maximum history depth to bootstrap regular-pay detection on a fresh install. |

### When adding new historical queries

Match the window to the use case:
- **Daily spending patterns** (Tier 4 bridging, short-term behavioral detection): 3 months
- **Monthly bill cadence, spending pace, target calibration**: 6 months
- **Income/bonus detection, annual trend analysis, dashboard overview**: 12 months
- **Recurring subscription or commitment detection**: 24 months

Do not use ad-hoc windows (e.g., 1 month, 18 months) without documenting the rationale in this section.
