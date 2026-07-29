# weather-tools

Historical weather MCP server for [Claude Code](https://docs.anthropic.com/en/docs/claude-code), wrapping the [Open-Meteo archive API](https://open-meteo.com/). Fetch daily weather variables for any location and date range, or measure consecutive rain days. No API key, no account, no configuration.

## Setup

```bash
# From packages/weather-tools in your clone of the repo
uv tool install --editable .
```

This installs `weather-tools` (CLI) and `weather-tools-mcp` (MCP server).

## MCP Server

Register with Claude Code:

```bash
claude mcp add -s user weather-tools -- weather-tools-mcp
```

Or add to Claude Desktop (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "weather-tools": {
      "command": "/absolute/path/to/weather-tools-mcp"
    }
  }
}
```

The path must be absolute — Claude Desktop does not inherit your shell `PATH`, so a
bare command name fails to start with no useful error. Find yours with
`which weather-tools-mcp`, then quit Desktop with **⌘Q** and reopen.

### MCP Tools (2)

| Tool | Description |
|------|-------------|
| `weather_history` | Fetch daily weather variables (temperature, precipitation, wind, etc.) for any lat/lon over a date range |
| `rain_streak` | Compute consecutive rain days ending on a given date |

## CLI

```bash
weather-tools history --lat 40.7128 --lon -74.0060 --start 2026-05-01 --end 2026-06-01
weather-tools history --lat 40.7128 --lon -74.0060 --start 2026-05-01 --end 2026-06-01 \
  --var temperature_2m_max --var precipitation_sum
weather-tools streak --lat 40.7128 --lon -74.0060 --as-of 2026-06-01 --lookback 30
```

Run `weather-tools --help` for all options.

## How It Works

Data comes from the Open-Meteo archive endpoint (`https://archive-api.open-meteo.com/v1/archive`), which needs no authentication. There is no default location: pass `--lat`/`--lon` explicitly. Errors return `{"status": "error", "message": "..."}` rather than raising.

## Requirements

- Python 3.11+
- Network access to Open-Meteo

## License

MIT
