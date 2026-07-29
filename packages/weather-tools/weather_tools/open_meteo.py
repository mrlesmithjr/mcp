"""Open-Meteo archive API client.

Wraps the archive-api.open-meteo.com endpoint. No authentication required.
All functions are synchronous; they use httpx with a reasonable timeout.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
DEFAULT_TIMEOUT = 30  # seconds


def fetch_daily(
    latitude: float,
    longitude: float,
    start_date: str,
    end_date: str,
    variables: list[str],
    timezone: str = "America/New_York",
) -> dict[str, Any]:
    """Fetch daily weather data from the Open-Meteo archive API.

    Returns the raw API response dict on success, or a dict with
    ``{"error": True, "message": "..."}`` on failure.

    Parameters
    ----------
    latitude:
        Location latitude in decimal degrees.
    longitude:
        Location longitude in decimal degrees.
    start_date:
        Start of the date range in YYYY-MM-DD format (inclusive).
    end_date:
        End of the date range in YYYY-MM-DD format (inclusive).
    variables:
        List of Open-Meteo daily variable names, e.g. ``["precipitation_sum"]``.
    timezone:
        IANA timezone string. Defaults to ``"America/New_York"``.
    """
    params: dict[str, Any] = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": start_date,
        "end_date": end_date,
        "daily": ",".join(variables),
        "timezone": timezone,
    }

    logger.debug("Open-Meteo request: %s", params)

    try:
        with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
            response = client.get(ARCHIVE_URL, params=params)
            response.raise_for_status()
            data = response.json()
    except httpx.TimeoutException:
        return {"error": True, "message": "Request to Open-Meteo timed out."}
    except httpx.HTTPStatusError as exc:
        body = exc.response.text[:500]
        return {
            "error": True,
            "message": f"Open-Meteo returned HTTP {exc.response.status_code}: {body}",
        }
    except httpx.RequestError as exc:
        return {"error": True, "message": f"Network error contacting Open-Meteo: {exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": True, "message": f"Unexpected error: {exc}"}

    # The API itself can return an error payload with a 200 status code.
    if "error" in data and data["error"]:
        reason = data.get("reason", "Unknown error from Open-Meteo")
        return {"error": True, "message": reason}

    return data
