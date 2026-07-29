"""Application window finder - score days for spray, granular, and pre-emergent."""

from datetime import date

from lawnops.mowing import is_mow_buffer_day


def find_application_window(product_type, daily_data, config):
    """Score the next 7 forecast days for a product application type.

    Args:
        product_type: One of "spray", "granular", "pre-emergent".
        daily_data: List of daily weather dicts from weather.aggregate_daily().
        config: Full config dict.

    Returns a dict with keys:
        best_day: dict for the highest-scored day (or None).
        all_days: list of scored day dicts.
        status: "GO", "CAUTION", or "NO-GO".
    """
    thresholds = config.get("thresholds", {})
    today = date.today()
    today_str = today.isoformat()

    # Filter to forecast days (today and future, up to 7)
    forecast = [d for d in daily_data if d["date"] >= today_str][:7]

    if not forecast:
        return {"best_day": None, "all_days": [], "status": "NO-GO"}

    scored_days = []
    for d in forecast:
        day_date = date.fromisoformat(d["date"])
        score = 100
        issues = []

        if product_type == "spray":
            score, issues = _score_spray(d, day_date, thresholds, config)
        elif product_type == "granular":
            score, issues = _score_granular(d, day_date, thresholds, config)
        elif product_type == "pre-emergent":
            score, issues = _score_pre_emergent(d, day_date, thresholds, config, forecast)
        else:
            raise RuntimeError(f"Unknown product type: {product_type}")

        scored_days.append(
            {
                "date": d["date"],
                "score": max(0, min(100, score)),
                "issues": issues,
                "air_max": d.get("air_max"),
                "wind_max": d.get("wind_max"),
                "precip": d.get("precip_total", 0),
                "soil_avg": d.get("soil_avg"),
            }
        )

    best = max(scored_days, key=lambda x: x["score"])
    best_score = best["score"]

    if best_score >= 70:
        status = "GO"
    elif best_score >= 40:
        status = "CAUTION"
    else:
        status = "NO-GO"

    return {
        "best_day": best,
        "all_days": scored_days,
        "status": status,
    }


def _score_spray(d, day_date, thresholds, config):
    """Score a day for spray application."""
    score = 100
    issues = []

    min_air = thresholds.get("spray_air_temp", 60.0)
    max_wind = thresholds.get("spray_max_wind_mph", 10.0)

    # Air temp check
    air_max = d.get("air_max")
    if air_max is not None and air_max < min_air:
        score -= 40
        issues.append(f"Air temp {air_max:.0f}F < {min_air:.0f}F")
    elif air_max is not None and air_max >= min_air:
        # Bonus for ideal range (60-85F)
        if air_max <= 85:
            score += 5

    # Wind check
    wind = d.get("wind_max")
    if wind is not None and wind >= max_wind:
        score -= 30
        issues.append(f"Wind {wind:.0f} mph >= {max_wind:.0f} mph")
    elif wind is not None:
        # Lower wind is better
        wind_ratio = wind / max_wind
        score += int((1 - wind_ratio) * 10)

    # Rain check
    precip = d.get("precip_total", 0)
    if precip > 0:
        score -= 35
        issues.append(f'Rain {precip:.2f}"')

    # Mow buffer check
    if is_mow_buffer_day(day_date, config):
        score -= 25
        issues.append("Mow buffer window")

    return score, issues


def _score_granular(d, day_date, thresholds, config):
    """Score a day for granular application."""
    score = 100
    issues = []

    min_air = thresholds.get("granular_min_air_temp", 50.0)
    max_wind = thresholds.get("granular_max_wind_mph", 15.0)

    # Air temp check
    air_max = d.get("air_max")
    if air_max is not None and air_max < min_air:
        score -= 40
        issues.append(f"Air temp {air_max:.0f}F < {min_air:.0f}F")

    # Wind check
    wind = d.get("wind_max")
    if wind is not None and wind >= max_wind:
        score -= 25
        issues.append(f"Wind {wind:.0f} mph >= {max_wind:.0f} mph")

    # Rain during application is bad for granular
    precip = d.get("precip_total", 0)
    if precip > 0:
        score -= 30
        issues.append(f'Rain {precip:.2f}"')

    return score, issues


def _score_pre_emergent(d, day_date, thresholds, config, forecast):
    """Score a day for pre-emergent application (granular + rain bonus)."""
    # Start with granular scoring
    score, issues = _score_granular(d, day_date, thresholds, config)

    # Bonus: rain forecast 24-48h after helps activate pre-emergent
    day_idx = None
    for i, fd in enumerate(forecast):
        if fd["date"] == d["date"]:
            day_idx = i
            break

    if day_idx is not None:
        # Check days +1 and +2 for rain
        rain_after = False
        for offset in [1, 2]:
            future_idx = day_idx + offset
            if future_idx < len(forecast):
                future_precip = forecast[future_idx].get("precip_total", 0)
                if future_precip > 0:
                    rain_after = True
                    break

        if rain_after:
            score += 20
            issues.append("Rain 24-48h after (activates product)")
        else:
            # Not a penalty, just no bonus
            issues.append("No rain forecast to activate")

    return score, issues
