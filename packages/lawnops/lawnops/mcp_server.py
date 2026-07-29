"""MCP server exposing lawnops data as callable tools for Claude Code.

Returns structured JSON instead of terminal-formatted output - optimized for
LLM consumption (lower token count, directly parseable, no unicode decoration).
"""

import json
import logging
import sys
import time

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
logger = logging.getLogger(__name__)

# Load config on startup so irrigation credentials are available
from lawnops.config import load_config as _load_config  # noqa: E402

_load_config()

mcp = FastMCP(
    "lawnops",
    instructions=(
        "Query existing treatments and products before recommending new ones to avoid duplicates. "
        "Zone numbers map to yard areas - use irrigation_status to see the mapping."
    ),
)

# MCP tool annotations follow the workspace convention in the root CLAUDE.md.
# openWorldHint is set explicitly on every tool because the spec default is true.
# destructiveHint is set explicitly on reversible-write tools because the spec default is also true.
#
# Shared constants for the two dominant read patterns:
_READ_LOCAL = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
_READ_EXTERNAL = ToolAnnotations(readOnlyHint=True, openWorldHint=True)

# ── Caching ──

_weather_cache = None
_weather_cache_time = 0
_pollen_cache = None
_pollen_cache_time = 0
_WEATHER_TTL = 300  # 5 minutes
_POLLEN_TTL = 300  # 5 minutes


def _config():
    """Load config from disk on each call so edits take effect without restart."""
    from lawnops.config import load_config

    return load_config()


def _weather():
    """Fetch weather data with 5-minute TTL cache.

    Returns (config, raw_data, daily_data, current).
    """
    global _weather_cache, _weather_cache_time
    now = time.monotonic()
    if _weather_cache is not None and (now - _weather_cache_time) < _WEATHER_TTL:
        return _weather_cache

    from lawnops.weather import aggregate_daily, fetch_data, get_current

    config = _config()
    data = fetch_data(config)
    daily_data = aggregate_daily(data)
    current = get_current(data)
    _weather_cache = (config, data, daily_data, current)
    _weather_cache_time = now
    return _weather_cache


def _pollen():
    """Fetch pollen data with 5-minute TTL cache."""
    global _pollen_cache, _pollen_cache_time
    now = time.monotonic()
    if _pollen_cache is not None and (now - _pollen_cache_time) < _POLLEN_TTL:
        return _pollen_cache

    from lawnops.db import log_pollen
    from lawnops.pollen import fetch_pollen

    config = _config()
    data = fetch_pollen(config)
    if data is not None:
        log_pollen(config, data)
    _pollen_cache = data
    _pollen_cache_time = now
    return data


def _row_to_dict(row):
    """Convert a sqlite3.Row to a plain dict."""
    return dict(row)


def _rows_to_list(rows):
    """Convert a list of sqlite3.Row objects to a list of dicts."""
    return [dict(r) for r in rows]


# ── Weather ──


@mcp.tool(annotations=_READ_EXTERNAL)
def soil_temp_now() -> str:
    """Current soil temperature reading with threshold status and trend.

    Returns JSON: {time, soil_temp, air_temp, wind, precip, threshold,
    above_threshold, consecutive_days_above, trend_direction, trend_diff_f}
    """
    try:
        from lawnops.advisory import consecutive_days_above, trend_direction

        config, data, daily_data, current = _weather()
        threshold = config["thresholds"]["pre_emergent_soil_temp"]
        consec = consecutive_days_above(daily_data, threshold)
        direction, diff = trend_direction(daily_data)

        return json.dumps(
            {
                "location": config["location"]["name"],
                "time": current["time"],
                "soil_temp_f": round(current["soil_temp"], 1),
                "air_temp_f": round(current["air_temp"], 1),
                "wind_mph": round(current["wind"], 1),
                "precip_in": round(current["precip"], 2),
                "threshold_f": threshold,
                "above_threshold": current["soil_temp"] >= threshold,
                "degrees_below_threshold": round(max(0, threshold - current["soil_temp"]), 1),
                "consecutive_days_above": consec,
                "trend_direction": direction,
                "trend_diff_f": round(diff, 1),
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_EXTERNAL)
def soil_temp_trend() -> str:
    """14-day soil temperature trend - daily min/avg/max, precipitation, threshold flags.

    Returns JSON: {location, threshold_f, days: [{date, soil_min, soil_avg,
    soil_max, precip_in, above_threshold, is_forecast}]}
    """
    try:
        from datetime import datetime

        config, data, daily_data, current = _weather()
        threshold = config["thresholds"]["pre_emergent_soil_temp"]
        today = datetime.now().strftime("%Y-%m-%d")

        days = []
        for d in daily_data:
            days.append(
                {
                    "date": d["date"],
                    "soil_min_f": round(d["soil_min"], 1),
                    "soil_avg_f": round(d["soil_avg"], 1),
                    "soil_max_f": round(d["soil_max"], 1),
                    "precip_in": round(d["precip_total"], 2),
                    "above_threshold": d["soil_max"] >= threshold,
                    "is_forecast": d["date"] > today,
                }
            )

        return json.dumps(
            {
                "location": config["location"]["name"],
                "threshold_f": threshold,
                "days": days,
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_EXTERNAL)
def pre_emergent_advisory() -> str:
    """Pre-emergent herbicide advisory level and recommendation.

    Returns JSON: {level (SAFE|WARNING|URGENT|LATE), message, soil_temp_f,
    consecutive_days_above, trend_direction}. Does NOT duplicate the full
    trend data - call soil_temp_trend() separately if needed.
    """
    try:
        from lawnops.advisory import consecutive_days_above, trend_direction
        from lawnops.advisory import pre_emergent_advisory as _advisory

        config, data, daily_data, current = _weather()
        level, message = _advisory(daily_data, config)
        threshold = config["thresholds"]["pre_emergent_soil_temp"]
        consec = consecutive_days_above(daily_data, threshold)
        direction, diff = trend_direction(daily_data)

        return json.dumps(
            {
                "level": level,
                "message": message,
                "soil_temp_f": round(current["soil_temp"], 1),
                "threshold_f": threshold,
                "consecutive_days_above": consec,
                "trend_direction": direction,
                "trend_diff_f": round(diff, 1),
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_EXTERNAL)
def spray_advisory() -> str:
    """48-hour spray window assessment - GO/NO-GO with conditions and best windows.

    Returns JSON: {status, soil_ok, soil_temp_f, total_precip_in, max_wind_mph,
    air_range_f, issues[], mow_conflict, next_mow_date,
    windows: [{start, end, hours}]}
    """
    try:
        from lawnops.advisory import spray_advisory as _spray

        config, data, daily_data, current = _weather()
        result = _spray(daily_data, data, config)

        # Serialize spray windows to compact format
        windows = []
        for w in result.get("windows", []):
            if w:
                windows.append(
                    {
                        "start": w[0]["time"],
                        "end": w[-1]["time"],
                        "hours": len(w),
                    }
                )

        return json.dumps(
            {
                "status": result["status"],
                "soil_ok": result.get("soil_ok"),
                "soil_temp_f": round(result["soil_temp"], 1) if result.get("soil_temp") is not None else None,
                "total_precip_in": round(result["total_precip"], 2),
                "max_wind_mph": round(result["max_wind"], 1),
                "air_range_f": [round(result["air_range"][0], 1), round(result["air_range"][1], 1)],
                "issues": result["issues"],
                "mow_conflict": result.get("mow_conflict", False),
                "next_mow_date": result.get("next_mow_date"),
                "windows": windows,
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_EXTERNAL)
def fertilizer_recommendation() -> str:
    """Seasonal fertilizer recommendation based on Bermuda grass calendar, soil temp, treatment history, and inventory.

    Returns JSON: {phase, summary, actions[], products[], soil_temp_f,
    month_name, grass_type, last_fertilizer[], days_since_fertilizer,
    last_pre_emergent[], days_since_pre_emergent, inventory[], context_notes[]}
    """
    try:
        from lawnops.recommend import get_recommendation

        config, data, daily_data, current = _weather()
        result = get_recommendation(config, daily_data)

        return json.dumps(
            {
                "location": config["location"]["name"],
                "phase": result["phase"],
                "summary": result["summary"],
                "actions": result["actions"],
                "products": result["products"],
                "soil_temp_f": round(result["soil_temp"], 1) if result["soil_temp"] is not None else None,
                "soil_max_f": round(result["soil_max"], 1) if result["soil_max"] is not None else None,
                "month_name": result["month_name"],
                "grass_type": result["grass_type"],
                "last_fertilizer": result["last_fertilizer"],
                "days_since_fertilizer": result["days_since_fertilizer"],
                "last_pre_emergent": result["last_pre_emergent"],
                "days_since_pre_emergent": result["days_since_pre_emergent"],
                "inventory": result["inventory"],
                "context_notes": result["context_notes"],
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Application Window ──


@mcp.tool(annotations=_READ_EXTERNAL)
def application_window(app_type: str) -> str:
    """Find best application day in the next 7 days with day-by-day scoring."""
    try:
        from lawnops.window import find_application_window

        config, data, daily_data, current = _weather()
        result = find_application_window(app_type, daily_data, config)

        return json.dumps(
            {
                "app_type": app_type,
                "status": result["status"],
                "best_day": result["best_day"],
                "all_days": result["all_days"],
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Inventory & Database ──


@mcp.tool(annotations=_READ_LOCAL)
def product_list() -> str:
    """All products in inventory with category, quantity, cost, and source.

    Returns JSON: {products: [{name, category, qty_on_hand, unit,
    last_ordered, cost_each, source}]}
    """
    try:
        from lawnops.db import list_products

        rows = list_products(_config())
        return json.dumps({"products": _rows_to_list(rows)})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False))
def product_add(
    name: str,
    category: str | None = None,
    qty: float = 1,
    unit: str = "bag",
    cost: float | None = None,
    source: str | None = None,
    notes: str | None = None,
) -> str:
    """Add a new product to inventory."""
    try:
        from lawnops.db import add_product

        add_product(_config(), name, category, qty, unit, cost, source, notes)
        return json.dumps(
            {
                "added": True,
                "name": name,
                "category": category,
                "qty": qty,
                "unit": unit,
                "cost": cost,
                "source": source,
                "notes": notes,
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False))
def product_update(name: str, qty: float | None = None, cost: float | None = None) -> str:
    """Update an existing product's stock quantity and/or cost by partial name match."""
    try:
        from lawnops.db import update_product

        if qty is None and cost is None:
            return json.dumps({"error": "Must provide at least one of qty or cost"})

        rowcount = update_product(_config(), name, qty, cost)
        result = {"name": name, "matched": rowcount}
        if qty is not None:
            result["qty"] = qty
        if cost is not None:
            result["cost"] = cost
        if rowcount == 0:
            result["warning"] = f"No product found matching '{name}'"
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False))
def product_delete(name: str) -> str:
    """Delete a product from inventory by partial name match."""
    try:
        from lawnops.db import delete_product

        rowcount = delete_product(_config(), name)
        result = {"name": name, "deleted": rowcount}
        if rowcount == 0:
            result["warning"] = f"No product found matching '{name}'"
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_LOCAL)
def product_alerts() -> str:
    """Products at zero stock that need reordering.

    Returns JSON: {alerts: [{name, category, unit, cost_each, last_ordered,
    source, treatment_count, last_used}], count}
    """
    try:
        from lawnops.db import get_reorder_alerts

        alerts = get_reorder_alerts(_config())
        return json.dumps({"alerts": alerts, "count": len(alerts)})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False))
def mowing_add(
    date: str,
    cost: float | None = None,
    provider: str | None = None,
    notes: str | None = None,
) -> str:
    """Log a mowing visit."""
    try:
        from lawnops.db import add_mowing

        config = _config()
        resolved_provider = (
            provider if provider is not None else config.get("mowing", {}).get("default_provider", "Mowing Service")
        )
        add_mowing(config, date, provider, cost, notes)
        return json.dumps(
            {
                "added": True,
                "date": date,
                "provider": resolved_provider,
                "cost": cost,
                "notes": notes,
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False))
def mowing_delete(row_id: int) -> str:
    """Delete a mowing visit by ID."""
    try:
        from lawnops.db import delete_mowing

        rowcount = delete_mowing(_config(), row_id)
        result = {"id": row_id, "deleted": rowcount}
        if rowcount == 0:
            result["warning"] = f"No mowing visit found with id {row_id}"
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False))
def treatment_add(
    date: str,
    area: str,
    product: str,
    method: str | None = None,
    cost: float | None = None,
    notes: str | None = None,
) -> str:
    """Log a lawn treatment application."""
    try:
        from lawnops.db import add_treatment

        add_treatment(_config(), date, area, product, method, cost=cost, notes=notes)
        return json.dumps(
            {
                "added": True,
                "date": date,
                "area": area,
                "product": product,
                "method": method,
                "cost": cost,
                "notes": notes,
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False))
def treatment_delete(row_id: int) -> str:
    """Delete a treatment by ID."""
    try:
        from lawnops.db import delete_treatment

        rowcount = delete_treatment(_config(), row_id)
        result = {"id": row_id, "deleted": rowcount}
        if rowcount == 0:
            result["warning"] = f"No treatment found with id {row_id}"
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_LOCAL)
def treatment_list(year: int | None = None) -> str:
    """Treatment history - products applied, areas, methods, and costs."""
    try:
        from lawnops.db import list_treatments

        rows, yr = list_treatments(_config(), year)
        treatments = _rows_to_list(rows)
        return json.dumps({"year": yr, "treatments": treatments, "count": len(treatments)})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_LOCAL)
def mowing_summary(year: int | None = None) -> str:
    """Mowing season summary - visits, costs, and totals."""
    try:
        from lawnops.db import get_mowing_summary

        rows, total_visits, total_cost, yr = get_mowing_summary(_config(), year)
        return json.dumps(
            {
                "year": yr,
                "visits": _rows_to_list(rows),
                "total_visits": total_visits,
                "total_cost": total_cost,
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_LOCAL)
def equipment_list() -> str:
    """Equipment inventory with purchase dates, costs, and status.

    Returns JSON: {equipment: [{id, name, purchase_date, cost, source,
    status, notes}]}
    """
    try:
        from lawnops.db import list_equipment

        rows = list_equipment(_config())
        return json.dumps({"equipment": _rows_to_list(rows)})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False))
def purchase_add(
    date: str,
    item: str,
    cost: float,
    category: str | None = None,
    source: str | None = None,
    notes: str | None = None,
) -> str:
    """Log a product/equipment purchase."""
    try:
        from lawnops.db import add_purchase

        add_purchase(_config(), date, item, category, cost=cost, source=source, notes=notes)
        return json.dumps(
            {
                "added": True,
                "date": date,
                "item": item,
                "cost": cost,
                "category": category,
                "source": source,
                "notes": notes,
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False))
def purchase_delete(row_id: int) -> str:
    """Delete a purchase by ID."""
    try:
        from lawnops.db import delete_purchase

        rowcount = delete_purchase(_config(), row_id)
        result = {"id": row_id, "deleted": rowcount}
        if rowcount == 0:
            result["warning"] = f"No purchase found with id {row_id}"
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False))
def equipment_add(
    name: str,
    cost: float | None = None,
    purchase_date: str | None = None,
    source: str | None = None,
    notes: str | None = None,
) -> str:
    """Add equipment to inventory."""
    try:
        from lawnops.db import add_equipment

        add_equipment(_config(), name, cost, purchase_date, source, notes)
        return json.dumps(
            {
                "added": True,
                "name": name,
                "cost": cost,
                "purchase_date": purchase_date,
                "source": source,
                "notes": notes,
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False))
def equipment_delete(row_id: int) -> str:
    """Delete equipment by ID."""
    try:
        from lawnops.db import delete_equipment

        rowcount = delete_equipment(_config(), row_id)
        result = {"id": row_id, "deleted": rowcount}
        if rowcount == 0:
            result["warning"] = f"No equipment found with id {row_id}"
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Reports ──


@mcp.tool(annotations=_READ_LOCAL)
def spend_report(year: int | None = None, category: str | None = None) -> str:
    """Spending report - by-category totals and itemized purchases."""
    try:
        from lawnops.db import get_spend_report

        cat_rows, grand_total, item_rows, yr = get_spend_report(_config(), year, category)
        return json.dumps(
            {
                "year": yr,
                "category_filter": category,
                "categories": _rows_to_list(cat_rows),
                "grand_total": grand_total,
                "items": _rows_to_list(item_rows),
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Calculators ──


@mcp.tool(annotations=_READ_LOCAL)
def coverage_calculator(product: str, sqft: int | None = None) -> str:
    """Calculate how many bags/units of a product are needed for the yard."""
    try:
        from lawnops.coverage import calculate_coverage

        result = calculate_coverage(product, _config(), sqft_override=sqft)
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_LOCAL)
def mix_calculator(product: str, tank: float = 4.0, rate: str | None = None) -> str:
    """Calculate spray concentrate mix rate per tank load."""
    try:
        from lawnops.mixrate import calculate_mix

        result = calculate_mix(product, tank, _config(), rate_type=rate)
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Irrigation (read-only) ──


def _serialize_irrigation_status(ctrl, sensors, programs):
    """Convert pydrawise controller objects to a serializable dict."""
    from datetime import datetime

    zones = []
    for zone in ctrl.zones:
        sched = zone.scheduled_runs
        if sched.current_run:
            remaining = sched.current_run.remaining_time
            mins = remaining.total_seconds() / 60 if remaining else 0
            zone_status = f"running ({mins:.0f}m remaining)"
        elif zone.suspensions:
            zone_status = "suspended"
        else:
            zone_status = "ready"

        if sched.next_run:
            next_run = (
                sched.next_run.start_time.strftime("%Y-%m-%d %H:%M")
                if hasattr(sched.next_run, "start_time")
                else str(sched.next_run)
            )
        elif sched.summary:
            next_run = sched.summary
        else:
            next_run = None

        zone_info = {
            "number": zone.number.value,
            "name": zone.name,
            "status": zone_status,
            "next_run": next_run,
            "watering_adjustment_pct": zone.watering_settings.fixed_watering_adjustment,
        }
        if zone.suspensions:
            zone_info["suspended_until"] = zone.suspensions[0].end_time.strftime("%Y-%m-%d %H:%M")
        zones.append(zone_info)

    prog_list = []
    for prog in programs.values():
        prog_list.append(
            {
                "name": prog["name"],
                "start_time": prog["start"],
                "period_days": prog["period"],
                "zones": sorted(prog["zones"]),
                "current_month_pct": prog["monthly"][datetime.now().month - 1]
                if datetime.now().month - 1 < len(prog["monthly"])
                else None,
            }
        )

    sensor_list = []
    for s in sensors:
        if s.status is not None:
            active = getattr(s.status, "active", None)
            status_str = "triggered" if active else "dry"
        else:
            status_str = "unknown"
        sensor_list.append(
            {
                "name": s.name,
                "status": status_str,
            }
        )

    return {
        "controller": {
            "name": ctrl.name,
            "online": ctrl.online,
            "firmware": ctrl.software_version,
            "status": ctrl.status.summary if ctrl.status else "unknown",
            "last_contact": ctrl.last_contact_time.strftime("%Y-%m-%d %H:%M") if ctrl.last_contact_time else None,
        },
        "zones": zones,
        "programs": prog_list,
        "sensors": sensor_list,
    }


@mcp.tool(annotations=_READ_EXTERNAL)
def irrigation_status() -> str:
    """Hydrawise irrigation controller status - controller info, zones, programs, and sensors.

    Returns JSON: {controller: {name, online, firmware, status, last_contact},
    zones: [{number, name, status, next_run, suspended_until?}],
    programs: [{name, start_time, period_days, zones, current_month_pct}],
    sensors: [{name, status}]}
    """
    try:
        from lawnops.irrigation import get_status

        ctrl, sensors, programs = get_status()
        return json.dumps(_serialize_irrigation_status(ctrl, sensors, programs))
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_EXTERNAL)
def irrigation_history(days: int = 7) -> str:
    """Recent watering history from Hydrawise controller.

    Queries local database for full history (unlimited lookback). Always syncs
    the last 2 days from the live Hydrawise API first to catch runs completed
    since the last DB sync (the API has a short reporting delay). Falls back to
    a deeper sync if the DB is significantly stale.

    Includes inferred skip entries for expected runs that did not occur, with
    skip reasons derived from historical weather data (rain, wind thresholds).
    """
    try:
        from datetime import datetime

        from lawnops.db.irrigation_log import (
            get_history_from_db,
            get_skip_history_from_db,
            sync_irrigation,
            sync_skipped_runs,
        )

        cfg = _config()

        # Always sync recent 2 days before querying - the Hydrawise API has a
        # short delay after a run completes, so a gap-only check misses runs
        # that finished after the last sync but before the API reported them.
        try:
            sync_irrigation(cfg, days=2)
        except Exception:
            pass  # API unreachable - proceed with DB data

        entries = get_history_from_db(cfg, days)

        # If DB is still significantly stale after the 2-day sync, do a deeper pull
        if entries:
            newest = max(e["run_time"] for e in entries)
            newest_dt = datetime.strptime(newest[:16], "%Y-%m-%d %H:%M")
            gap_days = (datetime.now() - newest_dt).days
        else:
            gap_days = days  # DB empty - fetch full window from API

        if gap_days >= 2:
            try:
                sync_irrigation(cfg, days=min(gap_days + 1, 35))
                entries = get_history_from_db(cfg, days)
            except Exception:
                pass  # API unreachable - return what we have from DB

        # Detect and store skipped runs, then merge into output
        try:
            sync_skipped_runs(cfg, days=days)
        except Exception:
            pass  # Non-fatal - skip inference requires weather API

        skip_entries = get_skip_history_from_db(cfg, days)

        # Tag completed runs with type for consistency
        for e in entries:
            e.setdefault("type", "run")

        all_entries = sorted(
            entries + skip_entries,
            key=lambda e: e.get("run_time") or e.get("expected_date") or "",
            reverse=True,
        )

        return json.dumps(
            {
                "days": days,
                "entries": all_entries,
                "runs": len(entries),
                "skips": len(skip_entries),
                "source": "database",
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True))
def irrigation_run_zone(zone: int, minutes: int) -> str:
    """Run a single irrigation zone manually."""
    try:
        from lawnops.irrigation import run_zone

        zone_name, zone_num, mins = run_zone(zone, minutes)
        return json.dumps(
            {
                "zone_name": zone_name,
                "zone_num": zone_num,
                "minutes": mins,
                "status": "started",
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True))
def irrigation_run_all(minutes: int, zones: list[int] | None = None) -> str:
    """Run all zones or specific zones sequentially for N minutes each."""
    try:
        from lawnops.irrigation import run_all

        results = run_all(minutes, zones=zones)
        return json.dumps(
            {
                "zones": [{"zone_num": z, "zone_name": n} for z, n in results],
                "minutes_per_zone": minutes,
                "total_runtime_min": len(results) * minutes,
                "status": "started",
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)
)
def irrigation_stop(zone: int | None = None) -> str:
    """Stop a specific zone or all zones."""
    try:
        from lawnops.irrigation import stop

        zone_name = stop(zone_num=zone)
        result = {"stopped": "all" if zone_name is None else f"zone {zone}"}
        if zone_name:
            result["zone_name"] = zone_name
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True))
def irrigation_suspend(hours: int, zone: int | None = None) -> str:
    """Suspend irrigation for a number of hours."""
    try:
        from lawnops.irrigation import suspend

        until_str, zone_name = suspend(hours, zone_num=zone)
        result = {"suspended_until": until_str}
        if zone_name:
            result["zone_name"] = zone_name
        else:
            result["scope"] = "all zones"
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)
)
def irrigation_resume(zone: int | None = None) -> str:
    """Resume suspended irrigation zones."""
    try:
        from lawnops.irrigation import resume

        zone_name = resume(zone_num=zone)
        result = {"resumed": "all" if zone_name is None else f"zone {zone}"}
        if zone_name:
            result["zone_name"] = zone_name
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_LOCAL)
def irrigation_pace() -> str:
    """Current month irrigation pace vs historical averages with cost projection.

    Shows actual minutes used, projected total, comparison to same month last year,
    and alert level (on-track/elevated/high) based on cost thresholds.

    Projection uses a schedule-based model derived from 90-day run history
    (effective cycle period x zone count x avg run duration); falls back to
    linear pace extrapolation when history is insufficient. Cost-per-minute is
    dynamically weighted from the three most recent qualifying billing months
    (50/30/20%) rather than a hardcoded config value.

    Returns JSON: {month, month_name, current_minutes, projected_minutes,
    projected_cost, projection_method, projection_reliability, cpm_source,
    last_year_minutes, historical_avg_minutes, alert_level, thresholds}
    """
    try:
        from lawnops.db import irrigation_pace as _pace

        return json.dumps(_pace(_config()))
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_LOCAL)
def irrigation_budget() -> str:
    """Irrigation budget tracker - actual vs configured limits with YNAB integration.

    Tracks minutes or dollar budget, alerts at configured threshold, recommends ET%
    reduction if trending over. Includes ynab_integration section with estimated water
    bill and category info for Claude to orchestrate planned expense updates.

    Projection uses a schedule-based model (90-day run history); falls back to
    linear pace extrapolation. Cost-per-minute is dynamically weighted from recent
    billing months. et_pct_for_budget is the ET% needed to stay within the configured
    monthly dollar limit, or null if not applicable.

    Returns JSON: {month, budget_status, current_minutes, projected_minutes,
    projected_cost, et_pct_for_budget, projection_method, projection_reliability,
    cpm_source, pct_used, remaining, recommendations[], ynab_integration}
    """
    try:
        from lawnops.db import irrigation_budget as _budget

        return json.dumps(_budget(_config()))
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False))
def irrigation_budget_update(monthly_dollars: float | None = None, alert_pct: int | None = None) -> str:
    """Update irrigation dollar budget limits in config. Changes take effect immediately.

    Args:
        monthly_dollars: New monthly dollar limit (must be a positive number).
        alert_pct: Alert threshold as a percentage 0-100.

    At least one parameter must be provided.

    Returns JSON: {updated: true, monthly_dollars, alert_pct} on success,
    or {error: ...} on failure.
    """
    try:
        import json as _json

        if monthly_dollars is None and alert_pct is None:
            return _json.dumps({"error": "Must provide at least one of monthly_dollars or alert_pct"})

        if monthly_dollars is not None and monthly_dollars <= 0:
            return _json.dumps({"error": "monthly_dollars must be a positive number"})

        if alert_pct is not None and not (0 <= alert_pct <= 100):
            return _json.dumps({"error": "alert_pct must be between 0 and 100"})

        from lawnops.atomic_io import atomic_write_json
        from lawnops.config import CONFIG_FILE

        # Read current config from disk directly (bypass load_config layer processing)
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE) as f:
                file_config = _json.load(f)
        else:
            return _json.dumps({"error": f"Config file not found at {CONFIG_FILE}"})

        # Update only the provided fields under irrigation.budget
        budget = file_config.setdefault("irrigation", {}).setdefault("budget", {})
        if monthly_dollars is not None:
            budget["monthly_dollars"] = monthly_dollars
        if alert_pct is not None:
            budget["alert_pct"] = alert_pct

        # Write updated config back to disk atomically (config.json holds Hydrawise credentials).
        # mode/dir_mode keep the file and its directory locked down the same way
        # `lawnops configure` does -- this call rewrites the whole file, so omitting
        # them here would silently widen permissions back to umask defaults.
        atomic_write_json(CONFIG_FILE, file_config, mode=0o600, dir_mode=0o700)

        return _json.dumps(
            {
                "updated": True,
                "monthly_dollars": budget.get("monthly_dollars"),
                "alert_pct": budget.get("alert_pct"),
                "config_path": str(CONFIG_FILE),
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_LOCAL)
def zone_analysis(year: int | None = None, month: int | None = None) -> str:
    """Per-zone irrigation runtime breakdown with outlier detection.

    Groups zones by program (lawn vs beds), flags zones running significantly more
    than program average, identifies short-run anomalies (possible head issues).
    """
    try:
        from lawnops.db import zone_analysis as _zones

        return json.dumps(_zones(_config(), year, month))
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_LOCAL)
def et_recommendations(year: int | None = None) -> str:
    """Analyze cost-per-minute by month and recommend ET% reductions for expensive months.

    budget_based_recommendation is the primary field: if a monthly dollar budget is
    configured, it contains the ET% needed to stay within it this month using
    schedule-based projection and dynamic CPM. The legacy recommendations list
    (CPM vs historical average) is secondary context. Advisory only - adjustments
    must be made in the Hydrawise app.

    Returns JSON: {year, avg_cost_per_minute, months[], recommendations[],
    potential_savings, budget_based_recommendation, cpm_source}
    """
    try:
        from lawnops.db import et_recommendations as _et

        return json.dumps(_et(_config(), year))
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_EXTERNAL)
def irrigation_program_list() -> str:
    """List all irrigation programs with zones, run durations, frequency, and start times.

    Returns JSON: {programs: [{id, name, program_type, day_pattern, start_times,
    period_days, ignore_rain_sensor, predictive_watering: [{id, label}],
    zones: [{zone_num, zone_name, run_duration_min}]}]}

    program_type is "Time Based" or "Virtual Solar Sync".
    predictive_watering lists the active skip/adjust conditions for the program
    with both id and human-readable label (e.g. "80%+ chance of rain").

    Use this before irrigation_program_update to get the program ID and current settings.
    """
    try:
        from lawnops.irrigation import get_programs

        ctrl, programs = get_programs()
        clean = []
        for p in programs:
            clean.append(
                {
                    "id": p["id"],
                    "name": p["name"],
                    "program_type": p.get("scheduling_method_label", "Time Based"),
                    "day_pattern": p["day_pattern"],
                    "start_times": p["start_times"],
                    "period_days": p["period_days"],
                    "ignore_rain_sensor": p["ignore_rain_sensor"],
                    "predictive_watering": p.get("schedule_adjustments", []),
                    "zones": [
                        {
                            "zone_num": z["zone_num"],
                            "zone_name": z["zone_name"],
                            "run_duration_min": z["run_duration_min"],
                        }
                        for z in p["zones"]
                    ],
                }
            )
        return json.dumps(
            {
                "controller": ctrl.name,
                "programs": clean,
                "count": len(clean),
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True))
def irrigation_program_update(
    program_id: int,
    add_zones: list[int] | None = None,
    remove_zones: list[int] | None = None,
    period_days: int | None = None,
    start_time: str | None = None,
    run_duration_min: int | None = None,
    seasonal_adjustment_factors: list[int] | None = None,
    predictive_watering_ids: list[int] | None = None,
) -> str:
    """Update an irrigation program - add/remove zones, change frequency, duration, seasonal adjustments, or predictive watering conditions.

    Args:
        program_id: Program ID from irrigation_program_list().
        add_zones: Zone numbers to add to the program (e.g., [7]).
        remove_zones: Zone numbers to remove from the program.
        period_days: Watering interval in days (e.g., 2 = every other day).
        start_time: New start time in HH:MM format (e.g., "06:30"). Replaces all existing start times.
        run_duration_min: New run duration in minutes applied to every zone in the program.
        seasonal_adjustment_factors: 12-element list of monthly watering percentages
            [Jan, Feb, Mar, Apr, May, Jun, Jul, Aug, Sep, Oct, Nov, Dec].
            Current values shown in irrigation_status monthly % row.
            Example: [0, 0, 100, 100, 100, 100, 110, 110, 85, 85, 0, 0] bumps Apr/May to 100%.
        predictive_watering_ids: Explicit list of condition IDs to set on the program.
            Replaces all current conditions. Use irrigation_program_list() to see current IDs.
            Known IDs: 5=Forecast<70F, 6=30%less<75F, 7=more-often-hot, 8=longer-hot,
            9=80%+rain, 10=wind>15mph, 11=0.1in+rain-last-day, 12=1.5in+rain-last-3days.
            Pass [] to clear all conditions.

    Returns JSON: {program_id, name, zones_in_program, start_times, period_days, seasonal_adjustment_factors, status}

    Always call irrigation_program_list() first to confirm program_id and current state.
    """
    try:
        from lawnops.irrigation import update_program

        result = update_program(
            program_id=program_id,
            add_zone_nums=add_zones,
            remove_zone_nums=remove_zones,
            period_days=period_days,
            start_time=start_time,
            run_duration_min=run_duration_min,
            seasonal_adjustment_factors=seasonal_adjustment_factors,
            schedule_adjustment_ids=predictive_watering_ids,
        )
        result["status"] = "updated"
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True))
def irrigation_export(output_path: str | None = None) -> str:
    """Export live Hydrawise controller config to a YAML desired-state file (read-only).

    Captures all programs, zone run durations, 12-month seasonal adjustment curves,
    predictive watering condition IDs, and per-zone advisory settings (watering
    adjustment, cycle/soak -- marked advisory_only because zone-level writes are
    blocked by a Hydrawise server-side ISE as of 2026-06-21).

    Args:
        output_path: Optional path for the YAML file.  Defaults to
            ~/.config/lawnops/irrigation_state.yaml (local only; never committed
            to the repo, as exported config is operational home state).

    Returns JSON: {status, output_path, exported_at, controller, programs_count,
    zones_count, programs: [{name, zones_count, period_days, start_times,
    seasonal_adjustment_factors}]}
    """
    try:
        from lawnops.irrigation import get_programs
        from lawnops.irrigation_config import export_config, write_yaml

        ctrl, programs = get_programs()
        cfg = export_config(ctrl, programs)
        written = write_yaml(cfg, output_path)

        prog_summary = [
            {
                "name": p["name"],
                "zones_count": len(p["zones"]),
                "period_days": p["period_days"],
                "start_times": p["start_times"],
                "seasonal_adjustment_factors": p["seasonal_adjustment_factors"],
                "predictive_watering_ids": p["predictive_watering_ids"],
                "predictive_watering_labels": p["predictive_watering_labels"],
            }
            for p in cfg["programs"]
        ]

        return json.dumps(
            {
                "status": "exported",
                "output_path": str(written),
                "exported_at": cfg["exported_at"],
                "controller": cfg["controller"]["name"],
                "programs_count": len(cfg["programs"]),
                "zones_count": len(cfg["zones_catalog"]),
                "programs": prog_summary,
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_EXTERNAL)
def irrigation_diff(file_path: str | None = None) -> str:
    """Compare live Hydrawise controller config against a desired-state YAML file (read-only).

    Fetches the current live config from Hydrawise and compares it against the
    stored desired-state YAML.  Reports per-field added / removed / changed entries
    keyed by program and zone, categorized as:

    - program_level: actionable changes writable via updateStandardProgram (Phase 3)
    - zone_level: advisory / ISE-blocked (watering_adjustment_pct, cycle_soak);
      cannot be submitted until Hydrawise fixes updateZone* mutations

    Volatile metadata (exported_at, controller.online, controller.last_contact)
    is ignored so it never shows as drift.

    Args:
        file_path: Path to desired-state YAML file. Defaults to
            ~/.config/lawnops/irrigation_state.yaml. Run irrigation_export first
            to create the baseline if the file does not exist.

    Returns JSON: {has_drift, program_level_count, zone_level_count, total_count,
    changes: [{path, live, desired, category, advisory?, ise_blocked?, note?}]}
    """
    try:
        from lawnops.irrigation import get_programs
        from lawnops.irrigation_config import diff_config, export_config, read_yaml

        desired = read_yaml(file_path)
        ctrl, programs = get_programs()
        live = export_config(ctrl, programs)
        result = diff_config(live, desired)
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True))
def irrigation_apply(file_path: str | None = None, confirm: bool = False) -> str:
    """Converge the live Hydrawise controller to the desired-state YAML for program-level changes.

    DEFAULT IS DRY-RUN.  With confirm=False (the default), this tool computes the
    diff and the planned writes and returns them WITHOUT touching the controller.
    Set confirm=True only when you are ready to write to the live controller.

    What gets written (confirm=True):
      - seasonal_adjustment_factors per program
      - period_days (watering interval) per program
      - start_times per program (first time in the list)
      - predictive_watering_ids (conditional watering conditions) per program
      - zone add/remove within a program
      - run_duration_min for zones in a program

    What is ALWAYS SKIPPED (never written):
      - Zone-level fields (watering_adjustment_pct, cycle_soak): ISE-blocked by
        Hydrawise server-side error on updateZone* mutations
      - Program fields name, program_type, day_pattern, ignore_rain_sensor:
        need extended mutation support (use Hydrawise portal)
      - Controller metadata (name, firmware): portal-only
      - Whole-program add/remove: portal-only

    This tool NEVER calls run/stop/suspend/resume.  It only calls
    update_program() for programs with program-level drift.

    Args:
        file_path: Path to desired-state YAML. Defaults to
            ~/.config/lawnops/irrigation_state.yaml. Run irrigation_export first
            to create the baseline.
        confirm: Set True to execute writes. Default False = dry-run.

    Returns JSON with keys:
      dry_run (bool), has_drift (bool), updates_planned (int), skipped_count (int),
      and either:
        - (dry-run) updates: [{program_id, program_name, fields_applied, kwargs}],
          skipped: [{path, category, reason}]
        - (confirmed) applied: [{program_id, program_name, fields_applied, result}],
          skipped: [{path, category, reason}],
          errors: [{program_id, program_name, error}]
    """
    try:
        from lawnops.irrigation import execute_apply, get_programs
        from lawnops.irrigation_config import diff_config, export_config, plan_apply, read_yaml

        desired = read_yaml(file_path)
        ctrl, programs = get_programs()
        live = export_config(ctrl, programs)
        diff_result = diff_config(live, desired)
        apply_plan = plan_apply(diff_result, desired)

        n_updates = len(apply_plan["updates"])
        n_skipped = len(apply_plan["skipped"])

        if not diff_result["has_drift"]:
            return json.dumps(
                {
                    "dry_run": not confirm,
                    "has_drift": False,
                    "updates_planned": 0,
                    "skipped_count": 0,
                    "message": "No drift detected. Live config matches desired state. Nothing to apply.",
                    "updates": [],
                    "skipped": [],
                }
            )

        if not confirm:
            # Dry-run: return plan without executing
            return json.dumps(
                {
                    "dry_run": True,
                    "has_drift": True,
                    "updates_planned": n_updates,
                    "skipped_count": n_skipped,
                    "message": f"DRY-RUN: {n_updates} program update(s) planned, {n_skipped} change(s) skipped. Set confirm=True to execute.",
                    "updates": apply_plan["updates"],
                    "skipped": apply_plan["skipped"],
                }
            )

        # confirm=True: execute writes
        report = execute_apply(apply_plan)
        has_errors = len(report["errors"]) > 0

        return json.dumps(
            {
                "dry_run": False,
                "has_drift": True,
                "updates_planned": n_updates,
                "skipped_count": n_skipped,
                "status": "completed_with_errors" if has_errors else "ok",
                "applied": report["applied"],
                "skipped": report["skipped"],
                "errors": report["errors"],
                "summary": {
                    "applied_count": len(report["applied"]),
                    "skipped_count": len(report["skipped"]),
                    "error_count": len(report["errors"]),
                },
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True))
def irrigation_zone_update(zone: int, fixed_watering_adjustment: int) -> str:
    """Update per-zone ET/Smart Watering adjustment percentage.

    NOTE: The Hydrawise updateZoneStandard API mutation is currently returning
    internal server errors for all inputs. This tool is wired and ready but
    will fail until Hydrawise fixes their API. Use irrigation_program_update
    with seasonal_adjustment_factors instead to control watering levels.

    Returns JSON: {zone_num, zone_name, old_adjustment, new_adjustment}
    """
    try:
        from lawnops.irrigation import update_zone_settings

        result = update_zone_settings(zone, fixed_watering_adjustment)
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_LOCAL)
def water_usage_report(year: int | None = None) -> str:
    """Monthly irrigation runtime correlated with water bills to estimate irrigation cost."""
    try:
        from lawnops.db.water_usage import get_water_usage_report

        result = get_water_usage_report(_config(), year)
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Pollen ──


@mcp.tool(annotations=_READ_EXTERNAL)
def pollen_now() -> str:
    """Current pollen count with breakdown by type and spray impact assessment.

    Returns JSON: {date, total_count, category, trees[], grasses[], weeds[],
    molds[], spray_impact, spray_note}. Returns null fields if data unavailable
    (off-season or fetch error).
    """
    try:
        from lawnops.pollen import pollen_spray_impact

        data = _pollen()
        if data is None:
            return json.dumps({"available": False, "reason": "off-season or fetch error"})

        impact = pollen_spray_impact(data["total_count"], _config())

        return json.dumps(
            {
                "available": True,
                "date": data["date"],
                "total_count": data["total_count"],
                "category": data["category"],
                "trees": data.get("trees", []),
                "grasses": data.get("grasses", []),
                "weeds": data.get("weeds", []),
                "molds": data.get("molds", []),
                "spray_impact": impact["impact"],
                "spray_note": impact["note"],
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_LOCAL)
def pollen_trend(days: int = 7) -> str:
    """Pollen count trend over recent days with averages and direction."""
    try:
        from lawnops.db import get_pollen_history
        from lawnops.pollen import get_pollen_trend_analysis

        history = get_pollen_history(_config(), days)
        trend = get_pollen_trend_analysis(history)

        entries = []
        for h in history:
            entries.append(
                {
                    "date": h["date"],
                    "total_count": h["total_count"],
                    "category": h["category"],
                }
            )

        return json.dumps(
            {
                "days": days,
                "entries": entries,
                "count": len(entries),
                "trend": trend,
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Entry point ──


def main():
    """Run the MCP server over stdio."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
