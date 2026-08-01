# HomeOps

Home maintenance operations CLI and MCP server. Tracks recurring maintenance tasks, pest control treatments, service providers, appliance lifecycles, utility bills, and home costs - all backed by SQLite. Includes HVAC monitoring via Prometheus (sourced from Home Assistant's own climate metrics exporter, not a direct Home Assistant dependency) and budget planning with YNAB integration.

## Setup

```bash
git clone https://github.com/mrlesmithjr/mcp.git
cd mcp/packages/homeops
uv tool install --editable .
```

The SQLite database is created and initialized automatically on first use. To initialize it explicitly before first use, run `homeops db init` (optional).

### Configure Prometheus URL (optional)

Run the setup wizard:

```bash
homeops configure
```

**HVAC monitoring tools query Prometheus, not Home Assistant.** Home Assistant already exports its climate metrics to Prometheus independently of homeops, so `hvac_status`/`hvac_history` read from there instead of calling Home Assistant's REST API - homeops and Home Assistant never depend on each other directly. No credentials are required, just a reachable Prometheus URL - the wizard prompts for one, pre-filled with `http://localhost:9091`. Point it at whichever host runs the Prometheus that scrapes your Home Assistant; only accept the default if Prometheus runs on this machine. Non-sensitive settings (database path, categories) are preserved if you re-run configure.

### Alternative: manual config

Create `~/.config/homeops/config.json` directly:

```json
{
  "prometheus_url": "http://localhost:9091",
  "database": {
    "path": "~/.local/share/homeops/homeops.db"
  },
  "reminders": {
    "list": "Personal",
    "default_time": "10:00"
  }
}
```

### Config Reference

| Config Key | Required | Default | Description |
|-----------|----------|---------|-------------|
| `prometheus_url` | No | `http://localhost:9091` | Prometheus base URL for HVAC tools (no auth) |
| `database.path` | No | `~/.local/share/homeops/homeops.db` | SQLite database location |
| `reminders.list` | No | `Personal` | Unused (issue #39 - Apple Reminders creation removed); harmless to leave in config |
| `reminders.default_time` | No | `10:00` | Unused (issue #39 - Apple Reminders creation removed); harmless to leave in config |
| `categories.tasks` | No | hvac, plumbing, gutters, pest, electrical, exterior, interior, safety, appliance | Valid task categories |
| `categories.providers` | No | hvac, gutters, plumbing, electrical, pest, general, generator, lighting, radon | Valid provider categories |
| `categories.costs` | No | hvac, gutters, pest, plumbing, electrical, repair, supplies, service | Valid cost categories |

### Environment Variable Overrides

Environment variables take precedence over config.json values:

| Variable | Config Key |
|----------|-----------|
| `PROMETHEUS_URL` | `prometheus_url` |
| `HOMEOPS_DB_PATH` | `database.path` |

### Migration from config.yaml

If you have an existing `config.yaml` in the project root, it will be used as a fallback when no `config.json` exists. To migrate, move your settings to `~/.config/homeops/config.json` and add a `prometheus_url` entry if you don't want the default (`http://localhost:9091`).

## Usage

```bash
# Status dashboard
homeops status                       # Overdue tasks, appliance alerts, YTD spending

# Tasks
homeops task list                    # All active tasks with due dates
homeops task overdue                 # Overdue tasks only
homeops task add "Clean gutters" --interval 6m --category gutters
homeops task done "gutters" --cost 150 --provider "Gutter Pro"
homeops task history "gutters"       # Completion history
homeops task pause "gutters"         # Pause a task
homeops task resume "gutters"        # Resume
homeops task escalate                # Report safety/60+-day overdue tasks

# Pest control
homeops pest add --date 2025-03-23 --area perimeter --product "Cyzmic CS" --method spray
homeops pest list                    # Treatment history

# Providers
homeops provider list                # All providers
homeops provider detail "Provider Name"

# Appliances
homeops appliance list               # All with age, warranty, lifespan
homeops appliance alerts             # Expiring warranties and aging units

# Utilities
homeops utility summary              # Spending by type
homeops utility trend electric       # Monthly trend
homeops utility check-anomaly        # Report bills >20% above trailing baseline avg

# Costs
homeops cost summary                 # By category
homeops cost history --year 2025     # Line items

# HVAC (requires Prometheus, not Home Assistant directly)
homeops hvac status                  # Current temps, modes, setpoints
homeops hvac history --hours 48      # Mode changes and overrides

# Budget
homeops budget overview              # Sinking funds + upcoming maintenance
```

## MCP Server

### Claude Code

```bash
claude mcp add -s user homeops -- homeops-mcp
```

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "homeops": {
      "command": "homeops-mcp",
      "env": {
        "PROMETHEUS_URL": "http://localhost:9091"
      }
    }
  }
}
```

### MCP Tools (31)

#### Read Tools

| Tool | Description |
|------|-------------|
| `home_status` | Comprehensive dashboard overview |
| `task_list` | All active tasks with status |
| `task_overdue` | Overdue tasks only |
| `task_history` | Completion history for a task |
| `pest_history` | Pest treatment history |
| `provider_list` | Service providers |
| `provider_detail` | Detailed provider info |
| `appliance_list` | Appliances with age/warranty |
| `appliance_alerts` | Expiring warranties / aging |
| `utility_summary` | Utility spending summary |
| `utility_trend` | Monthly trend for a utility type |
| `cost_summary` | Maintenance spending by category |
| `cost_history` | Cost line items |
| `hvac_status` | Current HVAC zone status |
| `hvac_history` | HVAC history with mode changes |
| `hvac_trend` | Temperature trend over time by zone |
| `hvac_mode_distribution` | Time spent per HVAC mode |
| `hvac_efficiency` | HVAC efficiency metrics |
| `budget_overview` | Budget planning overview |
| `sinking_fund_plan` | Appliance replacement plan |

#### Write Tools

| Tool | Description |
|------|-------------|
| `task_done` | Mark task completed (partial name match, optional date/cost/provider) |
| `task_add` | Add recurring task (name, category, interval, notes) |
| `task_pause` | Pause a task |
| `task_resume` | Resume a paused task |
| `pest_add` | Log pest treatment |
| `cost_add` | Log maintenance cost |
| `utility_add` | Log utility bill |

#### Delete Tools

| Tool | Description |
|------|-------------|
| `task_delete` | Delete task by name (partial match) |
| `pest_delete` | Delete pest treatment by ID |
| `cost_delete` | Delete cost entry by ID |
| `utility_delete` | Delete utility bill by ID |

## Project Structure

```
homeops/
├── __init__.py          # Package init
├── __main__.py          # python -m homeops
├── config.py            # Layered config loader (config.json → env vars)
├── status.py            # Status dashboard aggregation
├── ha.py                # HVAC integration - queries Prometheus, not Home Assistant directly
├── ynab_bridge.py       # YNAB budget integration
├── checklists.py        # Seasonal maintenance checklists
├── db/
│   ├── __init__.py      # Re-export all CRUD
│   ├── connection.py    # SQLite connection helpers
│   ├── schema.py        # Schema + init
│   ├── tasks.py         # Recurring task CRUD
│   ├── pest.py          # Pest treatment tracking
│   ├── providers.py     # Service provider directory
│   ├── appliances.py    # Appliance lifecycle tracking
│   ├── utilities.py     # Utility bill tracking
│   ├── hvac.py          # HVAC history and analytics
│   └── costs.py         # Cost tracking and reports
├── cli/
│   ├── main.py          # Argparse CLI
│   └── display.py       # Terminal formatting
└── mcp_server.py        # FastMCP server (JSON output)
```

## License

MIT
