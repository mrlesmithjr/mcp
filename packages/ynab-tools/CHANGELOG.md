# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
