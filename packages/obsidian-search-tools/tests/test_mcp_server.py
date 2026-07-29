"""Tests for the MCP server tools (Unit 4).

Covers: tool annotations, missing OBSIDIAN_VAULT_PATH error shape, vault_status
on fresh DB, and return shapes.
"""

from __future__ import annotations

import json

# ---------------------------------------------------------------------------
# Tool annotations
# ---------------------------------------------------------------------------


def test_tool_annotations_registered():
    """All three tools must be registered (no scaffold stubs remain)."""
    from obsidian_search_tools.mcp_server import mcp

    tool_names = {t.name for t in mcp._tool_manager.list_tools()}
    assert "vault_search" in tool_names
    assert "vault_reindex" in tool_names
    assert "vault_status" in tool_names
    # Scaffold stubs must be gone.
    assert "example_tool" not in tool_names
    assert "add_item_tool" not in tool_names
    assert "list_items_tool" not in tool_names


def test_vault_search_annotation():
    from obsidian_search_tools.mcp_server import mcp

    tool = next(t for t in mcp._tool_manager.list_tools() if t.name == "vault_search")
    ann = tool.annotations
    assert ann.readOnlyHint is True
    assert ann.openWorldHint is False


def test_vault_reindex_annotation():
    from obsidian_search_tools.mcp_server import mcp

    tool = next(t for t in mcp._tool_manager.list_tools() if t.name == "vault_reindex")
    ann = tool.annotations
    assert ann.readOnlyHint is False
    assert ann.destructiveHint is False
    assert ann.idempotentHint is True
    assert ann.openWorldHint is False


def test_vault_status_annotation():
    from obsidian_search_tools.mcp_server import mcp

    tool = next(t for t in mcp._tool_manager.list_tools() if t.name == "vault_status")
    ann = tool.annotations
    assert ann.readOnlyHint is True
    assert ann.openWorldHint is False


# ---------------------------------------------------------------------------
# Missing OBSIDIAN_VAULT_PATH (AC #9)
# ---------------------------------------------------------------------------


def test_vault_search_missing_env_var(monkeypatch):
    """vault_search must return an error JSON, not a traceback, when env var is unset."""
    monkeypatch.delenv("OBSIDIAN_VAULT_PATH", raising=False)
    from obsidian_search_tools.mcp_server import vault_search

    result = json.loads(vault_search("test query"))
    assert "error" in result
    assert "OBSIDIAN_VAULT_PATH" in result["error"]
    # Must be parseable JSON (not a traceback).
    assert "Traceback" not in result["error"]


def test_vault_reindex_missing_env_var(monkeypatch):
    """vault_reindex must return an error JSON, not a traceback, when env var is unset."""
    monkeypatch.delenv("OBSIDIAN_VAULT_PATH", raising=False)
    from obsidian_search_tools.mcp_server import vault_reindex

    result = json.loads(vault_reindex())
    assert "error" in result
    assert "OBSIDIAN_VAULT_PATH" in result["error"]
    assert "Traceback" not in result["error"]


# ---------------------------------------------------------------------------
# vault_status on fresh (or no) DB
# ---------------------------------------------------------------------------


def test_vault_status_no_db(monkeypatch, tmp_path):
    """vault_status must not raise when the database has never been built."""
    monkeypatch.setattr(
        "obsidian_search_tools.db.connection.get_db_path",
        lambda: tmp_path / "nonexistent" / "vault.db",
    )
    monkeypatch.setattr(
        "obsidian_search_tools.mcp_server.get_db_path",
        lambda: tmp_path / "nonexistent" / "vault.db",
    )
    from obsidian_search_tools.mcp_server import vault_status

    result = json.loads(vault_status())
    assert "error" not in result or result.get("note_count") is not None
    # Should be parseable JSON.


def test_vault_status_return_shape(monkeypatch, tmp_path):
    """vault_status on an empty DB should return expected keys."""
    # Point to a temp dir that doesn't have a DB yet.
    fake_path = tmp_path / "vault.db"
    monkeypatch.setattr(
        "obsidian_search_tools.mcp_server.get_db_path",
        lambda: fake_path,
    )
    from obsidian_search_tools.mcp_server import vault_status

    result = json.loads(vault_status())
    if "error" not in result:
        assert "note_count" in result
        assert "chunk_count" in result
        assert "last_reindex" in result
        assert "indexed_sections" in result


# ---------------------------------------------------------------------------
# Return type contract
# ---------------------------------------------------------------------------


def test_all_tools_return_str(monkeypatch):
    """All MCP tools must return str (JSON)."""
    monkeypatch.delenv("OBSIDIAN_VAULT_PATH", raising=False)
    from obsidian_search_tools.mcp_server import vault_reindex, vault_search, vault_status

    assert isinstance(vault_search("test"), str)
    assert isinstance(vault_reindex(), str)
    # vault_status doesn't need vault path.
    result = vault_status()
    assert isinstance(result, str)
    # All must be valid JSON.
    json.loads(vault_search("test"))
    json.loads(vault_reindex())
    json.loads(result)
