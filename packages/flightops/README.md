# flightops

Flight price tracking MCP server and CLI for [Claude Code](https://docs.anthropic.com/en/docs/claude-code). Monitor fares for the routes you care about over time, spot price patterns, and find the best time to buy.

Flight data comes from [fast-flights](https://pypi.org/project/fast-flights/) (Google Flights). **No API key, no credentials, no account.** Tracked routes and price snapshots are stored in a local SQLite database.

## Setup

### From Source

```bash
git clone https://github.com/mrlesmithjr/mcp.git
cd mcp/packages/flightops
uv tool install --editable .
```

This installs two commands: `flightops` (CLI) and `flightops-mcp` (MCP server).

### MCP Server

Register as a Claude Code MCP server:

```bash
claude mcp add -s user flightops -- flightops-mcp
```

## How It Works

`flightops` searches Google Flights through the `fast-flights` library, so there is nothing to authenticate. Two kinds of operation:

- **One-off searches** (`search`, `compare`, `trip`) query live prices and print them. Nothing is stored.
- **Tracked routes** (`route add` + `poll`) save a price snapshot each time you poll, building a history you can analyze for trends and alert on when a fare drops to your target.

Round-trip prices are normalized to per-person so `price_per_person * passengers == price_total`.

## MCP Tools (8)

**Search**

| Tool | Description |
|------|-------------|
| `search_one_way(origin, destination, date, ...)` | Search one-way flights on a date |
| `search_round_trip(origin, destination, outbound_date, return_date, ...)` | Search round-trip flights, priced per person |

**Tracked routes**

| Tool | Description |
|------|-------------|
| `add_route(origin, destination, date, target_price?, return_date?, ...)` | Add a route to track for polling |
| `list_routes()` | List active tracked routes with their latest snapshot |
| `poll_routes(route_id?)` | Fetch and store current best prices (one route or all) |
| `get_price_history(route_id, limit?)` | Snapshot history for a route, newest first, with min/max/avg |
| `get_price_alerts()` | Routes where the latest price is at or below the target |
| `get_search_stats()` | Search log counts: total, this month, by type |

All tools return JSON. On failure they return `{"error": "..."}`.

## CLI

```bash
# One-off searches (not stored)
flightops search ATL YVR 2026-07-15 --passengers 2 --nonstop
flightops search ATL YVR 2026-07-15 --return-date 2026-07-22 --all-in --bags 1
flightops compare ATL YVR --around 2026-07-15 --days 3
flightops trip ATL YVR 2026-07-15 LAX ATL 2026-07-22   # multi-leg vs individual legs
flightops fees --airline Delta                          # baggage fee structures

# Track a route, then poll it over time
flightops route add ATL YVR 2026-07-15 --target 600 --label "Summer trip"
flightops route list
flightops route update 1 --target 550
flightops route remove 1
flightops poll                 # poll every active route
flightops poll --route-id 1    # poll a single route

# Analyze stored data
flightops history 1            # snapshot history for route 1
flightops report 1             # price trend analysis
flightops alerts               # routes at or below target
flightops searches             # search log counts
```

Run `flightops <command> --help` for the full option list on any command.

## Data and Configuration

There is nothing to configure. State lives in a single SQLite database at:

```
~/.local/share/flightops/flightops.db
```

Override the location with the `FLIGHTOPS_DB_PATH` environment variable. The database is created automatically on first use.

## Requirements

- Python 3.11+
- Network access to Google Flights (via `fast-flights`)

## Project Structure

```
flightops/
├── __init__.py          # Package init
├── cli.py               # flightops CLI (route, search, poll, history, report, compare, trip, fees, alerts, searches)
├── mcp_server.py        # FastMCP server (8 tools)
├── amadeus_client.py    # Flight search via fast-flights (Google Flights)
├── db.py                # SQLite store: routes, snapshots, search log
├── display.py           # Terminal formatting helpers
└── fees.py              # Airline baggage fee tables for all-in estimates
```

## License

MIT
