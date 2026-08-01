"""Tests for utility anomaly detection (issue #146).

evaluate_utility_anomalies() is DB-only, so it's tested here directly
against a temp SQLite DB. The CLI command's read-only report output is
tested separately (issue #39 - Apple Reminders creation removed, so
`homeops utility check-anomaly` now prints its findings instead).
"""

import pytest
from homeops.db.utilities import add_utility_bill, evaluate_utility_anomalies


@pytest.fixture()
def tmp_config(tmp_path):
    """Config pointing to a temp DB."""
    db_path = tmp_path / "homeops.db"
    return {
        "database": {"path": str(db_path)},
    }


def _add_months(config, utility_type, amounts, start="2026-01"):
    """Add sequential monthly bills for a utility type, oldest first."""
    year, month = (int(p) for p in start.split("-"))
    for amount in amounts:
        bill_date = f"{year:04d}-{month:02d}"
        add_utility_bill(config, bill_date, utility_type, amount)
        month += 1
        if month > 12:
            month = 1
            year += 1


class TestEvaluateUtilityAnomalies:
    def test_insufficient_history_does_not_fire(self, tmp_config):
        """Fewer than 7 bills on record: skip, no crash, no false positive."""
        _add_months(tmp_config, "electric", [100, 100, 100, 100, 100, 100])  # only 6
        anomalies = evaluate_utility_anomalies(tmp_config)
        assert anomalies == []

    def test_exact_threshold_boundary_does_not_fire(self, tmp_config):
        """Latest bill exactly 20.0% over baseline avg must NOT fire (> not >=)."""
        # Baseline avg = 100; latest = 120.00 is exactly +20%.
        _add_months(tmp_config, "electric", [100, 100, 100, 100, 100, 100, 120.0])
        anomalies = evaluate_utility_anomalies(tmp_config)
        assert anomalies == []

    def test_one_unit_above_threshold_fires(self, tmp_config):
        """Latest bill just over 20% above baseline avg fires."""
        _add_months(tmp_config, "electric", [100, 100, 100, 100, 100, 100, 120.01])
        anomalies = evaluate_utility_anomalies(tmp_config)
        assert len(anomalies) == 1
        result = anomalies[0]
        assert result["type"] == "electric"
        assert result["latest_amount"] == 120.01
        assert result["baseline_avg"] == 100.0
        assert result["pct_over"] > 20.0

    def test_below_threshold_does_not_fire(self, tmp_config):
        """Latest bill well below threshold never fires."""
        _add_months(tmp_config, "electric", [100, 100, 100, 100, 100, 100, 105])
        anomalies = evaluate_utility_anomalies(tmp_config)
        assert anomalies == []

    def test_baseline_excludes_latest_bill(self, tmp_config):
        """Baseline average must be computed from the 6 prior bills only,
        not smoothed by the spike candidate itself."""
        # Baseline (first 6) avg = 100. Latest = 200 (+100%).
        _add_months(tmp_config, "electric", [100, 100, 100, 100, 100, 100, 200])
        anomalies = evaluate_utility_anomalies(tmp_config)
        assert len(anomalies) == 1
        assert anomalies[0]["baseline_avg"] == 100.0
        assert anomalies[0]["latest_amount"] == 200

    def test_multiple_utilities_anomalous_at_once(self, tmp_config):
        """Multiple utility types can be flagged in a single evaluation."""
        _add_months(tmp_config, "electric", [100, 100, 100, 100, 100, 100, 200])
        _add_months(tmp_config, "water", [50, 50, 50, 50, 50, 50, 100])
        _add_months(tmp_config, "gas", [80, 80, 80, 80, 80, 80, 82])  # not anomalous

        anomalies = evaluate_utility_anomalies(tmp_config)
        flagged_types = {a["type"] for a in anomalies}
        assert flagged_types == {"electric", "water"}
        assert len(anomalies) == 2


class TestCmdUtilityCheckAnomaly:
    def test_prints_report_when_anomalies_found(self, tmp_config, capsys):
        from homeops.cli.main import _cmd_utility_check_anomaly

        _add_months(tmp_config, "electric", [100, 100, 100, 100, 100, 100, 200])

        _cmd_utility_check_anomaly(tmp_config)

        out = capsys.readouterr().out
        assert "1 anomaly found (electric)" in out
        assert "electric: latest $200.00 vs baseline avg $100.00" in out
        assert "Check HVAC efficiency, leaks, or rate changes." in out

    def test_prints_no_anomalies_when_none_found(self, tmp_config, capsys):
        from homeops.cli.main import _cmd_utility_check_anomaly

        _add_months(tmp_config, "electric", [100, 100, 100, 100, 100, 100, 105])

        _cmd_utility_check_anomaly(tmp_config)

        out = capsys.readouterr().out
        assert "Utility anomaly check: no anomalies" in out
