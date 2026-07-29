"""Daily weather observation logging."""

from datetime import datetime

from lawnops.db.connection import get_db


def log_daily_observations(config, daily_data, advisory_level=None):
    """Log daily observation data to the database.

    Silently skips if auto_log is disabled or DB doesn't exist.
    """
    if not config.get("database", {}).get("auto_log", True):
        return
    try:
        conn = get_db(config)
        today = datetime.now().strftime("%Y-%m-%d")
        for d in daily_data:
            if d["date"] > today:
                continue
            params = {
                "date": d["date"],
                "soil_min": d["soil_min"],
                "soil_max": d["soil_max"],
                "soil_avg": d["soil_avg"],
                "air_min": d.get("air_min"),
                "air_max": d.get("air_max"),
                "air_avg": d.get("air_avg"),
                "precip": d.get("precip_total", 0),
                "wind_max": d.get("wind_max"),
            }
            conn.execute(
                """
                INSERT INTO daily_observations (date, soil_min_f, soil_max_f, soil_avg_f,
                    air_min_f, air_max_f, air_avg_f, precip_in, wind_max_mph)
                VALUES (:date, :soil_min, :soil_max, :soil_avg,
                    :air_min, :air_max, :air_avg, :precip, :wind_max)
                ON CONFLICT(date) DO UPDATE SET
                    soil_min_f=:soil_min, soil_max_f=:soil_max, soil_avg_f=:soil_avg,
                    air_min_f=:air_min, air_max_f=:air_max, air_avg_f=:air_avg,
                    precip_in=:precip, wind_max_mph=:wind_max,
                    logged_at=datetime('now')
            """,
                params,
            )
        if advisory_level:
            conn.execute(
                """
                UPDATE daily_observations SET advisory_level = ?
                WHERE date = ?
            """,
                (advisory_level, today),
            )
        conn.commit()
        conn.close()
    except Exception:
        pass
