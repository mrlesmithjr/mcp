"""Tests for imessage-tools MCP server tool annotations.

Registry-only: no tool calls, no DB access, no AppleScript.
Mirrors the TestToolAnnotations pattern from launchd-tools.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# MCP tool annotations: read tools read-only; send_message destructive;
# access_add / access_remove reversible writes; all closed-world.
# ---------------------------------------------------------------------------


class TestToolAnnotations:
    @staticmethod
    def _annotations_by_name() -> dict:
        from imessage_tools.mcp_server import mcp

        return {t.name: t.annotations for t in mcp._tool_manager.list_tools()}

    def test_all_tools_declare_annotations(self):
        annotations = self._annotations_by_name()
        expected_tools = (
            "chat_list",
            "chat_messages",
            "send_message",
            "unread",
            "search_messages",
            "access_list",
            "access_add",
            "access_remove",
        )
        for name in expected_tools:
            ann = annotations.get(name)
            # readOnlyHint must be set explicitly: a bare ToolAnnotations() leaves it None.
            assert ann is not None and ann.readOnlyHint is not None, f"{name} is missing tool annotations"

    @pytest.mark.parametrize("name", ["chat_list", "chat_messages", "unread", "search_messages", "access_list"])
    def test_read_tools_are_read_only(self, name):
        ann = self._annotations_by_name()[name]
        assert ann.readOnlyHint is True

    def test_send_message_is_destructive_open_world(self):
        # send_message dispatches a real message via local Messages.app (AppleScript).
        # The recipient receives it immediately; there is no recall/undo (destructive),
        # and it delivers to an external recipient (open-world, matching mail send).
        ann = self._annotations_by_name()["send_message"]
        assert ann.readOnlyHint is False
        assert ann.destructiveHint is True
        assert ann.idempotentHint is False
        assert ann.openWorldHint is True

    @pytest.mark.parametrize("name", ["access_add", "access_remove"])
    def test_access_write_tools_are_reversible(self, name):
        # access_add and access_remove are inverses of each other (reversible writes).
        # destructiveHint must be False explicitly (spec default is True).
        ann = self._annotations_by_name()[name]
        assert ann.readOnlyHint is False
        assert ann.destructiveHint is False
        assert ann.idempotentHint is True

    def test_local_tools_are_closed_world(self):
        # Reads (chat.db) and allowlist tools (local JSON) are closed-world; the spec
        # default for openWorldHint is true, so each must override it to false. The
        # exception is send_message, which delivers to an external recipient.
        external = {"send_message"}
        for name, ann in self._annotations_by_name().items():
            expected = name in external
            assert ann.openWorldHint is expected, f"{name} openWorldHint should be {expected}"
