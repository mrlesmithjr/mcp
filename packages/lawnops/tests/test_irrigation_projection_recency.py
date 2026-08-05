"""Tests for the DB-only recency guard on schedule-based projection (issue #53, unit 3b).

`_derive_schedule_params` looks back up to 90 days, so schedule params can be
derived entirely from rows logged before a program was retired (suspended or
removed in favor of external scheduling). `_project_month` degrades to
actual-only when no run has landed within a staleness window
(`max(period_days * 4, 21)` days - see `_STALENESS_FLOOR_DAYS` /
`_STALENESS_CYCLE_MULTIPLIER` in `irrigation_analytics.py`), so stale
pre-retirement rows don't get extrapolated forward during the transition
window.

The staleness window is deliberately wider than one derived cycle
(code review, issue #53): a single cycle length is too tight for an
ACTIVE Hydrawise-scheduler user with a short 2-4 day cycle, since a normal
multi-day rain/wind/freeze skip streak would trip the guard and silently
suppress `irrigation_pace`'s `alert_level`. No live controller dependency -
purely a query against locally logged runs.
"""

from datetime import datetime, timedelta

import pytest
from lawnops.db.connection import get_db
from lawnops.db.irrigation_analytics import _project_month


@pytest.fixture()
def tmp_config(tmp_path):
    db_path = tmp_path / "lawnops.db"
    return {"database": {"path": str(db_path)}}


def _seed_zone_history(config, newest_offset_days, count_per_zone=6, spacing_days=6, zones=(1, 2)):
    now = datetime.now().date()
    conn = get_db(config)
    for i in range(count_per_zone):
        date = (now - timedelta(days=newest_offset_days + i * spacing_days)).isoformat()
        for zone in zones:
            conn.execute(
                """
                INSERT INTO irrigation_runs (date, zone_number, zone_name, start_time, duration_min, status, source)
                VALUES (?, ?, ?, '06:00', 10, 'Normal watering', 'hydrawise')
                """,
                (date, zone, f"Zone {zone}"),
            )
    conn.commit()
    conn.close()


class TestProjectMonthRecencyGuard:
    def test_degrades_to_actual_only_when_schedule_history_is_stale(self, tmp_config):
        # Newest logged run is 60 days ago, spaced 3 days apart -> derived
        # period_days ~12.5, staleness window ~50 days. 60 > 50: genuinely
        # weeks-old data, simulating a program retired mid-transition, with
        # only pre-retirement rows still inside the 90-day lookback.
        _seed_zone_history(tmp_config, newest_offset_days=60, spacing_days=3)

        result = _project_month(tmp_config, current_minutes=15.0, day_of_month=10, days_in_month=30)

        assert result["projection_method"] == "actual_only"
        assert result["projection_reliability"] == "low"
        assert result["projected_minutes"] == 15.0
        assert result["schedule_params"] is not None  # params existed but were stale

    def test_uses_schedule_based_projection_when_history_is_recent(self, tmp_config):
        # Newest logged run is 2 days ago - well within the staleness window -
        # the ordinary Hydrawise-scheduler path, unaffected by the guard.
        _seed_zone_history(tmp_config, newest_offset_days=2)

        result = _project_month(tmp_config, current_minutes=15.0, day_of_month=10, days_in_month=30)

        assert result["projection_method"] == "schedule_based"
        assert result["projected_minutes"] > 15.0

    def test_legitimate_skip_streak_longer_than_cycle_stays_schedule_based(self, tmp_config):
        # Boundary case (code review, issue #53): an ACTIVE scheduler whose
        # newest run is 10 days old, spaced 4 days apart -> derived
        # period_days ~5.0, so the last run is more than one full cycle in
        # the past (a normal rain/wind/freeze skip streak longer than a
        # short 4-day cycle) but well inside the staleness window
        # (max(5.0*4, 21) = 21 days). This must NOT degrade to actual_only -
        # a multi-day skip streak on an active scheduler is normal, not a
        # sign of a retired controller.
        _seed_zone_history(tmp_config, newest_offset_days=10, spacing_days=4)

        result = _project_month(tmp_config, current_minutes=15.0, day_of_month=10, days_in_month=30)

        assert result["schedule_params"]["period_days"] < 10  # skip streak exceeds one cycle
        assert result["projection_method"] == "schedule_based"
        assert result["projected_minutes"] > 15.0

    def test_insufficient_history_still_falls_back_to_linear(self, tmp_config):
        # No rows at all - _derive_schedule_params returns None, unrelated to
        # the recency guard - the pre-existing linear-fallback path.
        result = _project_month(tmp_config, current_minutes=0.0, day_of_month=10, days_in_month=30)

        assert result["projection_method"] == "linear_pace"
        assert result["schedule_params"] is None
