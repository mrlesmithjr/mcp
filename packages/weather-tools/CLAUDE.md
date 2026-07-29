# weather-tools

Historical weather data MCP server wrapping the Open-Meteo archive API.

## MCP Tools (2)

| Tool | Description |
|------|-------------|
| `weather_history` | Fetch daily weather variables for any lat/lon date range |
| `rain_streak` | Compute consecutive rain days ending on a given date |

## Key Files

| File | Purpose |
|------|---------|
| `weather_tools/mcp_server.py` | FastMCP server with both tools |
| `weather_tools/open_meteo.py` | HTTP client for the Open-Meteo archive API |
| `weather_tools/cli.py` | CLI mirrors: `weather-tools history`, `weather-tools streak` |

## API

Open-Meteo archive endpoint: `https://archive-api.open-meteo.com/v1/archive`
No authentication required. Returns `daily.time[]` and `daily.<variable>[]`.

## Return Shapes

### weather_history

```json
{
  "status": "ok",
  "units": {"precipitation_sum": "mm"},
  "precipitation_sum": [{"date": "2026-05-24", "value": 55.6}]
}
```

### rain_streak

```json
{
  "status": "ok",
  "as_of_date": "2026-06-01",
  "streak_days": 13,
  "streak_start": "2026-05-19",
  "streak_extends_beyond_window": false,
  "total_mm": 206.3,
  "total_inches": 8.122,
  "threshold_mm": 0.1,
  "daily": [{"date": "2026-06-01", "mm": 2.9, "inches": 0.114, "rained": true}]
}
```

## Entry Points

```
weather-tools      weather_tools.cli:main
weather-tools-mcp  weather_tools.mcp_server:main
```

## CLI Quick Reference

```bash
weather-tools history --lat 40.7128 --lon -74.0060 --start 2026-05-01 --end 2026-06-01
weather-tools history --lat 40.7128 --lon -74.0060 --start 2026-05-01 --end 2026-06-01 --var temperature_2m_max --var precipitation_sum
weather-tools streak --lat 40.7128 --lon -74.0060 --as-of 2026-06-01 --lookback 30
```

## Patterns

- No hardcoded lat/lon anywhere. No default location.
- `rain_streak` fetches `lookback_days + 1` days to detect open-ended streaks.
  If the streak reaches the guard day, `streak_extends_beyond_window` is set to true.
- Errors return `{"status": "error", "message": "..."}` rather than raising.
- Open-Meteo can return a 200 with `{"error": true, "reason": "..."}` -- handled in `open_meteo.py`.

## Install and Register

```bash
# From packages/weather-tools in your clone of the repo
uv tool install --editable .
claude mcp add -s user weather-tools -- weather-tools-mcp
```

## Tests

```bash
cd packages/weather-tools
uv run --extra dev pytest tests/ -v
```

13 integration tests covering both tools. Requires network access (calls live Open-Meteo API).
