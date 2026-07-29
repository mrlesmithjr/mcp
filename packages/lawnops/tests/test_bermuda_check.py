"""Tests for the Bermuda green-up check (issue #146).

bermuda_greenup_ready() is a pure comparison function (no weather-fetching,
no EventKit); it's tested directly with literal soil-temp floats. The CLI
handler's dedup call into lawnops.reminders is tested separately with
RemindersManager mocked, mirroring test_irrigation_check.py.
"""

from unittest.mock import MagicMock, patch

from lawnops.advisory import bermuda_greenup_ready
from lawnops.cli.main import _cmd_bermuda_check


def _config(threshold=None):
    thresholds = {"pre_emergent_soil_temp": 55.0}
    if threshold is not None:
        thresholds["bermuda_greenup_soil_temp"] = threshold
    return {"thresholds": thresholds}


class TestBermudaGreenupReady:
    def test_below_default_threshold_not_ready(self):
        assert bermuda_greenup_ready(64.999, _config()) is False

    def test_at_default_threshold_ready(self):
        assert bermuda_greenup_ready(65.0, _config()) is True

    def test_above_default_threshold_ready(self):
        assert bermuda_greenup_ready(65.1, _config()) is True

    def test_missing_config_key_falls_back_to_65(self):
        """Backward-compat path: configs predating this change have no
        bermuda_greenup_soil_temp key and must still behave like the old
        hardcoded 65°F script."""
        config = _config()
        assert "bermuda_greenup_soil_temp" not in config["thresholds"]

        assert bermuda_greenup_ready(64.9, config) is False
        assert bermuda_greenup_ready(65.0, config) is True

    def test_custom_threshold_is_respected(self):
        config = _config(threshold=70.0)

        assert bermuda_greenup_ready(65.0, config) is False
        assert bermuda_greenup_ready(70.0, config) is True


class TestCmdBermudaCheck:
    @patch("lawnops.reminders.RemindersManager")
    def test_fires_and_creates_reminder(self, mock_manager_cls, capsys):
        mock_manager = MagicMock()
        mock_manager.search_reminders.return_value = []
        mock_manager.create_reminder.return_value = {"id": "new1"}
        mock_manager_cls.return_value = mock_manager

        _cmd_bermuda_check({"soil_temp": 66.0}, _config())

        mock_manager.search_reminders.assert_called_once_with("Bermuda green-up", list_name="Personal")
        mock_manager.create_reminder.assert_called_once()
        args, kwargs = mock_manager.create_reminder.call_args
        assert args[0] == "Bermuda green-up: begin broadleaf weed spray schedule"
        assert kwargs["priority"] == 5
        assert kwargs["due_time"] == "08:00"
        assert "66.0" in kwargs["notes"]
        assert "Mix rate: 2.5 oz Ortho Weed B-Gon" in kwargs["notes"]

        out = capsys.readouterr().out
        assert "reminder created" in out

    @patch("lawnops.reminders.RemindersManager")
    def test_fires_but_already_pending(self, mock_manager_cls, capsys):
        mock_manager = MagicMock()
        mock_manager.search_reminders.return_value = [{"id": "abc", "title": "Bermuda green-up"}]
        mock_manager_cls.return_value = mock_manager

        _cmd_bermuda_check({"soil_temp": 70.0}, _config())

        mock_manager.create_reminder.assert_not_called()
        out = capsys.readouterr().out
        assert "reminder already pending" in out

    @patch("lawnops.reminders.RemindersManager")
    def test_not_ready_yet_no_reminder_call_at_all(self, mock_manager_cls, capsys):
        _cmd_bermuda_check({"soil_temp": 50.0}, _config())

        mock_manager_cls.assert_not_called()
        out = capsys.readouterr().out
        assert "not ready yet" in out
        assert "50.0" in out
