"""Mowing schedule awareness - next mow dates, no-mow buffer windows."""

from datetime import date, timedelta

# Map day names to weekday integers (Monday=0)
_DAY_MAP = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def get_next_mow_dates(config, from_date=None, count=4):
    """Return the next `count` mow dates based on configured schedule day.

    Returns an empty list if mowing.schedule_day is not configured.
    """
    schedule_day = config.get("mowing", {}).get("schedule_day")
    if not schedule_day:
        return []

    day_name = schedule_day.lower().strip()
    target_weekday = _DAY_MAP.get(day_name)
    if target_weekday is None:
        return []

    if from_date is None:
        from_date = date.today()

    # Find the next occurrence of the target weekday on or after from_date
    days_ahead = (target_weekday - from_date.weekday()) % 7
    if days_ahead == 0:
        # If today is the mow day, include it
        next_mow = from_date
    else:
        next_mow = from_date + timedelta(days=days_ahead)

    dates = []
    for _ in range(count):
        dates.append(next_mow)
        next_mow += timedelta(weeks=1)
    return dates


def get_no_mow_windows(config, from_date=None, count=4):
    """Return no-mow buffer windows around the next `count` mow dates.

    Each window is a (start_date, end_date) tuple representing dates when
    mowing buffer restrictions apply (no spraying, etc.).

    Returns an empty list if mowing.schedule_day is not configured.
    """
    buffer_days = config.get("mowing", {}).get("no_mow_buffer_days", 2)
    mow_dates = get_next_mow_dates(config, from_date=from_date, count=count)

    windows = []
    for mow_date in mow_dates:
        start = mow_date - timedelta(days=buffer_days)
        end = mow_date + timedelta(days=buffer_days)
        windows.append((start, end))
    return windows


def is_mow_buffer_day(check_date, config):
    """Check if a date falls within any upcoming mow buffer window.

    Returns False gracefully if mowing.schedule_day is not configured.
    """
    windows = get_no_mow_windows(config, from_date=check_date - timedelta(days=14), count=6)
    for start, end in windows:
        if start <= check_date <= end:
            return True
    return False
