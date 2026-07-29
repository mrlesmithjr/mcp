"""HVAC snapshot storage and trend queries."""

from homeops.db.connection import get_db


def record_snapshot(config, zones, outdoor):
    """Insert one snapshot row per zone into hvac_snapshots.

    Args:
        zones: list of zone dicts from hvac_status() - name, mode, temps, humidity
        outdoor: dict with temp and humidity from hvac_status()

    As of issue #143 (hvac_status() migrated from Home Assistant's REST API
    to Prometheus), zone dicts no longer carry `target_high`/`target_low`/
    `preset` - Prometheus has no equivalent metric for any of the three (see
    ha.py's hvac_status() docstring). The target_temp_high/target_temp_low/
    preset columns below are NOT dropped (that would be a schema migration,
    out of scope here) - every row written from this point forward simply
    gets NULL/NULL/"" in those columns via the .get() defaults below. If
    you're debugging why hvac_trend or a historical query shows nothing but
    NULLs there going forward, this is why - it's not a bug in the query.
    """
    conn = get_db(config)
    from datetime import datetime

    captured_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for zone in zones:
        conn.execute(
            """
            INSERT INTO hvac_snapshots
                (captured_at, zone, hvac_mode, current_temp, target_temp,
                 target_temp_high, target_temp_low, humidity, preset,
                 outdoor_temp, outdoor_humidity)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                captured_at,
                zone["name"],
                zone["mode"],
                zone.get("current_temp"),
                zone.get("target_temp"),
                # target_high/target_low/preset: always None/None/"" as of
                # issue #143 - see this function's docstring.
                zone.get("target_high"),
                zone.get("target_low"),
                zone.get("humidity"),
                zone.get("preset", ""),
                outdoor.get("temp"),
                outdoor.get("humidity"),
            ),
        )

    conn.commit()
    conn.close()
    return {"captured_at": captured_at, "zones_recorded": len(zones)}


def get_hvac_trend(config, zone=None, days=14):
    """Hourly averages of temp, humidity, and active minutes per zone.

    Returns list of {hour, zone, avg_temp, avg_outdoor_temp, max_humidity,
    active_minutes} sorted chronologically.
    """
    conn = get_db(config)
    if zone:
        rows = conn.execute(
            """
            SELECT
                strftime('%Y-%m-%d %H:00', captured_at) AS hour,
                zone,
                ROUND(AVG(current_temp), 1) AS avg_temp,
                ROUND(AVG(outdoor_temp), 1) AS avg_outdoor_temp,
                ROUND(MAX(humidity), 1) AS max_humidity,
                COUNT(CASE WHEN hvac_mode IN ('heat', 'cool', 'heat_cool') THEN 1 END) * 30
                    AS active_minutes
            FROM hvac_snapshots
            WHERE captured_at >= datetime('now', ? || ' days')
              AND zone = ?
            GROUP BY hour, zone
            ORDER BY hour, zone
            """,
            (f"-{days}", zone),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT
                strftime('%Y-%m-%d %H:00', captured_at) AS hour,
                zone,
                ROUND(AVG(current_temp), 1) AS avg_temp,
                ROUND(AVG(outdoor_temp), 1) AS avg_outdoor_temp,
                ROUND(MAX(humidity), 1) AS max_humidity,
                COUNT(CASE WHEN hvac_mode IN ('heat', 'cool', 'heat_cool') THEN 1 END) * 30
                    AS active_minutes
            FROM hvac_snapshots
            WHERE captured_at >= datetime('now', ? || ' days')
            GROUP BY hour, zone
            ORDER BY hour, zone
            """,
            (f"-{days}",),
        ).fetchall()

    conn.close()
    return [dict(r) for r in rows]


def get_mode_distribution(config, zone=None, days=30):
    """Mode breakdown with percentage and estimated runtime minutes per zone.

    Returns list of {zone, hvac_mode, snapshot_count, pct, estimated_minutes}.
    """
    conn = get_db(config)
    if zone:
        rows = conn.execute(
            """
            SELECT
                zone,
                hvac_mode,
                COUNT(*) AS snapshot_count,
                ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (PARTITION BY zone), 1) AS pct,
                COUNT(*) * 30 AS estimated_minutes
            FROM hvac_snapshots
            WHERE captured_at >= datetime('now', ? || ' days')
              AND zone = ?
            GROUP BY zone, hvac_mode
            ORDER BY zone, snapshot_count DESC
            """,
            (f"-{days}", zone),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT
                zone,
                hvac_mode,
                COUNT(*) AS snapshot_count,
                ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (PARTITION BY zone), 1) AS pct,
                COUNT(*) * 30 AS estimated_minutes
            FROM hvac_snapshots
            WHERE captured_at >= datetime('now', ? || ' days')
            GROUP BY zone, hvac_mode
            ORDER BY zone, snapshot_count DESC
            """,
            (f"-{days}",),
        ).fetchall()

    conn.close()
    return [dict(r) for r in rows]


def get_hvac_efficiency(config, months=3):
    """Correlate estimated HVAC runtime with electric utility bills by month.

    Returns list of {month, zone, active_minutes, avg_outdoor_temp, electric_bill}.
    """
    conn = get_db(config)
    rows = conn.execute(
        """
        SELECT
            strftime('%Y-%m', s.captured_at) AS month,
            s.zone,
            COUNT(CASE WHEN s.hvac_mode IN ('heat', 'cool', 'heat_cool') THEN 1 END) * 30
                AS active_minutes,
            ROUND(AVG(s.outdoor_temp), 1) AS avg_outdoor_temp,
            u.amount AS electric_bill
        FROM hvac_snapshots s
        LEFT JOIN utility_bills u
            ON strftime('%Y-%m', s.captured_at) = u.bill_date
            AND u.type = 'electric'
        WHERE s.captured_at >= datetime('now', ? || ' months')
        GROUP BY month, s.zone, u.amount
        ORDER BY month, s.zone
        """,
        (f"-{months}",),
    ).fetchall()

    conn.close()
    return [dict(r) for r in rows]
