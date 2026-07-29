# Dashboard

The web dashboard provides variance-aware budget analysis, spending trends, and target calibration recommendations. It requires the `dashboard` extra.

## Install

```bash
uv tool install ".[dashboard]"
```

## Background / Autostart

### macOS (LaunchAgent)

`ynab dashboard install` is **macOS-only**. It registers a LaunchAgent that starts the dashboard on login and restarts it on crash.

```bash
# 1. Install with dashboard support
uv tool install "ynab-tools[dashboard]"

# 2. Configure credentials (one-time)
ynab configure

# 3. Initial data sync
ynab sync

# 4. Install as a persistent background service
ynab dashboard install
```

`install` defaults to `--sync-interval 60` so data refreshes every hour automatically. The dashboard runs at `http://127.0.0.1:8000`.

### Linux

`ynab dashboard install` is not available on Linux. Run the dashboard directly:

```bash
ynab dashboard start --sync-interval 60
```

A systemd service unit for persistent Linux autostart is planned (see [issue #102](https://github.com/mrlesmithjr/ynab-tools/issues/102)).

## Service Management (macOS)

```bash
ynab dashboard              # No subcommand: shows status output (same as ynab dashboard status)
ynab dashboard status       # Running/stopped, URL, recent log lines
ynab dashboard restart      # Restart after upgrades or config changes
ynab dashboard logs         # Tail the log file
ynab dashboard uninstall    # Stop and remove the LaunchAgent
```

## Foreground Mode

```bash
ynab dashboard start                          # http://127.0.0.1:8000
ynab dashboard start --port 8080              # Custom port
ynab dashboard start --sync-interval 60       # Auto-sync every 60 minutes
ynab dashboard start --reload                 # Dev mode with auto-reload
```

## LAN and Network Access

Set a password before exposing the dashboard on the network:

```bash
DASHBOARD_PASSWORD="yourpassword" ynab dashboard install --host 0.0.0.0
```

Or in `~/.config/ynab-tools/config.json`:

```json
{
  "dashboard_password": "yourpassword",
  "dashboard_host": "0.0.0.0"
}
```

The password is required for any non-loopback bind. `start` and `install` exit with an error rather than serving budget data to the network unauthenticated — set `DASHBOARD_PASSWORD` (or `dashboard_password`) or keep the default `127.0.0.1` host.

On loopback, a password remains optional. Without one the dashboard runs unauthenticated, so any local process or browser tab can read full budget data including balances, transactions, income, and net worth; a warning is printed on startup and during `install`/`status`.

Note that HTTP Basic sends the password base64-encoded on every request. Over plain HTTP on a LAN that is trivially recoverable by anyone who can observe the traffic — terminate TLS in front of the dashboard if it leaves the machine.

It is strongly recommended to set a password even for localhost-only use:

```bash
# Via environment variable
DASHBOARD_PASSWORD="yourpassword" ynab dashboard start

# Or permanently in ~/.config/ynab-tools/config.json
{ "dashboard_password": "yourpassword" }
```

Keep the default `127.0.0.1` bind unless you also have a password configured.

## Dev Install (from source)

```bash
cd ynab_tools/dashboard/frontend && npm install && npm run build && cd -
uv tool install --editable ".[dashboard]"
ynab dashboard start --reload
```

API docs are available at `http://127.0.0.1:8000/api/docs`.

## Dashboard Views

### Sidebar Navigation

The sidebar organizes views into four labeled sections. Advanced-only sections and items are hidden in basic mode.

| Section | Views |
|---------|-------|
| Operational | Overview, Unapproved Inbox, Spending Pace, Paycheck Funding, Upcoming |
| Monthly Remediation | Month-End (includes Overspend Coverage), Sinking Funds |
| Strategic (advanced) | Income, Financial Health, Target Calibration, Two-Pot Compliance |
| Quarterly (advanced) | Retirement, Net Worth, Subscriptions, Funding Log |
| Utilities (advanced) | Custom Report, Audit Log, Admin |

### View Reference

| View | What it shows |
|------|---------------|
| Overview | Current month snapshot: RTA, income, spending, pace per category (on-track / running hot / underspent / overspent); primary action banner auto-refreshes after approve-all and fund-goals actions |
| Unapproved Inbox | List of on-budget unapproved transactions with bulk-approve controls; KPI summary cards that previously appeared at the top of this page have been removed |
| Spending Pace | Intra-month spending pace per category; running-hot and overspent rows include anomaly z-score badges ("worth watching" or "likely one-time") when z >= 1.0; the Under-Paced section is collapsed by default and expands on click |
| Upcoming | Planned expenses for the next 30 days plus recurring bills inferred from transaction history (payees appearing in 3+ of the last 4 months); recurring bill chips are visually distinct and appear in a separate "Recurring Bills" table section |
| Target Calibration | Every category's average spend vs budgeted target, with variance classification, recommended adjustments, and months-over-budget data (formerly in Churn Analysis); each row now leads with a colored confidence badge (green = high, amber = moderate, gray = low) replacing the former italic text below the Apply button |
| Financial Health | Two-tab page combining Trends (income vs spending over time with rolling averages and surplus/deficit streaks) and Budget Fit; replaces the former standalone Trends and Budget Fit pages. Routes /trends and /budget-fit redirect here. The Trends tab shows Average Monthly Surplus and Trend Direction (last 3 months vs prior 3) KPI cards, replacing the former Best Month / Worst Month cards. |
| Net Worth | Current net worth, ex-mortgage net worth, MoM/YoY deltas, 24-month trend chart (total and ex-mortgage lines), and full asset/liability breakdown by account; ex-mortgage history line is approximate (uses off-budget debt as proxy); footnote on chart clarifies this |
| Group Trends | Spending trend by category group over time |
| Month-End | Consolidated close-out report with overspend classification and coverage suggestions. When overspends exist, an "Overspend Coverage" collapsible section appears inline at the bottom (formerly the standalone Overspend Plan page). Route /overspend-plan redirects here. The Transaction Review section at the bottom of Month-End has been removed; transaction details are shown in the top KPI row only. |
| Sinking Funds | Goal balances and underfunded status for bonus-pot goal categories; shows an amber callout above the table when any goals have past-due target dates, and an RTA pre-flight line above the Fund All Goals button showing whether available RTA covers total underfunded goals |
| Income | Regular pay and bonus income breakdown; the YTD KPI shows a Pace card (actual vs expected YTD based on salary) rather than a raw YTD total |
| Funding Log | Funding history and churn patterns (formerly "Churn Analysis"); the table now includes a Current Target column showing each category's current budgeted amount. Route /churn still exists. |
| Subscriptions | Recurring subscription detection with cost analysis; shows an amber callout above the table when any subscriptions renew within the next 14 days |
| Two-Pot | Budget split between regular-paycheck-funded and bonus-funded categories; structural backwards is now labeled "Bonus covered operations" and workflow gap is now labeled "Funding sequence gap (approx)"; the headroom figure appears at the top of the expanded month detail |
| Retirement | Retirement account balances and contributions; the Next Checkpoint KPI shows projected balance vs the next Fidelity savings benchmark (1x@30 through 10x@67), replacing the removed Years to FRA card; includes a Social Security card showing claim-age scenarios and combined portfolio+SS income at FRA and delayed age (requires `YNAB_SS_*` config; degrades gracefully if not set) |
| Paycheck Funding | Tier-based funding plan for the current paycheck; the KPI row shows Months Covered (RTA divided by T1-T3 total, as a decimal) rather than the former Protected (T1-T3) total |
| Admin | Validation and configuration for two-pot assignments and exclusion rules |

## Primary Action Banner (Overview)

The Overview page shows a full-width `PrimaryActionBanner` between the KPI row and the Needs Attention section. It fetches `GET /api/overview/primary-action` and displays the single most important financial action for the current month.

The endpoint evaluates five priority states in order and returns the first that applies:

| Priority | Condition | Action |
|----------|-----------|--------|
| `unapproved` | On-budget unapproved transactions exist | Review unapproved inbox |
| `fund_rta` | RTA > 0 and regular-pot categories have underfunded goals | Fund underfunded goals from RTA |
| `fund_goals` | RTA > 0 and sinking-fund goals are underfunded | Fund sinking-fund goals |
| `structural_overspend` | Categories are overspent and RTA is <= 0 to cover them | Address structural overspending |
| `all_good` | None of the above apply | Nothing actionable this month |

The banner renders in both basic and advanced mode. The `action_path` in the response links directly to the relevant dashboard view.

## Pace Classification (Overview)

Each category in the Overview is labeled based on how its spending rate compares to the month's elapsed time:

| Label | Condition |
|-------|-----------|
| `ON_TRACK` | Spending pace matches elapsed month percentage |
| `RUNNING_HOT` | Pace ratio > 1.15 (spending faster than the month is progressing) |
| `UNDERSPENT` | >70% of month elapsed but <40% of budget used |
| `OVERSPENT` | Category balance is negative |

## Variance Classification (Target Calibration)

The Target Calibration view uses the same `stats.py` engine as `ynab fund status`. Each category is classified by spending pattern and assigned a recommendation strategy:

| Pattern | CV Threshold | Recommendation Strategy |
|---------|-------------|------------------------|
| consistent | < 0.35 | Trimmed avg, rounded up to nearest $5 |
| moderate variance | 0.35-0.60 | Trimmed avg with `*` warning |
| lumpy | >25% zero-spend months | Amortized avg (includes zero months) |
| high variance | > 0.60 | Flagged, no recommendation shown |
| insufficient data | < 3 months | Skipped |

Lumpy categories (e.g., annual expenses, irregular bills) are amortized across all months including the zeros, so the recommendation reflects a monthly reserve amount rather than the average active-month spend.

## Auto-Sync

The dashboard auto-syncs on a configurable interval. Priority order for the interval setting:

1. CLI `--sync-interval` flag
2. `YNAB_DASHBOARD_SYNC_INTERVAL` env var
3. `dashboard_sync_interval_minutes` in `config.json`

After each successful sync, a net worth snapshot is automatically recorded. Sync errors are logged and swallowed - the server never crashes on a sync failure.
