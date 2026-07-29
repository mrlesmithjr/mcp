"""Tests for homeops MCP server tool annotations.

Only reads the tool registry -- no tool calls, no network, no DB writes.
"""

from __future__ import annotations

import pytest


class TestToolAnnotations:
    @staticmethod
    def _annotations_by_name() -> dict:
        from homeops.mcp_server import mcp

        return {t.name: t.annotations for t in mcp._tool_manager.list_tools()}

    def test_all_tools_declare_annotations(self):
        annotations = self._annotations_by_name()
        expected_tools = {
            "home_status",
            "task_list",
            "task_overdue",
            "task_history",
            "task_done",
            "task_add",
            "task_pause",
            "task_resume",
            "task_delete",
            "pest_history",
            "pest_add",
            "pest_delete",
            "provider_list",
            "provider_detail",
            "appliance_list",
            "appliance_alerts",
            "utility_summary",
            "utility_trend",
            "utility_add",
            "utility_delete",
            "cost_summary",
            "cost_history",
            "cost_add",
            "cost_delete",
            "budget_overview",
            "sinking_fund_plan",
            "hvac_status",
            "hvac_history",
            "hvac_trend",
            "hvac_mode_distribution",
            "hvac_efficiency",
        }
        for name in expected_tools:
            ann = annotations.get(name)
            assert ann is not None and ann.readOnlyHint is not None, f"{name} is missing tool annotations"

    @pytest.mark.parametrize(
        "name",
        [
            "home_status",
            "task_list",
            "task_overdue",
            "task_history",
            "pest_history",
            "provider_list",
            "provider_detail",
            "appliance_list",
            "appliance_alerts",
            "utility_summary",
            "utility_trend",
            "cost_summary",
            "cost_history",
            "budget_overview",
            "sinking_fund_plan",
            "hvac_status",
            "hvac_history",
            "hvac_trend",
            "hvac_mode_distribution",
            "hvac_efficiency",
        ],
    )
    def test_read_tools_are_read_only(self, name):
        ann = self._annotations_by_name()[name]
        assert ann.readOnlyHint is True, f"{name} should be readOnlyHint=True"

    @pytest.mark.parametrize("name", ["task_done", "task_add", "pest_add", "utility_add", "cost_add"])
    def test_reversible_write_tools(self, name):
        ann = self._annotations_by_name()[name]
        assert ann.readOnlyHint is False, f"{name} should be readOnlyHint=False"
        assert ann.destructiveHint is False, f"{name} should be destructiveHint=False"

    @pytest.mark.parametrize("name", ["task_pause", "task_resume"])
    def test_idempotent_write_tools(self, name):
        ann = self._annotations_by_name()[name]
        assert ann.readOnlyHint is False, f"{name} should be readOnlyHint=False"
        assert ann.destructiveHint is False, f"{name} should be destructiveHint=False"
        assert ann.idempotentHint is True, f"{name} should be idempotentHint=True"

    @pytest.mark.parametrize("name", ["task_delete", "pest_delete", "utility_delete", "cost_delete"])
    def test_delete_tools_are_destructive(self, name):
        ann = self._annotations_by_name()[name]
        assert ann.readOnlyHint is False, f"{name} should be readOnlyHint=False"
        assert ann.destructiveHint is True, f"{name} should be destructiveHint=True"

    def test_all_tools_are_closed_world(self):
        # The spec default for openWorldHint is true; all homeops tools touch only
        # local SQLite state or a local LAN Home Assistant instance (not a remote
        # third-party cloud API), so each must override it to False explicitly.
        for name, ann in self._annotations_by_name().items():
            assert ann.openWorldHint is False, f"{name} should be openWorldHint=False (closed-world)"
