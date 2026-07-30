"""PrometheusUnreachable replaces a bare ConnectionError on the HVAC path.

A raw requests ConnectionError surfaces as HTTPConnectionPool text naming
localhost:9091, which reads like a Prometheus outage. In practice the far more
common cause is prometheus_url never being set, so the default was used while
Prometheus runs on another host. These tests pin the distinction the message
draws, and that only connection failures are rewritten.

Reuses the FakeSession conventions from test_ha_timeout_retry_batching.py.
"""

import pytest
import requests
from homeops import ha
from homeops.config import DEFAULT_PROMETHEUS_URL


class FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def _reset_session(monkeypatch):
    monkeypatch.setattr(ha, "_session", None)
    yield
    monkeypatch.setattr(ha, "_session", None)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr(ha.time, "sleep", lambda _seconds: None)


def _conn_errors(n):
    return [requests.exceptions.ConnectionError("refused") for _ in range(n)]


def _install(monkeypatch, responses, url):
    monkeypatch.setattr(ha, "_session", FakeSession(responses))
    monkeypatch.setattr(ha, "_prometheus_url", lambda: url)


class TestMessage:
    def test_names_the_url_actually_tried(self, monkeypatch):
        # The raw error names a host the user never chose, which is what made
        # the original failure so easy to misread.
        _install(monkeypatch, _conn_errors(3), "http://192.168.1.5:9091")
        with pytest.raises(ha.PrometheusUnreachable) as exc:
            ha._prom_instant("up")
        assert "http://192.168.1.5:9091" in str(exc.value)

    def test_default_url_says_the_setting_is_unset(self, monkeypatch):
        _install(monkeypatch, _conn_errors(3), DEFAULT_PROMETHEUS_URL)
        with pytest.raises(ha.PrometheusUnreachable) as exc:
            ha._prom_instant("up")
        msg = str(exc.value)
        assert "not set" in msg
        assert "Only use localhost if Prometheus runs on this machine" in msg

    def test_explicit_url_does_not_claim_it_is_unset(self, monkeypatch):
        _install(monkeypatch, _conn_errors(3), "http://prom.example:9091")
        with pytest.raises(ha.PrometheusUnreachable) as exc:
            ha._prom_instant("up")
        assert "not set" not in str(exc.value)

    def test_says_only_hvac_is_affected(self, monkeypatch):
        # 5 of 29 homeops tools touch Prometheus; the rest are pure SQLite. A
        # connection error should not read as homeops being down.
        _install(monkeypatch, _conn_errors(3), DEFAULT_PROMETHEUS_URL)
        with pytest.raises(ha.PrometheusUnreachable) as exc:
            ha._prom_instant("up")
        assert "hvac_" in str(exc.value)

    def test_exposes_the_url_as_an_attribute(self, monkeypatch):
        _install(monkeypatch, _conn_errors(3), "http://prom.example:9091")
        with pytest.raises(ha.PrometheusUnreachable) as exc:
            ha._prom_instant("up")
        assert exc.value.url == "http://prom.example:9091"


class TestScope:
    def test_still_retries_before_giving_up(self, monkeypatch):
        # Rewriting the error must not shortcut the retry/backoff from issue #70.
        session = FakeSession(_conn_errors(3))
        monkeypatch.setattr(ha, "_session", session)
        monkeypatch.setattr(ha, "_prometheus_url", lambda: DEFAULT_PROMETHEUS_URL)
        with pytest.raises(ha.PrometheusUnreachable):
            ha._prom_instant("up")
        assert len(session.calls) == ha.MAX_RETRIES + 1

    def test_a_transient_connection_error_still_recovers(self, monkeypatch):
        ok = type(
            "R",
            (),
            {
                "raise_for_status": lambda self: None,
                "json": lambda self: {"status": "success", "data": {"resultType": "vector", "result": []}},
            },
        )()
        _install(monkeypatch, [requests.exceptions.ConnectionError("refused"), ok], DEFAULT_PROMETHEUS_URL)
        assert ha._prom_instant("up") == []

    def test_timeouts_are_not_rewritten(self, monkeypatch):
        # Only ConnectionError implies a wrong or dead URL. A timeout means
        # something answered, so the original exception is more informative.
        _install(monkeypatch, [requests.exceptions.Timeout("slow")] * 3, DEFAULT_PROMETHEUS_URL)
        with pytest.raises(requests.exceptions.Timeout):
            ha._prom_instant("up")

    def test_query_errors_are_not_rewritten(self, monkeypatch):
        bad = type(
            "R",
            (),
            {"raise_for_status": lambda self: None, "json": lambda self: {"status": "error", "error": "bad query"}},
        )()
        _install(monkeypatch, [bad], DEFAULT_PROMETHEUS_URL)
        with pytest.raises(RuntimeError, match="bad query"):
            ha._prom_instant("up")
