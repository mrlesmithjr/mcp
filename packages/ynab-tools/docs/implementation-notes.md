# Implementation Notes

Gotchas, invariants, and edge cases discovered during development. Consult this before writing new queries, endpoints, or reports.

## API and Sync

- YNAB API paths use `/plans/` (migrated from `/budgets/` per YNAB's v1.79.0 rename). Config accepts both `YNAB_PLAN_ID` and `YNAB_BUDGET_ID`.
- All API-touching commands call `config.require_credentials()` which loads config and exits if tokens are missing. Exception: `paycheck-funding` preview (no `--apply`) is DB-only and skips the credential gate; only the apply path calls `require_credentials()` internally via `run_paycheck_funding_apply`
- `sync.py` applies `_normalize_goal_type(goal_type, goal_target_month)` to every category during sync. The YNAB API returns `goal_type="TB"` for regular savings goals even when a target date is set (only CC "pay off by date" goals come back as `"TBD"` natively). The helper normalizes `TB + goal_target_month → TBD` in local storage so sinking-fund and paycheck-funding queries treat dated savings goals correctly.
- `client.py` `update_category_goal()` sends `goal_target_month` (not `goal_target_date`) in the PATCH payload. Both fields appear in GET responses; only `goal_target_month` is accepted on PATCH.
- YNAB API silently converts `goal_type="TBD"` to `"TB"` on write for regular savings categories. The date is stored correctly. The YNAB app shows "Eventually" instead of a deadline until a one-time manual in-app save (Edit Target → Target Balance by Date → pick month) locks in TBD permanently. The sync normalization handles the pre-save state transparently (issue #214).
- `money_movements` is synced from the YNAB API (via `sync_money_movements()` in `sync.py`) and captures ALL budget moves - both ynab-tools CLI moves and moves made directly in the YNAB app. It is the authoritative source for budget move analytics and workflow gap calculations. `funding_log` remains as a local-only audit trail of ynab-tools CLI funding operations. Use `money_movements` for any new analytics or compliance queries that must reflect the full picture of budget moves (#226).
- Subtransaction deduplication (issue #27): YNAB regenerates subtransaction IDs whenever a split transaction is edited. `INSERT OR REPLACE` alone cannot deduplicate across ID changes, so both `sync_transactions()` and `sync_plan_export()` delete existing subtransactions for a parent transaction before inserting the new batch. The delete is guarded by `if subs:` so delta syncs that return no subtransactions for a transaction leave existing rows untouched. `_run_migrations()` in `db.py` runs an idempotent startup dedup query that discards rows whose `last_synced_at` is not the maximum for their `transaction_id`, cleaning up any pre-existing duplicates. This migration relies on `sync.py` always writing `datetime.now(UTC).isoformat()` for `last_synced_at`, making timestamps monotonically increasing across sync runs.

## SQL Query Rules

- `AND hidden = 0` is correct for forward-looking queries (current or future budget categories, funding operations) but must NOT be used in historical reads over `budget_categories` (past months, multi-month `IN` clauses, trend, calibration, variance, and anomaly queries). The `INSERT OR REPLACE` sync pattern propagates the current `hidden` flag to all historical rows, so a category hidden after the fact (e.g. a paid-off loan) gets `hidden = 1` on every past-month row and is silently dropped from historical calculations. Remove `AND hidden = 0` from any historical query and add the comment: `-- hidden intentionally omitted: INSERT OR REPLACE propagates current hidden flag to historical rows`. Corrected across `stats.py`, `reports/spending.py`, `reports/budget.py`, `reports/month_end.py`, `reports/transactions.py`, `reports/paycheck_breakdown.py`, `reports/summary.py`, `reports/funding.py`, and all affected `dashboard/api/` endpoints (issues #121, #122, #124). Exception: `savings_progress.py` `_find_roth_category` and its balance fetch are forward-looking current-month queries and correctly include `AND hidden = 0` (#171).
- `should_skip_group()` from `stats.py` must be called in any new endpoint or report that iterates budget categories. Missing it causes infrastructure groups (`Internal Master Category`, `Credit Card Payments`, business groups) to leak into results. Fixed in `budget.py`, `month_end.py` (#125), `churn.py` (#132), `health_ratios.py` (#144).
- Queries over `transactions` that surface actionable items (unapproved counts, uncategorized counts, attention badges) must `JOIN accounts a ON t.account_id = a.id` and filter `AND a.on_budget = 1`. The `a.type NOT IN (...)` approach is deprecated in favor of `on_budget = 1`. Fixed in `needs_attention.py` (#160), `month_end.py` (#161), `transactions.py` (#163), `unapproved.py` (#166). Credit card payment category lookups must also filter `AND a.on_budget = 1`; `a.type = 'creditCard'` alone passes off-budget tracking CCs which have no `budget_categories` row. Fixed in `account_health.py` (#185).
- All `budget_categories` queries that select specific named categories must include `AND deleted = 0`. Deleted rows survive with `deleted = 1`; without the guard, soft-deleted categories match name filters and return stale data. Fixed in `needs_attention.py` (#183).
- Activity sign handling: any code summing `activity` must exclude refunds/inflows. Python pattern: `abs(activity_val) if activity_val < 0 else 0.0`. SQL pattern: `-SUM(CASE WHEN activity < 0 THEN activity ELSE 0 END)`. Never use `ABS(activity)` unconditionally - that converts refunds into positive spend.
- Trailing N-month averages over `budget_categories.activity` must divide by the fixed period count N, not use SQL `AVG()` with a `WHERE activity < 0` filter. `AVG()` silently excludes zero-spend months. Correct SQL: `-SUM(CASE WHEN activity < 0 THEN activity ELSE 0 END) / N.0`. The denominator must count only months with actual spending (`SUM(CASE WHEN activity < 0 THEN 1 ELSE 0 END)`), not all rows (`COUNT(*)`). Fixed in `spending_pace.py` (#165, #168, #178).
- SQL date-range windows for "last N months" must include both a lower bound (`>= date(?, '-N months')`) and an upper bound (`<= ?`) to exclude future budget months that YNAB may have pre-populated. Fixed in `sinking_funds.py` (#157).
- `month_starts(months, complete_only=True)` must be used whenever computing averages or totals over a historical window. The default includes the current in-progress partial month. The divisor must be `len(month_list)` not the raw `months` parameter. Fixed in `custom_report.py` (#177).
- CC payment category lookups in `month_end.py` and `account_health.py` must include `AND category_group_name = 'Credit Card Payments'`. Category names are unique within a group but not across groups.
- DB row field access must guard against `NULL` field values separately from missing rows. Pattern: `round(float(row["field"]), 2) if (row and row["field"] is not None) else None`. Fixed in `two_pot.py` (#155).
- `funding_log.timestamp` values are stored as naive UTC strings (no timezone suffix). Any Python `datetime` used for comparison must be converted with `.isoformat()` without `tzinfo` - passing `tzinfo=UTC` produces a `+00:00` suffix that breaks SQLite's lexicographic string comparison. Fixed in `two_pot.py` (#170).

## Category Classification and Filtering

- `_is_bonus_funded()` and `_is_excluded()` from `paycheck_breakdown.py` must be applied in any endpoint that returns goal-bearing or budget categories scoped to a specific pot (sinking funds, tier-based funding, waterfall donor lists). Omitting them returns all goal categories indiscriminately. Fixed in `overspend_plan.py` (#133).
- Two-pot exclusion is controlled by two independent config keys read by `paycheck_breakdown._get_breakdown_config()` and used by `_is_excluded(group, name, excluded_groups, excluded_cats)`: `YNAB_EXCLUDED_GROUPS` for group-substring matching (default: `"Credit Card Payments,Internal Master Category"`), and `YNAB_EXCLUDED_CATEGORIES` for exact category-name matching (default: `""`). Exact name match is checked first, then group substring match. Both `paycheck_breakdown._collect_categories()` and `dashboard/api/two_pot.py` call `_is_excluded` before `_is_bonus_funded`.

## Paycheck Funding Tiers

- The `paycheck-funding` tiers enforce the two-pot rule: BONUS-pot categories are excluded from tiers 3, 4, and 5. Classification is delegated to `_is_bonus_funded` from `paycheck_breakdown.py`. Categories in `excluded_groups` or `excluded_categories` are skipped entirely before the bonus/regular classification.
- Tier 1 additionally excludes infrastructure groups (`Internal Master Category`, `Credit Card Payments`) to prevent Uncategorized transactions from inflating overspend totals.
- Tier 3 uses `_should_skip_tier3` which applies a carve-out: CC payment categories (group = `Credit Card Payments`) with an active goal and positive `amount_needed` are included despite their group being in `YNAB_EXCLUDED_GROUPS`. Check order: `_is_bonus` → `is_cc_payoff` (carve-out) → `_is_excluded` → `_is_infra` (refs #208). Tier 3 also applies a bridge top-up: if a Tier 3 goal category also qualifies as daily spending and the post-goal balance would fall below the bridge target, the bridge delta is added to `amount_needed` so it appears as one Tier 3 line rather than a separate Tier 4 entry (refs #209).
- Tier 4 uses `_get_daily_spending_categories()` to auto-detect eligible categories from spend history using two filters: (1) consistency - spend in at least 2 distinct calendar months over the 3-month lookback; (2) lumpiness - max single-month spend must not exceed 2.5x the average monthly spend, where the average always divides by 3.0. No hardcoded category list (refs #209).

## Fund Command

- The `fund` command uses `PATCH /months/{month}/categories/{category_id}` to update budgeted amounts.
- `fund status` calculates trimmed averages (drops high/low months) with variance analysis; rounds up to nearest $5.
- `rta_insufficient` in `fund.py` uses `total_needed > rta` (not `total_needed > rta > 0`). The chained form suppressed the warning when RTA was exactly zero or negative (#159).
- `fund.py` fund-all-goals `total_funded` is accumulated from actual per-category success amounts inside the loop, not set to `total_needed` upfront (#162).
- `fund.py` `apply_calibration_target` only writes a local `goal_under_funded` estimate for simple monthly goals (`NEED` or `MF` with no target date). For date-bound goals (TBD, NEED with a target month), only `goal_target` is updated; `goal_under_funded` is left at its pre-patch value and recomputed by the next sync (#182).

## Overspend Plan

- `overspend_plan.py` `fully_coverable` requires `waterfall_total > 0` (not `>= 0`). When all overspends are one-time and routed to Holding, the waterfall is empty, so `fully_coverable` must be False (#169).
- `overspend_plan` splits categories into `one_time_overspends` (z > 2.0, routed to Holding) and structural overspends in `overspent_categories` (waterfall) before computing the donor waterfall and coverage gap.

## Anomaly Scoring

- `zscore_vs_history()` requires at least 5 data points (4 prior + current month); returns `None` when fewer are available, and `0.0` when baseline std dev is 0. `_build_prior_months()` always generates a full 12-month window with `0.0` fill for months with no activity.
- Anomaly helper chain: `zscore_vs_history` (pure numpy stats, no DB) → `category_zscore` / `category_zscore_by_name` (DB queries, in `stats.py`) → `dashboard/api/_anomaly.py` (re-exports for dashboard) → CLI reports import directly from `stats`.
- `anomaly_label()` returns `"likely one-time"` when z > 2.0, `"worth watching"` when 1.0 < z <= 2.0, and `None` otherwise. `anomaly_likely_one_time()` returns `True` when z > 2.0.
- `calibration.py` `recommendation_confidence`: consistent overspend with zero anomaly months → `"moderate"` (strong evidence target is too low, #176). Zero anomaly months with a LOWER-target recommendation → `"high"` (cleanest possible reduction signal, #181).
- Three endpoints enriched with anomaly fields: `overview_bundle` (biggest overspend z-score), `month_end` (per overspent category z-score, label, coverage suggestion), `calibration` (recent anomaly months, confidence, anomaly_supports_raise).
- CLI reports append anomaly label to overspent rows: `budget.py` always; `spending.py` in single-month mode only.

## Dashboard and API

- All code paths through an endpoint must return the same set of keys. Early-return / no-data dicts must include every field in the TypeScript interface (as `None`/`null`). Fixed in `net_worth_trend.py` (#164) and `overview_bundle.py` (#180).
- All spending/activity amounts returned in API responses must be positive. `month_end.py` `_get_spending_section` returns `"activity": round(-activity, 2)` (#175).
- `/api/overview/net-worth-trend` response includes `delta_from_month: str | null` indicating which prior month's snapshot the delta is computed against. Consumers must display this label rather than assuming "vs. last month" (#152).
- `net_worth_trend.py` fetches raw snapshots with `LIMIT _MAX_RAW_ROWS` (366) before the month-dedup loop, not `LIMIT 12`. After dedup, `unique_snapshots = unique_snapshots[:_MAX_MONTHS]` enforces the 12-month cap (#167).
- `/api/admin/validation-data` response includes `category_group_map: dict[str, str]` mapping each category name to its parent group name. The Admin page uses this to hide per-category suggestion chips when the parent group is already covered by an `excluded_groups` / `bonus_funded_groups` entry.
- Auto-sync: `_auto_sync_loop()` in `server.py` uses `asyncio.to_thread(run_sync)`. Errors are logged and swallowed. `_last_auto_sync_at` is a module-level string (GIL-safe). After each successful sync, `_take_net_worth_snapshot()` runs via a second `asyncio.to_thread`; snapshot errors are caught independently so a failure never blocks the next cycle. The loop uses `continue` on sync failure to skip the snapshot step.
- Auth: `_require_auth` is a global FastAPI dependency applied via `FastAPI(dependencies=[Depends(_require_auth)])`. When `DASHBOARD_PASSWORD` is unset, auth is disabled. When set, all routes return 401 until Basic auth is provided; username is ignored. The `/assets` static mount is not covered. `secrets.compare_digest` prevents timing attacks.

## Subscriptions

- Subscription analytics are configured via four env vars (or config.json keys): `YNAB_SUBSCRIPTION_CATEGORIES`, `YNAB_NON_SUBSCRIPTION_PAYEES`, `YNAB_PAYEE_PREFIX_OVERRIDES` (`prefix|canonical` pairs), `YNAB_PAYEE_NAME_OVERRIDES` (`exact name|canonical` pairs). Map entries use `|` as separator because payee names may contain commas.
- `subscriptions.py` `_load_payee_rules` uses a module-level mtime-aware cache (not `lru_cache`) so changes to `payee_rules.json` are picked up without a server restart (#156).

## Miscellaneous

- Payee commands get a DB connection via `db.get_connection()` and close it when done.
- Payee rules use regex patterns (`import_pattern`) matched against `import_payee_name_original` from transactions.
- Backups are JSON files in `backups/` - created before destructive payee operations.
- The `plan` command stores planned expenses locally in SQLite (`planned_expenses` table) - no YNAB API calls.
- The `audit` command reads from `audit_log`, written by fund, add, category, and reconcile operations.
- The `unapproved` command verifies approval status against the YNAB API before displaying results. One API call per locally-unapproved transaction.
- The `budget` command includes health ratios (housing, auto, debt service, retirement as % of gross) when `YNAB_GROSS_SALARY` is configured.
- `run_balance()` in `budget.py` uses `goal_overall_left` (not `goal_target`) for CC payoff goals. `goal_target` is always 0 for these; the payoff balance lives in `goal_overall_left`. Detection: `is_payoff = goal_target == 0 and overall_left > 0` (refs #208).
- `numpy>=1.26` is a base dependency (not dashboard-only) because `stats.py` is shared between CLI and dashboard.
- Tests: `pytest` (covering names, brokerage, classifier, config, DB, reports including funding, and planned expenses).
