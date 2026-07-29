"""Tests for unifi-tools MCP server tool annotations.

Registry-only tests: no tool calls, no network, no controller access.
All tests import the mcp object and inspect the tool manager directly.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# MCP tool annotations: read tools, reversible writes, and destructive writes
# ---------------------------------------------------------------------------


class TestToolAnnotations:
    @staticmethod
    def _annotations_by_name() -> dict:
        from unifi.mcp_server import mcp

        return {t.name: t.annotations for t in mcp._tool_manager.list_tools()}

    def test_all_tools_declare_annotations(self):
        annotations = self._annotations_by_name()
        for name, ann in annotations.items():
            assert ann is not None and ann.readOnlyHint is not None, (
                f"{name} is missing tool annotations (readOnlyHint must be set explicitly)"
            )

    @pytest.mark.parametrize(
        "name",
        [
            "device_list",
            "client_list",
            "client_count",
            "device_stats",
            "vlan_list",
            "wlan_list",
            "vpn_status",
            "wan_status",
            "wan_detail",
            "acl_rule_list",
            "traffic_matching_list",
            "firewall_policy_list",
            "firewall_zones",
            "ips_status",
            "ips_suppressed_list",
            "dns_config",
            "dns_policy_list",
            "pending_device_list",
            "dpi_application_list",
            "dpi_category_list",
            "wifi_broadcast_list",
            "wifi_network_list",
            "radio_list",
            "network_list",
            "radius_profile_list",
            "device_tag_list",
            "system_info",
            "switch_port_list",
        ],
    )
    def test_read_tools_are_read_only(self, name):
        ann = self._annotations_by_name()[name]
        assert ann.readOnlyHint is True, f"{name} should be read-only"

    def test_device_restart_is_destructive(self):
        ann = self._annotations_by_name()["device_restart"]
        assert ann.readOnlyHint is False
        assert ann.destructiveHint is True
        assert ann.idempotentHint is False
        assert ann.openWorldHint is False

    @pytest.mark.parametrize("name", ["client_block", "client_unblock"])
    def test_client_block_unblock_are_reversible_and_idempotent(self, name):
        ann = self._annotations_by_name()[name]
        assert ann.readOnlyHint is False
        assert ann.destructiveHint is False
        assert ann.idempotentHint is True
        assert ann.openWorldHint is False

    def test_client_reconnect_is_reversible_not_idempotent(self):
        ann = self._annotations_by_name()["client_reconnect"]
        assert ann.readOnlyHint is False
        assert ann.destructiveHint is False
        assert ann.idempotentHint is False
        assert ann.openWorldHint is False

    @pytest.mark.parametrize("name", ["ips_suppress_destination", "ips_remove_category"])
    def test_ips_write_tools_are_reversible_and_idempotent(self, name):
        ann = self._annotations_by_name()[name]
        assert ann.readOnlyHint is False
        assert ann.destructiveHint is False
        assert ann.idempotentHint is True
        assert ann.openWorldHint is False

    def test_switch_port_action_is_reversible_write(self):
        ann = self._annotations_by_name()["switch_port_action"]
        assert ann.readOnlyHint is False
        assert ann.destructiveHint is False
        assert ann.idempotentHint is False
        assert ann.openWorldHint is False

    def test_all_tools_are_closed_world(self):
        # The spec default for openWorldHint is True; unifi-tools targets only
        # the user's own LAN-local controller, so every tool must override to False.
        for name, ann in self._annotations_by_name().items():
            assert ann.openWorldHint is False, (
                f"{name} has openWorldHint={ann.openWorldHint!r}, expected False (closed-world controller)"
            )
