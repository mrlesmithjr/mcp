# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Security

- **Patched the last four transitive dashboard-frontend advisories** — `js-yaml`
  to 4.3.0 (high: quadratic-complexity DoS via merge keys), `@hono/node-server`
  to 2.0.12 (moderate: path traversal in `serve-static`), and `@babel/core` to
  7.29.7 (low: arbitrary file read via `sourceMappingURL`). `npm audit` now
  reports zero vulnerabilities. Non-breaking: only `package-lock.json` changed,
  no direct dependency moved. Scope note — all three are build-time tooling or a
  server adapter this FastAPI-backed dashboard does not run at runtime, so the
  previously shipped bundle was not itself exploitable; this clears the alerts
  and the toolchain rather than closing a live hole. The bundle was rebuilt to
  verify the toolchain and is included.

### Fixed

- **Claude Desktop setup docs were wrong and could not work as written.** Both
  `docs/mcp-server.md` and `docs/getting-started.md` showed
  `"command": "ynab-mcp"` — a bare name. Desktop launches servers with a minimal
  environment and does not inherit the shell `PATH`, and `ynab-mcp` is deliberately
  never symlinked into `~/.local/bin` (only human-facing CLIs are), so the server
  silently failed to start. Both now require an absolute path and explain how to
  find it for each install method. `mcp-server.md` also drops the `env` block that
  put a second plaintext copy of the access token in Desktop's config, in favor of
  the mode-600 `~/.config/ynab-tools/config.json` that `ynab configure` writes, and
  gains verification and troubleshooting sections.

### Changed

- **Dashboard is now opt-in.** The plugin no longer ships or installs a
  `com.ynab-tools.dashboard` LaunchAgent, so installing ynab-tools no longer
  starts a web service on `127.0.0.1:8000`. `ynab dashboard install` is the only
  thing that starts it, and is unchanged. The `dashboard` extra is still installed
  into the plugin venv, so opting in stays a single command with no rebuild.

### Fixed

- **`ynab plans` no longer requires a plan ID.** It is the command that tells
  you what your plan ID is, and `ynab configure` points at it -- but it demanded
  both `YNAB_ACCESS_TOKEN` and `YNAB_PLAN_ID`, so a first-time setup hit a
  chicken-and-egg dead end. `/plans` is a root endpoint, so the token alone is
  now enough (same for the deprecated `ynab payee plans`). `ynab configure` also
  does the lookup itself now: enter your token and it lists the plans that token
  can see and asks you to pick one, falling back to a manual UUID prompt if the
  lookup fails.

- **`ynab dashboard uninstall` no longer gets silently reverted.** The plugin hook
  and `ynab dashboard install/uninstall` both owned the same LaunchAgent label,
  and the hook unconditionally `launchctl load -w`s on every venv rebuild. An
  uninstalled dashboard therefore came back -- running the plugin-venv binary from
  a plist the CLI had not written -- on the next plugin update. The CLI is now the
  sole owner of that plist.

  Existing installs: a dashboard already loaded keeps running after this update.
  Run `ynab dashboard uninstall` once to stop it for good.

## [1.0.370] - 2026-06-20

### Added

- **Planned expenses** (`ynab plan`) - Track upcoming large expenses with funding gap analysis against category balances. Integrates into `ynab budget` to surface expenses due within 30 days.
- **Transaction creation** (`ynab add`) - Create transactions from the CLI with payee, category, memo, date, and inflow/outflow support.
- **Split transactions** (`ynab split`) - Find and apply split transaction suggestions for multi-category purchases.
- **Category management** (`ynab category`) - Create categories, set goals (MF/TB/TBD/NEED types), and clear goals.
- **Account reconciliation** (`ynab reconcile`) - Compare YNAB balances to real-world balances and create adjustment transactions.
- **Retirement tracking** (`ynab retirement`) - Retirement account balances, contribution history, and growth projections.
- **Credit card audit** (`ynab budget --cc-audit`) - Audit credit card payment categories against account balances.
- **Audit log** (`ynab audit`) - View history of all ynab-tools actions with action type filtering.
- **Funding audit log** (`ynab fund log`) - View history of funding changes.
- **Fidelity positions import** (`ynab import positions`) - Import portfolio positions CSV for investment account reconciliation.
- **Brokerage push** (`ynab import brokerage --push`) - Push brokerage transactions directly to YNAB via API.
- **Bank of America import** (`ynab import boa`) - Convert Bank of America transaction CSV to YNAB import format.
- **Subscription detection** (`ynab subscriptions`) - Detect recurring subscription charges with frequency, cost, and renewal date.
- **Paycheck funding plan** (`ynab paycheck-funding`) - Prioritized 5-tier waterfall for allocating RTA after each paycheck.
- **Paycheck budget breakdown** (`ynab breakdown`) - Show which categories are regular-pot-funded vs bonus-pot-funded.
- **Two-pot compliance report** (`ynab two-pot`) - Structural compliance check for the two-pot budgeting methodology.
- **Spending pace** (`ynab spending-pace`) - Mid-month pace analysis per category vs budget and elapsed time.
- **Month-end report** (`ynab month-end`) - Consolidated close-out report combining income, spending, overspends, and surplus.
- **Monthly summary** (`ynab summary`) - Income vs spending vs net overview across months.
- **Money movements sync** - Budget funding moves from the YNAB app are synced and queryable via `movement_log` MCP tool.
- **Web dashboard** - FastAPI + React dashboard with spending trends, Target Calibration, Paycheck Funding, Sinking Funds, Net Worth, Retirement, and more. Install as a macOS LaunchAgent via `ynab dashboard install`.
- **MCP server** - 47 tools exposing all reports and write operations to Claude via the Model Context Protocol.
- Account filter on recent transactions (`ynab recent --account`)
- Circular-funding protection for `ynab fund`
- `planned_expenses` and `audit_log` database tables

## [1.0.0] - 2025-12-16

### Added

- Delta sync engine using YNAB `server_knowledge` for efficient updates
- Payee audit: detect mismatches between bank import names and YNAB payee names
- Payee fix: bulk-correct payee names using regex rules with backup/restore
- Payee normalization: merge duplicate payee names
- Orphaned payee detection
- Auto-categorization with confidence levels (HIGH/MEDIUM/LOW)
- Net worth snapshot tracking with history
- Brokerage CSV import (Fidelity, Merrill Lynch)
- SQLite database for local data storage
- CLI entry point (`ynab` command)
