---
name: ynab-workflow
description: >
  Use this skill when the user asks about their "budget", "spending", "finances",
  "financial health", "categories", "transactions", "YNAB", "fund", "sinking funds",
  "debt", "income", "net worth", "retirement", or any personal finance question
  that involves their YNAB budget data. Also trigger when the user asks to
  "check my budget", "how much did I spend", "fund my goals", "review transactions",
  "sync my data", or any variation of budget management tasks.
version: 0.1.0
---

# YNAB Budget Workflow

This skill provides context for working with the user's YNAB budget via the ynab-tools MCP server.

## Available MCP Tools

The `ynab-tools` MCP server exposes 47 tools. Always call `sync_data` first if data might be stale.

### Data Sync
- `sync_data(full?, months?)` - Sync from YNAB API. Delta sync by default (fast). Use `full=true` for complete refresh.
- `sync_status()` - Check data freshness without syncing.

### Reports & Analysis
- `budget_check(month?, overspend_plan?)` - RTA, overspent, near-limit, underfunded categories, health ratios. Supports historical months and overspend classification.
- `monthly_summary(months?)` - Income vs spending vs net per month. Shows surplus/deficit status.
- `month_end_report(month?)` - Consolidated closeout: income, spending, overspent, pending, surplus, planned. Auto-detects closing month.
- `spending_report(months?, breakdown?)` - Spending vs budget by category. Multi-month comparison. Fixed/discretionary breakdown.
- `category_trend(category, months?)` - 6-month spending trend for a category.
- `income_report(months?)` - Regular pay, bonuses, other income, YTD.
- `paycheck_forecast(months?)` - Paycheck income projections.
- `debt_status()` - Debt balances, payments, payoff estimates.
- `net_worth(history?)` - Net worth snapshot or trend.
- `sinking_funds()` - Goal status grouped by funding state.
- `large_expenses(threshold?, months?)` - Large expenses grouped by month.
- `recent_transactions(n?, account?, payee?, category?, memo?, uncleared?, month?)` - Last N transactions with filters. Supports uncleared and month scoping.
- `transfers(days?)` - Recent account transfers.
- `category_balance(category)` - Look up any category balance.
- `funding_status()` - Target vs average spend vs recommendation per category.
- `planned_expenses()` - Active planned expenses with funding gaps.

### Budget Actions
- `fund_category(category, amount)` - Set or adjust a category budget. Use `+100` or `-50` for relative changes.
- `fund_goals_preview()` - Dry-run: show what `fund_goals_apply` would do.
- `fund_goals_apply()` - Fund all underfunded goal categories.

### Transaction Management
- `add_transaction(account, amount, payee?, category?, date?, memo?, inflow?)` - Create a transaction.
- `update_transaction(transaction_id, category?, memo?)` - Modify an existing transaction.
- `delete_transaction(transaction_id)` - Delete a transaction.
- `unapproved_transactions()` - List unapproved transactions.
- `approve_transactions(transaction_id?, all?)` - Approve transactions.
- `categorize_transactions(apply?)` - Auto-suggest categories for uncategorized transactions.

### Category & Payee Management
- `create_category_group(name)` - Create a new category group.
- `create_category(name, group)` - Create a new category.
- `set_category_goal(category, amount, type?, by_date?)` - Set a funding goal.
- `payee_audit()` - Find payee mismatches.
- `payee_preview()` / `payee_fix()` - Preview and apply payee corrections.
- `create_payee(name)` - Create a new payee.

### Planned Expenses
- `add_planned_expense(category, amount, by_date, memo?)` - Add a planned expense.
- `complete_planned_expense(plan_id)` - Mark completed.
- `edit_planned_expense(plan_id, ...)` - Modify a planned expense.

### Paycheck Funding
- `paycheck_forecast(months?)` - Projected paycheck dates and amounts.
- `paycheck_funding(month?)` - Generate prioritized 5-tier funding plan for the current paycheck (dry-run).
- `paycheck_funding_apply(month?, through_tier?)` - Execute the funding plan through a specified tier. Default: Tier 2.
- `paycheck_breakdown(month?)` - Show budget split: regular-paycheck-funded vs bonus-funded categories.

### Compliance and Analysis
- `two_pot_compliance(months?, month?)` - Structural two-pot compliance per bonus month: structural backwards metric, Holding delta, workflow gap.
- `spending_pace(month?)` - Mid-month pace analysis per category: on-track, running hot, or underspent.
- `movement_log(limit?, month?, category?)` - Budget funding movements from the YNAB API. Captures all moves, including those made directly in the YNAB app.

### Reconciliation
- `reconcile_list()` - Status for all accounts: YNAB balance, cleared balance, last reconciled date.
- `reconcile_account(account, balance, apply?)` - Compare to real-world balance; create adjustment transaction if `apply=true`.

### Subscriptions
- `get_subscriptions()` - Detect recurring subscription charges with frequency, estimated annual cost, and next renewal date.

### Additional Category Management
- `clear_category_goal(category)` - Remove a goal from a category.

## Budget Mental Model

Read `references/budget-methodology.md` for the full methodology. Key points:

- **Income flow**: All income flows to RTA, then assigned immediately to all categories.
- **Two funding streams**: Regular operations (funded from paychecks) + sinking funds (funded from bonuses/surplus).
- **Buffer category**: Target equals operations total. Goal is one month ahead.
- **Category protection tiers**: Savings and retirement are protected. Discretionary lifestyle categories are preferred sources for rebalancing. Configured per-user in CLAUDE.md.
- **Overspend coverage**: Classify overspends (timing float, seasonal, structural, one-off) then follow the source waterfall: same group → preferred sources → non-protected surplus → buffer.
- **Circular funding protection**: `fund_goals_apply` skips categories manually reduced this month.

## Common Workflows

### Month-End Closeout
1. `sync_data()` - Refresh data
2. `month_end_report(month="YYYY-MM")` - Consolidated closeout report
3. `unapproved_transactions()` - Resolve uncategorized transactions
4. `budget_check(month="YYYY-MM", overspend_plan=true)` - Classify overspends
5. Cover overspent categories per the overspend coverage policy
6. `recent_transactions(uncleared=true, month="YYYY-MM")` - Check pending transactions
7. `planned_expenses()` - Mark completed items
8. `monthly_summary(months=6)` - Verify income vs spending trend

### Monthly Budget Check-in
1. `sync_data()` - Refresh data
2. `funding_status()` - Review recommendations per category
3. `fund_goals_preview()` then `fund_goals_apply()` - Fund underfunded goals
4. `budget_check()` - Overall health including planned expenses
5. `planned_expenses()` - Review all active plans, mark completed items
6. `spending_report()` - Compare spending vs targets

### Quick Category Adjustment
1. `sync_data()` - Ensure current
2. `category_balance("Category Name")` - Check current state
3. `fund_category("Category Name", amount)` - Adjust (absolute or relative with +/-)

### Transaction Review
1. `sync_data()`
2. `unapproved_transactions()` - Find items needing approval
3. `categorize_transactions()` - Preview auto-categorization suggestions
4. `approve_transactions(all=true)` - Approve all categorized transactions

### Financial Health Overview
1. `sync_data()`
2. `monthly_summary(months=12)` - Income vs spending trend
3. `spending_report(breakdown=true, months=6)` - Fixed vs discretionary split
4. `budget_check()` - RTA, overspent, underfunded, health ratios
5. `debt_status()` - Loan balances and payoff timeline
6. `net_worth(history=6)` - Net worth trend
7. `income_report()` - Income breakdown
