"""Irrigation run logging and Hydrawise sync."""

import logging
from datetime import datetime, timedelta

from lawnops.db.connection import get_db

logger = logging.getLogger(__name__)


def log_irrigation_run(config, date, zone_number, zone_name, start_time, duration_min, status, source="hydrawise"):
    """Log an irrigation run to the database.

    Silently skips if auto_log is disabled or DB doesn't exist.
    """
    if not config.get("database", {}).get("auto_log", True):
        return
    try:
        conn = get_db(config)
        conn.execute(
            """
            INSERT INTO irrigation_runs (date, zone_number, zone_name, start_time,
                duration_min, status, source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date, zone_number, start_time) DO UPDATE SET
                duration_min=excluded.duration_min, status=excluded.status,
                logged_at=datetime('now')
        """,
            (date, zone_number, zone_name, start_time, duration_min, status, source),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("Failed to log irrigation run: %s", e)


def sync_irrigation(config, days=7):
    """Pull Hydrawise watering history into the database.

    Returns count of synced runs.
    """
    from lawnops.irrigation import fetch_controller, get_client, run_async

    conn = get_db(config)

    async def _sync():
        hw = get_client()
        ctrl = await fetch_controller(hw)
        end = datetime.now()
        start = end - timedelta(days=days)
        report = await hw.get_watering_report(ctrl, start=start, end=end)

        count = 0
        for entry in report:
            ev = entry.run_event
            zone_name = ev.zone.name if hasattr(ev, "zone") else "Unknown"
            zone_num = ev.zone.number.value if hasattr(ev, "zone") and hasattr(ev.zone, "number") else 0
            run_date = ev.reported_start_time.strftime("%Y-%m-%d") if hasattr(ev, "reported_start_time") else None
            start_time = ev.reported_start_time.strftime("%H:%M") if hasattr(ev, "reported_start_time") else None
            duration = (
                ev.reported_duration.total_seconds() / 60
                if hasattr(ev, "reported_duration") and ev.reported_duration
                else None
            )
            status = str(ev.reported_status) if hasattr(ev, "reported_status") else None

            if run_date and start_time:
                conn.execute(
                    """
                    INSERT INTO irrigation_runs (date, zone_number, zone_name, start_time,
                        duration_min, status, source)
                    VALUES (?, ?, ?, ?, ?, ?, 'hydrawise')
                    ON CONFLICT(date, zone_number, start_time) DO UPDATE SET
                        duration_min=excluded.duration_min, status=excluded.status,
                        logged_at=datetime('now')
                """,
                    (run_date, zone_num, zone_name, start_time, duration, status),
                )
                count += 1

        conn.commit()
        return count

    count = run_async(_sync())
    conn.close()
    return count, days


def backfill_irrigation(config):
    """Pull maximum Hydrawise history (~1 year) into the database."""
    return sync_irrigation(config, days=365)


def _infer_skip_reason(date_str, program_name, weather_by_date):
    """Apply known Hydrawise condition thresholds to infer why a run was skipped.

    Conditions checked (mirrors active Hydrawise predictive watering config):
      #10 - Wind above 15 mph
      #12 - 1.5in+ rainfall last 3 days (both programs)
      #11 - 0.1in+ rainfall yesterday (Beds/Misters only)
      #9  - 80%+ rain forecast (can't verify historically; flagged if same-day rain occurred)
    """
    d = datetime.strptime(date_str, "%Y-%m-%d").date()

    def precip(offset_days):
        key = (d - timedelta(days=offset_days)).isoformat()
        return weather_by_date.get(key, {}).get("precip_sum", 0.0) or 0.0

    wind = weather_by_date.get(date_str, {}).get("wind_max")

    precip_3day = precip(1) + precip(2) + precip(3)
    precip_yesterday = precip(1)
    precip_today = precip(0)

    reasons = []
    if precip_3day >= 1.5:
        reasons.append(f"1.5in+ rain last 3 days ({precip_3day:.2f}in)")
    if "Beds" in program_name and precip_yesterday >= 0.1:
        reasons.append(f"0.1in+ rain yesterday ({precip_yesterday:.2f}in)")
    if wind is not None and wind >= 15:
        reasons.append(f"Wind above 15mph ({wind:.0f}mph)")
    if not reasons and precip_today > 0:
        reasons.append(f"Rain forecast likely ({precip_today:.2f}in actual)")
    if not reasons:
        reasons.append("Unknown - no matching weather threshold")

    return "; ".join(reasons), round(precip_3day, 2), wind


def sync_skipped_runs(config, days=30):
    """Detect expected-but-skipped irrigation runs and store inferred skip reasons.

    Uses program cadence from Hydrawise to calculate expected run dates, compares
    against actual runs in irrigation_runs, fetches historical weather for gaps,
    then infers which predictive watering condition caused the skip.

    Gated on `has_active_program` (issue #53): skip inference assumes Hydrawise
    is the active scheduler, which is not true for every deployment (programs
    suspended or removed in favor of external scheduling). When no active
    program exists, this writes zero `irrigation_skips` rows and never reaches
    `_infer_skip_reason` - Hydrawise-scheduler users are unaffected since their
    controller always reports an active program.

    Returns (count, active) where `active` reflects the live controller state
    that gated this run. The only caller (`irrigation_history`) uses `active`
    to also gate its read of previously-stored skip rows.
    """
    from lawnops.irrigation import get_programs, has_active_program
    from lawnops.weather import fetch_historical_daily

    conn = get_db(config)
    ctrl, programs = get_programs()

    program_zone_nums = {z["zone_num"] for prog in programs for z in prog.get("zones", [])}
    active = has_active_program(ctrl, program_zone_nums)
    if not active:
        conn.close()
        return 0, False

    window_end = datetime.now().date()
    window_start = window_end - timedelta(days=days)

    actual_dates_by_zone: dict[int, set] = {}
    rows = conn.execute(
        "SELECT date, zone_number FROM irrigation_runs WHERE date >= ?",
        (window_start.isoformat(),),
    ).fetchall()
    for r in rows:
        actual_dates_by_zone.setdefault(r["zone_number"], set()).add(r["date"])

    weather_cache: dict = {}
    skip_count = 0

    for prog in programs:
        period = prog.get("period_days") or 2
        prog_id = prog["id"]
        prog_name = prog["name"]

        for zone in prog.get("zones", []):
            zone_num = zone["zone_num"]
            zone_name = zone["zone_name"]

            actual = actual_dates_by_zone.get(zone_num, set())
            if not actual:
                continue

            anchor = datetime.strptime(max(actual), "%Y-%m-%d").date()

            expected: set[str] = set()
            d = anchor
            while d >= window_start:
                expected.add(d.isoformat())
                d -= timedelta(days=period)
            d = anchor + timedelta(days=period)
            while d <= window_end:
                expected.add(d.isoformat())
                d += timedelta(days=period)

            skipped_dates = sorted(expected - actual)
            if not skipped_dates:
                continue

            fetch_start = (datetime.strptime(skipped_dates[0], "%Y-%m-%d").date() - timedelta(days=3)).isoformat()
            fetch_end = min(skipped_dates[-1], (window_end - timedelta(days=1)).isoformat())

            if not weather_cache or fetch_start < min(weather_cache):
                try:
                    weather_cache.update(fetch_historical_daily(config, fetch_start, fetch_end))
                except Exception:
                    pass

            for skip_date in skipped_dates:
                if skip_date >= window_end.isoformat():
                    continue
                reason, precip_3day, wind = _infer_skip_reason(skip_date, prog_name, weather_cache)
                air_min = weather_cache.get(skip_date, {}).get("air_min")
                conn.execute(
                    """
                    INSERT INTO irrigation_skips
                        (expected_date, zone_number, zone_name, program_id, program_name,
                         inferred_reason, precip_3day_in, wind_max_mph, air_min_f)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(expected_date, zone_number) DO UPDATE SET
                        inferred_reason=excluded.inferred_reason,
                        precip_3day_in=excluded.precip_3day_in,
                        wind_max_mph=excluded.wind_max_mph,
                        air_min_f=excluded.air_min_f,
                        synced_at=datetime('now')
                    """,
                    (skip_date, zone_num, zone_name, prog_id, prog_name, reason, precip_3day, wind, air_min),
                )
                skip_count += 1

    conn.commit()
    conn.close()
    return skip_count, True


def get_skip_history_from_db(config, days=30):
    """Query inferred skip history from local database."""
    conn = get_db(config)
    try:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = conn.execute(
            """
            SELECT expected_date, zone_number, zone_name, program_name,
                   inferred_reason, precip_3day_in, wind_max_mph
            FROM irrigation_skips
            WHERE expected_date >= ?
            ORDER BY expected_date DESC, zone_number
            """,
            (cutoff,),
        ).fetchall()
        return [
            {
                "type": "skip",
                "expected_date": r["expected_date"],
                "zone_num": r["zone_number"],
                "zone_name": r["zone_name"] or f"Zone {r['zone_number']}",
                "program": r["program_name"],
                "skip_reason": r["inferred_reason"],
                "precip_3day_in": r["precip_3day_in"],
                "wind_max_mph": r["wind_max_mph"],
            }
            for r in rows
        ]
    finally:
        conn.close()


def get_history_from_db(config, days=7):
    """Query irrigation run history from local database.

    Returns list of dicts matching the format used by the live Hydrawise API path,
    plus estimated_gallons computed from zone GPM config.
    """
    from lawnops.db.water_usage import _zone_gpm_map

    conn = get_db(config)
    zone_gpm, default_gpm = _zone_gpm_map(config)

    try:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = conn.execute(
            """
            SELECT date, zone_number, zone_name, start_time, duration_min, status
            FROM irrigation_runs
            WHERE date >= ?
            ORDER BY date DESC, start_time DESC
        """,
            (cutoff,),
        ).fetchall()

        results = []
        for r in rows:
            zone = r["zone_number"]
            mins = r["duration_min"] or 0
            gpm = zone_gpm.get(zone, default_gpm)
            results.append(
                {
                    "zone_name": r["zone_name"] or f"Zone {zone}",
                    "zone_num": zone,
                    "run_time": f"{r['date']} {r['start_time'] or ''}".strip(),
                    "duration": str(timedelta(minutes=mins)) if mins else "0:00:00",
                    "duration_min": round(mins, 2),
                    "estimated_gallons": round(mins * gpm, 1),
                    "status": r["status"] or "",
                }
            )
        return results
    finally:
        conn.close()
