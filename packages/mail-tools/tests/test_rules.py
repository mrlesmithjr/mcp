"""Unit tests for mail_tools/rules.py (issue #48).

Covers load_rules/save_rules persistence (mirroring gmail.py's
_load_tokens/_save_tokens pattern), build_category_query's OR-join +
base-filter behavior, and stale_categories's date-elapsed logic with an
explicit mocked as_of date - no live filesystem writes outside a tmp_path.
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from mail_tools import rules as rules_module


def test_load_rules_returns_empty_default_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(rules_module, "RULES_FILE", tmp_path / "sender_rules.json")

    result = rules_module.load_rules()

    assert result == {"version": 1, "categories": []}


def test_save_then_load_round_trips(tmp_path, monkeypatch):
    rules_file = tmp_path / "nested" / "sender_rules.json"
    monkeypatch.setattr(rules_module, "RULES_FILE", rules_file)

    data = {"version": 1, "categories": [{"id": "test-cat", "status": "active"}]}
    rules_module.save_rules(data)

    assert rules_file.exists()
    # mkdir parents=True must have created the nested dir.
    assert rules_file.parent.exists()

    loaded = rules_module.load_rules()
    assert loaded == data


def test_save_rules_writes_trailing_newline(tmp_path, monkeypatch):
    rules_file = tmp_path / "sender_rules.json"
    monkeypatch.setattr(rules_module, "RULES_FILE", rules_file)

    rules_module.save_rules({"version": 1, "categories": []})

    content = rules_file.read_text()
    assert content.endswith("\n")
    # Still valid JSON despite the trailing newline.
    json.loads(content)


def test_save_rules_interrupted_write_leaves_original_untouched(tmp_path, monkeypatch):
    """Simulates a process kill mid-write (Ctrl-C, OOM-kill, host reboot) by
    making json.dump raise partway through the temp-file write. The real
    sender_rules.json must be untouched and still valid - the crash must
    only ever corrupt the abandoned .json.tmp file, never the live one.
    """
    rules_file = tmp_path / "sender_rules.json"
    monkeypatch.setattr(rules_module, "RULES_FILE", rules_file)

    original = {"version": 1, "categories": [{"id": "existing-cat", "status": "active"}]}
    rules_module.save_rules(original)

    monkeypatch.setattr(rules_module.json, "dump", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk full")))

    with pytest.raises(OSError, match="disk full"):
        rules_module.save_rules({"version": 1, "categories": [{"id": "new-cat"}]})

    assert rules_file.exists()
    assert json.loads(rules_file.read_text()) == original
    tmp_file = rules_file.with_suffix(".json.tmp")
    assert not tmp_file.exists()


class TestBuildCategoryQuery:
    def test_joins_senders_with_or_and_wraps_in_parens(self):
        category = {"senders": ["from:amazon.com", "from:target.com"]}

        query = rules_module.build_category_query(category)

        assert query == "(from:amazon.com OR from:target.com) is:unread"

    def test_single_sender_still_wrapped(self):
        category = {"senders": ["from:gestaltit.com"]}

        query = rules_module.build_category_query(category)

        assert query == "(from:gestaltit.com) is:unread"

    def test_custom_base_filter_appended(self):
        category = {"senders": ["from:amazon.com"]}

        query = rules_module.build_category_query(category, base_filter="older_than:1y")

        assert query == "(from:amazon.com) older_than:1y"

    def test_empty_senders_list_raises_instead_of_building_degenerate_query(self):
        """An unguarded empty senders list would produce "() is:unread",
        which Gmail's query parser treats as a no-op filter - collapsing
        to just "is:unread" and matching every unread message in the
        account. This must fail loudly instead.
        """
        category = {"id": "broken-category", "senders": []}

        with pytest.raises(ValueError, match="broken-category"):
            rules_module.build_category_query(category)

    def test_missing_senders_key_raises(self):
        """Same degenerate-query risk as an empty list, but via a category
        dict that omits the `senders` key entirely (e.g. a hand-edited
        sender_rules.json entry missing the field).
        """
        category = {"id": "no-senders-key"}

        with pytest.raises(ValueError, match="no-senders-key"):
            rules_module.build_category_query(category)


class TestStaleCategories:
    def test_flags_category_whose_review_after_days_has_elapsed(self):
        rules = {
            "categories": [
                {
                    "id": "subscription-account-activity",
                    "last_reviewed": "2026-06-01",
                    "review_after_days": 30,
                }
            ]
        }

        stale = rules_module.stale_categories(rules, as_of=date(2026, 7, 15))

        assert [c["id"] for c in stale] == ["subscription-account-activity"]

    def test_does_not_flag_category_within_review_window(self):
        rules = {
            "categories": [
                {
                    "id": "subscription-account-activity",
                    "last_reviewed": "2026-07-01",
                    "review_after_days": 30,
                }
            ]
        }

        stale = rules_module.stale_categories(rules, as_of=date(2026, 7, 15))

        assert stale == []

    def test_ignores_categories_with_null_review_after_days(self):
        rules = {
            "categories": [
                {
                    "id": "professional-contacts-gestaltit",
                    "last_reviewed": "2020-01-01",
                    "review_after_days": None,
                }
            ]
        }

        stale = rules_module.stale_categories(rules, as_of=date(2026, 7, 15))

        assert stale == []

    def test_flags_regardless_of_status(self):
        """stale_categories is purely informational - it must surface
        elapsed categories of ANY status, not just active ones, and must
        never itself mutate anything (it takes no rules-writing path at all).
        """
        rules = {
            "categories": [
                {
                    "id": "financial-statements",
                    "status": "leave_alone",
                    "last_reviewed": "2026-01-01",
                    "review_after_days": 90,
                }
            ]
        }

        stale = rules_module.stale_categories(rules, as_of=date(2026, 7, 15))

        assert [c["id"] for c in stale] == ["financial-statements"]

    def test_exactly_on_due_date_is_not_yet_stale(self):
        rules = {
            "categories": [
                {
                    "id": "cat",
                    "last_reviewed": "2026-06-01",
                    "review_after_days": 30,
                }
            ]
        }

        # 2026-06-01 + 30 days = 2026-07-01 exactly.
        stale = rules_module.stale_categories(rules, as_of=date(2026, 7, 1))

        assert stale == []
