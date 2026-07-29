# MCP Server

ynab-tools exposes 47 callable tools via the Model Context Protocol. Once configured, Claude can access your budget data and take actions from any project - not just the ynab-tools directory.

The `ynab-mcp` entry point is included in the base install; no extra dependencies are needed.

## Setup

### Claude Code (user-scoped)

```bash
claude mcp add -s user ynab-tools -- ynab-mcp
```

This makes the server available in every Claude Code session. Restart Claude Code after running.

### Claude Desktop

Claude Desktop needs an **absolute path** to `ynab-mcp`, and it will not read your
credentials from anywhere you have to set up separately. Both points are covered below.

#### 1. Find the absolute path

Desktop launches servers with a minimal environment and does **not** inherit your
shell's `PATH`, so a bare `"command": "ynab-mcp"` fails to start with no useful error.
Get the real path for however you installed:

```bash
# uv tool install (ynab-mcp is on PATH)
which ynab-mcp

# monorepo / workspace checkout
ls "$PWD/.venv/bin/ynab-mcp"

# installed as a Claude Code plugin
ls ~/.local/share/ynab-tools/venv/bin/ynab-mcp
```

Note `ynab-mcp` is deliberately *not* symlinked into `~/.local/bin` by the plugin
installer — only human-facing CLIs like `ynab` are. So for a plugin or workspace
install there is no short name to fall back on; the absolute path is required.

#### 2. Add the server

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`. The file
already exists and holds Desktop's own settings, so **merge** this key in rather than
replacing the file:

```json
{
  "mcpServers": {
    "ynab-tools": {
      "command": "/absolute/path/to/ynab-mcp"
    }
  }
}
```

No `env` block is needed. The server reads `~/.config/ynab-tools/config.json`, which
`ynab configure` writes with mode 600. Putting `YNAB_ACCESS_TOKEN` in the Desktop
config instead means a second copy of your token in a plaintext file that Desktop
rewrites — prefer the config file. Env vars still take precedence if you need to
override for a single client (see [configuration.md](configuration.md)).

#### 3. Restart and verify

Quit Desktop completely — **⌘Q, not just closing the window** — then reopen. The
tools appear under the hammer icon. Ask it something concrete:

```
Check my budget status
```

To verify the server independently of Desktop, run the same handshake Desktop does,
with an empty environment to prove the absolute path and config lookup both hold:

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
  | env -i HOME="$HOME" PATH=/usr/bin:/bin /absolute/path/to/ynab-mcp
```

A working server returns an `initialize` result followed by all 47 tools.

#### Troubleshooting

| Symptom | Cause |
|---------|-------|
| Server never appears; no error | Bare command name instead of an absolute path, or Desktop was not fully quit (⌘Q) |
| Tools load, every report is empty | Database not populated yet — run `ynab sync` |
| `Error: YNAB_ACCESS_TOKEN and YNAB_PLAN_ID are required.` | `ynab configure` has not been run, or `HOME` is not what you expect |
| `ModuleNotFoundError: No module named 'mcp.server.fastmcp'` | The venv resolved `mcp` 2.x. Rebuild it; the dependency is capped at `<2` |
| `YNAB API error: 500 Internal Server Error` | Transient YNAB server error, **not** a credentials problem (those return `401`). Retry the sync |
| Two `ynab-mcp` processes | Normal — Desktop starts one server per window. Harmless for reads |

Your credentials are not the cause of a `500`. Before re-running `ynab configure` and
risking a working config, check the token directly:

```bash
curl -s -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer $(python3 -c 'import json,os;print(json.load(open(os.path.expanduser("~/.config/ynab-tools/config.json")))["access_token"])')" \
  https://api.ynab.com/v1/budgets
```

`200` means the token is fine and the problem is elsewhere.

### Claude Web (Projects)

In a Claude Web Project, add the MCP server URL pointing to wherever `ynab-mcp` is running. For local access, use a tunneling tool like `ngrok` or configure it as a remote server.

## Slash Commands

Pre-built workflows are available as slash commands in any Claude Code session:

| Command | What it does |
|---------|-------------|
| `/budget-review` | Sync, check budget, spending, and recent transactions |
| `/weekly-checkup` | Weekly spending review with action items |
| `/month-end` | Full month-end report with net worth, debt, and funding recommendations |
| `/payee-cleanup` | Audit payee mismatches and suggest rule additions |

## Available Tools

Read-only tools query the local database. Write tools push changes to the YNAB API and record them in the audit log.

| Tool | Description | Writes to YNAB |
|------|-------------|:--------------:|
| **Sync** | | |
| `sync_data` | Sync YNAB data to local database (`full`, `months` params) | |
| `sync_status` | Show database stats and data freshness | |
| **Budget and Reports** | | |
| `budget_check` | Budget health: RTA, overspent, near-limit, underfunded, health ratios | |
| `month_end_report` | Consolidated month-end closeout report | |
| `monthly_summary` | Income vs spending vs net over time | |
| `spending_report` | Spending by category vs budget (`months`, `breakdown` params) | |
| `spending_pace` | Mid-month pace analysis: on-track, running hot, underspent | |
| `category_trend` | Spending trend for a specific category | |
| `recent_transactions` | Recent transactions (`account`, `payee`, `category`, `memo`, `uncleared`, `month` filters) | |
| `large_expenses` | Find expenses over a threshold | |
| `income_report` | Income breakdown: regular pay, bonuses, YTD comparison | |
| **Analysis** | | |
| `paycheck_forecast` | Projected paycheck dates and amounts | |
| `debt_status` | Debt accounts and payoff estimates | |
| `net_worth` | Net worth snapshot or history | |
| `sinking_funds` | Goal progress and underfunded categories | |
| `category_balance` | Current balance, budgeted, activity, goal status for any category | |
| `transfers` | Recent account transfers | |
| **Funding** | | |
| `funding_status` | Target vs avg spend vs recommendation per category | |
| `fund_goals_preview` | Preview: what would change if all underfunded goals were funded | |
| `fund_goals_apply` | Fund all underfunded goals | YES |
| `fund_category` | Set or adjust a category's budgeted amount | YES |
| **Transactions** | | |
| `add_transaction` | Create a new transaction | YES |
| `update_transaction` | Change category, memo, or splits on a transaction | YES |
| `delete_transaction` | Delete a transaction | YES |
| `categorize_transactions` | Suggest categories for uncategorized transactions (`apply` param) | YES (with apply) |
| `unapproved_transactions` | List unapproved transactions (API-verified) | |
| `approve_transactions` | Approve by ID or all categorized unapproved | YES |
| **Payee Management** | | |
| `payee_audit` | Find payee name mismatches between bank imports and YNAB | |
| `payee_preview` | Preview what payee_rules.json would fix | |
| `payee_fix` | Apply payee rule fixes | YES |
| `create_payee` | Create a new payee | YES |
| **Category Management** | | |
| `create_category_group` | Create a new category group | YES |
| `create_category` | Create a new category in an existing group | YES |
| `set_category_goal` | Set a monthly funding goal on a category | YES |
| `clear_category_goal` | Remove a goal from a category | YES |
| **Planned Expenses** | | |
| `planned_expenses` | List all planned expenses with funding gaps | |
| `add_planned_expense` | Add a planned expense (stored locally) | |
| `complete_planned_expense` | Mark a planned expense as done | |
| `edit_planned_expense` | Update amount, date, memo, or category | |
| **Paycheck** | | |
| `paycheck_funding` | Generate prioritized funding plan (5-tier system) | |
| `paycheck_funding_apply` | Execute the funding plan through a specified tier | YES |
| `paycheck_breakdown` | Show budget split: regular-paycheck-funded vs bonus-funded | |
| `two_pot_compliance` | Structural two-pot compliance per bonus month: structural backwards metric, Holding delta, workflow gap (`months`, `month` params) | |
| **Reconciliation** | | |
| `reconcile_list` | Status for all accounts: YNAB balance, cleared balance, last reconciled | |
| `reconcile_account` | Compare to real balance; create adjustment if needed (`apply` param) | YES (with apply) |
| **Money Movements** | | |
| `movement_log` | Show budget funding movements from the YNAB API (`limit`, `month`, `category` filters). Captures all moves - CLI and YNAB app. | |
| **Subscriptions** | | |
| `get_subscriptions` | Detect recurring subscription charges with frequency, estimated annual cost, and renewal status | |

## How It Works

All 47 tools share the same local SQLite database and reports layer used by the CLI. Read tools query the DB directly. Write tools call the YNAB API and record each change to the local audit log, so the next sync picks up a clean state.

```
Claude → MCP tool call → ynab-mcp → reports layer → SQLite DB (reads)
                                                    → YNAB API (writes)
                                                    → audit_log (all writes)
```

The MCP server uses the `_capture()` helper to redirect CLI report output into strings returned as tool results, so MCP and CLI produce identical output.
