"""Tests for reindex LaunchAgent scheduling (issue #60)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from obsidian_search_tools.config import (
    DEFAULT_REINDEX_STALENESS_HOURS,
    DEFAULT_REINDEX_TIMES,
    get_reindex_staleness_hours,
    get_reindex_times,
    parse_reindex_times,
)
from obsidian_search_tools.scheduler import (
    get_last_reindex,
    is_stale,
    render_calendar_interval_xml,
    render_plist,
)

# ---------------------------------------------------------------------------
# config.parse_reindex_times / get_reindex_times
# ---------------------------------------------------------------------------


def test_parse_reindex_times_valid():
    assert parse_reindex_times("06:00,12:00,18:00") == ["06:00", "12:00", "18:00"]


def test_parse_reindex_times_strips_whitespace_and_dedupes():
    assert parse_reindex_times(" 06:00 , 06:00, 23:59 ") == ["06:00", "23:59"]


@pytest.mark.parametrize("bad", ["25:00", "06:60", "6:00pm", "not-a-time", "6", ""])
def test_parse_reindex_times_rejects_malformed(bad):
    if bad == "":
        with pytest.raises(ValueError, match="No valid times"):
            parse_reindex_times(bad)
    else:
        with pytest.raises(ValueError, match="Invalid reindex time"):
            parse_reindex_times(bad)


def test_get_reindex_times_default_when_unset(monkeypatch):
    monkeypatch.delenv("OBSIDIAN_REINDEX_TIMES", raising=False)
    assert get_reindex_times() == list(DEFAULT_REINDEX_TIMES)


def test_get_reindex_times_env_override(monkeypatch):
    monkeypatch.setenv("OBSIDIAN_REINDEX_TIMES", "05:00,11:00,17:00,23:00")
    assert get_reindex_times() == ["05:00", "11:00", "17:00", "23:00"]


def test_get_reindex_times_falls_back_on_invalid_env(monkeypatch):
    monkeypatch.setenv("OBSIDIAN_REINDEX_TIMES", "garbage")
    assert get_reindex_times() == list(DEFAULT_REINDEX_TIMES)


def test_get_reindex_staleness_hours_default(monkeypatch):
    monkeypatch.delenv("OBSIDIAN_REINDEX_STALENESS_HOURS", raising=False)
    assert get_reindex_staleness_hours() == DEFAULT_REINDEX_STALENESS_HOURS


def test_get_reindex_staleness_hours_env_override(monkeypatch):
    monkeypatch.setenv("OBSIDIAN_REINDEX_STALENESS_HOURS", "4.5")
    assert get_reindex_staleness_hours() == 4.5


@pytest.mark.parametrize("bad", ["0", "-1", "not-a-number"])
def test_get_reindex_staleness_hours_falls_back_on_invalid(monkeypatch, bad):
    monkeypatch.setenv("OBSIDIAN_REINDEX_STALENESS_HOURS", bad)
    assert get_reindex_staleness_hours() == DEFAULT_REINDEX_STALENESS_HOURS


# ---------------------------------------------------------------------------
# scheduler.render_calendar_interval_xml / render_plist
# ---------------------------------------------------------------------------


def test_render_calendar_interval_xml_one_entry_per_time():
    xml = render_calendar_interval_xml(["06:00", "18:30"])
    assert xml.count("<dict>") == 2
    assert "<key>StartCalendarInterval</key>" in xml
    assert "<integer>6</integer>" in xml
    assert "<integer>0</integer>" in xml
    assert "<integer>18</integer>" in xml
    assert "<integer>30</integer>" in xml


def test_render_plist_substitutes_placeholder():
    template = "before\n\t__START_CALENDAR_INTERVAL__\nafter"
    rendered = render_plist(template, ["06:00"])
    assert "__START_CALENDAR_INTERVAL__" not in rendered
    assert "before" in rendered and "after" in rendered
    assert "<key>StartCalendarInterval</key>" in rendered


def test_render_plist_produces_valid_xml_structure(tmp_path):
    """Guards against the shipped placeholder ever reaching launchd unrendered."""
    plist_path = (
        __import__("pathlib").Path(__file__).parent.parent / "launchagents" / "com.obsidian-search-tools.reindex.plist"
    )
    text = plist_path.read_text()
    assert "__START_CALENDAR_INTERVAL__" in text, "shipped template must still carry the placeholder"
    rendered = render_plist(text.replace("__HOME__", str(tmp_path)), ["06:00", "12:00", "18:00"])
    assert "__HOME__" not in rendered
    assert "__START_CALENDAR_INTERVAL__" not in rendered
    # 1 root dict + 3 StartCalendarInterval entries + 1 EnvironmentVariables dict
    assert rendered.count("<dict>") == 5
    assert "<key>RunAtLoad</key>" in rendered
    assert "<true/>" in rendered


# ---------------------------------------------------------------------------
# scheduler.is_stale / get_last_reindex
# ---------------------------------------------------------------------------


def test_is_stale_none_last_reindex():
    assert is_stale(None, 2.0) is True


def test_is_stale_unparseable_last_reindex():
    assert is_stale("not-a-timestamp", 2.0) is True


def test_is_stale_recent_is_not_stale():
    recent = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    assert is_stale(recent, 2.0) is False


def test_is_stale_old_is_stale():
    old = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    assert is_stale(old, 2.0) is True


def test_is_stale_naive_timestamp_does_not_crash():
    # indexer.py always writes tz-aware UTC, but is_stale should not crash on
    # a naive timestamp from some other source (the exact staleness verdict
    # depends on the local UTC offset, so only assert it returns a bool).
    naive_recent = datetime.now().isoformat()
    assert isinstance(is_stale(naive_recent, 2.0), bool)


def test_is_stale_boundary_uses_greater_or_equal(monkeypatch):
    """is_stale is documented as `>=`: a timestamp exactly staleness_hours old
    must count as stale, and one second younger must not. `datetime.now()` is
    frozen (via a scheduler-local subclass) so the two branches are compared
    against the same fixed instant instead of real elapsed wall-clock time,
    which would otherwise always tip slightly past the threshold."""
    fixed_now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz is not None else fixed_now.replace(tzinfo=None)

    monkeypatch.setattr("obsidian_search_tools.scheduler.datetime", _FrozenDatetime)

    exactly_at_threshold = (fixed_now - timedelta(hours=2)).isoformat()
    assert is_stale(exactly_at_threshold, 2.0) is True

    one_second_under_threshold = (fixed_now - timedelta(hours=2) + timedelta(seconds=1)).isoformat()
    assert is_stale(one_second_under_threshold, 2.0) is False


def test_get_last_reindex_missing_db(tmp_path):
    assert get_last_reindex(tmp_path / "does-not-exist.db") is None


def test_get_last_reindex_after_build(tmp_path):
    fixture_vault = __import__("pathlib").Path(__file__).parent / "fixtures" / "vault"
    if not fixture_vault.exists():
        pytest.skip("Fixture corpus not present")

    from obsidian_search_tools.indexer import build_index

    db_path = tmp_path / "vault.db"
    build_index(fixture_vault, excluded=set(), db_path=db_path)
    last_reindex = get_last_reindex(db_path)
    assert last_reindex is not None
    assert is_stale(last_reindex, 2.0) is False
