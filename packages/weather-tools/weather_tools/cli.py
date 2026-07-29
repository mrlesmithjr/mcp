"""CLI for weather-tools.

Mirrors the MCP tools so they can be invoked from the command line
for testing and scripting without running a full MCP session.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Optional

import typer

from weather_tools.mcp_server import rain_streak, weather_history

app = typer.Typer(
    name="weather-tools",
    help="Historical weather data via Open-Meteo API.",
    no_args_is_help=True,
)


@app.command("history")
def cmd_history(
    latitude: float = typer.Option(..., "--lat", help="Latitude in decimal degrees."),
    longitude: float = typer.Option(..., "--lon", help="Longitude in decimal degrees."),
    start_date: str = typer.Option(..., "--start", help="Start date YYYY-MM-DD (inclusive)."),
    end_date: str = typer.Option(..., "--end", help="End date YYYY-MM-DD (inclusive)."),
    variables: list[str] = typer.Option(
        ["precipitation_sum"],
        "--var",
        help="Open-Meteo daily variable name. Repeat for multiple variables.",
    ),
    timezone: str = typer.Option("America/New_York", "--tz", help="IANA timezone string."),
) -> None:
    """Fetch daily historical weather data for a location and date range."""
    result = weather_history(
        latitude=latitude,
        longitude=longitude,
        start_date=start_date,
        end_date=end_date,
        variables=variables,
        timezone=timezone,
    )
    typer.echo(json.dumps(result, indent=2))


@app.command("streak")
def cmd_streak(
    latitude: float = typer.Option(..., "--lat", help="Latitude in decimal degrees."),
    longitude: float = typer.Option(..., "--lon", help="Longitude in decimal degrees."),
    as_of_date: Optional[str] = typer.Option(
        None,
        "--as-of",
        help="Date to measure streak ending on (YYYY-MM-DD). Defaults to today.",
    ),
    lookback_days: int = typer.Option(60, "--lookback", help="Days to search for streak start."),
    timezone: str = typer.Option("America/New_York", "--tz", help="IANA timezone string."),
    threshold_mm: float = typer.Option(0.1, "--threshold", help="Min mm to count as a rain day."),
) -> None:
    """Compute consecutive rain days ending on a given date."""
    resolved_date = as_of_date or date.today().isoformat()
    result = rain_streak(
        latitude=latitude,
        longitude=longitude,
        as_of_date=resolved_date,
        lookback_days=lookback_days,
        timezone=timezone,
        threshold_mm=threshold_mm,
    )
    if result.get("status") == "error":
        typer.echo(json.dumps(result, indent=2), err=True)
        raise typer.Exit(code=1)

    # Print a human-friendly summary plus the full JSON.
    streak = result["streak_days"]
    if streak == 0:
        typer.echo(f"No rain on {resolved_date}. Streak: 0 days.")
    else:
        beyond = " (streak may extend further back)" if result.get("streak_extends_beyond_window") else ""
        typer.echo(
            f"Rain streak: {streak} day(s) ending {resolved_date}, "
            f"starting {result['streak_start']}{beyond}. "
            f"Total: {result['total_mm']} mm / {result['total_inches']} in."
        )
    typer.echo(json.dumps(result, indent=2))


def main() -> None:
    """Entry point for the weather-tools CLI."""
    app()


if __name__ == "__main__":
    main()
