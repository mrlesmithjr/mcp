"""Tests for nextdns-tools MCP server annotations.

Registry-only tests: no tool calls, no network access.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# MCP tool annotations: read tools read-only; write tools reversible
# ---------------------------------------------------------------------------


class TestToolAnnotations:
    @staticmethod
    def _annotations_by_name() -> dict:
        from nextdns.mcp_server import mcp

        return {t.name: t.annotations for t in mcp._tool_manager.list_tools()}

    def test_all_tools_declare_annotations(self):
        annotations = self._annotations_by_name()
        expected_tools = (
            "dns_status",
            "dns_blocked",
            "dns_devices",
            "dns_security",
            "dns_logs",
            "dns_profile",
            "dns_allowlist",
            "dns_allowlist_add",
            "dns_allowlist_remove",
            "dns_denylist_add",
            "dns_export",
        )
        for name in expected_tools:
            ann = annotations.get(name)
            assert ann is not None and ann.readOnlyHint is not None, f"{name} is missing tool annotations"

    @pytest.mark.parametrize(
        "name",
        [
            "dns_status",
            "dns_blocked",
            "dns_devices",
            "dns_security",
            "dns_logs",
            "dns_profile",
            "dns_allowlist",
            "dns_export",
        ],
    )
    def test_read_tools_are_read_only(self, name):
        ann = self._annotations_by_name()[name]
        assert ann.readOnlyHint is True

    @pytest.mark.parametrize(
        "name",
        [
            "dns_allowlist_add",
            "dns_allowlist_remove",
            "dns_denylist_add",
        ],
    )
    def test_write_tools_are_not_read_only(self, name):
        ann = self._annotations_by_name()[name]
        assert ann.readOnlyHint is False

    @pytest.mark.parametrize(
        "name",
        [
            "dns_allowlist_add",
            "dns_allowlist_remove",
            "dns_denylist_add",
        ],
    )
    def test_write_tools_are_reversible_not_destructive(self, name):
        ann = self._annotations_by_name()[name]
        assert ann.destructiveHint is False
        assert ann.idempotentHint is True

    def test_all_tools_are_open_world(self):
        # Every nextdns-tools tool calls the NextDNS cloud API, so all must
        # be open-world. The spec default for openWorldHint is true, but the
        # convention requires an explicit declaration on every tool.
        for name, ann in self._annotations_by_name().items():
            assert ann.openWorldHint is True, f"{name} must declare openWorldHint=True (calls NextDNS cloud API)"
