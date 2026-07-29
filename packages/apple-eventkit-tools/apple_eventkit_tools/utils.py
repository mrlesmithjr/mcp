"""Shared utility functions for EventKit date and color conversions."""

from datetime import datetime


def _nscolor_hex(nscolor):
    """Convert NSColor to hex string, or return None."""
    try:
        if nscolor is None:
            return None
        r = nscolor.redComponent()
        g = nscolor.greenComponent()
        b = nscolor.blueComponent()
        return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"
    except Exception:
        return None


def _datetime_to_nsdate(dt):
    """Convert Python datetime to NSDate."""
    import Foundation

    return Foundation.NSDate.dateWithTimeIntervalSince1970_(dt.timestamp())


def _nsdate_to_datetime(nsdate):
    """Convert NSDate to Python datetime (local timezone)."""
    if nsdate is None:
        return None
    ts = nsdate.timeIntervalSince1970()
    return datetime.fromtimestamp(ts).astimezone()


def _datetime_to_date_components(dt):
    """Convert Python datetime to NSDateComponents."""
    import Foundation

    components = Foundation.NSDateComponents.alloc().init()
    components.setYear_(dt.year)
    components.setMonth_(dt.month)
    components.setDay_(dt.day)
    if dt.hour or dt.minute:
        components.setHour_(dt.hour)
        components.setMinute_(dt.minute)
    return components
