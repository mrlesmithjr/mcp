# Budget Methodology

This document describes the budgeting framework used by ynab-tools. It is designed to work with any YNAB budget that follows the general principles below. User-specific details (income figures, category names, protection tiers) should be configured in your project's CLAUDE.md or equivalent configuration.

## Core Principles

1. **Zero-based budgeting**: Every dollar of income gets assigned to a category. Ready to Assign (RTA) should be $0 after assignment.
2. **Roll with the punches**: When overspending happens, move money between categories rather than ignoring it. YNAB converts uncovered overspending into credit card debt.
3. **Age your money**: Work toward funding next month's expenses with this month's income (one month ahead).

## Funding Streams

Most budgets have two types of categories with different funding patterns:

**Regular operations** - funded from each paycheck:
Bills, living expenses, transportation, lifestyle, debt payments. These repeat monthly with relatively predictable amounts.

**Sinking funds** - funded from surplus income (bonuses, extra paychecks, windfalls):
Annual expenses, travel, gifts, large purchases, savings goals. These accumulate over time toward a target.

## Buffer Strategy

A "holding" or "next month" category acts as a buffer:

1. On the 1st: Move from buffer to fund current month's operations
2. Income during the month flows to RTA
3. Assign from RTA back to buffer throughout the month
4. Goal: Refill buffer to operations target by month end

The buffer target should equal one month of regular operations spending.

## Category Protection Tiers

Organize categories into tiers to guide rebalancing decisions:

**Protected** (never reduce for overspend coverage):
Emergency fund, retirement savings, long-term savings goals. These represent commitments to your future self.

**Preferred sources** (reduce these first when covering overspending):
Discretionary lifestyle categories - dining, hobbies, entertainment, coffee. Pulling back here is a feature, not a sacrifice.

**Accept overspending** (cover from future surplus):
Seasonal categories like gifts and holidays that spike predictably. Cover from the next bonus or extra paycheck rather than raiding other categories.

Users should define their specific protected and preferred categories in their CLAUDE.md configuration.

## Overspend Coverage Policy

When a category ends the month with a negative balance, it must be covered before the month rolls. The sourcing logic follows a priority waterfall.

### Step 1: Classify the overspend

Before sourcing, determine *why* the category is negative. The classification drives where to pull from:

| Classification | Definition | Example | Primary Source |
|----------------|-----------|---------|----------------|
| **Timing float** | Money is owed back and will arrive within days | Reimbursement pending via Venmo/Zelle | Buffer category |
| **Seasonal spike** | Predictable annual event that caused a one-time burst | Holiday gifts, back-to-school | Same group → Preferred sources |
| **Structural overspend** | Category is consistently underfunded vs actual spending | Groceries exceeding target 3+ months | Flag for target adjustment; cover from Preferred sources |
| **One-off surprise** | Unexpected expense unlikely to recur | Emergency vet visit, car repair | Same group → Preferred sources → Buffer |

### Step 2: Source waterfall

These are the available coverage sources, ordered by cost. Users should configure their preferred priority order in their project's CLAUDE.md based on their budget structure and income patterns.

| Source | Cost | When to use |
|--------|------|-------------|
| **Same category group surplus** | Free | Always try first - rebalancing within a spending domain (e.g., Vet covering Food & Supplies in Pet Care) |
| **Buffer category** | Low (replenished by income) | Operational overspending where paychecks/bonuses will replenish the buffer. Natural absorption mechanism for income-vs-spending timing mismatches. |
| **Preferred source categories** | Medium (behavioral change) | When consciously choosing to reduce discretionary spending. Pulling from Dining to cover Groceries means spending less on dining - this should be intentional, not mechanical. |
| **Non-protected categories with surplus** | Medium | Any category with a positive balance outside the protected tier. Check that the surplus isn't earmarked for upcoming expenses. |

### Never touch (protected tier)

Protected categories are off-limits regardless of overspend severity. If the only remaining source is a protected category, let the overspend roll and address it with the next income event.

### Trade-offs to consider

- **Buffer-heavy approach**: If your preferred sources have limited surplus (common when the budget is tight), the buffer absorbs most overages and gets replenished by income. This works well with regular income but can erode the one-month-ahead goal.
- **Preferred-source-heavy approach**: If you have substantial discretionary surplus each month, pulling from lifestyle categories first preserves the buffer. This works well when the budget has meaningful slack.
- **Hybrid**: Use the buffer for timing floats and operational overages, preferred sources for conscious reductions when a category is genuinely overfunded.

Users should analyze their own Holding/buffer history (`ynab trends "Holding" --months 12`) to see which pattern their budget naturally follows, and configure accordingly.

### Tracking

All moves are recorded in `funding_log` via `ynab fund`. Use `ynab fund --log` to audit. The log captures old amount, new amount, delta, and timestamp - but not the *reason* for the move. When making overspend coverage moves, document the rationale alongside the moves so decisions can be reconstructed from the log.

### Structural overspend escalation

If a category is overspent 3+ months in a row, the problem isn't coverage - it's the target. Use `ynab fund --status` to compare the target vs 12-month trimmed average and adjust the monthly funding goal upward. Don't keep pulling from other categories to paper over a target that's too low.

## Important Data Notes

- **RTA**: Query `budget_months.to_be_budgeted` - NOT the "Inflow: Ready to Assign" category
- **Split transactions**: Parent transactions have `category_name = 'Split'` - always look at subtransactions for real category breakdown
- **YNAB amounts**: Already converted to dollars in the local database (converted from milliunits during sync)
- **Circular funding protection**: `fund_goals_apply` skips categories manually reduced this month (detected via funding_log)
