"""Smoke tests for weather-tools MCP server.

These tests call the Open-Meteo archive API via the real implementation.
They are integration tests and require network access.

Open-Meteo is unauthenticated and rate-limits by source IP. On a shared CI runner
that limit is regularly already spent by someone else, so these tests would fail for
reasons that have nothing to do with this code -- and because CI gates the release,
an unrelated red run blocks every merge in the repo.

`_require_live_api` therefore SKIPS when the API is unreachable, timing out, or
rate-limiting, and otherwise lets the failure through. The distinction is drawn on
the error message, which `mcp_server` passes verbatim from `open_meteo`:

  skip  -- timeout, transport error, HTTP 429/5xx, "limit exceeded" reason payload
  fail  -- anything else, including HTTP 4xx for a genuinely bad request

So a real regression still fails. Only infrastructure noise is skipped. Tests that
deliberately assert an error (`test_invalid_date_returns_error`) call the tools
directly and never go through the helper.
"""

from __future__ import annotations

import re

import pytest

from weather_tools.mcp_server import rain_streak, weather_history

# New York, NY coordinates -- used only in tests, not hardcoded in the tool.
LAT = 40.7128
LON = -74.0060

# Messages from open_meteo.py that mean "the service is unavailable to us right now",
# not "the request was wrong".
_TRANSPORT_PREFIXES = (
    "Request to Open-Meteo timed out.",
    "Network error contacting Open-Meteo:",
)
_HTTP_STATUS_RE = re.compile(r"Open-Meteo returned HTTP (\d{3})")


def _require_live_api(result: dict) -> dict:
    """Return `result`, or skip the test if Open-Meteo was unavailable.

    Skips only for infrastructure failures so genuine regressions still fail.
    """
    if result.get("status") != "error":
        return result

    message = str(result.get("message", ""))

    if message.startswith(_TRANSPORT_PREFIXES):
        pytest.skip(f"Open-Meteo unreachable: {message}")

    match = _HTTP_STATUS_RE.search(message)
    if match:
        code = int(match.group(1))
        if code == 429 or code >= 500:
            pytest.skip(f"Open-Meteo unavailable (HTTP {code}): {message}")

    # A 200 response carrying an error payload -- rate limits arrive this way too.
    if "limit exceeded" in message.lower() or "rate limit" in message.lower():
        pytest.skip(f"Open-Meteo rate limit: {message}")

    return result


class TestWeatherHistory:
    """Tests for the weather_history tool."""

    def test_returns_ok_status(self) -> None:
        result = weather_history(
            latitude=LAT,
            longitude=LON,
            start_date="2026-05-20",
            end_date="2026-05-25",
        )
        _require_live_api(result)
        assert result["status"] == "ok"

    def test_default_variable_precipitation_sum(self) -> None:
        result = weather_history(
            latitude=LAT,
            longitude=LON,
            start_date="2026-05-20",
            end_date="2026-05-25",
        )
        _require_live_api(result)
        assert "precipitation_sum" in result
        records = result["precipitation_sum"]
        assert len(records) == 6  # May 20 through May 25 inclusive.
        assert all("date" in r and "value" in r for r in records)

    def test_units_included(self) -> None:
        result = weather_history(
            latitude=LAT,
            longitude=LON,
            start_date="2026-05-20",
            end_date="2026-05-25",
        )
        _require_live_api(result)
        assert "units" in result
        assert "precipitation_sum" in result["units"]

    def test_multiple_variables(self) -> None:
        result = weather_history(
            latitude=LAT,
            longitude=LON,
            start_date="2026-05-20",
            end_date="2026-05-25",
            variables=["precipitation_sum", "temperature_2m_max"],
        )
        _require_live_api(result)
        assert result["status"] == "ok"
        assert "precipitation_sum" in result
        assert "temperature_2m_max" in result

    def test_single_day_returns_one_record(self) -> None:
        """A single-day range returns exactly one well-formed precipitation record."""
        result = weather_history(
            latitude=LAT,
            longitude=LON,
            start_date="2026-05-24",
            end_date="2026-05-24",
        )
        _require_live_api(result)
        assert result["status"] == "ok"
        records = result["precipitation_sum"]
        assert len(records) == 1
        # Precipitation is either a non-negative number or null, never negative.
        value = records[0]["value"]
        assert value is None or value >= 0.0

    def test_invalid_date_returns_error(self) -> None:
        result = weather_history(
            latitude=LAT,
            longitude=LON,
            start_date="2099-01-01",
            end_date="2099-12-31",
        )
        # Future dates have no archive data -- should return error.
        assert result["status"] == "error"
        assert "message" in result


class TestRainStreak:
    """Tests for the rain_streak tool."""

    def test_returns_ok_status(self) -> None:
        result = rain_streak(
            latitude=LAT,
            longitude=LON,
            as_of_date="2026-05-25",
        )
        _require_live_api(result)
        assert result["status"] == "ok"

    def test_return_shape(self) -> None:
        result = rain_streak(
            latitude=LAT,
            longitude=LON,
            as_of_date="2026-05-25",
        )
        _require_live_api(result)
        for key in ("streak_days", "streak_start", "total_mm", "total_inches", "daily", "as_of_date"):
            assert key in result

    def test_daily_list_newest_first(self) -> None:
        result = rain_streak(
            latitude=LAT,
            longitude=LON,
            as_of_date="2026-05-25",
            lookback_days=10,
        )
        _require_live_api(result)
        daily = result["daily"]
        assert len(daily) == 10
        # First entry should be as_of_date (newest first).
        assert daily[0]["date"] == "2026-05-25"

    def test_daily_has_expected_keys(self) -> None:
        result = rain_streak(
            latitude=LAT,
            longitude=LON,
            as_of_date="2026-05-25",
            lookback_days=5,
        )
        _require_live_api(result)
        for entry in result["daily"]:
            assert "date" in entry
            assert "mm" in entry
            assert "inches" in entry
            assert "rained" in entry

    def test_streak_start_consistency(self) -> None:
        """streak_days and streak_start must agree for any location and date."""
        result = rain_streak(
            latitude=LAT,
            longitude=LON,
            as_of_date="2026-05-19",
        )
        _require_live_api(result)
        assert result["status"] == "ok"
        assert isinstance(result["streak_days"], int)
        assert result["streak_days"] >= 0
        # Zero streak means no start date; a positive streak means a start is set.
        assert (result["streak_start"] is None) == (result["streak_days"] == 0)

    def test_invalid_date_returns_error(self) -> None:
        result = rain_streak(
            latitude=LAT,
            longitude=LON,
            as_of_date="not-a-date",
        )
        assert result["status"] == "error"
        assert "message" in result

    def test_total_inches_conversion(self) -> None:
        """total_inches should be approximately total_mm * 0.0393701."""
        result = rain_streak(
            latitude=LAT,
            longitude=LON,
            as_of_date="2026-05-25",
        )
        _require_live_api(result)
        if result["streak_days"] > 0:
            expected_inches = round(result["total_mm"] * 0.0393701, 3)
            assert abs(result["total_inches"] - expected_inches) < 0.001


class TestToolAnnotations:
    """Tool annotations are read from the registry; these need no network."""

    @staticmethod
    def _annotations_by_name() -> dict:
        from weather_tools.mcp_server import mcp

        return {t.name: t.annotations for t in mcp._tool_manager.list_tools()}

    def test_all_tools_declare_annotations(self) -> None:
        annotations = self._annotations_by_name()
        for name in ("weather_history", "rain_streak"):
            ann = annotations.get(name)
            # readOnlyHint must be set explicitly: a bare ToolAnnotations() leaves it None.
            assert ann is not None and ann.readOnlyHint is not None, f"{name} is missing tool annotations"

    def test_tools_are_read_only_and_open_world(self) -> None:
        # Both tools only read, but fetch from the external Open-Meteo archive,
        # so each is read-only and open-world.
        for ann in self._annotations_by_name().values():
            assert ann.readOnlyHint is True
            assert ann.openWorldHint is True
