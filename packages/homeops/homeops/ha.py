"""Prometheus client for HomeOps HVAC integration.

Home Assistant exports climate/thermostat metrics to Prometheus (30-day
retention) independently of homeops. Querying Prometheus instead of Home
Assistant's own REST API keeps homeops and Home Assistant fully decoupled -
neither system depends on the other in either direction (issue #143).

Prometheus base URL loaded from config.json or environment variables; no
authentication required (same trusted-network assumption unpoller/Grafana
already rely on for this Prometheus instance).
"""

import logging
import math
import re
import time
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

_session = None

# A hung (not down, just unresponsive) Prometheus instance must fail fast
# rather than block indefinitely - this runs both on-demand via the MCP tool
# and hourly via the com.homeops.hvac-snapshot LaunchAgent (issue #70,
# carried forward unchanged when the query target moved from Home Assistant
# to Prometheus in issue #143).
REQUEST_TIMEOUT_SECONDS = 10
MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = 1

# Prometheus scrape interval configured for the `homeassistant` job (verified
# live against http://localhost:9091/api/v1/status/config during issue
# #143). hvac_history()'s manual-override heuristic and range-query step both
# key off this value.
SCRAPE_INTERVAL_SECONDS = 60

# Prometheus's /api/v1/query_range rejects a query once (end-start)/step
# exceeds roughly 11,000 points per series. hvac_history() is an MCP tool
# that takes an unvalidated `hours` int, so a wide-enough window at the
# fixed 60s scrape interval (e.g. hours >= ~183) would hit that limit and
# surface as an opaque Prometheus error instead of degrading gracefully -
# code review on issue #143's initial commit caught this as a new failure
# mode the old REST-based history endpoint didn't have. Kept well under the
# real cap as a safety margin.
MAX_RANGE_POINTS = 10000

# Climate entities for the 3-zone Ecobee system. These match Prometheus's
# `entity` label on `homeassistant_climate_*` metrics exactly - confirmed
# live during issue #143 (the HA Prometheus exporter carries the real
# entity_id under a label named `entity`, not `entity_id`).
CLIMATE_ENTITIES = [
    "climate.living_room",
    "climate.master_bedroom",
    "climate.bonus_room",
]

ZONE_NAMES = {
    "climate.living_room": "Living Room",
    "climate.master_bedroom": "Master Bedroom",
    "climate.bonus_room": "Bonus Room",
}

# Per-zone humidity is not exported on the climate-domain metrics at all
# (confirmed live: no homeassistant_climate_*humidity* series exists). It
# comes from each thermostat's own sensor-domain humidity entity instead.
ZONE_HUMIDITY_ENTITIES = {
    "climate.living_room": "sensor.living_room_thermostat_current_humidity",
    "climate.master_bedroom": "sensor.master_bedroom_thermostat_current_humidity",
    "climate.bonus_room": "sensor.bonus_room_thermostat_current_humidity",
}

OUTDOOR_TEMP_ENTITY = "sensor.openweathermap_temperature"
OUTDOOR_HUMIDITY_ENTITY = "sensor.openweathermap_humidity"

# Standalone temp+humidity sensors (non-thermostat rooms)
STANDALONE_SENSOR_ENTITIES = {
    "Art Room": {
        "temp": "sensor.art_room_temperature_sensor",
        "humidity": "sensor.art_room_temperature_sensor_humidity_4",
    },
    "Master Bathroom": {
        "temp": "sensor.master_bathroom_temperature_sensor",
        "humidity": "sensor.master_bathroom_humidity_sensor",
    },
}


def _prometheus_url():
    """Load the Prometheus base URL from config.

    load_config() already strips any trailing slash before caching, so no
    need to repeat that here - config.py is the single place that owns
    normalizing this value.
    """
    from homeops.config import DEFAULT_PROMETHEUS_URL, load_config

    config = load_config()
    return config.get("prometheus_url") or DEFAULT_PROMETHEUS_URL


def _get_session():
    """Get the shared requests session (no auth required)."""
    global _session
    if _session is None:
        _session = requests.Session()
    return _session


def _prom_get(path, params):
    """Make a GET request to the Prometheus HTTP API with a bounded timeout
    and a short retry/backoff for transient failures (issue #70).

    Retries MAX_RETRIES times (so up to MAX_RETRIES + 1 attempts total) on
    any requests exception - timeout, connection error, or a raised HTTP
    status - before giving up and letting the exception propagate. Also
    raises if Prometheus itself reports a query error (status != "success").
    """
    s = _get_session()
    url = f"{_prometheus_url()}{path}"
    attempt = 0
    while True:
        try:
            r = s.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
            r.raise_for_status()
            body = r.json()
            if body.get("status") != "success":
                raise RuntimeError(f"Prometheus query failed: {body.get('error', 'unknown error')}")
            return body["data"]
        except requests.exceptions.RequestException as e:
            attempt += 1
            if attempt > MAX_RETRIES:
                raise
            logger.warning("Prometheus request to %s failed (attempt %d/%d): %s", path, attempt, MAX_RETRIES, e)
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)


def _prom_instant(query):
    """Run an instant PromQL query via /api/v1/query.

    Returns the list of result entries: [{"metric": {...labels}, "value":
    [ts, value_str]}, ...].
    """
    data = _prom_get("/api/v1/query", {"query": query})
    return data.get("result", [])


def _prom_range(query, start, end, step=SCRAPE_INTERVAL_SECONDS):
    """Run a range PromQL query via /api/v1/query_range.

    Returns the list of result entries: [{"metric": {...labels}, "values":
    [[ts, value_str], ...]}, ...].
    """
    data = _prom_get(
        "/api/v1/query_range",
        {"query": query, "start": start, "end": end, "step": step},
    )
    return data.get("result", [])


def _history_step_seconds(hours):
    """Pick a range-query step that keeps the point count per series safely
    under Prometheus's cap, scaling up from SCRAPE_INTERVAL_SECONDS as the
    requested window grows rather than hard-capping `hours` (issue #143
    code review). Windows under MAX_RANGE_POINTS * SCRAPE_INTERVAL_SECONDS
    (~166 hours at current constants) keep the full 60s scrape resolution;
    wider windows lose resolution (larger step) instead of failing outright.
    """
    window_seconds = hours * 3600
    required_step = math.ceil(window_seconds / MAX_RANGE_POINTS)
    return max(SCRAPE_INTERVAL_SECONDS, required_step)


def _entity_regex(entity_ids):
    """Build a PromQL-safe regex alternation matching any of entity_ids
    exactly, e.g. for use in a label matcher like entity=~"<result>".

    PromQL double-quoted string literals apply Go string-escaping rules
    before the result is used as a regex, so every backslash re.escape()
    produces (e.g. before each "." in an entity_id) must be doubled to
    survive that first pass - confirmed live during issue #143, where a
    single backslash produced a 400 "unknown escape sequence" from
    Prometheus instead of the intended literal-dot match.
    """
    escaped = "|".join(re.escape(e) for e in entity_ids)
    return escaped.replace("\\", "\\\\")


def _c_to_f(celsius):
    """Convert a Celsius reading to Fahrenheit, rounded to 1 decimal.

    Home Assistant's Prometheus exporter always reports temperature metrics
    in Celsius regardless of the instance's configured display unit; this
    instance displays Fahrenheit everywhere else (dashboards, the old REST
    API attributes hvac_status()/hvac_history() used to read directly), so
    every temperature value crossing this module gets converted here.
    """
    if celsius is None:
        return None
    return round(celsius * 9 / 5 + 32, 1)


def _float_or_none(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ── HVAC Status ──


def _fetch_active_modes(entity_ids):
    """Instant-query homeassistant_climate_mode and return {entity: mode}
    for whichever mode label currently has value 1 per entity.
    """
    regex = _entity_regex(entity_ids)
    results = _prom_instant(f'homeassistant_climate_mode{{entity=~"{regex}"}} == 1')
    modes = {}
    for r in results:
        entity = r["metric"].get("entity")
        mode = r["metric"].get("mode")
        if entity and mode:
            modes[entity] = mode
    return modes


def _fetch_instant_values(query):
    """Instant-query a metric and return {entity: float value}."""
    results = _prom_instant(query)
    values = {}
    for r in results:
        entity = r["metric"].get("entity")
        if entity is None:
            continue
        values[entity] = _float_or_none(r["value"][1])
    return values


def _get_standalone_sensor_zones(temp_by_entity, humidity_by_entity):
    """Build standalone temp+humidity sensor zones (non-thermostat rooms)
    from already-fetched Prometheus values - issue #70/#143.
    """
    zones = []
    for name, entities in STANDALONE_SENSOR_ENTITIES.items():
        zones.append(
            {
                "name": name,
                "entity_id": entities["temp"],
                "mode": "sensor",
                "current_temp": _c_to_f(temp_by_entity.get(entities["temp"])),
                "target_temp": None,
                "target_high": None,
                "target_low": None,
                "humidity": humidity_by_entity.get(entities["humidity"]),
            }
        )
    return zones


def hvac_status():
    """Get current HVAC status for all zones with outdoor conditions.

    Returns dict: {zones: [{name, mode, current_temp, target_temp,
    target_high, target_low, humidity}], outdoor: {temp, humidity}}

    `preset` and `fan_mode` are not included: Home Assistant's Prometheus
    exporter has no equivalent for thermostat preset, and while it does
    export a fan_mode series, this migration (issue #143) dropped both
    fields per the approved plan rather than reading one live from
    Prometheus and leaving the other permanently unavailable.

    `target_high`/`target_low` are always None: Home Assistant's Prometheus
    exporter only ever emits a single-setpoint
    homeassistant_climate_target_temperature_celsius metric, with no
    dual-setpoint (heat_cool mode) equivalent - confirmed live during issue
    #143 by checking every metric name Prometheus has ever recorded for the
    climate domain. The old REST-based implementation populated these from
    HA's target_temp_high/target_temp_low attributes when a zone was in
    heat_cool mode; that data has no Prometheus source and is not
    recoverable without adding an HA dependency back, which defeats the
    purpose of this migration.
    """
    zone_regex = _entity_regex(CLIMATE_ENTITIES)

    sensor_entities = (
        list(ZONE_HUMIDITY_ENTITIES.values())
        + [e["temp"] for e in STANDALONE_SENSOR_ENTITIES.values()]
        + [e["humidity"] for e in STANDALONE_SENSOR_ENTITIES.values()]
        + [OUTDOOR_TEMP_ENTITY, OUTDOOR_HUMIDITY_ENTITY]
    )
    sensor_regex = _entity_regex(sensor_entities)

    mode_by_entity = _fetch_active_modes(CLIMATE_ENTITIES)
    current_temp_by_entity = _fetch_instant_values(
        f'homeassistant_climate_current_temperature_celsius{{entity=~"{zone_regex}"}}'
    )
    target_temp_by_entity = _fetch_instant_values(
        f'homeassistant_climate_target_temperature_celsius{{entity=~"{zone_regex}"}}'
    )
    sensor_values = _fetch_instant_values(
        f'{{__name__=~"homeassistant_sensor_(temperature_celsius|humidity_percent)", entity=~"{sensor_regex}"}}'
    )

    zones = []
    for entity_id in CLIMATE_ENTITIES:
        zones.append(
            {
                "name": ZONE_NAMES.get(entity_id, entity_id),
                "entity_id": entity_id,
                "mode": mode_by_entity.get(entity_id, "unknown"),
                "current_temp": _c_to_f(current_temp_by_entity.get(entity_id)),
                "target_temp": _c_to_f(target_temp_by_entity.get(entity_id)),
                "target_high": None,
                "target_low": None,
                "humidity": sensor_values.get(ZONE_HUMIDITY_ENTITIES[entity_id]),
            }
        )

    # Standalone sensor rooms (non-thermostat)
    standalone_temp = {e["temp"]: sensor_values.get(e["temp"]) for e in STANDALONE_SENSOR_ENTITIES.values()}
    standalone_humidity = {e["humidity"]: sensor_values.get(e["humidity"]) for e in STANDALONE_SENSOR_ENTITIES.values()}
    zones.extend(_get_standalone_sensor_zones(standalone_temp, standalone_humidity))

    # Outdoor conditions
    outdoor = {
        "temp": _c_to_f(sensor_values.get(OUTDOOR_TEMP_ENTITY)),
        "humidity": sensor_values.get(OUTDOOR_HUMIDITY_ENTITY),
    }

    # Analysis
    issues = []
    for z in zones:
        if z["mode"] == "cool" and outdoor.get("temp") and outdoor["temp"] < 60:
            issues.append(
                f"{z['name']} is in cool-only mode but outside is {outdoor['temp']}°F - "
                "may not heat if temps drop overnight"
            )
        if z["mode"] == "heat" and outdoor.get("temp") and outdoor["temp"] > 80:
            issues.append(
                f"{z['name']} is in heat-only mode but outside is {outdoor['temp']}°F - should switch to cool or auto"
            )
        if z["mode"] == "off" and z["current_temp"] and z["current_temp"] < 62:
            issues.append(f"{z['name']} HVAC is off but temp is {z['current_temp']}°F - getting cold")

    return {
        "zones": zones,
        "outdoor": outdoor,
        "issues": issues,
    }


# ── HVAC History ──


def _epoch_to_time_str(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _build_active_mode_timeline(mode_series):
    """From homeassistant_climate_mode range-query results (one series per
    entity+mode label, value 0 or 1 at each timestamp), reconstruct
    {entity: {ts: active_mode}} - the mode whose series value is 1 at ts.
    """
    timeline = {}
    for series in mode_series:
        entity = series["metric"].get("entity")
        mode = series["metric"].get("mode")
        if not entity or not mode:
            continue
        entity_timeline = timeline.setdefault(entity, {})
        for ts, value in series.get("values", []):
            if _float_or_none(value) == 1:
                entity_timeline[ts] = mode
    return timeline


def _build_value_timeline(value_series):
    """From a range-query result for a single-value-per-entity metric,
    build {entity: {ts: float value}}.
    """
    timeline = {}
    for series in value_series:
        entity = series["metric"].get("entity")
        if not entity:
            continue
        entity_timeline = timeline.setdefault(entity, {})
        for ts, value in series.get("values", []):
            entity_timeline[ts] = _float_or_none(value)
    return timeline


def hvac_history(hours=24):
    """Get HVAC history - mode changes and temperature setpoint adjustments,
    plus detected manual overrides.

    Returns dict: {hours, zones: [{name, entity_id, changes: [{time, type,
    from, to, actual_temp}], change_count}], overrides, summary}

    Reconstructed by diffing adjacent samples from Prometheus range queries,
    not from Home Assistant's discrete state-change events - so the
    manual-override heuristic (two changes within 5 minutes) can only
    resolve changes to Prometheus's SCRAPE_INTERVAL_SECONDS (60s)
    granularity. Two real changes inside the same ~60s scrape window
    collapse into a single sample and become undetectable; this is an
    accepted precision loss from moving off HA's REST API (issue #143).

    target_temp_high/target_temp_low change events ("heat_setpoint" in the
    pre-migration return shape) are no longer produced: see hvac_status()'s
    docstring for why that data has no Prometheus source.

    For large `hours` values, the range-query step scales up beyond
    SCRAPE_INTERVAL_SECONDS to stay under Prometheus's per-series point cap
    (see MAX_RANGE_POINTS) - very wide windows lose timestamp resolution
    rather than erroring.
    """
    end = time.time()
    start = end - hours * 3600
    step = _history_step_seconds(hours)
    zone_regex = _entity_regex(CLIMATE_ENTITIES)

    mode_series = _prom_range(f'homeassistant_climate_mode{{entity=~"{zone_regex}"}}', start, end, step)
    temp_series = _prom_range(
        f'homeassistant_climate_current_temperature_celsius{{entity=~"{zone_regex}"}}', start, end, step
    )
    target_series = _prom_range(
        f'homeassistant_climate_target_temperature_celsius{{entity=~"{zone_regex}"}}', start, end, step
    )

    mode_timeline = _build_active_mode_timeline(mode_series)
    temp_timeline = _build_value_timeline(temp_series)
    target_timeline = _build_value_timeline(target_series)

    zones = []
    total_changes = 0
    total_mode = 0
    total_temp = 0

    for entity_id in CLIMATE_ENTITIES:
        entity_mode = mode_timeline.get(entity_id, {})
        entity_target = target_timeline.get(entity_id, {})
        entity_temp = temp_timeline.get(entity_id, {})

        timestamps = sorted(set(entity_mode) | set(entity_target))
        changes = []
        prev_mode = None
        prev_target = None

        for ts in timestamps:
            mode = entity_mode.get(ts)
            target_f = _c_to_f(entity_target.get(ts))
            actual_temp = _c_to_f(entity_temp.get(ts))
            time_str = _epoch_to_time_str(ts)

            if mode is not None and prev_mode is not None and mode != prev_mode:
                changes.append(
                    {
                        "time": time_str,
                        "type": "mode",
                        "from": prev_mode,
                        "to": mode,
                        "actual_temp": actual_temp,
                    }
                )
                total_mode += 1

            if target_f is not None and prev_target is not None and target_f != prev_target:
                changes.append(
                    {
                        "time": time_str,
                        "type": "temp",
                        "from": prev_target,
                        "to": target_f,
                        "actual_temp": actual_temp,
                    }
                )
                total_temp += 1

            if mode is not None:
                prev_mode = mode
            if target_f is not None:
                prev_target = target_f

        total_changes += len(changes)
        zones.append(
            {
                "name": ZONE_NAMES.get(entity_id, entity_id),
                "entity_id": entity_id,
                "changes": changes,
                "change_count": len(changes),
            }
        )

    # Detect rapid changes (possible manual overrides) - see docstring for
    # the ~60s resolution caveat this heuristic now operates under.
    overrides = []
    for z in zones:
        for i, change in enumerate(z["changes"]):
            if i > 0:
                prev = z["changes"][i - 1]
                if change["time"] and prev["time"]:
                    try:
                        t1 = datetime.strptime(prev["time"], "%Y-%m-%d %H:%M:%S")
                        t2 = datetime.strptime(change["time"], "%Y-%m-%d %H:%M:%S")
                        if (t2 - t1).total_seconds() < 300:
                            overrides.append(
                                {
                                    "zone": z["name"],
                                    "time": change["time"],
                                    "type": change["type"],
                                    "change": f"{change.get('from')} → {change.get('to')}",
                                }
                            )
                    except ValueError:
                        pass

    return {
        "hours": hours,
        "zones": zones,
        "overrides": overrides,
        "summary": {
            "total_changes": total_changes,
            "mode_changes": total_mode,
            "temp_adjustments": total_temp,
            "manual_overrides": len(overrides),
            "by_zone": {z["name"]: z["change_count"] for z in zones},
        },
    }
