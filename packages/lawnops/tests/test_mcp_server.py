"""Tests for lawnops MCP server tool annotations.

Registry-only: no tool calls, no network, no DB. Verifies that every registered
tool carries explicit ToolAnnotations with readOnlyHint set, and that tools are
correctly classified by read-only / destructive / open-world hints.
"""

from __future__ import annotations

import pytest


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
