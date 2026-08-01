"""Tests for the Bermuda green-up check (issue #146).

bermuda_greenup_ready() is a pure comparison function (no weather-fetching);
it's tested directly with literal soil-temp floats. The CLI handler's
read-only report output is tested separately (issue #39 - Apple Reminders
creation removed, so `lawnops bermuda-check` now prints its findings
instead), mirroring test_irrigation_check.py.
"""

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
    def test_ready_prints_checklist(self, capsys):
        _cmd_bermuda_check({"soil_temp": 66.0}, _config())

        out = capsys.readouterr().out
        assert "Bermuda green-up check: ready (soil temp 66.0" in out
        assert "Mix rate: 2.5 oz Ortho Weed B-Gon" in out

    def test_not_ready_yet_prints_no_checklist(self, capsys):
        _cmd_bermuda_check({"soil_temp": 50.0}, _config())

        out = capsys.readouterr().out
        assert "not ready yet" in out
        assert "50.0" in out
        assert "Mix rate" not in out
