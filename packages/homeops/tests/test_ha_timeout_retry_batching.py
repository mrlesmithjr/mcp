"""Tests for homeops.ha's bounded timeout, retry/backoff, batched Prometheus
queries, and history diff-reconstruction (issues #70, #143).

Uses a lightweight fake mimicking the slice of requests.Session's interface
ha.py depends on (.get(url, params=, timeout=)) - no real network calls, no
requests-mock dependency. Fake responses are shaped like the Prometheus HTTP
API (`{"status": "success", "data": {"resultType": ..., "result": [...]}}`),
not Home Assistant's REST API.
"""

import pytest
import requests
from homeops import ha


class FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code} error")

    def json(self):
        return self._json_data


def _prom_ok(result, result_type="vector"):
    """Wrap a Prometheus `result` list in a successful API envelope."""
    return FakeResponse({"status": "success", "data": {"resultType": result_type, "result": result}})


class FakeSession:
    """Records every .get() call. `responses` is a list of either a
    FakeResponse to return or an Exception instance to raise, consumed in
    order - one entry per call.
    """

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
    """ha.py caches its session at module level - reset it before and after
    every test so tests never leak state into each other."""
    monkeypatch.setattr(ha, "_session", None)
    yield
    monkeypatch.setattr(ha, "_session", None)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """Retry backoff uses time.sleep() - make it a no-op so retry tests run
    instantly instead of actually waiting."""
    monkeypatch.setattr(ha.time, "sleep", lambda _seconds: None)


def _install_fake_session(monkeypatch, responses):
    fake = FakeSession(responses)
    monkeypatch.setattr(ha, "_session", fake)
    return fake


def _metric(entity, friendly_name=None, **extra_labels):
    labels = {"entity": entity, "friendly_name": friendly_name or entity, "domain": "climate"}
    labels.update(extra_labels)
    return labels


class TestEntityRegexEscaping:
    """Regression coverage for the PromQL 400 'unknown escape sequence' bug
    found during issue #143's live verification: PromQL double-quoted
    strings apply Go string-escaping before the regex is parsed, so
    re.escape()'s single backslash before a literal "." must be doubled to
    survive that pass. Every other test in this file uses a FakeSession
    that returns canned results regardless of the query string sent, so
    none of them would catch a regression here on their own.
    """

    def test_entity_regex_doubles_the_escape_backslash(self):
        result = ha._entity_regex(["climate.living_room"])

        # re.escape(".") produces one backslash before the dot; PromQL
        # needs that backslash doubled to survive its own string-escaping
        # pass before the regex is parsed.
        assert result == "climate" + ("\\" * 2) + ".living_room"

    def test_entity_regex_joins_multiple_entities_with_alternation(self):
        result = ha._entity_regex(["climate.living_room", "climate.bonus_room"])

        expected = "climate" + ("\\" * 2) + ".living_room" + "|" + "climate" + ("\\" * 2) + ".bonus_room"
        assert result == expected

    def test_hvac_status_sends_the_double_escaped_regex_in_its_queries(self, monkeypatch):
        fake = _install_fake_session(monkeypatch, [_prom_ok([]), _prom_ok([]), _prom_ok([]), _prom_ok([])])

        ha.hvac_status()

        expected_fragment = "climate" + ("\\" * 2) + ".living_room"
        mode_query = fake.calls[0]["params"]["query"]
        assert expected_fragment in mode_query


class TestPromGetTimeoutAndRetry:
    def test_passes_bounded_timeout_to_every_request(self, monkeypatch):
        fake = _install_fake_session(monkeypatch, [_prom_ok([])])

        ha._prom_get("/api/v1/query", {"query": "up"})

        assert fake.calls[0]["timeout"] == ha.REQUEST_TIMEOUT_SECONDS

    def test_retries_once_after_a_transient_failure_then_succeeds(self, monkeypatch):
        fake = _install_fake_session(
            monkeypatch,
            [requests.exceptions.ConnectionError("boom"), _prom_ok([])],
        )

        result = ha._prom_get("/api/v1/query", {"query": "up"})

        assert result == {"resultType": "vector", "result": []}
        assert len(fake.calls) == 2

    def test_gives_up_after_max_retries_exhausted(self, monkeypatch):
        fake = _install_fake_session(
            monkeypatch,
            [requests.exceptions.Timeout("slow")] * (ha.MAX_RETRIES + 1),
        )

        with pytest.raises(requests.exceptions.Timeout):
            ha._prom_get("/api/v1/query", {"query": "up"})

        assert len(fake.calls) == ha.MAX_RETRIES + 1

    def test_a_hanging_response_fails_bounded_not_indefinitely(self, monkeypatch):
        """Simulates what `timeout=` actually produces against a truly
        unresponsive (not down, just hanging) Prometheus instance: a
        ReadTimeout on every attempt. The call must fail once MAX_RETRIES is
        exhausted, never hang, and every attempt must still carry the
        bounded timeout.
        """
        fake = _install_fake_session(
            monkeypatch,
            [requests.exceptions.ReadTimeout("hung")] * (ha.MAX_RETRIES + 1),
        )

        with pytest.raises(requests.exceptions.ReadTimeout):
            ha._prom_get("/api/v1/query", {"query": "up"})

        assert all(c["timeout"] == ha.REQUEST_TIMEOUT_SECONDS for c in fake.calls)

    def test_raises_when_prometheus_reports_a_query_error(self, monkeypatch):
        """A malformed PromQL query returns HTTP 200 with status="error" -
        must not be treated as a successful empty result."""
        fake = _install_fake_session(
            monkeypatch,
            [FakeResponse({"status": "error", "error": "bad query", "errorType": "bad_data"})],
        )

        with pytest.raises(RuntimeError, match="bad query"):
            ha._prom_get("/api/v1/query", {"query": "((("})

        assert len(fake.calls) == 1


class TestHvacStatusBatchesQueries:
    def _states_responses(self, mode="cool", current_temp_c=22.2, target_temp_c=22.2, humidity=62):
        """Four instant queries in the order hvac_status() issues them:
        active mode, current temp, target temp, then the combined
        sensor-domain (temp+humidity) query for zone humidity + standalone
        sensors + outdoor.
        """
        mode_result = [
            {"metric": _metric("climate.living_room", mode="off"), "value": [1785070005, "0"]},
            {"metric": _metric("climate.living_room", mode=mode), "value": [1785070005, "1"]},
            {"metric": _metric("climate.master_bedroom", mode="off"), "value": [1785070005, "0"]},
            {"metric": _metric("climate.master_bedroom", mode=mode), "value": [1785070005, "1"]},
            {"metric": _metric("climate.bonus_room", mode="off"), "value": [1785070005, "0"]},
            {"metric": _metric("climate.bonus_room", mode=mode), "value": [1785070005, "1"]},
        ]
        temp_result = [
            {"metric": _metric(eid), "value": [1785070005, str(current_temp_c)]} for eid in ha.CLIMATE_ENTITIES
        ]
        target_result = [
            {"metric": _metric(eid), "value": [1785070005, str(target_temp_c)]} for eid in ha.CLIMATE_ENTITIES
        ]
        sensor_entities = (
            list(ha.ZONE_HUMIDITY_ENTITIES.values())
            + [e["temp"] for e in ha.STANDALONE_SENSOR_ENTITIES.values()]
            + [e["humidity"] for e in ha.STANDALONE_SENSOR_ENTITIES.values()]
            + [ha.OUTDOOR_TEMP_ENTITY, ha.OUTDOOR_HUMIDITY_ENTITY]
        )
        sensor_result = [
            {"metric": _metric(eid, domain="sensor"), "value": [1785070005, str(humidity)]} for eid in sensor_entities
        ]
        return [_prom_ok(mode_result), _prom_ok(temp_result), _prom_ok(target_result), _prom_ok(sensor_result)]

    def test_hvac_status_issues_exactly_four_queries(self, monkeypatch):
        fake = _install_fake_session(monkeypatch, self._states_responses())

        result = ha.hvac_status()

        assert len(fake.calls) == 4
        assert all(c["url"].endswith("/api/v1/query") for c in fake.calls)
        zone_names = {z["name"] for z in result["zones"]}
        assert {"Living Room", "Master Bedroom", "Bonus Room", "Art Room", "Master Bathroom"} <= zone_names

    def test_hvac_status_converts_celsius_to_fahrenheit(self, monkeypatch):
        _install_fake_session(monkeypatch, self._states_responses(current_temp_c=22.2, target_temp_c=21.0))

        result = ha.hvac_status()

        living_room = next(z for z in result["zones"] if z["entity_id"] == "climate.living_room")
        assert living_room["current_temp"] == pytest.approx(72.0, abs=0.01)
        assert living_room["target_temp"] == pytest.approx(69.8, abs=0.01)

    def test_hvac_status_drops_preset_and_fan_mode_and_leaves_high_low_none(self, monkeypatch):
        _install_fake_session(monkeypatch, self._states_responses())

        result = ha.hvac_status()

        living_room = next(z for z in result["zones"] if z["entity_id"] == "climate.living_room")
        assert "preset" not in living_room
        assert "fan_mode" not in living_room
        assert living_room["target_high"] is None
        assert living_room["target_low"] is None

    def test_hvac_status_survives_missing_entity_in_results(self, monkeypatch):
        """A climate entity absent from every Prometheus result (renamed,
        removed, scrape gap) must degrade to 'unknown', not crash the whole
        call.
        """
        fake = _install_fake_session(monkeypatch, [_prom_ok([]), _prom_ok([]), _prom_ok([]), _prom_ok([])])

        result = ha.hvac_status()

        assert len(fake.calls) == 4
        modes = {z["entity_id"]: z["mode"] for z in result["zones"] if z["entity_id"] in ha.CLIMATE_ENTITIES}
        assert all(mode == "unknown" for mode in modes.values())
        assert result["outdoor"]["temp"] is None
        assert result["outdoor"]["humidity"] is None


class TestHvacHistoryBatchesQueries:
    def _mode_series(self, entity, changes):
        """changes: list of (ts, mode) pairs describing the active mode at
        each timestamp. Emits one Prometheus series per distinct mode seen,
        each with value 1 at its active timestamps and 0 elsewhere.
        """
        modes_seen = sorted({mode for _, mode in changes})
        series = []
        for mode in modes_seen:
            values = [[ts, "1" if m == mode else "0"] for ts, m in changes]
            series.append({"metric": _metric(entity, mode=mode), "values": values})
        return series

    def _value_series(self, entity, values):
        return [{"metric": _metric(entity), "values": [[ts, str(v)] for ts, v in values]}]

    def test_hvac_history_issues_exactly_three_range_queries(self, monkeypatch):
        mode_result = self._mode_series("climate.living_room", [(1000, "cool"), (1060, "cool")])
        temp_result = self._value_series("climate.living_room", [(1000, 22.0), (1060, 22.0)])
        target_result = self._value_series("climate.living_room", [(1000, 22.0), (1060, 22.0)])
        fake = _install_fake_session(
            monkeypatch,
            [_prom_ok(mode_result, "matrix"), _prom_ok(temp_result, "matrix"), _prom_ok(target_result, "matrix")],
        )

        ha.hvac_history(hours=24)

        assert len(fake.calls) == 3
        assert all(c["url"].endswith("/api/v1/query_range") for c in fake.calls)

    def test_hvac_history_detects_a_mode_change(self, monkeypatch):
        mode_result = self._mode_series("climate.living_room", [(1000, "cool"), (1060, "heat")])
        temp_result = self._value_series("climate.living_room", [(1000, 22.0), (1060, 21.5)])
        target_result = self._value_series("climate.living_room", [(1000, 22.0), (1060, 22.0)])
        _install_fake_session(
            monkeypatch,
            [_prom_ok(mode_result, "matrix"), _prom_ok(temp_result, "matrix"), _prom_ok(target_result, "matrix")],
        )

        result = ha.hvac_history(hours=24)

        living_room = next(z for z in result["zones"] if z["entity_id"] == "climate.living_room")
        assert living_room["change_count"] == 1
        assert living_room["changes"][0]["type"] == "mode"
        assert living_room["changes"][0]["from"] == "cool"
        assert living_room["changes"][0]["to"] == "heat"
        assert result["summary"]["mode_changes"] == 1
        other_zones = [z for z in result["zones"] if z["entity_id"] != "climate.living_room"]
        assert all(z["change_count"] == 0 for z in other_zones)

    def test_hvac_history_detects_a_temp_change(self, monkeypatch):
        mode_result = self._mode_series("climate.living_room", [(1000, "cool"), (1060, "cool")])
        temp_result = self._value_series("climate.living_room", [(1000, 22.0), (1060, 21.5)])
        target_result = self._value_series("climate.living_room", [(1000, 22.0), (1060, 20.0)])
        _install_fake_session(
            monkeypatch,
            [_prom_ok(mode_result, "matrix"), _prom_ok(temp_result, "matrix"), _prom_ok(target_result, "matrix")],
        )

        result = ha.hvac_history(hours=24)

        living_room = next(z for z in result["zones"] if z["entity_id"] == "climate.living_room")
        assert living_room["change_count"] == 1
        assert living_room["changes"][0]["type"] == "temp"
        assert result["summary"]["temp_adjustments"] == 1

    def test_hvac_history_flags_rapid_changes_as_overrides(self, monkeypatch):
        """Two mode changes inside the same 5-minute window are flagged as
        a likely manual override - same heuristic as before, now bounded to
        Prometheus's 60s scrape resolution."""
        mode_result = self._mode_series(
            "climate.living_room",
            [(1000, "cool"), (1060, "heat"), (1120, "cool")],
        )
        temp_result = self._value_series("climate.living_room", [(1000, 22.0), (1060, 22.0), (1120, 22.0)])
        target_result = self._value_series("climate.living_room", [(1000, 22.0), (1060, 22.0), (1120, 22.0)])
        _install_fake_session(
            monkeypatch,
            [_prom_ok(mode_result, "matrix"), _prom_ok(temp_result, "matrix"), _prom_ok(target_result, "matrix")],
        )

        result = ha.hvac_history(hours=24)

        assert result["summary"]["manual_overrides"] >= 1
        assert any(o["zone"] == "Living Room" for o in result["overrides"])

    def test_hvac_history_no_longer_emits_heat_setpoint_changes(self, monkeypatch):
        """target_high/target_low have no Prometheus source (see
        hvac_status() docstring) - the old 'heat_setpoint' change type must
        never appear in reconstructed history."""
        mode_result = self._mode_series("climate.living_room", [(1000, "heat_cool"), (1060, "heat_cool")])
        temp_result = self._value_series("climate.living_room", [(1000, 22.0), (1060, 22.0)])
        target_result = self._value_series("climate.living_room", [(1000, 22.0), (1060, 22.0)])
        _install_fake_session(
            monkeypatch,
            [_prom_ok(mode_result, "matrix"), _prom_ok(temp_result, "matrix"), _prom_ok(target_result, "matrix")],
        )

        result = ha.hvac_history(hours=24)

        all_types = {c["type"] for z in result["zones"] for c in z["changes"]}
        assert "heat_setpoint" not in all_types


class TestHvacHistoryStepScaling:
    """Prometheus's /api/v1/query_range rejects a query once (end-start)/step
    exceeds roughly 11,000 points per series - a plausible `hours` value on
    an unvalidated MCP tool parameter could hit that at the fixed 60s scrape
    step. hvac_history() must scale its step up for wide windows instead of
    erroring (issue #143 code review).
    """

    def test_history_step_stays_at_scrape_interval_for_a_normal_window(self):
        assert ha._history_step_seconds(24) == ha.SCRAPE_INTERVAL_SECONDS
        # MAX_RANGE_POINTS(10000) * SCRAPE_INTERVAL_SECONDS(60) == 166.67 hours -
        # anything under that keeps full scrape resolution.
        assert ha._history_step_seconds(24 * 6) == ha.SCRAPE_INTERVAL_SECONDS

    def test_history_step_scales_up_to_stay_under_the_point_cap(self):
        hours = 24 * 365  # a full year - would be ~525,600 points at 60s
        step = ha._history_step_seconds(hours)

        assert step > ha.SCRAPE_INTERVAL_SECONDS
        assert (hours * 3600) / step <= ha.MAX_RANGE_POINTS

    def test_hvac_history_passes_the_scaled_step_to_every_range_query(self, monkeypatch):
        hours = 24 * 365
        expected_step = ha._history_step_seconds(hours)
        fake = _install_fake_session(
            monkeypatch,
            [_prom_ok([], "matrix"), _prom_ok([], "matrix"), _prom_ok([], "matrix")],
        )

        result = ha.hvac_history(hours=hours)

        assert len(fake.calls) == 3
        assert all(c["params"]["step"] == expected_step for c in fake.calls)
        # Degrades gracefully (empty history), never raises.
        assert result["hours"] == hours
