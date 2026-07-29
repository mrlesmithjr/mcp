# Workflows

ynab-tools is designed around a repeating cycle: sync from YNAB, analyze locally, take action, push changes back. Every write operation completes the loop by updating YNAB and recording the action in the local audit log, so the next sync picks up a clean state.

## Sync and Data Flow

Each sync fetches only changes from YNAB using `server_knowledge` delta tokens. Write operations call the YNAB API directly, then record the action in the local audit log.

```mermaid
sequenceDiagram
    participant User
    participant CLI/MCP
    participant sync.py
    participant YNAB API
    participant SQLite DB
    participant Reports

    User->>CLI/MCP: ynab sync (or sync_data MCP tool)
    CLI/MCP->>sync.py: run_sync()
    sync.py->>SQLite DB: read server_knowledge token
    sync.py->>YNAB API: GET /plans/{id}/export?knowledge=N
    YNAB API-->>sync.py: delta payload (accounts, categories, transactions, payees)
    sync.py->>SQLite DB: upsert rows, update server_knowledge

    User->>CLI/MCP: ynab budget (or budget_check MCP tool)
    CLI/MCP->>Reports: run_budget_check()
    Reports->>SQLite DB: SELECT budget_categories, planned_expenses
    SQLite DB-->>Reports: rows
    Reports-->>User: formatted output

    User->>CLI/MCP: ynab fund "Groceries" 850 (or fund_category MCP tool)
    CLI/MCP->>Reports: run_fund()
    Reports->>YNAB API: PATCH /plans/{id}/months/{month}/categories/{id}
    YNAB API-->>Reports: updated category
    Reports->>SQLite DB: write funding_log entry
    Reports-->>User: confirmation
```

## Paycheck Day Workflow

Run after each paycheck lands to fund categories in priority order and verify everything is accounted for.

If your compensation includes bonuses, run `ynab bonus-split` before `ynab paycheck-funding` to separate the bonus portion into the Holding category first. See [Two-Pot Methodology](two-pot-methodology.md) for the full workflow.

```mermaid
sequenceDiagram
    participant User
    participant CLI/MCP
    participant DB
    participant YNAB API

    User->>CLI/MCP: ynab sync
    CLI/MCP->>YNAB API: GET delta (new paycheck transaction)
    YNAB API-->>DB: upsert transactions, budget_months

    User->>CLI/MCP: ynab budget
    CLI/MCP->>DB: RTA, overspent, underfunded goals
    DB-->>User: budget snapshot

    User->>CLI/MCP: ynab paycheck-funding
    CLI/MCP->>DB: compute 5-tier funding plan
    DB-->>User: preview (Tier 1-5 assignments)

    User->>CLI/MCP: ynab paycheck-funding --apply
    CLI/MCP->>YNAB API: PATCH each category budget
    YNAB API-->>CLI/MCP: confirmed
    CLI/MCP->>DB: write funding_log entries

    User->>CLI/MCP: ynab unapproved
    CLI/MCP->>YNAB API: verify unapproved status
    YNAB API-->>User: needs-category list + ready list

    User->>CLI/MCP: ynab approve --all --apply
    CLI/MCP->>YNAB API: bulk approve categorized transactions
    YNAB API-->>DB: next sync updates approval status
```

## Weekly Review Workflow

A lighter-weight mid-week or weekend check to stay on top of spending pace and transaction cleanliness.

1. `ynab sync` - pull the latest transactions
2. `ynab spending` - flag categories over budget or running hot
3. `ynab spending-pace` - check intra-month pace; spot categories running ahead of budget
4. `ynab recent -n 50` - scan for anything unexpected; look for `[!]` (uncategorized) and `[?]` (unapproved)
5. `ynab categorize` - review suggestions for uncategorized transactions
6. `ynab categorize --apply` - push HIGH-confidence categories to YNAB

## Month-End Workflow

Close out the month, cover any overspending, take a net worth snapshot, and carry forward a clean slate.

```mermaid
sequenceDiagram
    participant User
    participant CLI/MCP
    participant DB
    participant YNAB API

    User->>CLI/MCP: ynab sync --full
    CLI/MCP->>YNAB API: full month refresh
    YNAB API-->>DB: complete month data

    User->>CLI/MCP: ynab month-end
    CLI/MCP->>DB: income, spending, overspent,\nunapproved, surplus
    DB-->>User: consolidated closeout report

    note over User: Review overspent categories,\nidentify coverage sources

    User->>CLI/MCP: ynab fund "Holding" -150
    CLI/MCP->>YNAB API: PATCH Holding category budget
    YNAB API-->>DB: funding_log entry

    User->>CLI/MCP: ynab net-worth
    CLI/MCP->>DB: upsert today's snapshot
    DB-->>User: assets, liabilities, net

    User->>CLI/MCP: ynab approve --all --apply
    CLI/MCP->>YNAB API: bulk approve
    YNAB API-->>CLI/MCP: confirmed
```

## Payee Cleanup Workflow

Periodic cleanup to fix messy bank import names, merge duplicates, and prune orphaned payees. Run this after accumulating a batch of new import names or whenever `ynab payee audit` surfaces unmatched patterns.

`ynab_tools/data/payee_rules.json` ships empty. You build it up over time by running `ynab payee audit`, identifying unmatched import patterns, and adding rules for them. See `ynab_tools/data/payee_rules.example.json` for the format.

```mermaid
sequenceDiagram
    participant User
    participant CLI/MCP
    participant payee_rules.json
    participant DB
    participant YNAB API

    User->>CLI/MCP: ynab payee audit
    CLI/MCP->>DB: import names vs YNAB names
    DB-->>User: grouped mismatches with txn counts

    note over User: Edit payee_rules.json\nto add patterns for unmatched payees

    User->>CLI/MCP: ynab payee validate
    CLI/MCP->>payee_rules.json: check canonical refs
    payee_rules.json-->>User: typo warnings (if any)

    User->>CLI/MCP: ynab payee preview
    CLI/MCP->>DB: match rules against import names
    DB-->>User: proposed fixes + unmatched remainder

    User->>CLI/MCP: ynab payee fix
    CLI/MCP->>DB: write backup JSON
    CLI/MCP->>YNAB API: bulk update payee names
    YNAB API-->>CLI/MCP: confirmed

    User->>CLI/MCP: ynab payee normalize
    CLI/MCP->>DB: find case/suffix duplicates
    DB-->>User: merge groups preview

    User->>CLI/MCP: ynab payee normalize --apply
    CLI/MCP->>YNAB API: merge duplicate payees
    YNAB API-->>DB: next sync reflects canonical names
```
