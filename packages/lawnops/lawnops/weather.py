"""Weather data fetching and aggregation from Open-Meteo."""

from datetime import datetime
from statistics import mean

import requests


def fetch_data(config):
    """Fetch soil temp, air temp, precip, and wind data from Open-Meteo.

    Returns the raw JSON response dict.
    """
    params = {
        "latitude": config["location"]["latitude"],
        "longitude": config["location"]["longitude"],
        "hourly": ",".join(
            [
                "soil_temperature_6cm",
                "temperature_2m",
                "precipitation",
                "wind_speed_10m",
            ]
        ),
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": config["location"]["timezone"],
        "past_days": config["api"]["past_days"],
        "forecast_days": config["api"]["forecast_days"],
    }
    resp = requests.get(config["api"]["base_url"], params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def aggregate_daily(data):
    """Group hourly data into daily summaries.

    Returns a list of dicts with keys: date, soil_min, soil_max, soil_avg,
    air_min, air_max, air_avg, precip_total, wind_max.
    """
    hourly = data["hourly"]
    times = hourly["time"]
    soil = hourly["soil_temperature_6cm"]
    air = hourly["temperature_2m"]
    precip = hourly["precipitation"]
    wind = hourly["wind_speed_10m"]

    days = {}
    for i, t in enumerate(times):
        date = t[:10]
        if date not in days:
            days[date] = {"soil": [], "air": [], "precip": [], "wind": []}
        if soil[i] is not None:
            days[date]["soil"].append(soil[i])
        if air[i] is not None:
            days[date]["air"].append(air[i])
        if precip[i] is not None:
            days[date]["precip"].append(precip[i])
        if wind[i] is not None:
            days[date]["wind"].append(wind[i])

    result = []
    for date in sorted(days):
        d = days[date]
        if not d["soil"]:
            continue
        result.append(
            {
                "date": date,
                "soil_min": min(d["soil"]),
                "soil_max": max(d["soil"]),
                "soil_avg": mean(d["soil"]),
                "air_min": min(d["air"]) if d["air"] else None,
                "air_max": max(d["air"]) if d["air"] else None,
                "air_avg": mean(d["air"]) if d["air"] else None,
                "precip_total": sum(d["precip"]) if d["precip"] else 0,
                "wind_max": max(d["wind"]) if d["wind"] else None,
            }
        )
    return result


def fetch_historical_daily(config, start_date: str, end_date: str) -> dict:
    """Fetch daily precip, wind, and air temp from Open-Meteo archive API.

    start_date and end_date are 'YYYY-MM-DD' strings. Returns a dict keyed
    by date with keys: precip_sum, wind_max, air_min.
    """
    params = {
        "latitude": config["location"]["latitude"],
        "longitude": config["location"]["longitude"],
        "daily": "precipitation_sum,wind_speed_10m_max,temperature_2m_min",
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": config["location"]["timezone"],
        "start_date": start_date,
        "end_date": end_date,
    }
    resp = requests.get("https://archive-api.open-meteo.com/v1/archive", params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    daily = data.get("daily", {})
    dates = daily.get("time", [])
    precips = daily.get("precipitation_sum", [])
    winds = daily.get("wind_speed_10m_max", [])
    temps = daily.get("temperature_2m_min", [])
    return {
        d: {
            "precip_sum": precips[i] or 0.0,
            "wind_max": winds[i],
            "air_min": temps[i],
        }
        for i, d in enumerate(dates)
    }


def get_current(data):
    """Get the most recent hourly reading.

    Returns a dict with keys: time, soil_temp, air_temp, precip, wind.
    """
    hourly = data["hourly"]
    now = datetime.now()
    now_str = now.strftime("%Y-%m-%dT%H:00")

    best_idx = None
    for i, t in enumerate(hourly["time"]):
        if t <= now_str:
            best_idx = i

    if best_idx is None:
        best_idx = 0

    return {
        "time": hourly["time"][best_idx],
        "soil_temp": hourly["soil_temperature_6cm"][best_idx],
        "air_temp": hourly["temperature_2m"][best_idx],
        "precip": hourly["precipitation"][best_idx],
        "wind": hourly["wind_speed_10m"][best_idx],
    }
