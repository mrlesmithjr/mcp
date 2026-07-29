"""Unit tests for GoogleCalendarClient (issue #59).

Mocks GoogleOAuthClient.request() (and, transitively, urlopen) - no live
network calls, no OAuth. Mirrors mcp-common's test_google_oauth.py
fixture pattern.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from apple_eventkit_tools.google_calendar import GoogleCalendarClient

_CALENDAR_LIST_RESPONSE = {
    "items": [
        {"id": "primary", "summary": "Personal", "primary": True, "accessRole": "owner", "backgroundColor": "#111"},
    ]
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A GoogleCalendarClient with credential/token dirs and config redirected to tmp_path."""
    monkeypatch.setattr("mcp_common.google_oauth.config_dir", lambda name: tmp_path / name)
    (tmp_path / "google").mkdir(parents=True, exist_ok=True)
    (tmp_path / "apple-eventkit-tools").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("apple_eventkit_tools.google_calendar.load_layered_config", lambda tool_name, env_map: {})

    c = GoogleCalendarClient()
    c._tokens = {c.account: {"access_token": "at", "refresh_token": "rt"}}
    return c


def _fake_request_router(routes: dict, default):
    """Build a fake GoogleOAuthClient.request() side_effect that dispatches
    on a substring of the URL, recording every call as (method, url, body).
    """
    calls = []

    def fake_request(account, method, url, body=None, retry=True):
        calls.append((method, url, body))
        for needle, response in routes.items():
            if needle in url:
                return response
        return default

    fake_request.calls = calls
    return fake_request


class TestListCalendars:
    def test_maps_fields_and_tags_source(self, client):
        response = {
            "items": [
                {
                    "id": "primary",
                    "summary": "Personal",
                    "backgroundColor": "#111",
                    "primary": True,
                    "accessRole": "owner",
                },
                {
                    "id": "work@group.calendar.google.com",
                    "summary": "Work Calendar",
                    "backgroundColor": "#222",
                    "accessRole": "reader",
                },
            ]
        }
        with patch.object(client, "request", return_value=response):
            result = client.list_calendars()

        by_title = {c["title"]: c for c in result}
        assert by_title["Personal"]["id"] == "primary"
        assert by_title["Personal"]["is_subscribed"] is False
        assert by_title["Personal"]["allows_modification"] is True
        assert by_title["Personal"]["source"] == "google"
        assert by_title["Work Calendar"]["is_subscribed"] is True
        assert by_title["Work Calendar"]["allows_modification"] is False

    def test_paginates_via_next_page_token(self, client):
        page1 = {"items": [{"id": "a", "summary": "A", "accessRole": "owner"}], "nextPageToken": "tok2"}
        page2 = {"items": [{"id": "b", "summary": "B", "accessRole": "owner"}]}
        responses = iter([page1, page2])

        with patch.object(client, "request", side_effect=lambda *a, **kw: next(responses)) as mock_request:
            result = client.list_calendars()

        assert {c["id"] for c in result} == {"a", "b"}
        assert mock_request.call_count == 2


class TestCalendarTitleCaching:
    def test_known_calendar_names_and_calendar_title_share_one_api_call(self, client):
        with patch.object(client, "request", return_value=_CALENDAR_LIST_RESPONSE) as mock_request:
            names = client.known_calendar_names()
            title = client._calendar_title("primary")

        assert names == {"primary", "personal"}
        assert title == "Personal"
        mock_request.assert_called_once()

    def test_known_calendar_names_falls_back_to_empty_on_oauth_error(self, client):
        from mcp_common.google_oauth import GoogleOAuthError

        with patch.object(client, "request", side_effect=GoogleOAuthError("not authorized")):
            names = client.known_calendar_names()

        assert names == set()


class TestListEvents:
    def test_serializes_and_sorts_by_start(self, client):
        events_response = {
            "items": [
                {
                    "id": "e2",
                    "summary": "Later",
                    "start": {"dateTime": "2026-07-10T14:00:00-04:00"},
                    "end": {"dateTime": "2026-07-10T15:00:00-04:00"},
                },
                {
                    "id": "e1",
                    "summary": "Earlier",
                    "start": {"dateTime": "2026-07-10T09:00:00-04:00"},
                    "end": {"dateTime": "2026-07-10T10:00:00-04:00"},
                },
            ]
        }
        fake_request = _fake_request_router({"calendarList": _CALENDAR_LIST_RESPONSE}, events_response)

        with patch.object(client, "request", side_effect=fake_request):
            events = client.list_events(datetime(2026, 7, 10), datetime(2026, 7, 11))

        assert [e["id"] for e in events] == ["google:e1", "google:e2"]
        assert events[0]["calendar"] == "Personal"
        assert events[0]["source"] == "google"

    def test_all_day_event_has_no_time_component(self, client):
        events_response = {
            "items": [
                {"id": "e1", "summary": "Holiday", "start": {"date": "2026-07-04"}, "end": {"date": "2026-07-05"}}
            ]
        }
        fake_request = _fake_request_router({"calendarList": _CALENDAR_LIST_RESPONSE}, events_response)

        with patch.object(client, "request", side_effect=fake_request):
            events = client.list_events(datetime(2026, 7, 1), datetime(2026, 7, 10))

        assert events[0]["all_day"] is True
        assert events[0]["start"] == "2026-07-04"


class TestCreateEvent:
    def test_builds_body_and_serializes_response(self, client):
        created = {
            "id": "new1",
            "summary": "Meeting",
            "start": {"dateTime": "2026-07-10T09:00:00-04:00"},
            "end": {"dateTime": "2026-07-10T10:00:00-04:00"},
        }
        fake_request = _fake_request_router({"calendarList": _CALENDAR_LIST_RESPONSE}, created)

        with patch.object(client, "request", side_effect=fake_request):
            result = client.create_event(
                title="Meeting",
                start=datetime(2026, 7, 10, 9, 0),
                end=datetime(2026, 7, 10, 10, 0),
                alarm_minutes=15,
                location="Office",
                notes="Discuss roadmap",
            )

        post_calls = [c for c in fake_request.calls if c[0] == "POST"]
        assert len(post_calls) == 1
        body = post_calls[0][2]
        assert body["summary"] == "Meeting"
        assert body["location"] == "Office"
        assert body["description"] == "Discuss roadmap"
        assert body["reminders"]["overrides"] == [{"method": "popup", "minutes": 15}]
        assert result["id"] == "google:new1"

    def test_all_day_event_sends_date_not_datetime(self, client):
        created = {"id": "new1", "summary": "Holiday", "start": {"date": "2026-07-04"}, "end": {"date": "2026-07-05"}}
        fake_request = _fake_request_router({"calendarList": _CALENDAR_LIST_RESPONSE}, created)

        with patch.object(client, "request", side_effect=fake_request):
            client.create_event(title="Holiday", start=datetime(2026, 7, 4), end=datetime(2026, 7, 5), all_day=True)

        post_calls = [c for c in fake_request.calls if c[0] == "POST"]
        body = post_calls[0][2]
        assert body["start"] == {"date": "2026-07-04"}
        assert body["end"] == {"date": "2026-07-05"}


class TestUpdateEvent:
    def test_title_only_update_does_not_fetch_existing_event(self, client):
        updated = {
            "id": "evt1",
            "summary": "New Title",
            "start": {"dateTime": "2026-07-10T09:00:00-04:00"},
            "end": {"dateTime": "2026-07-10T10:00:00-04:00"},
        }
        fake_request = _fake_request_router({"calendarList": _CALENDAR_LIST_RESPONSE}, updated)

        with patch.object(client, "request", side_effect=fake_request):
            client.update_event("evt1", title="New Title")

        get_event_calls = [c for c in fake_request.calls if c[0] == "GET" and "/events/evt1" in c[1]]
        assert get_event_calls == [], "must not fetch the existing event when start/end aren't changing"

    def test_inherits_all_day_from_existing_event_when_not_specified(self, client):
        """Regression test for issue #59 code review MINOR #5: updating
        only start on an existing all-day event must not default
        all_day=False and send a dateTime-typed start against an
        unchanged date-typed end (a type mismatch Google Calendar is
        likely to reject).
        """
        existing_event = {
            "id": "evt1",
            "summary": "Vacation",
            "start": {"date": "2026-07-10"},
            "end": {"date": "2026-07-11"},
        }
        updated_event = {**existing_event, "start": {"date": "2026-07-12"}}

        def fake_request(account, method, url, body=None, retry=True):
            fake_request.calls.append((method, url, body))
            if method == "GET" and "/events/evt1" in url:
                return existing_event
            return updated_event

        fake_request.calls = []

        with patch.object(client, "request", side_effect=fake_request):
            client.update_event("evt1", start=datetime(2026, 7, 12))

        get_calls = [c for c in fake_request.calls if c[0] == "GET" and "/events/evt1" in c[1]]
        assert len(get_calls) == 1, "must fetch the existing event to inherit all_day"

        patch_calls = [c for c in fake_request.calls if c[0] == "PATCH"]
        assert len(patch_calls) == 1
        body = patch_calls[0][2]
        assert "date" in body["start"], "must send a date-typed start to match the existing all-day event"
        assert body["start"] == {"date": "2026-07-12"}

    def test_explicit_all_day_false_skips_existing_event_fetch(self, client):
        """Uses a timezone-AWARE start (fixed UTC-4 offset) rather than a
        naive datetime, so the expected serialized string is deterministic
        regardless of the test runner's system timezone. A naive datetime
        here would be treated as system-local (see _to_rfc3339's
        docstring), which is exactly what made this test pass on a
        machine set to America/New_York and fail in CI on a UTC runner
        (issue #59 code review follow-up).
        """
        updated = {
            "id": "evt1",
            "summary": "Standup",
            "start": {"dateTime": "2026-07-10T09:00:00-04:00"},
            "end": {"dateTime": "2026-07-10T10:00:00-04:00"},
        }

        def fake_request(account, method, url, body=None, retry=True):
            fake_request.calls.append((method, url, body))
            return updated

        fake_request.calls = []

        aware_start = datetime(2026, 7, 10, 9, 0, tzinfo=timezone(timedelta(hours=-4)))

        with patch.object(client, "request", side_effect=fake_request):
            client.update_event("evt1", start=aware_start, all_day=False)

        get_event_calls = [c for c in fake_request.calls if c[0] == "GET" and "/events/evt1" in c[1]]
        assert get_event_calls == [], "an explicit all_day must skip the inherit-from-existing fetch"

        patch_calls = [c for c in fake_request.calls if c[0] == "PATCH"]
        assert patch_calls[0][2]["start"] == {"dateTime": "2026-07-10T09:00:00-04:00"}

    def test_future_events_flag_has_no_effect_on_body(self, client):
        updated = {
            "id": "evt1",
            "summary": "X",
            "start": {"dateTime": "2026-07-10T09:00:00-04:00"},
            "end": {"dateTime": "2026-07-10T10:00:00-04:00"},
        }

        def fake_request(account, method, url, body=None, retry=True):
            fake_request.calls.append((method, url, body))
            return updated

        fake_request.calls = []

        with patch.object(client, "request", side_effect=fake_request):
            client.update_event("evt1", title="X", future_events=True)

        patch_calls = [c for c in fake_request.calls if c[0] == "PATCH"]
        assert patch_calls[0][2] == {"summary": "X"}


class TestDeleteEvent:
    def test_calls_delete_and_returns_bare_id(self, client):
        with patch.object(client, "request", return_value={}) as mock_request:
            result = client.delete_event("evt123")

        assert result == {"deleted": "evt123"}
        call_args = mock_request.call_args.args
        assert call_args[1] == "DELETE"
        assert "evt123" in call_args[2]


class TestSearchEvents:
    def test_uses_q_param_and_caps_at_limit(self, client):
        response = {
            "items": [
                {
                    "id": f"e{i}",
                    "summary": f"Match {i}",
                    "start": {"dateTime": "2026-07-10T09:00:00-04:00"},
                    "end": {"dateTime": "2026-07-10T10:00:00-04:00"},
                }
                for i in range(5)
            ]
        }
        fake_request = _fake_request_router({"calendarList": _CALENDAR_LIST_RESPONSE}, response)

        with patch.object(client, "request", side_effect=fake_request):
            events = client.search_events("Match", start=datetime(2026, 7, 1), end=datetime(2026, 7, 20), limit=3)

        assert len(events) == 3
        search_calls = [c for c in fake_request.calls if c[0] == "GET" and "q=Match" in c[1]]
        assert len(search_calls) == 1


class TestAvailability:
    def test_is_available_false_without_credentials(self, client):
        assert client.is_available() is False

    def test_default_calendar_id(self, client):
        assert client.calendar_id == "primary"

    def test_account_unset_when_no_account_authorized(self, client):
        """No config and no tokens on file -> no account guessed."""
        assert client.account is None

    def test_account_defaults_to_sole_authorized_account(self, tmp_path, monkeypatch):
        """The single authorized account is used when config names none."""
        monkeypatch.setattr("mcp_common.google_oauth.config_dir", lambda name: tmp_path / name)
        (tmp_path / "google").mkdir(parents=True, exist_ok=True)
        tool_dir = tmp_path / "apple-eventkit-tools"
        tool_dir.mkdir(parents=True, exist_ok=True)
        (tool_dir / "google_tokens.json").write_text(
            json.dumps({"someone@example.com": {"access_token": "at", "refresh_token": "rt"}})
        )
        monkeypatch.setattr("apple_eventkit_tools.google_calendar.load_layered_config", lambda tool_name, env_map: {})

        assert GoogleCalendarClient().account == "someone@example.com"

    def test_account_unset_when_several_authorized(self, tmp_path, monkeypatch):
        """Ambiguous multi-account state must not silently pick one."""
        monkeypatch.setattr("mcp_common.google_oauth.config_dir", lambda name: tmp_path / name)
        (tmp_path / "google").mkdir(parents=True, exist_ok=True)
        tool_dir = tmp_path / "apple-eventkit-tools"
        tool_dir.mkdir(parents=True, exist_ok=True)
        (tool_dir / "google_tokens.json").write_text(
            json.dumps(
                {
                    "a@example.com": {"access_token": "at", "refresh_token": "rt"},
                    "b@example.com": {"access_token": "at", "refresh_token": "rt"},
                }
            )
        )
        monkeypatch.setattr("apple_eventkit_tools.google_calendar.load_layered_config", lambda tool_name, env_map: {})

        assert GoogleCalendarClient().account is None

    def test_configured_account_wins_over_token_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("mcp_common.google_oauth.config_dir", lambda name: tmp_path / name)
        (tmp_path / "google").mkdir(parents=True, exist_ok=True)
        tool_dir = tmp_path / "apple-eventkit-tools"
        tool_dir.mkdir(parents=True, exist_ok=True)
        (tool_dir / "google_tokens.json").write_text(
            json.dumps({"sole@example.com": {"access_token": "at", "refresh_token": "rt"}})
        )
        monkeypatch.setattr(
            "apple_eventkit_tools.google_calendar.load_layered_config",
            lambda tool_name, env_map: {"account": "configured@example.com"},
        )

        assert GoogleCalendarClient().account == "configured@example.com"
