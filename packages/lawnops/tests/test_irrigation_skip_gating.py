"""Tests for gating skip inference on live controller state (issue #53).

`has_active_program` is a pure, offline signal derived only from live
controller state (program membership + per-zone suspensions) - no hardcoded
IPs/entities/dates/thresholds. `sync_skipped_runs` must not assert skips
(write zero `irrigation_skips` rows, never reach `_infer_skip_reason`) when
the controller reports no active Hydrawise program - covering both retirement
paths ("programs suspended" and "programs removed") - and must leave the
Hydrawise-scheduler path (active, unsuspended program) unaffected.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from lawnops.db.connection import get_db
from lawnops.db.irrigation_log import sync_skipped_runs
from lawnops.irrigation import has_active_program


def _fake_zone(number, suspended=False):
    suspensions = [SimpleNamespace(end_time=None)] if suspended else []
    return SimpleNamespace(number=SimpleNamespace(value=number), suspensions=suspensions)


def _fake_ctrl(zones):
    return SimpleNamespace(zones=zones)


def _program(zone_number=1, zone_name="Front Left", period_days=2):
    return [
        {
            "id": 1,
            "name": "Main Lawn",
            "period_days": period_days,
            "zones": [{"zone_num": zone_number, "zone_name": zone_name}],
        }
    ]


def _seed_runs(config, zone_number=1, zone_name="Front Left"):
    """Two logged runs 10 days apart, well within the 30-day sync window.

    Deliberately spaced further apart than the program's period_days=2 so
    skip inference has gaps to find if it runs - proving skip suppression
    when inactive isn't just an artifact of there being nothing to infer.
    """
    now = datetime.now().date()
    conn = get_db(config)
    for offset in (20, 10):
        date = (now - timedelta(days=offset)).isoformat()
        conn.execute(
            """
            INSERT INTO irrigation_runs (date, zone_number, zone_name, start_time, duration_min, status, source)
            VALUES (?, ?, ?, '06:00', 10, 'Normal watering', 'hydrawise')
            """,
            (date, zone_number, zone_name),
        )
    conn.commit()
    conn.close()


@pytest.fixture()
def tmp_config(tmp_path):
    db_path = tmp_path / "lawnops.db"
    return {"database": {"path": str(db_path)}}


class TestHasActiveProgram:
    """Pure, offline truth table - fake ctrl objects, no network calls."""

    def test_no_program_zones_is_inactive(self):
        ctrl = _fake_ctrl([_fake_zone(1)])
        assert has_active_program(ctrl, []) is False

    def test_unsuspended_program_zone_is_active(self):
        ctrl = _fake_ctrl([_fake_zone(1, suspended=False)])
        assert has_active_program(ctrl, {1}) is True

    def test_all_program_zones_suspended_is_inactive(self):
        ctrl = _fake_ctrl([_fake_zone(1, suspended=True), _fake_zone(2, suspended=True)])
        assert has_active_program(ctrl, {1, 2}) is False

    def test_one_unsuspended_program_zone_among_suspended_is_active(self):
        ctrl = _fake_ctrl([_fake_zone(1, suspended=True), _fake_zone(2, suspended=False)])
        assert has_active_program(ctrl, {1, 2}) is True

    def test_zones_outside_program_membership_are_ignored(self):
        # Zone 3 is unsuspended but not part of any program - must not count.
        ctrl = _fake_ctrl([_fake_zone(1, suspended=True), _fake_zone(3, suspended=False)])
        assert has_active_program(ctrl, {1}) is False


class TestSyncSkippedRunsGating:
    @patch("lawnops.weather.fetch_historical_daily")
    @patch("lawnops.irrigation.get_programs")
    def test_no_skips_written_when_all_program_zones_suspended(self, mock_get_programs, mock_weather, tmp_config):
        _seed_runs(tmp_config)
        ctrl = _fake_ctrl([_fake_zone(1, suspended=True)])
        mock_get_programs.return_value = (ctrl, _program())

        count, active = sync_skipped_runs(tmp_config, days=30)

        assert (count, active) == (0, False)
        mock_weather.assert_not_called()

        conn = get_db(tmp_config)
        row = conn.execute("SELECT COUNT(*) as c FROM irrigation_skips").fetchone()
        conn.close()
        assert row["c"] == 0

    @patch("lawnops.weather.fetch_historical_daily")
    @patch("lawnops.irrigation.get_programs")
    def test_no_skips_written_when_no_programs_configured(self, mock_get_programs, mock_weather, tmp_config):
        _seed_runs(tmp_config)
        ctrl = _fake_ctrl([_fake_zone(1, suspended=False)])
        mock_get_programs.return_value = (ctrl, [])  # program deleted entirely

        count, active = sync_skipped_runs(tmp_config, days=30)

        assert (count, active) == (0, False)
        mock_weather.assert_not_called()

    @patch("lawnops.weather.fetch_historical_daily")
    @patch("lawnops.irrigation.get_programs")
    def test_skips_inferred_when_program_is_active(self, mock_get_programs, mock_weather, tmp_config):
        _seed_runs(tmp_config)
        ctrl = _fake_ctrl([_fake_zone(1, suspended=False)])
        mock_get_programs.return_value = (ctrl, _program())
        mock_weather.return_value = {}

        count, active = sync_skipped_runs(tmp_config, days=30)

        assert active is True
        assert count > 0

        conn = get_db(tmp_config)
        row = conn.execute("SELECT COUNT(*) as c FROM irrigation_skips").fetchone()
        conn.close()
        assert row["c"] == count
