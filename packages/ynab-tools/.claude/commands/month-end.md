Run a month-end financial review:

1. `ynab sync` - refresh data
2. `ynab unapproved` - find uncategorized/unapproved transactions (must resolve before close)
3. `ynab budget` - full budget status (RTA, overspent, underfunded)
4. `ynab spending` - spending vs budget for the closing month
5. `ynab income` - income breakdown and YTD comparison
6. `ynab debt` - debt status and payoff progress
7. `ynab net-worth` - take a net worth snapshot
8. `ynab net-worth --history` - show trend
9. `ynab sinking-funds` - goal progress
10. `ynab fund --status` - funding recommendations
11. `ynab plan` - review planned expenses, mark completed items

Provide a month-end report covering:
- Income vs expenses summary
- Uncategorized/unapproved transactions that need resolution
- Categories that were over/under budget and by how much
- **Overspend coverage plan** - for each negative-balance category, classify the overspend
  (timing float, seasonal spike, structural, one-off) and propose sources following the
  overspend coverage policy in `plugin/skills/ynab-workflow/references/budget-methodology.md`
- Debt payoff progress
- Net worth trend direction
- Sinking fund health
- Structural overspend flags (categories overspent 3+ months - need target adjustment)
- Funding recommendations for next month (which categories to adjust)
- Any action items for the coming month
