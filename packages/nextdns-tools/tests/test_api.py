"""Tests for nextdns.api.get_logs device attribution."""

from __future__ import annotations

import pytest
from nextdns import api


@pytest.fixture(autouse=True)
def _no_real_config(monkeypatch):
    # get_logs() builds its URL via _profile_path(), which calls load_config()
    # and requires real credentials. Stub it out so these tests never touch
    # config.json or the network.
    monkeypatch.setattr(api, "_profile_path", lambda *a, **k: "/profiles/test/logs")


def test_get_logs_returns_device_name(monkeypatch):
    monkeypatch.setattr(
        api,
        "_api_get",
        lambda *a, **k: [
            {
                "timestamp": "2026-07-06T04:01:43.590Z",
                "domain": "sessions.bugsnag.com",
                "status": "blocked",
                "reasons": [{"id": "blocklist:nextdns-recommended"}],
                "client": "nextdns-cli",
                "device": {"id": "1SDEP", "name": "Larrys-MBP", "model": "Apple, Inc."},
                "encrypted": True,
            }
        ],
    )

    logs = api.get_logs()

    assert logs == [
        {
            "timestamp": "2026-07-06T04:01:43.590Z",
            "domain": "sessions.bugsnag.com",
            "status": "blocked",
            "reasons": ["blocklist:nextdns-recommended"],
            "device": "Larrys-MBP",
            "encrypted": True,
        }
    ]


def test_get_logs_handles_missing_device(monkeypatch):
    monkeypatch.setattr(
        api,
        "_api_get",
        lambda *a, **k: [
            {"timestamp": "t", "domain": "d", "status": "default", "reasons": [], "client": "nextdns-cli"}
        ],
    )

    logs = api.get_logs()

    assert logs[0]["device"] == ""


def test_get_logs_handles_null_device(monkeypatch):
    monkeypatch.setattr(
        api,
        "_api_get",
        lambda *a, **k: [
            {
                "timestamp": "t",
                "domain": "d",
                "status": "default",
                "reasons": [],
                "client": "nextdns-cli",
                "device": None,
            }
        ],
    )

    logs = api.get_logs()

    assert logs[0]["device"] == ""


def test_get_logs_respects_limit(monkeypatch):
    monkeypatch.setattr(
        api,
        "_api_get",
        lambda *a, **k: [
            {"timestamp": str(i), "domain": "d", "status": "default", "reasons": [], "device": {"name": "X"}}
            for i in range(5)
        ],
    )

    logs = api.get_logs(limit=2)

    assert len(logs) == 2
