# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Status**: ACTIVE
**Last Updated**: 2026-06-20

## Overview

Flight price tracking MCP server and CLI. Searches Google Flights via the `fast-flights` library (no API key, no credentials) and stores tracked routes and price snapshots in a local SQLite database. Used to monitor fares over time and find the best time to buy.

## Setup

```bash
uv tool install --editable .
claude mcp add -s user flightops -- flightops-mcp
```

No configuration required. State lives at `~/.local/share/flightops/flightops.db`, overridable via `FLIGHTOPS_DB_PATH`.

## Architecture

```
flightops/
├── __init__.py          # __version__
├── cli.py               # argparse CLI, cmd_* handlers, dispatch table
├── mcp_server.py        # FastMCP("flightops"), 8 tools
├── amadeus_client.py    # search_flights / search_roundtrip via fast-flights (Google Flights)
├── db.py                # get_db (auto-inits schema), add_route, list_routes, add_snapshot, get_history, get_stats, search log
├── display.py           # terminal table/price formatting
└── fees.py              # per-airline baggage fee tables for --all-in estimates
```

> Note: `amadeus_client.py` is a legacy filename. It does NOT use the Amadeus API. Its module docstring is the source of truth: "Flight search via fast-flights (Google Flights, no API key required)."

## MCP Tools (8)

| Tool | Description |
|------|-------------|
| `search_one_way(origin, destination, date, passengers?, nonstop_only?, airline_filter?, seat?, max_results?)` | One-way search, not stored |
| `search_round_trip(origin, destination, outbound_date, return_date, ...)` | Round-trip search, priced per person |
| `add_route(origin, destination, date, passengers?, target_price?, label?, nonstop_only?, preferred_airline?, return_date?)` | Add a tracked route |
| `list_routes()` | Active routes with latest snapshot |
| `poll_routes(route_id?)` | Fetch + store current best price (one route or all) |
| `get_price_history(route_id, limit?)` | Snapshot history with min/max/avg/count |
| `get_price_alerts()` | Routes at or below target; also lists routes with no target / no data |
| `get_search_stats()` | Search log counts: total, this month, by type, recent |

All tools return JSON; errors return `{"error": "..."}`.

## CLI Commands

| Command | Purpose |
|---------|---------|
| `route add/list/update/remove` | Manage tracked routes |
| `search` | One-off fare search (supports `--return-date`, `--all-in`, `--bags`, `--seat`) |
| `poll [--route-id N]` | Poll and store prices |
| `history <id>` / `report <id>` | Snapshot history and trend analysis for a route |
| `compare --around DATE [--days N]` | Compare prices across nearby dates |
| `trip O1 D1 D1DATE O2 D2 D2DATE` | Multi-leg itinerary vs individual legs |
| `fees [--airline X]` | Airline baggage fee structures |
| `alerts` | Routes at or below target |
| `searches` | Search log counts |

## Key Patterns

- Round-trip prices are normalized to per-person in `amadeus_client.search_roundtrip` so `price_per_person * passengers == price_total`.
- `get_db()` auto-inits the schema on every connection (idempotent via `CREATE TABLE IF NOT EXISTS`); callers do not call any init function explicitly.
- `poll_routes` chooses round-trip vs one-way per route based on whether `return_date` is set, stores only the best (cheapest) result.
- `--all-in` (CLI) adds estimated baggage fees from `fees.py` to the fare for a closer total-cost comparison.
- `price_level` (low/typical/high) comes from Google Flights and is carried through searches and snapshots.

## Entry Points

| Command | Purpose |
|---------|---------|
| `flightops` | CLI |
| `flightops-mcp` | MCP server (stdio; `MCP_TRANSPORT` env overrides transport) |
