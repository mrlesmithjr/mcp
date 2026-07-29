"""Mowing-gap detection.

get_mowing_summary reports what is logged and cannot tell a stopped service from
an untracked one. next_mow_date does not help: it comes from the configured
schedule_day and never reads the log, so a season of unlogged visits looks
healthy. These tests cover the signal that closes that gap.
"""

import sqlite3
from datetime import date, timedelta

import pytest
from lawnops.db.mowing import get_mowing_gap
from lawnops.db.schema import SCHEMA_SQL


@pytest.fixture
def config(tmp_path):
    db = tmp_path / "lawnops.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    conn.close()
    return {
        "database": {"path": str(db)},
        "mowing": {"schedule_day": "friday", "default_provider": "Test Mowing"},
    }


def _add(config, when):
    conn = sqlite3.connect(config["database"]["path"])
    conn.execute(
        "INSERT INTO mowing_visits (date, provider, cost, notes) VALUES (?, ?, ?, ?)",
        (when.isoformat(), "Test Mowing", None, None),
    )
    conn.commit()
    conn.close()


def test_none_when_nothing_logged(config):
    assert get_mowing_gap(config) is None


def test_recent_visit_is_not_flagged(config):
    _add(config, date.today() - timedelta(days=5))
    gap = get_mowing_gap(config)
    assert gap["days_since_last"] == 5
    assert gap["unlogged_suspected"] is False


def test_single_late_visit_is_not_flagged(config):
    # 10 days on a 7-day cadence is late, not missing. Flagging here would make
    # the signal noise.
    _add(config, date.today() - timedelta(days=10))
    assert get_mowing_gap(config)["unlogged_suspected"] is False


def test_missed_cycle_is_flagged(config):
    _add(config, date.today() - timedelta(days=15))
    gap = get_mowing_gap(config)
    assert gap["unlogged_suspected"] is True
    assert gap["expected_interval_days"] == 7


def test_reports_the_latest_visit_not_the_first(config):
    _add(config, date.today() - timedelta(days=60))
    _add(config, date.today() - timedelta(days=3))
    gap = get_mowing_gap(config)
    assert gap["last_visit"] == (date.today() - timedelta(days=3)).isoformat()
    assert gap["unlogged_suspected"] is False


def test_none_without_a_configured_cadence(config):
    # No schedule_day means no baseline to judge against, so stay silent rather
    # than invent one.
    _add(config, date.today() - timedelta(days=90))
    config["mowing"] = {"default_provider": "Test Mowing"}
    assert get_mowing_gap(config) is None
