"""FastMCP server for weather-tools.

Provides two tools:
- weather_history: fetch daily weather variables for a lat/lon date range.
- rain_streak: compute consecutive rain days ending on a given date.
"""

from __future__ import annotations

import logging
import sys
from datetime import date, timedelta
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from weather_tools.open_meteo import fetch_daily

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
logger = logging.getLogger(__name__)

mcp = FastMCP(
    "weather-tools",
    instructions=(
        "Use these tools to retrieve historical weather data for any location. "
        "'weather_history' fetches arbitrary daily variables (temperature, precipitation, wind, etc.) "
        "from the Open-Meteo archive. "
        "'rain_streak' quickly answers how many consecutive days of rain have occurred up to a given date."
    ),
)


# MCP tool annotations follow the workspace convention in the root CLAUDE.md.
# Both tools only read data, but they fetch it from the external Open-Meteo
# archive API, so they are read-only and open-world.
_READ_ONLY_OPEN_WORLD = ToolAnnotations(readOnlyHint=True, openWorldHint=True)


# ---------------------------------------------------------------------------
# Tool: weather_history
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_READ_ONLY_OPEN_WORLD)
def weather_history(
    latitude: float,
    longitude: float,
    start_date: str,
    end_date: str,
    variables: list[str] | None = None,
    timezone: str = "America/New_York",
) -> dict[str, Any]:
    """Fetch daily historical weather data for a location and date range.

    Calls the Open-Meteo archive API (no auth required) and returns structured
    daily records for the requested variables.

    Parameters
    ----------
    latitude:
        Location latitude in decimal degrees (e.g. 40.7128).
    longitude:
        Location longitude in decimal degrees (e.g. -74.0060).
    start_date:
        Start date in YYYY-MM-DD format (inclusive).
    end_date:
        End date in YYYY-MM-DD format (inclusive).
    variables:
        List of Open-Meteo daily variable names. Defaults to ["precipitation_sum"].
        Common options: precipitation_sum, temperature_2m_max, temperature_2m_min,
        wind_speed_10m_max, rain_sum, snowfall_sum.
    timezone:
        IANA timezone string. Defaults to "America/New_York".

    Returns a dict with:
    - One key per requested variable, each holding a list of {date, value} records.
    - "units": dict mapping variable name to its unit string.
    - "status": "ok" on success, "error" on failure.
    """
    if variables is None:
        variables = ["precipitation_sum"]

    data = fetch_daily(
        latitude=latitude,
        longitude=longitude,
        start_date=start_date,
        end_date=end_date,
        variables=variables,
        timezone=timezone,
    )

    if data.get("error"):
        return {"status": "error", "message": data.get("message", "Unknown error")}

    daily_block = data.get("daily", {})
    units_block = data.get("daily_units", {})
    dates: list[str] = daily_block.get("time", [])

    result: dict[str, Any] = {"status": "ok", "units": {}}

    for var in variables:
        values = daily_block.get(var, [])
        result[var] = [{"date": d, "value": v} for d, v in zip(dates, values)]
        if var in units_block:
            result["units"][var] = units_block[var]

    return result


# ---------------------------------------------------------------------------
# Tool: rain_streak
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_READ_ONLY_OPEN_WORLD)
def rain_streak(
    latitude: float,
    longitude: float,
    as_of_date: str,
    lookback_days: int = 60,
    timezone: str = "America/New_York",
    threshold_mm: float = 0.1,
) -> dict[str, Any]:
    """Compute consecutive rain days ending on (and including) as_of_date.

    Fetches precipitation_sum from the Open-Meteo archive for a lookback window
    and walks backward from as_of_date to find the streak length.

    Parameters
    ----------
    latitude:
        Location latitude in decimal degrees.
    longitude:
        Location longitude in decimal degrees.
    as_of_date:
        The date the streak must end on, in YYYY-MM-DD format. If this date
        had no rain, streak_days will be 0.
    lookback_days:
        How many days before as_of_date to search for the streak start.
        Defaults to 60. The tool fetches one extra day so it can detect
        whether the streak began before the window.
    timezone:
        IANA timezone string. Defaults to "America/New_York".
    threshold_mm:
        Minimum precipitation (mm) to count a day as a rain day. Defaults to 0.1.

    Returns a dict with:
    - "streak_days": consecutive rain days ending on as_of_date (0 if as_of_date was dry).
    - "streak_start": YYYY-MM-DD when the streak began, or null if no streak.
    - "streak_extends_beyond_window": true if the streak may have started before the lookback window.
    - "total_mm": total rainfall over the streak period.
    - "total_inches": total_mm converted to inches.
    - "daily": list of {date, mm, inches, rained} for the full lookback window (newest first).
    - "as_of_date": the date the streak was measured from.
    - "threshold_mm": the threshold used.
    - "status": "ok" on success, "error" on failure.
    """
    try:
        anchor = date.fromisoformat(as_of_date)
    except ValueError:
        return {"status": "error", "message": f"Invalid as_of_date format: '{as_of_date}'. Use YYYY-MM-DD."}

    # Fetch one extra day before the window so we can detect open-ended streaks.
    extra_day = 1
    window_start = anchor - timedelta(days=lookback_days + extra_day - 1)
    start_str = window_start.isoformat()

    data = fetch_daily(
        latitude=latitude,
        longitude=longitude,
        start_date=start_str,
        end_date=as_of_date,
        variables=["precipitation_sum"],
        timezone=timezone,
    )

    if data.get("error"):
        return {"status": "error", "message": data.get("message", "Unknown error")}

    daily_block = data.get("daily", {})
    dates: list[str] = daily_block.get("time", [])
    precip_values: list[float | None] = daily_block.get("precipitation_sum", [])

    if not dates:
        return {
            "status": "error",
            "message": "Open-Meteo returned no data for the requested range.",
        }

    # Build a mapping from date string to mm.
    date_to_mm: dict[str, float] = {}
    for d, v in zip(dates, precip_values):
        date_to_mm[d] = float(v) if v is not None else 0.0

    # Walk backward from as_of_date counting the streak.
    streak_days = 0
    streak_extends_beyond_window = False
    current = anchor

    while True:
        current_str = current.isoformat()
        mm = date_to_mm.get(current_str, 0.0)
        if mm < threshold_mm:
            break  # Dry day -- streak ends here.
        streak_days += 1
        if current == window_start:
            # We've consumed the extra lookback day and the streak is still going.
            streak_extends_beyond_window = True
            break
        current -= timedelta(days=1)

    # Determine streak_start. After the loop, current is the dry day that broke
    # the streak (or window_start if the streak extends beyond the window), so
    # the actual first rainy day is current + 1.
    if streak_days == 0:
        streak_start = None
    else:
        streak_start = (current + timedelta(days=1)).isoformat()

    # Compute totals over the streak window (excluding the guard day if it's beyond).
    total_mm = 0.0
    if streak_days > 0 and streak_start is not None:
        streak_anchor = date.fromisoformat(streak_start)
        for i in range(streak_days):
            day = streak_anchor + timedelta(days=i)
            total_mm += date_to_mm.get(day.isoformat(), 0.0)

    total_inches = round(total_mm * 0.0393701, 3)
    total_mm = round(total_mm, 2)

    # Build the daily list for the lookback window (exclude the extra guard day).
    window_start_display = anchor - timedelta(days=lookback_days - 1)
    daily_list: list[dict[str, Any]] = []
    for i in range(lookback_days):
        day = window_start_display + timedelta(days=i)
        day_str = day.isoformat()
        mm = date_to_mm.get(day_str, 0.0)
        inches = round(mm * 0.0393701, 3)
        daily_list.append(
            {
                "date": day_str,
                "mm": round(mm, 2),
                "inches": inches,
                "rained": mm >= threshold_mm,
            }
        )
    # Return newest first so the most recent day is first.
    daily_list.reverse()

    return {
        "status": "ok",
        "as_of_date": as_of_date,
        "streak_days": streak_days,
        "streak_start": streak_start,
        "streak_extends_beyond_window": streak_extends_beyond_window,
        "total_mm": total_mm,
        "total_inches": total_inches,
        "threshold_mm": threshold_mm,
        "daily": daily_list,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the MCP server."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
