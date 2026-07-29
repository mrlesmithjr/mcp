#!/bin/bash
# Planned expense funding gap alert: runs weekly via LaunchAgent
# Flags planned expenses due within 14 days where funding is short vs RTA

LOG="${HOME}/.local/share/homeops/planned-expense-alert.log"
CLAUDE="${HOME}/.local/bin/claude"
TODAY=$(date +%Y-%m-%d)

echo "--- Planned expense alert $TODAY ---" >> "$LOG"

OUTPUT=$("$CLAUDE" \
  --dangerously-skip-permissions \
  -p \
  --max-budget-usd 0.50 \
  "Today is $TODAY. You are running an automated planned expense funding check.

Run these MCP tools:
1. mcp__plugin_ynab-tools_ynab-tools__sync_data: sync YNAB data first
2. mcp__plugin_ynab-tools_ynab-tools__planned_expenses: get all planned expenses
3. mcp__plugin_ynab-tools_ynab-tools__budget_check: get current RTA

Evaluate:
- Which planned expenses have a due date within the next 14 days?
- For each, what is the current category balance vs the amount needed?
- Is the total funding gap for near-due expenses greater than 0?

If ANY planned expense due within 14 days has a funding gap (category balance < amount needed), call mcp__plugin_apple-eventkit-tools_apple-eventkit-tools__reminder_create with:
- title: 'Planned expense funding: action needed'
- list_name: 'Personal'
- due_date: $TODAY
- due_time: '08:00'
- priority: 1
- notes: [list each underfunded expense: name, due date, amount needed, current balance, gap; include current RTA; keep it concise]

If all planned expenses due within 14 days are fully funded, do nothing. Do not explain your reasoning." 2>&1)
STATUS=$?

echo "$OUTPUT" >> "$LOG"

echo "--- Done ---" >> "$LOG"

exit $STATUS
