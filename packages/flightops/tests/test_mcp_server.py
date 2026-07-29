"""Tests for flightops MCP server tool annotations.

Registry-only: no tool calls, no network access, no SQLite I/O.
All assertions inspect the registered ToolAnnotations via mcp._tool_manager.list_tools().
"""

from __future__ import annotations

import pytest


class TestToolAnnotations:
    @staticmethod
    def _annotations_by_name() -> dict:
        from flightops.mcp_server import mcp

        return {t.name: t.annotations for t in mcp._tool_manager.list_tools()}

    def test_all_tools_declare_annotations(self):
        """Every tool must declare annotations with readOnlyHint explicitly set."""
        annotations = self._annotations_by_name()
        all_tools = {
            "search_one_way",
            "search_round_trip",
            "list_routes",
            "poll_routes",
            "get_price_history",
            "get_price_alerts",
            "add_route",
            "get_search_stats",
        }
        for name in all_tools:
            ann = annotations.get(name)
            assert ann is not None and ann.readOnlyHint is not None, (
                f"{name} is missing tool annotations or readOnlyHint is None"
            )

    @pytest.mark.parametrize("name", ["list_routes", "get_price_history", "get_price_alerts", "get_search_stats"])
    def test_local_read_tools_are_read_only_closed_world(self, name):
        """Local SQLite read tools: readOnlyHint=True, openWorldHint=False."""
        ann = self._annotations_by_name()[name]
        assert ann.readOnlyHint is True, f"{name}: expected readOnlyHint=True"
        assert ann.openWorldHint is False, f"{name}: expected openWorldHint=False (local DB, no network)"

    @pytest.mark.parametrize("name", ["search_one_way", "search_round_trip"])
    def test_external_search_tools_are_read_only_open_world(self, name):
        """Live flight search tools: readOnlyHint=True, openWorldHint=True."""
        ann = self._annotations_by_name()[name]
        assert ann.readOnlyHint is True, f"{name}: expected readOnlyHint=True"
        assert ann.openWorldHint is True, f"{name}: expected openWorldHint=True (queries Google Flights)"

    def test_poll_routes_is_write_open_world_non_destructive(self):
        """poll_routes writes snapshots to local DB AND queries Google Flights."""
        ann = self._annotations_by_name()["poll_routes"]
        assert ann.readOnlyHint is False, "poll_routes writes price snapshots to the DB"
        assert ann.destructiveHint is False, "poll_routes adds snapshots (reversible accumulation)"
        assert ann.openWorldHint is True, "poll_routes reaches Google Flights for live prices"

    def test_add_route_is_write_closed_world_non_destructive(self):
        """add_route writes a local route record only; no external calls."""
        ann = self._annotations_by_name()["add_route"]
        assert ann.readOnlyHint is False, "add_route inserts a route record"
        assert ann.destructiveHint is False, "add_route is reversible (route can be deactivated)"
        assert ann.openWorldHint is False, "add_route only touches local SQLite"
