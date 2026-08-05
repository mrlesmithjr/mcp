"""CLI tests for the `schedule` command group and `reindex --skip-if-fresh` (issue #60)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from typer.testing import CliRunner

from obsidian_search_tools.cli import app

runner = CliRunner()


def test_schedule_show_default(monkeypatch):
    monkeypatch.delenv("OBSIDIAN_REINDEX_TIMES", raising=False)
    monkeypatch.delenv("OBSIDIAN_REINDEX_STALENESS_HOURS", raising=False)
    result = runner.invoke(app, ["schedule", "show"])
    assert result.exit_code == 0
    assert "06:00, 12:00, 18:00" in result.output
    assert "2.0h" in result.output


def test_schedule_show_env_override(monkeypatch):
    monkeypatch.setenv("OBSIDIAN_REINDEX_TIMES", "05:00,23:00")
    monkeypatch.setenv("OBSIDIAN_REINDEX_STALENESS_HOURS", "3")
    result = runner.invoke(app, ["schedule", "show"])
    assert result.exit_code == 0
    assert "05:00, 23:00" in result.output
    assert "3.0h" in result.output


def test_schedule_render_writes_calendar_interval(tmp_path, monkeypatch):
    monkeypatch.setenv("OBSIDIAN_REINDEX_TIMES", "06:00,18:00")
    target = tmp_path / "rendered.plist"
    target.write_text("before\n\t__START_CALENDAR_INTERVAL__\nafter", encoding="utf-8")

    result = runner.invoke(app, ["schedule", "render", str(target)])
    assert result.exit_code == 0

    rendered = target.read_text(encoding="utf-8")
    assert "__START_CALENDAR_INTERVAL__" not in rendered
    assert "<key>StartCalendarInterval</key>" in rendered


def test_schedule_render_missing_target_errors(tmp_path):
    missing = tmp_path / "does-not-exist.plist"
    result = runner.invoke(app, ["schedule", "render", str(missing)])
    assert result.exit_code == 1
    assert "not a file" in result.output


def test_reindex_skip_if_fresh_skips_without_vault_path(tmp_path, monkeypatch):
    """--skip-if-fresh runs before the vault-path check would matter for a fresh index,
    but reindex still requires OBSIDIAN_VAULT_PATH -- this asserts that requirement
    still gates skip-if-fresh, so a misconfigured LaunchAgent errors loudly."""
    monkeypatch.delenv("OBSIDIAN_VAULT_PATH", raising=False)
    result = runner.invoke(app, ["reindex", "--skip-if-fresh"])
    assert result.exit_code == 1
    assert "OBSIDIAN_VAULT_PATH is not set" in result.output


@pytest.fixture
def fixture_vault():
    from pathlib import Path

    vault = Path(__file__).parent / "fixtures" / "vault"
    if not vault.exists():
        pytest.skip("Fixture corpus not present")
    return vault


def test_reindex_skip_if_fresh_skips_recent_index(fixture_vault, tmp_path, monkeypatch):
    from obsidian_search_tools.db import connect

    db_path = tmp_path / "vault.db"
    conn = connect(db_path)
    recent = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    conn.execute("INSERT OR REPLACE INTO index_meta (key, value) VALUES ('last_reindex', ?)", (recent,))
    conn.commit()
    conn.close()

    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(fixture_vault))
    # scheduler.get_last_reindex() does `from obsidian_search_tools.db import
    # get_db_path` at call time, so patching the db module's attribute is
    # what the local import resolves.
    monkeypatch.setattr("obsidian_search_tools.db.get_db_path", lambda: db_path)

    result = runner.invoke(app, ["reindex", "--skip-if-fresh"])
    assert result.exit_code == 0
    assert "Skipping reindex" in result.output
