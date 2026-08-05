"""Tests for lawnops MCP server tool annotations.

Registry-only: no tool calls, no network, no DB. Verifies that every registered
tool carries explicit ToolAnnotations with readOnlyHint set, and that tools are
correctly classified by read-only / destructive / open-world hints.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest


def _fake_zone(number, name="Zone", suspended=False, watering_adjustment=100):
    """Minimal fake pydrawise Zone covering every attribute _serialize_irrigation_status reads."""
    from datetime import datetime

    sched = SimpleNamespace(current_run=None, next_run=None, summary=None)
    suspensions = [SimpleNamespace(end_time=datetime(2026, 1, 1, 6, 0))] if suspended else []
    return SimpleNamespace(
        number=SimpleNamespace(value=number),
        name=name,
        scheduled_runs=sched,
        suspensions=suspensions,
        watering_settings=SimpleNamespace(fixed_watering_adjustment=watering_adjustment),
    )


def _fake_ctrl(zones, name="Controller", online=True):
    return SimpleNamespace(
        zones=zones,
        name=name,
        online=online,
        software_version="1.0",
        status=None,
        last_contact_time=None,
    )


def _fake_program(zones, name="Main Lawn"):
    return {"name": name, "start": "06:00", "period": 2, "monthly": [100] * 12, "zones": zones}


class TestSerializeIrrigationStatusSchedulingNote:
    """Unit #53: scheduling_note is live-state-derived and additive-only."""

    def test_active_program_gets_no_scheduling_note(self):
        from lawnops.mcp_server import _serialize_irrigation_status

        ctrl = _fake_ctrl([_fake_zone(1, suspended=False)])
        programs = {1: _fake_program([1])}

        result = _serialize_irrigation_status(ctrl, [], programs)

        assert "scheduling_note" not in result

    def test_all_zones_suspended_gets_scheduling_note(self):
        from lawnops.mcp_server import _serialize_irrigation_status

        ctrl = _fake_ctrl([_fake_zone(1, suspended=True)])
        programs = {1: _fake_program([1])}

        result = _serialize_irrigation_status(ctrl, [], programs)

        assert "scheduling_note" in result
        assert "suspended" in result["scheduling_note"]

    def test_no_programs_configured_gets_scheduling_note(self):
        from lawnops.mcp_server import _serialize_irrigation_status

        ctrl = _fake_ctrl([_fake_zone(1, suspended=False)])

        result = _serialize_irrigation_status(ctrl, [], {})

        assert "scheduling_note" in result
        assert "No Hydrawise program" in result["scheduling_note"]

    def test_partial_suspension_within_program_gets_no_scheduling_note(self):
        """One unsuspended program zone is enough to count as active."""
        from lawnops.mcp_server import _serialize_irrigation_status

        ctrl = _fake_ctrl([_fake_zone(1, suspended=True), _fake_zone(2, suspended=False)])
        programs = {1: _fake_program([1, 2])}

        result = _serialize_irrigation_status(ctrl, [], programs)

        assert "scheduling_note" not in result


class TestIrrigationHistorySkipGating:
    """Unit #53: irrigation_history's skip entries are gated on active_program."""

    @patch("lawnops.db.irrigation_log.sync_skipped_runs")
    @patch("lawnops.db.irrigation_log.get_skip_history_from_db")
    @patch("lawnops.db.irrigation_log.get_history_from_db")
    @patch("lawnops.db.irrigation_log.sync_irrigation")
    def test_no_skip_entries_when_inactive(
        self, mock_sync_irrigation, mock_get_history, mock_get_skip_history, mock_sync_skipped
    ):
        from lawnops.mcp_server import irrigation_history

        mock_sync_irrigation.return_value = (0, 2)
        mock_get_history.return_value = []
        mock_sync_skipped.return_value = (0, False)
        mock_get_skip_history.return_value = [{"type": "skip", "expected_date": "2026-07-01"}]

        result = json.loads(irrigation_history(days=7))

        assert result["active_program"] is False
        assert result["skips"] == 0
        assert all(e.get("type") != "skip" for e in result["entries"])
        mock_get_skip_history.assert_not_called()

    @patch("lawnops.db.irrigation_log.sync_skipped_runs")
    @patch("lawnops.db.irrigation_log.get_skip_history_from_db")
    @patch("lawnops.db.irrigation_log.get_history_from_db")
    @patch("lawnops.db.irrigation_log.sync_irrigation")
    def test_skip_entries_present_when_active(
        self, mock_sync_irrigation, mock_get_history, mock_get_skip_history, mock_sync_skipped
    ):
        from lawnops.mcp_server import irrigation_history

        mock_sync_irrigation.return_value = (0, 2)
        mock_get_history.return_value = []
        mock_sync_skipped.return_value = (1, True)
        mock_get_skip_history.return_value = [{"type": "skip", "expected_date": "2026-07-01"}]

        result = json.loads(irrigation_history(days=7))

        assert result["active_program"] is True
        assert result["skips"] == 1
        assert any(e.get("type") == "skip" for e in result["entries"])
        mock_get_skip_history.assert_called_once()

    @patch("lawnops.db.irrigation_log.sync_skipped_runs")
    @patch("lawnops.db.irrigation_log.get_skip_history_from_db")
    @patch("lawnops.db.irrigation_log.get_history_from_db")
    @patch("lawnops.db.irrigation_log.sync_irrigation")
    def test_fails_closed_when_active_state_cannot_be_determined(
        self, mock_sync_irrigation, mock_get_history, mock_get_skip_history, mock_sync_skipped
    ):
        """Controller unreachable during sync_skipped_runs -> no skips asserted."""
        from lawnops.mcp_server import irrigation_history

        mock_sync_irrigation.side_effect = Exception("controller unreachable")
        mock_get_history.return_value = []
        mock_sync_skipped.side_effect = RuntimeError("controller unreachable")

        result = json.loads(irrigation_history(days=7))

        assert result["active_program"] is False
        assert result["skips"] == 0
        mock_get_skip_history.assert_not_called()


class TestToolAnnotations:
    @staticmethod
    def _annotations_by_name() -> dict:
        from lawnops.mcp_server import mcp

        return {t.name: t.annotations for t in mcp._tool_manager.list_tools()}

    def test_all_tools_declare_annotations(self):
        """Every registered tool must have annotations with readOnlyHint explicitly set."""
        annotations = self._annotations_by_name()
        for name, ann in annotations.items():
            assert ann is not None and ann.readOnlyHint is not None, (
                f"{name} is missing tool annotations or readOnlyHint is not set"
            )

    # ── Read-only tools (readOnlyHint=True) ────────────────────────────────

    @pytest.mark.parametrize(
        "name",
        [
            # Weather / external reads
            "soil_temp_now",
            "soil_temp_trend",
            "pre_emergent_advisory",
            "spray_advisory",
            "fertilizer_recommendation",
            "application_window",
            # Pollen reads
            "pollen_now",
            "pollen_trend",
            # Inventory / DB reads
            "product_list",
            "product_alerts",
            "treatment_list",
            "mowing_summary",
            "equipment_list",
            "spend_report",
            # Calculators (local config only)
            "coverage_calculator",
            "mix_calculator",
            # Irrigation reads
            "irrigation_status",
            "irrigation_history",
            "irrigation_pace",
            "irrigation_budget",
            "irrigation_program_list",
            "irrigation_diff",
            # Water & efficiency reads
            "water_usage_report",
            "et_recommendations",
            "zone_analysis",
        ],
    )
    def test_read_tools_are_read_only(self, name):
        ann = self._annotations_by_name()[name]
        assert ann.readOnlyHint is True, f"{name}: expected readOnlyHint=True, got {ann.readOnlyHint}"

    # ── Destructive tools (delete operations) ──────────────────────────────

    @pytest.mark.parametrize(
        "name",
        [
            "product_delete",
            "mowing_delete",
            "treatment_delete",
            "purchase_delete",
            "equipment_delete",
        ],
    )
    def test_delete_tools_are_destructive(self, name):
        ann = self._annotations_by_name()[name]
        assert ann.readOnlyHint is False, f"{name}: expected readOnlyHint=False"
        assert ann.destructiveHint is True, f"{name}: expected destructiveHint=True"

    # ── Reversible write tools (non-destructive) ───────────────────────────

    @pytest.mark.parametrize(
        "name",
        [
            # Local DB writes
            "product_add",
            "product_update",
            "mowing_add",
            "treatment_add",
            "purchase_add",
            "equipment_add",
            "irrigation_budget_update",
            # Hydrawise actuation (reversible)
            "irrigation_run_zone",
            "irrigation_run_all",
            "irrigation_stop",
            "irrigation_suspend",
            "irrigation_resume",
            "irrigation_program_update",
            "irrigation_export",
            "irrigation_apply",
            "irrigation_zone_update",
        ],
    )
    def test_reversible_write_tools_are_not_destructive(self, name):
        ann = self._annotations_by_name()[name]
        assert ann.readOnlyHint is False, f"{name}: expected readOnlyHint=False"
        assert ann.destructiveHint is False, f"{name}: expected destructiveHint=False"

    # ── Open-world tools (external HTTP calls) ─────────────────────────────

    @pytest.mark.parametrize(
        "name",
        [
            # Open-Meteo weather API
            "soil_temp_now",
            "soil_temp_trend",
            "pre_emergent_advisory",
            "spray_advisory",
            "fertilizer_recommendation",
            "application_window",
            # Pollen external API
            "pollen_now",
            # Hydrawise cloud API (read)
            "irrigation_status",
            "irrigation_history",
            "irrigation_program_list",
            "irrigation_diff",
            # Hydrawise cloud API (write)
            "irrigation_run_zone",
            "irrigation_run_all",
            "irrigation_stop",
            "irrigation_suspend",
            "irrigation_resume",
            "irrigation_program_update",
            "irrigation_export",
            "irrigation_apply",
            "irrigation_zone_update",
        ],
    )
    def test_external_tools_are_open_world(self, name):
        ann = self._annotations_by_name()[name]
        assert ann.openWorldHint is True, f"{name}: expected openWorldHint=True"

    # ── Closed-world tools (local DB / config only) ────────────────────────

    @pytest.mark.parametrize(
        "name",
        [
            "pollen_trend",
            "product_list",
            "product_add",
            "product_update",
            "product_delete",
            "product_alerts",
            "mowing_add",
            "mowing_delete",
            "mowing_summary",
            "treatment_add",
            "treatment_delete",
            "treatment_list",
            "purchase_add",
            "purchase_delete",
            "equipment_list",
            "equipment_add",
            "equipment_delete",
            "spend_report",
            "coverage_calculator",
            "mix_calculator",
            "irrigation_pace",
            "irrigation_budget",
            "irrigation_budget_update",
            "water_usage_report",
            "et_recommendations",
            "zone_analysis",
        ],
    )
    def test_local_tools_are_closed_world(self, name):
        ann = self._annotations_by_name()[name]
        assert ann.openWorldHint is False, f"{name}: expected openWorldHint=False"

    # ── Idempotent tools ───────────────────────────────────────────────────

    @pytest.mark.parametrize("name", ["irrigation_stop", "irrigation_resume"])
    def test_idempotent_tools(self, name):
        ann = self._annotations_by_name()[name]
        assert ann.idempotentHint is True, f"{name}: expected idempotentHint=True"
