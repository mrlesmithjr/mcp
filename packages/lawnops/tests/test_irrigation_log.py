"""Unit tests for lawnops.db.irrigation_log (issue #76).

Coverage: a DB write failure while logging an irrigation run must be logged
via the module logger, not silently swallowed, and must never propagate --
the irrigation run itself already succeeded on the controller by the time
logging happens.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from lawnops.db.irrigation_log import log_irrigation_run


def test_db_write_failure_logs_warning_and_does_not_raise(caplog):
    config = {"database": {"auto_log": True}}

    mock_conn = MagicMock()
    mock_conn.execute.side_effect = RuntimeError("disk I/O error")

    with patch("lawnops.db.irrigation_log.get_db", return_value=mock_conn):
        with caplog.at_level("WARNING"):
            log_irrigation_run(
                config,
                date="2026-07-02",
                zone_number=1,
                zone_name="Front Left",
                start_time="05:00",
                duration_min=15,
                status="Normal watering",
            )

    assert any("Failed to log irrigation run" in record.message for record in caplog.records)
    assert any("disk I/O error" in record.message for record in caplog.records)


def test_successful_write_does_not_log_warning(caplog):
    config = {"database": {"auto_log": True}}

    mock_conn = MagicMock()

    with patch("lawnops.db.irrigation_log.get_db", return_value=mock_conn):
        with caplog.at_level("WARNING"):
            log_irrigation_run(
                config,
                date="2026-07-02",
                zone_number=1,
                zone_name="Front Left",
                start_time="05:00",
                duration_min=15,
                status="Normal watering",
            )

    assert mock_conn.execute.called
    assert mock_conn.commit.called
    assert mock_conn.close.called
    assert not caplog.records


def test_auto_log_disabled_skips_db_entirely():
    config = {"database": {"auto_log": False}}

    with patch("lawnops.db.irrigation_log.get_db") as mock_get_db:
        log_irrigation_run(
            config,
            date="2026-07-02",
            zone_number=1,
            zone_name="Front Left",
            start_time="05:00",
            duration_min=15,
            status="Normal watering",
        )

    mock_get_db.assert_not_called()
