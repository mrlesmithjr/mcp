Run the full payee cleanup workflow:

1. `ynab sync` - make sure data is current
2. `ynab payee audit` - find mismatches between bank import names and YNAB payees
3. `ynab payee preview` - show what the current rules would fix and what has no rule yet
4. `ynab payee orphaned` - find payees with zero transactions

Summarize:
- How many mismatches exist and the top offenders
- Which fixes the rules would apply
- Which import patterns still need rules added (suggest rule additions for `ynab_tools/data/payee_rules.json`)
- How many orphaned payees could be cleaned up

Do NOT run `ynab payee fix` or `ynab payee normalize --apply` without asking first.
