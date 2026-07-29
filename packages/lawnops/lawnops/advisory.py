"""Lawn care advisory logic - pre-emergent timing and spray window assessment."""

from datetime import date, datetime, timedelta
from statistics import mean

from lawnops.mowing import get_next_mow_dates, is_mow_buffer_day


def consecutive_days_above(daily_data, threshold, use_max=True):
    """Count consecutive days (from most recent backward) where soil temp >= threshold."""
    today = datetime.now().strftime("%Y-%m-%d")
    historical = [d for d in daily_data if d["date"] <= today]
    count = 0
    for day in reversed(historical):
        val = day["soil_max"] if use_max else day["soil_avg"]
        if val >= threshold:
            count += 1
        else:
            break
    return count


def trend_direction(daily_data):
    """Compare last 3 days avg soil temp to prior 3 days.

    Returns (direction_str, diff_float).
    """
    today = datetime.now().strftime("%Y-%m-%d")
    historical = [d for d in daily_data if d["date"] <= today]
    if len(historical) < 6:
        return "insufficient data", 0.0

    recent_3 = mean([d["soil_avg"] for d in historical[-3:]])
    prior_3 = mean([d["soil_avg"] for d in historical[-6:-3]])
    diff = recent_3 - prior_3

    if diff > 2.0:
        return "rising", diff
    elif diff < -2.0:
        return "falling", diff
    else:
        return "stable", diff


def pre_emergent_advisory(daily_data, config):
    """Determine pre-emergent application advisory.

    Returns (level, message) where level is one of:
    SAFE, WARNING, URGENT, LATE, UNKNOWN.
    """
    threshold = config["thresholds"]["pre_emergent_soil_temp"]
    today = datetime.now().strftime("%Y-%m-%d")
    historical = [d for d in daily_data if d["date"] <= today]

    if not historical:
        return "UNKNOWN", "No data available"

    current_avg = historical[-1]["soil_avg"]
    current_max = historical[-1]["soil_max"]
    consec = consecutive_days_above(daily_data, threshold, use_max=True)
    direction, diff = trend_direction(daily_data)

    product = config["products"]["pre_emergent"]

    if consec >= 5:
        return "LATE", (
            f"Soil has been above {threshold}°F for {consec} consecutive days. "
            f"Apply {product} IMMEDIATELY - crabgrass may already be germinating."
        )
    elif consec >= 3 or (current_max >= threshold and direction == "rising"):
        return "URGENT", (
            f"Soil approaching critical threshold. Max today: {current_max:.1f}°F, "
            f"avg: {current_avg:.1f}°F. {consec} days above {threshold}°F. "
            f"Trend: {direction} ({diff:+.1f}°F). Apply {product} within 1-2 days."
        )
    elif current_max >= (threshold - 5) or direction == "rising":
        return "WARNING", (
            f"Soil warming toward threshold. Max today: {current_max:.1f}°F, "
            f"avg: {current_avg:.1f}°F. Trend: {direction} ({diff:+.1f}°F). "
            f"Plan to apply {product} soon - watch daily."
        )
    else:
        gap = threshold - current_max
        return "SAFE", (
            f"Soil temp well below threshold ({current_max:.1f}°F max, "
            f"{gap:.1f}°F below {threshold}°F). Trend: {direction} ({diff:+.1f}°F). "
            f"No rush on {product}."
        )


def bermuda_greenup_ready(current_soil_temp, config):
    """Return True if current soil temp meets the Bermuda green-up threshold."""
    return current_soil_temp >= config["thresholds"].get("bermuda_greenup_soil_temp", 65.0)


def spray_advisory(daily_data, data, config, pollen_data=None):
    """Assess spray window conditions for next 48 hours.

    Returns a dict with keys: soil_ok, soil_temp, total_precip, max_wind,
    air_range, windows, issues, status, pollen_impact (optional).
    """
    thresholds = config["thresholds"]
    today = datetime.now().strftime("%Y-%m-%d")
    now_str = datetime.now().strftime("%Y-%m-%dT%H:00")
    cutoff = (datetime.now() + timedelta(hours=48)).strftime("%Y-%m-%dT%H:00")

    historical = [d for d in daily_data if d["date"] <= today]
    soil_ok = historical[-1]["soil_max"] >= thresholds["spray_soil_temp"] if historical else False

    hourly = data["hourly"]
    forecast_hours = []
    for i, t in enumerate(hourly["time"]):
        if now_str <= t <= cutoff:
            forecast_hours.append(
                {
                    "time": t,
                    "air": hourly["temperature_2m"][i],
                    "precip": hourly["precipitation"][i],
                    "wind": hourly["wind_speed_10m"][i],
                }
            )

    if not forecast_hours:
        return {"status": "UNKNOWN", "message": "No forecast data available"}

    # Find best spray windows (consecutive hours meeting all criteria)
    windows = []
    current_window = []
    for h in forecast_hours:
        air_ok = h["air"] is not None and h["air"] >= thresholds["spray_air_temp"]
        rain_ok = h["precip"] is not None and h["precip"] == 0
        wind_ok = h["wind"] is not None and h["wind"] < thresholds["spray_max_wind_mph"]

        if air_ok and rain_ok and wind_ok:
            current_window.append(h)
        else:
            if len(current_window) >= 2:
                windows.append(current_window)
            current_window = []
    if len(current_window) >= 2:
        windows.append(current_window)

    total_precip = sum(h["precip"] for h in forecast_hours if h["precip"] is not None)
    max_wind = max((h["wind"] for h in forecast_hours if h["wind"] is not None), default=0)
    min_air = min((h["air"] for h in forecast_hours if h["air"] is not None), default=0)
    max_air = max((h["air"] for h in forecast_hours if h["air"] is not None), default=0)

    issues = []
    if not soil_ok:
        issues.append(f"Soil temp below {thresholds['spray_soil_temp']}°F")
    if total_precip > 0:
        issues.append(f'Rain expected ({total_precip:.2f}" in 48hrs)')
    if max_wind >= thresholds["spray_max_wind_mph"]:
        issues.append(f"High winds (max {max_wind:.0f} mph)")
    if max_air < thresholds["spray_air_temp"]:
        issues.append(f"Air temp too low (max {max_air:.0f}°F)")

    # Mow buffer awareness
    today_date = date.today()
    mow_conflict = is_mow_buffer_day(today_date, config)
    next_mow = get_next_mow_dates(config, from_date=today_date, count=1)
    next_mow_date = next_mow[0].isoformat() if next_mow else None

    if mow_conflict:
        issues.append(f"In mow buffer window (next mow: {next_mow_date})")

    result = {
        "soil_ok": soil_ok,
        "soil_temp": historical[-1]["soil_max"] if historical else None,
        "total_precip": total_precip,
        "max_wind": max_wind,
        "air_range": (min_air, max_air),
        "windows": windows,
        "issues": issues,
        "status": "GO" if not issues and windows else "NO-GO",
        "mow_conflict": mow_conflict,
        "next_mow_date": next_mow_date,
    }

    # Pollen impact (advisory only - does NOT change GO/NO-GO status)
    if pollen_data and pollen_data.get("total_count") is not None:
        from lawnops.pollen import pollen_spray_impact

        impact = pollen_spray_impact(pollen_data["total_count"], config)
        result["pollen_impact"] = impact
        result["pollen_count"] = pollen_data["total_count"]
        delay_threshold = config.get("pollen", {}).get("spray_delay_threshold", 1500)
        if pollen_data["total_count"] >= delay_threshold:
            result["issues"].append(
                f"Extreme pollen ({pollen_data['total_count']}) \u2014 consider delaying spray 24-48h"
            )

    return result
