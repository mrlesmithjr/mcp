---
description: Quick budget health check
---

Run a quick budget health check:

1. Call `sync_data()` to refresh from YNAB
2. Call `budget_check()` to get RTA, overspent categories, near-limit, underfunded goals, and health ratios
3. Call `planned_expenses()` to surface any upcoming planned expenses with funding gaps

Present results as a concise summary: highlight anything that needs attention (overspent categories, underfunded goals, upcoming expenses with gaps) and note what looks healthy. Keep it brief - the user wants a quick pulse check, not a deep analysis.
